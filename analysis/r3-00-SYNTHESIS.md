# r3-00 — 3개 저장소 정밀 분석 종합 (handy · t3code · claude-mem) · **성능** 라운드

- 분석일: 2026-11-02
- 대상 스냅샷(모두 `main` 브랜치 zip, **git 이력 없음**):
  - `cjpais/handy` v0.10.0, 2026-11-02, **MIT** → 로컬 `.analysis-repos/handy/Handy-main/` (484파일 8.1MB)
  - `pingdotgg/t3code`, 2026-11-02, **MIT** → `.analysis-repos/t3code/t3code-main/` (벤더 제외 7,112파일 97.5MB / 원본 29,137파일 321.6MB)
  - `thedotmack/claude-mem` v15.8.3, 2026-11-02, **Apache-2.0** → `.analysis-repos/claude-mem/claude-mem-main/` (2,873파일 53.7MB)
- 방법: 세 저장소는 **순수 정적 분석**이다(Rust/cargo·bun·pnpm/tsc 없음 → 빌드·실행·프로파일링 미실시).
  반면 **TalkAbout 기준선은 실제 실행 측정**이다(`.venv` Python + Node 24, `.tmp-perf/` 스크립트, 실 PostgreSQL 없음).
- 상세 보고서 4편: `r3-handy.md`(554줄) · `r3-t3code.md`(440줄) · `r3-claude-mem.md`(522줄) · `r3-talkabout-baseline.md`(482줄).
  이 문서는 그 4편을 **비교·판정**하고 TalkAbout 착수 순서를 확정한다.

---

## 0. 결론

**세 저장소 모두 "알고리즘"이 아니라 "경계(프로세스·왕복·이벤트 빈도·전체 재생성)"에서 성능을 벌었다.**
그리고 셋 다 **자기 성능을 테스트로 고정하는 하네스**를 갖고 있다. TalkAbout은 그 반대다 —
병목은 이미 코드에 있고(§4 실측), 하네스는 없다.

| 저장소 | 성능 병목의 정체 | 남은 개선 여지 | 성격 |
| --- | --- | --- | --- |
| **handy** | 실시간 파이프라인의 **이벤트 폭/블로킹/부팅 자산** | 중간 — 3건이 `[즉시] S`(emit 스로틀·`spawn_blocking`·i18n lazy) | 로컬 데스크톱, 사용자 1명, 지연 = ms 단위 체감 |
| **t3code** | 이미 튜닝됨. 남은 것은 **잔여 O(n) + 구독자 배수 + 빌드 비용** | 작음 — 큰 승리 없음, S~M 7건 | 서버/웹, 다중 표면(웹·데스크톱·모바일·peer) |
| **claude-mem** | **동기 훅의 프로세스 체인·직렬 왕복 수·LLM 턴 수** (DB는 병목 아님) | 중간 — 기본값/중복 호출 4건이 `[즉시] S` | 에이전트 플러그인, 지연 = "응답이 멈춘 초" |
| **TalkAbout(기준선)** | **요청 모델**: 커넥션 풀 부재, 전량 재적재·전량 직렬화, 0.5s 무조건 write+NOTIFY, 브라우저 6중 재조회 | **큼** — 상위 4건은 외부 저장소 없이 자체 근거로 정당화 | 서버 + 자체 웹, Python/FastAPI/PostgreSQL |

**가장 실질적인 산출물은 §4다.** 세 저장소에서 "새로" 가져올 개념은 3개뿐이고
(증분 구독 · 압축 주입 · 지연 계측 하네스), TalkAbout의 1~4순위 개선은 **자체 실측만으로** 정당화된다.

---

## 1. 세 저장소의 성능 프로필 (실측 구조 기준)

### 1.1 handy — "패턴은 저장소 안에 있는데 핫패스 한 곳에만 빠져 있다"

| 항목 | 값/근거 |
| --- | --- |
| 구조 | Tauri 메인 + 웹뷰 2개(설정/오버레이) + **별도 `transcribe.cpp` 워커 프로세스**(모델 상주). `src-tauri/src/lib.rs:396-397`, `engine_supervisor/supervisor.rs:35-47` |
| 오디오 프레임 | Silero VAD 기준 **512샘플 = 32ms = 31.25프레임/s** (`vad/mod.rs:66-75`) |
| 피드 경로 | 프레임마다 **동기 IPC 왕복**(write+flush+read), 큐 상한 없음(`supervisor.rs:513-528`, `:954-994`) |
| 기본 모델 | `parakeet-unified-en-0.6b` **731MB** Q8_0, 유휴 5분 언로드(`settings.rs:150-160`) |
| 카탈로그 RTF | 0.6b: GPU 25.3× / CPU 14.0× (30s 발화 = GPU 1.2s/CPU 2.1s 추정) — `catalog.json` |
| 자체 계측 | 시작 4분해·마이크 open 3분해·`emit/feed` 카운트·`pending_ms` 등 **`Instant` 로그가 이미 20여 곳** (`actions.rs:566-652`, `transcription.rs:1476-1489`) |
| 부팅 자산 | 26개 로케일 **1,343,007B eager**(두 엔트리 모두) `src/i18n/index.ts:12-25` + 오버레이 웹뷰 무조건 생성 |

**핵심 3건**
1. 라이브 텍스트가 revision마다 **committed 전체 문자열**을 emit, 스로틀 없음(`managers/transcription.rs:1410-1416`).
   같은 파일의 마이크 레벨은 이미 30FPS 스로틀(`overlay.rs:817-844`) → **패턴 부재가 아니라 적용 누락**.
2. `async_runtime::spawn` 안에서 `stop_recording`/`transcribe_recording`(수 초 블로킹) 직접 호출(`actions.rs:697-771`).
   같은 크레이트에 `spawn_blocking` 12곳이 있다.
3. 라이브 실패 시 **전체 재디코드**(`live.rs:232-236` → `transcription.rs:1005`) = 말 끝→텍스트 2배.

### 1.2 t3code — "성능 규율이 CI에 박혀 있다"

| 항목 | 값/근거 |
| --- | --- |
| 규모(벤더 제외) | 7,112파일/97.5MB. apps/web 1,848, apps/server 1,487, apps/mobile 1,478, packages 29개. 테스트 1,889파일(26.6%) |
| 이벤트 경로 | provider CLI → AdapterV2 → `EventSink.write*`(단일 트랜잭션: events+projection+receipt+outbox, `EventSink.ts:259-284`) → PubSub → **50ms 코얼레서**(최대 512건) → ws |
| 와이어 예산 | 상세 32KB·동적값 16KB·온디맨드 256KB·체크포인트 200개, **잘림 마커 포함 계산**(`WireProjection.ts:24-30`) |
| 하네스 | `render-metrics.ts`(React 커밋/렌더 수, **3회 결정성 요구**), `test:perf:v2-wire`(16파일), `performance.bench.ts`·`remotePerformance.bench.ts`, `profile-startup.mjs`, 전송 예산 테스트(8,790B→≤1,024B 강제) |
| 이미 잘함 | 터미널 백프레셔(8청크/64KB ack, 8MB 큐), SQLite reader/checkpoint 워커 스레드, 배치 1회 `SubscriptionRef` 적용, 캐시 쓰기 병합(`Queue.sliding(1)`) |

**남은 7건(모두 S~M)**: 코얼레서 **선행 50ms 대기**(첫 델타가 창 종료까지 잡힘) ·
`message.updated`만 남은 정방향 `findIndex`+전체 복사(같은 파일 `turn-item.updated`는 이미 역방향) ·
dedupe `findLast` 3종 · `run.updated`마다 visible rows 전체 재계산 ·
**와이어 프로젝션이 구독자마다 재실행**(3~4표면이면 1/3~1/4) · prod sourcemap 기본 on · `ChatView.tsx` 4,024줄이 스토어 12곳 구독.

### 1.3 claude-mem — "DB는 병목이 아니다. 훅 프로세스와 LLM 턴 수가 병목이다"

| 항목 | 값/근거 |
| --- | --- |
| 규모 | `src/` TS 750파일 159,537줄. 번들 `worker-service.cjs` **4,529,909B**(매 훅이 이걸 파싱) |
| 훅 분류 | **동기 4개**: SessionStart context(60s) · ensure-launcher(180s) · UserPromptSubmit session-init(15s) · PreToolUse file-context(15s). async 4개(observation/summarize/…)는 사용자를 안 막음(`hooks.json:17-179`) |
| 프로세스 오버헤드 | 저장소 **자체 측정** "Windows에서 shell+bun+bundle load 4-5초" → `hookProcessOverheadMs()`가 win32 8,000 / posix 5,000(`hook-constants.ts:117-134`) |
| DB 계층 | FTS5 트리거·v68 `observation_files` 색인·nocase recent 인덱스 — **이미 좋음**(`SessionStore.ts:330-354`, `:2131-2134`) |
| 트랜스크립트 | 증분 테일링(영속 offset, 패스당 4MB) — 개선 대상 아님(`watcher.ts:24-27,59`) |
| 자체 예산 규율 | 훅 하나가 10s 예산을 체인 전체가 나눠 쓰고, 500ms 미만이면 단계를 **건너뛴다**(`session-init.ts:56-62,290-298`) |

**핵심 4건**: SessionStart가 렌더를 **2회**(모델용+색상용, `context.ts:414-425`, 기본값 true) ·
관측 배치 기본 **1** → 툴콜 1회 = LLM 턴 1회(`SettingsDefaultsManager.ts:523`) ·
PreToolUse마다 **훅 프로세스가 메인 SQLite 직접 오픈**(`file-context-dedupe.ts:110,154`) ·
`/api/sessions/init`의 직렬 SQLite 왕복 **8회**(`SessionRoutes.ts:875-1025`).

---

## 2. 횡단 패턴 6개 — "어디서 성능이 나오는가"

| # | 패턴 | handy | t3code | claude-mem | TalkAbout 현재 |
| --- | --- | --- | --- | --- | --- |
| P1 | **델타/증분 전송** (전체 재전송 금지) | ❌ committed 전체 emit(`transcription.rs:1410`) | ✅ wire projection·본문 LRU32 dedupe(`WireProjection.ts:326-385`) | ✅ 증분 테일링·delta | ❌ `LIMIT 2049` 전량 후 Python 필터(`store.py:360`, `core.py:732`) |
| P2 | **코얼레스/스로틀** (이벤트 폭 상한) | ⚠️ mic-level만 30FPS, 텍스트는 무제한 | ✅ 50ms 창 + 512건(`ThreadLiveEventCoalescer.ts:18-19`) | ✅ 캐시 렌더 2s debounce(`ContextCacheService.ts:47`) | ❌ 0.5s 무조건 tick + 250ms SSE 폴링 |
| P3 | **백프레셔/지연 예산** (유한 상한) | ⚠️ 큐 무제한, `pending_ms` 관측만 | ✅ 64KB ack 창·8MB 큐·`availableAt` 재시도(`OutputProtocol.ts:45-75`) | ✅ 단일 deadline 전파·500ms 미만 단계 skip | ⚠️ 상한이 개별 상수라 곱해짐(`MAX_INPUT`·SSE 60s·wait 10s) |
| P4 | **블로킹 분리** (작업 스레드 경계) | ❌ async 런타임에서 수 초 블로킹(`actions.rs:697`) | ✅ reader/checkpoint 워커 스레드(`Sqlite.ts:52-64`) | ⚠️ 훅 프로세스 자체가 경계(그게 비쌈) | ✅ `run_in_threadpool` 일관(`api.py:103,131,206`) — **TalkAbout 우위** |
| P5 | **사전 렌더 캐시 + 무효화** | ⚠️ 기기 프리워밍만 | ✅ 스냅샷 영속 병합(`threads.ts:280-286`) | ✅ 디스크 컨텍스트 캐시 24h + 2s debounce 워밍(`context-cache.ts:31,173-193`) | ❌ 매 요청 `public_room` 재투영·`deepcopy`(`core.py:75-138`) |
| P6 | **측정을 테스트로 고정** | ⚠️ 로그는 있으나 게이트 아님 | ✅ 전송 예산 테스트·렌더 카운트 결정성 | ✅ `check-hook-io-discipline.cjs` + 테스트 레인 | ❌ **없음**(`tools/`·`.ci-local/`은 기능 회귀만) |

**해석**
- P4는 TalkAbout이 이미 이긴 영역이다. handy에 **역제안**할 수 있다(§5-3).
- P6이 없는 저장소는 P1~P3을 도입해도 다시 폴링으로 되돌아간다. **TalkAbout의 최우선은 P6이다.**
- 세 저장소 중 **P1+P2+P3를 동시에 갖춘 것은 t3code뿐**이고, 그 조합이 "다중 표면에서도 지연이 안 늘어나는" 이유다.
  TalkAbout은 다중 표면(서버 worker + 브라우저 + 참여자 agent)이므로 **t3code의 전달 규율이 가장 전이성이 높다**.
  반면 claude-mem의 전이성은 **경계 설계**(동기/비동기 분리, 단일 deadline)와 **주입 예산**에 있고,
  handy의 전이성은 **이벤트 폭 상한**과 **부팅 자산 최소화**에 있다.

---

## 3. 저장소별 최우선 개선 Top 3 (상세 근거는 각 보고서)

| 저장소 | 순위 | 개선 | 근거 | 효과(추정) | 비용 |
| --- | --- | --- | --- | --- | --- |
| handy | 1 | 라이브 텍스트 emit 병합·스로틀(변경분/16~33ms) | `transcription.rs:1410-1416`, `overlay.rs:817-844`(선례) | 장문 누적 IPC 바이트·오버레이 리렌더/리플로우 상한화 | S |
| handy | 2 | 전사·정지를 `spawn_blocking`으로 | `actions.rs:697-771` | tokio 워커 수 초 점유 제거(4코어 25%) | S |
| handy | 3 | i18n lazy + 오버레이 지연 생성 | `i18n/index.ts:12-25`, `lib.rs:396-397` | 부팅 파싱 1.31MB→42.5KB, 웹뷰 1개↓ | S~M |
| t3code | 1 | 코얼레서 trailing-only(첫 델타 즉시) | `ThreadLiveEventCoalescer.ts:119-157` | 첫 토큰→화면 최대 50ms↓ | S |
| t3code | 2 | `message.updated` 역방향 인덱싱(3줄) | `orchestrationV2Projection.ts:53-62,370` vs `:375-379` | 긴 스레드 델타 단가 O(n)→O(k) | S |
| t3code | 3 | 와이어 프로젝션 WeakMap 메모 + publish 버킷팅 | `WireProjection.ts:314-324`, `LiveStreamBudget.ts:354`, `EventSink.ts:228-238` | 서버 CPU가 **구독자 수에 비례하지 않게** | S |
| claude-mem | 1 | SessionStart 색상용 2차 렌더 제거 | `context.ts:414-425`, `SettingsDefaultsManager.ts:581` | 캐시 미스 렌더 50%↓, 왕복 2→1 | S |
| claude-mem | 2 | 관측 배치 기본값 상향(1→4~8, 프리셋 권고) | `SettingsDefaultsManager.ts:523`, `ClaudeProvider.ts:1253-1323` | 툴콜 100회 세션의 LLM 턴 100→13~25 | S |
| claude-mem | 3 | PreToolUse의 메인 SQLite 직접 오픈 제거 | `file-context-dedupe.ts:110,154` | Read당 수 ms + WAL writer 경합↓ | S |

> 세 저장소 모두 "1순위가 S(반나절)"라는 점이 중요하다. **비싼 최적화보다 상수·기본값·중복 호출이 먼저**다.
> claude-mem 보고서의 결론("이미 상당히 최적화된 코드베이스이며 개선 여지가 작다는 것 자체가 결론")도 같은 방향이다.

---

## 4. TalkAbout 적용 — 실측 근거 기반 착수 순서

TalkAbout 기준선(`r3-talkabout-baseline.md`)의 **실행 측정값**만 인용한다(단위: 이 환경, TestClient/MemoryStore, 실 DB 없음).

### 4.1 측정된 사실 (요약)

| 지표 | 값 |
| --- | --- |
| 웹 자산 | 300,102B → gzip9 **80,064B(26.7%)**, `Cache-Control: no-store`, GZip 미들웨어 없음 |
| 인프로세스 HTTP | `/health` 3.44ms(바닥값) · `GET /rooms` **5.91ms/37KB** · `GET /room` 5.26ms · `events` 6.04ms · `POST message` 5.71ms |
| 도메인(4방 40메시지) | `core.events()` **5.04ms/회**(DB 0) · `core.rooms()` 4.40ms → 그중 **`snapshot_bounds` 3.12ms(71%)** |
| 바이트 상한 검사 | 1.32MB 방 `json.dumps`+`encode` **3.27ms**(403.5KB/ms). Postgres 쓰기 1회 = dumps **2회** |
| 이벤트 | 평균 **223.4B/건**, 최대 2,049건 = 약 **457KB/폴링** |
| 요청당 커넥션 | **4개**(미들웨어 setup_required · token auth · dispatch auth · snapshot), SSE 갱신 1회 = **약 24개** |
| 스케줄러 | 0.5s마다 활성 방 R개에 **무조건 UPDATE+NOTIFY**(변경 검사 없음) |
| 워커 | 유휴 시 **4 req/s**(`wait_seconds=0`), 턴 중 방 전문 124KB 재조회(9.38ms) |
| 테스트 | 전체 1,244 수집 0.89s / 표준 러너 172.90s(직전 257.75s — **같은 코드 1.49배 변동**) / 그린 서브셋 200 pass 11.1s |

### 4.2 P0 — 계측 먼저, 그다음 "측정 없이도 확실한" 4건 (전부 S)

| 순위 | 개선 | 파일 | 측정 근거 → 기대 |
| --- | --- | --- | --- |
| P0-0 | **성능 하네스**: 쿼리 수·스냅샷 호출 수·바이트를 **pytest 어서션**으로 고정 + `tools/perf_snapshot.py` | `tools/`, `tests/` | t3code `ThreadTransportPerformance.test.ts` 방식. **이게 없으면 나머지가 되돌아간다** |
| P0-1 | **커넥션 풀 도입**(`psycopg_pool`) | `store.py:252-256`(24호출) | 커넥션 4→1, SSE 갱신 24→6 |
| P0-2 | **dirty 게이트**: 변경 없으면 `_flush`/`NOTIFY` 생략 | `store.py:427-441`, `:364-373`, `api.py:28-37` | 유휴 write/NOTIFY **R→0/s** → 브라우저 재조회 연쇄 소멸 |
| P0-3 | **정적 자산 gzip + 캐시**(`/web/*`만 `no-store` 예외) | `api.py:73,110-113` | 첫 로드 300KB→80KB(-73%), 재방문 304 |
| P0-4 | **`setup_required` 단기 캐시 / 정적 경로 제외** | `api.py:57-61`, `setup.py:40-47` | 초기 로드 DB 접근 5→0 |

### 4.3 P1 — 구조 4건 (M)

| 순위 | 개선 | 파일 | 기대 |
| --- | --- | --- | --- |
| P1-1 | SSE 루프 스냅샷 **2→1** + LISTEN 구동(0.25s 폴링 제거) | `core.py:695,736,760` | 유휴 SSE 1개 CPU 5.04ms×4/s → 대폭↓ |
| P1-2 | 이벤트 **델타 조회**(`seq > offset`, `LIMIT 2049` 제거) | `store.py:355-362`, `core.py:726-733` | 폴링당 이벤트 바이트 457KB→신규분(유휴 0) |
| P1-3 | **bounds 증분화**(전체 `json.dumps` 제거) | `bounds.py:5-14` | `core.rooms()` 71%·쓰기 3.27ms 제거 |
| P1-4 | 브라우저 SSE 이벤트 **코얼레스(100ms) + payload 소비**(6요청→1) | `web/app.mjs:967-969` | 서버 스냅샷 -80% |

### 4.4 P2 — 정리·장기

`cursors` 인덱스+만료 정리(`schema.sql:23-26`) · 워커 long-poll(`worker.py:340`, `cli.py:463`) ·
경량 room fence 엔드포인트(`worker.py:518`, 124KB→1KB) · `_worker_ready` 배치 조회(`core.py:576`) ·
키 기반 DOM 패치(`app.mjs:954-959`) · 라벨 ko/en 분할(`state.mjs`) · 페이지네이션(`api.py:297`).

### 4.5 세 저장소 → TalkAbout 매핑 (실제로 새로 가져올 것)

| 저장소:개념 | TalkAbout 부착 | 판정 |
| --- | --- | --- |
| **t3code: 델타/코얼레스/와이어 예산** | `core.py:690-760`, `api.py:131-143`, `web/app.mjs:967` | **[채택]** P1-1·P1-2·P1-4의 설계 원천. TalkAbout은 Event/seq 모델이 이미 정본이므로 **전달 형식만** 차용 |
| **claude-mem: 주입 예산·압축** | `collaboration.py:92-160`(shared_context·manifest) | **[차용]** 서버 CPU 효과는 작고(프롬프트 조립 0.052ms) **LLM 토큰 비용**이 목적. TalkAbout은 이미 감축 순서가 결정적 → "측정 후 감축"만 추가 |
| **claude-mem: 단일 deadline 전파** | `core.py`·`collaboration.py` 인자 추가 | **[차용]** 개별 상수 조합으로 상한이 곱해지는 문제 방지. `manifest.omissions` 자리에 `budget_truncated` 추가 |
| **claude-mem: 캐시+debounce 무효화** | `store.py` 프로젝션 캐시 | **[채택]** P0-2·P1-1과 결합. dirty일 때만 재렌더 |
| **handy: 이벤트 폭 상한(30FPS/100ms)** | `core.py` SSE 배출, `web/app.mjs` 렌더 | **[채택]** P1-4. "변경 없으면 아무것도 하지 않는다"를 명시 |
| **handy: 부팅 자산 최소화** | `web/state.mjs`(91KB, 대부분 ko+en 라벨) | **[단기]** 언어별 분할 — 초기 JS -30KB 추정 |
| **handy: `Instant` 계측 문화** | `core.py`·`store.py` 스팬 | **[채택]** P0-0. 이미 있는 로그를 모으는 방식이 가장 싸다 |
| **t3code: 측정을 CI 게이트로** | `tools/run_safe_regression.py` 옆 | **[채택]** 결정적 지표(쿼리 수·이벤트 수) 우선, wall-clock은 상대 비교만 |

### 4.6 대조 — TalkAbout이 이미 우위인 것 (베끼지 말 것)

- **블로킹 분리**: 모든 동기 코어 호출이 `run_in_threadpool`(`api.py:103,131,206`). handy의 개선안 2번이 바로 이 패턴이다 → **역제안 대상**.
- **런타임 재사용**: `RoomRuntimePool(limit=8)`(`connector.py:122-152`) — t3code의 provider 세션 풀에 해당.
- **실행기 probe 선행 검증**: `agent_runtime.py:644-699`(지문 캐시).
- **Event/seq 정본 모델**: 세 저장소 중 claude-mem만 유사(트랜스크립트 증분), t3code는 별도 스트림.
- **감축 순서의 결정성**: `collaboration.py:104-125`(pinned→newest→adopted→recent) — claude-mem `ContextBudget`보다 예측 가능.

---

## 5. 검증 상태와 한계

### 5.1 내가 직접 spot-check한 것 (4편의 주장 중 핵심)
- handy `transcription.rs:1410-1416`의 무스로틀 emit, `actions.rs:697-703`의 async 내 블로킹 호출 — **원문 확인**.
- t3code `ThreadLiveEventCoalescer.ts:18` `COALESCE_WINDOW = 50ms`, `orchestrationV2Projection.ts:53-62` 정방향 `findIndex`+전체 복사 — **원문 확인**.
- TalkAbout `store.py:355-362` 전량 이벤트 로딩, `core.py:695/736` 스냅샷 2회, `app.mjs:967-969` SSE→`refresh()`, `cli.py:463-466` 0.25s 폴링 — **원문 확인**.
- 라이선스: handy MIT · t3code MIT · claude-mem **Apache-2.0**(NOTICE 동봉) — 코드 복사 시 고지 의무. 이 라운드는 **코드를 복사하지 않았다**(구조·수치 인용만).

### 5.2 한계 (정직하게)
1. **세 저장소는 실측 0** — Rust/TS 툴체인이 없어 빌드·프로파일링을 하지 않았다. 모든 ms/배수는 상수·주석·구조 유도 **추정**이다.
2. **git 이력 없음**(zip 스냅샷) → "이 최적화가 언제 왜 들어갔는가"는 확인 불가. claude-mem 보고서처럼 "이미 고쳐진 흔적(v68 인덱스 등)"은 코드 주석으로만 판단했다.
3. **t3code `.repos/`(Effect 소스) 미열람** → `PubSub.unbounded` 백프레셔 특성 등은 미확인.
4. **TalkAbout 실 DB 없음** → 커넥션 1개 비용·row lock 보유시간·`cursors` 스캔 악화율은 **미측정**. 커넥션 "개수"만 정적 계산.
5. **브라우저 측정 없음** → DOM 재구축·rAF 비용은 노드 수 산출까지만.
6. **테스트 시간 변동**(172.90s vs 257.75s, 1.49배) → 성능 게이트는 wall-clock 절대값이 아니라 **상대 비교/카운터**로 설계해야 한다(P0-0의 근거).

### 5.3 반증 조건 — 이 우선순위가 뒤집히는 경우
- **커넥션 1개 생성 비용이 실제로 매우 싸면**(로컬 unix socket·trust 인증) P0-1의 순위가 내려가고 P1-1·P1-2가 올라간다.
- **동시 SSE 클라이언트가 1~2명이면** P1-4(브라우저 6요청)의 절대 효과는 작다 — 다만 P0-2(무조건 NOTIFY) 때문에 **유휴에도 계속 돈다**는 사실은 인원과 무관하다.
- **방당 이벤트가 수십 건 수준이면** P1-2(델타 조회)의 이득은 작다. 현재 상한 2,049건·`docs/API_DRAFT.md`의 paging 미구현이 전제다.
- **handy의 스트림 `revision` 상승 빈도가 낮으면**(모델·백엔드 의존) H1의 실효는 줄어든다 — `Live preview perf` 로그의 `emit/feed` 비율로 즉시 판정 가능.

---

## 6. 다음 단계 (권고)

1. **P0-0 하네스 먼저**(반나절): `core.events()` 1회당 쿼리 수·`snapshot()` 호출 수·응답 바이트를 pytest 어서션으로.
   전후 비교가 가능해지면 나머지는 기계적으로 검증된다.
2. **P0-1~4**(1일): 전부 S, 서로 독립. 실 DB에서 커넥션 수·gzip 전송량만 확인하면 끝.
3. **P1-1~4**(3~5일): SSE/이벤트/bounds/웹 4축. P1-3(bounds)은 단위 테스트로 2MB 경계를 고정한 뒤 진행.
4. P2는 릴리스 관문(`docs/RELEASE_GAP_MATRIX.md`)과 충돌하지 않는 것부터.
5. 세 저장소 쪽 제안은 각 보고서의 `[즉시]` 항목만 upstream 이슈 후보로 남긴다(코드 복사 없이 근거만).

---

### 부록: 이 라운드 산출물
- `analysis/BRIEF-R3.md` — 브리프(규칙·범위·배정)
- `analysis/r3-handy.md` — handy 상세(554줄)
- `analysis/r3-t3code.md` — t3code 상세(440줄)
- `analysis/r3-claude-mem.md` — claude-mem 상세(522줄)
- `analysis/r3-talkabout-baseline.md` — TalkAbout 기준선 실측(482줄) + `.tmp-perf/` 벤치 스크립트
- `analysis/r3-00-SYNTHESIS.md` — 이 문서
