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


def render_download_tiles(tiles: Sequence[dict[str, str]]) -> None:
    """다운로드 카드들을 CSS grid 한 블록으로 렌더.

    st.columns 로 그리면 카드마다 높이가 달라 어긋나므로, grid + align-items:stretch
    로 묶어 가장 긴 카드(예: 본문 3줄) 높이에 나머지 카드를 맞춘다.
    """
    import streamlit as st

    if not tiles:
        return
    cards = "".join(
        download_tile_html(t.get("title", ""), t.get("body", ""), note=t.get("note", ""))
        for t in tiles
    )
    st.markdown(
        f'<div class="eg-download-grid" style="--eg-tile-cols: {len(tiles)};">{cards}</div>',
        unsafe_allow_html=True,
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


def metric_card_html(label: str, value: str, note: str = "") -> str:
    note_html = f'<div class="eg-metric-note">{escape(note)}</div>' if note else ""
    return (
        '<div class="eg-metric-card">'
        f'<div class="eg-metric-label">{escape(label)}</div>'
        f'<div class="eg-metric-value">{escape(value)}</div>'
        f"{note_html}"
        "</div>"
    )


def render_metric_cards(cards: Sequence[dict[str, str]], *, columns: int | None = None) -> None:
    """지표(gap score, ESG 항목별 점수 등)를 카드형 UI 로 렌더.

    CSS grid 한 블록으로 그려 카드 간격을 균등하게 맞춘다.
    (streamlit-extras 의 stylable_container 는 화면에 deprecation 경고를 띄워 정렬을
     깨뜨리므로 사용하지 않는다.)
    """
    import streamlit as st

    if not cards:
        return

    ncols = columns or min(len(cards), 6)
    tiles = "".join(
        metric_card_html(card.get("label", ""), card.get("value", "—"), card.get("note", ""))
        for card in cards
    )
    st.markdown(
        f'<div class="eg-metric-grid" style="--eg-metric-cols: {ncols};">{tiles}</div>',
        unsafe_allow_html=True,
    )


def render_report_card(text: str, kind: str = "draft", tag_label: str | None = None) -> None:
    """보고서 본문을 카드형 컨테이너 안에 네이티브 마크다운으로 렌더.

    이전에는 raw 마크다운을 HTML <div> 로 감쌌으나, HTML 블록 안의 마크다운은
    재파싱되지 않아 표·헤딩(###, |)이 그대로 노출됐다. st.container 로 감싸고
    본문은 st.markdown 네이티브로 넘겨 표·헤딩이 정상 렌더되게 한다.
    카드 외곽 스타일은 숨김 marker + CSS :has(...) 로 래퍼에 적용한다.
    """
    import streamlit as st

    with st.container(border=True):
        st.markdown(
            f'<span class="eg-report-card-marker {escape(kind)}" aria-hidden="true"></span>',
            unsafe_allow_html=True,
        )
        if tag_label:
            st.markdown(
                f'<span class="esg-report-tag {escape(kind)}">{escape(tag_label)}</span>',
                unsafe_allow_html=True,
            )
        st.markdown(text)


def render_pipeline_loading(message: str) -> None:
    """L0~L5 파이프라인 진행 중 shimmer 로딩 텍스트.

    Streamlit markdown directive 지원 여부와 무관하게 동일하게 보이도록
    커스텀 CSS shimmer 만 사용한다.
    """
    import streamlit as st

    st.markdown(f'<span class="eg-shimmer">{escape(message)}</span>', unsafe_allow_html=True)
