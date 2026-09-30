"""정형 고지서·위탁명세 표에서 사용량·총량을 행·열·머리글·단위 관계를 유지해 읽는다.

기존 템플릿 매칭(`ocr_router._apply_template`)은 토큰 문자열의 키워드와 인접 숫자만
보았다. 그래서 (1) Upstage가 복원한 표 셀(`ExtractedTable`)을 전혀 쓰지 않았고,
(2) '당월 전력 사용량'처럼 띄어 쓴 머리글을 놓쳤으며, (3) 단위가 없는 이웃 숫자(제목
번호 '3.')에 템플릿 기본 단위를 붙였다. 이 모듈은 표를 격자로 복원해 머리글의 **역할**
(사용량·지침·배율·총량 등)로 칸을 고르고, 단위는 셀 → 머리글 괄호 순서로만 정한다.

규칙:
  · 단위를 원문에서 찾지 못하면 값을 만들지 않고 검토 목록에 남긴다(기본 단위 금지).
  · 명시 사용량을 우선하고, 지침·배율이 있으면 (당월 − 이전) × 배율로 검산한다.
    다르면 명시값을 유지하고 두 값과 불일치를 기록한다. 명시값이 없으면 입력이 모두
    원문에 있을 때만 계산한다 — 전기는 배율 칸이 필요하고, 가스·수도는 지침 자체에
    부피 단위가 있을 때만 배율 없이 계산한다. 환산 계수(발열량 등)는 채우지 않는다.
  · 위치 정밀도를 사실대로 남긴다: 원문 문자 좌표가 있는 셀은 "cell", 표 외접 사각형만
    공유하는 OCR 셀은 "table"(PDF 문자 좌표로 좁히면 "pdf_text").
문서 이름·회사명·특정 수치로 분기하지 않는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..rag_gates.units import convert_to_common, normalize_unit


# ---- 격자 복원 ------------------------------------------------------------------

@dataclass
class _Cell:
    text: str
    bbox: list[float] | None = None
    page: int | None = None
    precision: str = "table"


@dataclass
class _Grid:
    rows: list[list[_Cell]]
    table_id: str
    page: int | None
    source: str            # "table_cells" | "markdown" | "text_lines"
    bbox: list[float] | None = None


def _grids_from_tables(tables: list[Any]) -> list[_Grid]:
    grids: list[_Grid] = []
    for t in tables or []:
        cells = list(getattr(t, "cells", None) or [])
        if not cells:
            continue
        n_rows = max(int(getattr(t, "row_count", 0) or 0),
                     max(c.row_index + max(c.row_span, 1) for c in cells))
        n_cols = max(int(getattr(t, "column_count", 0) or 0),
                     max(c.column_index + max(c.column_span, 1) for c in cells))
        boxes = {tuple(c.bbox) for c in cells if c.bbox}
        shared = len(boxes) <= 1        # 셀마다 같은 사각형 = 표 외접 bbox 공유
        table_bbox = list(next(iter(boxes))) if len(boxes) == 1 else None
        matrix: list[list[_Cell | None]] = [[None] * n_cols for _ in range(n_rows)]
        for c in cells:
            cell = _Cell(str(c.content or "").strip(), list(c.bbox) if c.bbox else None,
                         c.page if c.page is not None else getattr(t, "page", None),
                         "table" if shared else "cell")
            for dr in range(max(c.row_span, 1)):
                for dc in range(max(c.column_span, 1)):
                    r, k = c.row_index + dr, c.column_index + dc
                    if r < n_rows and k < n_cols and matrix[r][k] is None:
                        matrix[r][k] = cell
        rows = [[x or _Cell("", table_bbox, getattr(t, "page", None)) for x in row]
                for row in matrix if any(x is not None and x.text for x in row)]
        if len(rows) >= 1:
            grids.append(_Grid(rows, str(getattr(t, "table_id", "")), getattr(t, "page", None),
                               "table_cells", table_bbox))
    return grids


_MD_SEP_RE = re.compile(r"^:?-{3,}:?$")


def _grids_from_markdown(tokens: list[dict[str, Any]]) -> list[_Grid]:
    grids: list[_Grid] = []
    for i, tok in enumerate(tokens):
        lines = [ln.strip() for ln in str(tok.get("text", "")).splitlines() if ln.strip()]
        if len(lines) < 2 or not all(ln.startswith("|") for ln in lines):
            continue
        rows: list[list[_Cell]] = []
        for ln in lines:
            parts = [p.strip() for p in ln.strip("|").split("|")]
            if parts and all(_MD_SEP_RE.match(p) for p in parts if p):
                continue
            rows.append([_Cell(p, tok.get("bbox"), tok.get("page"), "table") for p in parts])
        if rows:
            grids.append(_Grid(rows, f"markdown_{i}", tok.get("page"), "markdown", tok.get("bbox")))
    return grids


_ROW_Y_TOL = 0.004       # 같은 행으로 묶을 y중심 차(정규화). 행 간격 실측 ≈0.03
_BLOCK_GAP = 0.045       # 이보다 행 간격이 크면 다른 표로 본다


def _grids_from_lines(tokens: list[dict[str, Any]]) -> list[_Grid]:
    """좌표가 있는 텍스트 줄(pymupdf span 등) → y로 행을 묶고, 칸 수가 같은 연속 행을 표로 본다."""
    items = []
    for t in tokens:
        text = str(t.get("text", "")).strip()
        bbox = t.get("bbox")
        if not text or not bbox or len(bbox) < 4 or "\n|" in text or text.startswith("|"):
            continue
        items.append((t.get("page"), (bbox[1] + bbox[3]) / 2, bbox[0], text, list(bbox)))
    items.sort(key=lambda x: (x[0] if x[0] is not None else -1, x[1], x[2]))
    rows: list[tuple[Any, float, list[_Cell]]] = []
    for page, yc, _x0, text, bbox in items:
        if rows and rows[-1][0] == page and abs(rows[-1][1] - yc) <= _ROW_Y_TOL:
            rows[-1][2].append(_Cell(text, bbox, page, "cell"))
        else:
            rows.append((page, yc, [_Cell(text, bbox, page, "cell")]))
    for _p, _y, cells in rows:
        cells.sort(key=lambda c: c.bbox[0])

    grids: list[_Grid] = []
    block: list[tuple[Any, float, list[_Cell]]] = []

    def flush() -> None:
        if len(block) >= 2:
            grids.append(_Grid([r[2] for r in block], f"text_block_{len(grids)}", block[0][0], "text_lines"))
        block.clear()

    for row in rows:
        page, yc, cells = row
        if block and len(cells) < len(block[0][2]) and not _is_total_label(cells[0].text) \
                and block[-1][0] == page and yc - block[-1][1] <= _BLOCK_GAP:
            # 빈 칸은 PDF 문자로 남지 않는다 — 수치 행의 칸을 머리글 열에 x로 맞추고 나머지는
            # 빈 칸으로 둔다. 맞출 수 없으면 기존처럼 표를 끊는다.
            aligned = _align_to_header(cells, block[0][2])
            if aligned is not None:
                block.append((page, yc, aligned))
                continue
        if len(cells) < 2:
            flush()
            continue
        if block:
            head = block[0][2]
            same_page = block[-1][0] == page
            close = yc - block[-1][1] <= _BLOCK_GAP
            fits = len(cells) == len(head) or (
                _is_total_label(cells[0].text) and len(cells) <= len(head))
            if not (same_page and close and fits):
                flush()
        block.append(row)
    flush()
    return grids


def _align_to_header(cells: list[_Cell], head: list[_Cell]) -> list[_Cell] | None:
    """칸 수가 모자란 수치 행을 머리글 열에 맞춘다(가장 가까운 머리글 x중심, 열마다 하나).
    칸이 모두 수치인 행만 맞춘다 — '기본요금 247,500'처럼 행 머리가 있는 줄은 표 밖의
    요금 행일 수 있어 열을 추측하지 않는다."""
    if not all(_parse_qty(c.text) for c in cells) or not all(c.bbox for c in head + cells):
        return None
    centers = [(h.bbox[0] + h.bbox[2]) / 2 for h in head]
    gaps = [b - a for a, b in zip(centers, centers[1:])]
    if not gaps or min(gaps) <= 0:
        return None
    out: list[_Cell | None] = [None] * len(head)
    for c in cells:
        cx = (c.bbox[0] + c.bbox[2]) / 2
        k = min(range(len(centers)), key=lambda i: abs(centers[i] - cx))
        if out[k] is not None or abs(centers[k] - cx) > min(gaps) / 2:
            return None
        out[k] = c
    page = cells[0].page
    return [c if c is not None else _Cell("", None, page, "cell") for c in out]


# ---- 셀 해석 ------------------------------------------------------------------

_UNIT_ALT = r"kWh|MWh|GWh|MJ|GJ|TJ|m\^?3|m³|㎥|kg|㎏|tons?|톤|t|%"
_QTY_RE = re.compile(
    r"^\s*(?P<num>[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*"
    rf"(?P<unit>{_UNIT_ALT})?\s*(?:\((?P<paren>[^()]*)\))?\s*$",
    re.IGNORECASE,
)
_HEADER_UNIT_RE = re.compile(r"\(([^()]*)\)\s*$")

_ENERGY = {"kWh", "MWh", "GWh", "MJ", "GJ", "TJ"}
_ELECTRIC = {"kWh", "MWh", "GWh"}
_HEAT = {"MJ", "GJ", "TJ"}
_VOLUME = {"m³"}
_MASS = {"kg", "ton"}


def _canon_unit(raw: str | None) -> str | None:
    """원문 단위 → 표시 단위(공통 별칭 사용). 질량 t는 기존 관례대로 'ton'."""
    if not raw:
        return None
    u = normalize_unit(raw)
    if u == "t":
        return "ton"
    return u


def _parse_qty(text: str) -> dict[str, Any] | None:
    """셀 전체가 '숫자 [단위] [(환산 표기)]'일 때만 수치로 본다. 날짜·금액·제목 번호 제외."""
    m = _QTY_RE.match(text or "")
    if not m:
        return None
    raw_unit = m.group("unit")
    unit = _canon_unit(raw_unit)
    if raw_unit and unit is None:
        return None
    return {"value": float(m.group("num").replace(",", "")), "raw_unit": raw_unit or "",
            "unit": unit, "paren": m.group("paren") or ""}


def _header_unit(label: str) -> tuple[str | None, str]:
    m = _HEADER_UNIT_RE.search(label or "")
    if not m:
        return None, ""
    raw = m.group(1).strip()
    return _canon_unit(raw), raw


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


_TOTAL_LABELS = ("합계", "총계", "계", "총", "전체", "소계합계")


def _is_total_label(text: str) -> bool:
    n = _norm(text)
    return n in _TOTAL_LABELS or n.startswith("합계") or n.startswith("총계")


# 금액 이름(요금·공급가액·세액·납부금액·기본료 등). 이름만으로 행을 지우지 않는다 — 표 구조
# (금액 열·수량 열·행 이름)와 함께 판정한다(_classify_row). '…료'는 비용 접미로 보되 물질 명사는 뺀다.
_MONEY_WORDS = ("요금", "금액", "가액", "세액", "부가세", "부가가치세", "납부", "청구액", "단가", "할인",
                "수수료", "비용", "대금")
_NOT_FEE_ENDINGS = ("연료", "원료", "재료", "자료", "시료", "도료", "비료", "사료", "염료", "안료", "향료",
                    "음료", "완료", "종료")
_DATE_RE = re.compile(r"\d{4}\s*년|\d{1,2}\s*월|\d{4}\s*[-./]\s*\d{1,2}")
_CURRENCY_RE = re.compile(r"^\s*[₩￦]?\s*(?P<num>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*(?:원|krw)?\s*$",
                          re.IGNORECASE)
_ABSENT_WORDS = ("예정", "미검침", "미측정", "미기재", "미입력", "미확인", "미정", "없음", "확인중", "산정중",
                 "추후", "공란")
_NOTE_HEAD_WORDS = ("비고", "메모", "참고", "설명", "특이사항", "주석")


def _is_money_label(text: str) -> bool:
    n = _norm(text)
    if not n or _parse_qty(text):
        return False
    if any(w in n for w in _MONEY_WORDS) or n.endswith("(원)"):
        return True
    stem = re.sub(r"\([^()]*\)$", "", n)
    return stem.endswith("료") and not stem.endswith(_NOT_FEE_ENDINGS)


def _money_number(text: str) -> float | None:
    """금액 칸의 숫자 — 숫자만 있거나 '원'·'₩'이 붙은 칸. 물리 단위가 붙은 칸은 금액이 아니다."""
    q = _parse_qty(text)
    if q is not None:
        return q["value"] if not q["unit"] else None
    m = _CURRENCY_RE.match(text or "")
    return float(m.group("num").replace(",", "")) if m else None


def _is_absent_marker(text: str) -> bool:
    """값이 없다는 표시(빈칸·'-'·'—'·'검침 예정' 등) — 행 이름이 아니다."""
    n = _norm(text)
    if not n or not re.sub(r"[-–—―‐‑·._/\u00ad]", "", n) or n in ("n/a", "na"):
        return True
    return any(w in n for w in _ABSENT_WORDS)


# ---- 머리글 역할 --------------------------------------------------------------

_USAGE_EXCLUDE = ("요금", "금액", "단가", "청구", "부가세", "세액", "지침", "배율", "최대", "평균",
                  "전년", "전월", "이전", "직전", "전회", "계약", "예상", "추정", "내부", "재투입",
                  "계산", "목표", "절감")
_PREV_WORDS = ("이전", "전월", "전회", "직전")
_CUR_WORDS = ("당월", "금월", "현재", "이번", "금회")
_WASTE_EXCLUDE = ("내부", "재투입", "재사용", "공정내", "률", "율", "비율", "방법", "업체", "허가")

# 역할 → (산출 라벨, K-ESG 코드, 허용 단위)
_OUTPUT: dict[str, dict[str, tuple[str, str | None, set[str]]]] = {
    "kepco_bill": {"usage": ("사용전력량", "E-4-1", _ELECTRIC)},
    # 체적(m³)은 E-4-1(TJ) 정의 단위와 다르고 환산 계수가 문서에 없을 수 있어 코드를 두지 않는다.
    "gas_bill": {"usage": ("도시가스 사용량", None, _VOLUME),
                 "heat": ("도시가스 사용열량", "E-4-1", _HEAT)},
    "water_bill": {"usage": ("상수도 사용량", "E-5-1", _VOLUME | {"ton"})},
    "waste_ledger": {"total": ("총 위탁량", "E-6-1", _MASS),
                     "recycled": ("재활용량", None, _MASS)},
}

# 표가 역할을 차지하면 같은 역할의 템플릿 라벨(인접 숫자 + 기본 단위)은 쓰지 않는다.
TEMPLATE_LABELS_BY_ROLE: dict[str, dict[str, tuple[str, ...]]] = {
    "kepco_bill": {"usage": ("사용전력량",)},
    "gas_bill": {"usage": ("가스사용량",), "heat": ("열량",)},
    "water_bill": {"usage": ("사용량",)},
    "waste_ledger": {"total": ("폐기물처리량",), "recycled": ("재활용량",)},
}


def _role(label: str, doc_type: str) -> str | None:
    n = _norm(label)
    if not n:
        return None
    if "지침" in n:
        if any(w in n for w in _PREV_WORDS):
            return "prev"
        if any(w in n for w in _CUR_WORDS):
            return "cur"
        return None
    if any(w in n for w in ("배율", "승률", "배수")):
        return "mult"
    if doc_type == "waste_ledger":
        if any(w in n for w in ("내부", "재투입", "재사용", "공정내")):
            return None
        if any(w in n for w in ("중량", "수량", "위탁량", "처리량", "인계량")) and not _is_total_label(label) \
                and not any(w in n for w in ("총", "전체", "합계")):
            return "detail_mass"
        if "방법" in n:
            return "method"
        if any(w in n for w in _WASTE_EXCLUDE):
            return None
        if _is_total_label(label) or any(w in n for w in ("합계", "총계", "전체", "총위탁", "총배출",
                                                          "총처리", "총량", "총중량")):
            return "total"
        if "재활용" in n:
            return "recycled"
        if "소각" in n:
            return "incineration"
        if "매립" in n:
            return "landfill"
        return None
    if any(w in n for w in _USAGE_EXCLUDE):
        return None
    if doc_type == "gas_bill" and "열량" in n:
        return None if any(w in n for w in ("발열량", "단위", "환산")) else "heat"
    usage_words = {
        "kepco_bill": ("사용량", "사용전력량", "전력량", "사용전력"),
        "gas_bill": ("사용량", "가스량", "사용체적"),
        "water_bill": ("사용량", "급수량", "사용수량"),
    }.get(doc_type, ())
    if any(w in n for w in usage_words):
        return "usage"
    return None


def _unit_role(doc_type: str, role: str | None, unit: str | None) -> str | None:
    """라벨 역할과 원문 단위를 함께 본다 — 가스 '사용량'이 열량 단위(MJ·GJ·TJ)로 적혀
    있으면 그 칸은 부피가 아니라 열량 수치다('사용열량' 표현만 열량으로 보지 않는다)."""
    if doc_type == "gas_bill" and role == "usage" and unit in _HEAT:
        return "heat"
    return role


# 범위 칸의 머리글 → 그 칸이 가리키는 경계 축. 계량기는 경계 필드가 아니라 같은 사실인지
# 가르는 최소 식별 정보로만 남긴다.
_PERIOD_HEAD_WORDS = ("기간", "연월", "년월", "사용월", "청구월", "검침월", "월별", "일자", "날짜")
_SITE_HEAD_WORDS = ("사업장", "공장", "사이트", "지점", "시설", "장소")
_METER_HEAD_WORDS = ("계량기", "계기번호", "미터", "전력계", "가스계량", "수도계량")


def _label_axis(header_text: str) -> str:
    n = _norm(header_text)
    if any(w in n for w in _PERIOD_HEAD_WORDS) or n in ("월", "연도", "년도"):
        return "period"
    if any(w in n for w in _SITE_HEAD_WORDS):
        return "site"
    if any(w in n for w in _METER_HEAD_WORDS):
        return "meter"
    return ""


# ---- 추출 ---------------------------------------------------------------------

@dataclass
class TableMetricResult:
    metrics: list[Any] = field(default_factory=list)       # ExtractedMetric
    records: list[dict[str, Any]] = field(default_factory=list)
    review: list[dict[str, Any]] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    claimed_roles: set[str] = field(default_factory=set)
    detail_sums: list[dict[str, Any]] = field(default_factory=list)
    # 산출 역할 칸은 있는데 값이 비어 있는 칸(빈칸·'-'·'검침 예정') — 표 영역과 함께 둔다.
    # 템플릿 인접 숫자가 그 표의 다른 칸(지침·배율)을 사용량으로 되살리지 않게 막는 근거다.
    absent_cells: list[dict[str, Any]] = field(default_factory=list)
    # 에너지 근거가 아닌 원문 칸 — 금액 열의 칸, 금액 행·보류 행의 수치 칸(reason으로 구분).
    # 사용량·열량 후보가 아니다. 템플릿이 되살리지 않게 둔다.
    money_cells: list[dict[str, Any]] = field(default_factory=list)

    def meta(self) -> dict[str, Any]:
        out = {"records": self.records, "review": self.review, "checks": self.checks,
               "claimed_roles": sorted(self.claimed_roles)}
        if self.absent_cells:
            out["absent_cells"] = [{k: v for k, v in a.items() if k != "row_numbers"}
                                   for a in self.absent_cells]
        return out


def _cell_ref(cell: _Cell) -> dict[str, Any]:
    return {"text": cell.text, "bbox": cell.bbox, "page": cell.page, "precision": cell.precision}


def _union(boxes: list[list[float] | None]) -> list[float] | None:
    bs = [b for b in boxes if b]
    if not bs:
        return None
    return [min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)]


def _index_inputs(row: list[_Cell], roles: list[str | None], header: list[_Cell]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, role in enumerate(roles):
        if role not in ("prev", "cur", "mult") or k >= len(row):
            continue
        q = _parse_qty(row[k].text)
        if q is None:
            continue
        unit = q["unit"] or _header_unit(header[k].text)[0]
        out[role] = {"value": q["value"], "unit": unit, "cell": row[k], "header": header[k].text}
    return out


def _compute_from_index(doc_type: str, inputs: dict[str, Any], allowed: set[str],
                        usage_unit: str | None) -> dict[str, Any] | None:
    """(당월 − 이전) × 배율. 입력이 모자라면 None, 계산 불가 사유는 'reason'으로.

    지침 단위가 서로 다르면(전월 kWh · 당월 MWh) 당월 지침 단위로 맞춘 뒤 빼고, 차이를
    사용량 칸 단위로 환산한다. 지침에 단위가 없으면 사용량 칸 단위로 읽는다(기존 동작).
    한쪽 지침에만 단위가 있거나, 환산할 수 없는 단위(m³ → MJ)면 계산하지 않는다.
    """
    if "prev" not in inputs or "cur" not in inputs:
        return None
    prev, cur = inputs["prev"]["value"], inputs["cur"]["value"]
    prev_unit, cur_unit = inputs["prev"]["unit"], inputs["cur"]["unit"]
    cells = [inputs["prev"]["cell"], inputs["cur"]["cell"]]
    input_units = {"previous": prev_unit, "current": cur_unit}
    if (prev_unit is None) != (cur_unit is None):
        return {"reason": "index_unit_missing", "cells": cells, "input_units": input_units}
    prev_in_cur = prev
    if prev_unit and prev_unit != cur_unit:
        prev_in_cur = convert_to_common(prev, prev_unit, cur_unit)
        if prev_in_cur is None:
            return {"reason": "index_unit_incompatible", "cells": cells, "input_units": input_units}
    if cur < prev_in_cur:
        return {"reason": "index_decreased", "cells": cells, "input_units": input_units}
    index_unit = cur_unit
    if "mult" in inputs:
        mult = inputs["mult"]["value"]
        cells.append(inputs["mult"]["cell"])
        formula = "(당월 지침 − 이전 지침) × 배율"
    elif doc_type != "kepco_bill" and index_unit in allowed:
        # 지침이 부피 단위로 적힌 계량기는 차이가 곧 사용량이다(배율 표기 없음).
        mult, formula = None, "당월 지침 − 이전 지침"
    else:
        return {"reason": "multiplier_missing", "cells": cells, "input_units": input_units}
    unit = usage_unit or (index_unit if index_unit in allowed else None)
    if unit is None:
        return {"reason": "unit_missing", "cells": cells, "input_units": input_units}
    diff = (cur - prev_in_cur) * (mult if mult is not None else 1)
    value = diff
    if index_unit and index_unit != unit:
        value = convert_to_common(diff, index_unit, unit)
        if value is None:
            return {"reason": "unit_incompatible", "cells": cells, "input_units": input_units,
                    "usage_unit": unit}
    inp = {"previous": prev, "current": cur}
    if mult is not None:
        inp["multiplier"] = mult
    out = {"value": round(value, 6), "unit": unit, "formula": formula, "inputs": inp, "cells": cells}
    if index_unit:
        out["input_units"] = input_units
        if index_unit != unit or prev_unit != cur_unit:
            out["conversion"] = f"지침 {prev_unit}·{cur_unit} → {cur_unit}로 맞춰 뺀 뒤 {unit}로 환산"
    return out


def _emit(res: TableMetricResult, *, doc_type: str, role: str, value: float, unit: str,
          raw_unit: str, raw_text: str, header: str, row_label: str, grid: _Grid,
          cells: list[_Cell], value_source: str, unit_source: str,
          index_check: dict[str, Any] | None, confidence: float, formula: str = "",
          label_axis: str = "", scope: dict[str, str] | None = None) -> None:
    from .ocr_router import ExtractedMetric
    label, code, _allowed = _OUTPUT[doc_type][role]
    hint = f"{label} ({row_label})" if row_label else label
    precision = cells[0].precision if len(cells) == 1 else (
        "cells" if all(c.precision == "cell" for c in cells) else "table")
    detail: dict[str, Any] = {
        "extractor": "ocr_table_metrics", "role": role, "table_id": grid.table_id,
        "grid_source": grid.source, "header": header, "row_label": row_label,
        "raw_text": raw_text, "raw_value": value, "raw_unit": raw_unit, "unit": unit,
        "unit_source": unit_source, "value_source": value_source, "precision": precision,
        "cells": [_cell_ref(c) for c in cells],
    }
    if row_label and label_axis:
        detail["row_label_axis"] = label_axis
    if scope:
        detail["scope"] = dict(scope)
    if formula:
        detail["formula"] = formula
    if index_check:
        detail["index_check"] = index_check
    m = ExtractedMetric(
        metric_hint=hint, value=value, unit=unit,
        period=(scope or {}).get("period") or (row_label if label_axis == "period" else ""),
        kesg_code_guess=code,
        bbox=cells[0].bbox if len(cells) == 1 else _union([c.bbox for c in cells]),
        page=cells[0].page, confidence=confidence,
    )
    m.source_detail = detail
    # 표 객체와 텍스트 줄이 **같은 원문 칸**을 겹쳐 제공할 때만 한 번으로 줄인다. 같은 쪽·
    # 같은 범위·같은 값이라도 다른 칸(요약표 + 본문 표)이면 같은 사실인지 원문만으로
    # 알 수 없다 — 두 후보를 모두 두고 중복 여부를 미확정으로 남긴다(합산 단계가 동일
    # 측정값 중복으로 막는다). 범위(사업장·기간·계량기)나 쪽이 다르면 독립된 사실이다.
    twins = []
    for prev in res.metrics:
        pd = prev.source_detail
        if not (pd["role"] == role and prev.value == value and prev.unit == unit
                and pd["row_label"] == row_label and pd.get("scope") == detail.get("scope")):
            continue
        if _same_origin(prev, m):
            return
        if prev.page == m.page:
            twins.append(prev)
    for prev in twins:
        prev.source_detail["duplicate_status"] = detail["duplicate_status"] = "undetermined"
        prev.source_detail.setdefault("possible_duplicate_cells", []).extend(detail["cells"])
        detail.setdefault("possible_duplicate_cells", []).extend(prev.source_detail["cells"])
    res.metrics.append(m)
    res.records.append({k: v for k, v in detail.items() if k != "cells"} | {"metric_hint": hint})


def _same_origin(a: Any, b: Any) -> bool:
    """두 산출물이 같은 원문 칸에서 왔는가 — 같은 쪽이고, 한 위치가 다른 위치의 중심을
    품는다. 두 위치가 모두 칸 좌표면 그것으로 충분하다. 한쪽이 표 외접 bbox(여러 칸을
    감쌈)거나 위치가 없으면 머리글·원문까지 같을 때만 같은 칸으로 본다."""
    if a.page != b.page:
        return False
    da, db = a.source_detail, b.source_detail
    if a.bbox and b.bbox:
        def holds(box, other):
            cx, cy = (other[0] + other[2]) / 2, (other[1] + other[3]) / 2
            return box[0] <= cx <= box[2] and box[1] <= cy <= box[3]
        if not (holds(a.bbox, b.bbox) or holds(b.bbox, a.bbox)):
            return False
        if da.get("precision") == "cell" and db.get("precision") == "cell":
            return True
    return da.get("header") == db.get("header") and da.get("raw_text") == db.get("raw_text")


def _header_row_roles(grid: _Grid, doc_type: str) -> list[str | None]:
    return [_unit_role(doc_type, _role(c.text, doc_type), _header_unit(c.text)[0]) for c in grid.rows[0]]


def _process_header_table(res: TableMetricResult, grid: _Grid, doc_type: str) -> bool:
    """머리글 행 + 데이터 행. 역할 머리글이 없으면 False."""
    header = grid.rows[0]
    if any(_parse_qty(c.text) for c in header):
        return False
    roles = _header_row_roles(grid, doc_type)
    outputs = _OUTPUT.get(doc_type, {})
    if not any(r in outputs or r in ("prev", "cur", "detail_mass") for r in roles):
        return False
    data = grid.rows[1:]
    if not data:
        return True
    # 금액 열 — 머리글이 금액 이름이고 역할·범위 열이 아님('요금(원)'·'청구금액'). 범위 머리글
    # 판정(_label_axis)은 머리글에만 쓴다.
    money_cols = [k for k, (c, r) in enumerate(zip(header, roles))
                  if r is None and not _label_axis(c.text) and _is_money_label(c.text)]
    label_col = 0 if roles[0] is None and 0 not in money_cols else None
    label_axis = _label_axis(header[0].text) if label_col is not None else ""
    # 범위 칸(사업장·기간·계량기)은 열 순서와 무관하게 머리글로 모두 읽는다.
    scope_cols = [(k, axis) for k, (c, r) in enumerate(zip(header, roles))
                  if r is None and (axis := _label_axis(c.text))]
    area = _union([c.bbox for row in grid.rows for c in row])
    # 항목명만으로 행을 지우지 않는다(R8 재보완). 행 구조로 가르고, 제외는 원문 셀 단위로 남긴다.
    kept = []
    for r in data:
        kind, row_name = _classify_row(r, header, roles, set(outputs) | {"prev", "cur", "mult", "detail_mass"},
                                       label_col, label_axis, scope_cols, money_cols)
        if kind == "data":
            kept.append(r)
            _register_excluded_cells(res, grid, r, money_cols, "money_column")
            continue
        res.review.append({"reason": kind, "row_label": row_name, "cells": [c.text for c in r],
                           "table_id": grid.table_id})
        _register_excluded_cells(res, grid, r, range(len(r)), kind)
    data = kept
    if not data:
        return True
    total_rows = [r for r in data if label_col is not None and _is_total_label(r[0].text)]
    if doc_type == "waste_ledger" and "detail_mass" in roles:
        # '구분 | 중량' 아래 행 머리가 총량·재활용량이면 명세가 아니라 항목표다.
        if any(_role(r[0].text, doc_type) in outputs for r in data if r):
            return False
        _collect_detail_sums(res, grid, roles)
        return True
    use_rows = total_rows or data
    per_row: list[list[dict[str, Any]]] = []
    row_axis_of: dict[int, str] = {}
    for row in use_rows:
        row_label = row[label_col].text if label_col is not None and label_col < len(row) else ""
        if row_label and _parse_qty(row_label):
            row_label = ""
        if total_rows:
            row_label = ""
        scope: dict[str, str] = {}
        for k, axis in scope_cols:
            text = row[k].text if k < len(row) else ""
            if text and not _parse_qty(text) and not _is_total_label(text):
                scope.setdefault(axis, text)
        row_axis = label_axis if row_label else next((a for _k, a in scope_cols if a in scope), "")
        if scope:
            row_label = " · ".join(p for p in dict.fromkeys(
                [row_label] + [scope[a] for _k, a in scope_cols if a in scope]) if p)
        inputs = _index_inputs(row, roles, header)
        found: list[dict[str, Any]] = []
        explicit_roles = set()
        absent: list[dict[str, Any]] = []
        held = ""
        for k, role in enumerate(roles):
            if role not in outputs or k >= len(row):
                continue
            cell = row[k]
            q = _parse_qty(cell.text)
            if q is None:
                # 칸은 있으나 값이 없다(빈칸·'-'·'검침 예정'). 역할은 차지하지 않는다(R3).
                absent.append({"role": role, "header": header[k].text, "raw_text": cell.text,
                               "table_id": grid.table_id, "page": cell.page if cell.page is not None
                               else grid.page, "area": area, "row_label": row_label,
                               "row_numbers": sorted({p["value"] for c in row
                                                      if (p := _parse_qty(c.text))})})
                continue
            # 원문 칸에 수치가 있을 때만 역할을 차지한다 — 값을 못 만든 표가 같은 역할의
            # 기존 템플릿 결과를 조용히 지우지 않게 한다(단위 누락·단위 불일치는 검토로 남김).
            res.claimed_roles.add(role)
            h_unit, h_raw = _header_unit(header[k].text)
            unit, raw_unit, unit_source = q["unit"], q["raw_unit"], "cell"
            if unit is None and h_unit:
                unit, raw_unit, unit_source = h_unit, h_raw, "header"
            if unit is None:
                res.review.append({"reason": "unit_missing", "role": role, "header": header[k].text,
                                   "raw_text": cell.text, "table_id": grid.table_id})
                explicit_roles.add(role)
                continue
            unit_role = _unit_role(doc_type, role, unit)
            if unit_role != role and unit_role in outputs:
                role = unit_role
                res.claimed_roles.add(role)
            if unit not in outputs[role][2]:
                res.review.append({"reason": "unit_not_allowed_for_role", "role": role, "unit": unit,
                                   "header": header[k].text, "raw_text": cell.text, "table_id": grid.table_id})
                continue
            explicit_roles.add(role)
            found.append({"role": role, "value": q["value"], "unit": unit, "raw_unit": raw_unit,
                          "raw_text": cell.text, "header": header[k].text, "cells": [cell],
                          "unit_source": unit_source, "value_source": "explicit",
                          "row_label": row_label, "scope": scope})
        usage_role = "usage" if "usage" in outputs else None
        if usage_role and inputs:
            usage_col = next((k for k, r in enumerate(roles) if r == usage_role), None)
            usage_unit = _header_unit(header[usage_col].text)[0] if usage_col is not None else None
            explicit = next((f for f in found if f["role"] == usage_role), None)
            if explicit:
                usage_unit = explicit["unit"]
            comp = _compute_from_index(doc_type, inputs, outputs[usage_role][2], usage_unit)
            if comp and "value" in comp:
                check = {"formula": comp["formula"], "inputs": comp["inputs"], "computed": comp["value"],
                         "computed_unit": comp["unit"], "cells": [_cell_ref(c) for c in comp["cells"]]}
                for key in ("input_units", "conversion"):
                    if key in comp:
                        check[key] = comp[key]
                if explicit:
                    same = abs(explicit["value"] - comp["value"]) < 1e-6
                    check["status"] = "match" if same else "mismatch"
                    explicit["index_check"] = check
                    if not same:
                        res.review.append({"reason": "explicit_vs_index_mismatch", "explicit": explicit["value"],
                                           "computed": comp["value"], "table_id": grid.table_id})
                elif usage_role not in explicit_roles:
                    check["status"] = "computed_only"
                    res.claimed_roles.add(usage_role)
                    found.append({"role": usage_role, "value": comp["value"], "unit": comp["unit"],
                                  "raw_unit": comp["unit"], "raw_text": "", "cells": comp["cells"],
                                  "header": comp["formula"], "unit_source": "index" if not usage_unit else "header",
                                  "value_source": "computed", "row_label": row_label, "scope": scope,
                                  "index_check": check, "formula": comp["formula"]})
            elif comp:
                held = comp["reason"]
                res.review.append({"reason": comp["reason"], "table_id": grid.table_id,
                                   "cells": [c.text for c in comp["cells"]],
                                   **{k: comp[k] for k in ("input_units", "usage_unit") if k in comp}})
                if explicit:
                    explicit["index_check"] = {"status": comp["reason"]}
        # 빈 칸의 상태: 지침으로 계산됨 / 계산 보류(사유) / 값 없음. 어느 경우도 다른 칸의
        # 수치(지침·배율·금액)를 그 칸의 값으로 쓰지 않는다.
        for a in absent:
            a["state"] = ("computed" if any(f["role"] == a["role"] for f in found)
                          else f"held:{held}" if held else "absent")
            if a["state"] != "computed":
                res.review.append({"reason": "value_absent", "role": a["role"], "header": a["header"],
                                   "raw_text": a["raw_text"], "state": a["state"],
                                   "table_id": grid.table_id})
        res.absent_cells.extend(absent)
        for f in found:
            row_axis_of[id(f)] = row_axis
        per_row.append(found)
        if doc_type == "waste_ledger" and not total_rows:
            _check_components(res, grid, roles, row)
    multi = sum(1 for f in per_row if f) > 1
    for found in per_row:
        for f in found:
            conf = 0.9 if f["value_source"] == "explicit" else 0.75
            chk = f.get("index_check") or {}
            if chk.get("status") == "mismatch":
                conf = 0.6
            axis = row_axis_of[id(f)] if f["row_label"] else ""
            if multi and not f["row_label"]:
                f["row_label"] = f"행 {per_row.index(found) + 1}"
            # 행 라벨(사업장·기간)은 데이터 행이 하나여도 원문 범위라 버리지 않는다.
            _emit(res, doc_type=doc_type, role=f["role"], value=f["value"], unit=f["unit"],
                  raw_unit=f["raw_unit"], raw_text=f["raw_text"], header=f["header"],
                  row_label=f["row_label"], grid=grid, cells=f["cells"],
                  value_source=f["value_source"], unit_source=f["unit_source"],
                  index_check=f.get("index_check"), confidence=conf if not multi else min(conf, 0.7),
                  formula=f.get("formula", ""), label_axis=axis, scope=f.get("scope"))
    if doc_type == "waste_ledger" and total_rows:
        _check_components(res, grid, roles, total_rows[0])
    return True


def _classify_row(row: list[_Cell], header: list[_Cell], roles: list[str | None], qty_roles: set[str],
                  label_col: int | None, label_axis: str, scope_cols: list[tuple[int, str]],
                  money_cols: list[int]) -> tuple[str, str]:
    """데이터 행의 종류 → ("data" | 제외·보류 사유, 행 이름).

    · 항목 열이 없는 표에서 수량 열·금액 열에 글자 행 머리가 있으면('기본료 | 247,500') 머리글 구조에
      맞지 않는 행이다 — 표의 물리 단위를 상속하지 않는다. 금액 이름이면 금액 행, 아니면 보류.
    · 행 이름(항목 열·범위 값)이 금액 이름이면: 금액 열에 값이 있고 수량 칸에도 값이 있으면 요금
      명세 행('사용요금 | 8,420 | 360,772 | 247,500') — 수량 칸을 그대로 읽는다. 금액 열이 비어
      수량 칸 숫자의 뜻을 가를 수 없으면 보류, 금액 열이 없는 표면 금액 행이다.
      기간 열의 날짜 값('2026년 5월 요금 청구기간')은 기간 식별값이다."""
    if label_col is None:
        head = next((k for k, c in enumerate(row) if not _is_absent_marker(c.text)), None)
        if head is not None and head < len(roles) and (roles[head] in qty_roles or head in money_cols) \
                and not _parse_qty(row[head].text) and _money_number(row[head].text) is None:
            text = row[head].text
            return ("money_row_excluded" if _is_money_label(text) else "row_label_in_quantity_column"), text
    axis_of = dict(scope_cols)
    names = [(row[k].text, label_axis if k == label_col else axis_of.get(k, ""))
             for k, (c, r) in enumerate(zip(header, roles))
             if k < len(row) and r is None and k not in money_cols
             and not any(w in _norm(c.text) for w in _NOTE_HEAD_WORDS)]
    name = next((t for t, axis in names
                 if _is_money_label(t) and not (axis == "period" and _DATE_RE.search(t))), "")
    if not name:
        return "data", ""
    physical = any(k < len(row) and _parse_qty(row[k].text) for k, r in enumerate(roles) if r in qty_roles)
    money_value = any(k < len(row) and _money_number(row[k].text) is not None for k in money_cols)
    if money_cols and money_value and physical:
        return "data", name
    if money_cols and physical:
        return "money_label_row_unconfirmed", name
    return "money_row_excluded", name


def _register_excluded_cells(res: TableMetricResult, grid: _Grid, row: list[_Cell], cols: Any,
                             reason: str) -> None:
    for k in cols:
        if k >= len(row):
            continue
        c = row[k]
        value = _money_number(c.text)
        if value is None and (q := _parse_qty(c.text)):
            value = q["value"]
        if value is not None:
            res.money_cells.append({"value": value, "raw_text": c.text, "bbox": c.bbox, "reason": reason,
                                    "page": c.page if c.page is not None else grid.page,
                                    "table_id": grid.table_id})


def _process_key_value(res: TableMetricResult, grid: _Grid, doc_type: str) -> None:
    """행 머리 = 라벨인 표('총 위탁량 | 18,400 kg')."""
    outputs = _OUTPUT.get(doc_type, {})
    header = grid.rows[0]
    for r, row in enumerate(grid.rows):
        if len(row) < 2:
            continue
        role = _role(row[0].text, doc_type)
        if role not in outputs:
            continue
        qty = [(k, _parse_qty(c.text)) for k, c in enumerate(row[1:], start=1)]
        qty = [(k, q) for k, q in qty if q]
        if len(qty) != 1:
            continue
        k, q = qty[0]
        res.claimed_roles.add(role)
        unit, raw_unit, unit_source = q["unit"], q["raw_unit"], "cell"
        if unit is None and r > 0:
            h_unit, h_raw = _header_unit(header[k].text) if k < len(header) else (None, "")
            unit, raw_unit, unit_source = h_unit, h_raw, "header"
        if unit is None:
            res.review.append({"reason": "unit_missing", "role": role, "header": row[0].text,
                               "raw_text": row[k].text, "table_id": grid.table_id})
            continue
        unit_role = _unit_role(doc_type, role, unit)
        if unit_role != role and unit_role in outputs:
            role = unit_role
            res.claimed_roles.add(role)
        if unit not in outputs[role][2]:
            res.review.append({"reason": "unit_not_allowed_for_role", "role": role, "unit": unit,
                               "header": row[0].text, "raw_text": row[k].text, "table_id": grid.table_id})
            continue
        _emit(res, doc_type=doc_type, role=role, value=q["value"], unit=unit, raw_unit=raw_unit,
              raw_text=row[k].text, header=row[0].text, row_label="", grid=grid, cells=[row[k]],
              value_source="explicit", unit_source=unit_source, index_check=None, confidence=0.88)


def _to_kg(value: float, unit: str) -> float | None:
    return convert_to_common(value, "t" if unit == "ton" else unit, "kg")


def _check_components(res: TableMetricResult, grid: _Grid, roles: list[str | None], row: list[_Cell]) -> None:
    """총량 = 재활용 + 소각 + 매립(같은 행에 있는 질량 칸 전부)."""
    header = grid.rows[0]
    total = None
    parts: list[float] = []
    for k, role in enumerate(roles):
        if k >= len(row):
            continue
        q = _parse_qty(row[k].text)
        if q is None:
            continue
        unit = q["unit"] or _header_unit(header[k].text)[0]
        if unit not in _MASS:
            continue
        kg = _to_kg(q["value"], unit)
        if role == "total":
            total = kg
        elif role in ("recycled", "incineration", "landfill"):
            parts.append(kg)
    if total is None or len(parts) < 2:
        return
    s = round(sum(parts), 6)
    res.checks.append({"name": "waste_components_sum", "table_id": grid.table_id,
                       "status": "match" if abs(s - total) < 1e-6 else "mismatch",
                       "total_kg": total, "components_kg": parts, "sum_kg": s})


def _collect_detail_sums(res: TableMetricResult, grid: _Grid, roles: list[str | None]) -> None:
    """위탁 명세 행(처리일·폐기물·중량·처리 방법) — 값은 만들지 않고 검산용 합계만 모은다."""
    header = grid.rows[0]
    mass_col = roles.index("detail_mass")
    method_col = roles.index("method") if "method" in roles else None
    unit = _header_unit(header[mass_col].text)[0]
    total = recycled = 0.0
    n = 0
    for row in grid.rows[1:]:
        if mass_col >= len(row) or _is_total_label(row[0].text):
            continue
        q = _parse_qty(row[mass_col].text)
        if q is None:
            continue
        u = q["unit"] or unit
        if u not in _MASS:
            continue
        kg = _to_kg(q["value"], u)
        total += kg
        n += 1
        if method_col is not None and method_col < len(row) and "재활용" in _norm(row[method_col].text):
            recycled += kg
    if n:
        res.detail_sums.append({"table_id": grid.table_id, "rows": n, "total_kg": round(total, 6),
                                "recycled_kg": round(recycled, 6) if method_col is not None else None})


def extract_table_metrics(
    tokens: list[dict[str, Any]], tables: list[Any] | None, *, doc_type: str,
) -> TableMetricResult:
    res = TableMetricResult()
    if doc_type not in _OUTPUT:
        return res
    grids = _grids_from_tables(tables or [])
    if not grids:
        grids = _grids_from_markdown(tokens)
    grids += _grids_from_lines(tokens)
    for grid in grids:
        if not grid.rows:
            continue
        if not _process_header_table(res, grid, doc_type):
            _process_key_value(res, grid, doc_type)
    _flag_conflicts(res)
    if doc_type == "waste_ledger":
        _waste_detail_checks(res)
    return res


def drop_template_candidates_in_absent_tables(res: TableMetricResult, kv_pairs: dict[str, Any],
                                              doc_type: str) -> None:
    """값이 빈 산출 칸이 있는 표 **안에서** 템플릿이 집은 숫자는 버린다.

    그 표의 수치 칸은 모두 머리글 역할로 이미 해석됐다 — 역할을 차지하지 못한 템플릿
    라벨이 표 안에서 찾은 숫자는 빈 칸 옆의 지침·배율·금액이다. 출처 위치가 표 밖(본문
    문장·다른 표·다른 쪽)이면 그대로 둔다. 위치가 없으면 그 행의 다른 칸 수치와 같을 때만
    버린다(출처를 가를 근거가 그것뿐이다). LLM 정규화는 이 KV만 보므로 함께 막힌다."""
    labels = {lb for lbs in TEMPLATE_LABELS_BY_ROLE.get(doc_type, {}).values() for lb in lbs}
    for label in sorted(labels & set(kv_pairs)):
        info = kv_pairs[label]
        hit = next((a for a in res.absent_cells if _candidate_in_table(info, a)), None)
        if hit is None:
            continue
        kv_pairs.pop(label)
        res.review.append({"reason": "template_candidate_from_other_cell", "label": label,
                           "value": info.get("value"), "absent_role": hit["role"],
                           "absent_header": hit["header"], "table_id": hit["table_id"]})


def drop_template_candidates_from_money_cells(res: TableMetricResult, kv_pairs: dict[str, Any],
                                              doc_type: str) -> None:
    """표에서 금액 행으로 뺀 칸의 숫자를 템플릿 라벨이 사용량·열량으로 되살리지 않게 버린다.
    같은 값이어도 위치가 금액 칸 밖(본문·다른 칸)이면 그대로 둔다."""
    labels = {lb for lbs in TEMPLATE_LABELS_BY_ROLE.get(doc_type, {}).values() for lb in lbs}
    for label in sorted(labels & set(kv_pairs)):
        info = kv_pairs[label]
        verdicts = [(mc, _candidate_is_money_cell(info, mc)) for mc in res.money_cells]
        hit = next((mc for mc, v in verdicts if v), None) or next((mc for mc, v in verdicts if v is None), None)
        if hit is None:
            continue
        kv_pairs.pop(label)
        # 위치가 없어 같은 칸인지 확인할 수 없으면 채택하지 않고 보류 사유를 남긴다.
        reason = ("template_candidate_from_money_cell" if any(v for _mc, v in verdicts)
                  else "template_candidate_identity_unknown")
        res.review.append({"reason": reason, "label": label, "value": info.get("value"),
                           "raw_text": hit["raw_text"], "table_id": hit["table_id"]})


def _candidate_is_money_cell(info: dict[str, Any], money: dict[str, Any]) -> bool | None:
    """템플릿 후보가 제외한 원문 칸에서 왔는가 — True/False, 확인할 수 없으면 None."""
    try:
        if abs(float(info.get("value")) - money["value"]) > 1e-9:
            return False
    except (TypeError, ValueError):
        return False
    bbox, box = info.get("bbox"), money.get("bbox")
    if not (bbox and len(bbox) >= 4 and box):
        # 라벨 토큰 자체에 그 숫자가 적혀 있으면('사용열량 247,500 MJ') 그 토큰이 출처다.
        digits = re.sub(r"\D", "", money.get("raw_text") or "")
        return False if digits and digits in re.sub(r"\D", "", info.get("raw_label") or "") else None
    if info.get("page") is not None and money.get("page") is not None and info.get("page") != money.get("page"):
        return False
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return box[0] <= cx <= box[2] and box[1] <= cy <= box[3]


def _candidate_in_table(info: dict[str, Any], absent: dict[str, Any]) -> bool:
    bbox, area = info.get("bbox"), absent.get("area")
    if bbox and len(bbox) >= 4:
        if not area or (info.get("page") is not None and absent.get("page") is not None
                        and info.get("page") != absent.get("page")):
            return False
        cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        return area[0] <= cx <= area[2] and area[1] <= cy <= area[3]
    try:
        value = float(info.get("value"))
    except (TypeError, ValueError):
        return False
    return any(abs(value - v) < 1e-9 for v in absent.get("row_numbers") or ())


def _flag_conflicts(res: TableMetricResult) -> None:
    """같은 역할·같은 행 라벨인데 값이 다르면(표가 둘 이상) 어느 것도 고르지 않고 검토로 올린다."""
    def key(m: Any) -> tuple:
        d = m.source_detail
        return d["role"], d["row_label"], tuple(sorted((d.get("scope") or {}).items()))

    groups: dict[tuple, set[float]] = {}
    for m in res.metrics:
        kg = _to_kg(m.value, m.unit) if m.unit in _MASS else None
        groups.setdefault(key(m), set()).add(kg if kg is not None else float(m.value))
    for k, values in groups.items():
        if len(values) > 1:
            role, row_label, scope = k
            res.review.append({"reason": "conflicting_values", "role": role, "row_label": row_label,
                               "scope": dict(scope), "values": sorted(values)})
            for m in res.metrics:
                if key(m) == k:
                    m.confidence = min(m.confidence, 0.6)


def _metric_kg(res: TableMetricResult, role: str) -> float | None:
    vals = {_to_kg(m.value, m.unit) for m in res.metrics if m.source_detail["role"] == role}
    return next(iter(vals)) if len(vals) == 1 else None


def _waste_detail_checks(res: TableMetricResult) -> None:
    total, recycled = _metric_kg(res, "total"), _metric_kg(res, "recycled")
    for d in res.detail_sums:
        if total is not None:
            res.checks.append({"name": "waste_detail_sum", "table_id": d["table_id"], "rows": d["rows"],
                               "status": "match" if abs(d["total_kg"] - total) < 1e-6 else "mismatch",
                               "detail_kg": d["total_kg"], "total_kg": total})
        if recycled is not None and d["recycled_kg"] is not None:
            res.checks.append({"name": "waste_recycled_detail_sum", "table_id": d["table_id"],
                               "status": "match" if abs(d["recycled_kg"] - recycled) < 1e-6 else "mismatch",
                               "detail_kg": d["recycled_kg"], "recycled_kg": recycled})
    if total is None and res.detail_sums:
        res.review.append({"reason": "detail_rows_without_total", "detail_sums": res.detail_sums})


_RATE_ROUNDING_RULE = "재계산값을 원문 표시 소수 자릿수로 사사오입(ROUND_HALF_UP)한 값이 원문 수치와 같으면 일치"


def check_recycling_rate(res: TableMetricResult, metrics: list[Any]) -> None:
    """원문 재활용률(%)과 재활용량 ÷ 총량 재계산이 **원문 표시 자릿수**에서 일치하는지.

    자릿수는 원문 문자열('29%' → 0자리, '29.0%' → 1자리)에서만 읽는다. float 표기
    (29.0)로는 '29%'와 '29.0%'를 구분할 수 없어 쓰지 않는다. 원문 문자열이 없으면
    불일치로 몰지 않고 자릿수 미상으로 남긴다. 총량·재활용량은 같은 행 범위일 때만 비교한다."""
    from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
    rates = [m for m in metrics if m.kesg_code_guess == "E-6-2" and str(m.unit) == "%"]
    if len(rates) != 1:
        return
    totals = [m for m in res.metrics if m.source_detail["role"] == "total"]
    recycled_ms = [m for m in res.metrics if m.source_detail["role"] == "recycled"]
    if not totals or not recycled_ms:
        return
    scopes = {m.source_detail.get("row_label", "") for m in totals + recycled_ms}
    if len(scopes) != 1:
        res.review.append({"reason": "rate_check_scope_differs", "scopes": sorted(scopes),
                           "note": "총량과 재활용량의 행 범위가 달라 재활용률을 검산하지 않음"})
        return
    total, recycled = _metric_kg(res, "total"), _metric_kg(res, "recycled")
    if not total or recycled is None:
        return
    rate = rates[0]
    reported = float(rate.value)
    raw = str((getattr(rate, "source_detail", None) or {}).get("raw_text") or "")
    num = re.search(r"\d+(?:\.\d+)?", raw)
    exact = Decimal(repr(recycled)) / Decimal(repr(total)) * 100
    check = {"name": "waste_recycling_rate", "computed": round(float(exact), 4), "reported": reported,
             "reported_text": raw, "formula": "재활용량 ÷ 총 위탁량 × 100", "rounding": _RATE_ROUNDING_RULE}
    if not num:
        check.update(status="precision_unknown",
                     note="원문 비율 문자열이 없어 표시 자릿수를 알 수 없음 — 불일치로 판정하지 않음")
        res.checks.append(check)
        return
    digits = num.group(0)
    decimals = len(digits.split(".")[1]) if "." in digits else 0
    try:
        shown = Decimal(digits)
    except InvalidOperation:
        return
    rounded = exact.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    check.update(status="match" if rounded == shown else "mismatch", display_decimals=decimals,
                 computed_rounded=str(rounded))
    res.checks.append(check)


def refine_bboxes_with_pdf(metrics: list[Any], file_path: str) -> None:
    """표·텍스트 요소 외접 bbox만 있는 값을 PDF 문자 좌표로 좁힌다(그 안에서 정확히 한 번 나올 때만)."""
    p = Path(file_path)
    targets = [m for m in metrics if getattr(m, "source_detail", None)
               and m.source_detail.get("precision") in ("table", "text_block")]
    if not targets or not p.is_file() or p.suffix.lower() != ".pdf":
        return
    try:
        import fitz
    except ImportError:
        return
    try:
        doc = fitz.open(str(p))
    except Exception:
        return
    with doc:
        for m in targets:
            refined = []
            for ref in m.source_detail["cells"]:
                box = _search_cell(doc, ref)
                if box is None:
                    refined = []
                    break
                refined.append(box)
            if not refined:
                continue
            for ref, box in zip(m.source_detail["cells"], refined):
                ref["bbox"], ref["precision"] = box, "pdf_text"
            m.bbox = refined[0] if len(refined) == 1 else _union(refined)
            area = "텍스트 요소" if m.source_detail["precision"] == "text_block" else "표"
            m.source_detail["precision"] = "pdf_text" if len(refined) == 1 else "cells"
            m.source_detail["precision_note"] = f"OCR {area} bbox 안에서 PDF 문자 좌표로 좁힘"


def _search_cell(doc: Any, ref: dict[str, Any]) -> list[float] | None:
    import fitz
    page_no, bbox, text = ref.get("page"), ref.get("bbox"), str(ref.get("text") or "")
    if page_no is None or not bbox or not text or page_no >= len(doc):
        return None
    page = doc[page_no]
    w, h = page.rect.width, page.rect.height
    clip = fitz.Rect(bbox[0] * w - 2, bbox[1] * h - 2, bbox[2] * w + 2, bbox[3] * h + 2)
    q = _parse_qty(text)
    needles = [text]
    if q:
        num = re.match(r"\s*([+-]?[\d,]+(?:\.\d+)?)", text)
        if num and num.group(1) != text:
            needles.append(num.group(1))
    for needle in needles:
        hits = page.search_for(needle, clip=clip)
        if len(hits) == 1:
            r = hits[0]
            # 숫자만 찾았으면 셀 전체가 아닌 숫자 좌표다 — 같은 줄의 단위까지 넓히지 않는다.
            return [round(r.x0 / w, 4), round(r.y0 / h, 4), round(r.x1 / w, 4), round(r.y1 / h, 4)]
        if len(hits) > 1:
            return None
    return None
