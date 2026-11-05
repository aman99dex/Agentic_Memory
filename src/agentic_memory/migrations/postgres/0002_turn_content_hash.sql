-- 0002: a fingerprint per turn, so ingesting the same turn twice stores it once (Lesson 2.1).
ALTER TABLE turns ADD COLUMN content_hash text NOT NULL;
CREATE UNIQUE INDEX turns_user_content_hash ON turns (user_id, content_hash);
