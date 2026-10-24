"""Shared presentation helpers for the SmartPanchayat Streamlit interface."""

from html import escape


PALETTE = {
    "primary": "#1F352D",
    "secondary": "#426253",
    "accent": "#C7663D",
    "background": "#F0EEE4",
    "white": "#FFFDF7",
    "border": "#D7D3C3",
    "text": "#26342E",
    "muted": "#68766D",
    "selected": "#426253",
    "selected_surface": "#E7EEE5",
    "success": "#42735A",
    "warning": "#A9782B",
    "danger": "#B44A3A",
}


CSS = """
<style>
:root {
  --sp-primary: #1F352D;
  --sp-secondary: #426253;
  --sp-accent: #C7663D;
  --sp-background: #F0EEE4;
  --sp-surface: #FFFDF7;
  --sp-border: #D7D3C3;
  --sp-text: #26342E;
  --sp-muted: #68766D;
  --sp-selected: #426253;
  --sp-selected-surface: #E7EEE5;
  --sp-success: #42735A;
  --sp-warning: #A9782B;
  --sp-danger: #B44A3A;
}

.stApp, [data-testid="stAppViewContainer"] {
  background: var(--sp-background);
  color: var(--sp-text);
  font-family: Inter, "Segoe UI", Arial, sans-serif;
}
[data-testid="stAppViewContainer"] > .main .block-container {
  max-width: 1440px;
  padding: 1.35rem 2rem 2.4rem;
  background: transparent;
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
.sp-sidebar-description {
  color: #D8E2EA;
  font-size: .78rem;
  line-height: 1.45;
  margin: .25rem 0 .9rem;
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
  border-top: 3px solid var(--sp-secondary);
  border-radius: 10px;
  padding: .95rem 1rem;
  box-shadow: 0 3px 12px rgba(23, 50, 77, .055);
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

.sp-page-kicker {
  color: var(--sp-secondary);
  font-size: .72rem;
  font-weight: 750;
  letter-spacing: .12em;
  text-transform: uppercase;
  margin: .2rem 0 .35rem;
}
.sp-page-subtitle { color: var(--sp-muted); margin-top: -.15rem; }
.sp-dashboard-hero {
  background: linear-gradient(110deg, #FFFFFF 0%, #F2F7FA 100%);
  border: 1px solid var(--sp-border);
  border-left: 5px solid #247F83;
  border-radius: 12px;
  padding: 1.25rem 1.45rem;
  margin: .3rem 0 1.15rem;
  box-shadow: 0 4px 16px rgba(23, 50, 77, .05);
}
.sp-dashboard-hero h1 { margin: 0 !important; }
.sp-dashboard-hero p { color: var(--sp-muted); margin: .35rem 0 0; max-width: 760px; }
.sp-table-wrap, [data-testid="stDataFrame"] {
  border: 1px solid var(--sp-border);
  border-radius: 9px;
  overflow: hidden;
}

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

/* SmartPanchayat field atlas: archival paper, survey ink, and a small clay-red signal. */
.stApp, [data-testid="stAppViewContainer"] {
  background:
    radial-gradient(ellipse at 82% 7%, rgba(141, 157, 126, .13), transparent 28rem),
    repeating-linear-gradient(0deg, transparent 0 43px, rgba(31, 53, 45, .018) 43px 44px),
    var(--sp-background);
  color: var(--sp-text);
  font-family: "Segoe UI", Inter, sans-serif;
}
[data-testid="stAppViewContainer"] > .main .block-container {
  max-width: 1480px;
  padding: 1.15rem 2.25rem 2.8rem;
}

[data-testid="stSidebar"], [data-testid="stSidebar"] > div:first-child {
  background-color: #1F352D;
  background-image:
    repeating-radial-gradient(ellipse at 8% 9%, transparent 0 25px, rgba(233, 237, 213, .055) 26px 27px, transparent 28px 42px),
    linear-gradient(160deg, #1F352D 0%, #243C32 56%, #182B24 100%);
  border-right: 1px solid #15271F;
}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] { color: #F3F0E5; }
[data-testid="stSidebar"] .sp-brand {
  display: flex;
  align-items: center;
  gap: .62rem;
  color: #FFFDF7;
  font-family: Georgia, "Palatino Linotype", serif;
  font-size: 1.18rem;
  letter-spacing: -.025em;
  padding: .3rem 0 .16rem;
}
[data-testid="stSidebar"] .sp-brand::before {
  content: "SP";
  display: inline-grid;
  place-items: center;
  width: 2rem;
  height: 2rem;
  border: 1px solid rgba(239, 226, 190, .55);
  color: #E8C887;
  font: 700 .7rem/1 "Segoe UI", sans-serif;
  letter-spacing: .08em;
}
[data-testid="stSidebar"] .sp-brand-subtitle {
  color: #D9D8C6;
  font-size: .73rem;
  letter-spacing: .025em;
}
[data-testid="stSidebar"] .sp-sidebar-description {
  color: #C7D1C3;
  font-size: .76rem;
  line-height: 1.55;
  border-left: 1px solid rgba(232, 200, 135, .62);
  padding-left: .65rem;
  margin: .6rem 0 1.2rem;
}
[data-testid="stSidebar"] .sp-section-label {
  color: #D1B97E;
  font-size: .62rem;
  letter-spacing: .16em;
  margin-top: 1rem;
}
[data-testid="stSidebar"] [data-testid="stButton"] button {
  border-radius: 2px;
  min-height: 2.42rem;
  font-size: .88rem;
  transition: background .16s ease, transform .16s ease;
}
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="primary"] {
  background: #E8E6D9;
  border: 1px solid #E8E6D9;
  border-left: 3px solid #CB7048;
  color: #1F352D;
  font-weight: 700;
}
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="primary"] * { color: #1F352D !important; }
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="secondary"] {
  color: #DCE3D7;
  border-color: transparent;
  background: transparent;
}
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="secondary"] * { color: #DCE3D7 !important; }
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="secondary"]:hover {
  background: rgba(255,255,255,.09);
  border-color: rgba(255,255,255,.12);
  transform: translateX(2px);
}
[data-testid="stSidebar"] .sp-system-status {
  border-top: 1px solid rgba(232, 230, 217, .2);
  color: #C7D1C3;
  line-height: 1.65;
}

.sp-appbar {
  position: relative;
  align-items: center;
  background: rgba(255, 253, 247, .78);
  border: 0;
  border-bottom: 1px solid var(--sp-border);
  padding: .48rem .15rem .85rem;
  margin-bottom: 1.5rem;
}
.sp-appbar::after {
  content: "";
  position: absolute;
  bottom: -2px;
  left: 0;
  width: 5.4rem;
  height: 3px;
  background: var(--sp-accent);
}
.sp-appbar .sp-brand {
  color: var(--sp-primary);
  font: 700 1.03rem/1.2 "Segoe UI", Inter, sans-serif;
  letter-spacing: .08em;
  text-transform: uppercase;
}
.sp-appbar .sp-brand-subtitle {
  color: var(--sp-muted);
  font-size: .67rem;
  letter-spacing: .12em;
  text-transform: uppercase;
  margin-top: .3rem;
}
.sp-appbar .sp-user { color: var(--sp-primary); font-size: .84rem; }
.sp-appbar .sp-user-note { color: var(--sp-muted); font-size: .7rem; letter-spacing: .04em; }

h1, h2, h3, h4 {
  color: var(--sp-primary) !important;
  font-family: Georgia, "Palatino Linotype", serif;
  letter-spacing: -.025em;
}
h1 { font-size: 2.08rem !important; font-weight: 600 !important; }
h2 { font-size: 1.5rem !important; }
h3 { font-size: 1.2rem !important; }
.sp-page-header {
  position: relative;
  overflow: hidden;
  background: rgba(255, 253, 247, .84);
  border: 1px solid var(--sp-border);
  border-left: 4px solid var(--sp-accent);
  padding: 1.05rem 1.35rem 1.1rem;
  margin: .25rem 0 1.35rem;
}
.sp-page-header::after {
  content: "";
  position: absolute;
  top: -3.2rem;
  right: -1.5rem;
  width: 13rem;
  height: 13rem;
  border: 1px solid rgba(66, 98, 83, .13);
  border-radius: 48% 52% 58% 42%;
  box-shadow: 0 0 0 15px rgba(66, 98, 83, .035), 0 0 0 31px rgba(66, 98, 83, .03), 0 0 0 49px rgba(66, 98, 83, .022);
  pointer-events: none;
}
.sp-page-header h1 { position: relative; z-index: 1; margin: 0 !important; }
.sp-page-kicker {
  color: #9B5737;
  font-size: .65rem;
  font-weight: 800;
  letter-spacing: .17em;
  margin: 0 0 .38rem;
}
.sp-page-subtitle { position: relative; z-index: 1; color: var(--sp-muted); margin: .35rem 0 0; max-width: 790px; }
.sp-dashboard-hero {
  position: relative;
  overflow: hidden;
  background:
    repeating-radial-gradient(ellipse at 92% 28%, transparent 0 25px, rgba(245, 241, 220, .11) 26px 27px, transparent 28px 42px),
    linear-gradient(112deg, #20372E 0%, #345545 100%);
  border: 0;
  border-left: 5px solid #CB7048;
  border-radius: 2px;
  padding: 1.55rem 1.7rem;
  margin: .3rem 0 1.3rem;
  box-shadow: 0 7px 20px rgba(31, 53, 45, .12);
}
.sp-dashboard-hero h1 { color: #FFF9EB !important; margin: 0 !important; }
.sp-dashboard-hero p { color: #DCE3D7; margin: .45rem 0 0; max-width: 780px; }
.sp-dashboard-hero .sp-page-kicker { color: #E8C887; }

[data-testid="stMetric"] {
  background: rgba(255, 253, 247, .92);
  border: 1px solid var(--sp-border);
  border-top: 0;
  border-bottom: 2px solid #9AAB91;
  border-radius: 2px;
  padding: .9rem 1rem;
  box-shadow: 0 4px 10px rgba(31, 53, 45, .045);
}
[data-testid="stMetricLabel"] { color: var(--sp-muted) !important; font-size: .76rem; letter-spacing: .04em; }
[data-testid="stMetricValue"] { color: var(--sp-primary) !important; font: 600 1.85rem/1.1 Georgia, serif; }
[data-testid="stVerticalBlockBorderWrapper"] {
  background: rgba(255, 253, 247, .76);
  border-color: var(--sp-border) !important;
  border-radius: 2px !important;
  box-shadow: 0 3px 12px rgba(31, 53, 45, .035);
}
[data-testid="stDataFrame"] {
  background: var(--sp-surface);
  border: 1px solid var(--sp-border);
  border-radius: 2px;
}
[data-testid="stBaseButton-primary"] {
  background: #B95E3D;
  border-color: #B95E3D;
  border-radius: 2px;
  color: #FFFDF7;
  font-weight: 700;
  letter-spacing: .015em;
}
[data-testid="stBaseButton-primary"]:hover { background: #984A30; border-color: #984A30; color: #FFFDF7; }
[data-testid="stBaseButton-secondary"] {
  background: #FFFDF7;
  border-color: #9BA99A;
  border-radius: 2px;
  color: var(--sp-primary);
}
[data-testid="stTextInput"] input,
[data-testid="stNumberInput"] input,
[data-testid="stDateInput"] input,
[data-testid="stSelectbox"] [data-baseweb="select"] > div,
[data-testid="stTextArea"] textarea {
  border-color: #C7C8B7;
  border-radius: 2px;
  background: #FFFDF7;
}
[data-testid="stTextInput"] input:focus,
[data-testid="stNumberInput"] input:focus,
[data-testid="stDateInput"] input:focus,
[data-testid="stSelectbox"] [data-baseweb="select"] > div:focus-within,
[data-testid="stTextArea"] textarea:focus {
  border-color: #718A73;
  box-shadow: 0 0 0 2px rgba(113, 138, 115, .18);
  background: #FFFDF7;
}
[data-testid="stAlert"] { border-radius: 2px; }
[data-testid="stFileUploader"] section {
  border: 1px dashed #9CA994;
  border-radius: 2px;
  background: rgba(231, 238, 229, .34);
}
[data-testid="stExpander"] { border-color: var(--sp-border); border-radius: 2px; background: rgba(255, 253, 247, .58); }
.sp-result-summary { border-radius: 2px; border-left-color: var(--sp-accent); background: #F6EBDD; border-color: #E5CEB2; }
.sp-status-badge { border-radius: 2px; }
.sp-dashboard-section-title {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: .8rem;
  margin: .25rem 0 .65rem;
}
.sp-dashboard-section-title h3 {
  margin: 0;
  font-size: 1.35rem !important;
}
.sp-dashboard-section-title span {
  color: #9B5737;
  font-size: .62rem;
  font-weight: 800;
  letter-spacing: .13em;
  text-transform: uppercase;
}
.sp-case-table-wrap, .sp-priority-table-wrap {
  overflow: hidden;
  border: 1px solid #D7D3C3;
  border-radius: 3px;
  background: #FFFDF7;
  box-shadow: 0 4px 14px rgba(31, 53, 45, .055);
}
.sp-case-table, .sp-priority-table {
  width: 100%;
  border-collapse: collapse;
  color: #26342E;
  background: #FFFDF7;
  font-size: .82rem;
  line-height: 1.35;
}
.sp-case-table th, .sp-priority-table th {
  padding: .67rem .72rem;
  background: #E9E8DC;
  color: #59695F;
  border-bottom: 1px solid #D7D3C3;
  font-size: .64rem;
  font-weight: 800;
  letter-spacing: .09em;
  text-align: left;
  text-transform: uppercase;
  white-space: nowrap;
}
.sp-case-table td, .sp-priority-table td {
  padding: .67rem .72rem;
  border-bottom: 1px solid #EAE7DD;
  vertical-align: middle;
}
.sp-case-table tbody tr:last-child td, .sp-priority-table tbody tr:last-child td { border-bottom: 0; }
.sp-case-table tbody tr:nth-child(even) { background: #F7F5EC; }
.sp-case-table tbody tr:hover { background: #EDF1E8; }
.sp-case-table .sp-case-id {
  color: #314C3C;
  font-family: "Cascadia Code", Consolas, monospace;
  font-size: .75rem;
  font-weight: 700;
  white-space: nowrap;
}
.sp-table-chip {
  display: inline-block;
  border: 1px solid #DDD9CB;
  border-radius: 2px;
  padding: .2rem .38rem;
  color: #59665D;
  background: #F1F0E8;
  font-size: .66rem;
  font-weight: 750;
  line-height: 1.15;
  white-space: nowrap;
}
.sp-table-chip--review, .sp-table-chip--pending { color: #835B1E; background: #F7EFD9; border-color: #E8D7AF; }
.sp-table-chip--high, .sp-table-chip--critical { color: #963F30; background: #F8E7DF; border-color: #E9C8B7; }
.sp-table-chip--resolved, .sp-table-chip--low { color: #3E664D; background: #E8EFE5; border-color: #CBDAC6; }
.sp-priority-table td:first-child { font-weight: 650; }
.sp-priority-count {
  color: #314C3C;
  font: 700 1rem/1 Georgia, serif;
  text-align: right;
}
.sp-priority-meter {
  width: 5rem;
  height: .28rem;
  margin-left: .55rem;
  display: inline-block;
  vertical-align: middle;
  background: #E8E5D9;
}
.sp-priority-meter > span { display: block; height: 100%; background: #789276; }
.sp-priority-note {
  margin: .42rem 0 .7rem;
  color: #68766D;
  font-size: .72rem;
}
[data-testid="stSelectbox"] [data-baseweb="select"] > div {
  background: #FFFDF7 !important;
  border-color: #C7C8B7 !important;
  color: #26342E !important;
  border-radius: 2px !important;
}
[data-testid="stSelectbox"] [data-baseweb="select"] > div > div { color: #26342E !important; }
[data-testid="stSelectbox"] [data-baseweb="select"] [role="option"] { color: #26342E; }

@media (max-width: 760px) {
  [data-testid="stAppViewContainer"] > .main .block-container {
    padding: .8rem .85rem 1.5rem;
  }
  .sp-appbar { align-items: flex-start; }
  h1 { font-size: 1.65rem !important; }
  .sp-page-header { padding: .9rem 1rem; }
  .sp-page-header::after { right: -7rem; }
  .sp-case-table-wrap { overflow-x: auto; }
  .sp-case-table { min-width: 680px; }
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


def render_dashboard_case_table(streamlit, rows):
    def chip_tone(value, column):
        value = str(value).strip().lower()
        if column == "Risk":
            if "critical" in value:
                return "critical"
            if "high" in value:
                return "high"
            if "review" in value:
                return "review"
            if "low" in value:
                return "low"
        else:
            if "resolved" in value or "closed" in value:
                return "resolved"
            if "pending" in value or "information" in value:
                return "pending"
            if "investigation" in value or "verified" in value:
                return "review"
        return ""

    headers = ("Case ID", "Property", "Ward", "Risk", "Status", "Date")
    body = []
    for row in rows:
        cells = []
        for header in headers:
            value = escape(str(row.get(header, "Not recorded")))
            if header == "Case ID":
                cells.append(f'<td class="sp-case-id">{value}</td>')
            elif header in ("Risk", "Status"):
                tone = chip_tone(row.get(header, ""), header)
                cells.append(f'<td><span class="sp-table-chip sp-table-chip--{tone}">{value}</span></td>')
            else:
                cells.append(f"<td>{value}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    streamlit.markdown(
        '<div class="sp-case-table-wrap"><table class="sp-case-table" aria-label="Recent cases">'
        "<thead><tr>" + "".join(f"<th>{escape(header)}</th>" for header in headers)
        + "</tr></thead><tbody>" + "".join(body) + "</tbody></table></div>",
        unsafe_allow_html=True,
    )


def render_dashboard_priority_table(streamlit, rows):
    max_count = max((int(row.get("Cases", 0)) for row in rows), default=0)
    body = []
    for row in rows:
        label = escape(str(row.get("Priority", "")))
        count = int(row.get("Cases", 0))
        width = round((count / max_count) * 100) if max_count else 0
        body.append(
            "<tr>"
            f"<td>{label}<span class=\"sp-priority-meter\"><span style=\"width:{width}%\"></span></span></td>"
            f"<td class=\"sp-priority-count\">{count}</td>"
            "</tr>"
        )
    streamlit.markdown(
        '<div class="sp-priority-table-wrap"><table class="sp-priority-table" aria-label="Investigation cases by priority">'
        "<thead><tr><th>Priority</th><th>Cases</th></tr></thead>"
        "<tbody>" + "".join(body) + "</tbody></table></div>",
        unsafe_allow_html=True,
    )


def render_section_header(streamlit, title, subtitle=None):
    streamlit.subheader(title)
    if subtitle:
        streamlit.caption(subtitle)


def render_page_header(streamlit, title, subtitle=None, eyebrow=None):
    eyebrow_html = (
        f'<div class="sp-page-kicker">{escape(str(eyebrow))}</div>' if eyebrow else ""
    )
    subtitle_html = (
        f'<div class="sp-page-subtitle">{escape(str(subtitle))}</div>' if subtitle else ""
    )
    streamlit.markdown(
        '<section class="sp-page-header">'
        f"{eyebrow_html}<h1>{escape(str(title))}</h1>{subtitle_html}</section>",
        unsafe_allow_html=True,
    )


def render_dashboard_hero(streamlit, title, subtitle, eyebrow=None):
    eyebrow_html = (
        f'<div class="sp-page-kicker">{escape(str(eyebrow))}</div>' if eyebrow else ""
    )
    streamlit.markdown(
        '<section class="sp-dashboard-hero">'
        f"{eyebrow_html}<h1>{escape(str(title))}</h1>"
        f"<p>{escape(str(subtitle))}</p></section>",
        unsafe_allow_html=True,
    )


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


def render_header(streamlit, authenticated_user=None):
    user_name = escape(str((authenticated_user or {}).get("username", "Officer desk")))
    user_role = escape(str((authenticated_user or {}).get("role", "Workspace")))
    streamlit.markdown(
        f"""
        <div class="sp-appbar">
          <div>
            <div class="sp-brand">SmartPanchayat</div>
            <div class="sp-brand-subtitle">FIELD ATLAS &nbsp;·&nbsp; CIVIC LAND RECORDS</div>
          </div>
          <div>
            <div class="sp-user">{user_name}</div>
            <div class="sp-user-note">{user_role} &nbsp;·&nbsp; FIELD DESK</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
