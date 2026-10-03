# ClarityDQ — Explainable Data Quality Audit for SeefaERP

> Working name; set via two constants in `dashboard/app.py`.

An **LLM-augmented, explainable data quality audit framework** for Sri Lankan
SME systems running SeefaERP. It reads CSV/Excel exports (no database access),
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
- **Schema-agnostic** — real SeefaERP exports are headerless, so detection is
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

See **[docs/design-rationale.md](docs/design-rationale.md)** and
**[docs/artifact-handoff.md](docs/artifact-handoff.md)** for the full design
rationale and evaluation results.

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
python -m app.db.persist data/hemas --llm      # run from backend/, or set PYTHONPATH=backend

# AI-drafted schema documentation (review + verify it in the Schema review tab;
# verified docs are added to later LLM audits). Skips already-documented tables.
python -m app.schema_docs.generate data/hemas --client=hemas [--regenerate]

# the tool (Overview / Upload / Triage / Rules / Schema review / Relationships / Scores)
streamlit run dashboard/app.py

# HTTP API (health, uploads, issues, score, triage)
uvicorn app.api:app --reload

# evaluation
python -m app.eval.run_eval               # E1/E2 injection eval (rules + LLM)
python -m app.eval.run_eval --multiseed   # E1 robustness across seeds
python -m app.eval.cross_client           # E4 cross-client reproducibility
python -m app.eval.figures hemas          # E2 complementarity figure (PNG)
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
| **E4** cross-client | **83.33%** dimension reproducibility (Hemas ↔ Ussawatte) |

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
  io/          loader (CSV/Excel, header detection)
  profiler/    types, stats, keys, relationships, profile, sampling
  privacy/     pseudonymization
  rules/       the six checks + engine
  llm/         client, prompt, auditor, run_audit
  scoring/     DQ score
  db/          schema, repository, persistence
  eval/        injection, baseline, matching, metrics, complementarity, cross_client, figures
  api.py       FastAPI
  aliases.py   display aliases
  export.py    CSV export
dashboard/app.py   Streamlit UI
config/            aliases + score weights
docs/              design rationale, handoff, diagrams
data/              client exports (gitignored)
```

---

## Notes

Academic research artifact. Real client data (`data/`) and secrets (`.env`) are
gitignored and never committed. Claim: **reproducibility across clients of one
ERP + preliminary transferability** — not generalizability.
