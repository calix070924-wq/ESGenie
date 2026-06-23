"""Bootstrap high-trust RAG eval sets from local primary-source fixtures."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import DATA_DIR

RAG_EVAL_DIR = DATA_DIR / "rag_eval"
SAMPLE_DART_DIR = DATA_DIR / "sample_dart"

RETRIEVAL_SPECS: list[dict[str, Any]] = [
    {"corp_code": "005930", "area": "E", "code": "E-3-1", "query": "삼성전자 Scope1+2 글로벌 온실가스 배출량"},
    {"corp_code": "005930", "area": "E", "code": "E-4-1", "query": "삼성전자 연간 총 에너지 사용량"},
    {"corp_code": "005930", "area": "E", "code": "E-4-2", "query": "삼성전자 재생에너지 사용 비율"},
    {"corp_code": "005930", "area": "E", "code": "E-6-2", "query": "삼성전자 폐기물 재활용 비율"},
    {"corp_code": "005930", "area": "S", "code": "S-2-2", "query": "삼성전자 국내 정규직 비율"},
    {"corp_code": "005930", "area": "S", "code": "S-4-2", "query": "삼성전자 재해율"},
    {"corp_code": "005930", "area": "G", "code": "G-1-2", "query": "삼성전자 사외이사 비율"},
    {"corp_code": "005930", "area": "G", "code": "G-2-1", "query": "삼성전자 이사회 출석률"},
    {"corp_code": "005380", "area": "E", "code": "E-3-1", "query": "현대차 Scope1+2 글로벌 온실가스 배출량"},
    {"corp_code": "005380", "area": "E", "code": "E-4-2", "query": "현대차 재생에너지 사용 비율"},
    {"corp_code": "005380", "area": "E", "code": "E-6-1", "query": "현대차 폐기물 배출량"},
    {"corp_code": "005380", "area": "S", "code": "S-2-2", "query": "현대차 국내 정규직 비율"},
    {"corp_code": "005380", "area": "S", "code": "S-2-3", "query": "현대차 자발적 이직률"},
    {"corp_code": "005380", "area": "S", "code": "S-4-2", "query": "현대차 재해율"},
    {"corp_code": "005380", "area": "G", "code": "G-1-2", "query": "현대차 사외이사 비율"},
    {"corp_code": "005380", "area": "G", "code": "G-2-1", "query": "현대차 이사회 출석률"},
    {"corp_code": "005490", "area": "E", "code": "E-3-1", "query": "포스코 Scope1+2 철강 사업장 중심 배출량"},
    {"corp_code": "005490", "area": "E", "code": "E-4-2", "query": "포스코 재생에너지 사용 비율"},
    {"corp_code": "005490", "area": "E", "code": "E-6-2", "query": "포스코 폐기물 재활용 비율"},
    {"corp_code": "005490", "area": "S", "code": "S-2-2", "query": "포스코 정규직 비율"},
    {"corp_code": "005490", "area": "S", "code": "S-4-2", "query": "포스코 재해율"},
    {"corp_code": "005490", "area": "G", "code": "G-1-2", "query": "포스코 사외이사 비율"},
    {"corp_code": "005490", "area": "G", "code": "G-2-1", "query": "포스코 이사회 출석률"},
    {"corp_code": "005490", "area": "G", "code": "G-3-4", "query": "포스코 배당 성향"},
    {"corp_code": "SME001", "area": "E", "code": "E-3-1", "query": "한울정밀 온실가스 배출량 추정치"},
    {"corp_code": "SME001", "area": "E", "code": "E-4-1", "query": "한울정밀 사업보고서 에너지 사용량"},
    {"corp_code": "SME001", "area": "E", "code": "E-6-1", "query": "한울정밀 폐기물 배출량"},
    {"corp_code": "SME001", "area": "G", "code": "G-1-2", "query": "한울정밀 사외이사 비율"},
    {"corp_code": "SME001", "area": "G", "code": "G-3-4", "query": "한울정밀 현금배당성향"},
    {"corp_code": "SME002", "area": "E", "code": "E-3-1", "query": "대원전자 온실가스 배출량 추정치"},
    {"corp_code": "SME002", "area": "E", "code": "E-4-1", "query": "대원전자 사업보고서 에너지 사용량"},
    {"corp_code": "SME002", "area": "E", "code": "E-4-2", "query": "대원전자 재생에너지 비율"},
    {"corp_code": "SME002", "area": "E", "code": "E-5-1", "query": "대원전자 용수 사용량"},
    {"corp_code": "SME002", "area": "E", "code": "E-6-2", "query": "대원전자 폐기물 재활용 비율"},
    {"corp_code": "SME002", "area": "S", "code": "S-3-1", "query": "대원전자 재해율"},
    {"corp_code": "SME002", "area": "G", "code": "G-1-2", "query": "대원전자 사외이사 비율"},
    {"corp_code": "SME002", "area": "G", "code": "G-2-1", "query": "대원전자 이사회 출석률"},
    {"corp_code": "SME002", "area": "G", "code": "G-3-4", "query": "대원전자 현금배당성향"},
]

NEGATIVE_SPECS: list[dict[str, Any]] = [
    {"corp_code": "005930", "area": "E", "query": "삼성전자 환경영향평가 인증 등급"},
    {"corp_code": "005490", "area": "S", "query": "포스코 노동조합 가입률"},
    {"corp_code": "005380", "area": "G", "query": "현대차 여성 임원 비율"},
    {"corp_code": "SME001", "area": "S", "query": "한울정밀 개인정보 침해 건수"},
    {"corp_code": "SME002", "area": "E", "query": "대원전자 생물다양성 복원 면적"},
    {"corp_code": "005930", "area": "S", "query": "삼성전자 공급망 인권 실사 건수"},
]

GROUNDING_SOURCE_SPECS: list[dict[str, str]] = [
    {"corp_code": "005930", "code": "E-3-1"},
    {"corp_code": "005930", "code": "E-4-2"},
    {"corp_code": "005930", "code": "S-2-2"},
    {"corp_code": "005930", "code": "G-1-2"},
    {"corp_code": "005380", "code": "E-6-1"},
    {"corp_code": "005380", "code": "S-4-2"},
    {"corp_code": "005490", "code": "E-6-2"},
    {"corp_code": "005490", "code": "G-2-1"},
    {"corp_code": "SME001", "code": "E-4-1"},
    {"corp_code": "SME001", "code": "G-3-4"},
    {"corp_code": "SME002", "code": "E-6-2"},
    {"corp_code": "SME002", "code": "G-2-1"},
]


def bootstrap() -> dict[str, int]:
    sample_reports = _load_sample_reports()
    retrieval_rows = _build_retrieval_rows(sample_reports)
    grounding_rows = _build_grounding_rows(sample_reports)
    readme = _build_readme()

    RAG_EVAL_DIR.mkdir(parents=True, exist_ok=True)
    _write_jsonl(RAG_EVAL_DIR / "retrieval_qrels.jsonl", retrieval_rows)
    _write_jsonl(RAG_EVAL_DIR / "grounding_labels.jsonl", grounding_rows)
    (RAG_EVAL_DIR / "README.md").write_text(readme, encoding="utf-8")
    return {"retrieval_qrels": len(retrieval_rows), "grounding_labels": len(grounding_rows)}


def _build_retrieval_rows(sample_reports: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx, spec in enumerate(RETRIEVAL_SPECS, start=1):
        report = sample_reports[spec["corp_code"]]
        entry = report["kesg_data"][spec["code"]]
        rows.append({
            "query_id": f"ret-{idx:03d}",
            "corp_code": spec["corp_code"],
            "area": spec["area"],
            "query": spec["query"],
            "relevant_chunk_ids": [f"corp_{spec['corp_code']}_{spec['code']}"],
            "source_type": "sample_dart_structured",
            "source_file": report["_source_file"],
            "source_note": entry.get("note", ""),
            "source_value": entry.get("value"),
            "source_unit": entry.get("unit", ""),
            "source_code": spec["code"],
            "label_method": "direct_from_sample_dart",
        })
    offset = len(rows)
    for idx, spec in enumerate(NEGATIVE_SPECS, start=1):
        rows.append({
            "query_id": f"ret-{offset + idx:03d}",
            "corp_code": spec["corp_code"],
            "area": spec["area"],
            "query": spec["query"],
            "relevant_chunk_ids": [],
            "source_type": "negative_absent_metric",
            "source_file": sample_reports[spec["corp_code"]]["_source_file"],
            "label_method": "manual_absence_check_against_sample_dart",
        })
    return rows


def _build_grounding_rows(sample_reports: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    case_no = 1
    for spec in GROUNDING_SOURCE_SPECS:
        report = sample_reports[spec["corp_code"]]
        entry = report["kesg_data"][spec["code"]]
        chunk_id = f"corp_{spec['corp_code']}_{spec['code']}"
        note = str(entry.get("note", "")).strip()
        value = entry.get("value")
        unit = str(entry.get("unit", "")).strip()
        if value is None:
            continue
        value_text = _format_value(value)
        sentence = f"{note}은 {value_text}{_format_unit(unit)}입니다"
        cited_chunks = [{"id": chunk_id, "text": f"[DART/{spec['code']}] {note} 수치: {value_text} {unit}".strip()}]

        rows.append({
            "case_id": f"grd-{case_no:03d}",
            "answer": f"{sentence} [{chunk_id}]",
            "cited_chunks": cited_chunks,
            "faithful": True,
            "hallucinated_numbers": [],
            "has_uncited_claim": False,
            "source_type": "sample_dart_structured",
            "source_file": report["_source_file"],
            "source_code": spec["code"],
            "label_method": "exact_from_sample_dart",
        })
        case_no += 1

        corrupted = _corrupt_value(value)
        rows.append({
            "case_id": f"grd-{case_no:03d}",
            "answer": f"{note}은 {corrupted}{_format_unit(unit)}입니다 [{chunk_id}]",
            "cited_chunks": cited_chunks,
            "faithful": False,
            "hallucinated_numbers": [str(corrupted).replace(",", "")],
            "has_uncited_claim": False,
            "source_type": "controlled_negative",
            "source_file": report["_source_file"],
            "source_code": spec["code"],
            "label_method": "numeric_corruption_from_sample_dart",
        })
        case_no += 1

        rows.append({
            "case_id": f"grd-{case_no:03d}",
            "answer": sentence,
            "cited_chunks": cited_chunks,
            "faithful": False,
            "hallucinated_numbers": [],
            "has_uncited_claim": True,
            "source_type": "controlled_negative",
            "source_file": report["_source_file"],
            "source_code": spec["code"],
            "label_method": "citation_removed_from_sample_dart",
        })
        case_no += 1
    return rows


def _load_sample_reports() -> dict[str, dict[str, Any]]:
    reports: dict[str, dict[str, Any]] = {}
    for path in sorted(SAMPLE_DART_DIR.glob("*.json")):
        obj = json.loads(path.read_text(encoding="utf-8"))
        obj["_source_file"] = path.name
        reports[obj["corp_code"]] = obj
    return reports


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as fp:
        for row in rows:
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")


def _build_readme() -> str:
    return """# RAG Eval Set

이 디렉터리의 평가지표는 로컬에 저장된 1차 원천자료만 사용한다.

출처
- `data/sample_dart/*.json`: DART 기반 정형 수치 스냅샷
- `data/test_docs/*.pdf`: OCR/SSOT 확장용 원천 증빙 PDF

라벨링 원칙
- retrieval qrels: `sample_dart`의 실제 K-ESG 코드 → `corp_{corp_code}_{code}` 청크로 직접 매핑
- grounding labels: 동일 원천 문구/숫자에서 faithful 문장을 만들고,
  negative는 숫자 1개 변경 또는 citation 제거로만 통제 생성
- 블로그, 뉴스, 2차 요약본, LLM 자유 생성 문장은 gold source로 사용하지 않음
"""


def _format_value(value: Any) -> str:
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        if value.is_integer():
            return f"{int(value):,}"
        return f"{value:,.3f}".rstrip("0").rstrip(".")
    return str(value)


def _format_unit(unit: str) -> str:
    return f" {unit}" if unit else ""


def _corrupt_value(value: Any) -> str:
    if isinstance(value, int):
        if value == 0:
            return "1"
        return f"{value + 1:,}"
    if isinstance(value, float):
        delta = 0.1 if abs(value) < 10 else max(abs(value) * 0.05, 1.0)
        corrupted = value + delta
        if corrupted.is_integer():
            return f"{int(corrupted):,}"
        return f"{corrupted:,.3f}".rstrip("0").rstrip(".")
    return str(value)


if __name__ == "__main__":
    counts = bootstrap()
    print(json.dumps(counts, ensure_ascii=False, indent=2))
