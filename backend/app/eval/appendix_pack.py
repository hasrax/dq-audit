"""Appendix pack for the dissertation: every supporting table, figure and study material.

Usage (from backend/):
    python -m app.eval.appendix_pack

Writes evaluation/appendix/Appendix_pack.docx (figures and screenshots embedded,
black and white tables) and Appendix_pack.md (the same text, figures referenced by
file name) from the files in evaluation/results/ and the study materials. Company
labels only; values that could identify people never appear (open answers are
the participants' own words about the system).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
from docx import Document
from docx.shared import Inches, Pt

from app.study.participants import TASKS

ROOT = Path(__file__).resolve().parents[3]
RES = ROOT / "evaluation" / "results"
OUT = ROOT / "evaluation" / "appendix"
SHOTS = ROOT / "output" / "study-screens"
APPX_SHOTS = ROOT / "evaluation" / "screenshots" / "appendix"
TESTING = ROOT / "evaluation" / "testing"
FORM = next(iter(sorted((ROOT / "evaluation").glob("*usability questionnaire*.xlsx"))), None)
T11 = ROOT / "evaluation" / "T11_schema_check_CompanyA.xlsx"

CONSENT = [
    "ClarityDQ checks a company's business data for mistakes - missing, wrong or inconsistent values - and "
    "explains each problem in plain English.",
    "You'll try 5 short tasks on a copy of anonymised audit results, then answer a short questionnaire.",
    "Nothing you do here changes any real data. There are no right or wrong answers - we are testing the "
    "system, not you.",
    "This takes about 10 minutes, on a phone or a computer.",
    "Consent box: \"I agree to take part. My answers are anonymous and may be used in a university research "
    "project.\"",
]
SUS = ["I think that I would like to use this system frequently.", "I found the system unnecessarily complex.",
       "I thought the system was easy to use.",
       "I think that I would need the support of a technical person to be able to use this system.",
       "I found the various functions in this system were well integrated.",
       "I thought there was too much inconsistency in this system.",
       "I would imagine that most people would learn to use this system very quickly.",
       "I found the system very cumbersome to use.", "I felt very confident using the system.",
       "I needed to learn a lot of things before I could get going with this system."]
CLARITY = ["The explanation of each issue was easy to understand.", "I understood why the system flagged the issue.",
           "The suggested fix (\"What to do\") was clear and useful.",
           "The chat answer helped me understand the issue better.",
           "The report was clear enough to share with a manager."]
OPEN = ["What was the most useful part of the system?", "What was the most confusing part?",
        "If you could change one thing, what would it be?", "Any other comments?"]
SCREENS = [   # (caption, file); phone screenshots are placed narrower
    ("Study: information and consent screen (laptop)", APPX_SHOTS / "Study_1_SignIn.png"),
    ("Study: task card above the study copy (phone)", APPX_SHOTS / "Study_2_TaskCard.png"),
    ("Study: issue details (phone)", SHOTS / "2026-10-10/phone/m06_issue_dialog.png"),
    ("Study: chat answer comparing masked example rows (phone)", SHOTS / "2026-10-10/phone/m09_task3_chat.png"),
    ("Updated issue list: grouped issues under 'Needs attention' (phone)", SHOTS / "2026-10-12-v2/phone_triage_v2.png"),
    ("Study administration: participant sessions (P00 = pilot)", APPX_SHOTS / "Study_3_Sessions.png"),
    ("After the study: importance order and issue-type chips", SHOTS / "2026-10-12-v2/laptop_triage_priority.png"),
    ("After the study: action categories (Fix now / Can wait / All)", SHOTS / "2026-10-12-v2/laptop_triage_actions.png"),
    ("After the study: severity and Rule/AI labels with multi-select",
     SHOTS / "2026-10-12-v2/laptop_triage_labels_multiselect.png"),
]
PHONE_SHOTS = {"Study_2_TaskCard.png", "m06_issue_dialog.png", "m09_task3_chat.png", "phone_triage_v2.png"}


def md_tables(path: Path, first_cell: str) -> pd.DataFrame | None:
    """Rows of the Markdown tables in a file whose first cell is first_cell + a number (e.g. F12, D3)."""
    if not path.exists():
        return None
    header, body = None, []
    for ln in path.read_text(encoding="utf-8").splitlines():
        if not ln.startswith("|") or set(ln.replace("|", "").strip()) <= {"-", " "}:
            continue
        cells = [c.strip().replace("**", "") for c in ln.strip().strip("|").split("|")]
        if cells[0].startswith(first_cell) and cells[0][len(first_cell):].isdigit():
            body.append(cells)
        elif header is None and cells[0] in ("#", "No."):
            header = cells
    if not body:
        return None
    width = max(len(r) for r in body)
    header = (header or [f"col{i}" for i in range(width)])[:width]
    return pd.DataFrame([r + [""] * (width - len(r)) for r in body], columns=header)


class Pack:
    def __init__(self):
        self.doc = Document()
        st = self.doc.styles["Normal"]
        st.font.name, st.font.size = "Calibri", Pt(10)
        self.md: list[str] = []
        self.fig = 0
        self.tab = 0

    def h(self, text: str, level: int = 1):
        self.doc.add_heading(text, level=level)
        self.md.append("#" * level + " " + text)

    def p(self, text: str):
        self.doc.add_paragraph(text)
        self.md.append(text)

    def bullets(self, items: list[str]):
        for i in items:
            self.doc.add_paragraph(i, style="List Bullet")
        self.md.append("\n".join(f"- {i}" for i in items))

    def table(self, df: pd.DataFrame, caption: str):
        self.tab += 1
        cap = f"Table {self.tab}. {caption}"
        self.doc.add_paragraph(cap).runs[0].bold = True
        t = self.doc.add_table(rows=1, cols=len(df.columns))
        t.style = "Table Grid"
        for j, c in enumerate(df.columns):
            t.rows[0].cells[j].text = str(c)
            t.rows[0].cells[j].paragraphs[0].runs[0].bold = True
        for r in df.itertuples(index=False):
            cells = t.add_row().cells
            for j, v in enumerate(r):
                cells[j].text = "" if pd.isna(v) else str(v)
        self.doc.add_paragraph()
        cols = [str(c) for c in df.columns]
        rows = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        rows += ["| " + " | ".join("" if pd.isna(v) else str(v).replace("\n", " ") for v in r) + " |"
                 for r in df.itertuples(index=False)]
        self.md += [f"**{cap}**", "\n".join(rows)]

    def figure(self, path: Path, caption: str, width: float = 6.0):
        if not path.exists():
            return
        self.fig += 1
        cap = f"Figure {self.fig}. {caption}"
        self.doc.add_picture(str(path), width=Inches(width))
        self.doc.add_paragraph(cap).runs[0].italic = True
        self.md.append(f"**{cap}**")

    def csv(self, name: str) -> pd.DataFrame | None:
        p = RES / name
        return pd.read_csv(p) if p.exists() else None


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    k = Pack()
    k.doc.add_heading("Appendices: evaluation materials and results", 0)
    k.md.append("# Appendices: evaluation materials and results")
    k.p(f"Generated {datetime.now():%d %B %Y %H:%M} from the evaluation outputs (evaluation/results). "
        "Organisations are labelled Company A-F. The timestamped evidence workbook evaluation_results.xlsx "
        "records the git commit, model and SHA-256 of every source file.")

    k.h("Appendix A. Usability study materials")
    k.h("A.1 Information and consent", 2)
    k.bullets(CONSENT)
    k.h("A.2 The five tasks (wording after the pilot)", 2)
    k.table(pd.DataFrame([{"Task": n, "Title": t, "Instruction": d.replace("**", "")} for n, (t, d) in TASKS.items()]),
            "Study tasks shown to participants")
    k.h("A.3 Questionnaire", 2)
    k.table(pd.DataFrame({"Item": [f"Q{i}" for i in range(1, 11)], "Statement (1 = strongly disagree, 5 = strongly agree)": SUS}),
            "System Usability Scale items (Brooke, 1996)")
    k.table(pd.DataFrame({"Item": [f"C{i}" for i in range(1, 6)], "Statement (1-5)": CLARITY}),
            "Explanation clarity items")
    k.bullets(["Also asked: consent (yes/no), participant code (pre-filled), main role at work, years of ERP use."]
              + [f"Open question: {q}" for q in OPEN])

    k.h("Appendix B. Usability study results")
    k.p("One unmoderated study with 15 participants (P01-P15) and 14 completed questionnaires. P00 was the pilot "
        "session and is not counted. P05 did not submit the questionnaire. After the first seven sessions the issue "
        "list was updated (grouped issues and plainer wording); the tasks and the questionnaire stayed the same.")
    pp = k.csv("usability.csv")
    if pp is not None:
        keep = [c for c in pp.columns if c not in ("task1_answer", "browser", "version")]
        k.table(pp[keep], "Per-participant results (P01-P15): tasks done and system-confirmed, minutes, task seconds")
    d = k.csv("usability_tasks.csv")
    if d is not None:
        k.table(d, "Task completion (15 participants)")
        k.figure(RES / "fig_task_completion.png", "Task completion: self-reported and system-confirmed", 5.5)
    sus = k.csv("sus_scores.csv")
    if sus is not None:
        cols = [c for c in ("code", "role", "erp_experience", "sus", "C1", "C2", "C3", "C4", "C5") if c in sus.columns]
        k.table(sus[cols], "SUS score and clarity ratings per participant (14 questionnaires; P05 did not submit)")
        k.figure(RES / "fig_sus.png", "SUS scores per participant (benchmark 68)", 5.5)
    integ = k.csv("integrity.csv")
    if integ is not None:
        k.table(integ, "Integrity checks")
    if FORM is not None:
        form = pd.read_excel(FORM, dtype=str).fillna("")
        code_c = next(c for c in form.columns if c.lower().startswith("participant code"))
        form = form[form[code_c].str.upper() != "P00"]
        k.h("B.1 Open answers (participants' own words)", 2)
        for q in OPEN:
            col = next((c for c in form.columns if c.startswith(q[:25])), None)
            if col:
                answers = [f"{c}: {a.strip()}" for c, a in zip(form[code_c], form[col]) if a.strip()]
                if answers:
                    k.p(q)
                    k.bullets(answers)

    k.h("Appendix C. Detection results")
    prec = (RES / "precision.md")
    d = k.csv("precision.csv")
    if d is not None:
        k.table(d, "Precision by company and detector (Wilson 95% CI)")
        k.figure(RES / "fig_precision.png", "Precision by company and detector", 5.5)
    if prec.exists():
        txt = prec.read_text(encoding="utf-8")
        if "## By dimension" in txt:
            lines = [ln for ln in txt.split("## By dimension")[1].splitlines() if ln.startswith("|")]
            if len(lines) > 2:
                hdr = [c.strip() for c in lines[0].strip("|").split("|")]
                body = [[c.strip() for c in ln.strip("|").split("|")] for ln in lines[2:]]
                k.table(pd.DataFrame(body, columns=hdr), "Precision by dimension (decided issues)")
    for name, cap in (("complementarity.csv", "Complementarity: locations found by rules only, AI only or both"),
                      ("ai_stability.csv", "AI run-to-run stability (Company A)"),
                      ("ai_stability_pairs.csv", "Pairwise Jaccard similarity between AI runs (strict matching)"),
                      ("injection_coverage.csv", "Injection coverage demonstration (planted errors)"),
                      ("projection_error.csv", "Trend projection error"),
                      ("reproducibility.csv", "Reproducibility and transferability across Companies A-F (latest audit each)")):
        d = k.csv(name)
        if d is not None:
            k.table(d.drop(columns=[c for c in ("hosted",) if c in d.columns]), cap)
    k.figure(RES / "fig_ai_stability.png", "How often each AI finding recurred across three runs", 5.0)
    k.figure(RES / "fig_injection_coverage.png", "Planted-error coverage by detector", 5.5)

    k.h("Appendix D. Schema-description check (T11)")
    d = k.csv("schema_check_t11.csv")
    if d is not None:
        k.table(d, "AI-drafted column descriptions judged against the source schema, by AI confidence")
    if T11.exists():
        s = pd.read_excel(T11, sheet_name="Schema check")
        cols = ["Table", "Column", "Real name (source database)", "AI short name", "AI confidence", "Judgement"]
        k.table(s[cols], "All 93 Company A columns: real name, AI draft and judgement")

    k.h("Appendix E. Privacy and access-control check (T15)")
    d = k.csv("privacy_prompts.csv")
    if d is not None:
        k.table(d, "Personal data found in AI prompts built without sending (expected zero)")
    pc = RES / "privacy_check.md"
    if pc.exists() and "## Note" in pc.read_text(encoding="utf-8"):
        k.p(pc.read_text(encoding="utf-8").split("## Note")[1].strip())

    k.h("Appendix F. Second-reviewer results (T12 agreement, T4 recall)")
    k.p("One second reviewer (R2, data and operations role) worked blind to the detector and to the researcher's "
        "decisions, on Google Sheets with real column names and masked values.")
    d = k.csv("agreement.csv")
    if d is not None:
        k.table(d, "Triage agreement: researcher vs R2 (Cohen's kappa)")
    agr = RES / "agreement.md"
    if agr.exists():
        txt = agr.read_text(encoding="utf-8")
        for section, cap in (("## Confusion", "Decisions compared (rows = researcher, columns = R2)"),
                             ("## Disagreements", "Disagreements with R2's reasons")):
            if section in txt:
                part = txt.split(section, 1)[1].split("\n## ")[0]
                lines = [ln for ln in part.splitlines() if ln.startswith("|")]
                if len(lines) > 2:
                    hdr = [c.strip() for c in lines[0].strip("|").split("|")]
                    body = [[c.strip() for c in ln.strip("|").split("|")] for ln in lines[2:]]
                    k.table(pd.DataFrame(body, columns=hdr), cap)
    d = k.csv("recall.csv")
    if d is not None:
        k.table(d[d["rows_rated"] > 0], "Recall spot-check: problems found in rows the system did not flag (R2)")
        k.p("The researcher's own 200-row recall pass was not completed, so only R2's 50-row subset is reported.")

    k.h("Appendix G. Testing (T1 functional checklist, manual testing, T14 automated tests)")
    d = md_tables(TESTING / "T1_functional_checklist.md", "F")
    if d is not None:
        k.table(d, f"Functional checklist ({len(d)} functions): how each was verified")
    d = md_tables(TESTING / "manual_testing_log.md", "D")
    if d is not None:
        k.table(d, f"Defects found by manual and browser testing ({len(d)}, all fixed)")
    d = k.csv("automated_tests.csv")
    if d is not None:
        comp = (d.groupby("component", sort=False)[["tests", "passed", "failed", "skipped"]].sum().reset_index()
                .assign(files=d.groupby("component", sort=False).size().values))
        comp = comp[["component", "files", "tests", "passed", "failed", "skipped"]]
        k.table(comp, f"Automated tests by component ({int(comp['tests'].sum())} tests, all passed)")

    k.h("Appendix H. Screenshots of the artefact")
    for cap, path in SCREENS:
        k.figure(path, cap, 3.0 if path.name in PHONE_SHOTS else 6.0)

    k.h("Appendix I. Evidence trail")
    wb = RES / "evaluation_results.xlsx"
    if wb.exists():
        readme = pd.read_excel(wb, sheet_name="README", header=None).fillna("")
        k.table(readme.iloc[:, :5].astype(str), "Evidence workbook README (generation time, commit, model, file hashes)")

    k.doc.save(OUT / "Appendix_pack.docx")
    (OUT / "Appendix_pack.md").write_text("\n\n".join(k.md), encoding="utf-8")
    print(f"tables {k.tab}, figures {k.fig}; wrote {OUT / 'Appendix_pack.docx'} and Appendix_pack.md")


if __name__ == "__main__":
    main()
