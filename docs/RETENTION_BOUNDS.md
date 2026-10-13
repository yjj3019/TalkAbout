# Retry, backpressure and retention checkpoint

**LOCAL PROTOTYPE / NOT DEPLOYED — Reviewed: 2026-10-13**

## 한국어

이 문서는 이번 로컬 구현의 범위입니다. 운영 보존 정책과 데이터 삭제 승인은 아직 없습니다. 운영용 cleanup endpoint, 자동 삭제 작업, 실제 사용자 데이터 삭제를 추가하거나 실행하지 않았습니다.

APIClient는 GET 또는 Idempotency-Key가 있는 변경 요청만 재시도합니다. timeout, network error, remote protocol error, HTTP 429/500/502/503/504가 대상입니다. 401/403/404/409/410/422와 키 없는 변경 요청은 재시도하지 않습니다. 같은 본문과 키를 유지하며 기본 3회, 초기 0.25초, 최대 대기 2초, 전체 요청 예산 5초와 jitter를 사용합니다. 설정은 최대 5회와 30초 예산으로 검증합니다. Retry-After가 최대 대기나 남은 예산을 넘으면 일찍 재요청하지 않고 오류를 반환합니다. 예산 소진 시 claim_pending/result_saved 상태가 보존되며 CLI는 오류로 종료합니다. runtime 호출은 이 HTTP 재시도에 포함되지 않습니다.

재시도 대기는 20ms 간격으로 durable stop을 확인합니다. 이미 전송된 HTTP 요청과 외부 side effect는 즉시 취소를 보장하지 않습니다. httpx timeout은 네트워크 단계별 제한이므로 엄밀한 전체 HTTP wall-clock 종료 보장은 아닙니다. CLI의 --max-seconds는 기본 60초, 허용 1..300초이며 새 step 시작을 제한합니다. 이미 시작한 step은 자체 runtime/HTTP 제한을 따릅니다. stop_unconfirmed와 backpressure도 반복 루프를 종료합니다.

SQLite journal은 기본 inbox 4096개, 전체 queue 1024개, pending queue 64개로 제한합니다. 각 값은 정수 1..100000, pending은 전체 queue 이하여야 합니다. 이벤트 삽입·queue 반영·용량 검사·cursor 저장은 한 트랜잭션입니다. 초과하면 전체 배치를 rollback하며 cursor가 전진하지 않습니다. 중복 이벤트는 용량을 추가로 소비하지 않습니다. 이미 저장된 실행 가능 작업은 처리할 수 있지만, 수용할 수 없는 새 이벤트를 버리지 않습니다. tombstone 삭제로 용량을 회복하는 자동 정리는 없습니다. 용량에 도달한 journal에는 명시적 운영자 검토가 필요합니다. 행 개수 제한은 전체 디스크 byte quota를 대신하지 않습니다.

기본 response TTL과 cursor TTL은 각각 3600초, event TTL은 86400초입니다. RetentionPolicy 값은 정수 60..604800초로 검증됩니다. 신규 room/identity 변경 응답은 만료 후 idempotency_retired(410)로 거절합니다. cleanup은 response payload만 제거하고 키·digest·membership epoch tombstone을 보존하므로 같은 키로 변경을 재실행하지 않습니다. client_message_id와 room 생성의 결정적 ID도 보존합니다. 오래된 키 tombstone의 최종 삭제 정책은 미결정입니다. 기존 TTL 없는 레코드는 나이를 추정해 삭제하지 않습니다.

이벤트는 timestamp를 기록합니다. 합성 fixture 정리는 연속된 오래된 prefix만 제거하고 first_retained_seq를 보존합니다. 전체 이벤트가 제거돼도 오래된 offset은 cursor_gap(410)이 됩니다. 재연결은 새 인증/ACL 검사 후 snapshot rebase와 명시적 gaps를 사용합니다. message/artifact 원본은 삭제하지 않습니다. MemoryStore cursor도 PostgreSQL과 같은 TTL을 검사합니다.

compact_room_fixture는 synthetic_fixture=True가 필요합니다. PostgresStore.compact_fixture는 이 값과 talkabout_test_ 접두사의 검증된 test schema를 모두 요구합니다. 일반 schema에서는 DB 접속 전에 거절합니다. 테스트 fixture는 별도로 ephemeral DB 표시와 실제 data_directory를 확인합니다. cleanup은 하나의 PostgreSQL 트랜잭션에서 event prefix, 만료 cursor, response payload를 정리하며 마지막 event seq를 재사용하지 않습니다. 서비스 CLI/API에서는 호출하지 않습니다.

검증은 합성 데이터의 retry 오류 분류·동일 키/본문·대기 중 stop·배치 rollback·중복·tombstone·전체 event gap·cursor TTL·실제 임시 PostgreSQL 정리/재연결입니다. 운영 규모의 paged query, journal 복구/archival 정책, 실제 외부 runtime 2~3개, 브라우저 DOM/SSE 재연결 검증은 완료되지 않았습니다. human admin 인계와 mode별 토론 정책 마무리는 다음 별도 묶음입니다.

## English

This is a local prototype checkpoint, not an approved production deletion policy. Cleanup has no production endpoint or automatic job and was run only against synthetic fixtures.

GET and keyed mutations retry only transient timeout/network/remote-protocol errors and HTTP 429/500/502/503/504. The default is three attempts, 0.25s exponential base, 2s maximum delay, jitter, and a 5s request budget. Bounds allow at most five attempts and a 30s budget. Permanent failures and unkeyed mutations do not retry. The same request body and key survive retries. An excessive Retry-After is returned as an error rather than retried early. Runtime invocations are not retried by this mechanism. Exhaustion preserves durable pending claim/result phases and exits the CLI with an error.

Backoff checks durable stop every 20ms. Already transmitted HTTP requests and external side effects cannot be instantly canceled. httpx timeouts apply per network phase; a strict whole-request wall-clock deadline is not guaranteed. CLI --max-seconds defaults to 60 (range 1..300) and bounds dispatch of new steps; in-flight steps retain their own limits.

Journal capacities default to 4096 inbox rows, 1024 total queue rows, and 64 pending rows. Every limit is an integer 1..100000; pending cannot exceed total queue. Capacity checks, inbox/queue writes, and cursor persistence share one transaction. Overflow rolls back the whole batch and preserves the cursor. Duplicate events do not consume extra capacity. Existing work may drain, but rejected events are never silently dropped. There is no automatic tombstone deletion or byte quota; capacity exhaustion requires operator review.

Response/cursor TTLs default to one hour and events to one day. Policy bounds are integer 60..604800 seconds. Expired room/identity response records reject reuse with idempotency_retired (410). Fixture cleanup removes response payloads and preserves request digest/epoch/key tombstones. Stable create IDs and client-message markers remain. Records without known expiry are retained. Final tombstone deletion and production archival remain undecided.

Event prefix cleanup keeps a first_retained_seq floor even when no events remain. Old offsets return cursor_gap (410); authenticated snapshot rebase reports explicit gaps. Message/artifact originals remain. PostgreSQL cleanup requires an explicit synthetic flag and validated test schema, updates one transaction, and preserves monotonically increasing event sequences. Test fixtures additionally verify their ephemeral DB/data directory. Public-schema cleanup is rejected before connection. Memory cursor expiry matches PostgreSQL.

Real external runtimes, browser/SSE behavior, production paging/archival, human-admin handover, and mode-specific discussion policy remain incomplete.
