-- 002: schema documentation (Layer 0 -> Layer 2 -> Layer 3).
-- The LLM drafts a description per table and column; a human verifies.
-- Docs are per client, not per upload, so verified work carries over to
-- later audits. The llm_* columns keep the original draft so the share
-- confirmed without edits can be measured by confidence level.

CREATE TABLE IF NOT EXISTS schema_doc_tables (
    id                    SERIAL PRIMARY KEY,
    source_client         TEXT      NOT NULL,
    table_name            TEXT      NOT NULL,
    table_description     TEXT,
    table_module          TEXT,
    llm_table_description TEXT,
    llm_table_module      TEXT,
    llm_raw               JSONB,
    missing_columns       JSONB     NOT NULL DEFAULT '[]',  -- profiled, absent from LLM output
    hallucinated_columns  JSONB     NOT NULL DEFAULT '[]',  -- in LLM output, not in profile
    model                 TEXT,
    generated_at          TIMESTAMP NOT NULL DEFAULT now(),
    verified_at           TIMESTAMP,
    verified_by           TEXT,
    UNIQUE (source_client, table_name)
);

CREATE TABLE IF NOT EXISTS schema_doc_columns (
    id                   SERIAL PRIMARY KEY,
    source_client        TEXT      NOT NULL,
    table_name           TEXT      NOT NULL,
    column_name          TEXT      NOT NULL,
    display_name         TEXT,
    description          TEXT,
    expected_domain      TEXT,
    business_rules       JSONB     NOT NULL DEFAULT '[]',
    confidence           TEXT      NOT NULL CHECK (confidence IN ('HIGH','MEDIUM','LOW','VERIFIED')),
    llm_display_name     TEXT,
    llm_description      TEXT,
    llm_expected_domain  TEXT,
    llm_business_rules   JSONB,
    llm_confidence       TEXT      CHECK (llm_confidence IN ('HIGH','MEDIUM','LOW')),
    edited_before_verify BOOLEAN,
    generated_at         TIMESTAMP NOT NULL DEFAULT now(),
    verified_at          TIMESTAMP,
    verified_by          TEXT,
    UNIQUE (source_client, table_name, column_name)
);

CREATE INDEX IF NOT EXISTS idx_schema_doc_columns_table
    ON schema_doc_columns(source_client, table_name);
