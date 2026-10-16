"""Shared presentation helpers for the SmartPanchayat Streamlit interface."""

from html import escape


PALETTE = {
    "primary": "#17324D",
    "secondary": "#285A7A",
    "accent": "#4F7C95",
    "background": "#F5F7F9",
    "white": "#FFFFFF",
    "border": "#D9E0E6",
    "text": "#33495C",
    "muted": "#5E7180",
    "selected": "#3B7898",
    "selected_surface": "#E8F2F7",
    "success": "#3F7652",
    "warning": "#B7791F",
    "danger": "#A94442",
}


CSS = """
<style>
:root {
  --sp-primary: #17324D;
  --sp-secondary: #285A7A;
  --sp-accent: #4F7C95;
  --sp-background: #F5F7F9;
  --sp-surface: #FFFFFF;
  --sp-border: #D9E0E6;
  --sp-text: #33495C;
  --sp-muted: #5E7180;
  --sp-selected: #3B7898;
  --sp-selected-surface: #E8F2F7;
  --sp-success: #3F7652;
  --sp-warning: #B7791F;
  --sp-danger: #A94442;
}

.stApp, [data-testid="stAppViewContainer"] {
  background: var(--sp-background);
  color: var(--sp-text);
  font-family: Inter, "Segoe UI", Arial, sans-serif;
}
[data-testid="stAppViewContainer"] > .main .block-container {
  max-width: 1440px;
  padding: 1.2rem 1.5rem 2.1rem;
  background: var(--sp-surface);
  border: 1px solid var(--sp-border);
  border-radius: 8px;
  box-shadow: 0 1px 3px rgba(23, 50, 77, .05);
}

/* Government-style navy navigation with a clear active state. */
[data-testid="stSidebar"], [data-testid="stSidebar"] > div:first-child {
  background: var(--sp-primary);
  border-right: 1px solid #102638;
}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] {
  color: #FFFFFF;
}
[data-testid="stSidebar"] [data-testid="stButton"] button {
  justify-content: flex-start;
  min-height: 2.25rem;
  border-radius: 5px;
  font-size: .9rem;
  padding: .32rem .68rem;
  transition: background-color .12s ease;
}
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="primary"] {
  background: var(--sp-selected);
  border-color: #6C9BB4;
  border-left: 3px solid #C4E5F2;
  color: #FFFFFF;
  font-weight: 650;
}
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="primary"] * {
  color: #FFFFFF !important;
}
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="secondary"] {
  background: transparent;
  border-color: transparent;
  color: #CFDCE5;
}
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="secondary"] * {
  color: #CFDCE5 !important;
}
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="secondary"]:hover {
  background: var(--sp-secondary);
  border-color: var(--sp-secondary);
  color: #FFFFFF;
}
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="secondary"]:hover * {
  color: #FFFFFF !important;
}
[data-testid="stSidebar"] .sp-brand {
  color: #FFFFFF;
  font-size: 1.08rem;
  letter-spacing: .04em;
}
[data-testid="stSidebar"] .sp-brand-subtitle,
[data-testid="stSidebar"] .sp-section-label,
[data-testid="stSidebar"] .sp-system-status {
  color: #C6D2DC;
}

.sp-brand {
  color: var(--sp-primary);
  font-size: 1.23rem;
  font-weight: 700;
  margin: 0;
}
.sp-brand-subtitle {
  color: var(--sp-muted);
  font-size: .77rem;
  margin-top: .12rem;
}
.sp-appbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: var(--sp-surface);
  border-bottom: 1px solid var(--sp-border);
  padding: .15rem 0 .8rem;
  margin-bottom: 1.1rem;
}
.sp-user {
  color: var(--sp-primary);
  font-size: .9rem;
  font-weight: 600;
  text-align: right;
}
.sp-user-note {
  color: var(--sp-muted);
  font-size: .75rem;
  text-align: right;
}

h1, h2, h3, h4 {
  color: var(--sp-primary) !important;
  letter-spacing: -.015em;
}
h1 { font-size: 1.85rem !important; margin-bottom: .12rem !important; }
h2 { font-size: 1.38rem !important; }
h3 { font-size: 1.12rem !important; }
p, label, .main [data-testid="stMarkdownContainer"] { color: var(--sp-text); }
[data-testid="stCaptionContainer"] { color: var(--sp-muted); }
::selection,
::-moz-selection,
input::selection,
textarea::selection {
  background: var(--sp-secondary) !important;
  color: #FFFFFF !important;
}

[data-testid="stMetric"] {
  background: var(--sp-surface);
  border: 1px solid var(--sp-border);
  border-left: 3px solid var(--sp-secondary);
  border-radius: 6px;
  padding: .76rem .9rem;
  box-shadow: 0 1px 2px rgba(23, 50, 77, .04);
}
[data-testid="stMetricLabel"] { color: var(--sp-muted) !important; font-size: .82rem; }
[data-testid="stMetricValue"] {
  color: var(--sp-primary) !important;
  font-size: 1.75rem;
  font-weight: 700;
}
div[data-testid="stDataFrame"] {
  background: var(--sp-surface);
  border: 1px solid var(--sp-border);
  border-radius: 6px;
}

[data-testid="stBaseButton-primary"] {
  background: var(--sp-primary);
  border-color: var(--sp-primary);
  color: #FFFFFF;
}
[data-testid="stBaseButton-primary"]:hover {
  background: var(--sp-secondary);
  border-color: var(--sp-secondary);
  color: #FFFFFF;
}
[data-testid="stBaseButton-secondary"] {
  border-color: var(--sp-primary);
  color: var(--sp-primary);
  background: #FFFFFF;
}
[data-testid="stBaseButton-primary"]:focus-visible,
[data-testid="stBaseButton-secondary"]:focus-visible {
  outline: 2px solid var(--sp-accent);
  outline-offset: 2px;
}
[data-testid="stTextInput"] input,
[data-testid="stNumberInput"] input,
[data-testid="stDateInput"] input,
[data-testid="stSelectbox"] [data-baseweb="select"] > div,
[data-testid="stTextArea"] textarea {
  border-color: var(--sp-border);
  border-radius: 5px;
}
[data-testid="stTextInput"] input:focus,
[data-testid="stNumberInput"] input:focus,
[data-testid="stDateInput"] input:focus,
[data-testid="stSelectbox"] [data-baseweb="select"] > div:focus-within,
[data-testid="stTextArea"] textarea:focus {
  border-color: var(--sp-accent);
  box-shadow: 0 0 0 2px rgba(79, 124, 149, .18);
  background: var(--sp-selected-surface);
}
[role="option"][aria-selected="true"] {
  background: var(--sp-secondary) !important;
  color: #FFFFFF !important;
}
[data-baseweb="tag"] {
  background: var(--sp-secondary) !important;
  color: #FFFFFF !important;
}
[data-baseweb="tag"] * {
  color: #FFFFFF !important;
}
[data-testid="stExpander"] { border-color: var(--sp-border); border-radius: 6px; }
[data-testid="stAlert"] { border-radius: 6px; }

.sp-section-label {
  color: var(--sp-muted);
  font-size: .68rem;
  font-weight: 700;
  letter-spacing: .09em;
  margin: .78rem 0 .22rem;
}
.sp-system-status {
  border-top: 1px solid rgba(255,255,255,.2);
  margin-top: 1.2rem;
  padding-top: .7rem;
  color: #C6D2DC;
  font-size: .78rem;
}
.sp-image-label {
  color: var(--sp-muted);
  font-size: .68rem;
  font-weight: 700;
  letter-spacing: .1em;
  margin: .2rem 0 .35rem;
}
.sp-result-summary {
  background: #FFF8E8;
  border: 1px solid #E8D8B5;
  border-left: 4px solid var(--sp-warning);
  border-radius: 6px;
  padding: .85rem 1rem;
  margin: .65rem 0 1rem;
}
.sp-result-title { color: var(--sp-primary); font-size: 1rem; font-weight: 700; }
.sp-result-status { color: #76520F; font-size: .78rem; font-weight: 700; letter-spacing: .04em; margin-top: .18rem; }
.sp-result-note { color: var(--sp-text); font-size: .88rem; margin-top: .3rem; }
.sp-status-badge {
  display: inline-block; border-radius: 4px; padding: .2rem .48rem;
  font-size: .72rem; font-weight: 700; letter-spacing: .025em;
  border: 1px solid var(--sp-border); background: #F0F3F6; color: var(--sp-primary);
}
.sp-status-badge--pass { background: #EDF5EF; border-color: #C9DECE; color: #315E40; }
.sp-status-badge--warning { background: #FFF6E4; border-color: #E8D8B5; color: #76520F; }
.sp-status-badge--danger { background: #F9EEEE; border-color: #E7C9C8; color: #812F2E; }

@media (max-width: 760px) {
  [data-testid="stAppViewContainer"] > .main .block-container {
    padding: .8rem .85rem 1.5rem;
    border-radius: 0;
  }
  .sp-appbar { align-items: flex-start; }
  h1 { font-size: 1.55rem !important; }
}
</style>
"""


def apply_theme(streamlit):
    streamlit.markdown(CSS, unsafe_allow_html=True)


def render_status_badge(streamlit, label, status, tone="neutral"):
    tone = tone if tone in {"pass", "warning", "danger"} else "neutral"
    streamlit.markdown(
        f'<span class="sp-status-badge sp-status-badge--{tone}">'
        f"{escape(str(label))}: {escape(str(status))}</span>",
        unsafe_allow_html=True,
    )


def render_metric_card(streamlit, label, value, help_text=None):
    streamlit.metric(label, value, help=help_text)


def render_section_header(streamlit, title, subtitle=None):
    streamlit.subheader(title)
    if subtitle:
        streamlit.caption(subtitle)


def render_result_summary(streamlit, title, status, note):
    streamlit.markdown(
        '<div class="sp-result-summary">'
        f'<div class="sp-result-title">{escape(str(title))}</div>'
        f'<div class="sp-result-status">{escape(str(status))}</div>'
        f'<div class="sp-result-note">{escape(str(note))}</div>'
        '</div>',
        unsafe_allow_html=True,
    )


def render_technical_details(streamlit, title="Technical Details", expanded=False):
    return streamlit.expander(title, expanded=expanded)


def render_header(streamlit):
    streamlit.markdown(
        """
        <div class="sp-appbar">
          <div>
            <div class="sp-brand">SmartPanchayat</div>
            <div class="sp-brand-subtitle">Construction Monitoring &amp; Investigation System</div>
          </div>
          <div>
            <div class="sp-user">Officer workspace</div>
            <div class="sp-user-note">Local prototype</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
