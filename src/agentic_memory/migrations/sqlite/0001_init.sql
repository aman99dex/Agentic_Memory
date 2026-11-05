-- SQLite schema for local mode (ADR 0019): the same shelves as Postgres 0001-0005.
-- Times are ISO 8601 UTC text with a fixed width, so comparing the text compares the moments.
-- Lists of ids are JSON text; vectors are float32 BLOBs (384 x 4 bytes).

CREATE TABLE turns (
    id             INTEGER PRIMARY KEY,
    user_id        TEXT    NOT NULL,
    session_id     TEXT    NOT NULL,
    user_text      TEXT    NOT NULL,
    assistant_text TEXT    NOT NULL DEFAULT '',
    occurred_at    TEXT    NOT NULL,
    created_at     TEXT    NOT NULL,
    content_hash   TEXT    NOT NULL,
    namespace      TEXT,
    processed_at   TEXT,
    attempts       INTEGER NOT NULL DEFAULT 0,
    last_error     TEXT
);
CREATE UNIQUE INDEX turns_user_content_hash ON turns (user_id, content_hash);
CREATE INDEX turns_pending ON turns (user_id, occurred_at, id) WHERE processed_at IS NULL;

CREATE TABLE memories (
    id               INTEGER PRIMARY KEY,
    user_id          TEXT    NOT NULL,
    content          TEXT    NOT NULL,
    source_turn_ids  TEXT    NOT NULL,                 -- JSON list of turn ids
    valid_from       TEXT    NOT NULL,
    valid_to         TEXT,
    superseded_by    INTEGER REFERENCES memories (id) ON DELETE SET NULL,
    created_at       TEXT    NOT NULL,
    embedding        BLOB,
    embedding_model  TEXT,
    importance       REAL    NOT NULL DEFAULT 0 CHECK (importance >= 0),
    last_accessed_at TEXT,
    archived_at      TEXT,
    namespace        TEXT,
    kind             TEXT    NOT NULL DEFAULT 'fact' CHECK (kind IN ('fact', 'episode', 'profile')),
    session_id       TEXT,
    CHECK (valid_to IS NULL OR valid_to >= valid_from)
);
CREATE INDEX memories_user ON memories (user_id, kind);
CREATE UNIQUE INDEX memories_one_episode ON memories (user_id, session_id) WHERE kind = 'episode';
CREATE UNIQUE INDEX memories_one_profile ON memories (user_id, coalesce(namespace, ''))
    WHERE kind = 'profile';

-- Full-text index over memories.content: FTS5 with Porter stemming and real BM25 ranking.
-- 'content=' keeps one copy of the text; the triggers keep the index in step with the table.
CREATE VIRTUAL TABLE memories_fts USING fts5(
    content, content='memories', content_rowid='id', tokenize='porter unicode61'
);
CREATE TRIGGER memories_fts_insert AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts (rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER memories_fts_delete AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts (memories_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER memories_fts_update AFTER UPDATE OF content ON memories BEGIN
    INSERT INTO memories_fts (memories_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO memories_fts (rowid, content) VALUES (new.id, new.content);
END;

CREATE TABLE deletions (
    id         INTEGER PRIMARY KEY,
    user_id    TEXT    NOT NULL,
    deleted_at TEXT    NOT NULL,
    memories   INTEGER NOT NULL,
    turns      INTEGER NOT NULL
);

CREATE TABLE ingest_leases (
    user_id    TEXT PRIMARY KEY,
    holder     TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE api_keys (
    id         INTEGER PRIMARY KEY,
    tenant     TEXT NOT NULL,
    key_sha256 TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    revoked_at TEXT
);
