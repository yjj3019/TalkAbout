# R3 기준선 — TalkAbout 성능 기준선·병목 진단

- 대상: `D:\AI-Codding\Claude\TalkAbout` (Python 3.12 venv / Node v24.15.0 / Windows)
- 정본: `docs/ARCHITECTURE.md`, `docs/STATUS.md`(2026-11-01, 기준 커밋 `bc45c15`), 브리프 `analysis/BRIEF-R3.md`
- 이 문서는 **성능만** 다룹니다. 기능·보안 판정은 2라운드 문서를 따릅니다.
- 이 문서의 모든 수치는 `.tmp-perf/` 아래 스크립트로 **실제 실행**한 값이거나, 그렇지 않으면 "정적 분석"·"미측정"으로 표시했습니다.
- 금지 준수: 제품 코드·테스트 수정 없음, 서버 기동·부하테스트·외부 LLM 호출 없음. 임시 스크립트만 `.tmp-perf/`에 만들었습니다(부록 A).

---

## §0 요약 — 병목 표

등급: **A**=요청마다 상시 발생, **B**=동시접속·방 수에 비례, **C**=체감 지연 직결.
"측정값"이 비어 있으면 정적 분석만 한 항목입니다.

| # | 지점 | file:line | 증거(짧은 인용) | 영향 | 측정값 |
| --- | --- | --- | --- | --- | --- |
| B1 | 요청마다 **새 DB 커넥션** | `talkabout/store.py:252-256` | `return psycopg.connect(self.dsn, row_factory=dict_row, ...)` — 풀 없음. `self.connect()` 호출 24곳 | 요청 1건당 커넥션 3~6개. TCP+SCRAM 핸드셰이크가 매 요청 고정비 | 커넥션 수는 정적 계산(§2.1). ms는 **미측정**(실 DB 없음) |
| B2 | 미들웨어가 **모든 요청**에 `setup_required()` | `talkabout/api.py:57-61`, `talkabout/setup.py:40-47` | `locked = await run_in_threadpool(store.setup_required)` — 정적 자산(`/web/app.mjs`)에도 적용 | 정적 자산 요청에도 커넥션 1개 + `principals` 스캔 | `/health` 3.44ms, `/web/app.mjs` 5.50ms(인프로세스, DB 0) |
| B3 | SSE 폴링 루프가 **회당 스냅샷 2회** | `talkabout/core.py:695,736`, `talkabout/api.py:123-134` | `actor, rooms, _ = self.store.snapshot(token)` … `latest_actor, latest_rooms, _ = self.store.snapshot(token)` — 배치 1회마다 `core.events` 1회 호출(`api.py:131`) | 유휴 상태에서도 초당 4회 반복 → 방 4개 기준 초당 스냅샷 8회 | `core.events()` 1회: 1방 1.19ms / 4방 **5.04ms**(CPU만) |
| B4 | 스냅샷마다 **방 전량 이벤트 로딩** | `talkabout/store.py:355-362` | `SELECT payload FROM events WHERE room_id=%s ORDER BY seq LIMIT 2049` | 이벤트 1건 223.4B(실측) 가정 시 최대 2,049건 ≈ 457KB를 폴링마다 전송 | 이벤트 평균 223.4B/건 |
| B5 | **바이트 상한 검사 = 방 전체 JSON 직렬화** | `talkabout/bounds.py:5-14` | `require(len(json.dumps(room, ensure_ascii=False).encode("utf-8")) <= 2097152, ...)` — 읽기·쓰기마다 1회, Postgres 쓰기는 `_room`+`_flush`로 **2회** | 372KB 방 1.21ms, 1.32MB 방 **3.27ms**; 4방 스냅샷 3.12ms 중 대부분 | 403KB/ms(1.32MB 기준). `core.rooms()` 4.4ms 중 **71%** |
| B6 | **스케줄러가 0.5초마다 전 방에 무조건 UPDATE+NOTIFY** | `talkabout/api.py:28-37`, `talkabout/store.py:427-441`, `talkabout/store.py:365-373` | `while not stop.wait(0.5): store.tick_all()` → `self._flush(conn, room, row["last_event_seq"])` (변경 여부 검사 없음) | 활성 방 1개당 초당 2회 write 트랜잭션 + NOTIFY → **모든 SSE 구독자 깨움** | 방 수 × 2 write/s (정적). 실 DB 미측정 |
| B7 | 브라우저 **1 이벤트 → HTTP 6요청** | `talkabout/web/app.mjs:967,969` | `source.addEventListener('room_update',()=>{...refresh()})`, `refresh()`가 `api('rooms')`+`refreshAgents()`+`refreshHandovers()`+`refreshTextAttachmentSettings()`+`openRoom()`(+`interventions`) | 전량 스냅샷 4회 + 방 읽기 1회 + 인증 전용 1회, 커넥션 약 24개/이벤트 | §2.7 정적 계산 |
| B8 | 전체 재렌더·DOM 전면 재구축 | `talkabout/web/app.mjs:954,956,958,959` | `el('room-stage').innerHTML=stageMarkup(room)` / `el('messages').replaceChildren(...room.messages.map(...))` | 200개 메시지 = 노드 약 2,000개 재생성 + SVG 스테이지 innerHTML 파괴/재생성 | DOM 렌더 시간 **미측정**(DOM 없음) |
| B9 | 정적 자산 **압축·캐시 없음** | `talkabout/api.py:73`, `talkabout/api.py:110-113` | `response.headers["Cache-Control"] = "no-store"` — `GZipMiddleware` 없음 | 300KB(원본) vs 80KB(gzip9) 전송, 재방문마다 재다운로드 | app 126,541B + state 91,336B + css 56,739B + html 25,486B = **300,102B**, gzip9 **80,064B** |
| B10 | `cursors` 테이블 **무한 증가 + 인덱스 없음** | `talkabout/schema.sql:23-26`, `talkabout/store.py:420-425` | `SELECT count(*) AS n FROM cursors WHERE principal_id=%s` — `principal_id` 인덱스 없음. 만료 삭제는 fixture 전용(`store.py:227-250`) | SSE 메시지 1건마다 INSERT. 누적분 전체 스캔 | 크기 = 배달 횟수와 1:1(정적) |
| B11 | 워커 유휴 폴링 0.25초 + `wait_seconds=0` | `talkabout/cli.py:463-466`, `talkabout/worker.py:340-343` | `if result in {"waiting","retry_wait"}: until = ... + 0.25` / `{"wait_seconds": 0}` | 워커 1대당 초당 4회 API → 회당 `core.events`(B3) | 워커 1대당 4 req/s(정적) |
| B12 | 워커가 턴 중 다시 **방 전문 GET** | `talkabout/worker.py:518` | `room_now=self.client.request('GET',f'rooms/{rid}')` | 첨부 턴마다 full projection 1회 추가 | `GET room`(199 msg) 9.38ms 인프로세스 |
| B13 | `_worker_ready`가 후보마다 DB 커넥션 | `talkabout/core.py:576`, `talkabout/store.py:188-194` | `require(all(self._worker_ready(...) for pid, _ in candidates), ...)` — 내부에서 `self.connect()` | start 승인 1회에 후보 수만큼 커넥션 | 정적: 후보 N → 커넥션 N |
| B14 | 60fps SVG rAF + 무한 CSS 애니메이션 | `talkabout/web/app.mjs:518-521`, `talkabout/web/style.css:130,928-995` | `stageAnimation.raf = requestAnimationFrame(frame)` (매 프레임 `setAttribute`) / `animation: blink 6.4s ... infinite` 등 11개 | 저사양 기기 상시 합성 비용. 백그라운드 탭은 브라우저가 rAF를 제한 → 영향 **미측정** | frame당 actor ≤ 6회 `setAttribute` + viewBox 1회(정적) |

**한 줄 결론**: TalkAbout의 지연은 대부분 알고리즘이 아니라 **요청 모델**에서 나옵니다 — (a) 커넥션 풀 부재, (b) 방 전체를 읽고 전체를 직렬화하는 projection, (c) 0.5초 스케줄러 틱과 SSE 폴링의 무조건 재조회, (d) 그 위에 얹힌 브라우저 재조회 6중. 넷이 곱해져 유휴 상태에서도 CPU·DB·네트워크가 계속 돕니다.

---

## §1 기준선 수치

### 1.1 측정 환경

| 항목 | 값 |
| --- | --- |
| OS/런타임 | Windows, `.venv` Python, Node **v24.15.0** |
| DB | **없음** → `--postgres-config`가 없으면 `postgres_fixture`가 skip됩니다(`tests/conftest.py:42-44`). 실 DB 왕복 지연은 **미측정** |
| 샌드박스 제약 | `tempfile`/`pathlib` DACL 거부, `node --test` 자식 프로세스 `spawn EPERM` (STATUS.md:47에 동일 제약 기록됨) |
| 테스트 수집 | `1244 tests collected in 0.89s` (collect wall 2.56s) |

### 1.2 Python 기준선 — 3가지 층

| 층 | 명령 | 결과 |
| --- | --- | --- |
| **표준 러너(권위 기준선)** | `python -B tools/run_safe_regression.py` (마지막 기록: `.ci-results/2026-11-01-final/`) | **1188 passed / 35 skipped / 7 deselected, 172.90s** (exit 0), Node **243 pass / 0 fail, 2004.57ms** |
| 표준 러너(직전 기록) | `.ci-results/2026-11-01/` | 1176 passed / 35 skipped, **257.75s** → **같은 코드도 실행 간 1.49배 차이** |
| 이 샌드박스 전체 실행 | `.\.venv\Scripts\python.exe -B .tmp-perf/run_pytest_bench.py` | **716 passed / 480 failed / 48 skipped**, wall **221.5s** (call 75.2s + setup 1.1s) |
| **재현 가능한 그린 서브셋** | 아래 명령 | **200 passed / 2 skipped in 11.17s**(2회차 11.09s, wall 12.5s) |

실패 480건의 원인 분류(자동 분류, `.tmp-perf/summarize_cases.py`):

| 원인 | 건수 | 근거 |
| --- | --- | --- |
| `PermissionError` (tempfile/pathlib DACL) | **402** | 샌드박스 제약. STATUS.md:47이 기록한 것과 동일 |
| `AdapterError` (`agent_runtime.py:182`) | 25 | 실행기/워크스페이스 준비 실패(같은 제약 계열) |
| `test_work_profile_events.py` 단언 | 53 | 위 제약의 파급으로 추정 — **개별 확인 안 함** |

> 주의: **이 환경의 실패 수는 회귀 지표가 아닙니다.** STATUS.md:52도 같은 취지로 "실패 수 자체를 회귀 지표로 쓰지 않는다"고 기록했습니다. 성능 회귀 기준선은 §1.2의 그린 서브셋을 쓰십시오.

그린 서브셋(재현 명령):

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider `
  tests/test_participation.py tests/test_room_interfaces.py tests/test_trial_http_diagnostics.py `
  tests/test_work_evidence_server.py tests/test_resolve_uncertain.py tests/test_room_diagnostics.py `
  tests/test_replay_acl_projection.py tests/test_storage_health.py
# -> 200 passed, 2 skipped in 11.1s
```

### 1.3 가장 느린 테스트 상위 10 (이 샌드박스 실측, `call` 단계)

| 초 | 테스트 | 비고 |
| --- | --- | --- |
| 13.287 | `tests/test_sessions.py::test_login_throttle_is_not_reset_by_a_key_flood` | 로그인 스로틀 대기(의도된 sleep) |
| 3.474 | `tests/test_http_runtime.py::test_http_errors_map_to_fixed_codes` | HTTP timeout |
| 3.344 | `tests/test_sessions.py::test_https_cookie_and_login_throttle` | 대기 |
| 2.585 | `tests/test_deployment_readiness.py::test_fixture_or_unavailable_db_is_not_ready` | DB 연결 실패 대기 |
| 2.520 | `tests/test_http_runtime.py::test_invalid_payloads_fail_closed` | |
| 2.169 | `tests/test_initial_setup.py::test_scheduler_waits_for_owner` | 스케줄러 0.5s 틱 × 대기 |
| 1.804 | `tests/test_http_runtime.py::test_timeout_is_bounded` | |
| 1.704 | `tests/test_initial_setup.py::test_locked_api_origin_csrf_gui_and_normal_login` | |
| 1.316 | `tests/test_initial_setup.py::test_expiry_limit_reissue_and_existing_owner` | |
| 1.208 | `tests/test_http_runtime.py::test_probe_requires_configuration` | |

파일별 누적(call 초) 상위: `test_sessions.py` 18.2s(7건) · `test_http_runtime.py` 10.4s(9건) · `test_initial_setup.py` 10.3s(11건) · `test_work_evidence_server.py` 8.9s(25건). **대부분 의도된 sleep/타임아웃**이며 순수 CPU 병목이 아닙니다.

### 1.4 Node 기준선

`node --test`는 이 샌드박스에서 불가(`spawn EPERM`, 위와 동일 제약). 대신 파일별 인프로세스 실행(STATUS.md:47이 쓴 방식과 동일):

```powershell
foreach ($f in Get-ChildItem tests\*.mjs | Sort-Object Name) { node $f.FullName }
# -> 12개 파일, 247 passed / 0 failed, 내부 duration 합 504ms, 프로세스 기동 포함 wall 3.65s
```

표준 러너 기준 Node는 **243 pass / 0 fail / 2004.57ms**(`.ci-results/2026-11-01-final/node.log`). 파일 수는 12개로 동일하며 4건 차이의 원인은 **미확인**(이 샌드박스 실행은 `node --test` 불가로 집계 방식이 다름).

### 1.5 웹 자산 크기·파싱

| 파일 | 원본 B | gzip9 B | 비율 |
| --- | --- | --- | --- |
| `talkabout/web/app.mjs` | 126,541 | 34,144 | 0.270 |
| `talkabout/web/state.mjs` | 91,336 | 29,223 | 0.320 |
| `talkabout/web/style.css` | 56,739 | 11,206 | 0.198 |
| `talkabout/web/index.html` | 25,486 | 5,491 | 0.215 |
| **합계** | **300,102** | **80,064** | 0.267 |

- 모듈 파싱 비용은 무시 가능: `vm.SourceTextModule.link` 기준 state 0.11ms / app 0.12ms, state `link+evaluate` **1.07ms**.
- `state.mjs`는 `labels`(ko+en 동시) 리터럴이 대부분입니다(`talkabout/web/state.mjs:1,95`). **현재 두 언어를 항상 둘 다 로드**합니다.
- JSON 파싱(합성 `/api/v1/rooms` 응답): 152,806B → **0.092ms**. 즉 **직렬화 크기가 브라우저 CPU 병목은 아닙니다**. 문제는 전송량과 재렌더입니다.

### 1.6 인프로세스 HTTP 지연 (TestClient + `MemoryStore`, DB 접속비 0)

하네스 바닥값: 인증 없는 `/health` **3.44ms**(이 값 이하로는 측정 불가).

| 워크스페이스 방 수 | 마지막 방 메시지 | `GET /api/v1/rooms` | 응답 B | `GET /api/v1/rooms/{id}` | 응답 B | `GET /agents/me/events` |
| --- | --- | --- | --- | --- | --- | --- |
| 2 | 1 | 4.30ms | 4,270 | 4.09ms | 1,786 | 4.13ms |
| 3 | 10 | 4.60ms | 11,532 | 4.31ms | 7,261 | 4.67ms |
| 4 (쿼터 상한, `store.py:139`) | 40 | **5.91ms** | 37,081 | 5.26ms | 25,548 | 6.04ms |
| `POST /rooms/{id}/messages` (메시지 40개 방) | — | **5.71ms** | — | | | |

참고: 방 1개·메시지 199개일 때 `GET /api/v1/rooms`는 **9.60ms / 124,011B**(같은 하네스, 1차 실행).

재현: `.\.venv\Scripts\python.exe -B .tmp-perf/bench_api.py` (`## L0`, `## J`, `## K`)

### 1.7 순수 도메인 마이크로벤치 (MemoryStore, DB·HTTP 제외)

| 메시지 수 | `core.rooms()` | `core.read()` | `room_snapshot()` | `core.events()` 1회 |
| --- | --- | --- | --- | --- |
| 1 | 0.12ms | 0.12ms | 0.26ms | 0.21ms |
| 40 | 0.71ms | 0.73ms | 1.43ms | 1.21ms |
| 100 | 1.73ms | 1.72ms | 3.18ms | 3.03ms |
| 199 | **3.22ms** | **3.23ms** | **6.02ms** | **5.58ms** |

`room_bounds()`(바이트 상한) 단독: 6.5KB 0.028ms → 78KB 0.253ms → 189KB 0.603ms → **372KB 1.213ms**. 1.32MB 방: dumps 2.68ms + encode 0.67ms = **3.27ms**, 처리량 **403.5KB/ms**.

방 4개(각 366KB) 스냅샷: `snapshot_bounds()` **3.12ms** / `core.rooms()` **4.40ms** → **약 71%가 상한 검사**.

재현: `bench_domain.py`(`## A~E`), `bench_scale.py`(`## F~I`)

---

## §2 병목 상세

### 2.1 [A] 커넥션 풀 부재 — 요청 1건당 커넥션 3~6개

**증거**: `talkabout/store.py:252-256`

```python
def connect(self, autocommit=False):
    import psycopg
    from psycopg.rows import dict_row
    options = {"options": "-csearch_path=" + self.schema} if self.schema else {}
    return psycopg.connect(self.dsn, row_factory=dict_row, autocommit=autocommit, **options)
```

`requirements.lock.txt`에 `psycopg==3.3.6`만 있고 `psycopg_pool`이 **없습니다**. `talkabout/` 전체에서 `ConnectionPool`을 찾지 못했습니다. `self.connect()` 호출은 `store.py`에 **24곳**입니다.

`GET /api/v1/rooms` 한 번의 커넥션 경로(정적 계산):

| 순서 | 위치 | 커넥션 |
| --- | --- | --- |
| 1 | `api.py:61` 미들웨어 `setup_required()` → `setup.py:43` | 1 |
| 2 | `api.py:89` `token()` → `store.auth` → `store.py:272-274` | 1 |
| 3 | `api.py:223` `dispatch()`의 `store.auth(credential)` | 1 |
| 4 | `api.py:297` `core.rooms` → `store.snapshot` → `store.py:405` | 1 |
| | **합계** | **4** |

SSE 이벤트 1건에 브라우저가 6요청을 보내는 경우(B7): 미들웨어 6 + `token()` 6 + `dispatch()` 6 + 스냅샷 4 + `read_transaction` 1 + `save_cursor` 1 ≈ **24커넥션**.

**영향**: 매 요청이 TCP 연결 + PostgreSQL startup + SCRAM 인증을 반복합니다. 커넥션 생성 비용 자체는 DB 종류·인증 방식·네트워크에 좌우되므로 **ms 수치를 측정하지 않았습니다(미확인)**. 다만 요청당 커넥션 수 4→1~2로 줄이는 것만으로 고정비가 커넥션 1개분까지 내려갑니다.

**재현**: `Select-String -Path talkabout\store.py -Pattern 'self\.connect\('` → 24건. `Select-String -Path requirements.lock.txt -Pattern 'psycopg'`

**예상 개선폭**: 커넥션 수 **4→1**(일반 조회), **24→6**(SSE 갱신 1회). 지연 개선폭은 커넥션 1개 비용 × 3~18에 비례하므로 실 DB에서 측정해야 합니다(§5).

### 2.2 [A] 미들웨어가 정적 자산에도 DB를 두드림

**증거**: `talkabout/api.py:57-61` — `@app.middleware("http")`의 첫 줄이 `store.setup_required`입니다. `talkabout/setup.py:40-47`은 Postgres에서

```python
with self.connect() as conn:
    if conn.execute("SELECT id FROM principals WHERE payload->>'kind'='human' LIMIT 1").fetchone():
        return False
```

를 실행합니다. `payload->>'kind'`를 위한 인덱스가 `talkabout/schema.sql`에 **없습니다**(§2.6) → `principals` 스캔.

**측정**: `/health` 3.44ms, `/web/app.mjs` 5.50ms(DB 0인 MemoryStore 기준 = 하네스 오버헤드 포함). 실제 DB에서는 여기에 커넥션 1개가 더 붙습니다.

**재현**: `bench_api.py`의 `## L0`.

**예상 개선폭**: 정적 자산·`/health`의 DB 접근 **1→0**. 초기 로드 5요청(/, app, state, css, favicon)에서 커넥션 **5→0**.

### 2.3 [B] SSE 폴링이 회당 스냅샷 2회 + 유휴에도 초당 4회

**증거**: `talkabout/core.py:690-760`

- `:693` `with self.store.listener() as wait:` — LISTEN 진입
- `:695` `actor, rooms, _ = self.store.snapshot(token)` — 1차 전량 스냅샷
- `:736` `latest_actor, latest_rooms, _ = self.store.snapshot(token)` — 응답 직전 ACL 재검사용 **2차 전량 스냅샷**
- `:757` `self.store.save_cursor(next_cursor, payload)` — 매 응답마다 커서 INSERT
- `:760` `wait(min(0.25, max(0, end - time.monotonic())))` — 이벤트가 없으면 0.25초 대기 후 **다시 처음부터**

`talkabout/api.py:131`은 `min(10, ...)`를 `wait_seconds`로 넘기므로 각 배치가 최대 10초 threadpool 점유를 목표로 합니다.

**측정**: `core.events()` 1회(인박스, 4방 40메시지) **5.04ms** — DB 비용 0, 순수 CPU. 유휴 0.25초 주기면 SSE 클라이언트 1개당 CPU **약 20ms/s**, 스냅샷 **8회/s**.

**재현**: `bench_scale.py`의 `## I`; 소스 확인 `read talkabout/core.py 690-760`.

**예상 개선폭**: 스냅샷 **2→1**회(-50%), 유휴 시 이벤트 히스토리 재로딩 **0회** 목표(§2.4) → 유휴 SSE 1개당 CPU **5.04ms→약 0.5ms 수준(추정, 이벤트 증분 조회 가정)**.

### 2.4 [B] 스냅샷이 방 전량 이벤트를 메모리로 올린다

**증거**: `talkabout/store.py:355-362`

```python
# shortcut: bounded P0 rooms load their event history; switch to paged event queries before raising room limits.
room["events"] = [r["payload"] for r in conn.execute("SELECT payload FROM events WHERE room_id=%s ORDER BY seq LIMIT 2049", (row["id"],)).fetchall()]
```

주석이 스스로 "paged event queries 전까지는 shortcut"이라고 밝힙니다. 그런데 이 벡터로 인해:

- `core.py:732` `selected = [e for e in history if e["seq"] > offsets[rid]][:16]` — **필요한 건 최대 16건**인데 전량(최대 2,049건)을 올린 뒤 Python에서 필터링.
- `core.py:726-731` `history[0]["seq"]`, `offsets[rid] < floor - 1` 판정에 첫/마지막만 씁니다.
- `store.py:376-383` `transaction`: `FOR UPDATE`로 **방 row lock을 잡은 뒤** `_room()`을 호출하므로, 이벤트 전량 로딩 시간이 락 보유 시간에 포함됩니다(정적; 실 DB 측정 **미확인**).

**측정**: 이벤트 평균 **223.4B/건**(40메시지 방 46이벤트 10,277B). 이벤트 2,048건이면 페이로드만 **약 457KB**.

**재현**: `bench_domain.py`의 `## E`.

**예상 개선폭**: 폴링 1회당 이벤트 전송 바이트 **전량→신규분(대개 0)**. 유휴 시 네트워크·역직렬화 **약 457KB→0**.

### 2.5 [A] 바이트 상한 검사가 읽기·쓰기마다 방 전체 JSON을 직렬화

**증거**: `talkabout/bounds.py:5-14`

```python
def room_bounds(room):
    require(len(room.get("events", [])) <= 2048, "event_capacity", 429)
    require(len(json.dumps(room, ensure_ascii=False).encode("utf-8")) <= 2097152, "room_byte_capacity", 413)

def snapshot_bounds(rooms, principals):
    ...
    for room in rooms.values():
        room_bounds(room)
```

호출 위치: `store.py:361`(`_room`), `store.py:367`(`_flush`) → **Postgres 쓰기 1회 = dumps 2회**. `store.py:412`(`snapshot`) → 방마다 1회. `core.py`도 프로젝션마다 호출. 그 직전에 `store.py:357` `copy.deepcopy(row["payload"])`로 **방 전체 복사**가 한 번 더 있습니다(실측 `deepcopy`: 372KB 방 2.61ms, `bench_domain.py` `## C`).

**측정**: 372KB 방 **1.21ms**, 1.32MB 방 **3.27ms**, 처리량 **403.5KB/ms**. 4방(각 366KB) `snapshot_bounds` **3.12ms**는 `core.rooms()` 4.40ms의 **71%**.

**재현**: `bench_domain.py` `## B`, `bench_scale.py` `## G`,`## H`.

**예상 개선폭**: 유효 한도가 2MB이고 상한 검사는 "초과 여부"만 필요하므로, 증분 카운터(쓰기 시 delta만 반영)로 바꾸면 **쓰기 2회 3.27ms→약 0ms**, 4방 스냅샷 **3.12ms→0**. 절감 추정 = 측정값 그대로(검사 자체를 제거할 경우).

### 2.6 [B] 인덱스 부재 (`talkabout/schema.sql`)

전체가 36줄이고, 인덱스는 PK/UNIQUE 외에 **하나도 없습니다**(`schema.sql:1-36`).

| 쿼리 | 위치 | 필요한 인덱스 | 현재 |
| --- | --- | --- | --- |
| `WHERE principal_id=%s` (count) | `store.py:424` | `cursors(principal_id)` | **없음 → 순차 스캔** |
| `WHERE expires_at<=%s` (정리) | `store.py:249`(fixture 전용) | `cursors(expires_at)` | 없음 |
| `WHERE owner=%s ORDER BY id` | `store.py:408` | `rooms(owner)` | 없음 |
| `WHERE payload->>'state'='active'` | `store.py:430` | 표현식 인덱스 | 없음 |
| `WHERE payload->>'kind'='human'` | `setup.py:31,44`, `store.py:221,283` | 표현식 인덱스 | 없음 |
| `WHERE id=%s AND owner=%s AND payload->>'kind'='agent'` | `store.py:200` | PK로 좁혀지므로 필수 아님 | 없음(동작엔 무해) |
| `WHERE room_id=%s ORDER BY seq` | `store.py:360` | PK `(room_id,seq)` 사용 | 있음(PK) |

**영향**: `cursors`는 SSE 배달마다 1행씩 늘고(`store.py:757`), 만료 정리는 fixture 전용(`store.py:227-250`, `retention.py:31-43`)이므로 **프로덕션에서 삭제 경로가 없습니다**. 따라서 `store.py:424`의 `count(*)`는 누적 SSE 배달 횟수에 비례해 느려집니다(정적 결론, 실 DB 측정 **미확인**).

**재현**: `read talkabout/schema.sql`, `grep 'count(\*)' talkabout/store.py`.

**예상 개선폭**: `count(*)` 순차 스캔 → 인덱스 전용 인덱스 스캔(정성). 정리 배치 추가 시 테이블 크기 선형 유지.

### 2.7 [C] 브라우저 1 이벤트 → HTTP 6요청 (증폭)

**증거**: `talkabout/web/app.mjs:967` `source.addEventListener('room_update',()=>{...refresh()})` → `:969`

```js
async function refresh(){const epoch=++listEpoch;try{const rooms=await api('rooms',{access:'workspace'});...
  const optional=await Promise.allSettled([refreshAgents(),refreshHandovers(epoch),refreshTextAttachmentSettings()]);
  ... if(view.selected){ ... await openRoom(view.selected,false,false); }
```

서버는 `api('rooms')`(전 방 projection) + `agents`(`identity.py:44` 스냅샷) + `admin/handovers`(`core.py:239` 스냅샷) + `capabilities` + `rooms/{id}`(`core.py:273` `read`) + `rooms/{id}/interventions`(`core.py:784` 스냅샷)을 각각 새로 계산합니다. **전량 스냅샷 4회 + 방 읽기 1회 + 인증만 하는 요청 1회**.

여기에 B6(스케줄러 0.5초 NOTIFY)이 겹치면 **아무 내용 변화가 없어도** SSE가 깨어나 같은 6요청이 반복됩니다.

**재현**: `grep` 또는 `read talkabout/web/app.mjs 950-975`.

**예상 개선폭**: 이벤트 1건당 서버 스냅샷 **4→1**(이벤트 델타 전송) 목표. 커넥션 24→4~5.

### 2.8 [A] 재렌더·DOM 재구축

| 위치 | 코드 | 비용 |
| --- | --- | --- |
| `app.mjs:954` | `el('room-stage').innerHTML=stageMarkup(room)` + `startStageAnimation(room)` | SVG 스테이지 전체 파괴/재생성 + rAF 루프 재시작(구 루프 `cancelAnimationFrame`) |
| `app.mjs:956` | `el('messages').replaceChildren(...room.messages.map(m=>messageNode(room,m)))` | 메시지마다 약 10개 노드(`app.mjs:97`) |
| `app.mjs:958,959` | tasks/artifacts 동일 | |
| `app.mjs:883` | `setInterval(...,10000)` → `refresh()` | 탭 앞에 있으면 10초마다 6요청 |

**측정**: DOM 조작 시간은 **미측정**(헤드리스 DOM 없음). 상한만 산출: 메시지 200개 × 약 10노드 = **약 2,000노드/갱신**(정적).

**예상 개선폭**: 변경분만 갱신(키 기반 patch)하면 스테이지 재생성 0회, 메시지 목록 재구축 O(신규)로 감소. 정량은 브라우저 프로파일링으로 확정 필요(**미확인**).

### 2.9 [A] 정적 자산 압축·캐시 없음

**증거**: `api.py:73` `response.headers["Cache-Control"] = "no-store"` — `asset()`(`api.py:110-113`)의 `FileResponse`에도 동일 적용. `GZipMiddleware`를 `talkabout/`에서 **찾지 못했습니다**.

**측정**: 총 300,102B → gzip9 **80,064B**(26.7%). gzip9은 최적 압축이고 실제 협상 압축은 이보다 조금 큽니다(추정).

**예상 개선폭**: 첫 로드 전송량 **300KB→약 80KB(-73%)**, 재방문은 `no-store` 해제 + ETag/304 시 **가능 0바이트**. 응답 헤더 `no-store`는 보안 의도(민감 응답)일 수 있으므로 `/web/*` 자산에만 예외를 두는 것이 안전합니다.

### 2.10 [A] 워커 유휴 폴링

**증거**: `talkabout/cli.py:463-466`

```python
if result in {"waiting", "retry_wait"}:
    until = min(deadline, time.monotonic() + 0.25)
    while time.monotonic() < until and not journal.stop_requested():
        time.sleep(min(0.02, max(0, until - time.monotonic())))
```

`talkabout/worker.py:340-343`의 `poll()`은 `{"wait_seconds": 0}`으로 **즉시 반환**을 요청합니다(서버 long-poll 미사용). 결과적으로 워커 1대당 초당 약 4회 `GET /agents/me/events` → 서버는 B3의 2회 스냅샷을 수행.

관련 상수: `worker.py:29` `STOP_POLL_SECONDS = 0.2`, `worker.py:281` `heartbeat_interval=3, runtime_timeout=30`, `worker.py:603` `finished.wait(STOP_POLL_SECONDS)`.

**재현**: `read talkabout/cli.py 455-470`, `read talkabout/worker.py 336-356`.

**예상 개선폭**: `wait_seconds`를 1~5초로 올리고 서버 `core.events`의 대기-후-재스냅샷 구조를 1회 스냅샷으로 바꾸면 워커 1대당 스냅샷 **8회/s→0.2~1회/s(-90% 이상)**.

### 2.11 [B] 워커가 턴 중 방 전문을 다시 읽음

**증거**: `talkabout/worker.py:518` `room_now=self.client.request('GET',f'rooms/{rid}')` — 첨부 파일 턴에서 fencing 재확인용. 응답은 `public_room` 전체(메시지·아티팩트·태스크 포함)입니다. 199메시지 방에서 `GET room` 9.38ms(인프로세스), 응답 124,008B.

**예상 개선폭**: 필요한 필드(task attempt/generation/lease/state, room state)만 주는 경량 엔드포인트 → 응답 **124KB→1KB 미만**, CPU 9.38ms→1ms 미만(추정).

### 2.12 [B] `_worker_ready` 루프가 후보마다 커넥션

**증거**: `talkabout/core.py:576-577`

```python
require(all(self._worker_ready(principals.get(pid, {}), room['id'], now, required)
            for pid, _ in candidates), 'worker_connection_required')
```

`_worker_ready`(`core.py:171-185`)는 `self.store.credential_active(...)`를 호출하고, Postgres 구현(`store.py:188-194`)은 **새 커넥션**으로 `credentials`를 조회합니다.

**예상 개선폭**: start 승인 1회 커넥션 **N후보→1**(배치 SELECT).

### 2.13 [B] 스케줄러 0.5초 틱이 무조건 쓰기+NOTIFY

**증거**: `talkabout/api.py:28-37` `while not stop.wait(0.5): store.tick_all()`, `talkabout/store.py:427-441`:

```python
ids = [... "SELECT id FROM rooms WHERE payload->>'state'='active' ORDER BY id LIMIT 65" ...]   # 커넥션 1
for rid in ids:
    with self.connect() as conn:                                       # 방마다 커넥션 1
        row = conn.execute("SELECT * FROM rooms WHERE id=%s FOR UPDATE SKIP LOCKED", ...)
        principals = {...}
        room = self._room(conn, row)                                   # 이벤트 전량 로드 + bounds
        schedule(room, principals)
        self._flush(conn, room, row["last_event_seq"])                 # UPDATE rooms + NOTIFY (조건 없음)
```

`_flush`(`store.py:365-373`)는 **변경 여부를 검사하지 않고** `UPDATE rooms SET payload=...,last_event_seq=...`와 `NOTIFY talkabout_changed`를 실행합니다. `NOTIFY`는 모든 `listener()`(`store.py:444-449`)를 깨워 B3·B7 연쇄를 유발합니다.

**측정**: 정적 — 활성 방 R개일 때 초당 `(1+R)` 커넥션, `R` write 트랜잭션, `R` NOTIFY. 실 DB 부하 **미측정**.

**예상 개선폭**: 변경 감지(dirty 플래그)만 추가하면 유휴 시 write **R→0/s**, NOTIFY **R→0/s** → B7의 "빈 갱신" 연쇄가 사라짐. 추정 절감은 측정된 `core.events` 5.04ms×4회/s·클라이언트 기준.

### 2.14 [C] rAF·CSS 애니메이션 상시 동작

- `app.mjs:518,521` `requestAnimationFrame(frame)` 재귀, `frame`(`:437`)는 `stageAnimation` 존재만 확인하고 `document.hidden`을 **검사하지 않습니다**.
- 매 프레임 actor(최대 6명) `setAttribute('transform', ...)` + `svg.setAttribute('viewBox', ...)`(`:486-516`).
- `style.css:130,928,967-995` 등 `animation: ... infinite` **11개**, `backdrop-filter` **10곳**, `box-shadow` **40곳**.
- `prefers-reduced-motion`은 지원(`app.mjs:365-373,409`, `style.css:1708`).

**측정**: 없음(브라우저 필요). 백그라운드 탭에서는 브라우저가 rAF를 초당 1회 수준으로 제한하므로 체감 영향은 **미확인**입니다. 정직하게 "낮은 우선순위"로 둡니다.

### 2.15 [제외] 그 밖의 소소한 비용 (선별 원칙에 따라 개선안에서 제외)

| 항목 | 근거 | 왜 제외 |
| --- | --- | --- |
| `prompt_text` 프롬프트 조립 | `agent_runtime.py:208-232` | 실측 **0.052ms**, 17KB — 무시 가능 |
| 실행기 preflight | `agent_runtime.py:684-699` | 실행 파일 지문 캐시로 재스폰 없음 |
| 런타임 프로세스 재사용 | `connector.py:122-152` `RoomRuntimePool(limit=8)` | 방·generation·membership 단위 재사용 이미 구현 |
| `public_room` 멤버 수 비례 스캔 | `core.py:104-114` | 멤버 5→15명에서 0.47ms→0.51ms — 미미 |

---

## §3 개선 후보 (효과 대비 비용 순)

태그: `[즉시]` S(<반나절) · `[단기]` M(1~3일) · `[장기]` L(1주+). 모든 항목은 §2의 실측/정적 근거에 연결됩니다.

| 순위 | 개선 | 대상 file:line | 근거 측정 | 예상 개선폭 | 비용 | 위험 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | **[즉시] 커넥션 풀 도입** (`psycopg_pool.ConnectionPool`, 요청 스코프 재사용) | `store.py:252-256` | 커넥션 4~24/요청(정적) | 커넥션 수 4→1, 24→6 | S | 풀 크기·타임아웃 설정 오류 시 고갈. `setup_required`/`auth`도 같은 풀 공유 필요 |
| 2 | **[즉시] 틱 쓰기·NOTIFY 억제** (변경 없으면 flush/NOTIFY 생략) | `store.py:427-441`, `store.py:365-373` | 활성 방 R → R write+NOTIFY/0.5s | 유휴 write·NOTIFY **R→0/s** | S | `last_event_seq` 외 필드 변경 감지 누락 시 다음 슬롯 지연 → `schedule()` 반환값으로 dirty 판정 권장 |
| 3 | **[즉시] `/web/*`에 gzip + 캐시 헤더** (`GZipMiddleware`, `asset()`만 `no-store` 예외) | `api.py:73`, `api.py:110-113` | 300,102B→80,064B | 첫 로드 **-73%**, 재방문 0B(304) | S | API 응답 헤더 정책은 유지해야 함(민감 응답) |
| 4 | **[즉시] `setup_required` 결과 단기 캐시/정적 경로 제외** | `api.py:57-61`, `setup.py:40-47` | 정적 요청마다 커넥션 1 | 초기 로드 DB 접근 5→0 | S | 설치 잠금 해제가 늦게 반영될 수 있음 → TTL 1~2초 또는 "완료 후 불변" 캐시 |
| 5 | **[단기] SSE 루프 스냅샷 2→1** (1차 스냅샷 결과로 최종 ACL 재검사) | `core.py:695,736` | `core.events` 5.04ms(4방) | CPU **-50%**, 유휴 스냅샷 8→4/s | M | ACL 재검사 순서 변경은 보안 회귀 위험 → `authorize` 재검증 위치 보존 필수 |
| 6 | **[단기] 이벤트 증분 조회** (`seq > offset` 쿼리로 교체, `LIMIT 2049` 제거) | `store.py:355-362`, `core.py:726-733` | 이벤트 223.4B/건, 최대 2,049건 | 폴링당 이벤트 바이트 **전량→신규분** (유휴 시 0) | M | `first_retained_seq` gap 판정 로직 재작성 필요(`core.py:727-731`) |
| 7 | **[단기] `bounds` 상한 검사 증분화** | `bounds.py:5-14` | 403.5KB/ms; 4방 스냅샷 3.12ms(71%) | 스냅샷 CPU **-3.12ms**, 쓰기 **-3.27ms/2회** | M | 2MB 초과 판정이 어긋나면 `room_byte_capacity` 413이 사라짐 → 단위 테스트 필수 |
| 8 | **[단기] `cursors` 인덱스 + 만료 정리** | `schema.sql:23-26`, `store.py:420-425`, `retention.py:31-43` | 배달마다 INSERT, 정리 경로 없음 | `count(*)` 순차 스캔 제거, 테이블 선형 억제 | S | 정책상 "fixture 전용 정리"를 프로덕션으로 올리는 결정 필요(운영 정책 변경) |
| 9 | **[단기] 브라우저 갱신 6→1~2요청** (이벤트 델타 + `since_seq`) | `app.mjs:967-969`, `core.py:690-760` | 4 스냅샷+1 읽기/갱신(정적) | 서버 스냅샷 **-75%**, HTTP 요청 6→1~2 | M | 델타 적용 버그 시 UI 불일치 → `membership_set_revision` 검사 유지 |
| 10 | **[단기] 페이지네이션/경량 목록 엔드포인트** | `api.py:297`(`rooms`), `api.py:329`(`room`), `worker.py:518` | `GET rooms` 37KB, `GET room` 124KB, 워커 재조회 124KB | 응답 크기 124KB→1KB 미만(워커), 목록은 `limit` 파라미터 | M | 커서·manifest 계약(API_DRAFT) 변경 → 계약 테스트 동반 |
| 11 | **[단기] `_worker_ready` 배치 조회** | `core.py:576`, `store.py:188-194` | 후보 N → 커넥션 N | N→1 | S | 없음(조회만) |
| 12 | **[단기] 워커 idle poll을 서버 long-poll로** | `cli.py:463-466`, `worker.py:340-343` | 워커당 4 req/s | **-90% 이상** | M | `wait_seconds` 상한(10초)과 stop 플래그 반응성(`STOP_POLL_SECONDS=0.2`) 동시 검증 |
| 13 | **[장기] 렌더 패치화** (키 기반 diff, 스테이지 재생성 금지) | `app.mjs:954-959` | 약 2,000노드/갱신(정적) | DOM 재구축 **-90%**(추정) | L | 프레임워크 없는 구조에서 회귀 위험 큼 |
| 14 | **[장기] 라벨 언어 분할 로딩** (`state.mjs` labels ko/en split) | `state.mjs:1-94` | 전체 91,336B 중 대부분 | 초기 JS **약 -30KB 추정** | M | `ui-*` 테스트가 두 언어 동시 로드에 의존(`ui-state.test.mjs` "KO EN labels match") |
| 15 | **[장기] rAF·CSS 애니메이션 예산** (가시성 검사, 프레임 스로틀) | `app.mjs:437,518`, `style.css:130,928-995` | 미측정 | 미측정 | S | 체감 품질 저하 가능 — 실익 불확실 |

**권장 순서**: 1→2→3→4(모두 S, 측정 없이 효과 명확) → 5·6·7(서버 핫패스의 실제 원인) → 9·10(증폭 제거) → 13·14.

---

## §4 3저장소에서 가져올 개념 → TalkAbout 파일 (매핑 **초안**)

> 이 표는 **개념 수준 초안**입니다. handy·t3code·claude-mem의 1차 근거(file:line)는 각 분석관 보고서(`r3-handy.md`·`r3-t3code.md`·`r3-claude-mem.md`)가 확정합니다. 이 §4는 **그 보고서들이 나오기 전에 쓴 초안**이므로, 병목 번호(B#)·개선 번호(§3)·개념 매핑(M#)의 최종 대조는 Lead의 `r3-00-SYNTHESIS.md` 단계에서 해야 합니다. 여기서 직접 확인한 것은 (a) BRIEF-R3.md:18-20의 저장소 정체, (b) M2·M7 행에 적은 저장소 내 경로의 존재뿐입니다.

| # | 저장소:개념 | TalkAbout 현재 코드 상태 | 부착 위치 | 착수 판정 |
| --- | --- | --- | --- | --- |
| M1 | **claude-mem: 압축(compression)** — 원문을 요약해 컨텍스트 토큰을 줄이는 계층 | `shared_context`가 메시지/버전을 자르기만 하고 **요약하지 않음** (`collaboration.py:92-124`), `MAX_MESSAGES` 상한 | `talkabout/collaboration.py:92`(`shared_context`), 호출부 `worker.py:491-495` | **[차용]** 프롬프트 크기·토큰 비용 절감. 단 이 환경에서 프롬프트 조립 실측 0.052ms/17KB → 서버 CPU 효과는 작고 **LLM 토큰 비용**이 목적 |
| M2 | **claude-mem: 백그라운드 인덱싱/훅 큐** — 요청 경로를 막지 않는 후처리 | 서버 스케줄러는 `threading.Thread` 1개, 0.5초 고정 틱(`api.py:28-37`); 요청 경로 안에서 전량 재계산(§2.7) | `talkabout/api.py:28`(scheduler), `talkabout/store.py:427`(`tick_all`) | **[채택]** 개선 2(변경 감지)와 결합. 관측: `claude-mem/src/{hooks,supervisor,services}` 존재 확인 |
| M3 | **claude-mem: 저장/검색 인덱스** — 조회 패턴에 맞춘 인덱스·캐시 | `schema.sql`에 PK/UNIQUE 외 인덱스 **없음**(§2.6) | `talkabout/schema.sql:23-26`, `talkabout/store.py:420-425` | **[채택]** 개선 8. 근거는 자체 진단으로 충분 |
| M4 | **t3code: 스트리밍 이벤트 로그/증분 구독** — 구독자가 "마지막 seq 이후"만 받는다 | 있음(이벤트·per-room seq)이나 **조회는 전량 로딩 후 필터**(`store.py:360`, `core.py:732`) | `talkabout/store.py:355-362`, `core.py:690-760`, `web/app.mjs:967` | **[채택]** 개선 6·9. TalkAbout의 Event 모델이 이미 정본이므로 **전달 형식만** 차용 |
| M5 | **t3code: provider 세션 재사용/풀** | 이미 구현: `RoomRuntimePool(factory, limit=8)` (`connector.py:122-152`) | `talkabout/connector.py:122` | **[대조]** 이미 우위 — 새로 가져올 것 없음 |
| M6 | **t3code: 구조화 이벤트/투영(projection) 최소화** — UI에 필요한 델타만 | `public_room`이 방 전체를 매 요청 재투영(`core.py:75-144`) | `talkabout/core.py:75`(`public_room`), `talkabout/response_projection.py` | **[차용]** 개선 9·10의 설계 참조 |
| M7 | **handy: 실시간 파이프라인의 지연 예산·계측** — 처리 시간을 측정·노출해 회귀를 잡는다 | 테스트에 시간 단언 **0건**(`perf_counter` 0회), 러너 타임아웃 600s/180s뿐(`run_safe_regression.py:76-77`) | `tools/run_safe_regression.py`, (신규) `.ci-local/` perf 게이트 | **[채택]** §1.2 그린 서브셋 + `bench_*.py`를 하네스로 승격. 확인한 경로: `handy/Handy-main/src-tauri/src/managers/audio.rs`, `commands/audio.rs`, `audio_feedback.rs` |
| M8 | **handy: 무거운 작업의 스레드 분리** — UI 스레드를 막지 않는 처리 | `run_in_threadpool`로 이미 분리(`api.py:360`)하나, 미들웨어가 **모든 요청**에 DB 조회(§2.2) | `talkabout/api.py:57-76` | **[차용]** 개선 4 |
| M9 | **handy: 자원 수명·probe 선행 검증** | 이미 구현: `probe()`+지문 캐시(`agent_runtime.py:644-699`) | `talkabout/agent_runtime.py:644` | **[대조]** 이미 충분 |
| M10 | **공통: 스레드/프레임 예산(애니메이션 스로틀)** | `app.mjs` rAF는 `document.hidden` 미검사, CSS infinite 11개 | `talkabout/web/app.mjs:437,518`, `web/style.css:130,928-995` | **[제외/미확인]** 효과 미측정. 브라우저 프로파일링 후 재판정 |

**§4에서 확정된 결론 3가지**
1. 이번 라운드 개선안의 **상위 4개는 외부 저장소 없이도** §2 실측만으로 정당화됩니다(커넥션 풀·틱 억제·gzip·미들웨어).
2. 외부에서 **실제로 새로 가져올 것**은 "증분 구독"(t3code)·"압축"(claude-mem)·"지연 계측 하네스"(handy) 3개입니다.
3. TalkAbout이 이미 우위인 영역: 런타임 풀(M5), probe 선행 검증(M9), Event/seq 모델 자체.

---

## §5 미확인·한계

| 항목 | 상태 | 이유 |
| --- | --- | --- |
| 커넥션 1개 생성 지연(ms) | **미측정** | 실 PostgreSQL 없음(브리프가 서버 기동 금지). 커넥션 **개수**만 정적 계산 |
| 실제 DB 왕복 지연 p50/p95 | **미측정** | 동일. `postgres_fixture` 케이스도 `--postgres-config`가 없어 skip(`tests/conftest.py:42-44`) |
| row lock 보유 시간(§2.4) | **미확인** | `FOR UPDATE` 후 `_room()` 실행 순서는 코드로 확인했으나, 시간은 실 DB 필요 |
| `cursors` 순차 스캔의 실제 악화율 | **미확인** | 행 수를 늘려 재는 실험이 DB 필요 |
| DOM 재렌더 소요(ms) | **미측정** | 헤드리스 브라우저 없음. 노드 수만 정적 산출 |
| rAF/CSS 애니메이션의 CPU·배터리 영향 | **미측정** | 브라우저 필요. 백그라운드 탭 스로틀로 영향 제한적일 수 있음 |
| 전체 pytest 시간의 실행 간 변동 | 관측만 함(172.90s vs 257.75s, 1.49배) | 같은 코드에서 발생. 원인(러너 부하·I/O) **미확인** → 성능 게이트는 **상대 비교**로 설계해야 함 |
| `test_work_profile_events.py` 53건 단언 실패 | **미확인** | 샌드박스 제약의 파급으로 **추정**만 함. 원인 개별 조사 안 함 |
| Node 243 vs 247 차이 | **미확인** | 4건 증가 원인 미조사 |
| `handy`/`t3code`/`claude-mem`의 개념 구현 세부 | **미확인** | 이 문서 §4는 개념 수준 초안. 1차 근거는 각 분석관 보고서 소관 |

**이 문서로 보장할 수 있는 것**: §1의 수치는 모두 이 저장소·이 환경에서 실행한 결과이며 재현 명령과 스크립트가 부록 A에 있습니다. §2의 "정적 분석" 표시 항목은 코드 라인 근거만 있고 시간은 재지 않았습니다. §3의 예상 개선폭은 **측정값에 근거한 산술**이거나 "(추정)"으로 표시했습니다.

---

## 부록 A. 임시 스크립트 (`D:\AI-Codding\Claude\TalkAbout\.tmp-perf\`)

| 파일 | 용도 | 실행 |
| --- | --- | --- |
| `run_pytest_bench.py` | 표준 러너와 같은 조건(`ci_guard` 설치, `-q -ra`, `no:cacheprovider`)으로 전체 pytest 실행 + 테스트별 소요 기록 | `.\.venv\Scripts\python.exe -B .tmp-perf/run_pytest_bench.py` |
| `summarize_cases.py` | `pytest-cases.jsonl` 집계(nodeid별 결과, 실패 원인 분류, 최느린 30) | `.\.venv\Scripts\python.exe -B .tmp-perf/summarize_cases.py` |
| `bench_domain.py` | 방 크기별 `core.rooms/read/room_snapshot/events` CPU, `room_bounds`, `public_room`, `prompt_text` | 동일 방식 |
| `bench_scale.py` | 멤버 수·4방·1.32MB 방 스케일, `snapshot_bounds`, 인박스 `events` | 동일 방식 |
| `bench_api.py` | TestClient 인프로세스 HTTP 지연(정적 자산·조회·mutation) | 동일 방식 |
| `bench_web.mjs` | 자산 크기·gzip9, 모듈 파싱, JSON 파싱 | `node --experimental-vm-modules .tmp-perf/bench_web.mjs` |
| `pytest-cases.jsonl` / `pytest-full.log` | 원시 기록 | (산출물) |

부수 효과로 `.tmp-perf/basetemp*`에 pytest 임시 디렉터리가 남습니다. 세션 종료 단계에서 DACL 때문에 `pytest_sessionfinish`가 `PermissionError`로 죽습니다(`STATUS.md:47`과 동일한 환경 제약) — 그래서 집계를 별도 스크립트로 떼어냈습니다.
