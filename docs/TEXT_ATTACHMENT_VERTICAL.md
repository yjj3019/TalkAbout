# Text attachments: development implementation

Reviewed: **2026-10-17**. **IMPLEMENTED IN DEVELOPMENT / NOT DEPLOYED**.

이번 단계는 사람이 실제 UTF-8 `.txt`/`.md`를 올리고, 메시지에 불변 참조를 붙이고, 명시적으로 허용한 에이전트가 현재 작업의 제한된 내용을 읽는 경로입니다. 이미지·PDF·Office, OCR, 바이러스 검사, 능동 미리보기, MCP 파일 도구는 구현하지 않았습니다. 기존 foundation의 메모리 fake scanner는 이 경로에 연결하지 않습니다.

This development slice accepts actual UTF-8 `.txt`/`.md` bytes, publishes immutable message references, and lets explicitly consented agents read bounded excerpts under a current task lease. Images, PDF, Office, OCR, malware scanning, active previews and MCP file tools remain unimplemented. Foundation fake scanner state cannot unlock these downloads.

## Enablement and storage

The default application remains disabled for attachments. The explicit factory option is `create_app(store, text_storage=PrivateTextStorage(existing_private_directory))`. The development CLI also accepts `serve --text-attachment-root <existing-private-directory>`. Startup does not create this directory or migrate room data. No operating configuration, container volume, service, database or provider account was changed for this work.

The operator must provision a private directory outside the web root and set host access controls. Server-generated version IDs select immutable `.blob` objects; client names never select filesystem paths. The implementation refuses symlink/reparse paths, checks regular-file size and SHA-256 on every read, and does not expose storage paths. This is local private storage, not an object-store or distributed-lock implementation. The directory and its parents must remain under trusted operator control; this code does not defend against a privileged host administrator.

파일 삭제는 즉시 접근을 차단하는 tombstone입니다. 물리적 삭제·복원 UI·보존 만료·중단된 업로드와 orphan 정리는 후속입니다. 삭제 후 이미 내려받은 파일이나 외부 runtime에 전달된 내용은 회수할 수 없습니다. 방당 업로드 기록은 삭제·실패를 포함해 최대 16개이며 이 개발 단계에서는 재사용하지 않습니다.

Deletion immediately blocks access through a tombstone. Physical purge, restoration UI, retention expiry, abandoned upload and orphan cleanup remain future work. Previously downloaded bytes or text already delivered to an external runtime cannot be recalled. The development limit is 16 upload records per room, including deleted and failed records.

## Wire contract

All routes below are under `/api/v1`. Human mutations require the existing authenticated human session, same-origin CSRF checks, current room contribution permission and an `Idempotency-Key`. Production human bearer bypass remains disabled. Metadata and downloads recheck current object and room permissions.

| Route | Contract |
| --- | --- |
| `GET /capabilities` | Authenticated explicit feature state, extensions, 64 KiB limit, malware scan `not_run` |
| `POST /rooms/{room}/uploads` | Exact fields: name, size, sha256, media_type, readers, allow_agent_processing, ack_unscanned |
| `PUT /rooms/{room}/uploads/{file}/content` | Raw `application/octet-stream`; streaming size bound and actual UTF-8, format, size, SHA-256 checks |
| `POST /rooms/{room}/uploads/{file}/complete` | `expected_revision`; reread and verify private bytes before text readiness |
| `GET /rooms/{room}/files` | Current actor-visible metadata; no private object paths |
| `GET /rooms/{room}/files/{file}` | Same object visibility checks |
| `GET /rooms/{room}/files/{file}/text` | Human-only inert JSON text, at most 8,000 characters |
| `GET /rooms/{room}/files/{file}/download` | Human-only raw text, `nosniff`, `Content-Disposition: attachment`, generated safe filename |
| `PATCH /rooms/{room}/files/{file}/consent` | Expected revision, explicit reader set and external-agent processing consent |
| `DELETE /rooms/{room}/files/{file}` | Expected revision; immediate tombstone, retained private blob |
| `POST /rooms/{room}/tasks/{task}/files/{file}/read` | Agent-only current token-bound lease proof, generation, membership revision, attempt, character start/end, idempotency key |

State transitions are `awaiting_content → content_received → validated_utf8_text → deleted`. Incomplete uploads expire after 15 minutes. `validated_utf8_text` asserts format and integrity only. Every state retains malware scan **`not_run`** and malware safety **`not_asserted`**. There is no `clean` result and no fake scan promotion. The UI requires acknowledgment of this limitation before upload; unsafe or unsupported types fail without a successful upload result.

Messages carry at most four exact `{file_id, version_id, sha256}` references. Bytes and version identifiers never change. Metadata consent revisions can change; content edits require a new upload. Existing visible humans can reattach a file, but cannot change its uploader or original bytes. Read receipts represent delivery to an agent context, not proof that a provider received or understood it.

## Authorization and bounded context

Sharing defaults to no agent readers. The uploader must explicitly consent to processing by the listed accepted agents and their connected external models. Server-side reader membership epochs prevent a departed/reinvited agent from inheriting an earlier grant. New participants are not automatically added. An authenticated human owner/contributor must issue a fresh consent revision. Unknown file types or caller-supplied runtime labels never bypass consent.

Claims snapshot only references from visible recent messages. The claim reply alone carries a derived lease proof bound to the current platform credential, room, task, attempt, generation, member revision and claim key. Public snapshots and stored replay responses omit the proof. Reads recheck current credential, membership, consent, visible source message, task reference, active room, lease expiry, generation and attempt even for idempotent retries. Budgets are 8,000 characters per read, 24,000 per task, at most 32 distinct reads. The worker takes at most four 2,000-character excerpts and retains file/version/hash and line/character citations. Token/proof values are not model inputs.

The worker rechecks current visible files and room/task fencing after file HTTP reads and before runtime launch. A concurrent remote revocation can still occur after the last check; already delivered bytes cannot be recalled. Server completion fencing prevents stale results from being published. Runtime cancellation and these checks reduce races without promising atomic remote model revocation.

외부 텍스트는 비신뢰 자료입니다. HTML/Markdown을 렌더링하지 않고 `textContent`로 표시하며, worker는 인용된 자료로만 모델 문맥에 포함합니다. 파일 내용의 지시를 shell·도구·관리자 승인으로 실행하지 않습니다. 실제 provider 동의와 로컬 runtime의 별도 실행 승인은 계속 필요합니다.

## Database and migration

This first vertical uses the existing locked PostgreSQL room JSONB transaction, not the proposed normalized attachment tables. The additive payload version is `text_attachment_schema: 1`, with a `text_files` map. `migrate_selected_rooms(store, human_token, room_ids)` explicitly migrates a bounded selected list under current human room-control permission. Existing messages and room fields are preserved, repeated migration is idempotent, unsupported versions fail closed. New upload mutations also initialize the missing map within their transaction. Reads do not migrate data. No schema DDL or automatic operating migration is introduced.

PostgreSQL atomicity covers metadata, messages, events, budgets and idempotency. Filesystem writes cannot share that transaction: a failed DB commit can leave an inaccessible orphan; the deterministic immutable version key allows safe retry. Production cleanup and multi-host storage remain deferred. The earlier API/DB proposal remains a proposal for later general binary support.

## Verification scope

Tests use synthetic text and generated temporary storage. Real Chrome exercises KO desktop and EN mobile upload, unsupported format feedback, message references, a synthetic worker reading actual bytes, grounded reply, inert view, download, consent withdrawal and deletion. PostgreSQL tests use a newly initialized temporary loopback cluster only. Actual provider CLI/API calls, user account files, operational databases, deployment, malware scan, images/PDF/Office and production storage migration are **NOTRUN**.
