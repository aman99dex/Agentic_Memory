-- 0001: the two shelves (ADR 0003). turns = the original words, memories = facts that point at them.

CREATE EXTENSION IF NOT EXISTS vector;

-- One round of conversation: what the user said + what the assistant replied.
CREATE TABLE turns (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id        text        NOT NULL,
    session_id     text        NOT NULL,
    user_text      text        NOT NULL,
    assistant_text text        NOT NULL DEFAULT '',
    occurred_at    timestamptz NOT NULL,               -- when the chat happened
    created_at     timestamptz NOT NULL DEFAULT now()  -- when we stored it
);

-- One fact. Never edited in place: when it stops being true we close it (ADR 0004).
CREATE TABLE memories (
    id              bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id         text        NOT NULL,
    content         text        NOT NULL,
    source_turn_ids bigint[]    NOT NULL,                -- the turns this fact came from
    valid_from      timestamptz NOT NULL,                -- became true
    valid_to        timestamptz,                         -- stopped being true; NULL = still true
    superseded_by   bigint      REFERENCES memories (id) ON DELETE SET NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

-- Meaning as 384 numbers (ADR 0006). The model name rides along so two models never get compared.
ALTER TABLE turns    ADD COLUMN embedding vector(384), ADD COLUMN embedding_model text;
ALTER TABLE memories ADD COLUMN embedding vector(384), ADD COLUMN embedding_model text;

-- HNSW: a map with shortcuts, so a search visits a few hundred pins instead of all of them (ADR 0001).
CREATE INDEX turns_embedding_hnsw    ON turns    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
CREATE INDEX memories_embedding_hnsw ON memories USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
