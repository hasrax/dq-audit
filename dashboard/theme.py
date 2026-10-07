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
which reads calmer on dark and keeps text contrast high.
"""
from __future__ import annotations

import html

import plotly.graph_objects as go

PALETTES = {
    "dark": dict(BG="#16181D", SURFACE="#1F2229", SURFACE_2="#272B34", BORDER="#343A46",
                 TEXT="#EEF0F4", MUTED="#B4BBC8", FAINT="#8790A0",
                 SAGE="#8FC9B4", MUSTARD="#F1C76E", CORAL="#F39A85", LAVENDER="#B7ABF4", BLUE="#86AEF7",
                 ON_ACCENT="#0B1210", GLOW=0.08),
    "light": dict(BG="#F7F6F2", SURFACE="#FFFFFF", SURFACE_2="#F1EFE9", BORDER="#E3E0D7",
                  TEXT="#1E2127", MUTED="#5B6270", FAINT="#8A909C",
                  SAGE="#2E7D63", MUSTARD="#94650A", CORAL="#C2412D", LAVENDER="#6A55C9", BLUE="#2F64C8",
                  ON_ACCENT="#FFFFFF", GLOW=0.10),
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
    global SAGE, MUSTARD, CORAL, LAVENDER, BLUE, ON_ACCENT, GLOW
    MODE = mode if mode in PALETTES else "dark"
    p = PALETTES[MODE]
    BG, SURFACE, SURFACE_2, BORDER = p["BG"], p["SURFACE"], p["SURFACE_2"], p["BORDER"]
    TEXT, MUTED, FAINT = p["TEXT"], p["MUTED"], p["FAINT"]
    SAGE, MUSTARD, CORAL, LAVENDER, BLUE = p["SAGE"], p["MUSTARD"], p["CORAL"], p["LAVENDER"], p["BLUE"]
    ON_ACCENT, GLOW = p["ON_ACCENT"], p["GLOW"]
    SEVERITY.clear()
    SEVERITY.update({"high": CORAL, "medium": MUSTARD, "low": MUTED})
    SOURCE.clear()
    SOURCE.update({"rule": ("Rule · Type 1", BLUE), "llm": ("AI · Type 2", LAVENDER)})
    TREND.clear()
    TREND.update({"new": ("NEW", BLUE), "worsening": ("▲ WORSENING", CORAL),
                  "improving": ("▼ IMPROVING", SAGE), "stable": ("■ STABLE", MUTED)})
    STATUS.clear()
    STATUS.update({"flagged": ("To review", MUTED), "accepted": ("Accepted", SAGE),
                   "rejected": ("Rejected", CORAL), "considered": ("Considered", MUSTARD)})
    return MODE


apply("dark")


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alpha})"


def pill(text: str, color: str) -> str:
    return (f'<span class="pill" style="color:{color};background:{_rgba(color, .14)};'
            f'border-color:{_rgba(color, .38)}">{html.escape(text)}</span>')


def kpi_card(label: str, value: str, caption: str, color: str) -> str:
    """Gradient score card in the spirit of the reference dashboards."""
    return f'''<div class="kpi" style="background:linear-gradient(135deg,{_rgba(color, .30)} 0%,{_rgba(color, .08)} 70%);
  border-color:{_rgba(color, .45)}">
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
.stApp {{ background: radial-gradient(1200px 600px at 100% 110%, {_rgba(SAGE, GLOW)}, transparent 60%), var(--bg); }}
[data-testid="stMainBlockContainer"] {{ padding-top: 2.4rem; padding-bottom: 5rem; max-width: 1320px; }}
[data-testid="stMainBlockContainer"] [data-testid="stVerticalBlock"] {{ gap: 1.15rem; }}
p, li {{ line-height: 1.6; }}
[data-testid="stSidebar"] .sidebar-section {{ font-size: .72rem; font-weight: 700; letter-spacing: .6px;
  text-transform: uppercase; color: var(--faint); margin: 14px 0 2px 4px; }}
[data-testid="stSidebar"] [data-testid="stPageLink"] a {{ padding: 6px 10px; border-radius: 10px; }}
.company-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 16px; padding: 18px 20px 14px 20px; }}
.company-card .cname {{ font-size: 1.15rem; font-weight: 700; color: var(--text); }}
.company-card .cmeta {{ font-size: .86rem; color: var(--muted); margin-top: 2px; }}
.company-card .cscore {{ font-size: 2.3rem; font-weight: 750; line-height: 1.1; margin-top: 12px; }}
.company-card .cstats {{ display:flex; gap:18px; margin-top: 10px; font-size: .88rem; color: var(--muted); }}
.company-card .cstats b {{ color: var(--text); font-size: 1rem; }}
.st-key-chat_scroll {{ height: calc(100vh - 335px) !important; min-height: 380px; }}
.st-key-chat_threads {{ height: calc(100vh - 225px) !important; min-height: 420px; }}
[data-testid="stSidebar"] {{ border-right: 1px solid var(--border); }}
[data-testid="stHeader"] {{ background: transparent; }}
h1, h2, h3, h4, h5 {{ letter-spacing: 0.1px; }}

/* cards */
.card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 16px; padding: 20px 22px; }}
.card-title {{ font-size: 1.08rem; font-weight: 650; color: var(--text); margin-bottom: 8px; }}
.card-sub {{ font-size: .84rem; color: var(--muted); }}
[data-testid="stVerticalBlockBorderWrapper"] {{ border-radius: 16px; }}
div[class*="st-key-"] [data-testid="stVerticalBlockBorderWrapper"],
[data-testid="stVerticalBlock"] > [data-testid="stVerticalBlockBorderWrapper"] {{ background: var(--surface); }}

.kpi {{ border: 1px solid; border-radius: 16px; padding: 16px 18px 14px 18px; min-height: 118px; }}
.kpi-label {{ font-size: .86rem; color: var(--muted); }}
.kpi-value {{ font-size: 2.2rem; font-weight: 750; line-height: 1.15; margin-top: 6px; letter-spacing: -0.5px; }}
.kpi-caption {{ font-size: .8rem; color: var(--muted); margin-top: 4px; }}

.pills {{ display:flex; gap:6px; flex-wrap:wrap; }}
.pill {{ display:inline-block; padding: 2px 10px; border-radius: 999px; border: 1px solid;
        font-size: .74rem; font-weight: 650; white-space: nowrap; letter-spacing: .2px; }}
.pill-outline {{ display:inline-block; padding: 2px 10px; border-radius: 999px; font-size: .74rem;
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
.issue-card {{ background: var(--surface); border: 1px solid var(--border); border-left: 4px solid var(--sev, {MUTED});
  border-radius: 14px; padding: 14px 18px 12px 18px; margin: 4px 0 6px 0; }}
.issue-head {{ display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; }}
.issue-title {{ font-size: 1.04rem; font-weight: 700; color: var(--text); }}
.issue-title .col {{ font-weight: 500; color: var(--muted); }}
.issue-desc {{ margin-top: 8px; font-size: 1rem; line-height: 1.55; color: var(--text); }}
.issue-rec {{ margin-top: 10px; font-size: .93rem; line-height: 1.5; color: var(--text);
  background: var(--surface-2); border-radius: 8px; padding: 8px 12px; }}
.issue-meta {{ margin-top: 8px; font-size: .82rem; color: var(--muted); }}
.issue-trend {{ margin-top: 8px; font-size: .88rem; line-height: 1.5; color: var(--text); }}
.issue-trend .est {{ color: var(--muted); font-style: italic; }}

/* chat */
[data-testid="stChatMessage"] h1, [data-testid="stChatMessage"] h2, [data-testid="stChatMessage"] h3 {{
  font-size: 1.02rem !important; margin: 10px 0 4px 0 !important; }}
[class*="st-key-thread_"] button {{ justify-content: flex-start; text-align: left; }}
[class*="st-key-thread_"] button p {{ text-align: left; }}

/* compact issue rows (triage list) */
.issue-row {{ background: var(--surface); border: 1px solid var(--border); border-left: 4px solid var(--sev, {MUTED});
  border-radius: 12px; padding: 10px 14px; }}
.issue-row .top {{ display:flex; justify-content:space-between; align-items:center; gap:10px; flex-wrap:wrap; }}
.issue-row .loc {{ font-weight: 700; color: var(--text); }}
.issue-row .loc .col {{ font-weight: 500; color: var(--muted); }}
.issue-row .sum {{ margin-top: 4px; color: var(--text); font-size: .93rem; line-height: 1.45;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.issue-row .meta {{ margin-top: 3px; color: var(--faint); font-size: .8rem; }}
.section-label {{ font-size: .78rem; text-transform: uppercase; letter-spacing: .5px; color: var(--faint);
  margin: 14px 0 4px 0; font-weight: 650; }}

.rule-card {{ background: var(--surface); border: 1px solid var(--border); border-left: 4px solid var(--accent, {SAGE});
  border-radius: 14px; padding: 12px 16px 10px 16px; margin: 0 0 4px 0; }}
.rule-what {{ font-size: 1rem; font-weight: 650; line-height: 1.45; margin-top: 6px; color: var(--text); }}
.rule-why {{ margin-top: 4px; font-size: .93rem; line-height: 1.5; color: var(--muted); }}
.rule-ev {{ margin-top: 6px; font-size: .8rem; color: var(--faint); }}

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

.brand {{ display:flex; align-items:center; gap:10px; padding: 4px 0 6px 0; }}
.brand .logo {{ width:30px; height:30px; border-radius:9px;
  background: linear-gradient(135deg, {SAGE}, {LAVENDER}); box-shadow: 0 0 18px {_rgba(SAGE, .35)}; }}
.brand .name {{ font-size: 1.25rem; font-weight: 750; color: var(--text); letter-spacing: .2px; }}
.brand .tag {{ font-size: .74rem; color: var(--muted); margin-top: -2px; }}

/* floating "+ Import" action */
.st-key-fab {{ position: fixed; right: 34px; bottom: 30px; z-index: 1000; width: auto; }}
.st-key-fab button {{ border-radius: 999px; padding: 10px 22px; font-weight: 700;
  background: linear-gradient(135deg, {SAGE}, {BLUE}); color: {ON_ACCENT}; border: none;
  box-shadow: 0 10px 30px {_rgba(SAGE, .35)}; }}
.st-key-fab button:hover {{ filter: brightness(1.08); color: {ON_ACCENT}; }}

/* primary buttons: text colour that reads on the sage primary in both modes */
button[data-testid="stBaseButton-primary"]:not(:disabled) {{ color: {ON_ACCENT} !important; font-weight: 650; }}
</style>
"""
