# Design Rationale & Results Reference

A single reference for writing Chapters 3 (design/development) and 4
(evaluation). It records what was built, **why** each decision was made,
and the actual measured results. British academic English; past tense
for completed work.

> Claim discipline: this system claims **reproducibility across clients
> of one ERP (SeefaERP) plus preliminary transferability** — never
> generalizability.

---

## 1. Problem & research question

Sri Lankan SMEs running SeefaERP accumulate poor data quality from manual
entry and bulk imports. Cross-table errors (e.g. an invoice referencing a
goods-receipt that does not exist) are invisible to single-file checks,
and existing tools (Great Expectations, Soda, Informatica, Talend) are too
expensive, too technical, or require database access. Target users are
non-technical: data-entry staff, accountants, stakeholders.

**Research question:** How can a hybrid rule-based profiling engine with
LLM-based explanation provide scalable, explainable data quality audit for
Sri Lankan SME SeefaERP systems?

---

## 2. Architecture — five layers (0–4)

Each layer is one Design Science Research build–evaluate increment and
runs end-to-end on a real client export before the next begins.

| Layer | Responsibility | Key modules |
|---|---|---|
| **L0 Ingestion & profiling** | Load CSV/Excel; infer types; column stats; PK discovery; cross-file FK discovery; stratified sampling; pseudonymization | `io/loader.py`, `profiler/*`, `privacy/pseudonymize.py` |
| **L1 Rules engine (Type 1)** | Six deterministic checks auto-derived from the profile, over all rows | `rules/*`, `rules/engine.py` |
| **L2 LLM auditor (Type 2)** | Contextual anomalies on profile + pseudonymized samples only | `llm/auditor.py`, `llm/run_audit.py` |
| **L3 Human triage** | Accept / reject / consider per issue; generates ground truth | `dashboard/app.py`, `db/repository.py` |
| **L4 Monitoring & scoring** | Severity-weighted DQ score + trend | `scoring/score.py` |

The business logic lives in the `app` package; the **Streamlit dashboard**
and a **FastAPI** layer (`app/api.py`) are two thin presentation layers
over the same service functions.

---

## 3. Key design decisions (design rationale)

Present this table in Chapter 3; every row is a defensible choice.

| Decision | Choice | Justification |
|---|---|---|
| Methodology | Design Science Research, 5 cycles | Build–evaluate increments map to the five layers |
| Detection taxonomy | Type 1 deterministic (rules, all rows) vs Type 2 contextual (LLM, samples) | Core contribution; their **complementarity** is a finding, not redundancy |
| No ML models | Rules + LLM only | Panel questioned ML; rules+LLM is simpler and more explainable |
| No ETL / warehouse | Direct CSV upload → simple PostgreSQL issue repository | Complexity without research value |
| **Headerless-safe, positional columns** | Detection uses `col_1..col_n`, never hard-coded names | Real SeefaERP exports arrive with **no header row**; keeps the engine schema-agnostic across clients |
| Consistency without names | **Structural mining** — ordering (A≤B holds ≥98%) and arithmetic (A×B≈C holds ≥95%) discovered empirically | Cannot key on "BillDate"/"DueDate" when columns are positional; same value-overlap method as FK discovery |
| DQ score | **Severity-weighted** `DQS = 100·Σ(w_d·s_d)`, `s_d = K/(K+penalty)` | A flat average lets trivial nulls swamp a critical cross-table break |
| Score basis | Excludes **rejected** issues | Score reflects genuine quality and rises as false positives are triaged out |
| LLM invocation | **Selective**: profile JSON + stratified samples + relationships only, never full tables | Scalability, privacy, token cost |
| LLM stack | One OpenAI-compatible client; base_url/model/key from `.env` | Provider is config, not code — Groq/Ollama/OpenRouter swap by editing `.env` |
| Pseudonymization | Mandatory before any cloud call; hybrid heuristic + per-client override; masks **both** samples and profile stats | Numeric columns are the audit subject and never auto-masked, so phone-as-int/geo need an override — a documented boundary |
| HITL triage | 3-state accept/reject/consider, timestamped | Doubles as the evaluation instrument (ground-truth generator) |
| Column aliasing | Display-only per-client map | Non-technical readability without compromising schema-agnostic detection |
| Frontend | Streamlit (React is first-to-cut) | Python-native, fast, adequate for non-technical triage |

---

## 4. Issue ontology (Contribution #4)

Every issue — whether rule- or LLM-sourced — is the tuple:

**(dimension, source, error_type, severity, fixability, status)**

- **dimension** ∈ {completeness, validity, consistency, uniqueness, timeliness, cross_table} (+ LLM `accuracy`, folded to validity for scoring)
- **source** ∈ {rule, llm} · **error_type** ∈ {type1, type2}
- **severity** ∈ {high, medium, low} · **fixability** ∈ {yes, no}
- **status** ∈ {flagged, accepted, rejected, considered}

Persisted in one `issues` table with the ontology enforced by SQL `CHECK`
constraints, so both detector sources are queried uniformly. Present the
ontology as a transferable conceptual artifact independent of the code.

---

## 5. Evaluation results

Reproduce with `python -m app.eval.run_eval` (and `--multiseed`),
`python -m app.eval.cross_client`. Rules metrics are deterministic; the
LLM-inclusive figures vary slightly per run.

### E1 — Synthetic injection (seeded, versioned `1.0.0`, seed 20260722)
- **Rules Type 1 recall = 1.0**, exceeding the ≥0.90 target; every Type 1
  dimension fully detected; rule precision (in-scope) ≈ 0.89.
- **Combined recall = 0.9383** (rules + LLM), exceeding ≥0.90.
- **Multi-seed (5 seeds): Type 1 recall mean 1.0 ± 0.0** — stable, not a
  fluke; rule precision 0.91 ± 0.05.

### E2 — Complementarity (headline)
- Rule-only **≈87.7%**, LLM-only **≈6.2%**, **both 0%**, missed ≈6.2%.
- `both = 0` shows the detectors catch **disjoint** error sets — direct
  evidence for the two-error-type taxonomy (no redundancy). LLM Type 2
  recall ≈0.33 at precision 1.0, reported honestly.

### E3 — Triage-derived (evaluation-in-use)
- Empirical precision = accepted/(accepted+rejected) per source (target
  ≥0.80); hallucination rate = rejected LLM / total LLM; ambiguity rate =
  considered / total. Module built and shown in the Scores tab; awaits
  real (or proxy) triage data to populate.

### E4 — Cross-client reproducibility (Hemas vs Ussawatte)
- **Dimension reproducibility 83.33%** (5/6 dimensions fire on both) —
  meets the ≥80% target. Rule-type overlap 71.43%. DQ 30.4 vs 44.06.
- The two non-overlapping rule types are **data-driven, not failures**:
  Hemas alone exhibits a qty×price arithmetic identity; Ussawatte alone
  has negative-valued amounts. The identical pipeline ran on both despite
  the `Dis_` naming and different column positions.

---

## 6. Limitations & threats to validity (state honestly)

- **Injection precision is "within scope"** — cleaning targets only the
  injected classes/columns on the real base, so pre-existing unlabelled
  errors elsewhere are excluded; precision is a lower bound.
- **Matching granularity** — detections are matched to injections at
  (table, column, dimension) level because detectors report aggregated
  issues (count + row sample), not per-row.
- **LLM hallucination** — measured via triage rejections; the phone-as-int
  false positive is a concrete example.
- **LLM non-determinism** — combined/complementarity figures vary per run.
- **Single-ERP scope**; **model-choice trade-off** (local privacy vs cloud
  capability; pseudonymization required for cloud); **human review still
  required** (cheaper than manual audit); **encrypted/blob columns out of
  scope**; **small SUS sample** (report n honestly).

---

## 7. Running the system

```
# one-off audit of a client directory (rules only; add --llm for Type 2)
python -m app.db.persist <data_dir> [--llm]

# triage + scores + upload UI
streamlit run dashboard/app.py

# HTTP API
uvicorn app.api:app --reload

# evaluation
python -m app.eval.run_eval            # E1/E2 (rules + LLM)
python -m app.eval.run_eval --multiseed   # E1 robustness
python -m app.eval.cross_client        # E4
```

Config (`.env`, gitignored): `LLM_BASE_URL/LLM_MODEL/LLM_API_KEY`,
`DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD`. Column aliases:
`config/aliases/<client>.json`.
