"""Reusable UI primitives for the Streamlit app."""
from __future__ import annotations

from html import escape
from typing import Iterable, Sequence

def badge_html(text: str, tone: str = "neutral") -> str:
    return f'<span class="eg-pill {escape(tone)}">{escape(text)}</span>'


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
