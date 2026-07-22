"""본문 형식 v2 (커버 항목 결정적 표 + LLM 서술) — 개편안 2026-07-16.

핵심 보장:
- 지표 표는 extraction의 영역 내 항목 전수를 결정적으로 렌더 (반영률 100%)
- 미공시 항목이 '미공시' 행으로 명시
- 공시값 원장 의사 청크가 grounding 게이트에서 인용 가능
- extraction=None이면 기존(v1) 동작 그대로 (하위 호환)
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from esgenie.layer2_rag import (
    HybridRAG,
    RAGContext,
    _area_item_rows,
    _assemble_section_v2,
    _kesg_pseudo_chunk,
    _render_kesg_table,
)


# ---------------------------------------------------------------------------
# 픽스처
# ---------------------------------------------------------------------------

class _FakeReport:
    corp_code = "005930"
    corp_name = "테스트기업"
    industry = "전자"
    report_year = 2025

    def to_context_dict(self):
        return {"corp_name": self.corp_name, "kesg_data": {}}


def _extraction(mapped=None, missing=None):
    return SimpleNamespace(mapped=mapped or {}, missing=missing or [])


def _mapped_entry(code, area, name="항목", value=10.0, unit="%", *,
                  evidence=None, beyond=False):
    return {
        "code": code, "name": name, "area": area, "value": value,
        "unit": unit, "evidence_node_ids": evidence or [], "beyond_profile": beyond,
    }


NARRATIVE = (
    "## 환경 성과\n\n"
    "### 전략 및 목표\n온실가스를 감축한다 [c_1].\n\n"
    "### 지표 해설\n배출량은 120 tCO2eq 수준이다 [kesg_items_E].\n\n"
    "### 주요 활동\n설비를 개선했다 [c_1].\n\n"
    "### 향후 계획 및 공시 보완 과제\n용수 사용량 공시를 보완한다."
)


class _StubClient:
    def __init__(self, content=NARRATIVE):
        self.content = content
        self.prompts: list[str] = []

    def complete(self, system, user, **kwargs):
        self.prompts.append(user)
        return SimpleNamespace(content=self.content, used_mock=False)


def _empty_ctx():
    return RAGContext(kesg_hits=[], industry_hits=[], corp_hits=[])


def _rag():
    return HybridRAG.__new__(HybridRAG)  # __init__(인덱스 빌드) 우회


# ---------------------------------------------------------------------------
# _area_item_rows
# ---------------------------------------------------------------------------

def test_area_item_rows_splits_covered_and_missing_by_area():
    ext = _extraction(
        mapped={
            "E-3-1": _mapped_entry("E-3-1", "E", "온실가스", 120, "tCO2eq"),
            "S-1-1": _mapped_entry("S-1-1", "S"),  # 다른 영역 — 제외
        },
        missing=["E-4-1", "S-2-1"],  # E-4-1(에너지 사용량)만 E 영역
    )
    covered, missing = _area_item_rows(ext, "E")
    assert [r["code"] for r in covered] == ["E-3-1"]
    assert [r["code"] for r in missing] == ["E-4-1"]
    assert missing[0]["status"] == "미공시"
    assert missing[0]["name"]  # by_code에서 실명 로드


def test_area_item_rows_status_labels():
    """Phase 2: 공시 상태를 증빙 연결 기준으로 분리 (ISSB 표와 동일 어휘)."""
    ext = _extraction(mapped={
        "E-3-1": _mapped_entry("E-3-1", "E", evidence=["n1"]),
        "E-4-1": _mapped_entry("E-4-1", "E", evidence=["survey_E-4-1"]),
        "E-5-1": _mapped_entry("E-5-1", "E", beyond=True),
        "E-6-1": _mapped_entry("E-6-1", "E"),  # 증빙 없음
    })
    covered, _ = _area_item_rows(ext, "E")
    status = {r["code"]: r["status"] for r in covered}
    assert status == {
        "E-3-1": "공시(증빙연결)",
        "E-4-1": "공시(설문)",
        "E-5-1": "공시(프로파일 외)",
        "E-6-1": "공시(자기기재)",
    }


def test_area_item_rows_unit_suspect_flag():
    ext = _extraction(mapped={"E-4-1": _mapped_entry("E-4-1", "E", unit="명")})
    ext.confidence_flags = {"E-4-1": ["unit_suspect"]}
    covered, _ = _area_item_rows(ext, "E")
    assert covered[0]["status"] == "공시(자기기재)·단위확인"


# ---------------------------------------------------------------------------
# 표 렌더 / 의사 청크 / 조립
# ---------------------------------------------------------------------------

def test_render_table_includes_all_rows_and_missing_dash():
    covered, missing = (
        [{"code": "E-3-1", "name": "온실가스", "value": 120, "unit": "tCO2eq", "status": "공시"}],
        [{"code": "E-4-1", "name": "에너지", "value": None, "unit": "TJ", "status": "미공시"}],
    )
    table = _render_kesg_table(covered, missing)
    assert "| E-3-1 | 온실가스 | 120 | tCO2eq | 공시 |" in table
    assert "| E-4-1 | 에너지 | — | TJ | 미공시 |" in table


def test_pseudo_chunk_contains_values_for_grounding():
    covered = [{"code": "E-3-1", "name": "온실가스", "value": 120, "unit": "tCO2eq", "status": "공시"}]
    doc = _kesg_pseudo_chunk(covered, "E")
    assert doc.chunk_id == "kesg_items_E"
    assert "120" in doc.text  # G2 숫자 대조가 원장 텍스트에서 값을 찾는다


def test_assemble_inserts_table_before_commentary():
    out = _assemble_section_v2(NARRATIVE, "| 표 |", "환경")
    assert out.index("### 핵심 지표") < out.index("### 지표 해설")
    assert out.index("### 전략 및 목표") < out.index("### 핵심 지표")


def test_assemble_replaces_llm_made_table():
    llm = NARRATIVE.replace(
        "### 지표 해설", "### 핵심 지표\n| 가짜 | 표 |\n\n### 지표 해설"
    )
    out = _assemble_section_v2(llm, "| 진짜표 |", "환경")
    assert "가짜" not in out
    assert "| 진짜표 |" in out
    assert out.count("### 핵심 지표") == 1


def test_assemble_appends_when_no_headers():
    out = _assemble_section_v2("서술만 있는 본문.", "| 표 |", "환경")
    assert out.startswith("## 환경 성과")
    assert out.rstrip().endswith("| 표 |")


# ---------------------------------------------------------------------------
# generate_section 통합 (v2 경로 / v1 하위 호환)
# ---------------------------------------------------------------------------

def test_generate_section_v2_full_reflection(monkeypatch):
    stub = _StubClient()
    monkeypatch.setattr("esgenie.layer2_rag.CLIENT", stub)
    ext = _extraction(
        mapped={
            "E-3-1": _mapped_entry("E-3-1", "E", "온실가스", 120, "tCO2eq"),
            "E-5-1": _mapped_entry("E-5-1", "E", "용수", 300, "ton"),
        },
        missing=["E-4-1"],
    )
    ctx = _empty_ctx()
    gen = _rag().generate_section(
        _FakeReport(), "E", context=ctx, corp=None, extraction=ext)

    # 반영률 100%: 공시 2행 + 미공시 1행 전부 표에 존재
    for code in ("E-3-1", "E-5-1", "E-4-1"):
        assert f"| {code} |" in gen.text
    assert "미공시" in gen.text
    # 의사 청크가 게이트 대상 청크에 포함
    assert any(c["id"] == "kesg_items_E" for c in ctx.as_chunk_dicts())
    # 프롬프트에 원장과 미공시 목록 명시
    assert "공시값 원장" in stub.prompts[0]
    assert "E-4-1" in stub.prompts[0]


def test_generate_section_v2_no_duplicate_pseudo_chunk_on_refine(monkeypatch):
    stub = _StubClient()
    monkeypatch.setattr("esgenie.layer2_rag.CLIENT", stub)
    ext = _extraction(mapped={"E-3-1": _mapped_entry("E-3-1", "E")})
    ctx = _empty_ctx()
    rag = _rag()
    rag.generate_section(_FakeReport(), "E", context=ctx, corp=None, extraction=ext)
    rag.generate_section(_FakeReport(), "E", extra_instruction="재작성",
                         context=ctx, corp=None, extraction=ext)  # 검증 루프 재생성 경로
    assert sum(1 for d, _ in ctx.corp_hits if d.chunk_id == "kesg_items_E") == 1
    assert "추가 지시" in stub.prompts[1]


def test_generate_section_v1_backward_compat(monkeypatch):
    stub = _StubClient(content="v1 본문")
    monkeypatch.setattr("esgenie.layer2_rag.CLIENT", stub)
    ctx = _empty_ctx()
    gen = _rag().generate_section(_FakeReport(), "E", context=ctx, corp=None)  # extraction 없음
    assert gen.text == "v1 본문"
    assert ctx.corp_hits == []  # 의사 청크 미부착
    assert "5개 이상" in stub.prompts[0]  # 기존 프롬프트 유지


def test_generate_section_v2_falls_back_when_area_empty(monkeypatch):
    stub = _StubClient(content="v1 본문")
    monkeypatch.setattr("esgenie.layer2_rag.CLIENT", stub)
    ext = _extraction(mapped={"S-1-1": _mapped_entry("S-1-1", "S")}, missing=["S-2-1"])
    gen = _rag().generate_section(
        _FakeReport(), "E", context=_empty_ctx(), corp=None, extraction=ext)
    assert gen.text == "v1 본문"  # E 영역 항목 전무 → v1 폴백
