-- 005: feedback-based prompt customisation (Layer 2, in-context learning).
-- No training: past triage decisions and organisation notes are added to
-- the auditor prompt. The log records exactly which examples each table
-- audit saw, so LLM rejection rates can be compared across audits.

CREATE TABLE IF NOT EXISTS org_notes (
    id            SERIAL PRIMARY KEY,
    source_client TEXT      NOT NULL,
    note          TEXT      NOT NULL,
    created_by    TEXT,
    created_at    TIMESTAMP NOT NULL DEFAULT now(),
    active        BOOLEAN   NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_org_notes_client ON org_notes(source_client);

CREATE TABLE IF NOT EXISTS llm_prompt_log (
    id                 SERIAL PRIMARY KEY,
    upload_id          INTEGER   REFERENCES uploads(id) ON DELETE CASCADE,
    source_client      TEXT      NOT NULL,
    table_name         TEXT      NOT NULL,
    accepted_issue_ids INTEGER[] NOT NULL DEFAULT '{}',
    rejected_issue_ids INTEGER[] NOT NULL DEFAULT '{}',
    note_ids           INTEGER[] NOT NULL DEFAULT '{}',
    schema_context     BOOLEAN   NOT NULL DEFAULT FALSE,
    est_prompt_tokens  INTEGER,
    trimmed_examples   INTEGER   NOT NULL DEFAULT 0,
    created_at         TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_llm_prompt_log_upload ON llm_prompt_log(upload_id);
