-- 0004: use-based importance and soft archiving (ADR 0016).
-- importance rises when a fact is recalled and decays nightly; last_accessed_at is the last recall.
-- archived_at hides a cold fact from search without deleting it (forget is the only hard delete).
ALTER TABLE memories
    ADD COLUMN importance       real NOT NULL DEFAULT 0 CHECK (importance >= 0),
    ADD COLUMN last_accessed_at timestamptz,
    ADD COLUMN archived_at      timestamptz;
