# r4-verification — R3 P0/P1 반영 결과와 검증 증거

작성: 2026-11-03. 이 문서는 **구현·검증 기록**이며 제품 문서가 아니다. 정본은 `docs/STATUS.md`다.
범위: `r3-00-SYNTHESIS.md` §4.2(P0 5건)·§4.3(P1 4건)을 TalkAbout에 독자 구현한 결과.

## 1. 반영 내역 (라이선스 안전)

외부 저장소 코드는 복사·번역하지 않았다. 구조·프로토콜·측정 관점만 참고하고 새 코드로 작성했다.

| 항목 | 반영 위치 | 내용 |
| --- | --- | --- |
| P0-0 계측 | `tools/perf_snapshot.py`, `tests/test_r4_perf.py` | 쿼리 수·`snapshot` 호출 수·응답 바이트·SSE 유휴 스냅샷 수를 pytest 어서션과 JSON 스냅샷으로 고정 |
| P0-1 커넥션 풀 | `talkabout/pool.py`(신규), `store.py:272-306` | 독자 구현 `ConnectionPool`(상한·대기 시간·broken 폐기·commit/rollback). `PostgresStore.connect(pooled=True)`가 기본, `listener()`는 세션 상태 보존을 위해 `pooled=False` |
| P0-2 dirty 게이트 | `domain.schedule()` 반환값, `store.py:174-185`, `core.py:725-740` | 변경이 없으면 `_flush`/`NOTIFY`/`signal.set()` 생략. 유휴 스케줄러가 SSE 하트비트로 변하지 않음 |
| P0-3 gzip·캐시 | `api.py` 하드닝 미들웨어, `static_file()` | `GZipMiddleware(minimum_size=1024)`, 정적 자산 `Cache-Control: public, max-age=0, must-revalidate` + 약한 ETag/304, API는 `no-store` |
| P0-4 setup 단기 캐시 | `api.py` `setup_locked()` | 1초 TTL + setup 완료 시 즉시 무효화. 정적 자산 요청이 DB를 열지 않음 |
| P1-1 SSE 스냅샷 2→1 | `core.py:695,726-740` | 폴링용 `snapshot(include_events=False)`, 이벤트가 있을 때만 2차 ACL 재검사 |
| P1-2 이벤트 델타 | `store.py` `room_events()`, `core.py:726-733` | `seq > offset LIMIT 16` 델타 조회. 폴링마다 방 전체 이벤트(최대 2,049건)를 읽지 않음 |
| P1-3 bounds 증분 | `bounds.py` `snapshot_bounds(deep=False)`, `store.py._room()` | 읽기 전용 투영은 2MB 재직렬화 생략, 용량 상한 검사는 유지. 쓰기 경로는 기존 `_flush`가 전량 검증 |
| P1-4 웹 SSE 코얼레스 | `web/app.mjs`(`queueRoomUpdate` 등) | 빈 배치 무시, 100ms 디바운스+400ms 상한, `document.hidden` 지연 후 `visibilitychange` 1회 따라잡기 |
| 보조: 인덱스 | `schema.sql` `cursors_principal_id`·`cursors_expires_at` | 커서 만료 스윕·principal 조회의 전체 스캔 제거 |
| 보조: 워커 롱폴 | `worker.py` `poll()` | `wait_seconds=0` → `2`(서버 상한 10초 이내). 유휴 워커의 초당 4회 호출 제거 |
| 보조: 테스트 정리 | `tests/conftest.py` | `store.close()`로 풀 커넥션 누수 방지 |

## 2. 검증 결과 (이 샌드박스)

| 검증 | 결과 | 비고 |
| --- | --- | --- |
| Python 그린 서브셋 (`test_core`·`test_postgres`·`test_http_runtime`·`test_bounded_retention`·`test_scheduler_bounds`·`test_r4_perf`) | **68 passed / 2 warnings, 15.93s** (2026-11-03 최종 재실행) | 임시 PostgreSQL 18.6 픽스처 + `PYTHONPATH=.tmp-verify`(mkdir mode 제거 셰임) 필요. 셰임 없이 실행하면 pytest 종료 시 임시 디렉터리 정리에서 `PermissionError`(WinError 5)가 난다 |
| Python 전체 (`tools/run_safe_regression.py` child) | **1173 passed / 28 skipped / 54 failed** | 실패 54건은 전부 이 샌드박스 제약: `test_credential_vault.py` 53건(Windows ACL/DPAPI `SetFileSecurityW` WinError 5) + named-pipe 1건. 제품 로직 실패 아님 |
| Node 12개 파일 인프로세스(`node <file>`) | **247 passed / 0 failed** (최종 재실행: 12/12 파일 통과) | `node --test`는 자식 프로세스 파이프 차단(`spawn EPERM`, `docs/STATUS.md` §2.1에 문서화) |
| P0-3 gzip/ETag/304 회귀 테스트 | `tests/test_r4_perf.py:190-212` `test_static_assets_are_compressed_and_revalidated` | `/web/app.mjs`가 `content-encoding: gzip`, `cache-control: public…`, ETag 반환, `If-None-Match` 재요청이 **304** |
| `tools/docs_check.py` | **0 new / 0 known** | 문서 계약 통과 |
| `node --check talkabout/web/app.mjs` | **exit 0** | ESM 구문 |
| `tools/perf_snapshot.py` | exit 0 | `sse_idle.snapshot_calls_median = 1`(P1-1 적용 확인), `core.rooms()` 커넥션 1회, 유휴 `core.events()` 커넥션 8회 |

### 2.1 왜 표준 러너를 그대로 통과시키지 못했나

`tools/run_safe_regression.py`는 정상적인 CI 계약이지만, 이 세션의 Windows 샌드박스에서는
(a) `tempfile.mkdtemp`/`TemporaryDirectory`가 만든 디렉터리에 접근이 거부되고(명시적 mode 인자),
(b) `node --test`가 자식 프로세스 파이프를 열지 못한다(`spawn EPERM`). 둘 다 `docs/STATUS.md` §2.1과
`docs/SESSION_LOG.md`에 이미 기록된 환경 제약이다. 그래서 이번 검증도 **동일 pytest 플래그 + 로컬 전용
`sitecustomize`(mode 인자 제거)** + **Node 인프로세스 실행**으로 수행했다. CI의 표준 명령과 의미는 바꾸지 않았다.
`sitecustomize`는 `.tmp-verify/`에만 있고 제품·CI 경로에서 읽지 않는다.

## 3. 라이선스 준수

- handy(MIT), t3code, claude-mem(Apache-2.0)의 **코드를 복사·번역하지 않았다**. 새 파일은 독자 작성이며,
  인용은 보고서의 구조·관찰 수준에 그친다.
- `talkabout/pool.py`는 `psycopg_pool`을 쓰지 않고 `threading.Condition` 기반으로 새로 작성했다(외부 풀 코드 미사용).
- 새 의존성은 없다. 표준 라이브러리 + 기존 fastapi/psycopg만 사용한다.

## 4. 남은 것 (정직한 한계)

- **실 브라우저 Network 카운트 미측정**: P1-4의 절감은 코드 경로와 인프로세스 하네스로만 검증했다.
- **wall-clock 절대값 금지**: 이 머신은 연속 실행만으로 `/health`가 3.6→11.9ms(+230%) 흔들린다.
  회귀 게이트는 `connection_opens`·`snapshot_calls`·바이트 같은 결정적 카운터와 같은 머신 `--baseline` 비교로만 쓴다.
- **CI 표준 러너 통과는 이 샌드박스에서 불가**: 실제 GitHub Actions(정상 러너)에서는 `node --test`와
  임시 디렉터리가 정상 동작하므로 그대로 통과할 것으로 예상하지만, 이 세션에서 증명할 수는 없다.
- P2(캐시 무효화 정교화, paging/archival)는 착수하지 않았다.
