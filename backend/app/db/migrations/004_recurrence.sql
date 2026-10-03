-- 004: recurring-issue detection and trend projection (Layer 4).
-- History is simply issues across uploads for the same source_client;
-- no warehouse or separate knowledge base.

ALTER TABLE uploads ADD COLUMN IF NOT EXISTS data_as_of DATE;  -- export cut-off, when known

ALTER TABLE issues ADD COLUMN IF NOT EXISTS affected_row_count INTEGER;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS previous_issue_id  INTEGER REFERENCES issues(id) ON DELETE SET NULL;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS occurrence_count   INTEGER NOT NULL DEFAULT 1;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS trend_status       TEXT
    CHECK (trend_status IN ('new','worsening','improving','stable'));

CREATE INDEX IF NOT EXISTS idx_issues_previous ON issues(previous_issue_id);

-- One linear projection per recurring issue with >= 3 historical points.
-- actual_value is filled in when the next audit for the client arrives,
-- so prediction error can be measured (evaluation hook).
CREATE TABLE IF NOT EXISTS issue_projections (
    id              SERIAL PRIMARY KEY,
    issue_id        INTEGER   NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    upload_id       INTEGER   NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
    n_points        INTEGER   NOT NULL,
    slope           NUMERIC   NOT NULL,
    projected_value NUMERIC   NOT NULL,
    actual_value    INTEGER,
    actual_issue_id INTEGER   REFERENCES issues(id) ON DELETE SET NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT now(),
    evaluated_at    TIMESTAMP,
    UNIQUE (issue_id)
);
