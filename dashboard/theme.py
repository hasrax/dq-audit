"""Visual system for the dashboard (calm, colour-with-meaning, light + dark).

Palette roles (from the project's reference swatches; brightened on the
graphite dark theme, deepened on the milk-white light theme so text keeps
WCAG AA contrast):
    sage      good / passed / accepted
    mustard   medium severity / warning
    coral     high severity / failed
    lavender  AI-detected (Type 2)
    blue      rules (Type 1) and actions
Pills are soft (tinted background + coloured text) rather than solid,
which reads calmer on dark and keeps text contrast high. Light-mode text
colours were deepened on 17 Oct 2026 so each meets WCAG AA (4.5:1) on all
light surfaces and on its own pill tint (checked with Impeccable).
"""
from __future__ import annotations

import html

import plotly.graph_objects as go

PALETTES = {
    "dark": dict(BG="#16181D", SURFACE="#1F2229", SURFACE_2="#272B34", BORDER="#343A46",
                 TEXT="#EEF0F4", MUTED="#B4BBC8", FAINT="#8790A0",
                 SAGE="#8FC9B4", MUSTARD="#F1C76E", CORAL="#F39A85", LAVENDER="#B7ABF4", BLUE="#86AEF7",
                 ON_ACCENT="#0B1210", GLOW=0.08, BRAND_A="#8FC9B4", BRAND_B="#B7ABF4"),
    "light": dict(BG="#F7F6F2", SURFACE="#FFFFFF", SURFACE_2="#F1EFE9", BORDER="#E3E0D7",
                  TEXT="#1E2127", MUTED="#4E5562", FAINT="#5F6470",
                  SAGE="#286B55", MUSTARD="#825909", CORAL="#AB3928", LAVENDER="#624EB9", BLUE="#2B5CB8",
                  ON_ACCENT="#FFFFFF", GLOW=0.10, BRAND_A="#2E8B6E", BRAND_B="#5A55D2"),
}
MODE = "dark"
SEVERITY: dict = {}
SOURCE: dict = {}
TREND: dict = {}
STATUS: dict = {}


def apply(mode: str | None) -> str:
    """Switch every colour to the light or dark palette (default dark).

    Lookup dicts are updated in place so modules holding a reference to
    them see the change."""
    global MODE, BG, SURFACE, SURFACE_2, BORDER, TEXT, MUTED, FAINT
    global SAGE, MUSTARD, CORAL, LAVENDER, BLUE, ON_ACCENT, GLOW, BRAND_A, BRAND_B
    MODE = mode if mode in PALETTES else "dark"
    p = PALETTES[MODE]
    BG, SURFACE, SURFACE_2, BORDER = p["BG"], p["SURFACE"], p["SURFACE_2"], p["BORDER"]
    TEXT, MUTED, FAINT = p["TEXT"], p["MUTED"], p["FAINT"]
    SAGE, MUSTARD, CORAL, LAVENDER, BLUE = p["SAGE"], p["MUSTARD"], p["CORAL"], p["LAVENDER"], p["BLUE"]
    ON_ACCENT, GLOW = p["ON_ACCENT"], p["GLOW"]
    BRAND_A, BRAND_B = p["BRAND_A"], p["BRAND_B"]
    SEVERITY.clear()
    SEVERITY.update({"high": CORAL, "medium": MUSTARD, "low": MUTED})
    SOURCE.clear()
    SOURCE.update({"rule": ("Rule", BLUE), "llm": ("AI", LAVENDER)})   # Type 1 / 2 are thesis terms
    TREND.clear()   # only trends that need attention get a badge (a stable one on every card is noise)
    TREND.update({"new": ("New", BLUE), "worsening": ("▲ Getting worse", CORAL)})
    STATUS.clear()
    STATUS.update({"flagged": ("To review", MUTED), "accepted": ("Accepted", SAGE),
                   "rejected": ("Rejected", CORAL), "considered": ("Unsure", MUSTARD)})   # = the dialog button
    return MODE


apply("dark")


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alpha})"


def pill(text: str, color: str) -> str:
    return (f'<span class="pill" style="color:{color};background:{_rgba(color, .10)};'
            f'border-color:{_rgba(color, .32)}">{html.escape(text)}</span>')


def kpi_card(label: str, value: str, caption: str, color: str, inside_panel: bool = False) -> str:
    """Flat KPI tile: plain surface and 1 px border; colour only on the number.
    inside_panel: no border of its own (a card inside a card is visual noise)."""
    return f'''<div class="kpi{' kpi-in-panel' if inside_panel else ''}">
  <div class="kpi-label">{html.escape(label)}</div>
  <div class="kpi-value" style="color:{color}">{html.escape(value)}</div>
  <div class="kpi-caption">{html.escape(caption)}</div>
</div>'''


def style_figure(fig: go.Figure, height: int = 300) -> go.Figure:
    fig.update_layout(
        height=height, margin=dict(l=8, r=8, t=8, b=8),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=MUTED, size=13), showlegend=False,
        hoverlabel=dict(bgcolor=SURFACE_2, bordercolor=BORDER, font_color=TEXT),
    )
    fig.update_xaxes(gridcolor=BORDER, griddash="dash", zeroline=False, linecolor=BORDER)
    fig.update_yaxes(gridcolor=BORDER, griddash="dash", zeroline=False, linecolor=BORDER)
    return fig


def donut(labels: list[str], values: list[int], colors: list[str], center: str, sub: str) -> go.Figure:
    fig = go.Figure(go.Pie(
        labels=labels, values=values, hole=0.68, sort=False, direction="clockwise",
        marker=dict(colors=colors, line=dict(color=SURFACE, width=3)),
        textinfo="none", hovertemplate="%{label}: %{value}<extra></extra>",
    ))
    fig.add_annotation(text=f"<b>{center}</b>", x=0.5, y=0.55, showarrow=False,
                       font=dict(size=30, color=TEXT))
    fig.add_annotation(text=sub, x=0.5, y=0.40, showarrow=False, font=dict(size=13, color=MUTED))
    return style_figure(fig, height=250)


def css() -> str:
    return f"""
<style>
:root {{
  --bg:{BG}; --surface:{SURFACE}; --surface-2:{SURFACE_2}; --border:{BORDER};
  --text:{TEXT}; --muted:{MUTED}; --faint:{FAINT};
  --sage:{SAGE}; --mustard:{MUSTARD}; --coral:{CORAL}; --lavender:{LAVENDER}; --blue:{BLUE};
}}
.stApp {{ background: var(--bg); }}
[data-testid="stMainBlockContainer"] {{ padding-top: 2.4rem; padding-bottom: 5rem; max-width: 1320px; }}
[data-testid="stMainBlockContainer"] [data-testid="stVerticalBlock"] {{ gap: 1rem; }}   /* 16 px */
::placeholder {{ color: var(--muted) !important; opacity: 1 !important; }}
p, li {{ line-height: 1.6; }}
[data-testid="stSidebar"] .sidebar-section {{ font-size: .75rem; font-weight: 700; letter-spacing: .6px;
  text-transform: uppercase; color: var(--faint); margin: 16px 0 4px 4px; }}
[data-testid="stSidebar"] [data-testid="stPageLink"] a {{ padding: 6px 10px; border-radius: 10px; }}
.st-key-review_progress {{ margin: 2px 0 4px 0; padding: 8px 12px; border: 1px solid var(--border);
  border-radius: 12px; background: var(--surface); }}
.st-key-review_progress [data-testid="stMetricValue"] {{ font-size: 1.35rem; }}
.st-key-review_progress [data-testid="stMetricLabel"] p {{ font-size: .78rem; color: var(--muted); }}
.company-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 16px; padding: 18px 20px 14px 20px; }}
.company-card .cname {{ font-size: 1.15rem; font-weight: 700; color: var(--text); }}
.company-card .cmeta {{ font-size: .875rem; color: var(--muted); margin-top: 4px; }}
.company-card .cscore {{ font-size: 2.3rem; font-weight: 750; line-height: 1.1; margin-top: 12px; }}
.company-card .cstats {{ display:flex; gap:18px; margin-top: 10px; font-size: .88rem; color: var(--muted); }}
.company-card .cstats b {{ color: var(--text); font-size: 1rem; }}
/* chat: the conversation fills the screen down to the question bar */
.st-key-chat_scroll {{ height: calc(100vh - 290px) !important; min-height: 320px; }}
.st-key-chat_threads {{ height: calc(100vh - 230px) !important; min-height: 320px; }}
.chat-empty {{ text-align: center; padding: 12vh 8% 18px 8%; }}
.chat-empty-title {{ font-size: 1.6rem; font-weight: 700; color: var(--text); }}
.chat-empty-sub {{ margin-top: 8px; color: var(--muted); font-size: .98rem; line-height: 1.6; }}
.st-key-chat_scroll [data-testid="stPills"] {{ display: flex; justify-content: center; }}
[data-testid="stChatInput"] {{ border-radius: 14px; }}
[data-testid="stSidebar"] {{ border-right: 1px solid var(--border); }}
[data-testid="stHeader"] {{ background: transparent; }}
h1, h2, h3, h4, h5 {{ letter-spacing: 0.1px; }}

/* cards */
.card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 16px; padding: 20px 22px; }}
.card-title {{ font-size: 1.08rem; font-weight: 650; color: var(--text); margin-bottom: 8px; }}
.card-sub {{ font-size: .875rem; color: var(--muted); }}
[data-testid="stVerticalBlockBorderWrapper"] {{ border-radius: 16px; }}
div[class*="st-key-"] [data-testid="stVerticalBlockBorderWrapper"],
[data-testid="stVerticalBlock"] > [data-testid="stVerticalBlockBorderWrapper"] {{ background: var(--surface); }}

.kpi {{ background: var(--surface); border: 1px solid var(--border); border-radius: 16px; padding: 16px;
  min-height: 118px; height: 100%; }}
.kpi-label {{ font-size: .875rem; color: var(--muted); }}
.kpi-value {{ font-size: 2.2rem; font-weight: 750; line-height: 1.15; margin-top: 4px; letter-spacing: -0.5px; }}
.kpi-caption {{ font-size: .875rem; color: var(--muted); margin-top: 4px; }}
/* a KPI inside a bordered panel is not a second card */
.kpi.kpi-in-panel {{ border: none; padding: 4px 0; min-height: 0; background: transparent; }}
/* Streamlit's captions and grey text: AA contrast (17 Oct 2026 check) */
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {{ color: var(--muted) !important;
  opacity: 1 !important; }}

.pills {{ display:flex; gap:6px; flex-wrap:wrap; }}
.pill {{ display:inline-block; padding: 2px 10px; border-radius: 999px; border: 1px solid;
        font-size: .8rem; font-weight: 650; white-space: nowrap; letter-spacing: .2px; }}
.pill-outline {{ display:inline-block; padding: 2px 10px; border-radius: 999px; font-size: .8rem;
        font-weight: 600; color: var(--muted); border: 1px solid var(--border); background: transparent; }}

/* issue table (dashboard) */
.itable {{ width:100%; border-collapse: separate; border-spacing: 0 6px; font-size: .9rem; }}
.itable th {{ text-align:left; color: var(--muted); font-weight: 600; font-size: .8rem; padding: 6px 12px;
              text-transform: uppercase; letter-spacing: .4px; }}
.itable td {{ padding: 12px; background: var(--surface); border-top: 1px solid var(--border);
              border-bottom: 1px solid var(--border); color: var(--text); }}
.itable td:first-child {{ border-left: 1px solid var(--border); border-radius: 10px 0 0 10px; }}
.itable td:last-child {{ border-right: 1px solid var(--border); border-radius: 0 10px 10px 0; }}
.itable .loc {{ font-weight: 650; }}
.itable .col {{ color: var(--muted); font-weight: 500; }}
.itable .num {{ text-align: right; font-variant-numeric: tabular-nums; }}

/* issue cards (triage) */
.issue-card {{ background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 16px; margin: 4px 0 8px 0; }}
[role="dialog"] .issue-card {{ border: none; background: transparent; padding: 0; }}   /* no card inside the dialog */
.issue-head {{ display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; }}
.issue-title {{ font-size: 1.04rem; font-weight: 700; color: var(--text); }}
.issue-title .col {{ font-weight: 500; color: var(--muted); }}
.issue-desc {{ margin-top: 8px; font-size: 1rem; line-height: 1.55; color: var(--text); }}
.issue-rec {{ margin-top: 10px; font-size: .93rem; line-height: 1.5; color: var(--text);
  background: var(--surface-2); border-radius: 8px; padding: 8px 12px; }}
.issue-meta {{ margin-top: 8px; font-size: .875rem; color: var(--muted); }}
.issue-trend {{ margin-top: 8px; font-size: .88rem; line-height: 1.5; color: var(--text); }}
.issue-trend .est {{ color: var(--muted); font-style: italic; }}

/* chat */
[data-testid="stChatMessage"] h1, [data-testid="stChatMessage"] h2, [data-testid="stChatMessage"] h3 {{
  font-size: 1.02rem !important; margin: 10px 0 4px 0 !important; }}
[class*="st-key-thread_"] button {{ justify-content: flex-start; text-align: left; }}
[class*="st-key-thread_"] button p {{ text-align: left; }}

/* compact issue rows (triage list) */
.issue-row {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 12px 16px; }}
.issue-row .top {{ display:flex; justify-content:space-between; align-items:center; gap:10px; flex-wrap:wrap; }}
.issue-row .loc {{ font-weight: 700; color: var(--text); }}
.issue-row .loc .col {{ font-weight: 500; color: var(--muted); }}
.issue-row .sum {{ margin-top: 5px; color: var(--text); font-size: .95rem; line-height: 1.5;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }}
.issue-row .dot {{ display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 9px;
  vertical-align: middle; }}
.group-card {{ box-shadow: 4px 4px 0 -1px var(--surface-2), 4px 4px 0 0 var(--border); }}
.issue-row .meta {{ margin-top: 4px; color: var(--faint); font-size: .875rem; }}
.section-label {{ font-size: .8rem; text-transform: uppercase; letter-spacing: .5px; color: var(--faint);
  margin: 14px 0 4px 0; font-weight: 650; }}
.decision-gap {{ height: 12px; }}   /* issue dialog: Accept / Reject / Unsure line up with the note box */

.rule-card {{ background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 12px 16px; margin: 0 0 4px 0; }}
.rule-what {{ font-size: 1rem; font-weight: 650; line-height: 1.45; margin-top: 6px; color: var(--text); }}
.rule-why {{ margin-top: 4px; font-size: .93rem; line-height: 1.5; color: var(--muted); }}
.rule-ev {{ margin-top: 8px; font-size: .875rem; color: var(--faint); }}

.context-line {{ font-size: .92rem; color: var(--muted); margin: -4px 0 10px 0; }}
.context-line b {{ color: var(--text); }}
.page-title {{ font-size: 1.9rem; font-weight: 700; color: var(--text); margin: 0 0 4px 0; letter-spacing: -.2px; }}
.page-sub {{ font-size: .95rem; color: var(--muted); margin: -2px 0 6px 0; }}

.hero {{ text-align:center; padding: 8px 0 6px 0; }}
.hero-num {{ font-size: 4.2rem; font-weight: 800; line-height: 1; }}
.hero-max {{ font-size: 1.4rem; font-weight: 600; color: var(--faint); }}
.hero-label {{ font-size: .9rem; color: var(--muted); letter-spacing: .4px; text-transform: uppercase; }}
.meter {{ margin: 10px 0; }}
.meter-top {{ display:flex; justify-content:space-between; font-size:.9rem; margin-bottom:4px; }}
.meter-label {{ font-weight:600; }}
.meter-val {{ font-weight:800; }}
.meter-track {{ height:10px; border-radius:999px; background: var(--surface-2); overflow:hidden; }}
.meter-fill {{ height:100%; border-radius:999px; }}

/* sign-in and study-code pages: one centred column */
.st-key-auth_wrap {{ max-width: 430px; margin: 4vh auto 0 auto; }}
.auth-brand {{ text-align: center; margin-bottom: 14px; }}
.auth-brand .mark {{ width: 64px; height: 64px; margin: 0 auto 14px auto;
  filter: drop-shadow(0 10px 24px {_rgba(LAVENDER, .35)}); }}
.auth-brand .mark svg {{ width: 64px; height: 64px; display: block; }}
.auth-brand .name {{ font-size: 2.1rem; font-weight: 750; letter-spacing: -.6px; color: var(--text); line-height: 1.1; }}
.auth-brand .name span {{ background: linear-gradient(135deg, {BRAND_A}, {BRAND_B});
  -webkit-background-clip: text; background-clip: text; color: transparent; }}
.auth-brand .tag {{ font-size: .95rem; color: var(--muted); margin-top: 6px; }}
.st-key-auth_card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 20px;
  padding: 26px 26px 22px 26px; box-shadow: 0 18px 50px {_rgba(TEXT, .07)}; }}
.st-key-auth_card [data-testid="stForm"] {{ border: none; padding: 0; }}
.auth-title {{ font-size: 1.35rem; font-weight: 700; color: var(--text); }}
.auth-sub {{ font-size: .93rem; color: var(--muted); margin-top: 2px; }}
.auth-badge {{ display: inline-block; font-size: .72rem; font-weight: 700; letter-spacing: .6px;
  text-transform: uppercase; color: {LAVENDER}; background: {_rgba(LAVENDER, .14)};
  border-radius: 999px; padding: 3px 10px; margin-bottom: 8px; }}
.st-key-auth_switch {{ align-items: center; gap: 0 !important; }}
.st-key-auth_switch [data-testid="stElementContainer"] {{ width: auto !important; }}
.st-key-auth_switch p {{ color: var(--muted); font-size: .92rem; margin: 0; text-align: center; }}
.st-key-auth_switch button p {{ color: {SAGE}; font-weight: 650; }}
/* study pages on phones: the three pages as buttons on top */
.st-key-row_topnav [data-testid="stLayoutWrapper"], .st-key-row_topnav [class*="st-key-topnav_"],
.st-key-row_topnav [data-testid="stElementContainer"], .st-key-row_topnav [data-testid="stPageLink"],
.st-key-row_topnav [data-testid="stPageLink"] a {{ width: 100% !important; }}
.st-key-row_topnav [data-testid="stPageLink"] a {{ justify-content: center; border: 1px solid var(--border);
  border-radius: 12px; background: var(--surface); padding: 6px 4px; }}
.st-key-topnav_on [data-testid="stPageLink"] a {{ background: {_rgba(SAGE, .14)}; border-color: {_rgba(SAGE, .5)}; }}
/* the study task card */
.st-key-study_panel .sidebar-section {{ font-size: .72rem; font-weight: 700; letter-spacing: .6px;
  text-transform: uppercase; color: var(--faint); margin: 0 0 2px 0; }}

/* the multi-select bar stays in view while scrolling the issue list */
.st-key-selection_bar_active {{ position: fixed; bottom: 22px; left: calc(50% + 95px); transform: translateX(-50%);
  width: min(860px, 90vw); z-index: 999; background: var(--surface); box-shadow: 0 10px 30px {_rgba(TEXT, .18)}; }}
@media (max-width: 900px) {{ .st-key-selection_bar_active {{ left: 50%; }} }}

/* small screens (phones) */
@media (max-width: 640px) {{
  [data-testid="stMainBlockContainer"] {{ padding-top: 3.6rem; }}   /* below the fixed logo bar */
  .st-key-study_panel {{ gap: .55rem !important; }}
  .st-key-study_panel p {{ font-size: .95rem; line-height: 1.45; }}
  .st-key-auth_wrap {{ margin-top: 0; }}
  .st-key-auth_card {{ padding: 20px 18px 16px 18px; }}
  .page-title {{ font-size: 1.5rem; }}
  /* containers whose key starts with "row_" keep their columns side by side */
  [class*="st-key-row_"] [data-testid="stHorizontalBlock"] {{ flex-wrap: nowrap !important; gap: .5rem; }}
  [class*="st-key-row_"] [data-testid="stColumn"] {{ min-width: 0 !important; flex: 1 1 0 !important;
    width: auto !important; }}
  /* triage rows: text on top, the three actions in one line underneath */
  [class*="st-key-irow_"] [data-testid="stHorizontalBlock"],
  [class*="st-key-wrap_"] [data-testid="stHorizontalBlock"] {{ flex-wrap: wrap !important; gap: .4rem; }}
  [class*="st-key-irow_"] [data-testid="stColumn"],
  [class*="st-key-wrap_"] [data-testid="stColumn"] {{ min-width: 0 !important; flex: 1 1 0 !important;
    width: auto !important; }}
  [class*="st-key-irow_"] [data-testid="stColumn"]:first-child,
  [class*="st-key-wrap_"] [data-testid="stColumn"]:first-child {{ flex: 1 1 100% !important; }}
  .issue-row .sum {{ white-space: normal; }}
  [class*="st-key-irow_"] .issue-row {{ margin-bottom: 22px; }}   /* clear of the actions below */
  /* chat: no tall thread list above the conversation */
  .st-key-chat_threads,
  [data-testid="stLayoutWrapper"]:has(> .st-key-chat_threads) {{ height: auto !important; max-height: 132px;
    min-height: 0; }}
  .st-key-chat_scroll {{ height: 62vh !important; min-height: 300px; }}
}}

/* primary buttons: text colour that reads on the sage primary in both modes */
button[data-testid^="stBaseButton-primary"]:not(:disabled),
button[data-testid^="stBaseButton-primary"]:not(:disabled) p {{ color: {ON_ACCENT} !important; font-weight: 650; }}
/* a disabled button stays readable (the caption above it says what is missing) */
button[data-testid^="stBaseButton"]:disabled, button[data-testid^="stBaseButton"]:disabled p {{
  color: var(--muted) !important; opacity: 1 !important; }}
</style>
"""
