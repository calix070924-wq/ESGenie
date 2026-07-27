"""P축 규정 검증 체크리스트의 K-ESG 코드 정합성.

2026-07-28 발견: POLICY_CHECKLISTS의 키 11개 중 9개가 실제 K-ESG 항목과 어긋나
있었다. 예를 들어 산업안전보건 체크리스트가 S-3-1(여성 구성원 비율)에 붙어 있어
"여성 구성원 비율 항목에 근로자 대표 참여 문구가 없다"는 지적이 UI 규정 검증 탭과
엑셀 데이터시트에 그대로 출력됐다.

이 테스트는 그 부류의 오배치를 다시 들여보내지 않는 안전망이다. 체크리스트를
추가·수정할 때 주석의 항목명을 kesg_items.py의 KESGItem.name과 정확히 맞춰야 한다.
"""

from __future__ import annotations

import re
from pathlib import Path

from esgenie.knowledge.kesg_items import ALL_ITEMS
from esgenie.pipeline import POLICY_CODES
from esgenie.ssot.prompts import POLICY_CHECKLISTS

_NAME_BY_CODE = {item.code: item.name for item in ALL_ITEMS}
_PROMPTS_SRC = Path(__file__).resolve().parents[1] / "esgenie" / "ssot" / "prompts.py"


def _checklist_comments() -> dict[str, str]:
    """POLICY_CHECKLISTS 블록에서 `"코드": [   # 항목명` 주석을 뽑는다."""
    src = _PROMPTS_SRC.read_text(encoding="utf-8")
    block = src[src.index("POLICY_CHECKLISTS"):]
    return {
        code: comment.strip()
        for code, comment in re.findall(r'"([A-Z]-\d-\d)":\s*\[\s*#\s*([^\n]+)', block)
    }


def test_checklist_codes_exist_in_kesg_items() -> None:
    unknown = sorted(set(POLICY_CHECKLISTS) - set(_NAME_BY_CODE))
    assert not unknown, f"K-ESG 61항목에 없는 코드가 체크리스트 키로 쓰임: {unknown}"


def test_checklist_comment_matches_kesg_item_name() -> None:
    """주석 항목명 == KESGItem.name.

    주석이 실제 항목명과 다르면 체크리스트 내용이 엉뚱한 항목을 검사하고 있다는
    신호다(오배치 9건이 전부 이 규칙 하나로 잡혔다).
    """
    comments = _checklist_comments()
    assert set(comments) == set(POLICY_CHECKLISTS), (
        "모든 체크리스트 항목은 `# 항목명` 주석을 달아야 한다: "
        f"{sorted(set(POLICY_CHECKLISTS) - set(comments))}"
    )

    mismatched = {
        code: (comment, _NAME_BY_CODE[code])
        for code, comment in comments.items()
        if _NAME_BY_CODE.get(code) != comment
    }
    assert not mismatched, (
        "체크리스트 주석과 실제 K-ESG 항목명 불일치 — "
        "체크리스트가 다른 항목을 검사 중일 가능성이 높다: " + repr(mismatched)
    )


def test_policy_codes_exist_in_kesg_items() -> None:
    unknown = sorted(set(POLICY_CODES) - set(_NAME_BY_CODE))
    assert not unknown, f"POLICY_CODES에 K-ESG 61항목 밖의 코드: {unknown}"


def test_checklists_are_subset_of_policy_codes() -> None:
    """체크리스트는 규정 검증 대상(POLICY_CODES) 안에서만 정의한다.

    대상 밖 코드에 체크리스트를 달아두면 라이브에선 절대 실행되지 않으면서
    "구현돼 있다"는 착시만 남는다.
    """
    orphan = sorted(set(POLICY_CHECKLISTS) - set(POLICY_CODES))
    assert not orphan, f"POLICY_CODES에 없는 코드의 체크리스트: {orphan}"


def test_demo_fallback_codes_have_checklists() -> None:
    """규정 문서가 없을 때 강제 검증하는 2건은 체크리스트가 반드시 있어야 한다.

    없으면 detector_5axis가 no-checklist 조기 반환으로 무조건 passed=True를 주고,
    데모 화면의 규정 통과율이 근거 없이 100%가 된다.
    """
    for code in ("S-4-1", "E-1-1"):
        assert code in POLICY_CHECKLISTS, f"{code}({_NAME_BY_CODE.get(code)}) 체크리스트 누락"


def test_no_checklist_codes_are_documented() -> None:
    """체크리스트 없는 POLICY_CODES는 통과율 분모를 부풀린다 — 현황을 고정해 둔다.

    이 목록이 줄면(체크리스트를 채우면) 테스트를 갱신하면 된다. 늘어나면
    검증 없이 무조건 통과하는 항목이 늘었다는 뜻이라 실패시킨다.
    """
    uncovered = sorted(set(POLICY_CODES) - set(POLICY_CHECKLISTS))
    assert uncovered == ["G-3-1", "G-5-1", "S-1-1", "S-2-6", "S-7-1"], (
        "체크리스트 미보유 POLICY_CODES가 변했다. 늘었다면 규정 통과율이 더 "
        f"부풀려진다: {uncovered}"
    )
