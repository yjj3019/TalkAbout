# claude-mem 성능 정밀 분석 (R3) — 훅 지연 · 워커 · SQLite · 관측 LLM · 주입 토큰

- 분석 대상: `.analysis-repos/claude-mem/claude-mem-main/` (`thedotmack/claude-mem`, `main`, 2026-11-02 zip 스냅샷, v15.8.3 — `package.json:3`)
- 라이선스: Apache-2.0 (`package.json:19`)
- **방법: 순수 정적 분석.** 빌드·설치·실행 시도 없음(Bun/툴체인 미보유, 브리프 §4-8). 모든 수치는 저장소 안의 상수·주석·구조에서 유도한 **추정**이며, 실측이 아닌 곳은 "정적 추정"으로 표기했다.
- 실측 규모: `src/` TS/TSX 750파일 159,537줄(주석 포함), `plugin/` 197파일 **21.35MB**. 번들 산출물 `plugin/scripts/worker-service.cjs` **4,529,909B**, `server-service.cjs` 2,277,860B, `mcp-server.cjs` 908,410B, 소스맵 `worker-service.cjs.map` 4,732,038B.
- 성능 정의: 훅 1회당 **사용자 체감 지연**(Claude Code 응답이 멈추는 시간) + **토큰/비용**. 워커 지연·SQLite 비용·주입 토큰량·콜드 스타트를 하위 축으로 본다.

---

## §0 요약

### 0.1 핫패스 한눈에

| # | 훅 이벤트 | 실행 방식 | host 타임아웃 | 핸들러 자체 예산 | 사용자 체감 |
|---|---|---|---|---|---|
| H1 | `SessionStart` → `claude-mem start` | **async** | 60s | — | 없음(백그라운드) |
| H2 | `SessionStart` → `hook … context` | **sync** | 60s | `min(30s, 60s-5s)` (`host-hook-limits.ts:19-24,33-38`) | **세션 시작이 멈춘다** |
| H3 | `SessionStart(startup)` → `ensure-launcher.cjs` | **sync** | 180s | 없음(런처 `--version` spawnSync 10s/30s) | 첫 세션·설치 직후 크게 멈춘다 |
| H4 | `UserPromptSubmit` → `session-init` | **sync** | 15s | `10s`(Win `7s`) (`hook-constants.ts:12,128-134`) | **프롬프트 제출이 멈춘다** |
| H5 | `PreToolUse(Read\|Bash\|Grep\|Glob\|memory_*)` → `file-context` | **sync** | 15s | `3s` ×2 (`file-context.ts:44`, `memory-search.ts:156`) | **모든 Read가 멈출 수 있다** |
| H6 | `PostToolUse`/`PostToolUseFailure` → `observation` | **async** | 120s | spool + nudge ≤250ms | 없음(비동기) |
| H7 | `Stop` → `summarize` | **async** | 120s | — | 없음(비동기) |
| H8 | `SubagentStop` / `SessionEnd` | **async** | 30s / 없음 | — | 없음 |

근거: `plugin/hooks/hooks.json:17-179`. **동기 훅은 H2·H3·H4·H5 넷**이고, 이 넷이 "사용자 체감 지연"의 전부다. H6·H7은 설계상 사용자를 막지 않는다(비동기 + 디스크 spool + `settleHookSpoolNudges`가 최대 250ms만 대기 — `hook-command.ts:82-85`). 이건 **잘 만든 부분**이며 TalkAbout이 그대로 배울 지점이다(§4-T1).

### 0.2 개선 후보 표 (효과 대비 비용 순)

| 순위 | 지점 (file:line) | 원인 | 기대 효과 | 태그 | 비용 |
|---|---|---|---|---|---|
| 1 | `src/cli/handlers/context.ts:414-425` | `SHOW_TERMINAL_OUTPUT` 기본 `true`(`SettingsDefaultsManager.ts:581`)라 **SessionStart가 워커 GET을 2회** 직렬로 보낸다(모델용 + 색상용). 캐시 미스면 전체 렌더 2회 | SessionStart 캐시 미스 시 DB렌더 2→1, 훅 왕복 2→1 | `[즉시]` | S |
| 2 | `SettingsDefaultsManager.ts:523` + `ClaudeProvider.ts:1253-1323` | `CLAUDE_MEM_CLAUDE_OBSERVATION_BATCH_SIZE` 기본 **1** → **툴콜 1회 = 관측 LLM 턴 1회**. `awaitObserverAnswer`로 직렬화되어 배치=1이면 큐가 밀린다 | 툴콜 100회 세션의 관측 턴 100→13~25(배치 4~8), 입력 토큰 및 배치 백로그 급감 | `[즉시]` | S |
| 3 | `src/cli/handlers/file-context.ts:392,411` → `file-context-dedupe.ts:110,154` | PreToolUse마다 **훅 프로세스가 메인 SQLite를 직접 열고** dedupe 클레임을 읽고/쓴다(WAL 락 경합) | Read당 수 ms + 워커와의 WAL writer 경합 감소 | `[즉시]` | S |
| 4 | `src/cli/handlers/memory-search.ts:44-51,146` | 매 PreToolUse마다 `settings.json` **3개를 동기 readFileSync** + `shell-quote` 파싱 후 대부분 `null` 반환 | 툴콜당 0.5~2ms 절감(정적 추정), 경로 판정 실패 시 조기 반환 | `[즉시]` | S |
| 5 | `src/services/context/ContextBudget.ts:81-104` | 10,000자에 맞출 때 **전체 렌더를 최대 6회 반복**(`render()` 재호출, 관측 50개 × narrative) | 캐시 미스 렌더 비용 ~6배→1.x배 | `[단기]` | M |
| 6 | `src/services/worker/http/routes/SessionRoutes.ts:875-1025` | `/api/sessions/init` 1회에 **직렬 SQLite 왕복 8회**(createSDKSession→getSessionById→getPromptNumber→findDuplicate→reopen→saveUserPrompt→getUserPromptById→ensureGenerator) | UserPromptSubmit 워커 구간 왕복 8→2, p95 개선 | `[단기]` | M |
| 7 | `src/shared/hook-constants.ts:118-134` (프로세스 체인 전체) | 훅 1회 = 셸 → 런처 바이너리 → bun → **4.5MB 번들 파싱**. 저장소 스스로 "Windows 오버헤드 4-5s"로 측정해 8s를 예산에서 깎는다 | Windows 훅 1회 8s 오버헤드 → 1s 미만(경량 HTTP 경로) | `[장기]` | L |
| 8 | `src/services/sqlite/connection.ts:124-135` | `busy_timeout=5000`, `synchronous=NORMAL`, WAL은 있으나 `cache_size`/`mmap_size` 미설정 | 대형 DB에서 페이지 캐시 적중 개선(정적 추정, 소폭) | `[단기]` | S |

---

## §1 훅 경로 추적 (지연 예산)

### 1.1 훅 1회의 실제 프로세스 체인

`hooks.json`은 `command: "claude-mem"`(PATH 위 런처) → 런처가 bun으로 `worker-service.cjs`의 `hook <platform> <event>` 서브커맨드를 띄운다(`package.json:79-86`의 `worker:start`가 같은 번들을 쓰는 것이 방증). 저장소가 스스로 이 체인 비용을 상수로 박아 두었다:

```ts
// src/shared/hook-constants.ts:118-125
export function hookProcessOverheadMs(platform: NodeJS.Platform = process.platform): number {
  return platform === 'win32' ? 8_000 : 5_000;
}
```

주석은 "shell prelude, node bun-runner, bun start-up and the bundle load before it (**4-5 s on Windows**)"라고 명시한다(`:117-122`). 즉 **동기 훅 H2/H4/H5는 요청을 보내기도 전에 Windows에서 최대 5초를 오버헤드로 이미 쓴다**. 이것이 `SESSION_INIT_REQUEST`가 POSIX 10s인데 Windows는 7s로 줄어드는 이유다(`:127-134`). 사용자 체감으로는 "프롬프트를 엔터 쳤는데 1~8초 멈춤"이 여기서 나온다.

- 번들 크기: `worker-service.cjs` 4,529,909B. 매 훅마다 이 번들을 **파싱**한다(소스맵 4.7MB는 로드하지 않지만 설치 용량·AV 스캔 비용을 키운다).
- 훅마다 별도 프로세스이므로 `loadFromFileOnce()`(`hook-settings.ts`)·`getWorkerPort()`(`worker-utils.ts:382-390`) 같은 per-process 캐시는 **훅 사이에서 재사용되지 않는다**.

### 1.2 H3 — `ensure-launcher.cjs` (동기, 180s)

`SessionStart(startup)` 매처에 **동기**로 걸려 있고 타임아웃이 180초다(`hooks.json:47-58`). 하는 일은 PATH 위 런처 바이너리 검사이고, 없거나 낡았으면 **`bun build --compile`을 실행**한다:

```js
// ensure-launcher.cjs (plugin/scripts) — 발췌
Re(e,n){ return ["build","--compile",...,e,"--outfile",n] }   // 런처 재컴파일
B=12e4  // 그 spawn의 timeout 120s
```

`ensure-launcher.cjs`는 `--version` spawnSync(타임아웃 10s, Win 30s)로 프로토콜을 확인하고, `already-current`면 즉시 끝난다. **정상 상태 비용은 작지만 첫 실행·업그레이드 직후에는 수십 초**가 동기로 들어간다. H2(context)는 이 뒤에 오므로 콜드 설치의 첫 세션 체감 지연은 H3+H2 합이다.

### 1.3 H2 — SessionStart `context` (동기, 60s cap → 실효 30s)

`contextHandler`(`src/cli/handlers/context.ts:218-477`)의 순서:

1. `shouldTrackProject(cwd)` / `getProjectContext(cwd)` — 로컬 판정(`:247-252`).
2. TeamBrain brief를 **먼저 띄워 로컬 읽기와 겹친다**(`:259-263`, 타임아웃 `TEAM_BRIEF_TIMEOUT_MS = 2_000`, `:54`). 겹치기 설계는 좋다.
3. `resolveSessionStartProjects()` — `CLAUDE_MEM_CONTEXT_PROJECTS=all`일 때만 워커 `/api/projects` GET(`:198-202`, 2s).
4. **컨텍스트 캐시 읽기**(`:317-328`). 적중하면 워커 없이 디스크에서 끝난다(좋은 완화책). 캐시는 `CONTEXT_CACHE_MAX_AGE_MS = 24h`(`context-cache.ts:31`), 워커측 워밍은 `CONTEXT_CACHE_RENDER_DEBOUNCE_MS = 2_000`(`ContextCacheService.ts:47`), 변형 최대 64(`:49`).
5. 캐시 미스 → `executeWithWorkerFallback(apiPath)`(`:338`). 이 호출의 예산은 `getWorkerApiRequestTimeoutMs()` = `API_REQUEST 30s`(`hook-constants.ts:10`, Win ×1.5 = 45s)인데, **내부에서 먼저 `ensureWorkerAliveOnce(timeoutMs)`로 워커 기동을 기다린다**(`worker-utils.ts:2318-2340`). 즉 **콜드 스타트 시 H2 하나가 워커 부팅(스폰 대기 15s + readiness 최대 30s)을 흡수**한다 — 훅 상한 60s 안에서.
6. **6. 색상 타임라인을 위해 워커를 한 번 더 부른다** (`:414-425`):

```ts
// context.ts:415-425
if (showTerminalOutput) {
  const colors = input.platform === 'claude-code';
  const colorResult = serverRender ? serverRender.terminal
    : (showLastMessage ? null : readCachedRender(colors))
      ?? await executeWithWorkerFallback<string>(colorApiPath, 'GET', undefined, workerOptions);
```

`showTerminalOutput`은 `CLAUDE_MEM_CONTEXT_SHOW_TERMINAL_OUTPUT === 'true'`이고 **기본값이 `'true'`**(`SettingsDefaultsManager.ts:581`). 즉 **기본 설정에서 캐시 미스 세션 시작은 워커 렌더를 2회** 한다(모델용 1회 + 색상용 1회, 둘 다 `renderContextInjectBody` 전체 경로 — `SearchRoutes.ts:528-606`). 색상 유무만 다른 같은 데이터인데 두 번 렌더한다. **§0-1위 개선안의 근거.**

7. 반환은 `additionalContext`(모델 주입) + `systemMessage`(사람용 배너). 둘 다 `CONTEXT_OUTPUT_LIMIT = 10_000`자(`ContextBudget.ts:9`)에 맞춰 절단된다.

### 1.4 H4 — UserPromptSubmit `session-init` (동기, 15s cap)

```ts
// src/cli/handlers/session-init.ts:56-62
// UserPromptSubmit is synchronous, so the whole session-init round-trip spends ONE budget
const SESSION_INIT_SERVER_TIMEOUT_DIVISOR = 2;
const SESSION_INIT_MIN_REMAINING_TIMEOUT_MS = 500;
```

**하나의 deadline을 체인 전체가 나눠 쓴다** — 이건 모범적이다. 각 단계 직전에 남은 예산을 재고 500ms 미만이면 **그 단계를 건너뛴다**(`:290-298`, `:359-366`). `worker-utils.ts:2347-2359`도 같은 원칙으로 남은 예산이 `MIN_WORKER_BUDGET_MS(100)` 미만이면 요청을 아예 안 보낸다.

기본 경로(워커 런타임)에서의 워커측 작업은 `/api/sessions/init` 1회이고, `CLAUDE_MEM_SEMANTIC_INJECT`가 기본 `'false'`(`SettingsDefaultsManager.ts:610`)이므로 **2차 `/api/context/semantic` 호출은 기본적으로 없다**(`:357`). 시맨틱 주입을 켜면 같은 예산 안에서 직렬 2차 호출이 붙는다(`:367-374`).

`/api/sessions/init`의 직렬 SQLite 왕복은 §0-6위에 정리했다. 부가로 `MAX_USER_PROMPT_BYTES = 256KB`(`SessionRoutes.ts:67`) 절단과 `USER_PROMPT_DEDUPE_WINDOW_MS` 중복 검사(`:950`)가 같은 요청에 들어 있다.

### 1.5 H5 — PreToolUse `file-context` (동기, 15s cap) — 가장 위험한 훅

매처가 `Read|^Bash$|^Grep$|^Glob$|^memory_search$|^memory$|`이다(`hooks.json:115`). 즉 **에이전트가 파일을 읽을 때마다** 동기로 돈다. 실행 순서:

1. `memorySearchHandler.execute(input)`(`file-context.ts:320`) — 대부분 `{}`로 끝나지만 그 전에 `customNativeRoots()`가 **`~/.claude/settings.json`, `<cwd>/.claude/settings.json`, `settings.local.json` 3개를 동기로 읽는다**(`memory-search.ts:44-51`). Bash면 추가로 `shell-quote` 파싱(`:114-121`). 실제 조회가 발생하면 `/api/search`에 `timeoutMs: 3000, workerStartupTimeoutMs: 1000`(`:156`).
2. 파일 stat + `FILE_CONTEXT_MIN_BYTES = 1_500` 미만이면 즉시 반환(`file-context.ts:24,469`).
3. **워커 조회 1회**, 예산 3초:

```ts
// src/cli/handlers/file-context.ts:44
export const FILE_CONTEXT_WORKER_BUDGET_MS = 3_000;
// :508-513
const result = await executeWithWorkerFallback<...>(`/api/observations/by-file?...`, 'GET', undefined,
  { timeoutMs: FILE_CONTEXT_WORKER_BUDGET_MS });
```

4. dedupe 클레임(`:392`, `:411`) → **훅 프로세스가 메인 DB를 직접 연다**(`file-context-dedupe.ts`). 
5. mtime ≥ 최신 관측이면 주입 생략(`:529`).

최악의 경우 `memory-search` 3s + `by-file` 3s = **6s**가 동기로 들어가고, 여기에 프로세스 오버헤드가 더해져 15s 상한에 근접한다. 파일이 32KB 이상(`FILE_READ_GATE_DENY_MIN_BYTES`, `:34`)이면 여기에 `openSync`+청크 라인카운트(`:186-229`)와 `realpath` 워크스페이스 검사(`:237-249`)가 **추가로** 붙는다.

> 설계 의도는 "게이트가 Read를 거부해 토큰을 아낀다"이고 거부 효과는 저장소 자체 eval로 근거가 있다(`:26-33`: 19KB 파일 −6%/−35%, 49KB −35%/−32%). 다만 **지연 비용은 게이트 판정 전에 이미 지불**된다(3s 워커 왕복 + 동기 FS).

### 1.6 H6/H7 — 비동기 경로 (사용자를 막지 않음)

`observation`은 `async: true`(`hooks.json:77-93`)이고, 훅은 디스크 spool에 먼저 쓰고 워커에 nudge만 넣는다(`hook-command.ts:82-85` — "let it land (≤ 250 ms)"). spool은 `HOOK_SPOOL_KINDS`(`hook-spool.ts:33`)로 종류를 나누고 7일 재시도 윈도우(`:200`)를 둔다. **이 구조는 사용자 지연을 워커/LLM 지연에서 완전히 분리한다.** TalkAbout에는 대응물이 없어 §4-T2의 모델이 된다.

### 1.7 워커 부팅·콜드 스타트

```ts
// src/services/worker-service.ts:733-748 (요약)
await startSupervisor();
await this.server.listen(port, host);   // ← 바인드 먼저
clearTimeout(this.bootWatchdog);
...
this.initializeBackground().catch(...); // ← DB/검색 초기화는 그 뒤
```

`listen()`이 먼저이고 DB 초기화(`dbManager.initialize()`, `:848`)·SearchManager·context-cache·spool drain이 백그라운드다. DB가 준비되기 전 요청은 거부된다(`:615`). 따라서 훅은 `/api/readiness`로 대기한다 — `HOOK_READINESS_WAIT 10s`, `POST_SPAWN_WAIT 15s`, `READINESS_WAIT 30s`(`hook-constants.ts:14-16`). 주석의 실측치: "starts in <1s on Linux, **6-8s on macOS with Chroma**"(`:15`).

**콜드 스타트 시나리오(정적 추정, POSIX)**: H3(런처 검사, 보통 수백 ms~수십 초) → H2가 워커 스폰·readiness를 최대 15s+30s 흡수 → 그 뒤 캐시 미스면 렌더 2회. 사용자는 첫 요청에서 수 초~수십 초를 기다린다. 워커가 이미 살아 있으면 H2는 캐시 적중 시 수십 ms, 미스 시 렌더 2회(수십~수백 ms)다.

### 1.8 SQLite 스키마·인덱스 — 대체로 잘 되어 있다

| 항목 | 값/근거 | 평가 |
|---|---|---|
| WAL / synchronous / busy_timeout / journal_size_limit | `connection.ts:124-127` (NORMAL, 5000ms, 4MB) | 적절 |
| `cache_size`, `mmap_size` | **찾지 못했다** | 소폭 개선 여지(§0-8) |
| schema version | `LATEST_SCHEMA_VERSION = 71`(`connection.ts:19`), writer stamp 비교로 다운그레이드 거부(`:44-72`) | 안전 |
| FTS5 | `observations_fts`, `session_summaries_fts` + AFTER INSERT/UPDATE/DELETE **트리거**(`SessionSearch.ts:57-90`, 생성 `:255-291`), LIKE 폴백 `:806` | 좋음 |
| 파일별 조회 | v68에서 `json_each(files_read)` 전테이블 스캔 → **`observation_files` 색인 테이블**(`SessionStore.ts:330-354`), `PRIMARY KEY(path, observation_id)` | 좋음(이미 고쳐짐) |
| project 최신순 | `idx_observations_project_nocase_recent (project COLLATE NOCASE, created_at_epoch DESC)` 등(`:2131-2134`) | 좋음 |
| 중복 프롬프트 | `idx_user_prompts_lookup(session_db_id, prompt_number)`, `idx_user_prompts_content_lookup`(`:713-714`) | 좋음 |
| 컨텍스트 조회 형태 | `(project IN … OR merged_into_project IN …) ORDER BY … LIMIT n` 회피, 프로젝트별 색인 스캔 후 union(`ObservationCompiler.ts:44-57,131-134`) | 좋음 — N+1을 의도적으로 배치 |

**결론: claude-mem의 DB 계층은 성능상 병목이 아니다.** 병목은 (a) 훅 프로세스 오버헤드, (b) 동기 훅의 직렬 왕복 수, (c) 관측 LLM 턴 수다. 이 구분이 중요하다 — TalkAbout이 베낄 것은 인덱스가 아니라 **경계 설계**다.

### 1.9 관측(observation) LLM 파이프라인 — 비용의 본체

```ts
// src/sdk/prompts.ts:152-170
// 16k chars ≈ ~4k tokens ... Two fields per observation → ~8k tokens of variable input.
export const OBS_PROMPT_FIELD_MAX_CHARS = 16_000;
```

```ts
// src/shared/SettingsDefaultsManager.ts:523
CLAUDE_MEM_CLAUDE_OBSERVATION_BATCH_SIZE: '1', // 1 = one event per prompt.
```

```ts
// src/services/worker/ClaudeProvider.ts:1253-1323 (요약)
for await (const message of this.sessionManager.getMessageIterator(session.sessionDbId)) {
  if (message.type === 'observation') {
    if (shouldRecycleConversation(...)) { await recycleObserverConversation(...); return; }
    const fieldMaxChars = observationFieldMaxChars(session.observerContextWindowTokens);
    const optimized = compressField ? await optimizeObservationFields(...) : {...};
    const obsPrompt = buildObservationPrompt({...}, fieldMaxChars, takeObserverSchemaReminder(session));
    session.conversationHistory.push({ role: 'user', content: batchPrompt });
    yield { ... content: withPendingInitPrompt(batchPrompt) };
    if (!(await this.awaitObserverAnswer(session, pacer, answeredBeforeSend))) return;   // ← 직렬
  } else if (message.type === 'summarize') { ... }
}
```

정적 추정 (근거: 위 상수 + 배치=1 + 직렬 대기):

| 시나리오 | 관측 LLM 턴 수 | 입력 토큰(추정) |
|---|---|---|
| 툴콜 1회, Read 출력 6KB | 1 (+필드 16k 초과 시 condense 1) | 1.5k~2.5k(정상) / 최악 8k+ |
| 툴콜 100회 세션 | **100 턴** + 세션당 요약 1~N + generation 재시작 시 init 1 | 150k~250k(정상), 최악 800k |
| 동일, `BATCH_SIZE=8` | 13 턴 | 스캐폴딩 중복 제거로 20~35% 절감(정적 추정) |

- 관측 대화는 `OBSERVER_CONVERSATION_MAX_CHARS = 400_000`(`observer-recycle.ts:31`)에서 recycle된다. recycle 1회 = 새 generation = init 프롬프트 재전송(`ClaudeProvider.ts:1266-1274`).
- 필드 상한은 모델 윈도우에 비례해 줄어든다(`context-window.ts:299-308`: 윈도우의 10%, 최대 16k자). 16k 윈도우면 ~6.5k자.
- **배치=1 + `awaitObserverAnswer` 직렬**이므로 처리량 상한은 "관측 모델 1턴 지연의 역수"다. 관측 턴이 3~10초라면 툴콜이 몰리는 구간(초당 2~3개)에서 백로그가 **분 단위로** 쌓인다. 사용자는 안 막히지만(비동기) 기억이 늦게 남고, 세션 종료 시점에 backlog가 남으면 유실 위험이 커진다(§5).

### 1.10 컨텍스트 주입 토큰량

| 요소 | 상한 | 근거 |
|---|---|---|
| 모델 주입 블록 | **10,000자 ≈ 2.5k 토큰**(4자/토큰 가정) | `ContextBudget.ts:9`, `CHARS_PER_TOKEN = 4`(`context-window.ts:79`) |
| 사람용 배너(systemMessage) | 10,000자(별도 필드) | `context.ts:69-71`, `:90-120` |
| 우선 감축 순서 | full narrative → lastSummary → lastMessage → sessionCount/2 → obsCount/2 | `ContextBudget.ts:56-73` |
| 관측 후보 수 | `CONTEXT_OBSERVATIONS=50`, `SESSION_COUNT=10`, `FULL_COUNT=0` | `SettingsDefaultsManager.ts:481,574-576` |
| 세션 시작 시맨틱 주입 | 기본 OFF. ON이면 상위 5건 narrative | `:610-612`, `SearchRoutes.ts:613,676-680` |

주입은 **상한이 확실히 지켜진다**(초과 시 호스트가 통째로 stub으로 갈아버리는 10,000자 절벽을 알고 맞춘다 — `ContextBudget.ts:3-9`). 비용 관점에서 남는 개선은 "맞추는 비용"(재렌더 6회)이지 "주입량"이 아니다.

### 1.11 트랜스크립트 파싱 — 증분이라 문제 없음

`watcher.ts`는 **증분 테일링**이다: 영속 오프셋/partial/파일 identity/체크포인트 fingerprint를 state 파일에 저장하고(`watcher.ts:24-27`, `:1032-1102`), 패스당 최대 4MB(`MAX_BYTES_PER_PASS = 4*1024*1024`, `:59`), 100줄마다 yield(`:76`), 손상 감지 시 4KB fingerprint로 재개 지점 검증(`:176-193`). "매 이벤트 전체 재파싱"은 **없다**. 이 축은 개선 대상이 아니다.

---

## §2 개선안 (효과순)

### A. `[즉시] S` SessionStart의 2차(색상) 워커 호출 제거 — **최우선**

- **원인**: `context.ts:414-425`가 `colors` 변형을 위해 같은 렌더를 한 번 더 요청한다. `SHOW_TERMINAL_OUTPUT` 기본 `true`(`SettingsDefaultsManager.ts:581`) → 기본 설정에서 항상 발생. 캐시 미스면 `renderContextInjectBody` 전체(작업 상태 조회 + 관측/요약 색인 스캔 + 최대 6회 fit 렌더) 2회.
- **before**: 캐시 미스 세션 시작 = 워커 GET 2회(직렬) + 렌더 2회.
- **after(의사코드)**:
  ```ts
  // 워커: 색상 변형을 같은 렌더에서 파생해 한 응답에 담는다
  // GET /api/context/inject?...&include=model,terminal
  // { model: "<plain>", terminal: "<ansi>" }  ← fit은 model 기준 1회
  ```
  또는 최소 변경: 훅이 `colors=true` 한 번만 호출하고 plain은 ANSI 제거로 파생(`stripAnsi`). 색상 코드 제거는 결정적이므로 렌더 결과가 동일하다.
- **효과(정적 추정)**: 캐시 미스 세션 시작 렌더 비용 50% 감소, 훅 왕복 1회 감소. `[단기]` 캐시 워밍까지 붙이면 대부분의 세션에서 0회.
- **위험**: 낮음. `colors=true` 응답에 placeholders(`CONTEXT_HEADER_TIME_PLACEHOLDER`)가 있어 plain 파생 시 치환 순서만 지키면 된다(`SearchRoutes.ts:503`).
- **측정**: `context_injected` telemetry의 `duration_ms`(`SearchRoutes.ts:462-468`)를 p50/p95로 집계, 훅 계측 로그의 GET 횟수.

### B. `[즉시] S` 관측 배치 크기 상향 (기본 1 → 4~8)

- **원인**: `SettingsDefaultsManager.ts:523` 기본값 1 + `ClaudeProvider.ts:1323`의 직렬 `awaitObserverAnswer`. 결과적으로 툴콜 수 = LLM 턴 수. 저장소 안에 이미 **batch 최대 32**와 Codex 기본 8(`:521`)이 있어 상향이 설계 범위 안이다(`boundedInteger(settings, 1, 32)` — `ClaudeProvider.ts:1373`).
- **before**: 툴콜 100 → 관측 프롬프트 100개, 각각 스키마 리마인더/스캐폴딩 중복.
- **after**: `CLAUDE_MEM_CLAUDE_OBSERVATION_BATCH_SIZE=8` → 13 프롬프트. 스캐폴딩·스키마 리마인더·init 프롬프트 중복 제거로 입력 토큰 20~35% 절감(정적 추정).
- **효과**: 관측 백로그 소진 속도 4~8배, 세션당 비용 감소. `[즉시]`
- **위험**: 관측 지연 증가(툴콜 8개가 모여야 1턴) → "방금 한 일"이 늦게 기억된다. 배치 실패 시 재전송 단위가 커져 재시도 토큰↑. → **기본값을 바꾸지 말고 `install` 프리셋/문서로 권고**하고, 사용자가 관측 지연에 민감하면 2~4를 선택하게 하는 편이 안전.
- **측정**: 세션당 `total_cost_usd`(pass마다 기록 — `ClaudeProvider.ts:391`), `session.conversationHistory` 길이, spool 잔량(`/api/queue`).

### C. `[즉시] S` PreToolUse에서 메인 DB 직접 오픈 제거

- **원인**: `file-context-dedupe.ts`가 훅 프로세스에서 SQLite를 열어 dedupe 클레임을 read/write한다(`:110`, `:154`). 훅은 단명 프로세스라 커넥션 오픈/PRAGMA/마이그레이션 확인 비용을 매번 지불하고, 같은 WAL DB에 워커와 동시 writer가 된다.
- **before**: Read마다 (커넥션 오픈 + `busy_timeout` 설정 + SELECT/INSERT) + WAL writer 경합.
- **after(택1)**:
  1. dedupe 상태를 **훅 로컬 파일**(`DATA_DIR/state/file-context-claims/<sha1(session,path)>.json`, 세션 종료 시 정리)로 이동 — 프로세스 간 공유가 필요 없다(같은 세션의 Read는 순차).
  2. 또는 워커의 `/api/observations/by-file` 응답에 `already_surfaced: bool`을 넣어 **한 왕복 안에서** 판정(현재 이미 왕복 중이다).
- **효과(정적 추정)**: Read당 2~10ms(느린 디스크/AV 환경에서 더 큼), 락 경합 제거. `[즉시]`
- **위험**: 낮음. 2번은 워커가 세션별 인메모리 Set을 이미 갖고 있으므로(`SessionMessageBuffer`의 `seenToolUseIds` 패턴) 구현이 단순하다.
- **측정**: 훅 계측(t_start→t_end) 분포, `file-context gate database unavailable` 경고 빈도(`file-context-dedupe.ts:110`).

### D. `[즉시] S` memory-search 브리지의 조기 반환

- **원인**: `memory-search.ts:146`이 roots를 만들기 위해 `customNativeRoots()`로 settings.json 3개를 동기 read(`:44-51`)한 뒤 `memorySearchLookup()`이 대부분 `null`을 낸다. 판정의 99%는 "메모리 경로가 아니다"인데 그 전에 파일 I/O를 한다.
- **after**: 후보 경로가 메모리 루트 패턴(`/.claude/projects/*/memory/`, `CODEX_HOME/memories/`)에 **문자열로** 걸리지 않으면 settings.json을 읽지 않는다. `args`를 먼저 검사하고, 통과한 경우에만 roots를 만든다.
- **효과(정적 추정)**: 매칭되는 툴콜마다 0.5~2ms 및 3회 open() 감소. `[즉시]`
- **측정**: 동일 계측 로그. 부수적으로 `memory_search` 사용 시에만 roots 생성됨을 단위 테스트로 고정.

### E. `[단기] M` `fitContextToBudget`의 반복 재렌더 제거

- **원인**: `ContextBudget.ts:92-101`이 예산 초과 때마다 `render(observations.slice(0,currentCount), config)`로 **전체 문자열을 다시 만든다**. 감축 단계는 최대 6단계(§`reduceConfig`), 관측 50개 × narrative(장문)면 렌더 1회도 가볍지 않다.
- **before**: `renders = reductions + 1`(최악 6~7회).
- **after**: (i) 1회 렌더 결과에서 **항목 경계 오프셋**을 함께 만들어 두고, 감축은 문자열 절단으로 처리(항목 단위 절단이므로 well-formed 유지); 또는 (ii) 현재 길이에서 목표 길이로의 비율로 후보 수를 **한 번에 추정**한 뒤 2~3회 이내 수렴(이진 탐색).
- **효과(정적 추정)**: 캐시 미스 렌더 비용 5~6배→1.5배. `[단기]`
- **위험**: `render`가 순수 함수라는 전제(`ContextBudget.ts:77-80`)를 유지해야 한다. 절단 방식으로 바꾸면 "항목을 반으로 자르지 않는다"는 보장(`:27-31`)을 깨지 않도록 경계 인덱스만 쓴다.
- **측정**: `Trimmed context to fit the hook output limit` 로그(`ContextBuilder.ts:390-392`)의 `reductions` 분포와 렌더 소요 시간.

### F. `[단기] M` `/api/sessions/init`의 직렬 왕복 축약

- **원인**: `SessionRoutes.ts:875-1025`가 한 요청에서 SQLite를 8회 왕복한다(목록은 §0-6). 각각은 색인 조회라 싸지만, **WAL 환경에서 8회 준비+바인딩+스텝**이고 UserPromptSubmit 예산(10s/7s) 안에서 경쟁한다.
- **after**: (i) 세션/프롬프트/프롬프트번호를 **한 트랜잭션·한 multi-statement**로 처리하고 중간 결과를 메모리로 넘긴다; (ii) `getSessionById` 결과를 `createSDKSession`이 반환하게 해 1회 제거; (iii) 중복 프롬프트 검사는 `saveUserPrompt`의 UNIQUE 위반 처리로 통합.
- **효과(정적 추정)**: 왕복 8→2~3. 워커 구간 지연 30~60% 감소(로컬 SQLite 기준 수 ms~수십 ms). `[단기]`
- **위험**: 중간 실패 시 부분 상태가 남지 않도록 트랜잭션 경계를 정확히 잡아야 한다(현재도 개별 커밋이라 오히려 위험이 있다 — 개선이 안전성도 올린다).
- **측정**: `EXPLAIN QUERY PLAN`은 각 쿼리가 이미 색인인지 확인용, 실제 개선은 route wall-clock(`duration_ms`)과 `sqlite3 .timer on`.

### G. `[장기] L` 훅 프로세스 오버헤드 자체의 축소

- **원인**: 훅 1회 = 셸 → 런처 바이너리 → bun → **4.5MB 번들 로드**. 저장소 자체 측정으로 Windows 4-5초(`hook-constants.ts:117-122`). 이 비용은 어떤 핸들러 최적화로도 줄지 않는다.
- **after(택1)**: (i) 런처 바이너리(이미 `bun build --compile` 산출물, `ensure-launcher.cjs`)가 **번들을 로드하지 않고 HTTP만** 수행하는 "thin hook" 모드 — `context`/`session-init`처럼 "워커에 물어보고 JSON 봉투로 출력"하는 훅은 Node 내장 `fetch`만으로 충분하다; (ii) 훅 번들을 이벤트별로 코드 스플리팅(`context.cjs`, `session-init.cjs`)해 로드 크기를 1/5로.
- **효과(정적 추정)**: Windows 동기 훅 1회 8s→1s 미만. **사용자 체감 지연의 최대 단일 항목**. `[장기]`
- **위험**: 높음 — 세 플랫폼 런처, 프로토콜 버전, PATH 부트스트랩, `hook_io` 규율(§`hook-io.ts`)과 얽혀 있다. 코드 스플리팅은 `scripts/build-hooks.js` 빌드 파이프라인 변경.
- **측정**: 훅 스크립트 첫 줄과 마지막 줄에 `Date.now()`를 찍어 프로세스 수명 분포를 수집(핸들러 시간과 분리), `hookProcessOverheadMs` 가정치와 비교.

### H. `[단기] S` SQLite 연결 PRAGMA 보강

- **원인**: `connection.ts:124-127`에 `cache_size`/`mmap_size`가 없다. 관측 테이블이 커지면 페이지 캐시 미스가 는다.
- **after**: `PRAGMA cache_size = -16384`(16MB), `PRAGMA mmap_size = 268435456`(256MB). 읽기 위주 경로(검색·컨텍스트)에 유리.
- **효과(정적 추정)**: 대형 DB에서 소폭. **근거가 약하므로 실측 후 채택**할 항목. `[단기]`
- **측정**: `sqlite3 <db> "PRAGMA stats"`, 쿼리 wall-clock before/after.

### I. `[제외]` FTS5 bloat·벡터 검색

이미 v54에서 bloat 원인을 막고 v68에서 파일조회를 색인화했으며(`SessionStore.ts:330-354`), FTS 재색인은 1회성 타이머로 미뤄져 있다(`worker-service.ts:886-888`). **추가 개선 여지가 작다** — 손대지 않는 것이 맞다.

---

## §3 측정 방법 (증명 절차)

빌드·실행을 하지 않았으므로 아래는 **측정 설계**다. 위 개선안은 모두 이 절차로 반증 가능해야 한다.

### 3.1 훅 지연 계측 (가장 중요)

1. **프로세스 수명 vs 핸들러 시간 분리**: `hookCommand` 진입(`src/cli/hook-command.ts:89`)과 `exitGraceful` 직전에 `Date.now()`를 찍어 두 값을 로그에 남긴다. `hookProcessOverheadMs()` 가정과 실측의 차이가 곧 G의 크기다.
2. **structured 로그**: `{event, t_proc_ms, t_handler_ms, worker_calls, cache_hit}` — `logger.debug`는 이미 `duration`을 쓰는 곳이 있다(`SessionRoutes.ts:1049-1057`).
3. 집계: `/api/logs`(LogsRoutes) 또는 `worker:logs`(`package.json:80`)로 p50/p95/p99를 낸다.
4. **A 검증**: `worker_calls`가 2→1로 바뀌는지로 직접 확인.

### 3.2 SQLite 비용

```bash
# 계획 확인 (각 쿼리가 색인인지)
sqlite3 "$DATA_DIR/claude-mem.db" "EXPLAIN QUERY PLAN
  SELECT * FROM observations WHERE project IN ('p1','p2') ORDER BY created_at_epoch DESC LIMIT 50;"
sqlite3 "$DATA_DIR/claude-mem.db" "EXPLAIN QUERY PLAN
  SELECT observation_id FROM observation_files WHERE path = 'src/a.ts';"
# 실제 시간
sqlite3 "$DATA_DIR/claude-mem.db" ".timer on"
# 인덱스 사용 통계(빌드에 따라)
sqlite3 "$DATA_DIR/claude-mem.db" "PRAGMA stats;"
```

`/api/observations/by-file`(§1.5-3)과 `/api/context/inject`(§1.3)의 쿼리 2개를 **최소한** 이렇게 고정한다.

### 3.3 토큰·LLM 턴 수

- **턴 수**: `ClaudeProvider.ts:1253`의 for-await 루프에서 yield마다 카운터를 올려 세션 종료 시 로그. `batch=1`이면 턴 수 ≈ 툴콜 수가 되어야 하고, B 적용 후 1/8이 되어야 한다.
- **토큰/비용**: 패스별 `total_cost_usd`(pass 시작 주석 `ClaudeProvider.ts:391`)를 세션 단위로 합산. `getUsage()`/observer-usage(`src/services/worker/observer-usage.ts`)가 있으면 그것을 쓴다.
- **주입량**: `context_injected` telemetry에 실린 `stats`(`SearchRoutes.ts:459-469`)와 응답 `text.length`를 함께 기록 — 10,000자 상한 대비 실사용률.

### 3.4 캐시 적중률

- `context.ts:322`의 `SessionStart context served from the context cache` 로그 비율(현재는 `logger.debug`).
- 워커측: `ContextCacheService`의 `recordLiveRender`/`warmVariant` 호출 수와 `CONTEXT_CACHE_RENDER_DEBOUNCE_MS=2000`으로 인한 취소 수.
- 목표: 세션 시작의 캐시 적중률 ≥80%. A·E는 미스일 때의 비용을, 캐시 워밍은 미스 빈도를 줄인다.

### 3.5 회귀 방지

- 저장소에 테스트 레인 스크립트가 있다(`package.json:110-119`, `scripts/test-lanes.ts`). 성능 회귀는 `tests/`의 sqlite/search 레인에 **쿼리 수·왕복 수**를 세는 단위 테스트로 넣는 것이 현실적이다(F 예: `init` 경로의 `db.run` 호출 수 스파이).
- 참고: `scripts/check-hook-io-discipline.cjs`(`package.json:105`)가 이미 훅 I/O 규율을 lint한다 — 훅 성능 규율 검사기(예: "동기 훅에서 워커 호출 2회 이상 금지")를 같은 자리에 추가하는 것이 자연스럽다.

---

## §4 TalkAbout 적용안

TalkAbout 실물 확인 결과: `talkabout/worker.py`(SQLite journal), `talkabout/store.py`(PostgreSQL), `talkabout/collaboration.py`(프롬프트 조립), `talkabout/core.py`(이벤트/스냅샷), `talkabout/api.py`(SSE), `talkabout/agent_runtime.py`(프롬프트/어댑터). **주의: Tools/·`.ci-local/`에 성능 회귀 하네스는 찾지 못했다**(`tools/`에는 `ci_guard.py`, `docs_check.py`, `run_safe_regression.py`, `run_trial_diagnostic.py`뿐). 따라서 §4의 before 값은 코드 구조에서 유도한 정적 추정이다.

### T1. `[즉시] S` SSE 폴링 루프의 "전체 스냅샷 2회 + 전체 이벤트 로드" 제거 — 가장 큰 승부처

**현재 코드.** SSE 루프가 250ms마다 이터레이션하면서 **스냅샷을 두 번** 뜨고, 각 스냅샷이 방의 이벤트 전체를 읽는다:

```python
# talkabout/core.py:694-736 (발췌)
while True:
    actor, rooms, _ = self.store.snapshot(token)            # ← 1회차
    ...
    for rid in room_ids:
        history = rooms[rid]["events"]
        selected = [e for e in history if e["seq"] > offsets[rid]][:16]   # 전체 선형 스캔
    latest_actor, latest_rooms, _ = self.store.snapshot(token)           # ← 2회차
    ...
    wait(min(0.25, max(0, end - time.monotonic())))
```

```python
# talkabout/store.py:356-362
def _room(conn, row):
    room = copy.deepcopy(row["payload"])
    room["events"] = [r["payload"] for r in conn.execute(
        "SELECT payload FROM events WHERE room_id=%s ORDER BY seq LIMIT 2049", (row["id"],)).fetchall()]
```

즉 **SSE 클라이언트 1개당 초당 4회 × (rooms SELECT 1 + events SELECT 2049행) × 2회**, 그리고 매 이터레이션 `save_cursor`(INSERT)까지. `snapshot()`은 `REPEATABLE READ` 트랜잭션을 열고 `principals`까지 읽는다(`store.py:404-413`). 방 하나에 이벤트가 1,000건이면 JSON 역직렬화 2,000건/초/클라이언트다.

**claude-mem에서 가져올 것**: 사전 렌더 캐시 + 변경 무효화. claude-mem은 SessionStart 컨텍스트를 디스크에 **키별로 렌더해 두고**(`context-cache.ts:173-193`, 24h TTL), 쓰기가 오면 `ContextCacheService`가 2초 debounce로 재렌더한다(`:47`). 훅은 캐시 적중 시 **DB를 아예 안 읽는다**(`context.ts:317-328`).

**after(설계)**:

```python
# core.events 안에서
snap = self.store.snapshot_if_changed(token, since=self._last_seen_seq.get(token))  # 1회, 그리고 변경 없으면 즉시 대기
# events 조회는 seq > offset 인덱스 범위만
rows = conn.execute("SELECT payload FROM events WHERE room_id=%s AND seq > %s ORDER BY seq LIMIT 16",
                    (rid, offset))         # ← LIMIT 2049 전체 로드 대신 델타
```

- **델타 조회는 이미 색인 가능하다**: `events`는 `PRIMARY KEY(room_id, seq)`(`talkabout/schema.sql:18-22`)이므로 `WHERE room_id=? AND seq > ?`는 범위 스캔이다. 지금 비싼 것은 색인이 없어서가 아니라 **매번 `LIMIT 2049`로 전부 읽는다**는 점이다(`store.py:360`). 즉 T1은 스키마 변경 없이 코드만으로 가능하다.
- 두 번째 `snapshot()`은 `NOTIFY talkabout_changed`(`store.py:373`)를 이미 받고 있으므로 **제거 가능**하다. 루프는 `listener().wait(timeout)`으로 변경을 기다리면 된다(`store.py:443-449`).
- `public_room` 렌더는 `(room_id, member.revision, last_seq)` 키로 캐시(방 1개·멤버 1명 기준 작은 dict).

**before/after(정적 추정)**: 이벤트 1,000건 방, 클라이언트 1개 → 초당 스냅샷 8회 + 이벤트 JSON 16,000건 역직렬화 → **초당 0~1회 델타 조회**. 유휴 상태에서 DB 왕복이 사실상 0이 된다. **측정**: `pg_stat_statements`로 `SELECT payload FROM events` 호출 수, `EXPLAIN (ANALYZE, BUFFERS)`로 shared hit/blks, 그리고 60초 유휴 SSE 연결 1개에 대한 초당 쿼리 수.

### T2. `[단기] M` worker.py Journal의 fsync를 상태전이 단위에서 "의미 단위"로

**현재 코드.**

```python
# talkabout/worker.py:156-157
self.db.execute("PRAGMA journal_mode=WAL")
self.db.execute("PRAGMA synchronous=FULL")
# :194-196 — 상태전이마다 개별 커밋
def save(self, task_id, state, payload):
    with self.db:
        self.db.execute("UPDATE queue SET state=?,payload=? WHERE id=?", ...)
```

`step()` 한 번에 `save()`가 최대 8~10회 호출된다(§`worker.py:404-689`: failed/claimed/claim_pending/ready/pre_call_retry/call_started/result_saved/...). `synchronous=FULL` + WAL이면 **커밋마다 fsync**다. 태스크 1건당 fsync 8~10회는 회전 디스크에서 수십 ms, NVMe에서도 1~3ms/회다.

**claude-mem 대비**: claude-mem은 `synchronous = NORMAL`(`connection.ts:126`)이고, **고빈도 이벤트는 인메모리 버퍼**로 처리한다(`SessionMessageBuffer.ts:30-50` — "이 replaces the durable pending_messages SQLite queue"). 듀러빌리티는 **트랜스크립트 JSONL 리플레이**가 담당한다(`:39-44`).

**after(TalkAbout은 journal이 진실 원천이므로 그대로 베끼면 안 된다)**:
1. `call_started` 이전의 **준비성 상태**(`claimed`→`claim_pending`→`ready`)는 하나의 트랜잭션으로 묶어 1 fsync로 커밋한다. 크래시 시 재생해도 안전한 구간이기 때문이다(현재 코드도 이 구간을 재시도 가능 상태로 다룬다: `worker.py:181-186`의 `safe_pre_call_retry`).
2. 결과 확정(`save_result`, `:219-225`)만 `synchronous=FULL` 단일 커밋으로 남긴다. SQLite는 PRAGMA를 커넥션 단위로 바꿀 수 있으므로 **별도 커넥션**(`journal_full.db`)으로 결과 저장만 FULL, 나머지는 NORMAL.
3. **하지 말 것**: claude-mem처럼 큐를 인메모리로 옮기는 것. TalkAbout의 journal은 크래시 복구 계약(§`docs/ARCHITECTURE.md`)의 일부다. `[제외]` 이유: 계약 위반.

**효과(정적 추정)**: 태스크당 fsync 8~10→2~3. **측정**: `journal.db`의 `PRAGMA wal_checkpoint`, 태스크당 `step()` wall-clock을 계측(현재 `report('idle')` 지점 `cli.py:441`을 스팬으로), 그리고 `iostat`/`fsync` 카운트.

### T3. `[즉시] S` `fit_context`의 재직렬화 루프 (O(k²) → O(k log n))

**현재 코드.**

```python
# talkabout/collaboration.py:199-214
while True:
    if _serialized_size(context, work_workspace=work_workspace) <= MAX_INPUT:   # ← 매 루프 전체 json.dumps
        return context
    ...
    item['text'], limits = excerpt(item['text'], max(floor, length // 2))        # 절반씩만 줄인다
```

`_serialized_size`는 `prompt_text(context)` = 헤더 + `json.dumps(context, ensure_ascii=False)`(`agent_runtime.py:232`)이고, `MAX_INPUT = 200000`(`:38`), `MAX_SHARED_TEXT_BYTES = 72000`(`collaboration.py:12`). 가장 큰 항목을 **매번 절반**으로 줄이므로 감축 단계가 `O(log)`이지만, **단계마다 전체 컨텍스트를 다시 직렬화**한다 → 총 비용 `O(k² log n)`(k=컨텍스트 크기).

claude-mem의 `fitContextToBudget`이 **정확히 같은 병이다**(`ContextBudget.ts:81-104`). 두 구현을 비교해 얻는 결론은 "같은 실수를 반복하지 말고 **직렬화 1회 + 오프셋 기반 절단**으로 가자"이다.

**after**: 1회 직렬화 후 `json.dumps` 결과의 **항목별 바이트 오프셋**을 기록하고, 목표 초과분만큼 큰 항목부터 잘라낸 뒤 오프셋을 갱신(전체 재직렬화 없이 총 길이 계산 가능 — JSON 구조는 그대로이고 문자열 내용만 줄기 때문). 수렴은 2~3회 이내.

**효과(정적 추정)**: 프롬프트 조립 CPU 3~10배 절감(컨텍스트 72KB+메타 기준). 턴 스케줄러가 붐빌 때(`TurnScheduler` round-robin) 체감 지연에 직접 반영. **측정**: `pytest` 벤치 추가 — `shared_context`+`fit_context`를 200KB 컨텍스트로 1,000회 호출 시간, `cProfile`로 `json.dumps` 비중.

### T4. `[차용] M` 단일 deadline 전파 — claude-mem의 훅 예산 패턴

claude-mem은 **체인 전체가 예산 하나**를 나눠 쓰고, 남은 예산이 임계 미만이면 **다음 단계를 아예 건너뛴다**(`session-init.ts:56-62,290-298`, `worker-utils.ts:2347-2359`). TalkAbout은 상한이 **개별 상수**다(`MAX_INPUT=200000`, SSE `duration_seconds<=60`(`api.py:122`), `wait_seconds<=10`(`core.py:691`), `runtime_timeout=30`(`worker.py:281`)) — 조합하면 상한이 곱해질 수 있다. 예: SSE 루프 1회 이터레이션이 `snapshot 2회 + save_cursor`를 하고 `wait`는 최대 10초(`api.py:131`의 `min(10, …)`)까지.

**적용**: `core.events`/`shared_context`/`fit_context`에 `deadline: float`을 인자로 넘기고, 각 단계 진입 시 `remaining = deadline - monotonic()`을 검사해 부족하면 **부분 결과를 반환하고 manifest에 `'budget_truncated'`를 남긴다**(현재 manifest의 `omissions`가 이미 이 자리를 갖고 있다 — `collaboration.py:156-160`). TalkAbout의 장점(무엇이 잘렸는지 기록)을 유지하면서 시간 축을 추가하는 것이므로 `[차용]`.

**측정**: deadline 초과율(로그), p95 응답시간.

### T5. `[차용] M` 관측→주입 계층을 도입한다면 claude-mem의 색인 설계를 이식

TalkAbout에는 "기억" 계층이 없다(이벤트가 진실 원천, 프롬프트는 매번 조립). 에이전트용 기억을 넣는다면 claude-mem에서 **성능상 이미 검증된 세 가지만** 가져온다:

| claude-mem 자산 | 근거 | TalkAbout 대응 |
|---|---|---|
| 파일→관측 역색인 테이블 | `SessionStore.ts:330-354` (v68, "json_each 스캔이 훅 예산을 넘겨 동시 조회가 큐에 쌓였다") | PostgreSQL `event_refs(event_id, ref_kind, ref_key)` + B-tree. jsonb 배열 GIN보다 조회가 예측 가능 |
| 10,000자 **측정 기반** 예산 + 우선순위 감축 | `ContextBudget.ts:56-73` | TalkAbout `MAX_SHARED_TEXT_BYTES=72000`(`collaboration.py:12`)는 **고정 상한**. 감축 순서는 이미 결정적(`pinned → newest → adopted → recent`, `:104-125`)이므로 **[대조] TalkAbout이 더 낫다** — 추가로 필요한 것은 "측정 후 감축"뿐 |
| 주입 상한 절벽 인지 | `ContextBudget.ts:3-9` (1자 초과 시 호스트가 전체를 stub으로 교체) | TalkAbout은 자체 서버라 절벽이 없음(**`[대조]` 우위**) |

### T6. `[즉시] S` 워커 유휴 폴링

```python
# talkabout/worker.py:340-343
response = self.client.request("GET", "agents/me/events",
                               params={"cursor": cursor, "wait_seconds": 0} if cursor else {"wait_seconds": 0})
```

```python
# talkabout/cli.py:463-466 — idle이면 0.25초 뒤 다시 step()
if result in {"waiting", "retry_wait"}:
    until = min(deadline, time.monotonic() + 0.25)
    while time.monotonic() < until and not journal.stop_requested():
        time.sleep(min(0.02, max(0, until - time.monotonic())))
```

**서버는 long-poll을 지원하는데**(`wait_seconds` 0~10, `core.py:691`) 워커가 **항상 0으로 호출**한다. 결과: 유휴 워커가 **초당 4회** `events`를 호출하고, T1의 비용(스냅샷 2회)을 매번 지불한다.

**after**: `wait_seconds=turn_wait`(예 5~10초)를 쓰고, 0.25s 폴링을 제거. claude-mem의 이벤트 구동 drain(`SessionMessageBuffer.ts:225-257`: 버퍼가 비면 **이벤트 emitter를 기다린다**, 폴링 없음)이 같은 원칙의 선례다.

**효과(정적 추정)**: 유휴 워커 1개당 서버 요청 4 req/s → 0.2 req/s (20배↓), T1과 곱해지면 서버 DB 부하가 사실상 사라진다. **측정**: 워커 1개 유휴 60초 동안의 서버 요청 카운트(프록시 로그 또는 `pg_stat_statements`), 워커 CPU%.

### T7. `[즉시] S` 성능 기준선 하네스 부재 — 먼저 만든다

`tools/`에 벤치가 없으므로(§4 서두), **측정 없이 위 개선을 주장할 수 없다.** 최소 하네스:

```
tools/perf_snapshot.py
  - 시나리오: (a) SSE 유휴 60s 클라이언트 1 (b) 이벤트 1,000건 방 스냅샷 (c) shared_context+fit_context 200KB
  - 수집: wall-clock p50/p95, 쿼리 수(pg_stat_statements 델타), 직렬화 호출 수(cProfile)
  - 출력: tools/perf_baseline.json (커밋) → 회귀 시 비교
```

`run_safe_regression.py`가 이미 CI 게이트로 있으므로(`tools/run_safe_regression.py`), perf_snapshot을 그 옆에 두고 **"쿼리 수가 늘면 실패"**하는 결정적 게이트로 만드는 편이 wall-clock 게이트보다 안정적이다.

---

## §5 미확인 / 한계

**확인하지 못한 것 (근거로 쓰지 않았다)**

1. **실측 지연 없음.** §1·§2의 모든 ms/초 수치는 저장소 상수와 주석에서 유도한 **정적 추정**이다. `hookProcessOverheadMs`의 4-5초조차 저장소 주석의 자기 측정치를 인용한 것이며, 이 환경에서 재현하지 않았다.
2. **워커 부팅 실측 미확인.** "Linux <1s, macOS 6-8s(Chroma)"(`hook-constants.ts:15` 주석)는 인용이며, Windows 수치는 명시되어 있지 않다.
3. **Chroma/벡터 검색 경로의 실제 지연 미확인.** `ChromaMcpManager`는 lazy 초기화(`worker-service.ts:842`)이고 벡터 전략(`search/strategies/ChromaStrategy.ts`)은 코드를 따라가지 않았다. 벡터 경로를 쓰는 설치에서는 §1.5의 3초 예산이 더 자주 소진될 수 있다.
4. **`/api/observations/by-file`의 최종 SQL 미확인.** `observation_files` 색인 테이블의 존재와 인덱스(`SessionStore.ts:344`)는 확인했으나, 라우트→store 함수 연결부는 따라가지 않았다(§1.8의 "색인 seek"은 v68 주석과 테이블 정의에 근거한 추정).
5. **`start`(H1)의 실제 작업 내용 미확인.** `async: true`라는 사실과 `worker-service.ts:1668`의 `case 'start'`만 확인했고, 그 서브커맨드가 워커를 스폰하는지 재사용하는지는 코드를 끝까지 읽지 않았다.
6. **관측 모델의 실제 턴 지연(p50/p95) 미확인.** §1.9의 "3~10초"는 가정이며 백로그 추정의 입력값이다. 실측하면 B의 우선순위가 달라질 수 있다.
7. **spool 잔량 폭주 한계 미확인.** spool은 7일 재시도 윈도우(`hook-spool.ts:200`)를 갖지만 디스크 상한·정리 정책은 확인하지 않았다. 배치=1 백로그가 길게 유지되면 여기가 먼저 터질 수 있다.

**구조적 한계**

8. **빌드 산출물 위주 추적의 한계.** `plugin/scripts/*.cjs`는 esbuild 인라인 번들이라 "어떤 소스 모듈이 실제로 로드되는지"를 번들에서 역추적하지 않았다. §1.1의 프로세스 체인은 `hooks.json`의 `command: "claude-mem"` + `package.json` 스크립트 + 런처 주석에서 재구성한 것이다.
9. **플랫폼 의존.** Windows 배수(`WINDOWS_MULTIPLIER=1.5`, `hook-constants.ts:19`)와 `hookProcessOverheadMs`의 8초는 Windows 전용이며, POSIX에서는 결론이 다르다(오버헤드 5초 가정, 예산 10초).
10. **TalkAbout 비교의 비대칭.** §4의 claude-mem 쪽 값은 저장소 상수 기반이고, TalkAbout 쪽 값은 코드 구조 기반이다. **두 쪽 모두 실측이 없으므로 "어느 쪽이 몇 배 낫다"는 주장은 하지 않았다** — 지연 원인의 **종류**가 다르다는 것까지만 주장한다.
11. **`claude-mem`은 이미 상당히 최적화된 코드베이스다.** §0.2의 상위 4건은 모두 "기본값/중복 호출" 수준이고, 알고리즘적 결함은 §2-E(`fitContextToBudget`) 정도다. 코멘트에 근거·측정·트레이드오프가 기록된 성숙한 저장소이며, **개선 여지가 작다는 것 자체가 이 분석의 결론 중 하나**다.
