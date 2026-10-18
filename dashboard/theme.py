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

PALETTES = {   # style sheet board of the design canvas (18 Oct 2026); dark is the default
    "dark": dict(BG="#121416", SURFACE="#1B1E21", SURFACE_2="#22262B", BORDER="#2D3136",
                 TEXT="#ECEBE8", MUTED="#B4B1AB", FAINT="#9C9993",
                 SAGE="#84CFA8", MUSTARD="#E6BC63", CORAL="#F2917C", LAVENDER="#B7A6F5", BLUE="#8DB2F2",
                 PRIMARY="#7FCCA4", ON_ACCENT="#10251C", LINK="#7FCCA4", GLOW=0.08,
                 BRAND_A="#7FCCA4", BRAND_B="#7FCCA4"),
    # light: five accents a shade darker than the board so each passes 4.5:1 on every light surface
    "light": dict(BG="#F7F6F3", SURFACE="#FFFFFF", SURFACE_2="#F2F0EB", BORDER="#E4E2DC",
                  TEXT="#1C1B19", MUTED="#5C5A55", FAINT="#65625D",
                  SAGE="#2B6F4D", MUSTARD="#835C00", CORAL="#AA3E2C", LAVENDER="#694DC5", BLUE="#2F5FB3",
                  PRIMARY="#7FCCA4", ON_ACCENT="#10251C", LINK="#2F6F55", GLOW=0.10,
                  BRAND_A="#2F6F55", BRAND_B="#2F6F55"),
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
    global SAGE, MUSTARD, CORAL, LAVENDER, BLUE, ON_ACCENT, GLOW, BRAND_A, BRAND_B, PRIMARY, LINK
    MODE = mode if mode in PALETTES else "dark"
    p = PALETTES[MODE]
    BG, SURFACE, SURFACE_2, BORDER = p["BG"], p["SURFACE"], p["SURFACE_2"], p["BORDER"]
    TEXT, MUTED, FAINT = p["TEXT"], p["MUTED"], p["FAINT"]
    SAGE, MUSTARD, CORAL, LAVENDER, BLUE = p["SAGE"], p["MUSTARD"], p["CORAL"], p["LAVENDER"], p["BLUE"]
    ON_ACCENT, GLOW, PRIMARY, LINK = p["ON_ACCENT"], p["GLOW"], p["PRIMARY"], p["LINK"]
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


def pill(text: str, color: str, title: str = "") -> str:
    """A soft label; `title` is a one-line explanation shown on hover."""
    tip = f' title="{html.escape(title)}"' if title else ""
    return (f'<span class="pill" style="color:{color};background:{_rgba(color, .10)};'
            f'border-color:{_rgba(color, .32)}"{tip}>{html.escape(text)}</span>')


def kpi_card(label: str, value: str, caption: str, color: str, inside_panel: bool = False, suffix: str = "") -> str:
    """Flat KPI tile: plain surface and 1 px border; colour only on the number.
    inside_panel: no border of its own (a card inside a card is visual noise).
    suffix: smaller text after the number, e.g. "/ 100" (the number never wraps)."""
    small = f"<small>{html.escape(suffix)}</small>" if suffix else ""
    return f'''<div class="kpi{' kpi-in-panel' if inside_panel else ''}">
  <div class="kpi-label">{html.escape(label)}</div>
  <div class="kpi-value" style="color:{color}">{html.escape(value)}{small}</div>
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
  --primary:{PRIMARY}; --on-primary:{ON_ACCENT}; --link:{LINK};
}}
.stApp {{ background: var(--bg); }}
[data-testid="stMainBlockContainer"] {{ padding: 2rem 2.5rem 5rem 2.5rem; max-width: 1320px; }}
[data-testid="stMainBlockContainer"] [data-testid="stVerticalBlock"] {{ gap: 1rem; }}   /* 16 px */
::placeholder {{ color: var(--muted) !important; opacity: 1 !important; }}
p, li {{ line-height: 1.6; }}
[data-testid="stSidebar"] .sidebar-section {{ font-size: .75rem; font-weight: 700; letter-spacing: .6px;
  text-transform: uppercase; color: var(--faint); margin: 16px 0 4px 4px; }}
[data-testid="stSidebar"] [data-testid="stPageLink"] a {{ padding: 8px 12px; border-radius: 8px; }}
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
/* more specific than Streamlit's own fixed height, which is also !important */
div.stVerticalBlock.st-key-chat_scroll, div[data-testid="stLayoutWrapper"]:has(> .st-key-chat_scroll) {{
  height: calc(100vh - 410px) !important; min-height: 320px !important; max-height: none !important;
  flex: 0 0 auto !important; }}   /* the wrapper sizes itself by flex-basis */
.discussing {{ font-size: .95rem; color: var(--text); line-height: 1.5; }}
.discussing .label {{ color: var(--muted); font-weight: 650; }}
/* answers stay readable: about 70 characters per line */
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] {{ max-width: 70ch; }}
[data-testid="stChatMessage"] table {{ font-size: .875rem; }}
.chat-empty {{ text-align: center; padding: 12vh 8% 18px 8%; }}
.chat-empty-title {{ font-size: 1.6rem; font-weight: 700; color: var(--text); }}
.chat-empty-sub {{ margin-top: 8px; color: var(--muted); font-size: .98rem; line-height: 1.6; }}
.st-key-chat_scroll [data-testid="stPills"] {{ display: flex; justify-content: center; }}
[data-testid="stChatInput"] {{ border-radius: 14px; }}
[data-testid="stSidebar"] {{ border-right: 1px solid var(--border); }}
[data-testid="stSidebarUserContent"] {{ padding-bottom: 24px; }}
/* the theme is chosen in Settings > Appearance (it reloads the page, so every colour follows).
   Streamlit's own switch in the menu does not re-run the app, which left the page half light, half dark */
[data-testid="stThemeSwitcher"], [data-testid="stThemeSwitcher"] + [data-testid="stMainMenuDivider"] {{
  display: none !important; }}
[data-testid="stHeader"] {{ background: transparent; }}
h1, h2, h3, h4, h5 {{ letter-spacing: 0.1px; }}

/* cards */
.card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px 24px; }}
.card-title {{ font-size: 1.08rem; font-weight: 650; color: var(--text); margin-bottom: 8px; }}
.card-sub {{ font-size: .875rem; color: var(--muted); }}
[data-testid="stVerticalBlockBorderWrapper"] {{ border-radius: 12px; }}
[role="dialog"] {{ border-radius: 16px !important; }}
div[class*="st-key-"] [data-testid="stVerticalBlockBorderWrapper"],
[data-testid="stVerticalBlock"] > [data-testid="stVerticalBlockBorderWrapper"] {{ background: var(--surface); }}

.kpi {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 16px 20px;
  min-height: 118px; height: 100%; margin-bottom: 16px; }}   /* 16 px between the KPI row and the panels */
.kpi-label {{ font-size: .875rem; color: var(--muted); }}
.kpi-value {{ font-size: clamp(1.6rem, 2.6vw, 2.4rem); font-weight: 700; line-height: 1.15; margin-top: 4px;
  letter-spacing: -0.5px; white-space: nowrap; font-variant-numeric: tabular-nums; }}
.kpi-value small {{ font-size: .5em; font-weight: 600; color: var(--muted); margin-left: 2px; }}
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
/* Triage: the whole row (tick, text, Accept / Reject / Open) is one card; 10 px between cards */
[class*="st-key-irow_"], [class*="st-key-grp_"] {{ background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 8px 12px 12px 12px; margin-bottom: -6px; }}   /* 16 px block gap - 6 = 10 px */
[class*="st-key-irow_"] .issue-row, [class*="st-key-grp_"] .issue-row {{ border: none; background: transparent;
  padding: 4px 4px; }}
[class*="st-key-grp_"] {{ box-shadow: 4px 4px 0 -1px var(--surface-2), 4px 4px 0 0 var(--border); }}   /* a stack */
[class*="st-key-grp_"] [data-testid="stExpander"] details {{ border: none; border-top: 1px solid var(--border);
  border-radius: 0; }}
/* the issues inside an opened group are rows of that card, not cards of their own */
[class*="st-key-grp_"] [class*="st-key-irow_"] {{ border: none; border-top: 1px solid var(--border); border-radius: 0;
  background: transparent; margin: 0; padding: 8px 0; box-shadow: none; }}
.issue-row .meta {{ margin-top: 4px; color: var(--faint); font-size: .875rem; }}
.section-label {{ font-size: .8rem; text-transform: uppercase; letter-spacing: .5px; color: var(--faint);
  margin: 14px 0 4px 0; font-weight: 650; }}
.decision-gap {{ height: 12px; }}
.hint {{ text-decoration: underline dotted; text-underline-offset: 3px; cursor: help; }}   /* has a tooltip */
.guide-step {{ font-size: .8rem; font-weight: 700; letter-spacing: .5px; text-transform: uppercase;
  color: var(--sage); margin-bottom: 4px; }}
.guide-text {{ font-size: 1rem; line-height: 1.55; color: var(--text); max-width: 70ch; }}
/* compact user card at the bottom of the sidebar */
.st-key-account_card {{ padding: 8px 12px !important; gap: 0 !important; }}
.st-key-account_card p {{ margin: 0; line-height: 1.35; }}   /* issue dialog: Accept / Reject / Unsure line up with the note box */

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

/* sign-in (board 9): a dark-green hero on the left, the form on the right; phones: form first */
.st-key-auth_page {{ max-width: 1200px; margin: 1vh auto 0 auto; }}
.auth-hero {{ box-sizing: border-box; min-height: 500px; padding: 32px clamp(20px, 3vw, 40px); border-radius: 16px;
  background-color: #0F1E18; background-image: radial-gradient(rgba(127, 204, 164, .10) 1px, transparent 1px);
  background-size: 22px 22px; color: #ECEBE8; display: flex; flex-direction: column; justify-content: space-between;
  gap: 40px; }}
.auth-logo, .auth-mlogo {{ display: flex; align-items: center; gap: 12px; font-size: 1.35rem; font-weight: 700;
  letter-spacing: -.01em; }}
.auth-logo .mark svg, .auth-mlogo .mark svg {{ width: 38px; height: 38px; display: block; }}
.auth-mlogo {{ display: none; color: var(--text); margin-bottom: 16px; }}
.auth-copy {{ display: flex; flex-direction: column; gap: 18px; max-width: 460px; }}
.auth-copy .auth-h1 {{ margin: 0; padding: 0; font-size: clamp(1.8rem, 2.8vw, 2.5rem); font-weight: 700; line-height: 1.08;
  letter-spacing: -.02em; color: #ECEBE8 !important; }}
.auth-copy .auth-h1 span {{ color: #7FCCA4 !important; }}
.auth-copy p {{ margin: 0; font-size: 1.02rem; line-height: 1.5; color: #B9C9C0; }}
.auth-example {{ display: flex; flex-direction: column; gap: 10px; max-width: 440px; padding: 18px 20px;
  background: #1B1E21; border: 1px solid #2D3136; border-radius: 14px; box-shadow: 0 18px 40px rgba(0, 0, 0, .35); }}
.auth-example .top {{ display: flex; align-items: center; gap: 8px; font-size: .95rem; }}
.auth-example .sev {{ padding: 1px 9px; border-radius: 999px; font-size: .78rem; font-weight: 600; background: #3A2320;
  color: #F2917C; }}
.auth-example .ex {{ margin-left: auto; font-size: .78rem; color: #8C8984; }}
.auth-example .what {{ font-size: .95rem; color: #CFCDC8; }}
.auth-example .todo {{ padding: 8px 10px; border-radius: 8px; background: #1A2A22; font-size: .9rem; color: #CFE7DA; }}
.auth-example .todo b {{ color: #9FDABB; }}
.auth-chips {{ margin: 0; padding: 0; list-style: none; display: flex; flex-wrap: wrap; gap: 8px 10px; }}
.auth-chips li {{ margin: 0; padding: 4px 12px; border-radius: 999px; border: 1px solid #2F5A45; font-size: .9rem;
  color: #CFE7DA; }}
.st-key-auth_card {{ max-width: 460px; margin: 0 auto; width: 100%; }}
.st-key-auth_card [data-testid="stForm"] {{ border: none; padding: 0; }}
.auth-title {{ font-size: 1.75rem; font-weight: 700; color: var(--text); letter-spacing: -.01em; margin-bottom: 4px; }}
.auth-divider {{ display: flex; align-items: center; gap: 12px; margin: 12px 0 4px 0; color: var(--faint);
  font-size: .82rem; }}
.auth-divider::before, .auth-divider::after {{ content: ""; flex: 1; height: 1px; background: var(--border); }}
@media (max-width: 760px) {{
  .st-key-auth_page [data-testid="stHorizontalBlock"]:has(.auth-hero) {{ flex-direction: column-reverse !important; }}
  .auth-hero {{ min-height: 0; gap: 24px; }}
  .auth-example {{ display: none; }}
  .auth-mlogo {{ display: flex; }}
}}
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
  .page-title {{ font-size: 1.5rem; }}
  /* the sidebar's review counts and user card stay on one line */
  .st-key-review_progress [data-testid="stHorizontalBlock"],
  .st-key-account_card [data-testid="stHorizontalBlock"] {{ flex-wrap: nowrap !important; gap: .5rem; }}
  .st-key-review_progress [data-testid="stColumn"],
  .st-key-account_card [data-testid="stColumn"] {{ min-width: 0 !important; width: auto !important; }}
  .st-key-review_progress [data-testid="stColumn"] {{ flex: 1 1 0 !important; }}
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
  /* Ask fits the screen, so Streamlit's scroll-to-bottom does not push the header under the logo bar */
  div.stVerticalBlock.st-key-chat_scroll, div[data-testid="stLayoutWrapper"]:has(> .st-key-chat_scroll) {{
    height: 38vh !important; min-height: 240px !important; flex: 0 0 auto !important; }}
  .chat-empty {{ padding: 2vh 4% 8px 4%; }}
  .chat-empty-title {{ font-size: 1.3rem; }}
}}

/* primary buttons: mint with dark text in both themes (one per screen) */
button[data-testid^="stBaseButton-primary"]:not(:disabled) {{ background: var(--primary) !important;
  border-color: #5DB98C !important; }}
button[data-testid^="stBaseButton-primary"]:not(:disabled):hover {{ filter: brightness(1.06); }}
button[data-testid^="stBaseButton-primary"]:not(:disabled),
button[data-testid^="stBaseButton-primary"]:not(:disabled) p {{ color: var(--on-primary) !important; font-weight: 650; }}
/* touch targets: buttons and inputs 44 px high */
button[data-testid^="stBaseButton-primary"], button[data-testid^="stBaseButton-secondary"] {{ min-height: 2.75rem; }}
[data-testid="stTextInputRootElement"], [data-baseweb="select"] > div {{ min-height: 2.75rem; }}
/* Overview: the "Needs attention" list (no wide table, so nothing overlaps at ~1000 px) */
.att-row {{ display: flex; align-items: flex-start; gap: 12px; padding: 10px 0; border-top: 1px solid var(--border); }}
.att-row:first-of-type {{ border-top: none; }}
.att-main {{ flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; gap: 2px; font-size: .95rem; }}
.att-main span {{ color: var(--muted); }}
.att-side {{ flex: none; display: flex; flex-direction: column; align-items: flex-end; gap: 4px; font-size: .875rem;
  color: var(--muted); white-space: nowrap; font-variant-numeric: tabular-nums; }}
/* short labels never break inside a word ("Rep orts") */
button[data-testid^="stBaseButton"] p {{ white-space: nowrap; }}
/* sidebar: review progress for the audit shown */
.progress-card {{ margin: 4px 4px 8px 4px; display: flex; flex-direction: column; gap: 6px; font-size: .82rem;
  color: var(--muted); }}
.progress-card .top {{ display: flex; justify-content: space-between; font-variant-numeric: tabular-nums; }}
.progress-card .bar {{ height: 8px; border-radius: 999px; background: var(--surface-2); overflow: hidden; }}
.progress-card .bar div {{ height: 8px; border-radius: 999px; background: var(--sage); }}
/* Scores: "Why is it N?"; Organisation: notes and example lists */
.why-list {{ margin: 4px 0 8px 0; padding-left: 20px; font-size: .95rem; line-height: 1.55; color: var(--text); }}
.why-list li {{ margin-bottom: 6px; }}
.org-note {{ font-size: .95rem; line-height: 1.5; color: var(--text); }}
.org-note.off {{ color: var(--muted); text-decoration: line-through; }}
.org-list {{ margin: 6px 0 4px 0; padding-left: 18px; font-size: .93rem; line-height: 1.5; color: var(--text); }}
.org-list li {{ margin-bottom: 6px; }}
.org-list li.more {{ list-style: none; margin-left: -18px; color: var(--muted); }}
/* rule cards, notes, sections, history */
.rule-body .issue-head {{ margin-bottom: 4px; }}
.rule-note {{ margin-top: 10px; padding: 8px 12px; border-radius: 8px; font-size: .93rem; line-height: 1.5;
  background: {_rgba(MUSTARD, .12)}; border: 1px solid {_rgba(MUSTARD, .35)}; color: var(--text); }}
.section-head {{ margin: 16px 0 0 0; font-size: 1.05rem; font-weight: 650; color: var(--text); }}
.section-head span {{ font-weight: 400; color: var(--muted); font-size: .93rem; }}
.rel-link {{ font-size: 1rem; color: var(--text); }}
.history {{ list-style: none; margin: 4px 0 6px 0; padding: 0; border-left: 2px solid var(--border); }}
.history li {{ margin: 0 0 8px 0; padding: 0 0 0 14px; display: flex; gap: 12px; font-size: .93rem;
  line-height: 1.5; color: var(--text); }}
.history .when {{ flex: none; min-width: 6.5em; color: var(--muted); font-variant-numeric: tabular-nums; }}
/* a disabled button stays readable (the caption above it says what is missing) */
button[data-testid^="stBaseButton"]:disabled, button[data-testid^="stBaseButton"]:disabled p {{
  color: var(--muted) !important; opacity: 1 !important; }}
</style>
"""
