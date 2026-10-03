-- 006: chat clarification turns (Layer 3), kept for qualitative analysis.

CREATE TABLE IF NOT EXISTS chat_messages (
    id         SERIAL PRIMARY KEY,
    upload_id  INTEGER   NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
    issue_id   INTEGER   REFERENCES issues(id) ON DELETE SET NULL,
    username   TEXT,
    role       TEXT      NOT NULL CHECK (role IN ('user','assistant')),
    content    TEXT      NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chat_upload ON chat_messages(upload_id);
