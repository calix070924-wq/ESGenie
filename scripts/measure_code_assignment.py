#!/usr/bin/env python3
"""5개사 OCR 캐시의 K-ESG 코드 배정을 legacy/exact/fuzzy로 전수 비교한다.

라이브 API를 호출하지 않는다. 중복 실험 캐시에 같은 metric이 여러 번 들어 있으므로
``(회사, hint, value, unit, period, guess)``가 같은 행은 한 번만 센다. legacy 정책은
작업 시작점 커밋의 ``_HINT_TO_KESG``를 읽어 재현하므로 운영 코드에 옛 사전을 복제하지 않는다.
"""
from __future__ import annotations

import argparse
import ast
import collections
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from esgenie.knowledge.kesg_items import by_code  # noqa: E402
from esgenie.rag_gates.units import normalize_unit, units_compatible  # noqa: E402
from esgenie.ssot.evidence_graph import _resolve_kesg_code  # noqa: E402
from esgenie.ssot.ocr_router import ExtractedMetric  # noqa: E402


BASELINE_COMMIT = "fb8874ef4cbb43f7ca0082379319b4e8946da6bf"
TARGETS = {
    "012330_mobis_2025.pdf": ("012330", "현대모비스"),
    "009150_samsungsem_2025.pdf": ("009150", "삼성전기"),
    "051910_lgchem_2025.pdf": ("051910", "LG화학"),
    "055550_shinhan_2025.pdf": ("055550", "신한지주"),
    "035420_naver_2025.pdf": ("035420", "NAVER"),
}


@dataclass(frozen=True)
class CachedMetric:
    ticker: str
    company: str
    hint: str
    value: float
    unit: str
    period: str
    guess: str | None

    def extracted(self) -> ExtractedMetric:
        return ExtractedMetric(
            metric_hint=self.hint,
            value=self.value,
            unit=self.unit,
            period=self.period,
            kesg_code_guess=self.guess,
        )


def _load_metrics(cache_dir: Path) -> list[CachedMetric]:
    unique: dict[tuple[Any, ...], CachedMetric] = {}
    for path in sorted(cache_dir.glob("*.json")):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        source = str((entry.get("meta") or {}).get("source_file") or "")
        target = TARGETS.get(source)
        if target is None:
            continue
        ticker, company = target
        for raw in (entry.get("response") or {}).get("metrics", []):
            try:
                value = float(raw.get("value"))
            except (TypeError, ValueError):
                continue
            hint = str(raw.get("metric_hint") or "").strip()
            if not hint:
                continue
            guess_raw = raw.get("kesg_code")
            guess = str(guess_raw) if guess_raw is not None else None
            metric = CachedMetric(
                ticker=ticker,
                company=company,
                hint=hint,
                value=value,
                unit=str(raw.get("unit") or "").strip(),
                period=str(raw.get("period") or "").strip(),
                guess=guess,
            )
            key = (ticker, hint, value, metric.unit, metric.period, guess)
            unique[key] = metric
    return sorted(unique.values(), key=lambda m: (m.ticker, m.hint, m.period, m.value))


def _baseline_literals() -> dict[str, Any]:
    source = subprocess.run(
        ["git", "show", f"{BASELINE_COMMIT}:esgenie/ssot/evidence_graph.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    tree = ast.parse(source)
    wanted = {
        "_HINT_TO_KESG",
        "_HINT_EXCLUDE",
        "_ASSIGNMENT_NEGATIVE_KEYWORDS",
        "_GUARD_TERMS",
    }
    found: dict[str, Any] = {}
    for node in tree.body:
        name = None
        value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            name = getattr(node.targets[0], "id", None)
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            name = getattr(node.target, "id", None)
            value = node.value
        if name in wanted and value is not None:
            found[name] = ast.literal_eval(value)
    missing = wanted - found.keys()
    if missing:
        raise RuntimeError(f"baseline 상수 읽기 실패: {sorted(missing)}")
    return found


def _legacy_relaxed_unit(unit: str) -> str:
    value = re.sub(r"\s+", "", str(unit)).lower()
    value = value.replace("co₂", "co2").replace("톤", "t")
    return re.sub(r"^tons?(?=co2|$)", "t", value)


def _legacy_unit_suspect(extracted: str, expected: str) -> bool:
    if not extracted or not expected:
        return False
    left, right = _legacy_relaxed_unit(extracted), _legacy_relaxed_unit(expected)
    if left == right:
        return False
    norm_left, norm_right = normalize_unit(left), normalize_unit(right)
    return not (
        norm_left is not None
        and norm_right is not None
        and units_compatible(norm_left, norm_right)
    )


def _legacy_resolver() -> Callable[[ExtractedMetric], str | None]:
    constants = _baseline_literals()
    mapping: dict[str, str] = constants["_HINT_TO_KESG"]
    excludes: tuple[str, ...] = constants["_HINT_EXCLUDE"]
    negatives: dict[str, tuple[str, ...]] = constants["_ASSIGNMENT_NEGATIVE_KEYWORDS"]
    guards: tuple[str, ...] = constants["_GUARD_TERMS"]

    def resolve(metric: ExtractedMetric) -> str | None:
        hint = metric.metric_hint.lower().replace(" ", "")
        if any(term in hint for term in excludes) or any(term in hint for term in guards):
            return None

        def conflicts(code: str) -> bool:
            return any(term in hint for term in negatives.get(code, ()))

        code = metric.kesg_code_guess
        if code and "scope3" in hint and str(code) == "2":
            code = None
        if code and conflicts(code):
            code = None
        if not code:
            for key, mapped in sorted(mapping.items(), key=lambda pair: -len(pair[0])):
                if key.lower() in hint and not conflicts(mapped):
                    code = mapped
                    break
        if not code:
            return None
        item = by_code(str(code))
        if item and item.unit and _legacy_unit_suspect(metric.unit, item.unit):
            return None
        return str(code)

    return resolve


def _obvious_error_reasons(metric: CachedMetric, code: str | None) -> list[str]:
    """요청서에 명시된 고위험 오배정 유형만 보수적으로 자동 표식한다."""
    if not code:
        return []
    hint = re.sub(r"\s+", "", metric.hint).lower()
    unit = re.sub(r"\s+", "", metric.unit).lower()
    reasons: list[str] = []
    if code == "E-3-1" and any(x in hint for x in ("scope3", "1+2+3", "가치사슬")):
        reasons.append("Scope3→Scope1+2")
    if code == "E-3-2" and "scope3" not in hint and re.search(r"scope[12]", hint):
        reasons.append("Scope1/2→Scope3")
    scope3_categories = (
        "업스트림", "다운스트림", "상류부문", "하류부문", "자본재", "임대자산",
        "임직원통근", "출장온실가스", "판매제품", "프랜차이즈", "투자온실가스",
        "연료/에너지", "연료및에너지", "운송및물류",
    )
    if code == "E-3-1" and any(term in hint for term in scope3_categories):
        reasons.append("Scope3카테고리→Scope1+2")

    item = by_code(code)
    expected = (item.unit if item else "").lower()
    is_rate = unit in {"%", "pct", "퍼센트", "‰", "퍼밀"}
    expected_rate = expected in {"%", "‰"}
    if expected_rate and unit and not is_rate:
        reasons.append("절대량→비율코드")
    if expected and not expected_rate and is_rate:
        reasons.append("비율→절대량코드")

    if any(x in hint or x in unit for x in ("원단위", "집약도", "intensity", "/억원")):
        reasons.append("원단위→절대량코드")
    people_axis = unit in {"명", "인", "people", "persons"} and any(
        x in hint for x in ("직원", "임직원", "종업원", "인원", "이사", "참석자")
    )
    if code.startswith("E-") and ("매출" in hint or people_axis):
        reasons.append("다른축수치→E코드")
    if code == "G-1-2" and any(x in hint for x in ("출석", "참석")):
        reasons.append("출석률→사외이사비율")
    if code == "G-6-2" and "내부거래위원회" in hint:
        reasons.append("위원회운영→내부거래공시")
    return sorted(set(reasons))


def _evaluate(metrics: list[CachedMetric]) -> dict[str, Any]:
    legacy = _legacy_resolver()
    rows: list[dict[str, Any]] = []
    for metric in metrics:
        extracted = metric.extracted()
        before_raw = legacy(extracted)
        # legacy는 GRI 번호 같은 임의 문자열도 그대로 돌려줬다. 배정 성공률은 실제
        # K-ESG 코드만 세므로 그런 값은 미배정(None)으로 정규화한다.
        before = before_raw if before_raw and by_code(before_raw) is not None else None
        exact = _resolve_kesg_code(extracted)
        fuzzy = _resolve_kesg_code(extracted, allow_fuzzy=True)
        rows.append({
            "metric": metric,
            "before": before,
            "exact": exact,
            "fuzzy": fuzzy,
            "exact_error": _obvious_error_reasons(metric, exact),
            "fuzzy_error": _obvious_error_reasons(metric, fuzzy),
        })
    return {"rows": rows}


def _assignment_summary(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for _source, (_ticker, company) in TARGETS.items():
        company_rows = [row for row in rows if row["metric"].company == company]
        out[company] = sum(row[field] is not None for row in company_rows)
    return out


def _coverage(rows: list[dict[str, Any]], field: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for _source, (_ticker, company) in TARGETS.items():
        codes = {
            row[field]
            for row in rows
            if row["metric"].company == company and str(row[field] or "").startswith("E-")
        }
        out[company] = sorted(codes)
    return out


def _changes(
    rows: list[dict[str, Any]], field: str, *, baseline: str = "before"
) -> list[dict[str, Any]]:
    return [
        row for row in rows
        if row[field] is not None and row[field] != row[baseline]
    ]


def _print_report(result: dict[str, Any], sample_limit: int) -> None:
    rows = result["rows"]
    print(f"baseline_commit={BASELINE_COMMIT}")
    print(f"cache_unique_metrics={len(rows)}")
    print("\n[회사별 배정 성공]")
    before = _assignment_summary(rows, "before")
    exact = _assignment_summary(rows, "exact")
    fuzzy = _assignment_summary(rows, "fuzzy")
    for company in before:
        total = sum(row["metric"].company == company for row in rows)
        print(
            f"{company}\t{before[company]}/{total} ({100*before[company]/total:.1f}%)\t"
            f"{exact[company]}/{total} ({100*exact[company]/total:.1f}%)\t"
            f"{fuzzy[company]}/{total} ({100*fuzzy[company]/total:.1f}%)"
        )

    print("\n[회사별 E 코드 커버리지 예상 — 캐시 배정 고유 코드/17]")
    cov_before = _coverage(rows, "before")
    cov_exact = _coverage(rows, "exact")
    cov_fuzzy = _coverage(rows, "fuzzy")
    for company in cov_before:
        print(
            f"{company}\t{len(cov_before[company])}/17 {cov_before[company]}\t"
            f"{len(cov_exact[company])}/17 {cov_exact[company]}\t"
            f"{len(cov_fuzzy[company])}/17 {cov_fuzzy[company]}"
        )

    for field, baseline, label, error_key in (
        ("exact", "before", "exact 신규/변경(legacy 대비)", "exact_error"),
        ("fuzzy", "exact", "fuzzy 추가/변경(exact 대비)", "fuzzy_error"),
    ):
        changed = _changes(rows, field, baseline=baseline)
        errors = [row for row in changed if row[error_key]]
        rate = 100.0 * len(errors) / max(len(changed), 1)
        print(f"\n[{label}] {len(changed)}건, 명백 오배정 {len(errors)}건 ({rate:.2f}%)")
        by_code_count = collections.Counter(str(row[field]) for row in changed)
        by_reason = collections.Counter(
            reason for row in errors for reason in row[error_key]
        )
        print("code_counts=" + ", ".join(f"{code}:{count}" for code, count in by_code_count.most_common()))
        print("error_reasons=" + (
            ", ".join(f"{reason}:{count}" for reason, count in by_reason.most_common()) or "-"
        ))
        aggregates: collections.Counter[tuple[str, str | None, str, str]] = collections.Counter()
        for row in changed:
            metric = row["metric"]
            reason = ",".join(row[error_key]) or "-"
            aggregates[(metric.company, row[baseline], str(row[field]), metric.hint, reason)] += 1
        for (company, old, new, hint, reason), count in list(aggregates.items())[:sample_limit]:
            print(f"{company}\t{old or '-'}→{new}\t{hint}\t{reason}\tx{count}")

    exact_new = [row for row in rows if row["before"] is None and row["exact"] is not None]
    exact_errors = [row for row in exact_new if row["exact_error"]]
    exact_reassigned = [
        row for row in rows
        if row["before"] is not None and row["exact"] is not None
        and row["exact"] != row["before"]
    ]
    exact_lost = [row for row in rows if row["before"] is not None and row["exact"] is None]
    gate_rate = 100.0 * len(exact_errors) / max(len(exact_new), 1)
    print(
        f"\nexact_new={len(exact_new)} exact_reassigned={len(exact_reassigned)} "
        f"exact_lost={len(exact_lost)}"
    )
    print(f"\nGATE exact_obvious_error_rate={gate_rate:.2f}% threshold=10.00% verdict="
          f"{'STOP' if gate_rate > 10.0 else 'PASS'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "data" / "_cache" / "ocr")
    parser.add_argument("--sample-limit", type=int, default=300)
    args = parser.parse_args()
    metrics = _load_metrics(args.cache_dir)
    if not metrics:
        print("측정할 5개사 OCR 캐시 metric이 없습니다.", file=sys.stderr)
        return 1
    result = _evaluate(metrics)
    _print_report(result, args.sample_limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
