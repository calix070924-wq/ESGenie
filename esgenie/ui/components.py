"""Reusable UI primitives for the Streamlit app."""
from __future__ import annotations

from html import escape
from typing import Iterable, Sequence

def badge_html(text: str, tone: str = "neutral") -> str:
    return f'<span class="eg-pill {escape(tone)}">{escape(text)}</span>'


def section_badge_html(text: str) -> str:
    """primaryColor 옅은 톤의 섹션 구분 배지."""
    return f'<span class="eg-section-badge">{escape(text)}</span>'


def render_section_badge(text: str) -> None:
    import streamlit as st

    st.markdown(section_badge_html(text), unsafe_allow_html=True)


def meta_chip_html(text: str) -> str:
    return f'<span class="eg-meta-chip">{escape(text)}</span>'


def hero_html(
    *,
    kicker: str,
    title: str,
    subtitle: str,
    badges: Sequence[str] | None = None,
    meta: Sequence[str] | None = None,
) -> str:
    badge_row = "".join(badges or [])
    meta_row = "".join(meta_chip_html(item) for item in (meta or []))
    return (
        '<div class="eg-hero">'
        f'<span class="eg-kicker">{escape(kicker)}</span>'
        f"<h1>{escape(title)}</h1>"
        f"<p>{escape(subtitle)}</p>"
        + (f'<div class="eg-badge-row">{badge_row}</div>' if badge_row else "")
        + (f'<div class="eg-inline-meta">{meta_row}</div>' if meta_row else "")
        + "</div>"
    )


def render_section_header(title: str, subtitle: str, *, kicker: str | None = None) -> None:
    import streamlit as st

    kicker_html = f'<span class="eg-kicker">{escape(kicker)}</span>' if kicker else ""
    st.markdown(
        (
            '<div class="eg-section-head">'
            f"{kicker_html}"
            f"<h2>{escape(title)}</h2>"
            f"<p>{escape(subtitle)}</p>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def stat_card_html(label: str, value: str, note: str = "") -> str:
    return (
        '<div class="eg-stat-card">'
        f'<div class="eg-stat-label">{escape(label)}</div>'
        f'<div class="eg-stat-value">{escape(value)}</div>'
        + (f'<div class="eg-stat-note">{escape(note)}</div>' if note else "")
        + "</div>"
    )


def render_stat_row(cards: Sequence[dict[str, str]], *, columns: int | None = None) -> None:
    import streamlit as st

    if not cards:
        return
    ncols = columns or min(len(cards), 6)
    for start in range(0, len(cards), ncols):
        row_cards = cards[start : start + ncols]
        cols = st.columns(len(row_cards))
        for col, card in zip(cols, row_cards):
            with col:
                st.markdown(
                    stat_card_html(
                        card.get("label", ""),
                        card.get("value", "—"),
                        card.get("note", ""),
                    ),
                    unsafe_allow_html=True,
                )


def panel_html(title: str, body: str, *, tone: str = "neutral", compact_note: str = "") -> str:
    note_html = f'<div class="eg-compact-note">{escape(compact_note)}</div>' if compact_note else ""
    return (
        f'<div class="eg-panel {escape(tone)}">'
        f"<h3>{escape(title)}</h3>"
        f"<p>{escape(body)}</p>"
        f"{note_html}"
        "</div>"
    )


def callout_html(title: str, items: Iterable[str], *, tone: str = "info") -> str:
    li_html = "".join(f"<li>{escape(item)}</li>" for item in items)
    return (
        f'<div class="eg-panel {escape(tone)}">'
        f"<h3>{escape(title)}</h3>"
        f'<ul class="eg-list">{li_html}</ul>'
        "</div>"
    )


def download_tile_html(title: str, body: str, *, note: str = "") -> str:
    note_html = f'<div class="eg-compact-note">{escape(note)}</div>' if note else ""
    return (
        '<div class="eg-download-tile">'
        f"<h3>{escape(title)}</h3>"
        f"<p>{escape(body)}</p>"
        f"{note_html}"
        "</div>"
    )


def render_empty_state(title: str, message: str) -> None:
    import streamlit as st

    st.markdown(
        (
            '<div class="eg-empty">'
            f"<strong>{escape(title)}</strong>"
            f"<span>{escape(message)}</span>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )


# streamlit-extras 카드 컨테이너 CSS — primaryColor(#2E6F40) 강조, 그림자·radius·padding
_METRIC_CARD_CSS = """
{
    border: 1px solid rgba(46, 111, 64, 0.14);
    border-top: 3px solid #2E6F40;
    border-radius: 18px;
    padding: 1.15rem 1.2rem;
    background: #ffffff;
    box-shadow: 0 12px 30px rgba(60, 48, 24, 0.10);
}
"""


def render_metric_cards(cards: Sequence[dict[str, str]], *, columns: int | None = None) -> None:
    """지표(gap score, ESG 항목별 점수 등)를 stylable_container 카드형 UI 로 렌더.

    streamlit-extras 가 없으면 기존 stat_card_html 로 자동 폴백한다.
    """
    import streamlit as st

    if not cards:
        return

    try:
        from streamlit_extras.stylable_container import stylable_container
    except ImportError:
        render_stat_row(cards, columns=columns)
        return

    ncols = columns or min(len(cards), 6)
    for start in range(0, len(cards), ncols):
        row_cards = cards[start : start + ncols]
        cols = st.columns(len(row_cards))
        for idx, (col, card) in enumerate(zip(cols, row_cards)):
            with col:
                with stylable_container(key=f"eg-metric-{start + idx}", css_styles=_METRIC_CARD_CSS):
                    label = escape(card.get("label", ""))
                    value = escape(card.get("value", "—"))
                    note = card.get("note", "")
                    note_html = f'<div class="eg-metric-note">{escape(note)}</div>' if note else ""
                    st.markdown(
                        f'<div class="eg-metric-label">{label}</div>'
                        f'<div class="eg-metric-value">{value}</div>'
                        f"{note_html}",
                        unsafe_allow_html=True,
                    )


def render_pipeline_loading(message: str) -> None:
    """L0~L5 파이프라인 진행 중 shimmer 로딩 텍스트.

    Streamlit 1.31+ 의 :shimmer[...] 디렉티브를 쓰고, 미지원 시 커스텀 CSS 로 폴백.
    """
    import streamlit as st

    try:
        st.markdown(f":shimmer[{message}]")
    except Exception:  # 구버전 폴백
        st.markdown(f'<span class="eg-shimmer">{escape(message)}</span>', unsafe_allow_html=True)
