-- 008: company workspaces ("folders"). A workspace groups every import of
-- one organisation; its slug is the source_client key the rest of the
-- system already uses (trends, rules, feedback, privacy config), so
-- existing data needs no migration beyond a row per known client.

CREATE TABLE IF NOT EXISTS workspaces (
    id          SERIAL PRIMARY KEY,
    slug        TEXT      NOT NULL UNIQUE,   -- = uploads.source_client
    name        TEXT      NOT NULL UNIQUE,   -- any display name the user chooses
    description TEXT,
    created_by  TEXT,
    created_at  TIMESTAMP NOT NULL DEFAULT now()
);
