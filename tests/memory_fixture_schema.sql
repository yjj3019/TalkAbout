-- Synthetic tests only. Never apply this experimental schema to production.
-- The fixture validates the owned, ephemeral talkabout_test_* schema before use.
CREATE TABLE IF NOT EXISTS room_memories (
  room_id text NOT NULL REFERENCES rooms(id), id text NOT NULL,
  approved_seq bigint NOT NULL, payload jsonb NOT NULL,
  PRIMARY KEY (room_id, id)
);
CREATE INDEX IF NOT EXISTS room_memories_page ON room_memories(room_id, approved_seq, id);
CREATE TABLE IF NOT EXISTS room_memory_operations (
  room_id text NOT NULL REFERENCES rooms(id), actor_id text NOT NULL,
  operation text NOT NULL, key text NOT NULL, payload jsonb NOT NULL,
  PRIMARY KEY (room_id, actor_id, operation, key)
);
CREATE TABLE IF NOT EXISTS room_memory_revoke_index (
  room_id text NOT NULL REFERENCES rooms(id), memory_id text NOT NULL,
  payload jsonb NOT NULL, PRIMARY KEY (room_id, memory_id),
  FOREIGN KEY (room_id, memory_id) REFERENCES room_memories(room_id, id)
);
CREATE UNIQUE INDEX IF NOT EXISTS room_memory_revoke_key_unique
  ON room_memory_revoke_index (room_id, (payload->'receipt'->>'actor_id'), (payload->'receipt'->>'key_sha256'))
  WHERE payload->>'state' = 'used';
