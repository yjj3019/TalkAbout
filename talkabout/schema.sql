CREATE TABLE IF NOT EXISTS principals (
  id text PRIMARY KEY, owner text NOT NULL, name_key text NOT NULL,
  handle text NOT NULL, payload jsonb NOT NULL,
  UNIQUE(owner, name_key), UNIQUE(owner, handle)
);
CREATE TABLE IF NOT EXISTS credentials (
  token_hash text PRIMARY KEY, principal_id text NOT NULL REFERENCES principals(id),
  credential_id text UNIQUE,
  scopes jsonb NOT NULL, expires_at double precision NOT NULL,
  room_id text, revoked boolean NOT NULL DEFAULT false
);
ALTER TABLE credentials ADD COLUMN IF NOT EXISTS credential_id text;
CREATE UNIQUE INDEX IF NOT EXISTS credentials_public_id ON credentials(credential_id);
CREATE TABLE IF NOT EXISTS rooms (
  id text PRIMARY KEY, owner text NOT NULL, last_event_seq bigint NOT NULL DEFAULT 0,
  payload jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  room_id text NOT NULL REFERENCES rooms(id), seq bigint NOT NULL,
  event_id text NOT NULL UNIQUE, payload jsonb NOT NULL,
  PRIMARY KEY(room_id, seq)
);
CREATE TABLE IF NOT EXISTS cursors (
  id text PRIMARY KEY, principal_id text NOT NULL, payload jsonb NOT NULL,
  expires_at double precision NOT NULL
);
CREATE TABLE IF NOT EXISTS human_passwords (
  principal_id text PRIMARY KEY REFERENCES principals(id), record jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS human_sessions (
  token_hash text PRIMARY KEY, principal_id text NOT NULL REFERENCES principals(id),
  expires_at double precision NOT NULL, csrf_hash text NOT NULL
);
