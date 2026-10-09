-- 003: persisted rule instances with plain-language descriptions (Layer 1 -> Layer 3).
-- One row per concrete check the engine derived from the profile for an
-- upload, including checks that found nothing (violation_count = 0).
-- what_text / why_text are deterministic template output, no LLM.

CREATE TABLE IF NOT EXISTS rules (
    id              SERIAL PRIMARY KEY,
    upload_id       INTEGER   NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
    source_client   TEXT      NOT NULL,
    rule_key        TEXT      NOT NULL,   -- template:table:column[:detail]
    template        TEXT      NOT NULL,   -- e.g. cross_table_fk_orphan (= issues.rule_id)
    category        TEXT      NOT NULL,   -- completeness|validity|consistency|uniqueness|timeliness|cross_table
    table_name      TEXT      NOT NULL,
    column_name     TEXT,
    parameters      JSONB     NOT NULL DEFAULT '{}',
    what_text       TEXT      NOT NULL,
    why_text        TEXT      NOT NULL,
    evidence        JSONB     NOT NULL DEFAULT '{}',
    violation_count INTEGER   NOT NULL DEFAULT 0,
    enabled         BOOLEAN   NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP NOT NULL DEFAULT now(),
    UNIQUE (upload_id, rule_key)
);

CREATE INDEX IF NOT EXISTS idx_rules_upload ON rules(upload_id);

-- issues.rule_id keeps the template name (used by eval/export);
-- rule_ref links a rule-sourced issue to its exact rule instance.
ALTER TABLE issues ADD COLUMN IF NOT EXISTS rule_ref INTEGER REFERENCES rules(id) ON DELETE SET NULL;
