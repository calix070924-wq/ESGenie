"""Theme and visual tokens for the Streamlit app."""
from __future__ import annotations


PLOTLY_TEMPLATE = {
    "paper_bgcolor": "rgba(0,0,0,0)",
    "plot_bgcolor": "#FFFFFF",
    "font": {"family": "Pretendard, SUIT, Noto Sans KR, Apple SD Gothic Neo, sans-serif", "color": "#162218"},
}


def apply_theme() -> None:
    """Inject the shared Streamlit theme."""
    import streamlit as st

    st.markdown(
        """
<style>
:root {
    /* 크림 배경 + ESG 그린 액센트 (config.toml 과 동일 팔레트) */
    --bg-app: #f5f2eb;
    --bg-app-soft: #efeade;
    --bg-panel: #fbf9f4;
    --bg-elevated: #ffffff;
    --bg-subtle: #efeade;
    --bg-subtle-2: #ebe6da;
    --bg-dark: #1c2c21;
    --text-strong: #1f2a22;
    --text-muted: #5f675c;
    --text-soft: #8a8577;
    --border-soft: rgba(46, 111, 64, 0.14);
    --border-strong: rgba(46, 111, 64, 0.30);
    --accent-primary: #2e6f40;
    --accent-primary-strong: #245732;
    --accent-secondary: #8aa88f;
    --accent-highlight: #dfeae0;
    --accent-tint: rgba(46, 111, 64, 0.10);
    --accent-tint-strong: rgba(46, 111, 64, 0.16);
    --status-success: #2e7d32;
    --status-warning: #c58a18;
    --status-danger: #b7463b;
    --status-info: #295e8a;
    --shadow-soft: 0 18px 44px rgba(60, 48, 24, 0.08);
    --shadow-card: 0 12px 30px rgba(60, 48, 24, 0.10);
    --radius-lg: 24px;
    --radius-md: 18px;
    --radius-sm: 14px;
    --radius-btn: 8px;
    --font-ui: Pretendard, SUIT, "Noto Sans KR", "Apple SD Gothic Neo", sans-serif;
}

html, body, [class*="css"], [data-testid="stAppViewContainer"], [data-testid="stSidebar"] {
    font-family: var(--font-ui);
}

.stApp {
    color: var(--text-strong);
    background:
        radial-gradient(circle at top left, rgba(46, 111, 64, 0.10), transparent 26%),
        radial-gradient(circle at top right, rgba(138, 168, 143, 0.10), transparent 24%),
        linear-gradient(180deg, var(--bg-app) 0%, var(--bg-app-soft) 100%);
}

[data-testid="stHeader"] {
    background: var(--bg-app);
}

.block-container {
    max-width: 1460px;
    padding-top: 1.8rem;
    padding-bottom: 4rem;
}

section[data-testid="stSidebar"] {
    background:
        radial-gradient(circle at top, rgba(137, 168, 142, 0.16), transparent 30%),
        linear-gradient(180deg, #162218 0%, #1c2c21 100%);
    border-right: 1px solid rgba(255, 255, 255, 0.06);
}

section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] span,
section[data-testid="stSidebar"] small,
section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3,
section[data-testid="stSidebar"] h4,
section[data-testid="stSidebar"] li,
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"],
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] *,
section[data-testid="stSidebar"] [data-testid="stWidgetLabel"],
section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] *,
section[data-testid="stSidebar"] [role="radiogroup"] label,
section[data-testid="stSidebar"] [role="radiogroup"] label * {
    color: rgba(247, 250, 246, 0.96) !important;
}

section[data-testid="stSidebar"] [data-baseweb="input"] > div,
section[data-testid="stSidebar"] [data-baseweb="select"] > div,
section[data-testid="stSidebar"] textarea,
section[data-testid="stSidebar"] input {
    background: rgba(255, 255, 255, 0.96) !important;
    border: 1px solid rgba(21, 32, 24, 0.18) !important;
}

section[data-testid="stSidebar"] [data-baseweb="input"] input,
section[data-testid="stSidebar"] [data-baseweb="base-input"] input,
section[data-testid="stSidebar"] [data-baseweb="select"] input,
section[data-testid="stSidebar"] [data-baseweb="select"] span,
section[data-testid="stSidebar"] [data-baseweb="select"] div,
section[data-testid="stSidebar"] textarea,
section[data-testid="stSidebar"] input {
    color: #111827 !important;
    -webkit-text-fill-color: #111827 !important;
    caret-color: #111827 !important;
}

section[data-testid="stSidebar"] input::placeholder,
section[data-testid="stSidebar"] textarea::placeholder {
    color: #6b7280 !important;
    -webkit-text-fill-color: #6b7280 !important;
    opacity: 1 !important;
}

section[data-testid="stSidebar"] [data-baseweb="select"] svg,
section[data-testid="stSidebar"] [data-baseweb="input"] svg {
    fill: #1f2937 !important;
}

section[data-testid="stSidebar"] [data-baseweb="tag"] {
    background: #e8efe9 !important;
    color: #111827 !important;
}

section[data-testid="stSidebar"] .stSelectbox label,
section[data-testid="stSidebar"] .stTextInput label,
section[data-testid="stSidebar"] .stNumberInput label,
section[data-testid="stSidebar"] .stCheckbox label,
section[data-testid="stSidebar"] .stRadio label {
    color: rgba(247, 250, 246, 0.96) !important;
}

.stButton > button,
.stDownloadButton > button {
    border-radius: var(--radius-btn);
    border: 1px solid var(--border-soft);
    background: #ffffff;
    color: var(--text-strong);
    font-weight: 700;
    min-height: 2.8rem;
    transition: background 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease;
    box-shadow: 0 6px 16px rgba(60, 48, 24, 0.06);
}

/* 기본(primary) 버튼: 배경 #2E6F40 */
.stButton > button[kind="primary"] {
    background: var(--accent-primary);
    color: #ffffff;
    border-color: var(--accent-primary-strong);
    box-shadow: 0 12px 26px rgba(46, 111, 64, 0.26);
}

/* hover 시 살짝 어둡게 */
.stButton > button[kind="primary"]:hover {
    background: var(--accent-primary-strong);
    border-color: var(--accent-primary-strong);
    color: #ffffff;
    box-shadow: 0 14px 30px rgba(36, 87, 50, 0.32);
}

.stButton > button:hover,
.stDownloadButton > button:hover {
    border-color: var(--border-strong);
    background: #f0ede4;
    color: var(--text-strong);
}

.stButton > button:focus,
.stDownloadButton > button:focus,
.stButton > button:focus-visible,
.stDownloadButton > button:focus-visible,
.stButton > button:active,
.stDownloadButton > button:active {
    outline: none;
    box-shadow: 0 0 0 3px var(--accent-tint-strong);
}

.stButton > button[kind="primary"]:focus,
.stButton > button[kind="primary"]:focus-visible,
.stButton > button[kind="primary"]:active {
    background: var(--accent-primary-strong);
    color: #ffffff;
    border-color: var(--accent-primary-strong);
}

.stButton > button:not([kind="primary"]):focus,
.stButton > button:not([kind="primary"]):active,
.stDownloadButton > button:focus,
.stDownloadButton > button:active {
    background: #ece8df;
    color: var(--text-strong);
    border-color: var(--border-strong);
}

[data-baseweb="input"] > div,
[data-baseweb="select"] > div,
textarea,
input {
    background: rgba(255, 255, 255, 0.92) !important;
    border-radius: 14px !important;
    border: 1px solid rgba(21, 32, 24, 0.16) !important;
    box-shadow: none !important;
}

[data-baseweb="input"] input,
[data-baseweb="base-input"] input,
[data-baseweb="select"] input,
[data-baseweb="select"] span,
textarea,
input {
    color: #111827 !important;
    -webkit-text-fill-color: #111827 !important;
    caret-color: #111827 !important;
}

input::placeholder,
textarea::placeholder {
    color: #6b7280 !important;
    -webkit-text-fill-color: #6b7280 !important;
    opacity: 1 !important;
}

[data-testid="stWidgetLabel"],
[data-testid="stWidgetLabel"] * {
    color: #172119 !important;
    font-weight: 700 !important;
}

[role="radiogroup"] label,
[role="radiogroup"] label *,
.stRadio label,
.stCheckbox label,
.stSelectbox label,
.stTextInput label,
.stNumberInput label,
.stFileUploader label {
    color: #172119 !important;
    font-weight: 700 !important;
}

[data-testid="stExpander"] details {
    background: var(--bg-panel);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    overflow: hidden;
    box-shadow: 0 12px 28px rgba(60, 48, 24, 0.05);
}

[data-testid="stExpander"] summary {
    background: var(--bg-subtle-2);
    border-bottom: 1px solid var(--border-soft);
    padding-top: 0.25rem;
    padding-bottom: 0.25rem;
    color: #172119 !important;
    font-weight: 800 !important;
}

[data-testid="stExpander"] summary *,
[data-testid="stExpander"] details p,
[data-testid="stExpander"] details span,
[data-testid="stExpander"] details label {
    color: #172119 !important;
}

[data-testid="stExpander"] details > div {
    background: transparent !important;
}

[data-testid="stExpander"] details [data-testid="stVerticalBlock"] {
    background: transparent !important;
}

[data-testid="stExpander"] details [data-testid="stVerticalBlockBorderWrapper"] {
    background: #ffffff !important;
    border: 1px solid var(--border-soft) !important;
    box-shadow: none !important;
}

[data-testid="stFileUploaderDropzone"] {
    background: #ffffff !important;
    border: 1.5px dashed var(--border-strong) !important;
    border-radius: 18px !important;
}

[data-testid="stFileUploaderDropzone"] *,
[data-testid="stFileUploaderDropzoneInstructions"],
[data-testid="stFileUploaderDropzoneInstructions"] * {
    color: #172119 !important;
}

[data-testid="stFileUploaderDropzone"] small,
[data-testid="stFileUploaderDropzone"] svg {
    color: #4b5563 !important;
    fill: #4b5563 !important;
}

[data-testid="stFileUploaderDropzone"] button,
[data-testid="stFileUploaderDropzone"] button:hover,
[data-testid="stFileUploaderDropzone"] button:focus,
[data-testid="stFileUploaderDropzone"] button:focus-visible,
[data-testid="stFileUploaderDropzone"] button:active {
    background: #ffffff !important;
    color: #172119 !important;
    border: 1px solid rgba(31, 64, 46, 0.14) !important;
    border-radius: 12px !important;
    box-shadow: none !important;
}

[data-testid="stFileUploaderDropzone"] button:hover {
    background: #f0ede4 !important;
    border-color: var(--border-strong) !important;
}

[data-testid="stFileUploaderDropzone"] button:focus,
[data-testid="stFileUploaderDropzone"] button:focus-visible,
[data-testid="stFileUploaderDropzone"] button:active {
    background: #ece8df !important;
    border-color: var(--border-strong) !important;
    outline: none !important;
}

[data-testid="stTabs"] [data-baseweb="tab-panel"] {
    color: #172119;
}

[data-testid="stTabs"] [data-baseweb="tab-panel"] p,
[data-testid="stTabs"] [data-baseweb="tab-panel"] span,
[data-testid="stTabs"] [data-baseweb="tab-panel"] div,
[data-testid="stTabs"] [data-baseweb="tab-panel"] label,
[data-testid="stTabs"] [data-baseweb="tab-panel"] small {
    color: #172119;
}

.stSlider label,
.stSlider [data-testid="stWidgetLabel"],
.stSlider [data-testid="stWidgetLabel"] * {
    color: #172119 !important;
    font-weight: 700 !important;
}

[data-testid="stTickBar"],
[data-testid="stTickBar"] * {
    color: #374151 !important;
}

[data-testid="stSliderTickBarMin"],
[data-testid="stSliderTickBarMax"],
[data-testid="stSliderTickBar"] {
    color: #374151 !important;
}

[data-baseweb="slider"] [role="slider"] {
    background: #1f6b4f !important;
    border-color: #154b37 !important;
}

[data-baseweb="slider"] > div > div {
    background: rgba(31, 107, 79, 0.22) !important;
}

.stCheckbox p,
.stCheckbox span,
.stRadio p,
.stRadio span,
.stSelectbox p,
.stSelectbox span,
.stNumberInput p,
.stNumberInput span {
    color: #172119 !important;
}

[data-testid="stCaptionContainer"],
[data-testid="stCaptionContainer"] * {
    color: #4b5563 !important;
}

[data-testid="stMarkdownContainer"] code {
    color: var(--accent-primary-strong) !important;
    background: var(--accent-tint) !important;
    border: 1px solid var(--border-soft);
    border-radius: 8px;
    padding: 0.1rem 0.35rem;
}

[data-testid="stTooltipHoverTarget"] svg,
[data-testid="stTooltipIcon"] svg {
    fill: #374151 !important;
}

div[data-testid="stVerticalBlockBorderWrapper"] {
    background: var(--bg-panel);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    box-shadow: var(--shadow-soft);
}

[data-testid="stDataFrame"],
.stDataFrame {
    background: #ffffff !important;
    border: 1px solid var(--border-soft) !important;
    border-radius: 16px !important;
}

[data-testid="stDataFrame"] *,
.stDataFrame * {
    color: #172119 !important;
}

[data-testid="stDataFrame"] [role="grid"],
[data-testid="stDataFrame"] [data-testid="stDataFrameResizable"] {
    background: #ffffff !important;
}

[data-testid="stCodeBlock"],
.stCode,
pre,
code[class*="language-"] {
    background: #f0ede4 !important;
    color: var(--text-strong) !important;
    border: 1px solid var(--border-soft) !important;
}

pre code {
    background: transparent !important;
    color: #172119 !important;
}

[data-testid="stAlert"] {
    background: var(--bg-panel) !important;
    color: var(--text-strong) !important;
    border: 1px solid var(--border-soft) !important;
    border-radius: 16px !important;
}

[data-testid="stAlert"] * {
    color: #172119 !important;
}

div[data-testid="stMetric"] {
    background: var(--bg-elevated);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    padding: 0.9rem 1rem;
    box-shadow: var(--shadow-card);
}

div[data-testid="stMetric"] label,
[data-testid="stMetricLabel"] {
    color: var(--text-muted);
    font-weight: 600;
}

[data-testid="stMetricValue"] {
    color: var(--text-strong);
}

[data-baseweb="tab-list"] {
    gap: 0.4rem;
    background: var(--bg-panel);
    border: 1px solid var(--border-soft);
    border-radius: 999px;
    padding: 0.35rem;
    box-shadow: 0 10px 24px rgba(60, 48, 24, 0.06);
}

button[data-baseweb="tab"] {
    min-height: 2.75rem;
    border-radius: 999px;
    color: var(--text-muted);
    font-weight: 700;
    padding: 0 1rem;
}

button[data-baseweb="tab"][aria-selected="true"] {
    background: linear-gradient(135deg, var(--accent-primary) 0%, #3c8a54 100%);
    color: #ffffff;
}

.eg-hero {
    position: relative;
    overflow: hidden;
    padding: 1.55rem 1.6rem;
    border-radius: 28px;
    border: 1px solid rgba(255, 255, 255, 0.18);
    background:
        radial-gradient(circle at top right, rgba(255, 255, 255, 0.22), transparent 24%),
        linear-gradient(135deg, #16321f 0%, #2e6f40 48%, #7ca085 100%);
    box-shadow: 0 26px 54px rgba(24, 46, 30, 0.22);
    color: #ffffff;
}

.eg-hero::after {
    content: "";
    position: absolute;
    inset: auto -15% -55% auto;
    width: 280px;
    height: 280px;
    border-radius: 50%;
    background: rgba(255, 255, 255, 0.08);
}

.eg-kicker {
    display: inline-block;
    margin-bottom: 0.55rem;
    padding: 0.3rem 0.65rem;
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.16);
    font-size: 0.78rem;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    font-weight: 800;
}

.eg-hero h1,
.eg-panel h3,
.eg-section-head h2 {
    margin: 0;
}

.eg-hero h1 {
    font-size: 2rem;
    line-height: 1.15;
    font-weight: 900;
}

.eg-hero p {
    margin: 0.55rem 0 0;
    max-width: 860px;
    color: rgba(255, 255, 255, 0.86);
    line-height: 1.65;
}

.eg-inline-meta,
.eg-badge-row {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-top: 0.95rem;
}

.eg-pill {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    padding: 0.42rem 0.72rem;
    border-radius: 999px;
    font-size: 0.78rem;
    font-weight: 800;
    border: 1px solid transparent;
}

.eg-pill.neutral {
    background: rgba(255, 255, 255, 0.12);
    color: #ffffff;
    border-color: rgba(255, 255, 255, 0.16);
}

.eg-pill.success,
.eg-panel.success {
    background: rgba(46, 125, 50, 0.10);
    color: #1f5c25;
    border-color: rgba(46, 125, 50, 0.16);
}

.eg-pill.warning,
.eg-panel.warning {
    background: rgba(197, 138, 24, 0.12);
    color: #7a5410;
    border-color: rgba(197, 138, 24, 0.18);
}

.eg-pill.danger,
.eg-panel.danger {
    background: rgba(183, 70, 59, 0.12);
    color: #8a2e26;
    border-color: rgba(183, 70, 59, 0.18);
}

.eg-pill.info,
.eg-panel.info {
    background: rgba(41, 94, 138, 0.12);
    color: #214e74;
    border-color: rgba(41, 94, 138, 0.18);
}

.eg-meta-chip {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    padding: 0.45rem 0.75rem;
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.12);
    color: rgba(255, 255, 255, 0.94);
    font-size: 0.84rem;
    font-weight: 700;
}

.eg-section-head {
    margin: 0.5rem 0 1rem;
}

.eg-section-head h2 {
    font-size: 1.35rem;
    color: var(--text-strong);
    font-weight: 900;
}

.eg-section-head p {
    margin: 0.35rem 0 0;
    color: var(--text-muted);
    line-height: 1.6;
}

.eg-panel {
    padding: 1.15rem 1.2rem;
    border-radius: var(--radius-md);
    border: 1px solid var(--border-soft);
    background: #ffffff;
    box-shadow: 0 12px 28px rgba(22, 34, 24, 0.06);
}

.eg-panel h3 {
    font-size: 1.02rem;
    color: var(--text-strong);
    font-weight: 800;
}

.eg-panel p {
    color: var(--text-muted);
    line-height: 1.6;
}

.eg-list {
    margin: 0.75rem 0 0;
    padding-left: 1.05rem;
    color: var(--text-strong);
}

.eg-list li {
    margin: 0.4rem 0;
}

.eg-stat-card {
    padding: 1rem 1.05rem;
    border-radius: 20px;
    border: 1px solid var(--border-soft);
    background: #ffffff;
    box-shadow: 0 14px 30px rgba(22, 34, 24, 0.06);
}

.eg-stat-label {
    font-size: 0.82rem;
    font-weight: 800;
    color: var(--text-muted);
    letter-spacing: 0.02em;
}

.eg-stat-value {
    margin-top: 0.45rem;
    font-size: 1.8rem;
    line-height: 1.05;
    font-weight: 900;
    color: var(--text-strong);
}

.eg-stat-note {
    margin-top: 0.45rem;
    font-size: 0.86rem;
    color: var(--text-muted);
    line-height: 1.45;
}

/* 다운로드 카드 그리드 — 본문 줄 수가 달라도 가장 큰 카드 높이에 맞춰 정렬 */
.eg-download-grid {
    display: grid;
    grid-template-columns: repeat(var(--eg-tile-cols, 3), minmax(0, 1fr));
    gap: 0.85rem;
    margin: 0.4rem 0 0.9rem;
    align-items: stretch;
}

@media (max-width: 900px) {
    .eg-download-grid {
        grid-template-columns: 1fr;
    }
}

.eg-download-tile {
    padding: 1rem 1.05rem;
    border-radius: 20px;
    border: 1px solid var(--border-soft);
    background: #ffffff;
    box-shadow: 0 14px 30px rgba(22, 34, 24, 0.06);
    /* grid 안에서 stretch 되어 3개 카드가 같은 높이로 정렬된다 */
    height: 100%;
    display: flex;
    flex-direction: column;
}

.eg-download-tile h3 {
    margin: 0;
    font-size: 1rem;
    font-weight: 900;
    color: var(--text-strong);
}

.eg-download-tile p {
    margin: 0.45rem 0 0;
    color: var(--text-muted);
    line-height: 1.55;
}

.eg-compact-note {
    margin-top: 0.6rem;
    color: var(--text-soft);
    font-size: 0.82rem;
}

/* 다운로드 카드 안에서는 note 를 하단에 고정해 카드 높이가 늘어도 정렬을 유지 */
.eg-download-tile .eg-compact-note {
    margin-top: auto;
    padding-top: 0.6rem;
}

.esg-report-card {
    background: #ffffff;
    border: 1px solid var(--border-soft);
    border-radius: 22px;
    padding: 1.45rem 1.55rem;
    font-size: 0.97rem;
    line-height: 1.85;
    color: var(--text-strong);
    word-break: keep-all;
    box-shadow: 0 14px 30px rgba(22, 34, 24, 0.06);
}

.esg-report-card.final {
    border-left: 5px solid var(--accent-primary);
}

.esg-report-tag {
    display: inline-block;
    font-size: 0.72rem;
    font-weight: 900;
    letter-spacing: 0.08em;
    padding: 0.32rem 0.65rem;
    border-radius: 999px;
    margin-bottom: 0.9rem;
    text-transform: uppercase;
}

.esg-report-tag.draft {
    color: #4b5c50 !important;
    background: #edf1ed !important;
}

.esg-report-tag.final {
    color: #1f6b4f !important;
    background: #dff0e5 !important;
}

.eg-empty {
    padding: 1.4rem 1.45rem;
    border-radius: 22px;
    border: 1px dashed rgba(31, 64, 46, 0.22);
    background: #f7faf7;
}

.eg-empty strong {
    display: block;
    font-size: 1rem;
    margin-bottom: 0.35rem;
    color: var(--text-strong);
}

.eg-empty span {
    color: var(--text-muted);
    line-height: 1.55;
}

/* 섹션 구분 컬러 배지 — 배경은 primaryColor(#2E6F40)의 옅은 톤 */
.eg-section-badge {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    padding: 0.34rem 0.72rem;
    border-radius: 999px;
    font-size: 0.76rem;
    font-weight: 800;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--accent-primary-strong);
    background: var(--accent-tint);
    border: 1px solid var(--accent-tint-strong);
}

.eg-section-badge::before {
    content: "";
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--accent-primary);
}

/* 지표 카드 그리드 — 카드 폭·간격을 균등하게 맞춘다 */
.eg-metric-grid {
    display: grid;
    grid-template-columns: repeat(var(--eg-metric-cols, 5), minmax(0, 1fr));
    gap: 0.85rem;
    margin: 0.4rem 0 1.1rem;
}

@media (max-width: 900px) {
    .eg-metric-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }
}

/* 지표 카드 (그림자·radius·padding) */
.eg-metric-card {
    padding: 1.15rem 1.2rem;
    border-radius: var(--radius-md);
    border: 1px solid var(--border-soft);
    background: var(--bg-elevated);
    box-shadow: var(--shadow-card);
    border-top: 3px solid var(--accent-primary);
    height: 100%;
    display: flex;
    flex-direction: column;
    justify-content: flex-start;
}

.eg-metric-card .eg-metric-label {
    font-size: 0.82rem;
    font-weight: 800;
    color: var(--text-muted);
    letter-spacing: 0.02em;
}

.eg-metric-card .eg-metric-value {
    margin-top: 0.4rem;
    font-size: 1.9rem;
    line-height: 1.05;
    font-weight: 900;
    color: var(--accent-primary-strong);
}

.eg-metric-card .eg-metric-note {
    margin-top: 0.4rem;
    font-size: 0.85rem;
    color: var(--text-muted);
    line-height: 1.45;
}

/* :shimmer[...] 로딩 텍스트 강조 (네이티브 미지원 버전 폴백 겸용) */
[data-testid="stMarkdownContainer"] .eg-shimmer,
.eg-shimmer {
    background: linear-gradient(
        90deg,
        var(--text-muted) 0%,
        var(--accent-primary) 40%,
        var(--accent-secondary) 55%,
        var(--text-muted) 100%
    );
    background-size: 220% auto;
    -webkit-background-clip: text;
    background-clip: text;
    -webkit-text-fill-color: transparent;
    font-weight: 800;
    animation: eg-shimmer-move 1.8s linear infinite;
}

@keyframes eg-shimmer-move {
    to { background-position: -220% center; }
}
</style>
        """,
        unsafe_allow_html=True,
    )
