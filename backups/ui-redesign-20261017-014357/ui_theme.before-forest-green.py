"""Shared presentation helpers for the SmartPanchayat Streamlit interface."""


PALETTE = {
    "brown": "#8B6F47",
    "dark_brown": "#5F4630",
    "light_brown": "#D8C3A5",
    "cream": "#F5F0E8",
    "white": "#FFFFFF",
    "text": "#2F2F2F",
    "muted": "#6B6259",
    "success": "#4F7A5A",
    "warning": "#B78628",
    "error": "#A64B3C",
}


CSS = """
<style>
:root {
  --sp-brown: #8B6F47;
  --sp-dark-brown: #5F4630;
  --sp-light-brown: #D8C3A5;
  --sp-cream: #F5F0E8;
  --sp-text: #2F2F2F;
  --sp-muted: #6B6259;
  --sp-border: #E3D9CB;
  --sp-success: #4F7A5A;
  --sp-warning: #B78628;
  --sp-error: #A64B3C;
}

.stApp { background: #FFFFFF; color: var(--sp-text); }
[data-testid="stAppViewContainer"] > .main .block-container {
  max-width: 1600px; padding-top: 1.15rem; padding-bottom: 2.25rem;
}
[data-testid="stSidebar"] {
  background: var(--sp-cream); border-right: 1px solid var(--sp-border);
}
[data-testid="stSidebar"] [data-testid="stButton"] button {
  justify-content: flex-start; min-height: 2.35rem; border-radius: 5px;
  font-size: .91rem; padding: .35rem .7rem;
}
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="primary"] {
  background: var(--sp-brown); border-color: var(--sp-brown); color: #FFFFFF;
}
.sp-brand { color: var(--sp-dark-brown); font-size: 1.23rem; font-weight: 700; margin: 0; }
.sp-brand-subtitle { color: var(--sp-muted); font-size: .77rem; margin-top: .12rem; }
.sp-appbar { display: flex; justify-content: space-between; align-items: center;
  border-bottom: 1px solid var(--sp-border); padding: .15rem 0 .8rem; margin-bottom: 1.1rem; }
.sp-user { color: var(--sp-dark-brown); font-size: .9rem; font-weight: 600; text-align: right; }
.sp-user-note { color: var(--sp-muted); font-size: .75rem; text-align: right; }
h1, h2, h3, h4 { color: var(--sp-dark-brown) !important; letter-spacing: -.015em; }
h1 { font-size: 1.85rem !important; margin-bottom: .12rem !important; }
h2 { font-size: 1.38rem !important; }
h3 { font-size: 1.12rem !important; }
p, label, [data-testid="stMarkdownContainer"] { color: var(--sp-text); }
[data-testid="stCaptionContainer"] { color: var(--sp-muted); }
[data-testid="stMetric"] {
  background: #FFFFFF; border: 1px solid var(--sp-border); border-left: 3px solid var(--sp-light-brown);
  border-radius: 5px; padding: .78rem .9rem; box-shadow: none;
}
[data-testid="stMetricLabel"] { color: var(--sp-muted) !important; font-size: .82rem; }
[data-testid="stMetricValue"] { color: var(--sp-dark-brown) !important; font-size: 1.75rem; }
div[data-testid="stDataFrame"] { border: 1px solid var(--sp-border); border-radius: 5px; }
[data-testid="stBaseButton-primary"] { background: var(--sp-brown); border-color: var(--sp-brown); color: #FFFFFF; }
[data-testid="stBaseButton-primary"]:hover { background: var(--sp-dark-brown); border-color: var(--sp-dark-brown); color: #FFFFFF; }
[data-testid="stBaseButton-secondary"] { border-color: var(--sp-brown); color: var(--sp-dark-brown); background: #FFFFFF; }
[data-testid="stTextInput"] input, [data-testid="stNumberInput"] input,
[data-testid="stDateInput"] input, [data-testid="stSelectbox"] [data-baseweb="select"] > div,
[data-testid="stTextArea"] textarea { border-color: #CFC2B1; border-radius: 4px; }
[data-testid="stExpander"] { border-color: var(--sp-border); border-radius: 5px; }
[data-testid="stAlert"] { border-radius: 5px; }
.sp-section-label { color: var(--sp-muted); font-size: .68rem; font-weight: 700;
  letter-spacing: .09em; margin: 1rem 0 .3rem; }
.sp-system-status { border-top: 1px solid var(--sp-border); margin-top: 1.2rem; padding-top: .7rem;
  color: var(--sp-muted); font-size: .78rem; }
@media (max-width: 760px) {
  [data-testid="stAppViewContainer"] > .main .block-container { padding: .8rem .85rem 1.5rem; }
  .sp-appbar { align-items: flex-start; }
  h1 { font-size: 1.55rem !important; }
}
</style>
"""


def apply_theme(streamlit):
    streamlit.markdown(CSS, unsafe_allow_html=True)


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
