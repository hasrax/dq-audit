-- 009: chat threads. A user's conversations within one company, each
-- with any number of attached issues. Messages keep upload_id (the audit
-- that was open when they were written) for the qualitative analysis.

CREATE TABLE IF NOT EXISTS chat_threads (
    id            SERIAL PRIMARY KEY,
    source_client TEXT      NOT NULL,
    username      TEXT,
    title         TEXT      NOT NULL DEFAULT 'New chat',
    created_at    TIMESTAMP NOT NULL DEFAULT now(),
    updated_at    TIMESTAMP NOT NULL DEFAULT now(),
    archived      BOOLEAN   NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS chat_thread_issues (
    thread_id INTEGER NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
    issue_id  INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    PRIMARY KEY (thread_id, issue_id)
);

ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS thread_id INTEGER REFERENCES chat_threads(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_chat_threads_owner ON chat_threads(source_client, username);
