-- 010: study mode (unmoderated usability test) and workspace kinds.
--
-- kind: real (evaluated data) | study (usability copy) | test (injection
-- coverage demo) | replay (time-sliced replay, used for projection error).
-- Only 'real' counts in real-data metrics, dashboards and trends.
-- local_only: never copied to the hosted database (Companies D-G).
-- config_from: the workspace whose configuration (column names, privacy
-- masks, schema docs) a copy reuses, e.g. the study copy of Company A.

ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'real'
    CHECK (kind IN ('real', 'study', 'test', 'replay'));
ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS local_only BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS config_from TEXT;
UPDATE workspaces SET kind = 'replay', config_from = left(slug, length(slug) - 7)
    WHERE slug LIKE '%\_replay' ESCAPE '\' AND kind = 'real';

-- No names, no IP addresses: a participant is only a code (P00 = pilot).
CREATE TABLE IF NOT EXISTS study_participants (
    code         TEXT PRIMARY KEY,
    created_at   TIMESTAMP NOT NULL DEFAULT now(),
    consent_at   TIMESTAMP,
    started_at   TIMESTAMP,
    finished_at  TIMESTAMP,
    status       TEXT NOT NULL DEFAULT 'not_started'
                 CHECK (status IN ('not_started', 'in_progress', 'finished')),
    device_type  TEXT,          -- desktop | mobile | tablet
    browser      TEXT           -- name only, e.g. Chrome
);

CREATE TABLE IF NOT EXISTS study_task_events (
    id              SERIAL PRIMARY KEY,
    code            TEXT NOT NULL REFERENCES study_participants(code) ON DELETE CASCADE,
    task_no         INTEGER NOT NULL CHECK (task_no BETWEEN 1 AND 5),
    started_at      TIMESTAMP NOT NULL DEFAULT now(),
    ended_at        TIMESTAMP,
    self_status     TEXT CHECK (self_status IN ('done', 'couldnt')),
    system_verified BOOLEAN NOT NULL DEFAULT FALSE,
    answer_text     TEXT,
    UNIQUE (code, task_no)
);

-- What the app observed (detail opened + severity, decision saved, chat
-- sent, Excel/PDF downloaded); system_verified is derived from these.
CREATE TABLE IF NOT EXISTS study_events (
    id         SERIAL PRIMARY KEY,
    code       TEXT NOT NULL REFERENCES study_participants(code) ON DELETE CASCADE,
    event      TEXT NOT NULL,
    detail     JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT now()
);

-- Participants' accept / reject decisions: never written to issues.
CREATE TABLE IF NOT EXISTS study_decisions (
    id         SERIAL PRIMARY KEY,
    code       TEXT NOT NULL REFERENCES study_participants(code) ON DELETE CASCADE,
    issue_id   INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    decision   TEXT NOT NULL CHECK (decision IN ('accepted', 'rejected', 'considered', 'flagged')),
    note       TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    UNIQUE (code, issue_id)
);

ALTER TABLE chat_threads ADD COLUMN IF NOT EXISTS study_code TEXT;
CREATE INDEX IF NOT EXISTS idx_study_events_code ON study_events(code);
