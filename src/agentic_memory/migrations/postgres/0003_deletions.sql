-- 0003: proof that a hard delete happened, with counts only and never any content (ADR 0004, 0014).
CREATE TABLE deletions (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id    text        NOT NULL,
    deleted_at timestamptz NOT NULL DEFAULT now(),
    memories   int         NOT NULL,
    turns      int         NOT NULL
);
