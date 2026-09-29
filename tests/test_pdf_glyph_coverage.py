"""PDF에 실리는 글자가 번들 폰트에 실제로 있는지 — 2026-09-20 §8 렌더 회귀.

고친 결함(HMC 응답서 PDF 렌더 실측)
  · "D1 불일치: 자가신고 92.0% ↔ 증빙 29.3%"의 ↔(U+2194)가 빈 네모(□)로 찍혔다.
    번들 서브셋 NotoSansKR에 그 글리프가 없다. 텍스트 추출로는 잡히지 않는다 —
    추출하면 ↔가 그대로 나오고, 눈으로 봐야 네모가 보인다. 그래서 추출 대조가 아니라
    **폰트 글리프 보유 여부**를 검사한다.

고정하는 계약
  1. pdf_safe_text를 거친 문자열에는 번들 폰트가 못 그리는 문자가 남지 않는다.
  2. 비교 기호는 지우지 않고 바꾼다 — 지우면 무엇과 무엇을 견줬는지 사라진다.
  3. 검사가 불가능한 폰트(Helvetica 폴백)에서는 조용히 통과한다.

입력 구분: 통제 실험. DataPoint·SupplierClaim을 직접 구성해 실제 mapping 경로로
Answer를 만들고, 그 Answer가 PDF에 실을 문자열을 검사한다. OCR/LLM 호출 없음.
"""
from __future__ import annotations

import pytest

from esgenie.ssot.audit_trace import DataPoint, EvidenceLink
from esgenie.supplychain.claims import SupplierClaim
from esgenie.supplychain.exporters._fonts import (
    REGULAR_NAME,
    pdf_safe_text,
    resolve_korean_font,
    unsupported_chars,
)
from esgenie.supplychain.mapping import derive_answer
from esgenie.supplychain.schema import Question


@pytest.fixture(scope="module")
def font():
    f = resolve_korean_font()
    if not f.embedded:
        pytest.skip("한글 폰트 미임베드 — 글리프 검사 불가")
    return f


def _mismatch_answer():
    """자가주장 92% ↔ 증빙 29.3% — ↔가 실제로 들어가는 경로."""
    q = Question(qid="E-6-2", section="환경", text="폐기물 재활용률",
                 qtype="numeric", kesg_codes=("E-6-2",), unit_hint="%")
    link = EvidenceLink(
        file_name="03_사업장폐기물_위탁처리명세_2026-04.pdf",
        relative_path="evidence_pack/03_사업장폐기물_위탁처리명세_2026-04.pdf",
        origin="ocr_unstructured", page=0, node_id="OCR_0001",
        kesg_codes=["E-6-2"], quote="재활용 29.3%", resolved=True, independent=True)
    dp = DataPoint(kesg_code="E-6-2", kesg_name="폐기물 재활용률", value=29.3,
                   unit="%", period=2026, confidence=0.9, verification="verified",
                   d1_risk=0.1, evidence_files=[link], completeness="total")
    claim = SupplierClaim(code="E-6-2", value=92.0, unit="%", raw="재활용률 92%",
                          source="saq:05_OEM_ESG자가진단설문_한성모터스.pdf")
    return derive_answer(q, mapped={}, missing=set(), dp_by_code={"E-6-2": dp},
                         evidence_index={link.node_id: link},
                         claims={"E-6-2": claim})


class TestTheComparisonSymbolIsReadable:
    def test_the_mismatch_flag_really_contains_the_arrow(self, font):
        """전제 확인 — 이 경로가 실제로 미지원 기호를 만든다."""
        ans = _mismatch_answer()
        raw = " ".join(ans.flags)
        assert "↔" in raw
        assert unsupported_chars(raw, REGULAR_NAME), "폰트가 ↔를 지원하면 이 테스트는 무의미"

    def test_the_comparison_survives_as_words_not_as_a_hole(self, font):
        """기호를 지우지 않고 바꾼다 — 두 값과 견줌 관계가 모두 남아야 한다."""
        safe = pdf_safe_text(" ".join(_mismatch_answer().flags))
        assert "92.0%" in safe and "29.3%" in safe
        assert "vs" in safe, safe


class TestNoGlyphHolesInAnyOutputString:
    """응답서가 PDF에 싣는 모든 문자열에 빈 네모가 남지 않는다."""

    def test_answer_fields_are_fully_drawable(self, font):
        """필드를 손으로 나열하지 않고 직렬화 전체를 훑는다 — 새 필드가 생겨도 덮인다."""
        def strings(v):
            if isinstance(v, str):
                yield v
            elif isinstance(v, dict):
                for x in v.values():
                    yield from strings(x)
            elif isinstance(v, (list, tuple)):
                for x in v:
                    yield from strings(x)

        checked = 0
        for raw in strings(_mismatch_answer().to_dict()):
            checked += 1
            missing = unsupported_chars(pdf_safe_text(raw), REGULAR_NAME)
            assert not missing, f"{raw!r} → 그릴 수 없는 문자 {missing}"
        assert checked > 10, f"검사한 문자열이 {checked}개뿐 — 직렬화가 비었는지 확인"

    @pytest.mark.parametrize("raw", [
        "자가신고 92.0% ↔ 증빙 29.3%",
        "단위 차원 상이(MJ ↔ kWh)",
        "차이 62.70%p, 기준 ≥10%p",
        "한울정밀공업㈜ 실사응답서",
        "🚩 검토필요 · ✍ 작성필요 · ⚠ 확인",
        "실적 ↔ 목표/계획은 비교 대상이 아니다",
        "─────────",
    ])
    def test_known_output_shapes_are_fully_drawable(self, raw, font):
        assert not unsupported_chars(pdf_safe_text(raw), REGULAR_NAME)

    def test_status_words_survive_emoji_removal(self, font):
        """배지 그림은 빼도 상태 글자는 남는다."""
        safe = pdf_safe_text("🚩 검토필요")
        assert "검토필요" in safe and "🚩" not in safe


class TestTheGuardDoesNotFireWithoutAFont:
    def test_unknown_font_is_not_reported_as_broken(self):
        """확인할 수 없는 폰트를 두고 '글리프가 없다'고 말하지 않는다."""
        assert unsupported_chars("한글 ↔ 기호", "NoSuchFontRegistered") == set()

    def test_ascii_is_never_flagged(self, font):
        assert unsupported_chars("plain ASCII 123 %p", REGULAR_NAME) == set()
