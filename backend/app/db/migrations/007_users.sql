-- 007: simple login, roles, reviewer attribution and admin settings (Layer 3).

CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    username      TEXT      NOT NULL UNIQUE,
    password_hash TEXT      NOT NULL,          -- bcrypt
    role          TEXT      NOT NULL CHECK (role IN ('admin','reviewer')),
    created_at    TIMESTAMP NOT NULL DEFAULT now()
);

-- who made each triage decision (alongside the existing reviewed_at)
ALTER TABLE issues ADD COLUMN IF NOT EXISTS reviewed_by TEXT;

-- admin: "this rule is not applicable to us" per client. Suppressed rules
-- are still evaluated but recorded as disabled and raise no issues.
CREATE TABLE IF NOT EXISTS rule_suppressions (
    id            SERIAL PRIMARY KEY,
    source_client TEXT      NOT NULL,
    rule_key      TEXT      NOT NULL,
    reason        TEXT,
    disabled_by   TEXT,
    disabled_at   TIMESTAMP NOT NULL DEFAULT now(),
    active        BOOLEAN   NOT NULL DEFAULT TRUE,
    UNIQUE (source_client, rule_key)
);

-- admin-chosen settings, e.g. which .env-defined LLM option is active
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_by TEXT,
    updated_at TIMESTAMP NOT NULL DEFAULT now()
);
