"""그린워싱 검출 벤치마크 — 룰 단독 vs 하이브리드 vs LLM 단독.

실행:
    python -m esgenie.benchmark                       # 3개 검출기 전체 비교
    python -m esgenie.benchmark --detectors rule hybrid
    JUDGE_TRIGGER=0.3 python -m esgenie.benchmark     # 임계치 실험

데이터셋: data/benchmark/greenwash_bench.json
  - 005930 샘플 DART 수치를 정답 앵커로 사용한 라벨링 문장
  - 카테고리: pure_exaggeration / backed_modifier / numeric_mismatch /
              numeric_match / timeseries_contradiction / future_plan / clean_factual

판정 규칙 (flagged = 그린워싱 의심):
  aggregate.risk_score >= threshold(0.25)
  OR max(D1, D2, D5) >= axis_flag(0.8)      # 단일 축 강신호 (D5 저가중치 보완)

  ※ D3(의미 일관성)는 RAG 컨텍스트 의존이라 본 벤치마크에서는 중립값으로 고정
    (문장 단위 ground-truth가 D3에 대해 정의 불가하기 때문)

출력: 콘솔 비교표 + outputs/benchmark/benchmark_{ts}.json / .md

⚠ MOCK 모드 경고: LLM 키가 없으면 판정·분류가 결정적 mock으로 대체된다.
  mock 수치는 아키텍처 데모용일 뿐 성능 주장에 사용할 수 없다.
  실제 성능표는 OPENAI_API_KEY 또는 ANTHROPIC_API_KEY 설정 후 재실행할 것.
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import DATA_DIR, ROOT_DIR, SETTINGS
from .schemas import RiskVector

BENCH_PATH = DATA_DIR / "benchmark" / "greenwash_bench.json"
OUTPUT_DIR = ROOT_DIR / "outputs" / "benchmark"

DEFAULT_THRESHOLD = 0.25   # aggregate 판정 경계 (RISK_LEVEL low 경계와 동일)
DEFAULT_AXIS_FLAG = 0.80   # 단일 축 강신호 경계

CLASSIFY_SYSTEM = """\
당신은 ESG 공시 그린워싱 분류기다. 문장과 기업 실측 데이터를 보고
그 문장이 그린워싱(과장·허위·오도 표현)인지 판정하라.
JSON으로만 응답: {"greenwash": true|false, "confidence": 0.0~1.0, "rationale": "근거"}"""

CLASSIFY_PROMPT = """\
[[GW_CLASSIFY]]

[문장]
{sentence}

[기업 실측 데이터 (DART 기준)]
{evidence}

위 문장이 그린워싱인지 JSON으로 판정하라."""


# ====================================================================
# 결과 스키마
# ====================================================================

@dataclass
class CaseResult:
    case_id: str
    category: str
    label: str            # greenwash | clean
    flagged: bool
    risk_score: float
    detail: str = ""

    @property
    def correct(self) -> bool:
        return self.flagged == (self.label == "greenwash")


@dataclass
class DetectorReport:
    name: str
    cases: list[CaseResult] = field(default_factory=list)
    llm_calls: int = 0

    # ---- 지표 ----------------------------------------------------------
    def _counts(self) -> tuple[int, int, int, int]:
        tp = sum(1 for c in self.cases if c.label == "greenwash" and c.flagged)
        fp = sum(1 for c in self.cases if c.label == "clean" and c.flagged)
        fn = sum(1 for c in self.cases if c.label == "greenwash" and not c.flagged)
        tn = sum(1 for c in self.cases if c.label == "clean" and not c.flagged)
        return tp, fp, fn, tn

    def metrics(self) -> dict[str, float]:
        tp, fp, fn, tn = self._counts()
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        accuracy = (tp + tn) / len(self.cases) if self.cases else 0.0
        return {
            "precision": round(precision, 3), "recall": round(recall, 3),
            "f1": round(f1, 3), "accuracy": round(accuracy, 3),
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "llm_calls": self.llm_calls,
        }

    def by_category(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for c in self.cases:
            d = out.setdefault(c.category, {"total": 0, "correct": 0})
            d["total"] += 1
            d["correct"] += int(c.correct)
        for d in out.values():
            d["accuracy"] = round(d["correct"] / d["total"], 3)
        return out


# ====================================================================
# LLM 호출 카운터
# ====================================================================

class CountingLLM:
    """LLMClient 래퍼 — complete() 호출 수를 센다 (비용 비교용)."""

    def __init__(self, inner: Any | None = None) -> None:
        if inner is None:
            from .llm import LLMClient
            inner = LLMClient()
        self._inner = inner
        self.calls = 0

    def complete(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        return self._inner.complete(*args, **kwargs)


# ====================================================================
# 벤치마크 실행
# ====================================================================

def load_benchmark(path: Path = BENCH_PATH) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fp:
        return json.load(fp)


def _flagged(rv: RiskVector, threshold: float, axis_flag: float) -> tuple[bool, float]:
    score = rv.risk_score
    max_axis = max(rv.D1_numeric.score, rv.D2_modifier.score, rv.D5_timeseries.score)
    return (score >= threshold or max_axis >= axis_flag), score


def _evidence_table(report: Any) -> str:
    """LLM-only 베이스라인에 제공할 실측 데이터 요약 (공정 비교 — 동일 정보 접근)."""
    rows = []
    for code, e in report.kesg_data.items():
        v = e.get("value")
        if isinstance(v, (int, float)):
            rows.append(f"- {code} {e.get('note', '')[:20]}: {v} {e.get('unit', '')}")
    return "\n".join(rows[:30])


def run_benchmark(
    detectors: list[str] | None = None,
    *,
    ticker: str | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    axis_flag: float = DEFAULT_AXIS_FLAG,
    bench_path: Path = BENCH_PATH,
) -> dict[str, DetectorReport]:
    """벤치마크 실행 → {detector_name: DetectorReport}."""
    from .dart_client import load_report
    from .layer0_evidence_graph import build_evidence_graph
    from .layer3_detect import detect_risk_vector
    from .layer3_judge import judge_risk_vector

    detectors = detectors or ["rule", "hybrid", "llm_only"]
    bench = load_benchmark(bench_path)
    cases = bench["cases"]

    report = load_report(ticker or bench.get("ticker", "005930"))
    graph = build_evidence_graph(report)
    evidence_txt = _evidence_table(report)

    reports: dict[str, DetectorReport] = {d: DetectorReport(name=d) for d in detectors}
    hybrid_llm = CountingLLM()
    only_llm = CountingLLM()

    for case in cases:
        sent, label = case["sentence"], case["label"]

        # ── 룰 1차 (rule/hybrid 공용) ─────────────────────────────────
        rule_rv = detect_risk_vector(sent, evidence_graph=graph)

        if "rule" in reports:
            flagged, score = _flagged(rule_rv, threshold, axis_flag)
            reports["rule"].cases.append(CaseResult(
                case["id"], case["category"], label, flagged, score,
                detail=rule_rv.aggregate.get("top_axis", ""),
            ))

        if "hybrid" in reports:
            import copy
            hyb_rv = judge_risk_vector(sent, copy.deepcopy(rule_rv), llm=hybrid_llm)
            flagged, score = _flagged(hyb_rv, threshold, axis_flag)
            j = hyb_rv.aggregate.get("judge", {})
            reports["hybrid"].cases.append(CaseResult(
                case["id"], case["category"], label, flagged, score,
                detail=str(j.get("verdicts", j.get("reason", ""))),
            ))

        if "llm_only" in reports:
            resp = only_llm.complete(
                system=CLASSIFY_SYSTEM,
                user=CLASSIFY_PROMPT.format(sentence=sent, evidence=evidence_txt),
                mock_hint="classify",
                json_mode=True,
                temperature=0.0,
            )
            pred = _parse_classify(resp.content)
            reports["llm_only"].cases.append(CaseResult(
                case["id"], case["category"], label,
                flagged=pred.get("greenwash", False),
                risk_score=float(pred.get("confidence", 0.0)),
                detail=str(pred.get("rationale", ""))[:60],
            ))

    if "hybrid" in reports:
        reports["hybrid"].llm_calls = hybrid_llm.calls
    if "llm_only" in reports:
        reports["llm_only"].llm_calls = only_llm.calls
    return reports


def _parse_classify(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    return {"greenwash": False, "confidence": 0.0, "rationale": "파싱 실패"}


# ====================================================================
# 리포트 출력
# ====================================================================

_DETECTOR_LABELS = {
    "rule": "룰 단독 (1차)",
    "hybrid": "하이브리드 (룰+LLM)",
    "llm_only": "LLM 단독",
}


def format_report(reports: dict[str, DetectorReport], *, n_cases: int) -> str:
    lines: list[str] = []
    mock = SETTINGS.use_mock_llm
    lines.append("# 그린워싱 검출 벤치마크 결과")
    lines.append("")
    lines.append(f"- 케이스: {n_cases}개 | 실행: {datetime.datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- LLM 모드: {'⚠ MOCK (데모용 — 성능 주장 사용 금지, 실키로 재실행 필요)' if mock else f'실모델 ({SETTINGS.openai_model if SETTINGS.openai_api_key else SETTINGS.anthropic_model})'}")
    lines.append("")
    lines.append("## 종합 지표")
    lines.append("")
    lines.append("| 검출기 | Precision | Recall | F1 | Accuracy | LLM 호출 |")
    lines.append("|---|---|---|---|---|---|")
    for name, rep in reports.items():
        m = rep.metrics()
        lines.append(
            f"| {_DETECTOR_LABELS.get(name, name)} | {m['precision']:.3f} | {m['recall']:.3f} "
            f"| {m['f1']:.3f} | {m['accuracy']:.3f} | {m['llm_calls']} |"
        )
    lines.append("")
    lines.append("## 카테고리별 정확도")
    lines.append("")
    cats = sorted({c.category for rep in reports.values() for c in rep.cases})
    header = "| 카테고리 | " + " | ".join(_DETECTOR_LABELS.get(n, n) for n in reports) + " |"
    lines.append(header)
    lines.append("|---" * (len(reports) + 1) + "|")
    for cat in cats:
        row = [cat]
        for rep in reports.values():
            bc = rep.by_category().get(cat, {})
            row.append(f"{bc.get('correct', 0)}/{bc.get('total', 0)}")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    lines.append("## 오답 상세")
    lines.append("")
    for name, rep in reports.items():
        wrong = [c for c in rep.cases if not c.correct]
        lines.append(f"### {_DETECTOR_LABELS.get(name, name)} — 오답 {len(wrong)}건")
        for c in wrong:
            kind = "오탐(FP)" if c.label == "clean" else "미탐(FN)"
            lines.append(f"- [{kind}] {c.case_id} ({c.category}) score={c.risk_score:.3f} {c.detail}")
        lines.append("")
    return "\n".join(lines)


def save_report(
    reports: dict[str, DetectorReport],
    *,
    n_cases: int,
    out_dir: Path = OUTPUT_DIR,
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = out_dir / f"benchmark_{ts}.md"
    json_path = out_dir / f"benchmark_{ts}.json"

    md_path.write_text(format_report(reports, n_cases=n_cases), encoding="utf-8")
    payload = {
        "generated_at": datetime.datetime.now().isoformat(),
        "mock_mode": SETTINGS.use_mock_llm,
        "detectors": {
            name: {
                "metrics": rep.metrics(),
                "by_category": rep.by_category(),
                "cases": [vars(c) for c in rep.cases],
            }
            for name, rep in reports.items()
        },
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return md_path, json_path


# ====================================================================
# CLI
# ====================================================================

def _cli() -> None:
    parser = argparse.ArgumentParser(description="ESGenie 그린워싱 검출 벤치마크")
    parser.add_argument("--detectors", nargs="+", default=["rule", "hybrid", "llm_only"],
                        choices=["rule", "hybrid", "llm_only"])
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--axis-flag", type=float, default=DEFAULT_AXIS_FLAG)
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()

    bench = load_benchmark()
    reports = run_benchmark(
        args.detectors, threshold=args.threshold, axis_flag=args.axis_flag,
    )
    text = format_report(reports, n_cases=len(bench["cases"]))
    print(text)
    if not args.no_save:
        md, js = save_report(reports, n_cases=len(bench["cases"]))
        print(f"\n저장: {md}\n      {js}")


if __name__ == "__main__":
    _cli()
