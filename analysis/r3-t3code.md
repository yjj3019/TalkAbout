# t3code 성능 정밀 분석 (3라운드)

- **대상**: `.analysis-repos/t3code/t3code-main/` — `pingdotgg/t3code`, `main`, 2026-11-02 zip 스냅샷 (git 이력 없음). 라이선스 **MIT**(`LICENSE:1-3`, "Copyright (c) 2026 T3 Tools Inc.").
- **방법**: **순수 정적 분석**. 이 환경에는 Node 24만 있고 pnpm/tsc/bun/`vp`가 없으므로 `pnpm install`·빌드·테스트·벤치를 **실행하지 않았다**. 저장소가 자체 제공하는 벤치/계측 하네스는 *존재와 코드만* 인용하고 수치는 인용하지 않는다(실행값 없음). 모든 소요·배수 언급은 "정적 분석 추정"으로 표기한다.
- **제외 범위(브리프 지시)**: `.repos/`, `apps/mobile/modules/*/Vendor/`, `node_modules/`, `third-party-licenses/`, `assets/`. 이 필터로 **7,112 파일 / 97.5MB**만 본다(원본 29,137/321.6MB — §1).
- **읽은 범위**: 이벤트 파이프라인(서버 `EventSink`/`WireProjection`/코얼레서, 클라 `orchestrationV2Projection`/`threads-sync`), 터미널 데이터 경로, SQLite 계층, 빌드/캐시 설정, 렌더 계측 하네스. 파일 크기만 보고 고른 곳은 없다.

---

## §0 요약

### 0.1 핫패스 지도 (코드로 확인한 실제 실행 경로)

```
provider CLI subprocess
  └─ Adapters/{Claude,Codex,Cursor,Grok,OpenCode}AdapterV2.ts     (프로토콜 번역, 스트리밍 델타 생성)
       └─ Orchestrator.ts            (순수 명령 직렬화, I/O 없음)
            └─ EventSink.write*       ← sql.withTransaction(events + projection + receipt + outbox)  EventSink.ts:259-284
                 ├─ EventStore        → SQLite (WAL, reader worker)                                  Sqlite.ts:14-44,52-64
                 └─ publishLiveEvents → PubSub(전역) + 타입별 PubSub 맵                            EventSink.ts:228-238,849-857
                      └─ ThreadLiveEventCoalescer  (50ms 창, 최대 512건)                            ThreadLiveEventCoalescer.ts:18-19,119-134
                           └─ LiveStreamBudget → ws Chunk(frame)                                    LiveStreamBudget.ts
                                └─ client driver → threads.ts applyEventsLocked (배치 적용)          threads.ts:479-598
                                     └─ applyOrchestrationV2ProjectionEvent (이벤트→불변 프로젝션)  orchestrationV2Projection.ts:259-421
                                          └─ SubscriptionRef → @effect/atom-react → React(Compiler)  ChatView.tsx / MessagesTimeline.tsx
                                               └─ LegendList 가상화 + 증분 markdown 파서            markdown-incremental.ts
```

병렬 핫패스 2개: **터미널**(PTY→`TerminalOutput`→ws, `apps/server/src/terminal/`)와 **원격 HTTP 스냅샷**(`boundedThreadSnapshotHttp`).

### 0.2 개선 후보 (효과 대비 비용 순)

| # | 개선 | 태그 | 비용 | 근거(요지) | 예상 효과(정적 추정) |
|---|---|---|---|---|---|
| 1 | 스트리밍 코얼레서의 **선행 50ms 대기** 제거(trailing-only) | `[즉시]` | S | `ThreadLiveEventCoalescer.ts:18,119-134,153-157` | 첫 토큰→화면 지연 최대 50ms 단축, 처리량 동일 |
| 2 | `message.updated` 적용 경로의 **정방향 선형스캔 + 전체 배열 복사** | `[즉시]` | S | `orchestrationV2Projection.ts:53-62,370`(대조 `:375-379`) | 긴 스레드 델타 단가 O(n)→O(1)~O(k) |
| 3 | dedupe 본문 복원용 `findLast` 3종을 **인덱스로** | `[단기]` | S | `orchestrationV2Projection.ts:65-72,374` | turn-item 델타당 3×O(n) 스캔 제거 |
| 4 | `run.updated`/`run-attempt.updated`마다 **visible rows 전체 재계산** | `[단기]` | M | `orchestrationV2Projection.ts:301-322,101-121` | 수백~수천 행 재계산·할당 제거 |
| 5 | 서버 fan-out 비용: **구독자 수만큼 반복되는 와이어 프로젝션** + `publishLiveEvents`의 타입별 전체스캔 | `[즉시]` | S | `WireProjection.ts:32-50,314-324`, `LiveStreamBudget.ts:348-356`, `EventSink.ts:228-238,849-857` | 구독자 수 × 이벤트 수 → 이벤트 수로 고정 |
| 6 | 웹 프로덕션 빌드 **sourcemap 기본 on** | `[단기]` | S | `apps/web/vite.config.ts:75-80,377-391` | CI 빌드 시간·`dist` 크기 |
| 7 | `ChatView.tsx`(4,024줄)가 **12개 스토어 셀렉터를 직접 구독** | `[단기]` | M | `ChatView.tsx:580-615,673,1630-1635` | 입력/드래프트 변경 시 재렌더 범위 |
| — | 이미 최적화되어 **베낄 것**이 더 많은 영역 | `[대조]` | — | §2.8 | TalkAbout 적용(§4)의 원천 |

**총평(정직하게)**: t3code의 핫패스는 이미 상당히 튜닝되어 있다. WS 페이로드 예산을 테스트로 강제하고(`ThreadTransportPerformance.test.ts`), 터미널에 백프레셔 창을 두고(`OutputProtocol.ts`), SQLite에 reader/checkpoint 워커 스레드를 붙이고(`Sqlite.ts:52-64`), React 커밋 수를 CI에서 센다(`scripts/render-metrics.ts`). 따라서 "큰 승리"는 없고, 남은 것은 **(a) 저비용 잔여 O(n) 경로, (b) 빌드/CI 비용, (c) 측정 하네스 커버리지 밖 코드**다. 반면 **TalkAbout에 그대로 옮길 수 있는 설계 자산은 매우 많다**(§4).

---

## §1 구조 실측 (숫자)

### 1.1 규모 (벤더 제외 후 실측)

| 디렉터리 | 파일 | MB | 성격 |
|---|---:|---:|---|
| `apps/web` | 1,848 | 26.6 | React 19 + Vite(Rolldown) + TanStack Router UI |
| `apps/server` | 1,487 | 26.6 | Node WebSocket 서버 + 오케스트레이션(Effect 중심) |
| `apps/mobile` | 1,478 | 14.4 | React Native(iOS/Android) 클라 |
| `apps/desktop` | 292 | 3.2 | Electron 셸(web 래핑 + 네이티브 IPC) |
| `apps/marketing` | 84 | 2.2 | 정적 사이트 |
| `packages/*` (**29개**) | 1,259 | 17.0 | `client-runtime`(383/3.3)·`contracts`(108/1.1)·`shared`(227) · `provider-*` **10종**(core/acp/acp-registry/opencode/pi/cursor/grok/muse/fx/testing) · `source-control-*` **9종**(core/definitions/github/gitlab/gitcafe/bitbucket/azure-devops/forgejo/testing) · `secret-provider-*` 3종 · `effect-acp`·`effect-codex-app-server`·`ssh`·`tailscale` |
| **합계(제외 적용)** | **7,112** | **97.5** | 원본 29,137/321.6MB의 24% |

언어 구성(제외 적용): `.ts` 4,927/50.2MB, `.tsx` 1,276/10.7MB, **테스트 파일 1,889개**(전체의 26.6%), `.ndjson` 159/7.0MB(프로바이더 리플레이 픽스처 — 바이너리성 데이터).

디렉터리 집중도: `apps/server/src/orchestration-v2` **15.2MB**, 그중 테스트 `.test.ts`가 대부분이고 최대 소스는 `Orchestrator.ts`(520KB)·`ProjectionStore.ts`(307KB)·`ClaudeAdapterV2.ts`(471KB). `apps/web/src/components` 21.9MB(최대 `ChatView.tsx` 170KB/4,024줄).

### 1.2 빌드·캐시 체계 (여기가 t3code의 숨은 강점)

| 요소 | 근거 | 내용 |
|---|---|---|
| 태스크 러너 | 루트 `vite.config.ts:83-92` | `run.cache` + **원격 태스크 캐시**. 엔드포인트는 `.task-cache.json`(79바이트): `t3-task-cache.pinglabs.workers.dev/projects/t3code` → **머신 간 빌드/타입체크 재사용** |
| typecheck | `apps/web/vite.config.ts:259-265` | `cache: { input: [{auto:true}, "!*.tsbuildinfo"] }` — tsc-rs가 읽으면서 tsbuildinfo를 다시 써 캐시가 깨지던 문제를 입력에서 제외해 해결 |
| 번들러 | `apps/web/vite.config.ts:282-283` | `tanstackRouter({ autoCodeSplitting: true })` + `react({ compiler: true })` → **라우트 단위 자동 분할 + React Compiler** |
| 분할 안전장치 | 같은 파일 `:225-247` | 빌드 후 **chunk import cycle 검사**(사이클이면 빌드 실패). "청크가 자기 의존보다 먼저 실행되어 앱이 하얗게 되는" 부류를 CI에서 차단 |
| clone 방지 | `:31-48` | `@pierre/diffs/react`의 `CodeView` 직접 import 금지(가상화 기하가 있는 `StyledDiffCodeView` 사용) — 성능 회귀를 **lint로** 막는다 |
| 린트 성능 규칙 | 루트 `vite.config.ts:215-221,232,575` | `categories.perf: "warn"`, `oxc/no-accumulating-spread=error`, `react/memo-dependencies=error`, `t3code/no-manual-memo=error`(직접 `useMemo` 금지 — React Compiler가 맡는다) |
| dev 서버 워밍 | `apps/web/vite.config.ts:342-346` | `warmup.clientFiles: ["./src/main.tsx"]` — tailnet 원격 접속 시 직렬 transform 대기 제거 |
| dev 압축 | `:210-223` | brotli **quality 5** 고정("기본 11은 전송 정체를 인코딩 정체로 바꾼다") |
| 무거운 의존성 | `apps/web/package.json:5-95` | pdfjs 6.4, `@betteroffice/{docx,pptx,xlsx}`, sql.js, jszip, katex, `@pierre/diffs`, tiptap, clerk — **모두 `React.lazy` 경계 뒤**(32곳 lazy 확인: `BrowserDocumentFrame.tsx:75`, `MarkdownMath.tsx:7`, `OfficeViewer.tsx:20-25` 등) |

### 1.3 저장소가 자체 보유한 성능 계측(내가 실행하지 않은 것들)

| 하네스 | 경로 | 무엇을 재나 |
|---|---|---|
| 렌더 시나리오 | `apps/web/test/{renderScenarios,appScenarios}.renders.tsx` + `test/renderCounter.ts` | React **커밋 수 / 컴포넌트 렌더 수**를 DevTools 훅으로 계산. 시나리오: `mount-thread`, `stream-assistant-turn`(30델타), `type-in-composer`, `switch-thread`, `app-stream-assistant-turn`, `app-terminal-updates-in-other-thread`, `app-type-in-composer`, `app-toggle-diff-panel`, `app-git-status-updates-with-side-chat` |
| 렌더 비교 CLI | `scripts/render-metrics.ts:1-30` | 3회 실행해 **결정성**을 요구하고(불일치 시 실패) before/after 커밋·렌더 델타 표 생성 |
| 클라 벤치 | `apps/web/src/performance.bench.ts`, `packages/client-runtime/src/remotePerformance.bench.ts` | 1,000 스레드 정렬/선택, 시간 포맷, **100 vs 1,000 메시지에 델타 200회 적용** |
| 서버 perf 테스트 | `package.json:42` `test:perf:v2-wire` (16개 파일) | 와이어 프로젝션·페이징·전송 페이로드 |
| 전송 예산 테스트 | `apps/server/src/orchestration-v2/ThreadTransportPerformance.test.ts:17-22,93-95` | `PRE_OMISSION = 8,790B` → `MAX = 1,024B`(≤10%) 강제 |
| 시작 프로파일러 | `scripts/profile-startup.mjs:1-30` | Node `--import` 훅 + inspector로 **모듈 로드·V8 startup 프로파일** |
| dev DB 복제 | `AGENTS.md`(§Test data) | `migrate-dev-db`로 실데이터 500MiB 상한 시드 |

---

## §2 개선안 (효과 대비 비용 순, 상위 7건)

### 2.1 `[즉시]` 스트리밍 코얼레서의 선행 50ms 대기 — 첫 델타가 창 종료까지 잡힌다

**근거**: `apps/server/src/orchestration-v2/ThreadLiveEventCoalescer.ts:18` `const COALESCE_WINDOW = Duration.millis(50);`
`:153-157` 첫 코얼레스 항목이 들어오면 **즉시** 50ms 타이머를 걸고, `:119-134` `flushWindow`는 `Effect.sleep(COALESCE_WINDOW)` **뒤에** `flushPending()`을 한다.

```ts
// as-is: 첫 항목도 무조건 창이 끝나야 나간다
if (pendingUpdates.length === 1) windowFiber = forkIn(flushWindow(gen), scope);  // :154-157
flushWindow = sleep(50ms) |> mutex → flushPending()                              // :119-134

// to-be: 첫 항목은 즉시, 이어지는 항목만 창으로 묶는다 (leading + trailing)
yield* flushPending();                       // 첫 항목 즉시 방출
if (pendingUpdates.length === 0) { /* 창 예약 */ }
```
또는 `Queue.offer` 직후 `Effect.sleep(0)` 후 한 번 더 pull해 "이미 대기 중인 항목이 있으면 그때만 50ms 창"을 적용.

- **효과**: 스트리밍 첫 토큰이 화면에 닿기까지의 **증폭 지연 최대 50ms 제거**. 총 이벤트 수·대역폭·서버 부하는 그대로(창 안 중복 제거는 유지).
- **위험**: 낮음. 다만 `coalesceLiveUpdatedEvents`가 같은 키를 합치는 전제가 "창 안에 여러 델타가 있다"는 것이므로, 선행 1건이 즉시 나가면 리렌더가 1회 늘 수 있다 → 트레이드오프는 측정으로 결정.
- **측정**: (a) `apps/web/test/appScenarios.renders.tsx:177-219` `app-stream-assistant-turn`에서 **커밋/렌더 카운트 증가분** 확인, (b) 서버에 `Effect.fn` span 하나를 코얼레서 offer→ws send에 붙여 **p50/p95 지연**(추정 50ms→수 ms), (c) 사용자 지표는 "첫 토큰→첫 페인트"를 `PerformanceObserver('paint')`로.

### 2.2 `[즉시]` `message.updated` 적용 경로만 정방향 선형스캔 + 전체 배열 복사

**근거**: `packages/client-runtime/src/state/orchestrationV2Projection.ts:53-62`

```ts
function upsertEntity<T extends { readonly id: unknown }>(items: ReadonlyArray<T>, item: T) {
  const index = items.findIndex((candidate) => candidate.id === item.id);  // 0번부터 O(n)
  if (index === -1) return [...items, item];
  const next = [...items];   // 전체 복사
  next[index] = item;
  return next;
}
```
`message.updated`는 이 함수를 그대로 쓴다(`:370`). 반면 **같은 파일의 `turn-item.updated`는 이미 역방향 스캔으로 최적화**되어 있다(`:375-379`):

```ts
// Streamed updates land on the newest items, so search from the end.
let itemIndex = projection.turnItems.length - 1;
while (itemIndex >= 0 && projection.turnItems[itemIndex]!.id !== payload.id) itemIndex -= 1;
```
즉 메인테이너가 문제를 인지하고 한쪽 경로만 고쳤다. 메시지 배열도 **항상 append 순서**이므로(스레드 이력), 스트리밍 델타가 노리는 대상은 거의 항상 마지막 원소다.

```ts
// to-be: 메시지는 append 순서 → 뒤에서부터(코얼레서가 최신 메시지 델타를 합치므로 1~2 스텝)
function upsertNewestFirst<T extends {id: unknown}>(items: ReadonlyArray<T>, item: T) {
  for (let i = items.length - 1, steps = 0; i >= 0 && steps < 8; i -= 1, steps += 1) {
    if (items[i]!.id === item.id) { const next = items.slice(); next[i] = item; return next; }
  }
  return upsertEntity(items, item); // tail miss → 기존 경로
}
```
- **효과**: 메시지 1,000건 스레드에서 델타 1건당 배열 스캔 1,000회 + 1,000-ref 복사 → 역방향 1~8회. 저장소 자체 벤치가 바로 이 축을 잰다(`remotePerformance.bench.ts:114-140`, `for count of [100, 1000]`). 정확한 배수는 실행하지 않았으므로 **미측정**.
- **주의(정직)**: 서버 코얼레서가 50ms 창으로 묶으므로 실제 적용률은 스레드당 ≤20회/s다. 따라서 **체감 효과는 제한적**이고, 10k 메시지급 장기 스레드·대량 리플레이에서 의미가 커진다. 그래도 코드가 3줄 수준이라 비용 대비 이득.
- **측정**: `remotePerformance.bench.ts`에 `count of [100, 1000, 10000]`와 "마지막 메시지 갱신" 케이스를 추가해 `bench(...).run()` 출력 비교. 동작 고정은 `orchestrationV2Projection.test.ts`(23KB, 델타 적용 케이스 다수)로.

### 2.3 `[단기]` dedupe 본문 복원이 델타마다 3번의 역방향 전체스캔을 한다

**근거**: `orchestrationV2Projection.ts:65-72`

```ts
function latestBodies(projection) {
  return {
    messageText:   (id) => projection.messages.findLast((m) => m.id === id)?.text,
    checkpointFiles:(id) => projection.checkpoints.findLast((c) => c.id === id)?.files,
    subagent:      (id) => projection.subagents.findLast((s) => s.id === id),
  };
}
```
`:374` `restoreTurnItemBodies(event.payload, latestBodies(projection))` — **turn-item 델타마다** 새 클로저 3개를 만들고, dedupe된 본문이 있으면 각 배열을 끝에서부터 끝까지 훑을 수 있다(없으면 worst case 전 구간).

- **to-be**: 프로젝션 객체를 키로 하는 `WeakMap<projection, {messages: Map, checkpoints: Map, subagents: Map}>`을 두고 이벤트 적용 때 증분 갱신(§2.2의 인덱스와 같은 자료구조 재사용). `structuredClone`·`findLast` 없이 O(1) 조회.
- **효과**: turn-item 델타당 3×O(n) 제거. `latestBodies`가 매번 새 객체를 만들므로 **할당 3건/델타**도 사라진다.
- **위험**: 캐시 무효화 버그가 나면 본문이 유실된다 → `restoreTurnItemBodies` 관련 기존 테스트(`orchestrationV2Projection.test.ts`, `threadHistoryMerge.test.ts`)를 먼저 돌려 기준선 확보.
- **측정**: `apps/web/src/performance.bench.ts` 패턴(vite-plus `bench`)으로 turnItems 2,000건 + dedupe 비율 50% 픽스처.

### 2.4 `[단기]` `run.updated`/`run-attempt.updated`가 visible rows 전체를 매번 재계산

**근거**: `orchestrationV2Projection.ts:301-305`, `:318-322` — 런/시도 갱신마다 `activeVisibleTurnItems(next)` 호출. 이 함수(`:101-121`)는 **호출마다** `createOrchestrationV2TurnItemVisibility(...)` 클로저를 새로 만들고 `visibleTurnItems` 전 행을 순회한다. `:83-87` `renumberVisibleItems`는 조건이 맞으면 행 수만큼 객체를 새로 만든다.

같은 파일이 더 뜨거운 경로(`turn-item.updated`)에서는 이 비용을 이미 피한다 — `:395-401`:
```ts
// Only interrupt requests can change another item's visibility. Streaming
// text/tool updates must not recheck every row against every run.
const visible = payload.type === "run_interrupt_request" || previous?.type === "run_interrupt_request"
  ? { ...next, visibleTurnItems: activeVisibleTurnItems(next) } : next;
```
→ 남은 것은 런 상태 전이다. 한 턴에 `preparing→queued→starting→running→completed`(+attempt)로 여러 번 발생하고, 각 이벤트가 **전체 타임라인 폭**을 다시 훑는다.

- **to-be**: `run.status`가 가시성에 영향을 주는 전이(예: `UNFINISHED_RUN_STATUSES` 경계, `:136-142`)일 때만 재계산하고, 그 외에는 `dirty` 플래그만 세운 뒤 다음 렌더/스냅샷에서 1회 재계산. 또는 `visibleTurnItems`를 런 상태의 함수로 보고 계산 자체를 지연.
- **효과**: 긴 스레드(수백~수천 visible rows)에서 턴당 수회의 O(rows) 순회·할당 제거. 정적 추정으로 rows 1,000 × 런 이벤트 5회 = 5,000행 재검사/턴.
- **위험**: 중간. 가시성 규칙이 미묘하다(부분 타임라인 워터마크 `:177-191`). 이벤트 적용 후 `visibleTurnItems` 불변식을 검증하는 테스트를 먼저 추가할 것.
- **측정**: `orchestrationV2Projection.test.ts`에 "rows 2,000에서 run.updated 100회" 벤치 + 렌더 하네스의 `app-stream-assistant-turn` 렌더 카운트가 변하지 않음을 확인(가시성 결과 동일성).

### 2.5 `[즉시]` 서버 fan-out: 와이어 프로젝션이 **구독자마다** 다시 돌고, publish는 타입 수만큼 배열을 만든다

**(a) 구독자별 재계산.** `WireProjection.ts:314-324`의 `projectDomainEventForWire`는 `turn-item.updated`마다 `projectTurnItemForWire`를 호출하고, 그 안에서 문자열 필드를 `truncateDetail`(`:32-50`)로 자른다. `truncateDetail`은 **길이를 재기 위해 매번 UTF-8 인코딩**한다(`:38` `Buffer.byteLength(value, "utf8")`) — 길이가 상한 안이어도 이 스캔은 실행된다. `dynamic_tool`은 추가로 `summarizeDynamicValue`가 **값 전체를 `JSON.stringify`**(`:73`)할 수 있다.

문제는 이 투영이 **한 번이 아니라 구독(subscription)마다** 돈다는 것이다:
- 라이브: `LiveStreamBudget.ts:354` `Stream.map(input.project)` — 구독 스트림 안에서 매 이벤트.
- 리플레이: `apps/server/src/ws.ts:881-886` `Stream.map((stored) => ({ ..., event: projectDomainEventForWire(stored.event) }))` — 구독마다 다시.

PubSub은 **같은 `OrchestrationV2StoredEvent` 객체**를 모든 구독자에게 넘기므로(커밋 후 불변), 같은 이벤트를 N명이 보면 잘림·UTF-8 스캔·`JSON.stringify`가 **N번** 반복된다. t3code는 웹·데스크톱·모바일·원격 peer가 동시에 같은 스레드를 볼 수 있는 제품이다(AGENTS.md "Multi-surface").

```ts
// to-be: 이벤트 객체 식별자 기준 WeakMap 메모 (파일 내 기존 패턴: WireProjection.ts:108 cappedCheckpointFiles)
const projectedEvents = new WeakMap<OrchestrationV2DomainEvent, OrchestrationV2DomainEvent>();
export function projectDomainEventForWire(event) {
  const cached = projectedEvents.get(event);
  if (cached !== undefined) return cached;
  const projected = /* 기존 switch */;
  projectedEvents.set(event, projected);
  return projected;
}
```
(경계: `makeLiveBodyDeduper`는 "구독마다 다른 것을 기억"하는 게 의도된 설계이므로 `:334-339` 주석대로 **메모 대상이 아니다**. 메모는 순수 함수인 wire 투영에만 적용한다.)

**(b) 타입별 publish.** `EventSink.ts:228-238`은 배치마다 `liveEventsByType`를 순회하며 `events.filter(...)`로 배열을 새로 만든다. 타입 맵은 지연 생성 후 **삭제되지 않는다**(`:849-857`). 확인된 `eventType` 구독은 7~8종(`CommandOutputHub.ts:215`, `Orchestrator.ts:13729,13766-13768`, `ThreadLaunchService.ts:781`, `peer/RemoteDelegation.ts:585`, `mcp/OrchestratorMcpService.ts:128-132`).

```ts
// to-be: 배치를 한 번만 버킷팅하고, 버킷이 있는 타입에만 publish. 마지막 구독 해제 시 map entry 삭제.
const buckets = new Map<Type, StoredEvent[]>();
for (const e of events) { const b = buckets.get(e.event.type); b ? b.push(e) : buckets.set(e.event.type, [e]); }
for (const [type, ps] of liveEventsByType) { const b = buckets.get(type); if (b) yield* PubSub.publishAll(ps, b); }
```

- **효과**: (a) 서버 CPU가 **동시 구독자 수에 비례하지 않게** 된다(정적 추정: 구독자 3~4명이면 투영 비용 1/3~1/4). 잘림은 대부분 바이트 예산 근처에서만 발생하지만 UTF-8 스캔과 `dynamic_tool` 요약은 매번 돈다. (b) publish당 배열 할당 O(타입수) 제거.
- **위험**: 낮음. (a)는 순수 함수 메모이제이션이므로 위험은 "이벤트 객체를 제자리에서 변형하는 코드가 있는가"뿐 — 커밋된 스토어드 이벤트는 불변이고, `WireProjection.test.ts:339`가 "투영은 멱등"을 이미 검증한다(`expect(projectDomainEventForWire(projected)).toEqual(projected)`).
- **측정**: 구독자 1/2/4명을 붙여 같은 이벤트 1,000건을 흘려보내는 서버 테스트를 추가하고 `Effect.withSpan("wire.project")`의 총 시간이 구독자 수에 평탄한지 확인. 회귀 방지는 `vp run test:perf:v2-wire`(package.json:42)와 `WireProjection.test.ts`·`ThreadStream.test.ts`.

### 2.6 `[단기]` 웹 프로덕션 빌드가 sourcemap을 기본으로 만든다

**근거**: `apps/web/vite.config.ts:75-80`
```ts
const buildSourcemap: boolean | "hidden" = sourcemapEnv === "0" || "false" ? false
  : sourcemapEnv === "hidden" ? "hidden" : true;   // 기본값 = true
```
`:377-391`에서 `build.sourcemap = buildSourcemap`. 같은 파일 `:68-70`은 "라이선스 매니페스트가 빌드의 약 2초"라며 `builtDev`에서는 생략하지만, sourcemap은 **기본 켜짐**이다. 이 앱은 `dist`에 pdfjs wasm·폰트·office 청크까지 포함하는 대형 번들이다(정적 추정: 청크 수십 개, 수 MB 단위).

- **to-be**: CI/프로덕션 아티팩트는 `T3CODE_WEB_SOURCEMAP=hidden`(또는 `0`)을 기본으로, 릴리스 파이프라인에서만 별도 업로드. 로컬 `dev:built`는 켜 둔다.
- **효과**: 빌드 시간·`dist` 크기. 정확한 수치는 미측정 — A/B로 재야 한다.
- **위험**: 디버깅성 저하(에러 스택이 난독화). `hidden`이면 소스맵은 생성되지만 참조 주석이 없어 공개 노출은 없다 → **`hidden` 권장**.
- **측정**: `T3CODE_WEB_SOURCEMAP=0` vs `hidden` vs 기본으로 각각 `vp build` 시간(`hyperfine` 없이 `Measure-Command`)과 `dist` 크기 비교. CI 로그의 빌드 스텝 시간이 지표.

### 2.7 `[단기]` `ChatView.tsx` 4,024줄이 스토어를 12곳에서 직접 구독한다

**근거**: `apps/web/src/components/ChatView.tsx` — 파일 4,024줄. 같은 파일에서 `useComposerDraftStore(` 12회, `useAtomValue(` 4회, `useState` 28개, `useStableCallback` 33회. 대표 지점: `:580-616`(composer draft의 runtimeMode/interactionMode/activeProvider/modelSelection/attachments/contexts/content 여부를 **각각** 구독), `:673`, `:1630-1635`.

`useComposerDraftStore`는 zustand 계열 selector 구독이므로, 프롬프트 한 글자 입력(`setComposerDraftPrompt`, `:615`)마다 **이 컴포넌트의 모든 selector가 재평가**되고 ChatView 함수 본문이 다시 실행된다. React Compiler가 자식 경계를 메모이즈하므로 실제 DOM 비용은 제한되지만, 4,024줄 본문 + derived 계산(`:1539-1560` timeline entries, `:1784` routing hook 등)이 입력 이벤트마다 도는 구조다.

- **to-be**: composer 표시에만 쓰이는 값(모델/모드/첨부/컨텍스트 플래그)을 `ChatComposer`(이미 존재, 141KB) 또는 전용 leaf로 내리고 ChatView는 콜백만 전달. 구독은 leaf에서.
- **효과**: 타이핑당 재렌더 범위 축소. **추정**이며 하네스로 반드시 확인해야 한다(이 파일은 하네스 시나리오의 주인공이다).
- **위험**: 중간(리팩터 범위가 넓고 폼 상태 동기화가 얽혀 있다). `t3code/no-unreasoned-state-sync` 린트가 effect 남용을 막아주긴 한다.
- **측정**: `apps/web/test/appScenarios.renders.tsx:253-262` `app-type-in-composer`의 `renders`/`components` **before/after**를 `scripts/render-metrics.ts compare`로 비교(커밋 수는 `commits: false` 처리되는 시나리오가 있으니 렌더 수 기준).

### 2.8 `[대조]` t3code가 이미 잘해 둔 것 — 여기서 베낄 것이 §4의 원천이다

| 영역 | 근거 | 내용 |
|---|---|---|
| WS 페이로드 예산 | `WireProjection.ts:24-30`, `ThreadTransportPerformance.test.ts:17-22` | `MAX_WIRE_CHECKPOINT_FILES=200`, 상세문자열 32KB, 동적값 16KB, 온디맨드 256KB, 잘림 마커. **테스트로 8,790B → ≤1,024B를 강제** |
| 이벤트 경계 값 | `WireProjection.ts:24-30,241-259` | `MAX_WIRE_CHECKPOINT_FILES=200`, 상세문자열 32KB, 동적값 16KB, 온디맨드 256KB, 잘림 마커 포함 계산 |
| 본문 중복 제거 | `WireProjection.ts:326-385` | 구독마다 `makeLiveBodyDeduper`가 "이미 보낸 본문"을 LRU 32건으로 기억해 반복 본문을 비워 보낸다(`:336-339` 주석: 순서 뒤집힘은 바이트만 손해). 여기서 `message.updated`는 본문을 **보내는** 쪽이라 클라 `latestBodies`(§2.3)의 대응물 |
| 스트리밍 코얼레스 | `ThreadLiveEventCoalescer.ts:18-19,50-77` | 50ms 창 + 512건 상한, 같은 엔티티의 연속 갱신만 제거(순서·커서 안전) |
| 배치 적용 | `packages/client-runtime/src/state/threads.ts:479-598` | 이벤트 배열을 **한 번의 `SubscriptionRef.modify`**로 적용, 변화 없으면 상태 객체 유지("A batch that changed nothing keeps its state object, so readers do not re-render") |
| 캐시 쓰기 병합 | `threads.ts:280-286,113-125` | `Queue.sliding(1)`로 스냅샷 영속화 병합, 실행 중 런이 있으면 영속화 생략 |
| 터미널 백프레셔 | `terminal/OutputProtocol.ts:9-13,45-70,73-75,153-167` | 8청크/64KB 미만이면 ack 즉시, 넘으면 ack 보류; 8MB 큐 상한, `takeBatch`로 묶음 전송 |
| 터미널 히스토리 | `terminal/Manager.ts:875-1044` | 청크 링 + codepoint 바이트 트림(`:1007-1017`) + 2,048 이후 주기 압축(`:1018-1025`) + 조인 결과 캐시(`:1037-1044`) |
| SQLite | `persistence/Sqlite.ts:14-44,52-64` | WAL, `synchronous=NORMAL`(이유 주석 포함), `temp_store=MEMORY`, `journal_size_limit=32MB`, `checkpoint_fullfsync`, `ANALYZE` 재생성, **reader worker + checkpoint worker 스레드** |
| 스케줄링 | `EffectWorker.ts:900-945` | 알림 기반 wake + liveness 폴링 fallback(1,000ms 상한), `availableAt` 재시도 시 정확한 sleep |
| 렌더 측정 | §1.3 | 커밋/렌더 카운트 CI 하네스 + 3회 결정성 요구 |
| GPU | `apps/web/src/index.css:564-655` | 애니메이션 기본 `paused`(`--visible-animation-state`), 가시성에만 재생, `transform` 기반, reduced-motion 분기. (예외: `:551-577` `background-position` + `background-clip:text` shine은 13.6 step/s 페인트 — AGENTS.md의 "연속 리페인트 금지" 원칙과 살짝 어긋나지만 상한이 있다) |

---

## §3 어떻게 측정해 증명할지

### 3.1 저장소에 이미 있는 것 (그대로 쓰면 된다)

```bash
# 1) 렌더 커밋/렌더 수 — 리그레션 게이트
node scripts/render-metrics.ts run --out ../before.json          # 변경 전 체크아웃
node scripts/render-metrics.ts run --out ../after.json           # 변경 후
node scripts/render-metrics.ts compare ../before.json ../after.json --top 10

# 2) 클라 벤치(정렬/선택/포맷/원격 스냅샷/델타 적용)
vp test run apps/web/src/performance.bench.ts \
  packages/client-runtime/src/remotePerformance.bench.ts

# 3) 와이어 계열 회귀(페이징·전송 예산·스트림·정책)
vp run test:perf:v2-wire

# 4) 서버 시작 비용
T3CODE_STARTUP_PROFILE_DIR=/tmp/t3prof NODE_OPTIONS="--no-node-snapshot --import=$PWD/scripts/profile-startup.mjs" \
  node apps/server/src/bin.ts
```

### 3.2 개선안별 증명 설계

| # | 지표 | 방법 | 합격 기준(예시) |
|---|---|---|---|
| 1 | 첫 델타→ws send 지연 | 서버 `Effect.fn("...coalescer.flush")` span p50/p95 (`@effect/opentelemetry` 이미 배선) | p95가 50ms 이상↓, 렌더 수 ≤+10% |
| 2 | 델타 적용 시간 | `remotePerformance.bench.ts:114-140`에 `[100,1000,10000]` + "마지막 메시지" 케이스 추가 | 1,000건에서 apply가 기존 대비 선형 기울기 감소 |
| 3 | dedupe 조회 비용 | `performance.bench.ts` 패턴으로 turnItems 2,000 + dedupe 50% | 할당·시간 동시 측정(벤치 harness가 시간 제공) |
| 4 | run 갱신 비용 | 위와 동일한 단위 벤치 + 렌더 하네스 `app-stream-assistant-turn` | 렌더 수 불변(동작 동일) + 벤치 시간↓ |
| 5 | publish 시간 | `EventSink.publishLiveEvents` span + PubSub 구독 수 로그 | 배치당 시간↓, 이벤트 순서 테스트 green |
| 6 | 빌드 시간/dist 크기 | 3가지 `T3CODE_WEB_SOURCEMAP`로 `vp build` 시간·`dist` 용량 3회 평균 | CI 스텝 시간↓, `dist` 수십 %↓ (추정) |
| 7 | 타이핑 재렌더 | `app-type-in-composer` 시나리오 renders/commits before/after | renders 증가 없이 ChatView 렌더 수↓ |

**주의**: 렌더 하네스는 3회 동일 카운트를 요구하므로(`render-metrics.ts:1-30` `run` 설명), 프레임 타이밍에 민감한 시나리오(`switch-thread`)는 이미 `commits: null`로 제외되어 있다. 새 시나리오를 추가할 때도 같은 규약을 지켜야 노이즈를 안 만든다.

---

## §4 TalkAbout 적용안 (실제 코드 확인 후)

TalkAbout 쪽은 **t3code보다 개선 여지가 훨씬 크다**. 아래는 t3code에서 확인한 원리 → TalkAbout 실코드 지목 → 구체 수정 순서다. (모두 `talkabout/` 아래 실제 파일, 라인은 현재 워크스페이스 기준.)

### 4.1 서버 SSE: "0.25초 슬립 폴링 × 반복마다 전체 스냅샷 2회"

**현재 코드** — `talkabout/core.py:694-760`:
```python
with self.store.listener() as wait:
    while True:
        actor, rooms, _ = self.store.snapshot(token)          # 695  ← 반복마다 1회
        ...
        for rid in room_ids:
            history = rooms[rid]["events"]
            selected = [e for e in history if e["seq"] > offsets[rid]][:16]   # 732  O(이력길이)
        latest_actor, latest_rooms, _ = self.store.snapshot(token)             # 736  ← 또 1회
        if events or time.monotonic() >= end or rebase: ...
        wait(min(0.25, max(0, end - time.monotonic())))                        # 760  ← 0.25s 폴링
```
`PostgresStore.snapshot`은 `store.py:404-413`에서 `rooms`를 읽고 **방마다** `_room()`을 호출하며, `_room`(`:356-362`)은 **방마다 별도 `SELECT payload FROM events ... LIMIT 2049`**를 실행한다(N+1). 즉 클라이언트 1개가 유휴 상태여도 **초당 4회 × (2 + 2×방수) 쿼리**가 발생한다. 64방 상한(`:400`)이면 이론상 초당 ~500 쿼리/유휴 연결.

**t3code 교훈**: 대기는 이벤트 알림으로, 조회는 1회로. `EffectWorker.ts:900-945`("Notifications wake promptly; availableAt persists retry timing, and liveness polling recovers missed wakeups") + `EventSink.ts:228-238`(publish 1회 fan-out).

**적용**:
1. `core.events` 루프에서 `snapshot`을 **이벤트 도착 시 1회만** 수행. 대기는 `store.listener()`의 `wait` 한 번(권장 타임아웃 1~2s, LISTEN/NOTIFY라 즉시 깨어난다)으로. 루프 반복은 "깨어난 뒤 1회 스냅샷"으로 제한.
2. `snapshot()`이 방마다 events를 읽는 N+1 제거: `SELECT room_id, seq, payload FROM events WHERE room_id = ANY(%s) AND seq > %s ORDER BY room_id, seq`로 **1쿼리 배치**, 또는 `core.events`가 방별 `last_event_seq`와 커서 오프셋만 알면 되므로 **이벤트 본문은 필요한 방만** 가져오기(현재 `[:16]` 절단과 동일 의미).
3. `:732`의 `[e for e in history ...]`는 이력이 DB에서 2,049건까지 로드되므로(주석 `store.py:359`: "switch to paged event queries before raising room limits") **seq 인덱스 기반 이분 탐색**으로 교체: 이벤트가 `seq` 단조 증가면 `bisect.bisect_right(seqs, offset)`.
4. 측정: `pytest`에 계측 픽스처를 만들어 `core.events` 1회당 쿼리 수를 세는 어서션(예: `cur.execute` 카운터 → "유휴 3초 동안 쿼리 ≤ 6"). 이건 t3code의 `ThreadTransportPerformance.test.ts` 방식(숫자 상한을 테스트로 고정)의 직접 이식이다.

### 4.2 서버 SSE 응답: 배치당 thread hop + 전체 JSON 재직렬화 + 60초 재연결

**현재 코드** — `talkabout/api.py:115-144`:
```python
duration = float(request.query_params.get("duration_seconds", 60)); require(0 <= duration <= 60, ...)  # 119-122
data = await run_in_threadpool(core.events, credential, cursor, min(10, max(0, end-time.monotonic())), False, room_id)  # 131
yield "id: " + cursor + "\nevent: room_update\ndata: " + json.dumps(data, ensure_ascii=True) + "\n\n"                # 134
...
if time.monotonic() >= end:
    yield 'event: stream_end\ndata: {"reason":"budget"}\n\n'; return                                                # 141-143
```
- 매 배치마다 `run_in_threadpool`(스레드 홉) + `json.dumps`(전체 페이로드 재생성).
- **60초 상한** 때문에 연결이 주기적으로 끊기고, 클라(`app.mjs:967`)는 재연결 후 `refresh()`를 다시 한다 → 재연결마다 인증·`core.read`·커서 재개 비용.

**t3code 교훈**: `WireProjection.ts:24-30` — **와이어에 나가는 것을 상한으로 자른다**(문자열 32KB, 체크포인트 파일 200개, 동적값 16KB, 잘림 마커 포함 계산). `OutputProtocol.ts:9-13,73-75` — **바이트 예산**(8청크/64KB 창, 8MB 큐)으로 느린 소비자를 서버가 버티게 한다.

**적용**:
1. 이벤트 payload에 **바이트 상한**을 두고 초과분은 잘라서 `truncated: true` + 요약을 보낸다. 특히 rebase 응답은 `rooms` 전체를 싣는다(`core.py:758-759` `"rooms": [public_room(rooms[r], actor) ...] if rebase else []`) — 이 경로가 가장 크다. TalkAbout의 `public_room` 직렬화 지점에 t3code식 바이트 예산(`MAX_DETAIL_STRING_BYTES`·잘림 마커·`truncated` 플래그)을 넣는다.
2. `json.dumps(..., ensure_ascii=True)`는 비ASCII(한국어)에서 바이트를 6배로 늘린다 — **`ensure_ascii=False` + `charset=utf-8`** 로 전환(프로토콜 호환성 확인 필요: SSE는 UTF-8 기본이므로 안전, 테스트로 고정).
3. 재연결 예산: duration 상한을 60s→300s로 올리되, 서버가 **하트비트 코멘트(`: keepalive\n\n`)** 를 주기적으로 보내 프록시 타임아웃을 막는다. 재연결 시 커서 연속성은 `last-event-id`로 이미 지원(`api.py:126`).
4. 측정: `ab`/`k6`로 (a) 유휴 연결 100개에서 서버 CPU·쿼리/초, (b) 배치당 µs(파이썬 `time.perf_counter` 계측을 테스트 픽스처로). 합격 기준 예: 유휴 100연결 시 쿼리/초가 현재의 1/10.

### 4.3 웹: SSE 이벤트마다 "전체 목록 HTTP 재조회 + 전체 DOM 재생성"

**현재 코드** — `talkabout/web/app.mjs:967-969`(1줄에 압축된 코드):
```js
function watchRoom(id){closeLive();const source=new EventSource('/api/v1/rooms/'+id+'/stream');liveSource=source;
  source.addEventListener('room_update',()=>{ if(liveSource===source&&view.selected===id&&user) refresh();}); ...}
async function refresh(){const epoch=++listEpoch;
  const rooms=await api('rooms',{access:'workspace'});      // ← SSE 이벤트 1건마다 HTTP 왕복
  if(epoch!==listEpoch||!user)return; renderRooms(rooms);   // ← 카드 전체 replaceChildren
  ...
  const optional=await Promise.allSettled([...]);            // ← 추가 조회들
```
`renderRooms`(`:656`)는 `el('rooms').replaceChildren(...rooms.map(...))`로 **전체 재생성**하고, 상세는 `:954`에서 `el('room-stage').innerHTML = stageMarkup(room)`로 통째 교체한다. 배경 폴링도 있다 — `:883` `startCommunityWatch`가 `setInterval(...,10000)`로 `refresh()`를 부른다.

**t3code 교훈**: 이벤트는 **증분 적용**(`orchestrationV2Projection.ts` 전체), 서버가 먼저 **50ms 창으로 합치고**(`ThreadLiveEventCoalescer.ts:18`), 클라는 **한 배치 = 한 번의 상태 교체**(`threads.ts:568-581`).

**적용(비용순)**:
1. **[S] 코얼레스**: `room_update`를 즉시 `refresh()`하지 말고 100ms 디바운스로 모아 1회만. (코드 3줄, 즉시 효과)
2. **[S] 중복 조회 제거**: SSE `data`에 이미 바뀐 방의 스냅샷/델타가 실려 오는데도 `GET /api/v1/rooms`를 다시 부른다 → **SSE payload로 상태를 갱신**하고 HTTP 재조회는 생략. `next_cursor`/`events` 구조를 `app.mjs`에서 소비하도록.
3. **[M] keyed 부분 갱신**: `renderRooms`를 "방 id → DOM 노드" 맵 유지 + 변경된 방만 교체로. `replaceChildren` 전체 재생성은 방 수 × 카드 마크업 비용 + 포커스/스크롤 소실. t3code의 `LegendList`(가상화)·`useIncrementalTimelineEntries`에 해당하는 최소판.
4. **[M] 부트 비용**: `index.html:2`가 `app.mjs`(123.6KB)+`state.mjs`(89.2KB, 실측: **i18n 라벨 표**이지 상태 스토어가 아니다)+`style.css`(55.4KB)를 한 번에 로드. t3code는 라우트 단위 자동 분할(`vite.config.ts:282`)로 초기 청크를 줄인다. 프레임워크가 없으므로 `import()` 동적 로드로 화면군(설정/연결마법사/방 상세)을 분리하는 것이 현실적.
5. 측정: 브라우저 Performance 타임라인에서 "SSE 이벤트 1건당 스크립트 시간·레이아웃 수"를 `performance.mark/measure`로 계측(현재 계측 코드는 **찾지 못했다**). t3code `renderCounter.ts`의 커밋 카운터를 바닐라로 이식하려면 DOM 변이 수(`MutationObserver`)로 대체 가능.

### 4.4 워커: `should_stop` 한 번 확인에 SQLite 연결 1개

**현재 코드** — `talkabout/worker.py:297` `client.should_stop = journal.stop_requested` → `:263-264` → `_stop_flag()`(`:241-264`)가 **호출마다 `sqlite3.connect(self.path, timeout=0.2)`**(`:252`)로 `settings`를 읽는다. 호출 지점은 `worker.py:97`(모든 HTTP 시도 전)과 `:127-131`(재시도 백오프 중 **최대 20ms 간격 폴링**).

→ 재시도 백오프 10초 동안 이론상 최대 **500회 SQLite 연결 개설**이 stop 확인만으로 발생한다(정적 추정).

**t3code 교훈**: 상태 신호는 **프로세스 내 신호**로. `Latch`/`Deferred`(`OutputProtocol.ts:99,134,177`), `PubSub`(`EventSink.ts:223`), `Queue.sliding`(`threads.ts:280`).

**적용**:
```python
# to-be: durable 상태는 주기적으로만 읽고, 폴링은 메모리 플래그로
self._stop_cache = False
self._stop_checked = 0.0
def stop_requested(self):
    now = time.monotonic()
    if now - self._stop_checked >= 0.5:            # 0.5s TTL, 백오프 폴링은 캐시 사용
        self._stop_cache = self.journal.stop_requested()
        self._stop_checked = now
    return self._stop_cache
```
추가로 `time.sleep(min(0.02, ...))` 루프(`:127-131`)는 `threading.Event.wait(timeout)`로 바꿔 즉시 깨어나게 한다(t3code가 sleep 폴링 대신 `Deferred.await`를 쓰는 것과 같은 이유).
- 측정: `pytest`에서 `sqlite3.connect`를 스파이로 감싸 "재시도 3회 백오프 1초 동안 connect 호출 ≤ 3"을 어서션. 회귀 방지까지 같이 된다.

### 4.5 쓰기 경로: 이벤트 1건 = INSERT 1회

**현재 코드** — `talkabout/store.py:365-373` `_flush`: `room["events"]`를 돌며 `conn.execute("INSERT INTO events ...")` **이벤트마다 1회**(`:370-372`) 후 `NOTIFY`.
**t3code 교훈**: 한 트랜잭션에 이벤트 배열을 **한 번에** 쓴다. `EventSink.ts:259-284`는 events+projection+receipt+outbox를 **단일 `withTransaction`**에 넣고, 터미널도 `takeBatch`로 묶어 보낸다(`OutputProtocol.ts:153-167`).

**적용**: `executemany("INSERT INTO events(room_id,seq,event_id,payload) VALUES(%s,%s,%s,%s)", rows)` + `cursor`/`cursors` 정리도 배치. `NOTIFY`는 트랜잭션당 1회(이미 그렇다).
- 측정: 한 턴에서 발생하는 `_flush` 왕복 수를 세는 어서션("턴당 INSERT 문 ≤ 이벤트 배치 수"), 또는 `pg_stat_statements`의 calls/row 비교.

### 4.6 §4 요약 표

| TalkAbout 지점 | 현재 | t3code 원천 | 우선순위 |
|---|---|---|---|
| `core.py:694-760` | 0.25s 슬립 폴링 + 반복당 `snapshot()` 2회 + `:732` 선형스캔 | `EffectWorker.ts:900-945`, `EventSink.ts:228-238` | **P0** |
| `store.py:404-413`, `:356-362` | 방마다 events 쿼리(N+1) | 1회 배치 조회(§2.5 원리) | **P0** |
| `web/app.mjs:967-969` | SSE마다 HTTP 재조회 + 전체 DOM 재생성 | `orchestrationV2Projection.ts`, `threads.ts:568-581` | **P0** |
| `worker.py:97,127-131,241-264` | stop 확인마다 SQLite 연결 | `Latch`/`Deferred`/`Queue` | **P1** |
| `api.py:131-143` | 배치당 thread hop·`ensure_ascii=True`·60s 재연결 | `WireProjection.ts:24-30`, `OutputProtocol.ts:9-13,73-75` | **P1** |
| `store.py:365-373` | 이벤트당 INSERT | 단일 트랜잭션 배치(`EventSink.ts:259-284`) | **P2** |
| `web/index.html:2` | 3파일 동시 로드(번들 분할 없음) | `vite.config.ts:282` 자동 분할 | **P2** |
| 측정 인프라 | perf 하네스 **찾지 못했다**(`tools/run_safe_regression.py`는 기능 회귀, `.ci-local/`도 동일) | `render-metrics.ts`, `test:perf:v2-wire` | **P0(선행)** |

**권고 순서**: (1) 측정 하네스 먼저(쿼리 카운터 어서션 + SSE 배치 계측) → (2) P0 세 건 → (3) P1/P2. t3code처럼 **숫자를 테스트로 고정**하지 않으면 이 저장소는 다시 폴링으로 돌아간다.

---

## §5 미확인 / 한계

1. **실행 측정 없음**: 모든 소요·배수는 정적 추정이다. 벤치(`performance.bench.ts`, `remotePerformance.bench.ts`, `render-metrics`)는 **존재와 코드만** 확인했고 수치를 인용하지 않았다.
2. **`.repos/` 미열람**: 브리프 지시대로 제외했다. 따라서 Effect 라이브러리(`effect-smol`) 내부 스케줄링·PubSub 특성(예: `PubSub.unbounded`의 백프레셔 부재, `Effect.sleep` 타이머 해상도)은 **미확인**이다. `PubSub.unbounded`(`EventSink.ts:223`)의 무제한 버퍼가 실제 위험인지 판단하려면 그쪽 코드가 필요하다.
3. **모바일 최소**: `apps/mobile`은 "웹과 같은 리액트 렌더 카운터를 별도 구현"(`test/renderCounter.ts:2-3` 주석이 `apps/mobile/src/features/development/render-counter.ts`를 지목) 정도만 확인했다. 네이티브(Vendor) 코드는 제외 대상이라 성능 판단 근거가 없다.
4. **`apps/desktop`(Electron)·`apps/marketing`**: 이번 라운드 범위에서 제외(데스크톱은 web 래핑이므로 web 결론이 대부분 상속된다고 **추정**할 뿐 확인하지 않았다).
5. **§2.7(ChatView 구독)은 반증 가능한 가설**이다. React Compiler가 켜져 있어(`apps/web/vite.config.ts:283`) 실제 재렌더가 얼마나 줄어드는지는 하네스 실행 없이는 모른다. 특히 `useComposerDraftStore`가 zustand `useStore`인지 `use-sync-external-store` 래퍼인지는 import 체인을 끝까지 확인하지 않았다(`ChatView.tsx:111` `@effect/atom-react`의 `useAtomValue` 4곳은 확인).
6. **§2.4의 가시성 재계산**: `activeVisibleTurnItems`가 실제로 몇 행을 도는지는 스레드 길이 분포에 달렸다. 저장소에 스레드 아이템 상한을 **찾지 못했다**(`_generated`/계약 스키마에서 `MAX` 상수 grep 실패) → 무제한일 가능성이 있으나 단정하지 않는다.
7. **`message.updated` 벤치의 현실성**: `remotePerformance.bench.ts:127`의 케이스 이름은 "apply 200 message updates to 1000 loaded messages"지만, **코얼레서가 없는 원시 적용 비용**을 잰다. 실제 파이프라인은 50ms 창으로 묶이므로(§2.2의 주의) 이 벤치 값이 그대로 사용자 체감이 되지 않는다.
8. **`ThreadTransportPerformance.test.ts`의 8,790B→1,024B**는 **command_execution 출력 8KB 1건**에 대한 단일 케이스다. 모든 이벤트 타입에 대한 예산 강제는 아니다(파일 99줄, 테스트 1개).
9. **TalkAbout 쿼리 수 추정**(§4.1 "초당 ~500")은 64방 상한·4Hz를 **곱한 이론값**이며 실측이 아니다. 또 지금 프로덕션이 `PostgresStore`인지 `SetupStore`인지는 `provision.py`/`deploy`를 확인하지 않아 단정하지 않았다(둘 다 같은 `snapshot` 이름을 갖지만 `SetupStore`는 `deepcopy` 기반이라 비용 구조가 다르다 — `store.py:146-152`).
10. **§2.5의 배수는 구독자 수에 달렸다**: 한 스레드를 동시에 구독하는 클라 수(웹 탭 여러 개·데스크톱·모바일·원격 peer)를 세는 코드/설정은 **찾지 못했다**. 1명이면 효과는 0에 가깝고, 3~4명이면 투영 비용이 1/3~1/4이 된다. 실제 fan-out 폭은 `ws.ts`의 스레드 구독 등록부를 계측해야 확정된다.
