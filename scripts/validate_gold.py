"""gold_regulatory.jsonl 스키마 / 출처 / 누수 / 분포 검증.

게이트:
  G1  JSON 파싱 — 모든 줄이 valid JSON
  G2  스키마 — 필수 필드 존재, 신규 케이스(GOLD-15+)는 source_url 필수
  G3  eval_track — text_classification | out_of_scope 만 허용
  G4  레이블 — label="greenwash", gold=true 확인
  G5  중복 — 정규화 문장 기준 내부 중복 없음
  G6  누수 — 골드 문장이 layer3_judge.py few-shot 예시에 없음
  G7  카테고리 분포 — text_class 양성 기준 카테고리별 >=4
  G8  기관 분포 — source_url 도메인 기준 단일 도메인 <=40%
  G9  섹터 다양성 — text_class 케이스 기준 고유 섹터 >=6

FAIL 게이트가 하나라도 있으면 종료 코드 1.
"""
from __future__ import annotations
import json, re, sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
GOLD_PATH = ROOT / "data" / "benchmark_v2" / "gold_regulatory.jsonl"
JUDGE_PATH = ROOT / "esgenie" / "layer3_judge.py"
OUT_DIR = ROOT / "outputs" / "benchmark"
OUT_DIR.mkdir(parents=True, exist_ok=True)

REQUIRED_BASE = {"id", "sentence", "sector", "label", "gold", "category",
                 "axes", "rationale", "source", "labeler", "split",
                 "judgeable_from", "eval_track"}
REQUIRED_NEW = REQUIRED_BASE | {"source_url"}   # GOLD-15+ 신규 케이스
NEW_CUTOFF = "GOLD-15"

VALID_CATEGORIES = {
    "absolute_unsubstantiated", "condition_omitted",
    "partial_truth", "vague_abstract", "vague_label",
}
VALID_EVAL_TRACK = {"text_classification", "out_of_scope"}


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "").strip().lower()


def _extract_few_shot_sentences(path: Path) -> set[str]:
    """layer3_judge.py 에서 따옴표 속 한국어/영어 문장 추출."""
    content = path.read_text(encoding="utf-8")
    matches = re.findall(r'"([^"]{10,200})"', content)
    return {_norm(m) for m in matches}


def _domain_group(url: str) -> str:
    """출처 URL을 도메인 그룹(기관)으로 분류."""
    if not url:
        return "unknown"
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return "unknown"
    if "casenote.kr" in host or "ftc.go.kr" in host:
        return "KR-공정위"
    if "acm.nl" in host:
        return "NL-ACM"
    if "ftc.gov" in host:
        return "US-FTC"
    if "gov.uk" in host:
        return "UK-CMA"
    if "fss.or.kr" in host:
        return "KR-금감원"
    if "keiti.re.kr" in host or "me.go.kr" in host:
        return "KR-환경부"
    return host


def main() -> None:
    lines = [l for l in GOLD_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    few_shot = _extract_few_shot_sentences(JUDGE_PATH)

    results: list[tuple[str, str, str]] = []   # (gate, status, detail)
    records: list[dict] = []
    parse_errors: list[str] = []

    # G1 JSON 파싱
    for i, line in enumerate(lines, 1):
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as e:
            parse_errors.append(f"  line {i}: {e}")
    if parse_errors:
        results.append(("G1", "FAIL", f"{len(parse_errors)} JSON 파싱 오류\n" + "\n".join(parse_errors)))
    else:
        results.append(("G1", "PASS", f"{len(records)}줄 모두 valid JSON"))

    # G2 스키마
    schema_errors: list[str] = []
    for r in records:
        rid = r.get("id", "?")
        is_new = rid >= NEW_CUTOFF if rid != "?" else False
        required = REQUIRED_NEW if is_new else REQUIRED_BASE
        missing = required - r.keys()
        if missing:
            schema_errors.append(f"  {rid}: 누락 필드 {sorted(missing)}")
        if r.get("category") and r["category"] not in VALID_CATEGORIES:
            schema_errors.append(f"  {rid}: 알 수 없는 category='{r['category']}'")
    if schema_errors:
        results.append(("G2", "FAIL", f"{len(schema_errors)}건 스키마 오류\n" + "\n".join(schema_errors)))
    else:
        results.append(("G2", "PASS", f"모든 필드 완비 (신규 {sum(1 for r in records if r.get('id','') >= NEW_CUTOFF)}건 source_url 포함)"))

    # G3 eval_track
    invalid_track = [r["id"] for r in records if r.get("eval_track") not in VALID_EVAL_TRACK]
    if invalid_track:
        results.append(("G3", "FAIL", f"eval_track 비정상: {invalid_track}"))
    else:
        tc = sum(1 for r in records if r.get("eval_track") == "text_classification")
        oos = sum(1 for r in records if r.get("eval_track") == "out_of_scope")
        results.append(("G3", "PASS", f"text_classification={tc}  out_of_scope={oos}"))

    # G4 레이블
    label_errors = [r["id"] for r in records
                    if r.get("label") != "greenwash" or r.get("gold") is not True]
    if label_errors:
        results.append(("G4", "FAIL", f"label/gold 비정상: {label_errors}"))
    else:
        results.append(("G4", "PASS", "모두 label=greenwash, gold=true"))

    # G5 중복
    norm_sents: dict[str, str] = {}
    dups: list[str] = []
    for r in records:
        key = _norm(r.get("sentence", ""))
        if key in norm_sents:
            dups.append(f"  {r['id']} == {norm_sents[key]}")
        else:
            norm_sents[key] = r["id"]
    if dups:
        results.append(("G5", "FAIL", f"중복 문장 {len(dups)}쌍\n" + "\n".join(dups)))
    else:
        results.append(("G5", "PASS", f"중복 없음 ({len(norm_sents)}개 고유 문장)"))

    # G6 누수 (few-shot 예시와 겹치면 경고)
    leaks: list[str] = []
    for r in records:
        if _norm(r.get("sentence", "")) in few_shot:
            leaks.append(f"  {r['id']}: '{r['sentence'][:60]}'")
    if leaks:
        results.append(("G6", "WARN", f"layer3_judge.py few-shot에 {len(leaks)}건 노출(기존 누수)\n" + "\n".join(leaks)))
    else:
        results.append(("G6", "PASS", "few-shot 예시 누수 없음"))

    # G7 카테고리 분포 (text_class 전체 기준)
    tc_records = [r for r in records if r.get("eval_track") == "text_classification"]
    cat_counts = Counter(r.get("category") for r in tc_records)
    cat_errors = [f"  {c}: {n}건 (>=4 필요)" for c, n in cat_counts.items() if n < 4]
    # 기존 카테고리 누락 체크
    for c in VALID_CATEGORIES:
        if c not in cat_counts:
            cat_errors.append(f"  {c}: 0건 (누락)")
    if cat_errors:
        results.append(("G7", "FAIL", f"카테고리 분포 미달\n" + "\n".join(cat_errors)
                        + f"\n  현황: {dict(cat_counts)}"))
    else:
        results.append(("G7", "PASS", f"카테고리 분포 OK: {dict(cat_counts)}"))

    # G8 기관 분포 (신규 케이스만, source_url 도메인 기준)
    new_records = [r for r in tc_records if r.get("id", "") >= NEW_CUTOFF]
    n_new = len(new_records)
    if n_new > 0:
        agency_counts = Counter(_domain_group(r.get("source_url", "")) for r in new_records)
        agency_errors = [f"  {ag}: {n}/{n_new} = {n/n_new*100:.1f}% (>40%)"
                         for ag, n in agency_counts.items() if n / n_new > 0.40]
        if agency_errors:
            results.append(("G8", "FAIL", f"기관 집중 위반\n" + "\n".join(agency_errors)
                            + f"\n  현황: {dict(agency_counts)}"))
        else:
            results.append(("G8", "PASS", f"기관 분포 OK: " + ", ".join(
                f"{ag}={n}/{n_new}({n/n_new*100:.0f}%)" for ag, n in sorted(agency_counts.items()))))
    else:
        results.append(("G8", "SKIP", "신규 케이스 없음"))

    # G9 섹터 다양성
    sectors = {r.get("sector") for r in tc_records if r.get("sector")}
    if len(sectors) < 6:
        results.append(("G9", "FAIL", f"고유 섹터 {len(sectors)}개 (<6): {sorted(sectors)}"))
    else:
        results.append(("G9", "PASS", f"고유 섹터 {len(sectors)}개: {sorted(sectors)}"))

    # 결과 출력
    print("=" * 60)
    print("validate_gold 결과")
    print("=" * 60)
    fail_count = 0
    warn_count = 0
    for gate, status, detail in results:
        tag = {"PASS": "[PASS]", "FAIL": "[FAIL]", "WARN": "[WARN]", "SKIP": "[SKIP]"}[status]
        print(f"{tag} {gate}  {detail.split(chr(10))[0]}")
        if "\n" in detail:
            for sub in detail.split("\n")[1:]:
                print(f"       {sub}")
        if status == "FAIL":
            fail_count += 1
        elif status == "WARN":
            warn_count += 1
    print("=" * 60)
    print(f"FAIL={fail_count}  WARN={warn_count}")

    # 마크다운 저장
    lines_md = ["# validate_gold 결과\n",
                f"- FAIL={fail_count}  WARN={warn_count}\n"]
    for gate, status, detail in results:
        lines_md.append(f"## {gate} {status}\n{detail}\n")
    (OUT_DIR / "validate_gold_result.md").write_text(
        "\n".join(lines_md), encoding="utf-8")
    print(f"\n저장: {OUT_DIR / 'validate_gold_result.md'}")

    if fail_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
