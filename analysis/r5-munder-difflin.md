# munder-difflin 전수 분석 — 오피스 오브 클론 하네스

분석 대상: `.analysis-repos/munder-difflin` (github.com/HarnessMD/munder-difflin)
커밋: `8702b73eb626f153258a3aa527e89ae5d0ca5218` (2026-11-02) · MIT (코드), 픽셀 아트 자산은 별도 라이선스(`LICENSE-ASSETS`)
분석 방식: **정적 분석만**. 빌드/설치/실행/테스트 실행/서버/모델 호출 없음. 자산 디렉터리(`docs/`, `blog/`, `landing-remotion/`, `seo/`, `evidence/`, `build/`, `resources/`, `prototypes/`, `.git/`)는 읽지 않았다. 코드 인용은 리포 루트 기준 `path:line`이며 추측은 "미확인"으로 표시했다. 파일 총 줄 수는 읽은 파일의 실제 끝줄 기준(예: `src/main/index.ts` 5511줄, `src/main/hive.ts` 3669줄)이다.

---

## §0 요약 — 핵심 발견

| 주제 | 지점 | 내용 | 신뢰도 |
|---|---|---|---|
| 제품 형태 | `README.md:116-131`, `package.json:13-28` | Electron 데스크톱 앱. CLI 에이전트(Claude Code/Codex/Gemini/Grok/Kimi/Qwen/OpenCode/Crush/pi/Copilot/Cursor)를 `node-pty`로 감싸고, Pixi.js 오피스 바닥에 아바타로 시각화한다. | 높음(코드+문서) |
| 오케스트레이션 | `src/main/hive.ts:1530-1613`, `src/main/hive.ts:1587` | "god"(Michael)은 코드가 아니라 **프롬프트로 지시되는 LLM 오케스트레이터**다. `fleet.json`/`registry.json`/`tasks.json`/`board.md`를 읽고 `inbox`/`outbox` 파일로 지시한다. 결정적 스케줄러는 없다. | 높음 |
| 작업 모델 | `src/shared/taskLedger.ts:1-87`, `src/main/hive.ts:1906-1961` | `hive/tasks.json`은 **평평한 카드 배열**(todo/doing/blocked/done). `dependsOn`/`deps` 같은 필드가 일부 문서에 보이지만 스케줄러가 의존성을 해석하는 코드는 확인하지 못함(§7). 의존성·병렬성은 god의 프롬프트 판단. | 중간 |
| 동시 실행 상한 | `src/main/config.ts:439-442`, `src/main/index.ts:4886-4889` | `maxConcurrentWorkers` 기본 **4**, 워커 idle 타임아웃 20분, 워커당 토큰 캡 기본 **0(무제한)**. 상한은 "임시 워커(god이 `spawn-requests/`에 파일을 떨어뜨려 띄우는 격리 워커)"에만 적용된다. | 높음 |
| PTY 수명주기 | `src/main/pty.ts:40-74`, `src/main/index.ts:443-503` | 세션별 8KB tail ring, owner webContents 단위 라우팅, teardown에서 broker 권한 회수·아카이브·breaker/watchdog 상태 정리·worktree safety gate. | 높음 |
| 크래시 복구 | `src/main/hive.ts:376-386`, `src/main/index.ts:2627-3033` | 크래시 로그 최대 50개 프루닝, `registry.json`의 `sessionId`로 `--resume`, `--settings` 주입, 재시작 시 워크트리 복원. | 높음(문서+코드 주석) |
| 메시징/루프 가드 | `src/main/hive.ts:205`, `src/main/hive.ts:1619-1668`, `src/main/hive.ts:1832-1893` | 파일 inbox/outbox + 1.5s 폴링 라우터. `hops`는 하네스 소유 카운터, `HOP_CAP=12`; hookless provider로 간 메일은 god에게 bounce, 캡 초과 시 drop. | 높음 |
| 비용/폭주 가드 | `src/main/breaker.ts:1-24,42-72`, `src/main/usage.ts:104-139` | 순수 정책 모듈 `CircuitBreaker`(steer→constrain→stop, `hardStop` 기본 off), 누적 usage 샘플 diff로 velocity, 중복 샘플 게이트. | 높음 |
| 기억 | `src/main/memory.ts:1-13,434-446`, `src/main/kg-core.cjs:1-31` | 1차는 `agents/<id>/memory.md` 마크다운. 의미 검색은 **외부 `mempalace` CLI**(선택). 지식그래프(`kg-core.cjs`)는 파일 기반 JSONL 청크 인덱스+키워드 스코어. | 높음 |
| 그래프 UI 문서/코드 불일치 | `MEMORY_GRAPH_SPEC.md:3-5` vs `src/renderer/src/components/MemoryGraphPanel.tsx:1-22` | 스펙은 "No component code is written yet"라 하지만 구현이 이미 있음(SVG, 5s 폴링, 새 IPC 없음). | 높음 |
| 권한/샌드박스 | `src/main/config.ts:427`, `src/main/hive.ts:1240-1245,998-1005`, `src/main/hooks.ts:205` | `autoMode` **기본 true**(= 바이패스 권한 기본), 샌드박스 쓰기 디렉터리 목록, hook 소켓 단선 시 **fail-open**(모든 hook 허용). | 높음 |
| 텔레메트리 | `src/main/analytics.ts:47-64,191-245`, `TELEMETRY.md` | PostHog 익명 이벤트, 이벤트·속성 allowlist, `DO_NOT_TRACK` 존중, 포크 빌드는 no-op. | 높음 |
| 테스트 | `test/` 124개 `*.test.cjs`, `test/load-ts.cjs:1-69` | `node --test`, TS를 `typescript.transpileModule`로 즉석 로드. 회귀(hop cap, breaker, restart preserve 등) 중심. Electron 실행 테스트는 소수. | 높음 |
| 라인 규모 |  | `src/main/index.ts` 5511, `hive.ts` 3669, `preload/index.ts` 1348, `CommandCenterPanel.tsx` 1310, `OfficeFloor.tsx` 1713, `useHive.ts` 1201. | 높음 |

**TalkAbout 관점 한 줄**: Munder의 강점은 "PTY를 낀 실제 CLI 에이전트의 **생존·루프·비용·종료 프로토콜**"이고, 약점은 "결정적 스케줄링(의존성 DAG·공정성·재현성)"이다. TalkAbout의 plan dispatch(DAG 실행)는 Munder보다 앞서 있으므로, **가드레일·수명주기·프로토콜만 선택 차용**하고 오케스트레이션 코어는 가져오지 않는 것이 맞다.

### 핵심 발견 Top 5
1. **오케스트레이터는 LLM 프롬프트다** — god(Michael)의 행동 규약이 시스템 프롬프트(수천 자)에 박혀 있고, 실제 조정은 `tasks.json`/`inbox` 파일을 통해 일어난다(`hive.ts:1530-1613, 3006`). 스케줄러는 "미션 타이머"뿐이며, 작업 의존성·재시도·공정성은 코드가 아닌 프롬프트 판단이다.
2. **메시지는 파일, 커밋은 메시지당 1회** — inbox/outbox + 1.5s 라우터(`hive.ts:1647-1893`), 메시지 전송마다 `hive: msg …` 커밋(`hive.ts:1671-1676`). 처리량보다 감사 가능성(audit)을 택한 설계.
3. **PTY 생존성이 제품의 핵심 자산** — 8KB tail ring, crash log 50개, sessionId `--resume`, 워크트리 safety-gate 종료, "closing time" ACK 종료(`pty.ts`, `index.ts:443-503`, `closingTime.ts`). CLI가 죽는 상황을 제품 표면으로 끌어올렸다.
4. **비용 가드는 토큰 velocity 기반** — `--max-turns`와 별개로 자체 breaker(steer→constrain→stop)를 두고 `hardStop`은 기본 off(`breaker.ts:20-24`). "죽이지 않고 조인다"가 기본값.
5. **구독 시간당 한도 추적기는 없다** — README의 "hourly limits"는 CLI에 위임. 코드에서 시간창/쿼터 추적을 찾지 못했다(§4.4). 유료/Pro 기능 게이팅 코드도 찾지 못했다(§7).

---

## §1 아키텍처 실측

### 1.1 프로세스/레이어
- Electron 3분할이 그대로다: `src/main`(Node), `src/preload`(1348줄 contextBridge), `src/renderer`(React 18 + Pixi 8 + xterm 5 + CodeMirror/Monaco). 빌드는 electron-vite(`package.json:14-16`, `electron.vite.config.ts`).
- 보안 경계: `nodeIntegration:false`, `contextIsolation:true`(`SECURITY.md:33-35`), 렌더러 샌드박스 회귀 테스트(`test/renderer-sandbox.test.cjs`).
- 멀티 윈도우: 각 PTY가 스폰한 `WebContents`(owner)에만 `pty:data:<id>`/`pty:exit:<id>`를 보낸다(`src/main/pty.ts:56-61`). 설정 `multiWindow` 기본 true(`src/main/config.ts:450`).
- 아이러니: `src/main/index.ts` 하나가 5511줄로 사실상 "앱 전체"다. IPC 핸들러·미션 스케줄러·Slack/웹훅·워커 컨트롤러·heartbeat·breaker beat가 한 파일에 있다.

### 1.2 상태 저장 — 3원화
1. **SQLite(better-sqlite3)**: 극히 제한적. `kv`(창 위치)와 `command_history`(프롬프트 기록)만(`src/main/db.ts:1-16,49-68`). 에이전트 로스터/메시지 큐는 아직 localStorage이고, `agents`/`message_queue` 미러와 `cost_ledger`는 "미래 마이그레이션"으로 명시적으로 비워 두었다(`src/main/db.ts:36-47`).
2. **파일+git**: 하이브 루트(`<harnessHome>/hive/`) 아래 `agents/<id>/{identity.md,memory.md,cursor.json,inbox/,outbox/}`, `registry.json`, `fleet.json`, `tasks.json`, `board.md`, `cost-ledger.jsonl`, `spawn-requests/`. **git은 Electron main만 커밋**한다(단일 커미터, `.git/index.lock` 회피 — `HIVE.md:36-44`, `src/main/hive.ts:2893-2910`). 메시지 1건마다 `hive: msg …` 커밋이 생긴다(`src/main/hive.ts:1671-1676`).
3. **JSON 설정**: `config.ts`의 `HarnessConfig`(미션, autoMode, 워커 상한, 토큰 캡 등).

### 1.3 PTY 세션 수명주기 (핵심)
- `PtyManager`(이름은 파일 내 `class` 미확인이지만 `pty.ts`가 세션 맵을 소유)는 `node-pty` 스폰/리사이즈/킬, `lastOutputAt`(플로어 조용함·idle handshake), 8KB tail ring(`TAIL_MAX`)을 유지한다(`src/main/pty.ts:37-74`). tail은 "시작 직후 크래시한 provider의 마지막 설명"을 남기기 위한 것이라고 주석에 명시.
- 스폰 단일 관문: `spawnAgentCore`(`src/main/index.ts:2627-3033`). `~` 확장(2636), provider 추론(2642), CLI 미설치 시 설치 명령을 **같은 PTY에 가시적으로 실행** 후 설치 종료 시 같은 PTY로 재스폰(2652-2665), `--resume`(registry의 `sessionId`, `hive.ts:772-777`), 워크트리 격리 경로 반환(3027-3033).
- 종료 단일 관문: `teardownPty`(`src/main/index.ts:443-503`) — broker capability 회수(452), 에이전트 아카이브(469-471), watchdog/breaker/telemetry/grok 게이트 forget(458-465), 프록시 사이드카 종료(468), 워크트리는 **미통합 작업이 있으면 자동 삭제 금지**(479-491). 정상 종료 시 앱 quit는 `killAll()`에서 Windows만 프로세스 트리 선제 kill 후 `proc.kill()`(PTY HUP로 포그라운드 그룹 정리, `src/main/pty.ts:820-843`).
- "종료가 곧 복구 지점": 아카이브는 registry에서 `archived` 플래그일 뿐 삭제가 아니고, 크래시 로그는 최대 50개로 프루닝된다(`src/main/hive.ts:376-386`). `recordAgentExit`가 종료 사유/테일을 남긴다(`src/main/hive.ts:2696`).

### 1.4 preload IPC 계약
- `src/preload/index.ts` 1348줄이 유일한 렌더러↔main 통로다. 마지막 줄에서 `contextBridge.exposeInMainWorld('cth', api)`로 단일 객체를 노출한다(`src/preload/index.ts:1429`). 렌더러는 `window.cth.*`만 본다.
- 채널 명명은 `domain:action` 규칙: `pty:spawn/write/resize/redraw/kill/list`(`preload/index.ts:583-600`), `fs:*`(`:677-706`), `git:*`(`:709-746`), `hive:*`(`:751-864`), `control:*`(`:1038-1070`), `missions:*`, `slack:*`, `webhook:*`, `triggers:*`, `integrations:*`, `realtime:*`, `update:*`, `kg:*`, `memory:*`, `skills:*`, `workers:*`, `history:*`.
- 이벤트는 `ipcRenderer.on` 래퍼가 unsubscribe 함수를 반환하는 형태(`preload/index.ts:608-623, 870-1030`). `hive:hookEvent`, `hive:contextUpdate`, `hive:message`, `hive:agentSpawned/Archived`, `control:breakerState`, `app:closingTime`, `update:status` 등.
- 보안 자세: `nodeIntegration:false` + `contextIsolation:true`(`SECURITY.md:33-35`)로 렌더러에 Node를 주지 않지만, **main의 파일·프로세스·git 능력 대부분이 그대로 노출**된다. 핸들러는 각자 수동 인자 검증을 한다(예: `pty:write`의 `typeof` 검사, `src/main/index.ts:3035-3037`). 런타임 스키마 검증 레이어는 확인하지 못했다(§7).
- 규모 감각: IPC 표면이 180여 메서드에 달해, "앱 하나 = main/index.ts 5.5k줄 + preload 1.3k줄"이라는 결합도가 이 제품의 최대 유지보수 부담이다.

---

## §2 오케스트레이션·스케줄링 (가장 중요)

### 2.1 god은 프롬프트다 — 코드가 아니다
- god의 시스템 프롬프트에는 "`fleet.json`으로 에이전트별 토큰/비용/상태/breaker/inbox backlog를 모니터링하고, `tasks.json`의 각 task에 **dispatch 즉시 assignee를 지정하고 상태 변경 시에도 절대 지우지 말 것**, HUMAN FEEDBACK은 `humanQA` 배열에 짧게 적고 카드 상태를 `blocked`로 둘 것" 같은 운영 규약이 길게 박혀 있다(`src/main/hive.ts:1587`).
- 라이브 로스터 한 줄은 `HookServer`가 SessionStart/UserPromptSubmit마다 `additionalContext`로 주입한다(`src/main/hive.ts:2565-2586`; 주입 지점 `src/main/hooks.ts:533-577`). 앱 재시작 후 god 세션이 옛 플로어를 기억하는 문제를 "컨텍스트에 진실을 밀어 넣기"로 푼다 — 재미있는 패턴.
- 작업 보드 `tasks.json`은 god이 손으로 쓰는 파일이고, 렌더러가 부분 모델로 덮어써 필드를 날리는 문제를 `mergeTaskLedger`/`patchTaskInLedger`로 방지한다(`src/shared/taskLedger.ts:1-87`). 즉 **스키마가 코드가 아니라 관례**다.

### 2.2 정기 미션(스케줄러)
- 내장 미션 `OPS_STANDUP_MISSION`(기본 1시간): god에게 "누가 뭐 하는지, task가 정체·과예산·breaker인지 검토하라"는 dispatch(`src/main/config.ts:62-130`). 추가로 compact 유지보수 미션 2시간(`config.ts:82-101`).
- 하트비트 beat: `buildHeartbeatDigest`/`reengageGod`/`runBreakerBeat`(`src/main/index.ts:1130-1287`). 조용한 플로어·정체 판정 후 god PTY에 직접 nudge. PTY에 최근 출력이 있으면(mid-stream) 타이핑하지 않는다(idle handshake).
- "god이 스스로 spawn 가능" 플래그 `orchestratorMaySpawn`(기본 **false**, `config.ts:428`)가 켜져야 임시 워커 큐가 동작한다.

### 2.3 임시 워커(god-triggered) — 파일 큐 + 동시성 상한
- god이 `HIVE_ROOT/spawn-requests/<id>.json`을 떨어뜨리면 MAIN이 1.5s 폴링으로 집어 `spawnAgentCore`로 격리 워커를 띄운다(`src/main/index.ts:4581-4618`). 요청 스키마: `objective`,`cwd` 필수, `isolate` 기본 true, `tokenCap` 선택(`index.ts:4591-4615`).
- 동시성: "live worker 마무리→슬롯 회수→새 요청을 상한까지" 순서의 단일 tick(`index.ts:4886-4889`), 상한 `maxConcurrentWorkers=4`(`config.ts:439`), idle 20분 reap(`config.ts:440`), 워커 토큰 캡 기본 0=`무제한`(`config.ts:442`).
- 완료 신호는 워커가 god에게 `act:"done"` outbox 메일을 보내는 것(`index.ts:4818`). 워크트리는 **통합 전 자동 삭제 금지** safety gate(`index.ts:479-491`), GC는 "clean+integrated" 증명 시에만(60s throttle, `index.ts:4841-4884`).
- **없는 것**: 의존성 그래프, 우선순위 큐, 공정성, 재시도/백오프, 결정적 재실행. 전부 god의 판단에 위임된다.

### 2.4 중지/취소 — 3단
1. **hook 경계 정지**: `ControlRegistry.halt` → 다음 hook에서 `{continue:false}`로 **PTY를 죽이지 않고** 정지(`src/main/control.ts:91-116`, `src/main/hooks.ts:421-427`). steer/pause/gateTool도 hook 반환값으로 구현(`control.ts:63-89`).
2. **PTY kill**: 일반 에이전트 종료 경로. 워커는 teardown safety gate를 탄다.
3. **Closing Time**(floor 전체 graceful shutdown): 사람이 공지 → god이 방송 → 각 워커가 WIP 커밋/`memory.md` 기록 후 `CLOSING-TIME-ACK` 회신 → god이 `CLOSING-TIME-COMPLETE` → teardown(`src/main/closingTime.ts:1-51`). 6분 타임아웃, 완료 후 2.5s grace. "일괄 kill"이 아니라 **ACK 기반 데이터 무손실 종료**라는 점이 참신하다.

### 2.5 사람 개입 지점
- 별도 승인 큐가 **없다**. 승인은 각 CLI의 네이티브 권한 프롬프트이고, 원격은 `/remote-control`로 처리한다는 설계(`HIVE.md:45-52`). god이 위험/비용/충돌만 사람에게 올린다.
- UI에서는 god과의 대화 + `humanQA` 카드가 사실상의 HITL 표면이다.

### 2.6 CLI 어댑터 매트릭스 (12개 provider)
- provider 레지스트리: `src/shared/agentProvider.ts`(718줄) + `src/main/cliInstall.ts`, `nodeInstall.ts`. spawn 시 provider를 명령어에서 추론하고(`src/main/index.ts:2642`), provider별로 다른 스폰 인자/훅 주입/설치 사다리를 탄다.
- 훅 브리지 설치가 provider별로 따로 있다:
  - Claude Code: `hookSettings()`가 `--settings`용 JSON을 생성(`src/main/hive.ts:1245-1318`), 설정 파일은 `agents/<id>/settings.json`(`hive.ts:998-1005`).
  - Antigravity/Gemini: `installAgyHooks()`(`hive.ts:2095`), `installGeminiHooks()`(`hive.ts:2140`), `GEMINI_HOOK_SHIM`(`hive.ts:3546`).
  - Codex: `installCodexHooks()`(`hive.ts:2193`) + `CODEX_HOME` 노출/이동(`hive.ts:2273-2351`).
  - pi: `installPiHooks()`(`hive.ts:2392`) + `PI_EXTENSION`(`hive.ts:3232`).
  - OpenCode: `installOpenCodePlugin()`(`hive.ts:2429`) + `OPENCODE_PLUGIN`(`hive.ts:3291`).
  - Crush: `installCrushConfig()`(`hive.ts:2476`).
  - Grok: `installGrokHooks()`(`hive.ts:2515`) + `GROK_HOOK_SHIM`(`hive.ts:3603`).
- 훅이 없는 provider(qwen 등)는 `proxy-bridge` 사이드카가 1-upstream 프록시를 열고 응답 usage를 `CostSample`로 역산해 원장에 넣는다(`hive.ts:3328-3450`, 재시도 3회·바인드 백오프 250/750ms, `hive.ts:236-237`).
- 한계: 각 브리지 주석에 **LIVE-UNVERIFIED**가 반복된다. pi/OpenCode/Crush/Grok 등의 정확한 확장 API·auto-load 동작은 BYOK 키 없이 검증하지 못했다고 저자가 명시한다(`hive.ts:896, 906, 2389, 2426, 2447, 2472, 3231, 3290`). 즉 "12개 CLI 지원"은 코드 경로 기준이고, 실제 통합 성숙도는 provider마다 다르다.

### 2.7 관측성 — 파일이 곧 대시보드
- `fleet.json`은 8초 스냅샷(`writeFleetSnapshot`, `src/main/index.ts:1287`)으로 에이전트별 상태/토큰/비용/breaker/inbox backlog/last tool을 담는다. god 프롬프트가 이 파일을 읽으라고 지시하고(`hive.ts:1587`), UI worker 탭도 같은 스냅샷 계열을 쓴다.
- `registry.json`(영속 메타: cwd, provider, sessionId, archived, role), `tasks.json`(칸반), `board.md`(서술 보드), `hive/log.jsonl`(라우팅/드롭/드레인), `cost-ledger.jsonl`(비용), `crash logs 50개`.
- breaker 상태는 별도 IPC 이벤트 `control:breakerState`로 렌더러에 흘러간다(`src/preload/index.ts:1038`, `hive.ts` 주석 `breaker.ts:33-40`). 컨텍스트 잔량은 Status hook의 `context_window`로 `hive:contextUpdate`(`hooks.ts:403-417`).
- 없는 것: 메트릭 백엔드, 분산 트레이스, 비용 대시보드 API. **"파일을 읽는 운영자(사람 또는 god)"**가 관측성의 전부다. fleet.json이 8초 지연되므로, 그 사이 god이 죽은 에이전트에게 메일을 보내는 stale-roster 문제가 있고, 이를 `rosterContext` 주입으로 완화한다(`hive.ts:2565-2586`).
- 흥미로운 실패 서사: transcript fallback이 누적 샘플을 매 beat 재기록해 2,417개 중복 행을 남긴 사건(`usage.ts:104-113`), hop 가드가 0 vs 12를 비교해 작동하지 않았던 사건(`test/hive-hop-cap.test.cjs:5-18`) — 둘 다 "관측 없는 재시도"가 만든 버그다.

---

## §3 메시징·라우팅·기억

### 3.1 메시지 모델 (파일 기반 actor)
- 정규화된 메시지: `id`(하네스 발급), `from/to`, `act`(inform/request/query/propose/done…), `subject/body`, `conversation`, `in_reply_to`, `requires_reply`, `hops`, `created_at`(`src/main/hive.ts:1619-1642`). 단일 writer 원칙: 각자 자기 `outbox/`에만 쓰고, 라우터가 수신자 `inbox/`로 **원자적 rename/쓰기**한다(`hive.ts:1647-1652`). 수신측은 `cursor.json`으로 1회만 처리(`hive.ts:1467-1490`).
- 라우터: 1.5s 폴링(`hive.ts:1832-1838`), `deliver` 실패(수신자 디렉터리 없음) → god에게 bounce(`hive.ts:1654-1668`), 파손 outbox JSON은 `.sent/bad-*`로 격리해 무한 재처리를 막는다(`hive.ts:1887`).
- Stop hook 자동 소진: 완료 시점에 inbox가 남아 있으면 `{"decision":"block","reason":...}`으로 **계속 일하게** 만든다(`hive.ts:1467-1490`). `stop_hook_active` 가드(`src/main/hooks.ts:492`)로 무한 루프를 막는다.

### 3.2 루프/스팸 가드
- `hops`는 **하네스 소유**다. 에이전트가 쓴 값은 `[0, HOP_CAP]`으로 클램프만 하고(`hive.ts:1637`), 하네스 bounce에서만 +1(`hive.ts:1659-1668`). `HOP_CAP=12`(`hive.ts:205`). 회귀 테스트가 "agent-authored hops:13이 첫 배달을 drop하지 않아야 함"과 "relay loop이 12에서 멈춰야 함"을 고정한다(`test/hive-hop-cap.test.cjs:113-151`).
- hookless provider(Kimi 등)로 보낸 메일은 무조건 god에게 undeliverable bounce → god이 재라우팅. 이 경로가 relay loop의 온상이라 캡이 필수였다(테스트 주석 `test/hive-hop-cap.test.cjs:5-11`).
- **비용 폭주 가드**는 메시지가 아니라 토큰/진행도 기반: breaker의 `no-progress` arm(진행 파일·툴 스팬·사람 대화 3시계, `src/main/breaker.ts:398-411`).

### 3.3 기억(memory)
- 1차: `agents/<id>/memory.md` 마크다운 + 스폰 시 템플릿 생성(`hive.ts:764-767`), 종료 시 갱신 유도. `.gitignore`로 설정/커서/메일을 mine 대상에서 제외(`hive.ts:239-274`).
- 의미 검색: **외부 `mempalace` CLI**에 위임(`src/main/memory.ts:1-13`). `mempalace init --no-llm`(휴리스틱), `mine <agentDir> --wing <id>`, `search`, `wake-up`(세션 시작 digest 600-900 토큰). 10분 주기 incremental mine, 변경 없으면 skip(`memory.ts:65-73,434-446`). CLI 미설치 시 조용히 no-op.
- "지식그래프"의 실체: 벡터 DB가 아니라 **파일 백엔드 JSONL 청크 인덱스**(`src/main/kg-core.cjs:1-31`): 1200자/150자 오버랩 청킹, 영어 stopword 기반 term scoring, 스니펫 160자. `index.jsonl` + `docs/<id>/{meta.json,original.*,text.md}`. Electron main과 에이전트 CLI가 같은 순수 JS 코어를 공유한다.
- 그래프 UI: `buildGraph`(store.agents + `hiveLog(200)` + `hiveMemory`) + `forceLayout`로 SVG 렌더, 5s 폴링, hover 프리뷰, 클릭 시 메모리 탭 이동(`src/renderer/src/components/MemoryGraphPanel.tsx:1-120`). 스펙 문서(`MEMORY_GRAPH_SPEC.md`)보다 구현이 앞서 있다.

### 3.4 메시지 스키마·프로토콜 문서화
- 메시지 필드(정규화 기준): `id`(하네스 발급 `<stamp>-<rand>`, `hive.ts:213-218,1622`), `conversation`, `in_reply_to`, `from`, `to`(기본 `god`), `act`(기본 `inform`), `subject`, `body`, `hops`, `requires_reply`(request/query/propose면 기본 true), `needs_human`, `created_at`(`hive.ts:1619-1642`).
- `PROTOCOL_MD`/`COMMANDS_MD`가 에이전트에게 주입되는 문서로 생성되며(`hive.ts:2924-3102`), "하네스가 id/from/hops/timestamps를 채운다"는 규약이 문서-코드 양쪽에 있다(`hive.ts:1630-1636`).
- 로그: `appendLog`(jsonl)와 `logTail`(`hive.ts:2663-2772`). 라우터는 `routed N message(s)` 커밋(`hive.ts:1891`), 드롭은 `kind:'drop', reason:'hop-cap'`(`hive.ts:1664`), 소진은 `kind:'drain'`(`hive.ts:1479`). **운영 관측은 로그 파일 grep이 기본**이고 메트릭/트레이스 백엔드는 없다.
- 정렬/순서: 수신함 파일명은 `msg.id`(시간 기반 stamp)로 정렬하고 cursor는 "마지막 처리 id"만 저장한다(`hive.ts:1467-1474`). 즉 **at-least-once**이며, 처리 후 `.done` 이동으로 사실상 exactly-once를 근사한다. 프로세스가 메시지를 처리한 뒤 `.done` 이동 전에 죽으면 재처리될 수 있다(멱등성은 수신 에이전트 책임 — 코드 강제 없음).

### 3.5 기억의 개인정보·프롬프트 인젝션 표면
- `memory.md`는 하이브 git에 커밋된다(`HiveManager.commit`, `hive.ts:2893`). 즉 에이전트가 기억에 쓴 내용은 **로컬 git 히스토리에 영구 보존**된다. 시크릿 스크럽은 로그용 `redactSecrets`(`hive.ts:275`)에만 있고, memory 저장 경로에는 없다.
- MemPalace mine은 agentDir의 `.gitignore`로 설정/커서/메일/Codex 홈을 제외하지만(`memory.ts:33-46`, `hive.ts:239`), memory.md 본문은 그대로 인덱싱된다.
- 주입: `wake-up` digest와 `additionalContext`(로스터, 목표, steer)가 에이전트 컨텍스트에 들어간다(`hooks.ts:533-577`). 이 텍스트는 **에이전트가 쓴 파일에서 파생**될 수 있으므로, 다른 에이전트의 memory/메시지가 그대로 시스템 컨텍스트에 섞이는 프롬프트 인젝션 경로가 존재한다. 코드에 role/tool 경계를 강제하는 방어는 확인하지 못했다(§7).
- TalkAbout 관점: room 이벤트/메모리 주입에 "데이터는 명령이 아니다"를 명시하는 프레이밍이 필요하다. Munder는 이 문제를 문서(HIVE.md)의 신뢰 모델에 의존한다.

---

## §4 안전·권한·비용

### 4.1 권한/샌드박스
- provider별 `--settings`에 hook 설정과 **샌드박스 쓰기 가능 디렉터리**를 주입(`src/main/hive.ts:998-1005`, `1240-1245`). 에이전트가 만질 수 있는 곳은 자기 `agents/<id>/` + cwd + 명시적 extra dirs로 제한하려는 설계.
- `autoMode` 기본 **true**(`src/main/config.ts:427`) — 이 모드에서 provider CLI가 바이패스 권한으로 뜨는 것으로 보이나, 정확한 플래그 문자열은 provider 자동화 모듈(`src/shared/providerAutomation.ts`)에 있고 이번에 깊게 읽지 않았다(§7). 이는 "사람 승인 게이트를 CLI 네이티브에 위임"하는 설계와 상충할 수 있는 지점이다.
- hook 서버의 fail-open: 소켓이 죽으면 **"every hook has been allowed meanwhile"**라고 로그에 남긴다(`src/main/hooks.ts:205`). 가용성 우선. 보안 우선 제품(TalkAbout)에는 그대로 쓰면 안 되는 선택.
- 파일 IPC 경로 검증/컨테인먼트 테스트 존재(`test/fs-path-containment.test.cjs`). 시크릿 로그 리댁션 유틸 `redactSecrets`(`src/main/hive.ts:275`).

### 4.2 네트워크 표면 — 문서와 코드 불일치
- `SECURITY.md:7`은 "로컬 UDS 하나 외 네트워크 리스너 없음"이라고 단언한다.
- 그러나 코드에는 Slack reply/socket 서버(`src/main/index.ts:1688-1766`), 웹훅 서버(기본 포트 3849, `index.ts:1788-1832`), 그리고 `localtunnel`/`tunnelmole` 의존성(`package.json:52-53,62`)이 있다. 웹훅에는 전역+엔드포인트별 fixed-window rate limit과 비밀 토큰 게이트가 있지만(`src/main/webhook.ts:33-40,154-283`), "UDS만"이라는 보안 문서 서술은 **코드 기준으로 부정확**하다. (터널이 실제로 원격 노출을 여는지 여부는 이번에 코드를 추적하지 않았다 — §7.)

### 4.3 텔레메트리
- 이벤트 allowlist가 코드에 하드코딩(`src/main/analytics.ts:47-64`), 속성 키도 allowlist. `DO_NOT_TRACK` 존중(`analytics.ts:158`), 공식 빌드에만 PostHog 키 주입돼 포크는 no-op(`analytics.ts:191`, `TELEMETRY.md:101-103`). 메시지 본문·프롬프트·경로·API 키는 전송 금지 명시(`TELEMETRY.md:63-68`). 이 "문서=코드 allowlist" 패턴은 TalkAbout에도 그대로 쓸 만하다.

### 4.4 비용/구독 한도
- **비용 원장**: `usage.ts`의 `AgentUsageSample`은 누적(cumulative) 스냅샷이고, `appendCostLedger`가 `cost-ledger.jsonl`에 append(`src/main/hive.ts:2773`). 중복 방지를 위해 `CumulativeSampleGate`(서명 비교) + transcript fallback은 빈 sessionId로 원장에서 스스로 실격(2,417개 중복 행 사건의 후속, `usage.ts:104-139`).
- **breaker**: `healthy→steering→constrained→stopped`, 한 beat에 한 단계, 회복 시 한 단계 해제, `hardStop` 기본 off라서 "죽이지 않고 steer/constrain에서 멈춘다"(`src/main/breaker.ts:20-24,42-72`). 입력은 (a) usage diff, (b) 반복 동일 툴 호출/api_error, (c) 진행도 없음.
- **토큰 캡**: 에이전트별 캡 최대 100억(`src/shared/tokenCaps.ts:1-2`), 플로어 예산 `costCapTokens`, 워커 캡은 기본 무제한. `--max-turns` 전달 옵션도 존재(`config.ts:254`).
- **시간당 구독 한도 추적은 코드에 없다**. README는 "hourly limits로 동작"을 내세우지만(`README.md:16-22`), 실제로는 CLI가 자기 한도를 강제하는 것에 의존하고, v0.3.8의 usage-limit guard는 제거되었다고 README가 밝힌다(`README.md:397-398`). 즉 "구독 한도 추적기"는 이 리포의 기능이 아니다.
- rate limit/백오프는 웹훅 fixed-window와 proxy-bridge 바인드 재시도(3회, `hive.ts:236-237`) 정도. CLI API 429에 대한 지수 백오프는 확인 못함(§7).

### 4.5 시크릿·로깅·환경변수 표면
- 시크릿 리댁션: `redactSecrets()`(`src/main/hive.ts:275`)가 로그/에러 문자열에서 토큰류 패턴을 지운다. 비밀은 `config.ts`의 `slackSigningSecret`/`slackBotToken`(`config.ts:454-457`), provider 키(`providerKey:*` IPC), 통합 자격(`integrations:setSecret`)에 분산 저장된다. **저장 시 암호화 여부는 확인하지 못했다**(§7) — Electron `safeStorage` 사용 흔적은 이번 범위에서 보지 못함.
- 에이전트에 주입되는 환경: `HIVE_SOCK`, `AGENT_ID`, `AGENT_NAME`, `HIVE_ROOT`, `AGENT_DIR`(`src/main/hive.ts:1612`), 그 외 `buildPtyEnv`(`src/main/ptyEnv.ts`, 87줄)와 provider별 BYOK 변수. "에이전트가 자기 환경을 읽어 협업하는" 설계라 환경변수가 사실상 API의 일부다.
- 크래시 진단: 세션별 8KB tail(`pty.ts:40`) + 크래시 로그 50개 프루닝(`hive.ts:376-386`) + `agent-exit-signal-e2e`/`agent-exit-record` 테스트. "에이전트가 왜 죽었는지"를 제품 표면으로 끌어올린 점은 TalkAbout worker 로그에도 참고할 만하다.
- 로컬 표면 목록(코드 기준): UDS hook 소켓(`hive.sockPath()`, `hive.ts:462`), Slack reply/socket 서버, 웹훅 HTTP 3849, proxy-bridge loopback 리스너, 선택적 터널(`localtunnel`/`tunnelmole`). `SECURITY.md:7`의 "UDS만"은 이 목록과 어긋난다(§4.2).

### 4.6 신뢰 경계 요약
- **렌더러**: Node 없음, typed bridge(`preload/index.ts:1429`) → 상대적으로 안전.
- **main**: 파일·git·프로세스·네트워크·시크릿의 전권. god 프롬프트와 hook 반환값이 main 동작을 바꾼다 — 즉 **LLM 출력이 권한 결정에 닿는 경로**가 있다(steer/deny/additionalContext).
- **에이전트**: 각자 worktree/샌드박스 디렉터리 안에서 CLI 권한 모델을 따르되, `autoMode` 기본 ON이면 CLI의 승인 게이트가 사실상 꺼진다(`config.ts:427`, 정확한 플래그는 `providerAutomation.ts` — §7).
- **god**: 플로어 전체 파일과 `spawn-requests` 쓰기 권한. `orchestratorMaySpawn` 기본 false가 유일한 스위치(`hive.ts:438-441`).
- **외부**: Slack/웹훅은 비밀 토큰+rate limit, 터널은 원격 노출 가능. 문서(SECURITY.md)는 "로컬 전용"을 주장하지만 코드 기준으로는 **신뢰 경계가 문서보다 넓다**.
- **사람**: 승인 큐가 없고 CLI 네이티브 HITL + Slack/원격 제어가 게이트. 감사는 git 커밋/로그로 사후 추적.

---

## §5 UI·테스트

### 5.1 UI
- Command Center: `memory | graph | activity | skills | workers` 탭(`src/renderer/src/components/CommandCenterPanel.tsx:50,76,330-331`). worker 탭은 전체 워커 상한 대비 슬롯/토큰/보존 워크트리를 보여주는 스냅샷(`src/main/index.ts:5032-5049`).
- 오피스 바닥: `OfficeFloor.tsx` 1713줄, Pixi.js. 아바타는 hook 이벤트(PreToolUse→스테이션 이동, PostToolUse→복귀, Stop→idle)로 움직인다(`SPEC.md:57-67`). zustand 스토어(`src/renderer/src/store/store.ts`, 1005줄).
- 터미널: xterm.js 풀(`terminalPool.ts`, 986줄), 단일 터미널 뷰 1014줄. 뷰어가 아니라 **입력 가능**("read-only-ish", `SPEC.md:33`).
- i18n: `en`/`ar`/`zh-CN` 로케일(`src/renderer/src/i18n/locales/`). 한국어는 확인되지 않음. RTL 지원(`useDirection.ts`).
- 접근성/모션: `DESIGN.md:706-710`에 WCAG AA 대비, 색+아이콘+위치, 축소 모션 시 텔레포트 명시. 디자인 토큰은 `design/` + `tokens` 객체 병렬.
- 대화 표면: 터미널 직접 입력, MessageQueueComposer(686줄), steer 필드, Slack 스레드, hive dispatch — 텔레메트리의 `message_sent` surface와 일치한다(`TELEMETRY.md:35,44-51`).

### 5.2 테스트 전략
- 124개 `*.test.cjs` (`package.json:22`의 `test:focused`는 `node --test test/*.test.cjs`). TS 파일은 `test/load-ts.cjs`가 `typescript.transpileModule`로 CommonJS 변환 후 `new Function` 실행 — **빌드 없이** TS 모듈을 단위 테스트하는 영리한 트릭(`test/load-ts.cjs:1-69`).
- 계층: 순수 모듈/클래스 단위가 대부분(breaker, control, usage gate, task ledger, hop cap, worker wake, hooks framing, fs containment, provider config). `HiveManager`는 임시 디렉터리 `harnessHome`과 실제 파일을 쓰는 통합 스타일(`test/hive-hop-cap.test.cjs:34-47`). Electron 실행 e2e는 예외적(`test/quit-sweep.electron.test.cjs`).
- 실패를 "버그 재현 → 수정 → 회귀"로 고정하는 문화: hop cap 주석이 "가드가 0과 12를 비교하며 작동하지 않았다"는 사건을 상세히 남긴다(`test/hive-hop-cap.test.cjs:5-18`).
- 미확인: 124개 전체 실행(정적 분석 지시), 실제 PTY/CLI e2e는 아예 실행 불가(빌드 금지).

### 5.3 테스트 분포와 구멍
- 파일명 기준 대략적 분포: `hive-*` 14개, `worker-*` 5개, `hooks-*` 4개, `telemetry-*` 4개, `update-*` 6개, `provider/agent-*` 5개, `terminal-*` 9개, `ui/i18n/arabic-*` 6개, 보안/권한(`fs-path-containment`, `renderer-sandbox`, `claude-permissions-config`, `auto-mode-sandbox`) 4개, 비용/사용량(`cost-lifetime`, `transcript-usage`, `grok-usage-ledger`) 3개 등.
- 순수 로직 테스트가 두텁다: hop cap(`hive-hop-cap`), breaker(`breaker`), 제어 레지스트리(`control`), 사용량 게이트(`cost-lifetime`), 워커 wake(`worker-wake`, `worker-wake-stall`), 작업 원장 병합(`task-ledger`).
- 실행 환경 테스트는 얇다: `.electron.test.cjs` 1개(quit-sweep), `agent-exit-signal-e2e`는 sim/가짜 신호 수준. **렌더러 컴포넌트 테스트(DOM/스냅샷)는 확인되지 않았다** — UI는 타입체크+수동 QA 의존으로 보인다.
- 테스트 하네스 자체가 영리하다: `test/load-ts.cjs`가 TS import 그래프를 따라가며 트랜스파일·실행하고 `@shared/` 별칭까지 해석한다(`test/load-ts.cjs:9-64`). 이 패턴은 Python unittest에서 import 경로 트릭으로 대체 가능.
- 결론: "회귀 방지"는 강하지만 "통합 신뢰"는 약하다. 실제 CLI·PTY·Electron 조합은 릴리스 수동 QA(LIVE-UNVERIFIED 주석)에 의존한다.

### 5.4 UI 상태 일관성 — 두 개의 nudge 경로
- 워커 inbox-wake가 **두 곳**에 있다: 렌더러 `useHive.ts`의 setInterval 루프(창이 occluded되면 Chromium이 타이머를 조절)와 main의 `WorkerWakeWatchdog`(`workerWake.ts:1-57`). 후자는 같은 가드(idle 12s, boot grace 35s, HITL re-arm 5m, cooldown 60s)를 복제해 PTY에 직접 타이핑한다.
- 중복 타이핑을 막기 위해 공유 블랙리스트/cooldown이 아니라 **각자 독립 타이머**로 근사한다(`workerWake.ts:43-46`). 즉 두 경로가 동시에 활성일 때 중복 nudge 가능성이 남고, 실제로 "렌더러가 안 보이는 창에서 nudge를 놓쳤다"는 버그의 대응으로 main watchdog이 추가됐다.
- UI 상태의 원천이 셋(localStorage zustand, SQLite kv/history, hive 파일)이라, `roster.json`/`fleet.json`과 화면이 어긋날 수 있다. 이는 `registry.json`/`fleet.json`을 god의 단일 진실로 삼는 설계와 긴장한다.
- 접근성: DESIGN.md는 WCAG AA·키보드·축소 모션을 명시(`DESIGN.md:706-710`)하지만, 자동 접근성 테스트 파일은 확인되지 않았다(수동 QA 의존).

---

## §6 TalkAbout 적용안 (Python/FastAPI 기준)

전제: TalkAbout은 PostgreSQL 트랜잭션 + `core.py` 상태기계 + `domain.py` plan DAG(`work-plan/dispatch`, run 상태 `running/aborted`, abort 경로)를 이미 갖고 있다. Munder의 **파일/Electron** 메커니즘은 대부분 이식 대상이 아니고, **정책과 프로토콜**이 이식 대상이다.

### 매핑 표 (Munder → TalkAbout)

| Munder 요소 | 위치 | TalkAbout 대응 | 판정 |
|---|---|---|---|
| PTY tail ring + exit record | `pty.ts:40,67-74`, `hive.ts:2696` | `worker.py` 실패 이벤트 tail | 채택 |
| sessionId 기반 `--resume` | `hive.ts:772-777`, `index.ts:2627-3033` | worker claim/recovery 메타데이터 | 부분 채택 |
| breaker 사다리(순수 정책) | `breaker.ts:1-24,42-72` | `guard.py` + heartbeat | 채택 |
| 누적 usage + dedupe gate | `usage.ts:39-53,122-139` | cost 이벤트/ledger | 채택 |
| spawn-requests 큐 + 동시성 4 | `index.ts:4581-4618,4886-4889` | plan dispatch 동시 실행 상한 | 사상 채택 |
| worktree safety gate | `index.ts:479-491` | 산출물 보존 플래그 | 사상 채택 |
| Closing Time ACK | `closingTime.ts:1-51` | room close/abort graceful 경로 | 채택 |
| hops 하네스 소유 + 캡 | `hive.ts:1619-1668` | 메시징 도입 시 서버 카운터 | 선반영 |
| 파일 inbox/outbox + cursor | `hive.ts:1467-1652` | PG 기반 메시지 테이블 | 보류 |
| god 프롬프트 오케스트레이션 | `hive.ts:1530-1613` | 결정적 plan DAG | 거부 |
| autoMode 기본 ON / hook fail-open | `config.ts:427`, `hooks.ts:205` | 권한 모델 | 거부 |
| Pixi/xterm 오피스 UI | `OfficeFloor.tsx`, `terminalPool.ts` | TalkAbout 웹 UI | 거부 |
| memory graph SVG | `MemoryGraphPanel.tsx` | plan DAG 시각화 | 보류 |
| MemPalace 의미 검색 | `memory.ts` | memory-preview 확장 | 보류 |
| telemetry allowlist | `analytics.ts:47-64` | 사용량 통계(도입 시) | 채택 |

### 채택 (Adopt)
1. **Breaker를 순수 정책 모듈로** — `talkabout/domain.py` 옆에 `guard.py`(또는 `bounds.py` 확장)로 `healthy/steering/constrained/stopped` 사다리와 "beat당 1단계 상승·1단계 회복, hardStop 기본 off" 정책을 옮긴다. 근거: `src/main/breaker.ts:1-24,42-72`는 부작용이 전혀 없고 입력이 (usage diff, 반복 툴, 진행도)뿐이라 FastAPI 워커 루프에 그대로 맞는다. TalkAbout의 `worker.py` heartbeat/lease와 결합해 "steer 메시지→도구 게이트→정지" 순으로 집행. 한국어 UI 문구는 새로 써야 함.
2. **누적 usage + 중복 게이트** — `src/main/usage.ts:39-53,122-139`의 `AgentUsageSample`(누적 스냅샷, `session_id`가 resume/비용 dedup 키)과 `CumulativeSampleGate` 패턴을 `worker.py`/`store.py`에 도입. TalkAbout은 세션/재시도 개념이 있으므로 "동일 누적치 재기록 금지"가 바로 유효하다.
3. **PTY/프로세스 종료 테일 링 + 종료 사유 기록** — `src/main/pty.ts:37-49,67-74`의 8KB tail ring과 `recordAgentExit`(`hive.ts:2696`) 사고방식을 `worker.py` 이벤트에 맞게: 런타임 stderr 마지막 N KB를 `task.failed`/`run.aborted` 이벤트에 첨부. TalkAbout은 이미 이벤트 원장이 있으므로 **테일만 bounded로 추가**하면 된다(전체 로그 저장 금지).
4. **Closing Time ACK 프로토콜** — `src/main/closingTime.ts:1-51`의 "공지→ACK→완료→teardown, 6분 타임아웃, 2.5s grace"를 `domain.py`의 room close/abort 경로에 이식. 현재 TalkAbout의 abort는 즉시 task를 cancel하는데, "진행 중 작업의 마지막 상태를 남기게 하는 graceful 변형"은 제품 차별점이 된다.
5. **하네스 소유 카운터(hop cap)** — 지금 TalkAbout에 에이전트 간 메시징은 없지만, 향후 room 내 agent-to-agent 메시징을 넣을 때 `hops`를 서버 소유로 두고 `0..HOP_CAP` 클램프 + bounce 시에만 증가시키는 규칙(`hive.ts:1619-1668`)을 `core.py` 메시지/이벤트 모델에 선반영. 클라이언트가 보낸 카운터를 신뢰하지 않는 원칙은 TalkAbout의 generation/revision 검증과 동형이다.
6. **worker 동시성/워크트리 safety gate 사상** — `maxConcurrentWorkers=4`, idle 20분 reap, "미통합 작업이 있으면 삭제 금지"(`config.ts:439-442`, `index.ts:479-491`)를 plan dispatch에 대응: `run`에 동시 실행 노드 상한, 노드 idle 타임아웃, 워크스페이스 산출물 보존 규칙. 단, TalkAbout은 PG로 원자적 상태 전이가 가능하므로 **파일 GC 대신 상태 전이 + 보존 플래그**로 구현.
7. **텔레메트리 allowlist 패턴** — `src/main/analytics.ts:47-64`처럼 이벤트·속성 화이트리스트를 코드 상수로 두고 문서와 1:1로 맞추는 방식. TalkAbout이 사용량 통계를 넣을 경우 유용.
8. **"wake-up digest" 기억 주입** — MemPalace의 `wake-up`(600-900 토큰 digest, `memory.ts:441-446`) 개념을 TalkAbout의 `memory_preview`/room context 주입에: 전체 기억이 아니라 **세션 시작용 bounded digest**를 별도 생성.

### 보류 (Hold)
- **Memory Graph UI**: MIT라 `buildGraph`/`forceLayout`(`MemoryGraphPanel.tsx:8-22`)을 차용할 수 있지만, TalkAbout은 이미 work-plan DAG UI가 있고 React/Pixi 의존성이 다르다. "plan DAG 시각화"에 force layout을 쓰는 정도가 ROI다.
- **markdown-first + MemPalace**: 외부 Python CLI 의존은 제품 통합 비용이 크다. TalkAbout의 memory-preview는 PG 기반이므로, "마크다운 1차 + 검색 인덱스는 나중" 원칙만 차용.
- **파일 inbox/outbox + 단일 git 커미터**: TalkAbout은 PG 트랜잭션이라 필요 없음. 단 "메시지 1건=커밋 1건"은 안티패턴으로 기록해 둘 것(성능).
- **god 프롬프트 오케스트레이션**: LLM에게 assignee/DAG를 맡기는 방식은 TalkAbout의 결정적 dispatch와 충돌. 가져오지 않는다.

### 거부 (Reject)
- **autoMode 기본 ON / hook fail-open**: 보안 우선 제품에 부적합(`config.ts:427`, `hooks.ts:205`).
- **Electron/Pixi/xterm 오피스 UI**: 제품 표면이 다름.
- **`tasks.json` 평평한 원장**: TalkAbout의 DAG+revision 모델이 우위.

### 구현 순서 제안 (리스크 낮은 것부터)
1. **`guard.py`(순수 정책) + 단위 테스트**: breaker 정책만 이식. 상태는 프로세스 메모리(현재 `worker.py` heartbeat 옆)에 두고 `core.py`에 저장하지 않는다 → DSH 이벤트 원장 오염 없음. 리스크: 낮음. 검증: 순수 함수 테스트 + 기존 dispatch 스위트.
2. **crash tail**: `worker.py` 실패 이벤트에 마지막 8KB만 첨부. 이벤트 크기 상한(현재 room byte capacity)과 충돌하지 않도록 `bounds.py` 상수로 캡. 리스크: 낮음.
3. **usage dedupe + cost 이벤트**: `store.py`/`worker.py`에 누적 스냅샷·서명 게이트. 기존 generation/revision 검증과 같은 트랜잭션 경계에서 기록. 리스크: 중간(과금 집계 불일치).
4. **plan dispatch 동시 실행 상한 + idle 타임아웃**: `core.py` tick에서 queued 노드 중 실행 가능 수를 제한. Munder의 "슬롯" 개념을 run에 추가. 리스크: 중간(DAG 의존성과 상호작용, 테스트 필요).
5. **Closing Time ACK**: `domain.py`의 room close 경로에 "abort 전 공지·ACK 대기(타임아웃 6분)" 모드 추가. 기존 abort는 유지(즉시성 보장). 리스크: 중간(시간 기반 상태 전이).
6. **메시징 도입 시 hop 소유권**: 새 테이블/이벤트에 `hops` 서버 소유 규칙을 미리 문서화. 리스크: 낮음(아직 미구현).

각 단계는 기존 TalkAbout 테스트 불변 + 신규 단위 테스트로 검증 가능하며, 1·2번은 하루 이내, 3~5번은 각각 별도 설계 검토가 필요하다(추정 — 실측 아님).

### 참고하지 말아야 할 안티패턴 (실패 사례)
1. **메시지 1건 = git 커밋 1건**(`hive.ts:1671-1676, 1891`): 감사성은 얻지만 처리량/지연/저장소 팽창 비용이 크다. TalkAbout은 PG 트랜잭션+이벤트 원장으로 이미 감사성을 확보하므로 커밋 규약을 흉내 낼 필요 없음.
2. **거대 단일 파일**(`index.ts` 5511줄): 테스트는 되지만 리뷰/온보딩 비용이 크다. TalkAbout는 `core.py`/`domain.py`/`worker.py`로 이미 분리돼 있으므로 유지.
3. **문서보다 코드가 앞서거나 뒤처짐**: MEMORY_GRAPH_SPEC, SPEC, SECURITY, README 버전. TalkAbout의 `docs/IMPLEMENTATION_STATUS.md`처럼 "현재 구현 상태"를 한 곳에서 관리하는 규율을 유지할 것.
4. **fail-open 훅/기본 바이패스 권한**: 가용성 최적화가 보안 기본값을 뒤집는 사례. TalkAbout은 기본 fail-closed.
5. **프롬프트에 운영 규약을 수천 자로 우겨넣기**(`hive.ts:1587`): 모델 교체·컨텍스트 압축마다 규약이 희석된다. TalkAbout은 결정적 코드+명시적 API(`request_turn`, dispatch)로 같은 일을 한다.
6. **세 개의 상태 원천**(localStorage/SQLite/파일): 어긋남을 사후 병합 함수(`taskLedger.ts`)로 봉합한다. TalkAbout은 DB 단일 원천을 지킬 것.

### 재사용 가능한 코드 자산 (MIT — 이식 시 파일 단위 명시 권고)
- `src/main/breaker.ts` (416줄): 부작용 없는 정책 모듈. 인터페이스(`BreakerInput/BreakerDecision`)가 그대로 Python dataclass로 옮겨진다. 가장 이식 가치가 높다.
- `src/main/control.ts` (128줄): pause/gateTool/halt/steer의 상태기계. steer 큐 상한(20)과 "최신 우선" 정책 포함.
- `src/main/workerWake.ts` (309줄): idle/boot/HITL/cooldown 가드 상수와 판정 로직. PTY nudge와 분리된 순수 판정부가 참고 대상.
- `src/main/usage.ts` (140줄): 누적 샘플 스키마 + dedupe 게이트. "누적을 증분으로 오해하지 말 것" 불변식 주석이 좋다.
- `src/main/closingTime.ts` (237줄): ACK 기반 종료 상태기계(페이즈, 타임아웃, grace). TalkAbout room close에 맞게 재작성.
- `src/shared/taskLedger.ts` (87줄): 부분 모델 병합 규칙. JSON 원장을 쓰는 어떤 기능에도 재사용 가능.
- `test/load-ts.cjs` (69줄): 빌드 없는 TS 단위 테스트 로더 — TalkAbout에는 해당 없지만 "테스트가 빌드에 의존하지 않게" 사상은 참고.
- 라이선스: 코드는 MIT(`LICENSE`), 자산은 별도(`LICENSE-ASSETS`). 파일 복사 시 출처 커밋(`8702b73`)과 MIT 고지를 남길 것.

---

## §7 미확인/한계

- **정적 분석 한계**: 빌드/실행/테스트 실행 금지 지시에 따라 124개 테스트를 돌리지 않았고, Electron 앱을 띄우지 않았다. 따라서 "실제로 동작한다"는 증거는 코드·주석·테스트 소스 수준이다.
- **읽지 않은 주요 파일**: `src/main/slack.ts`(480줄), `transcript.ts`, `realtime*.ts`, `fs.ts`, `integrationBroker.ts`, `hiddenClaude.ts`, `reflect.ts`, `hooks.ts`의 나머지(410줄 이전), `index.ts`의 5.5k줄 중 3분의 1 이상, `src/renderer`의 OfficeFloor/scene 대부분, `.github/`, `hive/`, `docs/`, `.claude/`. 자산성 디렉터리는 지시대로 제외.
- **문서-코드 불일치 후보(추가 확인 필요)**:
  1. `MEMORY_GRAPH_SPEC.md:4-5` — "구현 없음" vs `MemoryGraphPanel.tsx` 구현 완료.
  2. `SPEC.md:16-20` — "에이전트 간 메시지 버스 아님" vs `hive.ts` 라우터/인박스 구현.
  3. `SECURITY.md:7` — "UDS만" vs Slack/웹훅 HTTP 서버 + 터널 의존성.
  4. `README.md:33` — 배지 0.5.6 vs `package.json:3` 0.4.6.
  5. `README.md:16-22` — "hourly limits" vs 코드에 시간당 한도 추적 없음(구독 한도는 CLI 위임).
  6. `src/main/hive.ts`의 73개 TODO/FIXME/LIVE-UNVERIFIED 마커: Pi/OpenCode/Crush/Grok 브리지는 "BYOK 키 없이 검증 불가"로 명시(`hive.ts:896,906,2389,2426,2472`).
- **미확인 사항**: (a) god의 task 실제 배정 정확도/비용 — 프롬프트 실측 없음. (b) `dependsOn`/deps가 렌더러에서만 쓰이고 god 스케줄러에 반영되는지. (c) `providerAutomation`의 autoMode 플래그 문자열. (d) localtunnel/tunnelmole가 실제로 활성화되는 조건과 보안 영향. (e) 124개 테스트의 실제 통과 여부. (f) Pro/유료 기능이 코드에 게이팅돼 있는지 — 라이선스/페이월 코드 검색 결과 0건이라 OSS 빌드에 포함된 것으로 보이나 확정 불가. (g) 성능 수치(부팅, PTY 1개당 메모리, 라우터 지연) — 측정 불가.
- **커밋 특이점**: 분석 스냅샷의 HEAD가 `seo-engine: day 36 PM, DeepSeek Harness alternatives`로, 릴리스 태그가 아니라 SEO/랜딩 작업 커밋이다. 따라서 README의 최신 기능 서술과 코드가 어긋날 가능성을 배제할 수 없다(위 4번).
