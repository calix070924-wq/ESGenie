"""고지서·위탁명세 표의 수치 인식 회귀 (2026-09-29, 한울정밀 BM 개편 리허설).

정답은 원본 PDF를 직접 대조해 고정한 검증 기준이다(작업지시서 §3). 제품 코드는 이
값·회사명·파일명·해시로 분기하지 않는다 — 아래 변형 입력이 그 근거다.

재생 픽스처(tests/fixtures/ocr_numeric_hanwool_bm/*.json)는 리허설 당시 **Upstage
Document Parse 단계의 출력**(요소 text·HTML 표 셀)이다. 추출이 끝난 metrics는 비교용
기록으로만 싣고 재생 입력으로 쓰지 않는다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from esgenie.ssot import ocr_router as R
from esgenie.ssot.ocr_router import ExtractedTable, TableCell

FIXTURES = Path(__file__).parent / "fixtures" / "ocr_numeric_hanwool_bm"
ROOT = Path("/Users/heojeongmin/Documents/Claude/Projects/ESGenie")
BM_DIR = ROOT / "output/pdf/한울정밀_촬영세트_BM개편_20260928/01_처음업로드_12건"
OLD_DIR = ROOT / "시연증빙세트_한울정밀공업"


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """LLM·Upstage 호출 없이 규칙 경로만 검증한다."""
    monkeypatch.setattr(R, "_get_openai_key", lambda: None)
    monkeypatch.setattr(R, "_get_anthropic_key", lambda: None)
    monkeypatch.setattr(R, "_get_upstage_key", lambda: None)


# ---- 헬퍼 ----------------------------------------------------------------------

def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _tables_from(raw: list[dict]) -> list[ExtractedTable]:
    out = []
    for t in raw:
        cells = [TableCell(**c) for c in t["cells"]]
        out.append(ExtractedTable(
            table_id=t["table_id"], row_count=t["row_count"], column_count=t["column_count"],
            cells=cells, source=t.get("source", ""), page=t.get("page"), meta=t.get("meta") or {}))
    return out


def _replay(name: str, doc_type: str | None = None):
    fx = _load_fixture(name)
    return R._tokens_to_extraction(
        fx["tokens"], doc_type=doc_type or fx["recorded_doc_type"],
        file_path=fx["source_file"], engine="upstage_dp", tables=_tables_from(fx["tables"]))


def _table(rows: list[list[str]], *, table_id: str = "t0", bbox=(0.1, 0.3, 0.9, 0.4)) -> ExtractedTable:
    cells = [TableCell(row_index=r, column_index=c, content=txt, bbox=list(bbox), page=0)
             for r, row in enumerate(rows) for c, txt in enumerate(row)]
    return ExtractedTable(table_id=table_id, row_count=len(rows),
                          column_count=max(len(r) for r in rows), cells=cells, source="upstage_dp", page=0)


def _md_token(rows: list[list[str]], bbox=(0.1, 0.3, 0.9, 0.4)) -> dict:
    lines = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join("---" for _ in rows[0]) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return {"text": "\n".join(lines), "bbox": list(bbox), "page": 0}


def _upstage(rows_list: list[list[list[str]]], doc_type: str, *, extra: list[str] = (), with_tables=True):
    """표 여러 개 + 본문 줄 → Upstage 경로 재생. with_tables=False면 마크다운 토큰만 준다."""
    tokens = [{"text": t, "bbox": None, "page": 0} for t in extra]
    tables = []
    for i, rows in enumerate(rows_list):
        bbox = (0.1, 0.1 + 0.2 * i, 0.9, 0.25 + 0.2 * i)
        tokens.append(_md_token(rows, bbox))
        tables.append(_table(rows, table_id=f"upstage_table_{i}", bbox=bbox))
    return R._tokens_to_extraction(tokens, doc_type=doc_type, file_path="variant.pdf",
                                   engine="upstage_dp", tables=tables if with_tables else [])


def _cell_tokens(rows: list[list[str]], *, x0s: list[float], y0: float = 0.40, dy: float = 0.03) -> list[dict]:
    """pymupdf span처럼 셀마다 좌표가 있는 토큰."""
    toks = []
    for r, row in enumerate(rows):
        y = y0 + r * dy
        for c, txt in enumerate(row):
            toks.append({"text": txt, "bbox": [x0s[c], y, x0s[c] + 0.08, y + 0.01], "page": 0})
    return toks


def _vals(ext, *, unit: str | None = None, code: str | None = "*"):
    out = []
    for m in ext.metrics:
        if unit is not None and m.unit != unit:
            continue
        if code != "*" and m.kesg_code_guess != code:
            continue
        out.append(m.value)
    return out


def _one(ext, *, unit: str, code: str | None = "*"):
    hits = [m for m in ext.metrics if m.unit == unit and (code == "*" or m.kesg_code_guess == code)]
    assert len(hits) == 1, [(m.metric_hint, m.value, m.unit, m.kesg_code_guess) for m in ext.metrics]
    return hits[0]


# ---- 1. 리허설 Upstage 출력 재생 -------------------------------------------------

def test_replay_02_electricity_reads_usage_not_index_or_amount():
    ext = _replay("02")
    m = _one(ext, unit="kWh", code="E-4-1")
    assert m.value == 142560
    # 전월 지침·청구 금액을 사용량으로 채택하지 않는다.
    assert 48210 not in _vals(ext) and 50586 not in _vals(ext)
    assert all(m.kesg_code_guess != "E-4-1" for m in ext.metrics if m.unit == "원")
    detail = m.source_detail
    assert detail["raw_text"] == "142,560 kWh" and detail["header"] == "당월 전력 사용량"
    # Upstage 셀은 표 외접 사각형만 공유한다 — 셀 위치처럼 꾸미지 않는다.
    assert detail["precision"] == "table"
    check = detail["index_check"]
    assert check["computed"] == 142560 and check["status"] == "match"
    assert check["inputs"] == {"previous": 48210, "current": 50586, "multiplier": 60}


def test_replay_03_gas_reads_volume_without_inventing_heat():
    ext = _replay("03")
    m = _one(ext, unit="m³")
    assert m.value == 8420 and m.kesg_code_guess is None
    assert m.source_detail["raw_unit"] == "m3"
    # 발열량·환산 계수가 없으므로 MJ·TJ를 만들지 않는다(과거 기본단위 MJ 오류 포함).
    assert not [x for x in ext.metrics if x.unit in {"MJ", "GJ", "TJ"}]
    assert 31580 not in _vals(ext) and 40000 not in _vals(ext)
    assert m.source_detail["index_check"]["status"] == "match"


def test_replay_07_water_reads_usage_in_m3():
    ext = _replay("07")
    m = _one(ext, unit="m³", code="E-5-1")
    assert m.value == 680
    assert 12320 not in _vals(ext) and 13000 not in _vals(ext)
    assert not [x for x in ext.metrics if x.unit == "ton"]   # 과거 기본단위 ton 오류


def test_replay_04_waste_total_recycled_and_rate_are_consistent():
    ext = _replay("04")
    total = _one(ext, unit="kg", code="E-6-1")
    assert total.value == 18400
    recycled = [m for m in ext.metrics if m.unit == "kg" and m.kesg_code_guess is None]
    assert [m.value for m in recycled] == [5400]
    assert "재활용" in recycled[0].metric_hint
    rate = _one(ext, unit="%", code="E-6-2")
    assert rate.value == 29.3
    checks = {c["name"]: c for c in ext.router_meta["table_metrics"]["checks"]}
    assert checks["waste_components_sum"]["status"] == "match"      # 5,400+6,100+6,900
    assert checks["waste_detail_sum"]["status"] == "match"          # 명세 9행 합계
    assert checks["waste_recycled_detail_sum"]["status"] == "match"  # 재활용 4행 합계
    rc = checks["waste_recycling_rate"]
    assert rc["status"] == "match" and abs(rc["computed"] - 29.3478) < 1e-3 and rc["reported"] == 29.3
    assert not ext.router_meta.get("hitl_required")


def test_replay_06_regulation_heading_never_becomes_quantity():
    """리허설 당시 분류(waste_ledger) 그대로 재생해도 제목 번호 '3.'이 수량이 되지 않는다."""
    ext = _replay("06")
    assert ext.metrics == []


def test_replay_01_company_profile_forced_as_waste_yields_nothing():
    ext = _replay("01")
    assert ext.metrics == []


# ---- 2. pymupdf 경로 — 실제 BM PDF ------------------------------------------------

def _bm(prefix: str) -> Path:
    hits = sorted(BM_DIR.glob(f"{prefix}_*.pdf"))
    if not hits:
        pytest.skip("BM 원본 PDF 없음(저장소 밖 자료)")
    return hits[0]


@pytest.mark.parametrize("prefix,doc_type,value,unit,code,header_x0", [
    ("02", "kepco_bill", 142560, "kWh", "E-4-1", 0.635),
    ("03", "gas_bill", 8420, "m³", None, 0.594),
    ("07", "water_bill", 680, "m³", "E-5-1", 0.580),
])
def test_pdf_usage_value_and_cell_location(prefix, doc_type, value, unit, code, header_x0):
    ext = R._extract_structured_no_llm(str(_bm(prefix)), doc_type=doc_type)
    m = _one(ext, unit=unit, code=code)
    assert m.value == value and m.page == 0
    # 원문 좌표가 있는 경로는 채택 값의 칸만 가리킨다(전월 지침 칸 x0=0.112가 아님).
    assert m.source_detail["precision"] == "cell"
    assert abs(m.bbox[0] - header_x0) < 0.01 and m.bbox[2] - m.bbox[0] < 0.12


def test_pdf_waste_values_and_locations():
    ext = R._extract_structured_no_llm(str(_bm("04")), doc_type="waste_ledger")
    total = _one(ext, unit="kg", code="E-6-1")
    assert total.value == 18400 and abs(total.bbox[0] - 0.727) < 0.01
    recycled = [m for m in ext.metrics if m.unit == "kg" and m.kesg_code_guess is None]
    assert [m.value for m in recycled] == [5400] and abs(recycled[0].bbox[0] - 0.112) < 0.01
    assert _one(ext, unit="%", code="E-6-2").value == 29.3


def test_pdf_regulation_yields_no_quantity_even_if_misrouted():
    ext = R._extract_structured_no_llm(str(_bm("06")), doc_type="waste_ledger")
    assert ext.metrics == []


@pytest.mark.parametrize("prefix,expect", [
    ("02", "kepco_bill"), ("03", "gas_bill"), ("04", "waste_ledger"), ("07", "water_bill"),
])
def test_route_bills_unchanged(prefix, expect):
    d = R.route_document(str(_bm(prefix)))
    assert d.channel is R.DocChannel.STRUCTURED and d.doc_type == expect


@pytest.mark.parametrize("prefix", ["01", "06"])
def test_route_waste_keyword_alone_does_not_force_ledger(prefix):
    d = R.route_document(str(_bm(prefix)))
    assert d.doc_type != "waste_ledger"


def test_bm_metrics_keep_month_and_site_through_graph():
    from esgenie.ssot.evidence_graph import EvidenceGraph, merge_ocr_extraction
    expect = {"02": ("kepco_bill", 142560), "03": ("gas_bill", 8420),
              "07": ("water_bill", 680), "04": ("waste_ledger", 18400)}
    for prefix, (doc_type, value) in expect.items():
        ext = R._extract_structured_no_llm(str(_bm(prefix)), doc_type=doc_type)
        g = EvidenceGraph("c", "한울정밀")
        merge_ocr_extraction(g, ext, report_year=2026)
        node = next(n for n in g.nodes.values() if n.value == value)
        b = node.boundary
        assert (b.period_start, b.period_end, b.aggregation) == ("2026-04-01", "2026-04-30", "monthly"), prefix
        assert "제1공장" in b.site and b.site_scope == "site", prefix
        cell = [p for p in b.provenance if p.get("source") == "table_cell"]
        assert cell and cell[0]["precision"] == "cell" and cell[0]["page"] == 0, prefix


# ---- 3. 변형 입력 — 전기 --------------------------------------------------------

def test_elec_changed_numbers():
    ext = _upstage([[["이전 지침", "당월 지침", "계기 배율", "당월 전력 사용량"],
                     ["10,000", "10,500", "40", "20,000 kWh"]]], "kepco_bill")
    assert _one(ext, unit="kWh", code="E-4-1").value == 20000


def test_elec_column_order_and_unit_only_in_header():
    ext = _upstage([[["당월지침", "전월지침", "사용량(kWh)", "배율"],
                     ["7,300", "7,100", "12,000", "60"]]], "kepco_bill")
    m = _one(ext, unit="kWh", code="E-4-1")
    assert m.value == 12000 and m.source_detail["unit_source"] == "header"
    assert m.source_detail["index_check"]["status"] == "match"


def test_elec_markdown_only_row_without_table_objects():
    ext = _upstage([[["이전 지침", "당월 지침", "계기 배율", "당월 전력 사용량"],
                     ["48,210", "50,586", "60", "142,560 kWh"]]], "kepco_bill", with_tables=False)
    assert _one(ext, unit="kWh", code="E-4-1").value == 142560


def test_elec_index_only_is_computed_and_marked():
    ext = _upstage([[["전월지침(kWh)", "당월지침(kWh)", "배율"], ["1,000", "1,250", "80"]]], "kepco_bill")
    m = _one(ext, unit="kWh", code="E-4-1")
    assert m.value == 20000 and m.source_detail["value_source"] == "computed"
    assert m.source_detail["formula"] == "(당월 지침 − 이전 지침) × 배율"


def test_elec_missing_multiplier_is_not_assumed():
    ext = _upstage([[["전월지침", "당월지침"], ["1,000", "1,250"]]], "kepco_bill")
    assert _vals(ext, unit="kWh") == []


def test_elec_explicit_differs_from_index_keeps_both_and_flags_review():
    ext = _upstage([[["이전 지침", "당월 지침", "계기 배율", "당월 전력 사용량"],
                     ["48,210", "50,586", "60", "150,000 kWh"]]], "kepco_bill")
    m = _one(ext, unit="kWh", code="E-4-1")
    assert m.value == 150000
    chk = m.source_detail["index_check"]
    assert chk["status"] == "mismatch" and chk["computed"] == 142560
    assert ext.router_meta["hitl_required"] is True
    assert m.confidence < 0.8


def test_elec_zero_usage_is_a_value():
    ext = _upstage([[["이전 지침", "당월 지침", "계기 배율", "당월 전력 사용량"],
                     ["5,000", "5,000", "60", "0 kWh"]]], "kepco_bill")
    assert _vals(ext, unit="kWh", code="E-4-1") == [0]


def test_elec_missing_unit_is_not_filled_by_template_default():
    ext = _upstage([[["이전 지침", "당월 지침", "당월 전력 사용량"], ["48,210", "50,586", "142,560"]]],
                   "kepco_bill")
    assert _vals(ext, unit="kWh") == []
    assert ext.router_meta["table_metrics"]["review"][0]["reason"] == "unit_missing"


def test_elec_amount_tax_date_noise_not_usage():
    ext = _upstage([
        [["항목", "내용"], ["사용 기간", "2026-04-01 ~ 2026-04-30"], ["발행 / 납기", "2026-05-07 / 2026-05-25"]],
        [["항목", "금액"], ["사용요금: 142,560 kWh × 125원", "17,820,000원"],
         ["부가세", "2,358,000원"], ["청구금액 합계", "25,938,000원"]],
    ], "kepco_bill", extra=["2. 청구 내역"])
    assert _vals(ext, unit="kWh") == []
    assert all(m.kesg_code_guess != "E-4-1" for m in ext.metrics)


def test_elec_rows_per_site_are_kept_apart():
    ext = _upstage([[["사업장", "사용량(kWh)"], ["김해 제1공장", "142,560"], ["양산 제2공장", "98,000"]]],
                   "kepco_bill")
    by_hint = {m.metric_hint: m.value for m in ext.metrics if m.unit == "kWh"}
    assert len(by_hint) == 2 and sorted(by_hint.values()) == [98000, 142560]
    assert any("제1공장" in h and v == 142560 for h, v in by_hint.items())
    assert 240560 not in by_hint.values()   # 다른 사업장을 합산하지 않는다


# ---- 4. 변형 입력 — 도시가스 ----------------------------------------------------

def test_gas_changed_numbers_and_square_meter_glyph():
    ext = _upstage([[["이전 지침", "당월 지침", "당월 가스 사용량"], ["1,000 ㎥", "1,250 ㎥", "250 ㎥"]]], "gas_bill")
    m = _one(ext, unit="m³")
    assert m.value == 250 and m.source_detail["raw_unit"] == "㎥"


def test_gas_unit_only_in_header_cells_per_token():
    toks = _cell_tokens([["전월지침(m3)", "당월지침(m3)", "사용량(m3)"], ["31,580", "40,000", "8,420"]],
                        x0s=[0.11, 0.35, 0.59])
    ext = R._tokens_to_extraction(toks, doc_type="gas_bill", file_path="v.pdf", engine="pymupdf")
    m = _one(ext, unit="m³")
    assert m.value == 8420 and m.source_detail["precision"] == "cell"
    assert abs(m.bbox[0] - 0.59) < 1e-6


def test_gas_index_only_with_volume_units_is_computed():
    ext = _upstage([[["전월 지침", "당월 지침"], ["31,580 m³", "40,000 m³"]]], "gas_bill")
    m = _one(ext, unit="m³")
    assert m.value == 8420 and m.source_detail["value_source"] == "computed"


def test_gas_explicit_heat_column_is_kept_separately():
    ext = _upstage([[["전월지침(㎥)", "당월지침(㎥)", "사용량(㎥)", "보정계수", "평균열량(MJ/㎥)", "사용열량(MJ)"],
                     ["31,580", "40,000", "8,420", "0.9942", "43.1", "360,772"]]], "gas_bill")
    assert _one(ext, unit="m³").value == 8420
    assert _one(ext, unit="MJ", code="E-4-1").value == 360772
    assert 43.1 not in _vals(ext) and 0.9942 not in _vals(ext)


def test_gas_current_below_previous_is_not_computed():
    ext = _upstage([[["전월 지침", "당월 지침"], ["40,000 m³", "31,580 m³"]]], "gas_bill")
    assert _vals(ext, unit="m³") == []
    assert ext.router_meta["table_metrics"]["review"][0]["reason"] == "index_decreased"


# ---- 5. 변형 입력 — 상수도 ------------------------------------------------------

def test_water_changed_numbers_m3():
    ext = _upstage([[["이전 지침", "당월 지침", "상수도 사용량"], ["500 m3", "620 m3", "120 m3"]]], "water_bill")
    assert _one(ext, unit="m³", code="E-5-1").value == 120


def test_water_swapped_order_ton_header():
    ext = _upstage([[["당월 지침", "이전 지침", "사용량(톤)"], ["2,045", "2,000", "45"]]], "water_bill")
    m = _one(ext, unit="ton", code="E-5-1")
    assert m.value == 45 and m.source_detail["raw_unit"] == "톤"


def test_water_previous_month_column_is_not_current_usage():
    ext = _upstage([[["전월 사용량", "당월 사용량"], ["680 m³", "700 m³"]]], "water_bill")
    assert _vals(ext, unit="m³") == [700]


def test_water_zero_usage():
    ext = _upstage([[["이전 지침", "당월 지침", "상수도 사용량"], ["13,000 m³", "13,000 m³", "0 m³"]]],
                   "water_bill")
    assert _vals(ext, unit="m³", code="E-5-1") == [0]


# ---- 6. 변형 입력 — 폐기물 ------------------------------------------------------

def test_waste_changed_numbers():
    ext = _upstage([[["재활용", "소각", "매립", "전체 위탁 처리"], ["1,000 kg", "500 kg", "500 kg", "2,000 kg"]]],
                   "waste_ledger")
    assert _one(ext, unit="kg", code="E-6-1").value == 2000
    assert _vals(ext, unit="kg", code=None) == [1000]


def test_waste_ton_units_preserved():
    ext = _upstage([[["재활용", "소각", "매립", "합계"], ["5.4 톤", "6.1 톤", "6.9 톤", "18.4 톤"]]], "waste_ledger")
    m = _one(ext, unit="ton", code="E-6-1")
    assert m.value == 18.4 and m.source_detail["raw_unit"] == "톤"


def test_waste_key_value_rows():
    ext = _upstage([[["구분", "중량"], ["총 위탁량", "18,400 kg"], ["재활용량", "5,400 kg"],
                     ["내부 재투입량", "11,500 kg"]]], "waste_ledger")
    assert _one(ext, unit="kg", code="E-6-1").value == 18400
    assert _vals(ext, unit="kg", code=None) == [5400]
    assert 11500 not in _vals(ext)   # 내부 재투입은 외부 위탁 폐기물이 아니다


def test_waste_components_mismatch_flags_review():
    ext = _upstage([[["재활용", "소각", "매립", "전체 위탁 처리"], ["5,400 kg", "6,100 kg", "6,900 kg", "19,000 kg"]]],
                   "waste_ledger")
    assert _one(ext, unit="kg", code="E-6-1").value == 19000
    checks = {c["name"]: c for c in ext.router_meta["table_metrics"]["checks"]}
    assert checks["waste_components_sum"]["status"] == "mismatch"
    assert ext.router_meta["hitl_required"] is True


def test_waste_detail_rows_alone_do_not_fabricate_total():
    ext = _upstage([[["처리일", "폐기물", "중량(kg)", "처리 방법"],
                     ["04-03", "폐합성수지", "2,400", "재활용"], ["04-12", "폐절삭유", "900", "소각"]]],
                   "waste_ledger")
    assert _vals(ext, unit="kg") == []


# ---- 7. 부정 사례 — 규정·제목 번호 ------------------------------------------------

@pytest.mark.parametrize("doc_type", ["waste_ledger", "gas_bill", "water_bill", "kepco_bill"])
def test_heading_numbers_after_label_sentence_never_quantity(doc_type):
    toks = [{"text": t, "bbox": None, "page": 0} for t in [
        "2. 폐기물과 공정 스크랩",
        "내부 스크랩 재투입량과 외부 위탁 폐기물 재활용량을 서로 다른 대장으로",
        "관리하고, 비율에는 계산식과 기간·사업장을 함께 적는다.",
        "3. 현장 안전과 교육",
        "전기·가스·상수도 고지서를 월별로 보관하고 사용량과 단위를 확인한다.",
        "4. 실사 요청 대응",
    ]]
    ext = R._tokens_to_extraction(toks, doc_type=doc_type, file_path="규정.pdf", engine="upstage_dp")
    assert ext.metrics == []


def test_route_long_policy_text_with_waste_words_not_ledger():
    text = ("환경·안전 관리규정 시행 2026-01-02 " + "외부 위탁 폐기물의 인계·처리 기록을 보관한다. " * 6
            + "3. 현장 안전과 교육 교육 시 대상 인원을 기록한다.")
    d = R.route_document("규정.pdf", preview_text=text)
    assert d.doc_type != "waste_ledger"


def test_route_long_ledger_text_with_mass_units_is_ledger():
    text = ("사업장폐기물 위탁처리 명세 올바로 인계서 번호 " + "폐합성수지 2,400 kg 재활용 " * 6
            + "배출자 한울정밀 처리량 합계 18,400 kg")
    d = R.route_document("명세.pdf", preview_text=text)
    assert d.doc_type == "waste_ledger"


# ---- 8. 이전 세트 회귀(HMC 입력) ---------------------------------------------------

def _old(name: str) -> Path:
    p = OLD_DIR / name
    if not p.exists():
        pytest.skip("이전 시연 증빙 없음")
    return p


def test_old_set_values_are_preserved():
    kepco = R._extract_structured_no_llm(str(_old("01_전기요금청구서_2026-05.pdf")), doc_type="kepco_bill")
    assert _vals(kepco, code="E-4-1") == [142560]
    gas = R._extract_structured_no_llm(str(_old("02_도시가스요금고지서_2026-05.pdf")), doc_type="gas_bill")
    assert _vals(gas, code="E-4-1") == [360772]
    assert _vals(gas, unit="m³") == [8420]          # 체적은 코드 없이 별도 보존
    waste = R._extract_structured_no_llm(str(_old("03_사업장폐기물_위탁처리명세_2026-04.pdf")), doc_type="waste_ledger")
    assert _vals(waste, code="E-6-1") == [18.4]
    assert _vals(waste, code="E-6-2") == [29.3]
    # 이전 코드는 재활용량 5,400을 ton으로 적었다(1000배 오류) — 원문 단위 kg 유지.
    assert _vals(waste, unit="ton", code=None) == []
    assert _vals(waste, unit="kg", code=None) == [5400]


# ---- 9. 단위 공통 함수 ------------------------------------------------------------

def test_units_volume_aliases_do_not_mix_dimensions():
    from esgenie.rag_gates.units import convert_to_common, normalize_unit, units_compatible
    assert {normalize_unit(u) for u in ("m3", "m³", "㎥", "M3")} == {"m³"}
    assert normalize_unit("㎏") == "kg"
    assert abs(convert_to_common(18400, "kg", "t") - 18.4) < 1e-9
    assert abs(convert_to_common(142560, "kWh", "TJ") - 0.513216) < 1e-9
    assert not units_compatible("m³", "t") and not units_compatible("m³", "MJ")


def test_two_tables_disagreeing_on_usage_flag_review():
    ext = _upstage([
        [["이전 지침", "당월 지침", "계기 배율", "당월 전력 사용량"], ["48,210", "50,586", "60", "142,560 kWh"]],
        [["구분", "사용량(kWh)"], ["합계", "150,000"]],
    ], "kepco_bill")
    reasons = [r["reason"] for r in ext.router_meta["table_metrics"]["review"]]
    assert "conflicting_values" in reasons and ext.router_meta["hitl_required"] is True
    assert all(m.confidence <= 0.6 for m in ext.metrics if m.unit == "kWh")


def test_route_ledger_with_unit_only_in_section_header_is_ledger():
    """단위가 '(단위: ton)' 머리글에만 있고 값은 맨숫자인 대장도 정형 대장으로 남는다."""
    text = ("올바로시스템 폐기물 인계서 (2025년 연간 집계) 배출자: 가상전자 사업장코드: 0000 "
            "[ 폐기물 종류별 배출량 (단위: ton) ] 일반폐기물 폐기물처리량(합계): 124.7 재활용량: 107.6 "
            "소각량: 12.4 매립량: 4.7 지정폐기물 폐유: 1.2 폐액: 0.8 소계: 2.0 "
            "폐기물 재활용 비율: 86.3% 처리업체: 가상환경 발급일: 2026-01-10")
    assert R.route_document("대장.pdf", preview_text=text).doc_type == "waste_ledger"


def test_upstage_table_bbox_is_narrowed_with_pdf_text_when_file_exists():
    """원본 PDF가 있으면 표 외접 bbox를 원문 문자 좌표로 좁히고 정밀도를 'pdf_text'로 표시한다."""
    fx = _load_fixture("02")
    ext = R._tokens_to_extraction(fx["tokens"], doc_type="kepco_bill", file_path=str(_bm("02")),
                                  engine="upstage_dp", tables=_tables_from(fx["tables"]))
    m = _one(ext, unit="kWh", code="E-4-1")
    assert m.source_detail["precision"] == "pdf_text"
    assert abs(m.bbox[0] - 0.635) < 0.01 and m.bbox[2] - m.bbox[0] < 0.12


# 재생 픽스처는 표 밖 요소의 bbox를 싣지 않는다. 아래 값은 2026-09-29 실제 Upstage 실행에서
# '사업장폐기물 재활용률 29.3%' 요소가 받은 외접 사각형(docs/validation/ocr-numeric-20260929).
_RATE_ELEMENT_BBOX = [0.0985, 0.5958, 0.4227, 0.6159]


def _replay_04_with_rate_element_bbox(file_path: str):
    fx = _load_fixture("04")
    tokens = [dict(t) for t in fx["tokens"]]
    rate_tok = next(t for t in tokens if t["text"] == "사업장폐기물 재활용률 29.3%")
    rate_tok["bbox"] = list(_RATE_ELEMENT_BBOX)
    return R._tokens_to_extraction(tokens, doc_type="waste_ledger", file_path=file_path,
                                   engine="upstage_dp", tables=_tables_from(fx["tables"]))


def test_rate_from_text_element_is_marked_as_text_block_not_cell():
    """본문에서 고정한 비율은 텍스트 요소 위치임을 표시한다 — 셀 위치로 꾸미지 않는다."""
    ext = _replay_04_with_rate_element_bbox("variant.pdf")
    rate = _one(ext, unit="%", code="E-6-2")
    assert rate.bbox == _RATE_ELEMENT_BBOX
    assert rate.source_detail["precision"] == "text_block"
    assert rate.source_detail["raw_text"] == "29.3%"


def test_rate_from_text_element_is_narrowed_with_pdf_text_when_file_exists():
    ext = _replay_04_with_rate_element_bbox(str(_bm("04")))
    rate = _one(ext, unit="%", code="E-6-2")
    assert rate.source_detail["precision"] == "pdf_text"
    # 요소 전체(x0=0.0985)가 아니라 '29.3%' 글자 좌표만 가리킨다.
    assert abs(rate.bbox[0] - 0.344) < 0.01 and rate.bbox[2] - rate.bbox[0] < 0.1


def test_rate_without_location_has_no_precision_claim():
    """위치를 못 찾은 비율에는 정밀도 표시를 붙이지 않는다(재생 픽스처 그대로).
    원문 비율 문자열은 자릿수 검산 근거라 위치와 무관하게 남는다(PR #68 검토 R5)."""
    rate = _one(_replay("04"), unit="%", code="E-6-2")
    assert rate.value == 29.3 and rate.bbox is None
    assert "precision" not in rate.source_detail and "cells" not in rate.source_detail
    assert rate.source_detail["raw_text"] == "29.3%"


def test_rate_text_span_is_not_labelled_table_cell_in_graph():
    from esgenie.ssot.evidence_graph import EvidenceGraph, merge_ocr_extraction
    ext = _replay_04_with_rate_element_bbox("variant.pdf")
    g = EvidenceGraph("c", "한울정밀")
    merge_ocr_extraction(g, ext, report_year=2026)
    node = next(n for n in g.nodes.values() if n.value == 29.3)
    sources = [p.get("source") for p in node.boundary.provenance if p.get("precision")]
    assert sources == ["text_span"]
