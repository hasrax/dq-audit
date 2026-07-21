-- Issue repository schema (Layer 0-4 persistence).
-- Idempotent: safe to run on every startup.

CREATE TABLE IF NOT EXISTS uploads (
    id            SERIAL PRIMARY KEY,
    filename      TEXT        NOT NULL,
    source_client TEXT        NOT NULL,
    upload_date   TIMESTAMP   NOT NULL DEFAULT now(),
    row_count     INTEGER     NOT NULL DEFAULT 0,
    status        TEXT        NOT NULL DEFAULT 'profiled'
);

CREATE TABLE IF NOT EXISTS profiles (
    id             SERIAL PRIMARY KEY,
    upload_id      INTEGER NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
    table_name     TEXT    NOT NULL,
    column_name    TEXT    NOT NULL,
    data_type      TEXT,
    null_count     INTEGER,
    null_pct       NUMERIC,
    distinct_count INTEGER,
    min_val        TEXT,
    max_val        TEXT,
    mean_val       NUMERIC
);

CREATE TABLE IF NOT EXISTS relationships (
    id            SERIAL PRIMARY KEY,
    upload_id     INTEGER NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
    child_table   TEXT    NOT NULL,
    child_column  TEXT    NOT NULL,
    parent_table  TEXT    NOT NULL,
    parent_column TEXT    NOT NULL,
    overlap_pct   NUMERIC,
    confirmed     BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS issues (
    id             SERIAL PRIMARY KEY,
    upload_id      INTEGER NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
    table_name     TEXT    NOT NULL,
    column_name    TEXT,
    dimension      TEXT    NOT NULL CHECK (dimension IN
                     ('completeness','validity','consistency','uniqueness',
                      'timeliness','cross_table','accuracy')),
    source         TEXT    NOT NULL CHECK (source IN ('rule','llm')),
    error_type     TEXT    NOT NULL CHECK (error_type IN ('type1','type2')),
    severity       TEXT    NOT NULL CHECK (severity IN ('high','medium','low')),
    description    TEXT,
    recommendation TEXT,
    fixable        TEXT    CHECK (fixable IN ('yes','no')),
    status         TEXT    NOT NULL DEFAULT 'flagged'
                     CHECK (status IN ('flagged','accepted','rejected','considered')),
    user_note      TEXT,
    row_reference  JSONB,
    rule_id        TEXT,
    created_at     TIMESTAMP NOT NULL DEFAULT now(),
    reviewed_at    TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dq_scores (
    id                 SERIAL PRIMARY KEY,
    upload_id          INTEGER NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
    overall_score      NUMERIC,
    completeness_score NUMERIC,
    validity_score     NUMERIC,
    consistency_score  NUMERIC,
    uniqueness_score   NUMERIC,
    timeliness_score   NUMERIC,
    cross_table_score  NUMERIC,
    created_at         TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_issues_upload   ON issues(upload_id);
CREATE INDEX IF NOT EXISTS idx_issues_status   ON issues(status);
CREATE INDEX IF NOT EXISTS idx_profiles_upload ON profiles(upload_id);
