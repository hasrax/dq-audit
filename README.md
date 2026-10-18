# ClarityDQ — Explainable Data Quality Audit for SME ERP data

> Working name; set via two constants in `dashboard/app.py`.

An **LLM-augmented, explainable data quality audit framework** for Sri Lankan
SME ERP systems. It reads CSV/Excel exports (no database access),
finds data quality issues with a hybrid of **deterministic rules** and a
**selective LLM auditor**, explains them in plain language, and lets a
non-technical reviewer triage them — turning their decisions into ground-truth
evaluation data.

BSc Hons Computing final-year research (Coventry University / NIBM), following
**Design Science Research**. Read-only, periodic **audit** — not a cleaning or
ETL tool.

---

## Highlights

- **Two-error taxonomy** — ⚙️ *Rules (Type 1)* over every row (completeness,
  validity, consistency, uniqueness, timeliness, cross-table integrity) +
  🤖 *LLM (Type 2)* on a pseudonymized sample for contextual anomalies. Their
  **complementarity** is measured, not assumed.
- **Schema-agnostic** — real ERP exports are headerless, so detection is
  positional and never hard-codes table/column names; the same pipeline runs on
  different clients of the ERP.
- **Explainable & auditable** — every issue carries a full ontology tuple, a
  plain-language explanation, a recommendation, and its affected rows.
- **Human-in-the-loop** — accept / reject / consider triage doubles as the
  evaluation instrument (empirical precision, hallucination rate).
- **Severity-weighted DQ score** with a transparent, configurable weighting and
  a cross-upload trend.
- **Streamlit dashboard** (5 tabs) + a **FastAPI** service layer over the same
  code.

---

## Architecture (five layers)

| Layer | Does | Key modules |
|---|---|---|
| L0 Ingestion & profiling | load CSV/Excel, type inference, stats, PK/FK discovery, sampling, pseudonymization | `app/io`, `app/profiler`, `app/privacy` |
| L1 Rules engine (Type 1) | six deterministic checks auto-derived from the profile | `app/rules` |
| L2 LLM auditor (Type 2) | contextual anomalies on profile + pseudonymized sample only | `app/llm` |
| L3 Human triage | accept/reject/consider → ground truth | `dashboard/app.py`, `app/db` |
| L4 Monitoring & scoring | severity-weighted DQ score + trend | `app/scoring` |

The design rationale and the evaluation results are in the dissertation. This
repository holds the code only.

---

## Setup

**Prerequisites:** Python 3.13, PostgreSQL 17, an OpenAI-compatible LLM endpoint
(e.g. a free Groq API key).

```bash
# 1. virtual env + dependencies
python -m venv .venv
.venv/Scripts/python -m pip install -r backend/requirements.txt   # Windows
# source .venv/bin/activate && pip install -r backend/requirements.txt  # *nix

# 2. configuration
cp backend/.env.example backend/.env      # then edit backend/.env with real values

# 3. database (once)
#    create an empty database named dq_audit in PostgreSQL; numbered
#    migrations (backend/app/db/migrations/NNN_*.sql) are applied
#    automatically on first use and recorded in schema_migrations.

# 4. first administrator (password is prompted, never typed on the command line)
cd backend && python -m app.auth.users create admin --role=admin
```

`backend/.env` (gitignored) holds the LLM and PostgreSQL credentials — see
`backend/.env.example`. `config/privacy/<client>.json` lists columns that must
always be masked before any cloud LLM call (phone numbers stored as numbers,
IDs, GPS); `config/client_labels.json` maps clients to anonymised display
labels (Company A/B/C) used throughout the UI.

---

## Running

All commands run from the repo root with the venv's Python.

```bash
# audit + persist + score a client's CSV/Excel directory (add --llm for Type 2)
python -m app.db.persist data/<company-folder> --llm      # run from backend/, or set PYTHONPATH=backend

# recurring issues: every audit is matched to the client's previous one
# (new / worsening / improving / stable + linear projection). To recompute
# history for all existing uploads:
python -m app.monitoring.recurrence --backfill

# time-sliced replay of one real export (config/replay/<client>.json) to
# demonstrate trends and measure projection error; snapshots go to the
# git-ignored data/_replay/, source files are never touched
python -m app.eval.replay data/<company-folder> --client=<company-key> [--llm]

# AI-drafted schema documentation (review + verify it in the Schema review tab;
# verified docs are added to later LLM audits). Skips already-documented tables.
python -m app.schema_docs.generate data/<company-folder> --client=<company-key> [--regenerate]

# the tool -- sign in first. Reviewers: Overview, Triage, Ask (chat), Rules,
# Scores, Relationships, CSV/Excel export. Admins also get New audit, Schema
# review, Organisation (notes the AI always sees + what it learned from
# triage), and Settings (AI model choice from LLM_OPTIONS, people).
streamlit run dashboard/app.py

# HTTP API (health, uploads, issues, score, triage)
uvicorn app.api:app --reload

# evaluation
python -m app.eval.run_eval               # E1/E2 injection eval (rules + LLM)
python -m app.eval.run_eval --multiseed   # E1 robustness across seeds
python -m app.eval.cross_client           # E4 cross-client reproducibility
python -m app.eval.figures <company-key>         # E2 complementarity figure (PNG)
```

Column display names: `config/aliases/<client>.json`. Score weights:
`config/score_weights.json`.

---

## Evaluation (headline results)

| Experiment | Result |
|---|---|
| **E1** detection recall | Rules Type 1 recall **1.0** (multi-seed **1.0 ± 0.0**); combined **0.9383** |
| **E2** complementarity | Rule-only ~80–88% · LLM-only ~6–21% · **Both 0%** — complementary, not redundant |
| **E3** triage-derived | empirical precision / hallucination / ambiguity (populated by triage) |
| **E4** cross-client | **83.33%** dimension reproducibility (Company A ↔ Company B) |

Rule metrics are deterministic; LLM-inclusive figures vary slightly per run.

---

## Testing

A comprehensive `pytest` suite (unit, real-database, Streamlit `AppTest`, and
FastAPI `TestClient`):

```bash
cd backend && python -m pytest -q
```
Database and LLM-independent tests skip gracefully when PostgreSQL is
unavailable.

---

## Project structure

```
backend/app/
  io/           loader (CSV/Excel, header detection)
  profiler/     types, stats, keys, relationships, profile, sampling
  privacy/      pseudonymization
  rules/        the six checks + engine + plain-English descriptions
  llm/          client, prompt, auditor, run_audit, feedback, model selection
  schema_docs/  AI-drafted, human-verified schema documentation
  monitoring/   recurring issues, trends, projections
  scoring/      DQ score
  db/           migrations, repository, persistence, masked hosted copy (sync_cloud)
  reports/      grouping, wording, audit trail, standard report, Word/PDF
  chat/         Ask (chat about an audit or an issue) and saved threads
  auth/         users and roles
  study/        unmoderated usability study mode
  eval/         evaluation scripts (T1-T15) and the evidence workbook
  api.py        FastAPI
  workspaces.py companies (workspaces)
  aliases.py    display names for headerless columns
  export.py     CSV / Excel export
backend/tests/      pytest suite
dashboard/          Streamlit app (app.py), theme, logos
config/             per-company labels, aliases, privacy lists, replay specs, score weights
tools/              helper scripts outside the framework
data/, evaluation/  exports and evaluation outputs (git-ignored, never committed)
```

---

## Notes

Academic research artifact. Real client data (`data/`) and secrets (`.env`) are
gitignored and never committed. Claim: **reproducibility across clients of one
ERP + preliminary transferability** — not generalizability.
