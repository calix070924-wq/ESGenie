"""동일 확인 목록을 PDF로 옮길 때 원문 기호·소수점이 달라지지 않는다."""
from esgenie.exporters.report_pdf import _inline
from esgenie.source_review import ReviewEvidence, ReviewFinding, review_markdown


def test_review_symbols_survive_pdf_inline_conversion():
    finding = ReviewFinding(
        id="review", category="source_fact", title="값 확인", fact="45.1% → 40.2%",
        reason="원문 **표시** 및 _기호_", action="<표> & [주석] 확인",
        evidence=[ReviewEvidence(source_file="a_b.pdf", page=0, quote="A | 45.1% (2024)")],
    )
    converted = _inline(review_markdown([finding]))
    assert "45.1% → 40.2%" in converted
    assert "원문 **표시** 및 _기호_" in converted
    assert "&lt;표&gt; &amp; [주석] 확인" in converted
    assert "a_b.pdf" in converted
    assert "A | 45.1% (2024)" in converted
    assert "\\" not in converted


def test_pdf_retains_requested_formatting_around_escaped_literal():
    assert _inline(r"**값 a\_b**와 `code` 및 _설명_") == (
        "<b>값 a_b</b>와 <font face='Courier'>code</font> 및 <i>설명</i>")
    assert _inline(r"경로 a\\b") == "경로 a\\b"
