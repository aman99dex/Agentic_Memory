-- 0005: background saving, scopes, episodes/profiles, tenant keys (ADRs 0020, 0021, 0023).

-- The queue (ADR 0020): a turn is stored first and turned into facts later by a worker.
-- attempts/last_error stop one bad turn from being retried forever.
ALTER TABLE turns
    ADD COLUMN namespace    text,
    ADD COLUMN processed_at timestamptz,
    ADD COLUMN attempts     int NOT NULL DEFAULT 0,
    ADD COLUMN last_error   text;
-- Every turn stored before this migration was processed when it arrived.
UPDATE turns SET processed_at = created_at;
CREATE INDEX turns_pending ON turns (user_id, occurred_at, id) WHERE processed_at IS NULL;
-- Nothing ever searched the turn vectors (ADR 0003 searches facts), so new turns skip them.

-- Scopes and kinds (ADR 0021). namespace NULL = true for this user everywhere.
ALTER TABLE memories
    ADD COLUMN namespace  text,
    ADD COLUMN kind       text NOT NULL DEFAULT 'fact'
        CHECK (kind IN ('fact', 'episode', 'profile')),
    ADD COLUMN session_id text;
-- One rolling summary per session, one profile per (user, namespace).
CREATE UNIQUE INDEX memories_one_episode ON memories (user_id, session_id) WHERE kind = 'episode';
CREATE UNIQUE INDEX memories_one_profile ON memories (user_id, coalesce(namespace, ''))
    WHERE kind = 'profile';

-- One worker per user at a time (ADR 0020). A lease that isn't renewed expires.
CREATE TABLE ingest_leases (
    user_id    text PRIMARY KEY,
    holder     text        NOT NULL,
    expires_at timestamptz NOT NULL
);

-- Tenant API keys (ADR 0023). Only a hash is stored; the key is shown once when created.
CREATE TABLE api_keys (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant     text        NOT NULL,
    key_sha256 text        NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz
);
