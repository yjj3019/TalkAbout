# 01 — Orca 제공자(provider) 통합 계층 정밀 분석

분석 대상: `orca-analysis/` (stablyai/orca, HEAD `51dcef8`, MIT) · 분석 기준: TalkAbout (`talkabout/*.py`, `docs/PROVIDERS.md`)
작성: providers-analyst · 범위: `src/main/<provider>/*`, `providers/`, `provider-process/`, `agent-launch/`, `agent-hooks/`, `acp/`, `jsonl-rpc/`, `usage/`, `rate-limits/`

## 0. 요약 — 가장 임팩트 큰 결론 6개

1. **Orca의 제공자 공통 추상화는 "이름 분기"가 아니라 "선언된 능력(capability) + 어댑터 메서드 존재 검증"이다.** 레지스트리가 `capabilities.rewind === true`인데 어댑터에 `rewind`가 없으면 **생성자에서 throw**한다(`src/main/native-chat/agent-session-wire/structured-agent-registry.ts:36-43`). TalkAbout `providers.py`에는 이 층이 없다.
2. **세션 재개(resume)의 정본은 "provider가 발급한 세션 ID + 그 세션을 낳은 계정 홈(account home)"이고, 계정 신원(email/home)이 아니다.** Orca는 훅 payload에서 provider별 키를 뽑아 `{key, id, transcriptPath}`로 정규화하고(`src/shared/agent-session-resume.ts:200-290`), 레코드에 `accountHome`을 못박으며(`src/shared/agent-session-account-home.ts:5-11,73-82`), 실제 자식 env에는 기본 홈과 다를 때만 `CLAUDE_CONFIG_DIR`을 pin한다(`src/main/claude/claude-config-dir-pin.ts:31-44`). TalkAbout은 반대로 **검증 불가능한 계정 신원을 게이트로 세워 재개를 영구 차단**한다(`talkabout/codex_sessions.py:69-70,113-116` + `talkabout/agent_runtime.py:399`).
3. **Claude stream-json은 "알려진 타입 화이트리스트 → 5분류 disposition"으로 처리한다.** `timeline-substantive / stream-into-item / status-chrome / suppressed-benign / error-surface`(`src/main/native-chat/agent-session-wire/provider-frame-disposition.ts:6-16,95-149`). TalkAbout은 반대로 **모르는 타입 = 프로토콜 실패**로 닫는다(`talkabout/agent_runtime.py:949-950`). Orca 쪽이 Claude의 빈번한 신규 프레임에 강하고, TalkAbout 쪽이 증거 위조에 강하다 — 둘을 섞어야 한다.
4. **Codex는 이 저장소에서 `codex exec --json`을 소비하지 않는다.** Codex 통합의 정본은 `codex app-server` JSON-RPC이고(`src/main/codex/codex-structured-thread-open.ts:64-97`), 이벤트 목록은 `codex-app-server-notification-schema.ts:2-75`(72종)다. 따라서 TalkAbout의 `exec --json` 파서는 **Orca에 대응물이 없는 TalkAbout 자산**이며, Orca에서 가져올 것은 파서가 아니라 **이벤트 분류표와 blocking request 응답표**다.
5. **usage-limit은 두 축으로 감지한다: (a) 실행 중 스트림 관측, (b) 계정 단위 폴링.** 관측은 `rate_limit_event`/`assistant.api_error`/`account/rateLimits/updated`를 타입화된 실패 사실로 바꾸고(`src/main/claude/claude-usage-limits.ts:52-135`, `src/main/codex/codex-usage-limits.ts:31-114`), 실패 종류를 `rate-limited / stale-token / missing-scope / server`로 분류해 재시도 여부를 결정한다(`src/main/rate-limits/claude-usage-error-classification.ts:11-34`). **TalkAbout은 이미 받고 있는 `rate_limit_event`를 그냥 버린다**(`talkabout/agent_runtime.py:949`).
6. **환경 격리는 "허용목록 + 삭제목록 + 거부(not drop)" 3단이다.** 특히 계정 변수는 값이 이상하면 **버리지 않고 거부**한다 — 홈이 바뀌면 다른 계정으로 실행되기 때문이다(`src/shared/agent-launch-account-env.ts:35-52`). TalkAbout은 `adapters.py`에서 **Codex 프로세스에 `ANTHROPIC_API_KEY`를, Claude 프로세스에 `OPENAI_API_KEY`를 함께 넘긴다**(`talkabout/adapters.py:21-27`) — 교차 자격증명 누출.

---

## 1. 제공자 통합의 공통 추상화

### 1.1 3계층 구조 (Orca)

| 계층 | 파일 | 역할 |
| --- | --- | --- |
| A. 선언(descriptor) | `src/main/native-chat/agent-session-wire/structured-agent-definition.ts:7-23` | `agent`, `handleTransport`, `accountHomeVariable`/`accountLocatorKind`, `capabilities`, `restingOptions` |
| B. 능력(capability) | `src/shared/agent-session-capabilities.ts:1-18` | `rewind/compact/threadGoal/contextUsage/imagePrompts/steering/approvalEnforcement` |
| C. 레지스트리+검증 | `src/main/native-chat/agent-session-wire/structured-agent-registry.ts:26-47` | 중복 등록 거부, **선언한 능력의 어댑터 메서드 부재 시 throw** |

정의 원문:

```ts
// structured-agent-definition.ts:7-18
export type StructuredAgentDefinition = AgentSessionStoredAgent & {
  capabilities: AgentSessionCapabilities
  managedRuntime?: () => boolean
  restingOptions: {
    acceptsKey: (key: string) => boolean
    fallbackModels: () => AgentSessionModelOption[] | null
    effortDefaultsToModel: boolean
  }
}
```

능력 선언은 "UI가 분기해도 되는 유일한 근거"로 못박혀 있다: `/** Branch on declared capabilities, not agent names. Sessions may narrow them ... never widen them */`(`src/shared/agent-session-capabilities.ts:1`). 승인 강제 주체도 능력값이다 — `approvalEnforcement: 'provider' | 'orca'`(같은 파일 16-17).

Codex 선언 예(`src/main/codex/codex-structured-agent-definition.ts:5-20`):

```ts
export const CODEX_STRUCTURED_AGENT: DirectoryAccountAgentDefinition = {
  agent: 'codex',
  handleTransport: CODEX_STRUCTURED_HANDLE_NAMESPACE.transport,
  accountHomeVariable: 'CODEX_HOME',
  capabilities: { rewind: true, compact: true, threadGoal: true, contextUsage: true,
    imagePrompts: true, steering: 'inject', approvalEnforcement: 'provider' },
  restingOptions: { acceptsKey: isCodexTurnOptionKey, fallbackModels: () => null, effortDefaultsToModel: false }
}
```

등록은 한 파일에서 조립된다: `src/main/runtime/structured-agent-session-runtime.ts:254-262`가 `STRUCTURED_AGENT_RUNTIME_REGISTRATIONS`를 import해 `{definition, adapter}`로 만들고 `new StructuredAgentRegistry(registrations)`에 넣는다. 실행 자체는 **공통 합성기**가 맡는다: `composedPart()`가 `record` 인가(provider/호스트/account pin 검사) → `cwd` 결정 → `env` 결정 → 시각자원 준비를 **레이지 1회** 수행하고(`src/main/runtime/structured-agent-launch-composition.ts:133-168`), `composeStructuredLaunch()`가 `cwd/env/envToDelete/commandEnv`를 **에이전트 파트가 아니라 합성기가 소유**하도록 강제한다(같은 파일 62-73, 170-198). 주석이 규약을 그대로 말한다: `// Compose shared admission, cwd, environment, visuals and child sealing; agent parts add only agent-specific inputs and never set cwd/sealed env.`(같은 파일 1행)

TUI(대화형 PTY) 쪽은 별도 디스크립터다: `src/shared/tui-agent-config-types.ts:24-77`의 `TuiAgentConfig`가 `detectCmd`, `launchCmdByPlatform`, `promptInjectionMode`(argv/flag-prompt/stdin-after-start…), `draftPasteReadySignal`, `preflightTrust` 등을 선언하고, `src/shared/tui-agent-config.ts:21-34`(claude), `67-75`(codex)가 값이다. **비대화형(구조화 세션)과 대화형(PTY) 디스크립터가 분리되어 있다** — TalkAbout의 `TransportSpec` 설계에 그대로 필요한 구분이다.

### 1.2 TalkAbout 대조

`talkabout/providers.py:19-42`의 `Provider`는 `id/label/transport/status/key_env/base_url/default_model/note/aliases`뿐이다. 능력 개념이 없고 `transport`는 자유 문자열(`"cli"`/`"http"`), `status`는 자유 문자열(`"ready"`/`"gated"`)이다(`providers.py:45-110`). 실행 가능성은 `agent_runtime.py:425-445`의 `probe()`가 즉석에서 계산한다:

```python
# talkabout/agent_runtime.py:433-437
reason = ('runtime_unavailable' if not available else
          'runtime_shell_wrapper_unsupported' if script else
          'runtime_auth_required' if self.kind == 'grok' and self.auth_mode == 'native' and not grok_native_ready else
          'runtime_auth_required' if self.auth_mode == 'api_key' and not key_present else
          'runtime_containment_unsupported' if not _containment_supported() else None)
```

즉 **"선언"과 "구현"이 같은 파일/같은 switch에 붙어 있지 않고, 능력이 데이터가 아니라 if-chain**이다. `providers.py`는 metadata only라고 스스로 규정한다(1-12행). 이 구조에서는 새 CLI를 추가할 때 (a) registry 항목, (b) `probe()`, (c) `command()`, (d) 파서, (e) preflight flag 목록이 **5곳에 흩어진다**.

| 항목 | 판정 | 비용 | TalkAbout에 붙일 위치 |
| --- | --- | --- | --- |
| `Provider`에 `capabilities` dict + `transport` enum + `account_home_env` 추가 | **[채택]** | S | `talkabout/providers.py:20-42` |
| 선언-구현 일치 검증 테스트(레지스트리 검증) | **[채택]** | S | `tests/test_providers.py`(신규) + `providers.py`의 `validate()` |
| 공통 launch 합성기(cwd/env/child seal을 에이전트 코드가 못 건드리게) | **[차용]** | M | `agent_runtime.py`의 `_run()` 분해, `runtime.py` |
| 에이전트명 분기 제거(능력 기반 분기) | **[차용]** | M | `worker.py`/`participation.py` 판정부 — 단, TalkAbout은 서버 판정이 정본이므로 무리한 이식 금지 |
| PTY/TUI 디스크립터 전부 | **[제외]** | L | TalkAbout은 비대화형 1회 호출만 쓴다. 붙이면 상호작용 승인 UI가 필요해져 서버 증거 모델과 충돌 |

---

## 2. CLI 설치·버전 탐지와 미설치 처리

### 2.1 탐지 3단 (Orca)

**(1) 탐지 명령 생성** — 디스크립터에서 자동 파생: `src/shared/tui-agent-detection-commands.ts:20-74`. `detectCmdAliases`(예: codebuddy←`cbc`, claude-agent-teams←`orca-dev`,`orca-ide`), `detectRequiredCommands`(예: Agent Teams는 `orca`+`claude` 둘 다 있어야 설치로 인정 — `tui-agent-config.ts:38-40`), `detectUnsupportedRuntimes`(win32/wsl에서는 탐지 제외 — 41-42행)를 반영한다.

**(2) PATH 탐지 + 셸 PATH 보수** — `src/main/agent-hooks/local-agent-cli-presence.ts:162-225`. GUI에서 띄운 Electron은 로그인 셸 PATH를 모르므로 `hydrateShellPath()`로 보수하고(143-160), Windows는 `PATHEXT` 확장자 후보를 만들고(76-99), WSL 게스트에서는 Windows 드라이브 마운트 경로를 제외한다(170-173). 사용자 `agentCmdOverrides`는 절대경로/`~` 확장/`unknown` 3분기로 처리한다(206-215). **"없다"가 아니라 "모른다(unknown)"를 별도 상태로 둔 점**이 핵심이다(`local-agent-cli-presence.ts:19-25`).

**(3) 버전 검사** — 최소 버전 상수는 선언형이다: `CODEX_MINIMUM_SUPPORTED_VERSION = '0.136.0'`(`src/shared/codex-cli-installation.ts:3`), 파싱은 `codex 0.164.0`/`v0.164.0` 형태를 정규화하고 **후보들이 모두 같을 때만** 버전으로 인정한다(11-22행). Codex 실행 시 버전 게이트는 launch resolver를 감싼다:

```ts
// src/main/codex/codex-cli-installation-error.ts:20-31
const check = await checkAgentCliVersion(input, [CODEX_MINIMUM_SUPPORTED_VERSION], readVersion)
if (check.status === 'tooOld') throw AgentCliInstallationError.tooOld('codex', check)
if (check.status !== 'ready') console.warn(`[codex-cli-version] Could not verify ${input.program}; allowing structured chat`)
```

여기서 **`tooOld`는 거부, `unverifiable`은 경고 후 허용**이라는 2단 정책이 보인다. 반면 기능 플래그 지원은 "최초 등장 버전 + 실패 진단 캐시"로 따로 판정한다: `src/main/claude/claude-cli-flag-support.ts:6-24`(`--thinking-display` 2.1.94, `--plugin-dir` 2.0.25, `unknown option` 정규식), 캐시 키는 **바이너리 realpath+mtime+cwd**(52-60행)라 자체 업데이트/샤드 변경을 따라간다.

**미설치 분류** — `ENOENT`가 명령 자체를 못 찾은 것인지(스크립트 인터프리터 누락이 아닌지)를 구분하고(`src/main/provider-process/provider-executable-missing.ts:5-19`), 그 사실을 `providerExecutableMissing` 플래그로 오류 체인에 실어 보낸다(21-23행). UI에는 `{agent, installedVersion, minimumVersion}` 사실로 전달하며 구형 클라이언트용 legacy 필드도 함께 보낸다(`src/shared/agent-cli-installation-problem.ts:7-46`).

### 2.2 대조: TalkAbout의 preflight는 이미 더 엄격하다

`talkabout/agent_runtime.py:465-542`는 실행 전에 `--version`을 파싱하고(`509-512`), **명령 스코프별 help를 실제로 실행**해 필수 플래그를 검사한다:

```python
# talkabout/agent_runtime.py:514-532 (요약: 필수 플래그 사전 + exec/resume 스코프 재검사)
required = {
  'codex': ('--ignore-user-config', '--strict-config', '--config'),
  'claude': ('--restricted', '--safe-mode', '--strict-mcp-config', '--mcp-config',
             '--setting-sources', '--settings', '--permission-mode', '--tools',
             '--input-format', '--output-format', '--session-id', '--resume'), ...}
...
resume_help = local(['exec', 'resume', '--help'])
for flag in ('--ignore-user-config', '--strict-config', '--ignore-rules', '--json', '--config', '--skip-git-repo-check'):
    if not self._has_flag(resume_help, flag): raise AdapterError('runtime_version_unsupported')
```

Orca의 `withSupportedCodexCli`는 "버전만" 게이트하고 `unverifiable`은 통과시키지만, TalkAbout은 **exec/exec resume 두 스코프의 플래그 실재까지** 요구한다. 이건 유지해야 할 우위다(`[대조]`). 다만 TalkAbout에 없는 것:

- **alias 탐지**(`codex-cli`, `chatgpt` 같은 실행 파일명 변형) — 현재 `shutil.which(kind)` 단일(`agent_runtime.py:392`).
- **셸 PATH 보수** — Windows 서비스/GUI 실행 시 PATH 미탐지.
- **`unknown`(모름) 상태** — TalkAbout은 못 찾으면 즉시 `runtime_unavailable`로 단정한다(`agent_runtime.py:426,433`).
- **설치 안내 사실** — `{installed, minimum, agent}` 구조를 서버 Event로 노출하지 않는다.
- **플래그 지원 캐시** — 매 프로세스마다 help를 다시 실행한다(`_verified_executable` fingerprint 캐시는 프로세스 내부 한정, `agent_runtime.py:471-477`).

| 항목 | 판정 | 비용 | 위치 |
| --- | --- | --- | --- |
| 실행파일 alias/필수 동반명령 탐지 | **[채택]** | S | `agent_runtime.py:392` 주변 `resolve_executable()` 신설 |
| 셸 PATH 보수(Windows) | **[차용]** | M | `processes.py`/`cli.py` 기동부 — Orca `local-agent-cli-presence.ts:143-160` 대응 |
| `missing`/`unknown`/`too_old` 3분류 + 설치 안내 사실 | **[채택]** | S | `agent_runtime.py:425-445`, Event 스키마(`domain.py`) |
| 플래그 지원 캐시(버전+help 결과) | **[차용]** | S | `agent_runtime.py:465-542` |
| `unverifiable` 허용 정책 | **[제외]** | — | TalkAbout은 합성 테스트를 실호출로 표시하지 않는 증거 규율(BRIEF 31행)상 fail-closed 유지가 맞다 |
| TUI 탐지(OSC 타이틀/프로세스 스캔) | **[제외]** | — | 대화형 터미널 전용 |

---

## 3. 세션 시작 / resume / fork / 신규 — 그리고 세션 ID 영속

### 3.1 재개 argv는 "에이전트별 표" 하나에 모은다

`src/shared/agent-resume-argv.ts:4-78`이 `ResumableTuiAgent` 26종의 재개 argv를 switch 하나로 정의한다(`RESUMABLE_TUI_AGENTS`는 `src/shared/agent-session-resume.ts:6-33`). 예: `codex → ['codex','resume',id]`, `claude → ['claude','--resume',id]`, `cursor → ['cursor-agent','--resume',id]`, `pi → ['pi','--session',transcriptPath]`, `kiro → ['kiro-cli','--resume-id',id]`, `copilot → ['copilot',`--resume=${id}`]`. **타입 계약이 핵심**이다 — 세션 메타데이터의 `key`가 `session_id`인지 `conversation_id`인지 검사해서 아니면 `null`을 돌려준다(11-77행). 즉 "ID 종류가 틀린 재개"는 실행 전에 차단된다.

### 3.2 fork는 별도 표 + "채팅 경유 fork" 이중화

```ts
// src/shared/agent-resume-argv.ts:80-95
const FORK_ARGV = {
  claude: ({ id }) => ['claude', '--resume', id, '--fork-session'],
  codex: ({ id }) => ['codex', 'fork', id],
  grok: ({ id }) => ['grok', '--resume', id, '--fork-session'],
  pi: ({ transcriptPath }) => (transcriptPath ? ['pi', '--fork', transcriptPath] : null)
}
const FORKS_THROUGH_CHAT: ReadonlySet<string> = new Set(['opencode','opencode2','omp'])
```

주석이 이유를 명시한다: `/** CLI fork creates a new conversation ID so chat and terminal cannot write the same one. */`(80행), 그리고 `/** OpenCode2.0.14/OMP17.0.5 fork through ACP session/fork then resume the copy. ... Refuse without host-advertised fork */`(90행). ACP 쪽 실제 구현은 capability 협상 후 fork 여부를 결정한다: `if (agentCapabilities?.sessionCapabilities?.fork == null) { ... }`(`src/main/acp/acp-conversation-copy.ts:127-128`).

### 3.3 세션 메타데이터 정규화 — provider별 키가 다르다

`src/shared/agent-session-resume.ts`가 정본이다.

- 타입: `{ key: 'session_id' | 'conversation_id', id, transcriptPath? }`(37-44행).
- 안전 검증: 길이 512 이하, 선행 `-` 금지(CLI 옵션 주입 방지), 제어문자 금지(98-122행).
- provider별 추출(200-290행): `claude/codex/qwen/qoder/codebuddy → session_id (+transcript_path)`, `gemini/droid/kimi → session_id`, `antigravity/cursor → conversationId/conversation_id`, `opencode/opencode2/mimo-code → sessionID`, `pi/prime-agent → session_file이 있어야만 인정`, `grok/devin/jcode → sessionId|session_id`, `amp/command-code → null(재개 불가)`.
- 왜 transcriptPath를 같이 저장하는지 코드가 말한다: `/** Prefer hook-reported transcript paths: recent Claude filenames differ from session_id. Claude/Codex still resume by ID; Pi uses session_file as its --session locator. */`(42-43행).
- 비교 함수는 Pi 계열만 파일 동일성까지 요구한다: `agentProviderSessionsEqual`(185-198행).

### 3.4 영속 위치 — SQLite 단일 writer + 리스

세션 레코드는 **`agent-session-journal.db`**(`src/main/native-chat/agent-session-journal/journal-host-database.ts:11-13`)에 저장되고, 스키마는 `AgentSessionRecord`(`src/shared/agent-session-record.ts:115-140`)다. 필드 중 재개/격리에 직결되는 것:

| 필드 | 의미 |
| --- | --- |
| `provider`, `providerHandleChain` | 어떤 에이전트이며 provider 핸들 체인(교체/승계 포함) |
| `accountHome` | 계정 홈 pin — "재개가 다른 로그인으로 표류" 차단(`src/shared/agent-session-account-home.ts:5-11,73-82`) |
| `launchDirectory` | 플로팅 워크스페이스의 초기 cwd pin(`agent-session-record.ts:124-125`) |
| `permissionMode`, `options` | 사용자가 고른 실행 옵션 리플레이 |
| `lease` | `runtimeFence`, `handoffStage`, `reservedSpawnToken`, `leaseDeadlineAt`, `claimStatus`(`released/live/conflicted/reserved`) |

리스 상수는 코드에 박혀 있다: `AGENT_SESSION_LEASE_TTL_MS = 30_000`, `AGENT_SESSION_LEASE_RENEW_INTERVAL_MS = 10_000`(`src/main/runtime/agent-session-record-store.ts:82-83`). store는 "single-writer records/operation ledger in the host chat-journal database"(같은 파일 1행)이고, 상위 빌드가 쓴 DB는 **읽기 전용**으로만 연다(113-115행).

**Claude 재개 지점(resume point)** 은 별도로 영속한다 — 완료 턴의 transcript leaf UUID를 커서로 저장하고(`src/main/claude/claude-structured-resume-point.ts:18-93`), 실패는 턴을 실패시키지 않고 경고 후 다음 턴에 재시도한다(80-90행). `sessionNotRestored` 실패 종류도 정의되어 있다: `/** The agent could not reopen the chat's saved session, so a fresh one without its memory continues it. */`(`src/shared/agent-session-failure.ts:78-79`).

### 3.5 신규 vs 재개 판정 — "스레드를 열어본다"

Codex는 앱서버에 `thread/resume`을 시도하고, **정확히 이 오류일 때만** 새 스레드로 대체한다:

```ts
// src/main/codex/codex-structured-thread-open.ts:22-30
function isCodexNoRolloutError(error: unknown, threadId: string): boolean {
  return isCodexAppServerRequestError(error) && error.method === 'thread/resume' &&
    error.code === -32600 && error.message.endsWith(`no rollout found for thread id ${threadId}`)
}
```

그리고 재개 후 ID가 요청과 다르면 즉시 실패한다(102-104행). `thread/resume`에는 `excludeTurns: true`를 줘서 **과거 턴을 새 출력으로 되돌려받지 않는다**(86행). Grok ACP 경로도 재생된 히스토리를 "검증만 하고 새 출력으로 반환하지 않는다" — TalkAbout이 같은 규율을 이미 구현했다: `# session/load replays history; validate but never return it as new output.`(`talkabout/agent_runtime.py:987-989`). 재개 시 과거 출력이 새 발언으로 오인되는 것을 막는 공통 원칙이다.

### 3.6 TalkAbout 대조 및 개선점

- 재개 argv/세션 메타데이터 **개념 자체가 없다.** `agent_runtime.py:547-574`가 `self.session_id`(메모리)만 보고 `--resume`/`resume`을 붙인다. Claude 신규 ID는 **인스턴스 생성 시 uuid4**로 만들고(`agent_runtime.py:416`) 세션 ID는 `_claude()` 성공 시에만 메모리에 기록된다(947행). 즉 **worker 프로세스가 죽으면 Claude 대화 연속성은 소실**된다.
- 영속은 opt-in + codex 전용: `talkabout/cli.py:227-231`에서 `--persist-provider-sessions`일 때만 `CodexSessionJournal`을 만들고, `codex_sessions.py:58-60`이 `runtime.kind != 'codex'`면 `provider_session_codex_only`로 거부한다.
- 게다가 codex 경로도 사실상 막혀 있다: `_verified_account_subject`는 `agent_runtime.py:397-399`에서 **항상 None**으로 선언되고, `codex_sessions.py:69-70`이 None이면 `account=None`을 저장하며, 같은 epoch 재개는 113-116행에서 `provider_session_identity_unavailable`로 차단된다. 결과적으로 `--persist-provider-sessions`의 실효 경로는 "같은 epoch·다른 turn"이라는 좁은 창뿐이다.
- fork/rewind 없음. 세션 ID를 서버가 발급/보관하지 않으므로 "새 대화로 분기"를 표현할 수단이 없다.

| 항목 | 판정 | 비용 | 위치 |
| --- | --- | --- | --- |
| `provider_session` 레코드(정규화 키/ID/transcriptPath) 도입 | **[채택]** | M | `codex_sessions.py` → `provider_sessions.py` 일반화, `settings` 테이블 재사용 |
| Claude 세션 ID를 결과와 함께 영속(턴 종료 시) | **[채택]** | S | `agent_runtime.py:940-948` + `codex_sessions.py` 스키마 확장 |
| 계정 신원 게이트 → "provider ID + account home pin"으로 교체 | **[채택]** | M | `codex_sessions.py:69-70,105-116` |
| resume-point(leaf UUID) 개념 | **[차용]** | M | Claude 재개 정밀도가 필요해질 때. 현재는 ID 재개로 충분 |
| fork 표(`--fork-session`, `codex fork`) | **[차용]** | S | 방 단위 "새 대화 분기" 기획이 생기면 `agent_runtime.command()` |
| 세션 ID 안전 검증(선행 `-`, 제어문자, 512자) | **[채택]** | S | `codex_sessions.py:24-27` `_identifier()` 강화 |
| sleeping-agent/PTY 세션 복원 전체 | **[제외]** | — | TalkAbout은 서버 worker가 아니라 이벤트 재생으로 복구(BRIEF 27-29행) |

---

## 4. Codex `exec --json` / Claude `stream-json` 파싱과 최종 응답 추출

### 4.1 Claude stream-json — 프레임 종류는 스키마로 고정

`src/main/native-chat/agent-session-wire/claude-stream-json-frame-schema.ts:2-54`가 50종의 프레임 kind를 열거한다(Claude Agent SDK 0.3.251 / Claude Code 2.1.258 기준, 파일 1행 주석). kind는 `['message', type, subtype ?? eventType].join(':')`로 구성된다(`src/main/claude/claude-structured-provider-fallback.ts:31-36`).

**최종 응답 추출**: `message:result` 계열이 턴 종료 신호이고, 성공/실패 판정은 별도 함수가 한다 — `claudeResultOutcome(message, leftToStop)`(같은 파일 60-79행). unknown subtype은 "정산된 결과"로 보지 않고 폴백 행을 만든다(`isSettledClaudeResultKind`, 50-57행). assistant/user 프레임의 content block을 item으로 바꾸는 일은 `src/main/claude/claude-structured-item-translation.ts`가 맡는다(파일 존재는 확인, 세부 규칙은 미확인).

### 4.2 5분류 disposition — "무시해야 할 타입"이 데이터다

`provider-frame-disposition.ts:6-16`이 분류 축을 정의하고, `95-149`가 Claude 50종을, `18-94`가 Codex 72종을 각각 분류한다. 무시 계열의 예:

| 분류 | 의미 | Claude 예 | Codex 예 |
| --- | --- | --- | --- |
| `stream-into-item` | 기존 item에 델타만 붙임 | `content_block_delta`, `message_delta` | `item/agentMessage/delta`, `item/reasoning/textDelta` |
| `status-chrome` | 상태 표시용, 타임라인 본문 아님 | `system:init`, `task_*`, `rate_limit_event` | `turn/started`, `account/updated`, `thread/name/updated` |
| `suppressed-benign` | 아예 버림 | `stream_event:ping`, `hook_started/progress/response` | `rawResponseItem/completed`, `fs/changed`, `account/rateLimits/updated` |
| `error-surface` | 오류 행으로 승격 | `permission_denied`, `model_refusal_no_fallback` | `warning`, `configWarning`, `thread/realtime/error` |
| `timeline-substantive` | 정식 타임라인 행 | `assistant`, `user`, `tool_use_summary` | `item/started`, `item/completed`, `thread/compacted` |

말미 규칙이 중요하다: **분류표에 없는 kind는 버리지 않고 `timeline-substantive`로 폴백**한다(`227-236`). 즉 "모르면 보여주되 조용히 삼키지 않는다". 그리고 폴백 행 생성기는 사람이 읽을 문장을 뽑고(`message/text/warning/detail/description/reason/error` 키 순서 — `unhandled-provider-frame.ts:39-79`), payload는 크기 제한해 raw 프레임을 붙인다(166-167, 206-216행). 반면 `stream-into-item/status-chrome/suppressed-benign`은 `null`을 돌려 행을 만들지 않는다(159-165행).

### 4.3 Codex: Orca는 `exec --json` 파서가 없다 (확인 결과)

`HEAD 51dcef8`의 `src/main` 전체에서 `thread.started`/`item.completed`/`turn.completed` 같은 **exec JSONL 타입 문자열을 파싱하는 코드는 검색되지 않았다**(grep 결과 0건; 매치는 전부 무관한 `turn.completedAt` 필드). Orca의 Codex 경로는 `codex app-server` JSON-RPC이며(`codex-structured-thread-open.ts:64-97`), 이벤트 이름은 `codex-app-server-notification-schema.ts:2-75`(72종)로 스키마화되어 있고, disposition 표는 그 72종을 전부 분류한다.

한편 Orca의 **비대화형 1회 호출**은 `codex exec --ephemeral --skip-git-repo-check -s read-only --model <m>`에 프롬프트를 stdin으로 주고(`src/shared/commit-message-agent-specs-primary.ts:83-95`), Claude는 `-p --output-format text --permission-mode plan`(+`--strict-mcp-config`, `--settings '{"disableAllHooks":true}'`)을 쓴다(같은 파일 32-48행, `src/shared/claude-background-run-isolation.ts:5-12`). **즉 Orca는 1회 호출에서 이벤트 스트림을 아예 소비하지 않는다** — 텍스트만 받는다. 그러므로 "exec JSONL 파싱"은 TalkAbout이 더 앞서 있고, Orca에서 배울 것은 위 분류표뿐이다.

### 4.4 TalkAbout 대조

TalkAbout의 Codex exec 파서는 엄격한 화이트리스트 + 도구 금지 정책이다:

```python
# talkabout/agent_runtime.py:760-795 (요약)
if kind == 'thread.started': ... 세션 일치 검사 ...
elif kind == 'turn.started': ... started = True
elif kind in {'item.started','item.updated','item.completed'}:
    if item.get('type') == 'error': continue              # advisory error는 무시
    if item.get('type') not in {'agent_message','reasoning','plan'}: raise tool_policy_violation
    if kind == 'item.completed' and type == 'agent_message': completed[id] = text
elif kind == 'turn.completed':
    if not started or not completed or event.get('error'): raise invalid_output
    if status != 'completed': raise runtime_failed
    return self._bounded_parts(completed)
elif kind in {'error','turn.failed'}: raise runtime_failed
else: raise runtime_protocol_mismatch
```

Claude는 `system:init`/`assistant`/`stream_event`/`result`/`rate_limit_event`만 허용하고 나머지는 `runtime_protocol_mismatch`(`agent_runtime.py:914-950`). 여기서 **3가지 구체적 개선**이 나온다.

1. `item.updated`의 `agent_message` 중간 텍스트를 버리고 `item.completed`만 쓴다(782-784행). TalkAbout의 app-server 경로는 이미 델타를 누적하고 완료 시 완료 item으로 대체한다(`agent_runtime.py:844-851`). 같은 방식을 `exec` 경로에도 적용하면 "완료 item 없이 turn.completed만 오는" CLI 변형에서 결과를 잃지 않는다.
2. `turn.failed`의 payload를 통째로 버리고 코드만 남긴다(792-793행). Orca는 `turn.failed`/`error`에서 오류 타입 문자열을 읽어 `usageLimitExceeded`/`rateLimitExceeded`를 구분한다(`codex-usage-limits.ts:14-15,48-63`). TalkAbout도 **경계된 allowlist로 오류 타입만** 추출하면 usage-limit 감지가 공짜로 생긴다.
3. Claude `result`의 `permission_denials`는 검사하지만(941-942행) `api_error`/`terminal_reason`/`api_error_status`는 읽지 않는다. Orca는 이 셋으로 quota/other/untyped를 판정한다(`claude-usage-limits.ts:58-97`).

| 항목 | 판정 | 비용 | 위치 |
| --- | --- | --- | --- |
| 5분류 disposition 표(무시/델타/상태/억제/오류) | **[채택]** | M | `agent_runtime.py`에 `_FRAME_DISPOSITION` dict, 파서 3종 공용 |
| 미지 kind → `timeline-substantive` 폴백 + 사람이 읽을 문장 추출 | **[차용]** | S | `agent_runtime.py:794-795`, `949-950` |
| 오류 payload에서 타입 allowlist 추출(`usageLimitExceeded` 등) | **[채택]** | S | `agent_runtime.py:792-793`, `940-944` |
| exec JSONL 델타 누적 후 완료 텍스트 대체 | **[채택]** | S | `agent_runtime.py:770-784` |
| Codex app-server 72종 이벤트 카탈로그 이식 | **[제외]** | L | TalkAbout의 app-server 경로는 실험적 opt-in이며 `exec-json`이 기본(`agent_runtime.py:5-9`) |

---

## 5. 도구 승인/거부(approval) 상호작용

### 5.1 Orca의 두 모델: provider 강제 vs Orca 응답

능력값이 주체를 정한다: `approvalEnforcement: 'provider' | 'orca'`, 주석은 "provider settings still control when requests are emitted, so this is not sandboxing"(`src/shared/agent-session-capabilities.ts:16-17`). Codex는 provider 강제(`approvalPolicy`가 thread에 실림)이고, 정책은 **3값 표**로 고정된다:

```ts
// src/main/codex/codex-structured-permission-policy.ts:16-37
const FULL_ACCESS_POLICY = { approvalPolicy: 'never', sandbox: 'danger-full-access', approvalsReviewer: 'user' }
const ASK_POLICY = { approvalPolicy: 'on-request', sandbox: 'workspace-write', approvalsReviewer: 'user' }
const AUTO_POLICY = { ...ASK_POLICY, approvalsReviewer: 'auto_review' }
// Explicit fields prevent inheriting permissive config or previous thread policies.
```

Claude는 SDK 콜백이 정본이다 — `canUseTool`이 요청을 **durable prompt로 등록**하고, 등록 실패(모델이 모를 형태)면 deny, provider 취소면 **응답을 쓰지 않는다**:

```ts
// src/main/claude/claude-structured-inbound-control.ts:15-21,47-61
function denySafeResult(toolUseId) { return { behavior: 'deny', message: 'Orca could not decode this permission request.', ... } }
...
if (!prompt) { settle(denySafeResult(options.toolUseID)); return }
cancel = () => { if (deps.prompts.forgetIfPending(prompt)) { ... settle(null) } } // Null = no response written
```

### 5.2 blocking request 전수 응답표 (Codex app-server)

`src/main/codex/codex-server-request-disposition.ts:28-77`은 **앱서버가 보내는 모든 요청**에 안전한 답을 강제한다: 모델링 가능하면 prompt, 아니면 switch로 명시 응답, `default`는 `-32000 Orca rejected unrecognized blocking request`. 개별 응답 예: `mcpServer/elicitation/request → {action:'decline'}`, `item/permissions/requestApproval → {permissions:{},scope:'turn'}`, `item/tool/call → {contentItems:[],success:false}`, legacy `applyPatchApproval/execCommandApproval → {decision:'abort'}`(39-75행). MCP 승인 폼 판정도 Codex TUI와 같은 규칙을 쓴다(`src/main/codex/codex-mcp-tool-approval.ts:4-35`).

### 5.3 TalkAbout은 어떻게 회피하나 (이미 잘함)

- Codex: `--sandbox read-only` + `--ignore-user-config --strict-config` + `-c approval_policy="never"` + 기능 플래그 전면 비활성(`agent_runtime.py:550-565`). 즉 **승인 프롬프트를 만나기 전에 도구 자체를 끈다.**
- Claude: `--tools '' --restricted --safe-mode --strict-mcp-config --mcp-config '{"mcpServers":{}}' --setting-sources '' --settings '{"disableAllHooks":true}' --permission-mode dontAsk`(`agent_runtime.py:566-572`). 그럼에도 blocking 요청이 오면 즉시 거부하고 실패시킨다(`910-913`).
- Grok: `--tools '' --deny '*' --sandbox read-only --no-plan --no-subagents --disable-web-search`(`573-574`) + ACP `session/request_permission → cancelled`(`660-661`).
- Codex app-server(opt-in): `_deny_request()`가 4개 메서드만 매핑하고 나머지는 `-32601`(`657-671`).

### 5.4 차이와 개선

TalkAbout은 **왕복 대화가 없으므로 승인 UX를 이식할 수 없다**(정답). 다만 Orca의 "blocking request 전수 응답표"는 그대로 쓸 수 있다 — TalkAbout `_deny_request`는 `item/tool/call`, `account/chatgptAuthTokens/refresh`, `attestation/generate`, legacy `applyPatchApproval`/`execCommandApproval`을 알지 못해 generic error로 떨어진다. Orca 쪽 분기는 10개 + `default`(unknown → `-32000`)이다(`codex-server-request-disposition.ts:39-75`). 또한 Claude 거부 응답은 `subtype:'error'` 하나뿐인데, Orca처럼 **요청 종류별로** `can_use_tool` → deny / user dialog → cancelled / 그 외 → error로 나누면 SDK 쪽 재시도/로그 해석이 정확해진다.

| 항목 | 판정 | 비용 | 위치 |
| --- | --- | --- | --- |
| Codex blocking request 응답표 확장(10종+default) | **[채택]** | S | `agent_runtime.py:657-671` |
| Claude 거부 응답 종류별 분기 | **[차용]** | S | `agent_runtime.py:910-913` |
| 승인을 durable prompt로 되묻는 UI | **[제외]** | L | TalkAbout은 1회 호출·비대화형. 승인 왕복은 task 재시도/실패로 이미 표현됨 |
| `approvalPolicy/sandbox/approvalsReviewer` 3값 표 | **[채택]** | S | `agent_runtime.py:550-554` — 현재 `read-only`+`never` 조합만 있고 "full-access" 개념이 없다(의도된 제한) |
| `approvalEnforcement` 능력을 provider metadata에 노출 | **[차용]** | S | `providers.py` capabilities |

---

## 6. 로그인·계정 격리 (HOME / `CLAUDE_CONFIG_DIR` / `CODEX_HOME`)

### 6.1 Orca의 원칙 4개

1. **상속된 계정 pin은 먼저 지운다.** `withoutInheritedClaudeConfigDir()`가 부모 프로세스의 `CLAUDE_CONFIG_DIR`(Windows는 대소문자 무시)을 제거하고(`src/main/claude/claude-config-dir-pin.ts:4-19`), 성공한 세션/계정 홈만 다시 pin한다.
2. **기본 홈과 같은 경로면 pin하지 않는다.** 다른 키체인 신원이 되어버리기 때문: `// Pin only nondefault homes: even an explicit default changes the CLI's Keychain identity.`(`claude-config-dir-pin.ts:31-44`).
3. **자격증명 계열 환경변수는 통째로 제거**하고, 헤더 값이 인증처럼 보이면 그것도 지운다: `ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN/CLAUDE_CODE_OAUTH_TOKEN/AWS_BEARER_TOKEN_BEDROCK` + `ANTHROPIC_CUSTOM_HEADERS` 정규식(`src/main/claude-accounts/environment.ts:8-13,38-51,87-92`). 자식 env 빌더는 여기에 "다른 채팅의 scratch 디렉터리 상속 금지", "자식 세션 스탬프 제거"까지 더한다(`src/main/claude/claude-child-process-environment.ts:63-83`).
4. **여러 디렉터리 계정은 태그된 locator로** 표현한다 — OpenCode처럼 홈이 하나가 아닌 에이전트를 위해 `{kind:'opencode', locator:{kind:'managed', managedProfileId}}`(`src/shared/agent-session-account-home.ts:13-25`). 계정 홈 종류가 에이전트와 다르면 launch에서 거부한다(73-82행).

Codex 쪽은 managed home을 앱 데이터 아래에 두고(`resolveOrcaManagedCodexHomePath → <userData>/codex-runtime-home/home`), 시스템 홈의 `skills/hooks/plugins/prompts/AGENTS.md`를 **심볼릭 링크(실패 시 소유 표식이 붙은 복사)**로 미러링한다(`src/main/codex/codex-home-paths.ts:25-49,83-181`). Windows 심볼릭 실패·WSL UNC 등 실패 모드를 각각 처리하고, 사용자 소유 리소스는 지우지 않는다(135-157, 256-303행). 계정 전환·재개 시에는 "읽을 수 없는 홈"을 **거부**해 다른 계정 대화를 조용히 열지 않는다: `/** Refuse unreadable homes so resume cannot silently select another account's alias */`(`src/main/codex-accounts/runtime-home-service-home-routing.ts:51-66`).

계정 변수는 실행 계층에서 **검증 스키마**로 통과시킨다: `CLAUDE_CONFIG_DIR`, 주입용 `CLAUDE_*`, `CODEX_HOME`만 허용하고, 절대경로·`..` 없음·길이 4096 이하·주입 변수는 `CLAUDE_CONFIG_DIR`과 동일해야 한다. 주석이 정책을 못박는다: `/** Why a refusal, not a drop: a dropped home would start the agent under another account. */`(`src/shared/agent-launch-account-env.ts:35-52`).

### 6.2 TalkAbout 대조 — **격리 수준이 제공자마다 다르다**

`runtime_environment()`는 제공자별 자격증명만 화이트리스트한다(좋음):

```python
# talkabout/agent_runtime.py:106-115
common = {'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','HOME','USERPROFILE','APPDATA','LOCALAPPDATA','TEMP','TMP','LANG','LC_ALL'}
provider = {'codex': {'OPENAI_API_KEY','CODEX_API_KEY','CODEX_HOME'},
            'claude': {'ANTHROPIC_API_KEY'}, 'grok': {'XAI_API_KEY'}}[kind]
```

문제:
- **Claude에는 `CLAUDE_CONFIG_DIR`가 아예 없다**(110-111행). 따라서 TalkAbout의 Claude는 항상 사용자의 실제 `~/.claude`(=실제 로그인/설정/플러그인/훅)를 읽는다. `--setting-sources ''`/`disableAllHooks`로 일부만 막을 뿐, 계정 격리·다중 계정은 불가능하다.
- **Codex는 `CODEX_HOME`을 상속만 하고 pin하지 않는다.** 격리 프로필을 쓰려면 운영자가 셸 환경을 오염시켜야 한다.
- 반대로 **Grok은 완전 격리**다 — HOME/USERPROFILE/APPDATA/LOCALAPPDATA/XDG_*/TEMP/TMP/TMPDIR/GROK_HOME을 프로필 디렉터리로 재지정하고, `GROK_DISABLE_AUTOUPDATER=1`, 타 에이전트 자원(`GROK_CURSOR_*`, `GROK_CLAUDE_*`) 차단, 기능 플래그 0(`agent_runtime.py:595-644`). 주석에 이유까지 있다(611-614행).
  → **같은 파일 안에서 Grok만 4번 원칙을 지키고 Claude/Codex는 1~2개만 지킨다.** 이 비대칭이 가장 값싼 개선 지점이다.
- `adapters.py`의 legacy 경로는 더 약하다: `child_environment()`가 `HOME`, `USERPROFILE`, `CODEX_HOME`을 그대로 넘기고 `CLAUDE_CONFIG_DIR`는 허용목록에 없다(`talkabout/adapters.py:19-27`). 여기에 `--no-session-persistence`를 쓰기 때문에 세션 누수는 없지만 계정 격리는 없다(80-82행).

| 항목 | 판정 | 비용 | 위치 |
| --- | --- | --- | --- |
| 제공자별 `account_home_env`(CLAUDE_CONFIG_DIR/CODEX_HOME/GROK_HOME) pin | **[채택]** | M | `agent_runtime.py:106-115,595-644` → `provider_environment(kind, profile)`로 일반화 |
| 상속된 계정 pin 제거 후 성공 홈만 재pin | **[채택]** | S | 위와 동일 |
| 기본 홈과 동일하면 pin 생략(키체인 신원 보존) | **[차용]** | S | 계정 기능을 붙일 때 |
| 자격증명 env 제거 목록(헤더 포함) | **[채택]** | S | `agent_runtime.py:640-643`, `adapters.py:19-27` |
| 계정 경로 검증(절대경로/`..`/길이) + "drop이 아니라 refuse" | **[채택]** | S | `codex_sessions.py`의 scope 기록 + `cli.py` 인자 검증 |
| 다중 계정 전환 UI/프로필 디렉터리 체계 전체 | **[차용]** | L | TalkAbout은 계정 scope을 서버 권한과 묶어야 함(`docs/PERMISSIONS.md`) — provider 로컬 계정과 서버 credential scope을 혼동하면 안 됨 |
| Codex managed home 리소스 미러링(symlink/copy) | **[제외]** | — | 데스크톱 앱 UX |

---

## 7. workspace 격리·cwd 지정·위험 환경변수 상속 차단

### 7.1 Orca

- **cwd는 레코드에 pin된다.** 플로팅 워크스페이스는 `launchDirectory`를 pin하고, pin된 폴더가 사라졌으면 **다른 곳으로 대체 실행하지 않고** pre-spawn 오류로 끝낸다: `/** Resuming a missing floating launch directory elsewhere would change the conversation. */`(`src/main/runtime/agent-session-launch-directory.ts:9-17,39-49`). pin 실패는 launch를 막지 않는다(50-58행).
- **존재/디렉터리 검증은 별도 계층**이고, WSL UNC·죽은 공유는 동시성 레인으로 격리한다(`src/main/providers/working-directory-validation.ts:8-43,90-156`). 오류 메시지에 cwd·platform·버전 진단을 붙인다(58-88행).
- **root-like cwd 거부**: `isRootLikePath()`(posix/win32 양쪽 dirname 비교), 그리고 "안전한 홈이 없으면 실행 거부"(`src/main/providers/pty-default-cwd.ts:12-32`, `src/main/providers/pty-path-safety.ts:4-12`).
- **자식 환경 밀봉**: 구조화 세션 자식에서 부모의 pane 정체성(`ORCA_PANE_KEY`, `ORCA_TAB_ID`, `ORCA_WORKTREE_ID`, launch token, hook runtime env)을 **삭제 목록**으로 제거하고, 세션 ID·구조화 마커·spawn token만 새로 넣는다(`src/main/runtime/structured-session-child-env.ts:22-31,42-60`). "에이전트 파트는 cwd/sealed env를 설정할 수 없다"는 합성기 규약(§1.1)이 이를 구조적으로 강제한다.
- **PTY 환경 위생**: 앱 전용 env 제거(`removeAppImageRuntimeEnv`, `stripPiProcessOwnerEnv`, `removeInheritedNoColor` 등) 후 `envToDelete`를 마지막에 재적용한다 — "앱 레벨 빌더가 삭제 키를 되살릴 수 있으므로 마지막에 강제"(`src/main/providers/local-pty-spawn-environment.ts:53-100`).
- **provider가 스크럽하는 이름 회피**: Claude/DSH가 `KEY|TOKEN|SECRET|PASSWORD` 패턴 env를 자체 스크럽하므로, Orca는 pane 정체성을 `ORCA_AGENT_PANE`/`ORCA_AGENT_LAUNCH` alias로도 내보내고 POSIX/Windows 훅 프롤로그에서 복원한다(`src/shared/agent-hook-scrub-safe-env.ts:1-39`). **상속된 alias가 부모 정체성을 주장하지 못하도록 source가 없으면 alias를 삭제**한다(14-24행).

### 7.2 TalkAbout 대조

- workspace: `tempfile.TemporaryDirectory(prefix='talkabout-agent-')`(`agent_runtime.py:1109-1112`), cwd는 그 임시 디렉터리(1136행). Grok만 빈 `.git` 레이아웃을 실제로 만들어 RepoDirChain 발견을 자기 workspace에 가둔다(`576-593`).
- **개선점**: ① cwd가 실행 중 삭제되어도 감지하지 않는다(검증 없음) ② 임시 디렉터리가 턴 사이 유지되지만 **세션/재개와 연결되지 않는다**(재개하면 대화는 이어져도 cwd가 달라짐) ③ `adapters.py`는 매 호출 `TemporaryDirectory`를 만들고 바로 버린다(36-37행) — codex `exec --ephemeral`과는 맞지만 재개 불가.
- env: `runtime_environment()`는 제공자별 화이트리스트라 위험변수 상속 차단은 **이미 강하다**(`PWD`/`OLDPWD`/`SHELL`/`NODE_OPTIONS` 등이 목록에 없어 결과적으로 차단됨). 없는 것은 (a) 계정 홈 pin, (b) provider 스크럽 이름 회피(TalkAbout은 hook을 쓰지 않으므로 불필요), (c) 부모 프로세스가 이미 오염된 `HOME`을 갖고 있을 때의 교정, (d) 자식 env를 "마지막에 다시 강제"하는 단계.

| 항목 | 판정 | 비용 | 위치 |
| --- | --- | --- | --- |
| cwd 존재/디렉터리 검증 + 삭제 시 명시적 코드(`runtime_cwd_missing`) | **[채택]** | S | `agent_runtime.py:1109-1112` |
| 세션 시작 시 cwd를 세션 레코드에 pin(재개 drift 방지) | **[채택]** | S | `codex_sessions.py` scope에 `workspace_digest` 추가 |
| root-like cwd/홈 거부 | **[채택]** | S | `cli.py` 인자 검증, `agent_runtime.py` |
| env 삭제목록을 앱 빌더 이후 마지막에 재적용 | **[차용]** | S | `agent_runtime.py:_environment()` 반환 직전 |
| PTY/sealed child pane 정체성 모델 | **[제외]** | — | TalkAbout은 pane이 없다(서버 worker) |
| provider 스크럽 이름 alias | **[제외]** | — | agent hook 미사용 |

---

## 8. usage / rate-limit 감지·표시

### 8.1 실행 중 관측(스트림) — "세션 한도 소진을 어떻게 알아채나"

**Claude** — `ClaudeUsageLimits` 클래스가 두 소스를 합친다(`src/main/claude/claude-usage-limits.ts:47-153`):

```ts
// 52-56: rate_limit_event → rate_limit_info 기록
observe(message) { if (message.type === 'rate_limit_event') this.record(claudeRecord(message.rate_limit_info)) }
// 58-76: assistant.api_error === 'usage_limit_reached' → quota, 그 외 문자열 → other, 없음 → untyped
const cause = typed === 'usage_limit_reached' ? 'quota' : typed ? 'other' : 'untyped'
// 99-117: status==='rejected' && !overage 일 때만 한도로 기록, resetsAt(초→ms) 저장
// 119-135: 거부된 한도가 모두 리셋되어야 해제; window가 하나면 이름 붙임, resetsAt은 모두 알 때만 최댓값
```

창 이름 매핑표도 데이터다: `five_hour→session`, `seven_day→weekly`, `seven_day_opus/sonnet/overage_included→weeklyModel`, `overage→credits`(14-21행). 결과는 타입화된 실패 사실(`usageLimitReached` + `{window, resetsAt}` + 사람용 detail)로 바뀐다(137-153행).

**Codex** — `CodexUsageLimits`가 `notification:account/rateLimits/updated`를 스냅샷으로 누적하고(32-46행, `limitId`가 `codex`이거나 없을 때만 — 모델별 버킷 제외 37-38행), 턴 실패 payload의 `error`가 `usageLimitExceeded|rateLimitExceeded`일 때만 한도로 판정한다(14-15, 48-63행). 창 판정은 `usedPercent >= 100` + `windowDurationMins` 폴백(300/10080/43200)으로 하고, **모든 소진 창의 reset을 알 때만** `resetsAt`을 말한다(65-114행). `credits_depleted`는 `credits` 창으로 분류(97-103행).

**표시 계층** — 4개 창(`session/weekly/weeklyModel/credits`) 타입과 "리셋 시각은 읽는 사람의 시계로" 규칙: `/** Render reset in the reader's clock; the host timezone may differ and persisted waits become stale. */`(`src/shared/agent-session-usage-limit-words.ts:16-45`). 31일을 넘는 리셋 시각은 "기다릴 수 있는 시간이 아니므로" 표시에서 뺀다(`src/shared/agent-session-usage-limit.ts:19-30`).

### 8.2 계정 단위 폴링 — 소스·실패 분류·재시도 시각

`src/shared/rate-limit-types.ts`가 계약이다: `RateLimitWindow{usedPercent,windowMinutes,resetsAt,resetDescription}`(1-10행), `UsageRateLimitSource = 'oauth'|'cli'|'web'|'live-session'`(47행), 실패 15종(`missing-credentials/stale-token/refreshable-credentials-without-token/delegated-refresh-required/deferred-by-live-session/keychain-unavailable/missing-scope/no-subscription/network/server/parse/rate-limited/cli-unavailable/usage-unavailable/unknown`, 49-65행), 그리고 `metadata.retryAtMs`(HTTP Retry-After, 80-81행). 실패 분류가 **재시도 정책**을 결정한다:

```ts
// src/main/rate-limits/claude-usage-error-classification.ts:11-34 (요약)
429 → terminal('rate-limited'); 401 → recoverableAuth('stale-token');
403 + 'user:profile' → terminal('missing-scope'); 403 else → recoverableAuth('stale-token');
>=500 → fallbackOnly('server'); 그 외 → terminal('usage-unavailable')
```

`src/main/rate-limits/service/`에는 15개 파일(스케줄·큐·폴링·비활성 계정·결과 정책 등)이 있고, fetcher는 제공자별로 분리되어 있다(`claude-fetcher.ts`, `codex-fetcher.ts`, `gemini-usage-fetcher.ts`, `cursor-fetcher.ts`, `opencode-go-usage-fetcher.ts`, `zcode-usage-fetcher.ts`, …). `live-session` 소스를 별도로 둔 점이 중요하다 — 실행 중 관측이 폴링보다 우선한다.

### 8.3 TalkAbout 대조

- 현재 usage/rate-limit 개념이 **전혀 없다.** `rate_limit_event`는 허용 목록에만 있고 버려진다(`agent_runtime.py:949`).
- Codex `turn.failed`/`error` payload도 버린다(792-793행). Claude `api_error`/`terminal_reason`도 읽지 않는다.
- 서버 쪽에는 attempt 예산·lease 만료가 있지만(BRIEF 28행), "한도 소진"과 "일시 장애"를 구분할 코드가 없어 **즉시 재시도**로 이어질 수 있다.

| 항목 | 판정 | 비용 | 위치 |
| --- | --- | --- | --- |
| Claude `rate_limit_event`/`api_error`/`terminal_reason` 관측 → `usage_limit_reached` 코드 | **[채택]** | M | `agent_runtime.py:899-950` |
| Codex 실패 payload의 오류 타입 allowlist → 동일 코드 | **[채택]** | S | `agent_runtime.py:792-793` |
| `{window, resetsAt}` typed fact + 사람용 문장 | **[채택]** | S | `domain.py`/Event 스키마, `participation.py` 표시 |
| `UsageRateLimitSource`/실패 분류 15종 전체 | **[차용]** | M | `providers.py` + worker 재시도 정책. TalkAbout에는 폴링 소스가 3개(oauth/cli/live-session)면 충분 |
| 계정 단위 usage 폴링 서비스 | **[제외]** | L | 플랫폼이 모델을 보유하지 않는 설계(BRIEF 25행)와 무관한 데스크톱 기능 |
| 5h/7d 게이지 UI | **[제외]** | — | web UI는 방 상태 중심(`docs/UI.md`) |

---

## 9. 실패·행(hang)·무응답 감지와 재시도/폴백

### 9.1 Orca의 실패 어휘는 타입이다

`AGENT_SESSION_FAILURE_KINDS` 41종(`src/shared/agent-session-failure.ts:25-80`) — 이 중 TalkAbout에 그대로 필요한 것:

| kind | 의미 | TalkAbout 현재 코드 |
| --- | --- | --- |
| `notSignedIn` | 로그인 안 됨 | `runtime_auth_required` |
| `cliMissing` | 실행 파일 없음(spawn ENOENT) | `runtime_unavailable` |
| `launchFolderMissing` | cwd 폴더 소실 | (없음) |
| `providerExited` | 시작 중 provider가 죽음 | `runtime_failed` |
| `providerRetrying` | API가 요청을 재시도 중(아직 실패 아님) | (없음 — `system:api_retry` 무시) |
| `usageLimitReached` | 한도 소진 | (없음) |
| `sessionNotRestored` | 저장 세션 재개 실패 → 기억 없는 새 세션으로 계속 | (없음) |
| `cancelUnconfirmed`/`stopRefused` | 취소/중지 증거 불확실 | `runtime_cleanup_unconfirmed` 일부 |

### 9.2 행(hang)/무응답 감지 기법

- **타임아웃에 "어느 단계"를 붙인다**: `AgentSessionRequestTimeout { method, timeoutMs }`를 오류 체인에 실어 "멈춘 메서드와 데드라인"을 사용자에게 말한다(`src/shared/agent-session-request-timeout.ts:3-33`, `src/main/provider-process/provider-request-timeout.ts:9-22`). TalkAbout은 deadline 하나뿐이라 "무엇이 멈췄는지"가 사라진다(`agent_runtime.py:1102`).
- **EOF ≠ 성공**: TalkAbout도 동일 규율을 이미 갖고 있다 — `receive()`가 EOF를 받으면 100ms만 더 종료를 관찰하고 `runtime_outcome_unknown`(`agent_runtime.py:284-300`), `wait_success()`가 종료 후 잔여 이벤트가 있으면 `runtime_protocol_mismatch`(302-313행). `[대조]` TalkAbout 우위.
- **후처리(backpressure) 재시도 큐**: Codex 알림 번역이 저수지(backpressure)면 25ms 뒤 재시도하고, 256건 초과·소유자 사망·번역 거부는 **세션을 강제 종료**한다(`src/main/codex/codex-structured-notification-retry.ts:5-107`). "재시도 가능한 것과 불가능한 것"을 구분하고 overflow 시 무한 대기하지 않는 패턴.
- **종료 증명 사다리**: Claude 연결은 root exit + 자손 트리 exit + reader drain(2초 한도)을 모두 확인해야 close 성공으로 본다(`CLAUDE_READER_DRAIN_AFTER_EXIT_MS = 2_000`, `src/main/claude/claude-stream-json-connection.ts:28-29,277-311`). stderr tail은 `providerStderrForDisplay`로 정제해 **오류 메시지에만** 붙인다(95-110행).
- **stdio 쓰기 큐**: 한 줄씩, capacity 초과 시 즉시 거부, abort 시 이미 스트림에 넘긴 줄은 되돌릴 수 없으므로 실패 처리(`src/main/provider-process/provider-stdio-write-queue.ts:10-129`). TalkAbout도 `_write()`에서 짧은 쓰기(short write) 루프를 별도 스레드로 처리하고 있다(`agent_runtime.py:257-282`) `[대조]` — 다만 TalkAbout은 큐 용량/대기 상한이 없다.

### 9.3 재시도/폴백 전략 — "폴백하지 않는다"가 원칙

- **전송 폴백 없음.** Codex `exec-json ↔ app-server` 자동 전환 없음: TalkAbout도 같은 원칙을 문서화한다(`agent_runtime.py:5-9`, "There is no transport fallback after authentication or model failures").
- **정확한 오류일 때만 1회 재구성**: Claude가 엔터프라이즈 정책으로 `--strict-mcp-config`를 거부하면 **완료된 그 메시지를 확인한 뒤** strict 없이 한 번만 재실행(`src/shared/claude-background-run-isolation.ts:14-34`). 조건은 exit code≠0 + 정확한 문장 포함.
- **재개 실패의 폴백은 "새 세션 + 경고 행"**: `sessionNotRestored`, ACP는 `-32002`+`mayBeUnsaved()`일 때만 승계(supersede), 그 외는 replace + 경고(`src/main/acp/acp-session-reopen-failure.ts:11-29,35-55`).
- **인증 실패와 모델 실패는 재시도 금지**, 그리고 `uncertain` 결과는 재호출 금지 — TalkAbout `codex_sessions.py:160-169`가 같은 규율을 이미 한국어 안내문으로 갖고 있다 `[대조]`.

| 항목 | 판정 | 비용 | 위치 |
| --- | --- | --- | --- |
| 실패 코드 ↔ `AGENT_SESSION_FAILURE_KINDS` 매핑표(41→12 축소) | **[채택]** | S | `agent_runtime.py`의 `AdapterError` 코드, `domain.py` Event |
| 타임아웃에 단계명 추가(`{stage, timeout}`) | **[채택]** | S | `agent_runtime.py:1102`, `_JSONLTransport.check()` |
| 번역/파싱 backpressure 큐(용량·재시도 간격·overflow fail) | **[차용]** | M | `worker.py`/`connector.py` — TalkAbout은 이벤트 수신 루프가 pull worker에 있음 |
| 정확한 오류 문장 기반 1회 재구성 | **[차용]** | S | `agent_runtime._ensure_preflight` / `command()` |
| 종료 증명 사다리(root+tree+reader drain) | **[대조]** | — | `processes.py`(Windows Job)가 이미 강함 — task-3 소관 |
| 프로세스 재부착/sleeping 세션 | **[제외]** | — | 서버 worker 모델 아님 |

---

## 10. TalkAbout 4개 모듈 구체 개선점 (파일:라인)

### 10.1 `talkabout/providers.py` — 능력·계정·탐지 메타데이터

| # | 근거(현재 코드) | 문제 | 개선 | 태그/비용 |
| --- | --- | --- | --- | --- |
| P1 | `providers.py:19-42` `Provider` 필드 | 능력이 데이터가 아니라 `probe()` if-chain(`agent_runtime.py:433-437`)에 있음 | `capabilities: dict`(예: `{"tools":"forbidden","session_resume":True,"stream":True,"usage_limits":True,"account_home":"CLAUDE_CONFIG_DIR"}`) 추가 | **[채택]** S |
| P2 | `providers.py:24` `status: str` 자유 문자열 | "등록됨/연결됨/실제 발언" 구분이 문자열 하나에 뭉개짐(BRIEF 31행 위반 위험) | `status`를 `registered/implemented/synthetic_verified/live_verified` enum으로 분리하고 `evidence` 필드 | **[채택]** S |
| P3 | `providers.py:45-110` registry | 그록만 `gated`, 나머지 `ready`인데 근거가 파일에 없음 | `evidence_ref`(docs/validation 경로) 필수 필드 + 테스트로 누락 차단 | **[채택]** S |
| P4 | `providers.py:45-110` | 실행파일 alias/최소버전/필수동반명령/플래그 프로필 없음 | Orca `TuiAgentConfig`(`tui-agent-config-types.ts:24-77`)의 비대화형 부분집합(alias, min_version, required_flags) 추가 | **[차용]** S |
| P5 | `providers.py:112-146` | alias 중복/미정의 검증 없음 | import 시 `validate_registry()` — alias 대상 존재, transport별 필수필드, capability 키 오타 검사 | **[채택]** S |

### 10.2 `talkabout/adapters.py` — 교차 자격증명 누출과 이중 명령 정의

| # | 근거 | 문제 | 개선 | 태그/비용 |
| --- | --- | --- | --- | --- |
| A1 | `adapters.py:21-27` | `allowed` 집합에 `OPENAI_API_KEY`와 `ANTHROPIC_API_KEY`가 **동시에** 있어 Codex 프로세스가 Anthropic 키를, Claude 프로세스가 OpenAI 키를 받는다 | `agent_runtime.runtime_environment()`처럼 제공자별 집합으로 교체(단일 함수로 통합) | **[채택]** S (보안) |
| A2 | `adapters.py:21-27` + `agent_runtime.py:108-111` | `CLAUDE_CONFIG_DIR`가 어느 쪽에도 없어 Claude는 항상 실제 홈 사용 | `CLAUDE_CONFIG_DIR`/`CODEX_HOME` pin 지원 | **[채택]** M |
| A3 | `adapters.py:78-90` vs `agent_runtime.py:547-574` | **명령 빌더가 2개**이고 내용이 다르다: adapters는 `--output-format json`(단발), `approval_policy` 없음, `--no-session-persistence`; runtime은 `stream-json`, `approval_policy="never"`, 세션 resume 지원. 같은 `codex`/`claude`에 대해 안전 프로필이 갈라진다 | `command_profile(kind, mode)` 단일 함수로 통합. Orca의 "spec 하나 + buildArgs" 패턴(`src/shared/commit-message-agent-spec.ts:43-69`) 참고 | **[채택]** M (정합성) |
| A4 | `adapters.py:80-82` | `--no-session-persistence`라 `--session-id`/`--resume`이 없음(의도) | 유지하되, 이 경로가 "재개 불가"임을 Provider capability로 노출 | **[차용]** S |
| A5 | `adapters.py:118-124` | allowlist 밖 타입을 `ValueError`로 **전부** 실패시켜 "무시 가능"과 "정책 위반"이 구분 안 됨 | 3분기: 소비 / 무시(progress·status) / `tool_policy_violation`(도구) | **[채택]** S |
| A6 | `adapters.py:56` | exit code만 보고 `runtime_failed`(payload 미보존) | stderr는 이미 버리지만 exit code+이벤트 카운트를 진단 facts로 남김(Orca `providerStderrForDisplay` 경계 준수) | **[차용]** S |

### 10.3 `talkabout/codex_sessions.py` — codex 전용 + 도달 불가능한 게이트

| # | 근거 | 문제 | 개선 | 태그/비용 |
| --- | --- | --- | --- | --- |
| C1 | `codex_sessions.py:58-60` | `runtime.kind != 'codex'` → `provider_session_codex_only`. Claude/Grok 재개 영속 불가 | `provider_session`으로 일반화(provider별 id 키만 다름). Orca `extractAgentProviderSession` 표(`agent-session-resume.ts:200-290`) 참조 | **[채택]** M |
| C2 | `codex_sessions.py:69-70,113-116` + `agent_runtime.py:397-399` | 검증된 계정 subject가 **항상 None**이라 같은 epoch 재개가 영구 차단(`provider_session_identity_unavailable`) | 게이트를 "계정 신원"이 아니라 "provider가 발급한 세션 ID + account home pin + profile digest"로 재정의(Orca `agentProviderSessionsEqual`/`accountHome` 방식). 계정 신원은 표시용으로만 | **[채택]** M (핵심) |
| C3 | `codex_sessions.py:61-63` | scope이 room/generation/membership/turn에 묶여 있는데 **cwd/workspace가 없다** | `workspace_digest` 또는 `launch_cwd`를 scope에 추가(Orca `launchDirectory` pin 대응) | **[채택]** S |
| C4 | `codex_sessions.py:117` | checkpoint 반환 키가 `session_id`,`thread_id`뿐 | `transcript_path`(Claude/Pi)와 `provider` 추가 | **[채택]** S |
| C5 | `codex_sessions.py:136-143` | `uncertain()`이 같은 turn일 때만 갱신 → scope 기록 전 크래시는 흔적 없음 | `begin()` 이전에 "turn 시작 예약" 레코드를 남기는 2단 커밋(Orca lease 예약-스폰-확인 3단, `agent-session-record.ts:96-107` 참조) | **[차용]** M |
| C6 | `codex_sessions.py:14-17,145-149` | 오류 코드 문자열 중심, UI 문장은 `recovery_guidance`에만 | 코드→문장 표를 분리(Orca `agent-session-failure-words.ts` 패턴) | **[차용]** S |

### 10.4 `talkabout/agent_runtime.py` — 세션 ID/rate-limit/격리 비대칭

| # | 근거 | 문제 | 개선 | 태그/비용 |
| --- | --- | --- | --- | --- |
| R1 | `agent_runtime.py:416,572,900,947` | Claude 신규 세션 ID가 **인스턴스 메모리 uuid4**로만 존재. 프로세스 재시작 시 대화 소실 | `--session-id`를 영속 레코드에서 가져오고, `result` 수신 즉시 `provider_session`에 저장(Orca `claude-structured-resume-point` 대응) | **[채택]** M (임팩트 최대) |
| R2 | `agent_runtime.py:949` | `rate_limit_event`를 **수신하고 버림** | ClaudeUsageLimits식 관측 + typed fact | **[채택]** S |
| R3 | `agent_runtime.py:940-944` | `result`의 `api_error`/`terminal_reason`/`api_error_status` 미독 | quota 판정 + `usage_limit_reached` 코드 | **[채택]** S |
| R4 | `agent_runtime.py:792-793` | Codex `error`/`turn.failed` payload 미독 | 오류 타입 allowlist 추출 | **[채택]** S |
| R5 | `agent_runtime.py:595-644` vs `106-115` | Grok만 완전 격리, Claude/Codex는 최소 | `provider_isolation(kind)`로 일반화(Grok 블록이 원형) | **[채택]** M |
| R6 | `agent_runtime.py:1109-1112` | cwd 검증/pin 없음 | 세션 레코드에 cwd 저장 + launching 시 존재 검증(`runtime_cwd_missing`) | **[채택]** S |
| R7 | `agent_runtime.py:1102` | deadline 단일 값, 단계 정보 없음 | `{stage, deadline}`로 세분화 | **[채택]** S |
| R8 | `agent_runtime.py:657-671` | Codex blocking request 4종만 매핑 | Orca `codex-server-request-disposition.ts:39-75`의 10종+default | **[채택]** S |
| R9 | `agent_runtime.py:33` `MAX_TEXT=16000` | 최종 응답이 16,000자를 넘으면 `_text()`가 `runtime_invalid_output`으로 실패하고 **부분 결과를 보존하지 않는다**(143-146행) | 실패 코드를 `runtime_output_too_large`로 분리하고, 경계 초과 시 앞부분 보존 + 경고 정책 결정 | **[차용]** S |
| R10 | `agent_runtime.py:284-314` | EOF→outcome_unknown, 잔여 이벤트→protocol_mismatch | 이미 Orca보다 보수적. 유지 | **[대조]** — |

### 10.5 공통: 서버 증거 모델과의 접점

TalkAbout은 "등록됨/연결됨/실제로 발언함"을 분리하는 증거 규율이 있고(BRIEF 31행), Orca는 이 구분이 없다(데스크톱 앱). 따라서 **Orca의 "provider가 말했다" 수준 증거를 TalkAbout의 `worker_reported_*` 등급으로 승격하면 안 된다.** 반대로 Orca에서 가져올 것은 "provider가 스스로 보고한 사실"의 **구조**(세션 ID, usage window, 실패 kind)이지 "검증됨" 라벨이 아니다. 이 경계를 어기면 BRIEF 3번(선별)과 31행을 동시에 위반한다.

---

## 11. Top 5 실행 제안

| 순위 | 제안 | 근거(Orca) | TalkAbout 작업 위치 | 비용 | 선행조건 | 검증 방법 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | **`provider_session` 일반화 + Claude 세션 ID 영속** (codex 전용/도달불가 게이트 해소) | `agent-session-resume.ts:37-44,200-290`, `agent-session-record.ts:115-140`, `claude-structured-resume-point.ts:18-93` | `codex_sessions.py`(C1,C2,C3,C4) + `agent_runtime.py:416,900,947`(R1) | M | 없음 | 합성 테스트: 런타임 재생성 후 `--resume` argv에 동일 ID 포함; 실제 Claude 1회 재개 시도는 `--approve-external`로 별도 기록 |
| 2 | **실패·한도 어휘 타입화** (`usage_limit_reached`/`not_signed_in`/`cli_missing`/`session_not_restored` + `{window,resetsAt}`) | `agent-session-failure.ts:25-80`, `claude-usage-limits.ts:47-153`, `codex-usage-limits.ts:26-114` | `agent_runtime.py:792-793,940-950`(R2,R3,R4) + `domain.py` Event + `participation.py` 표시 | S | 제안 1의 provider fact 구조 | 합성: `rate_limit_event`/`turn.failed(usageLimitExceeded)` 픽스처 → 코드·문장·`resetsAt` 검증; 즉시 재시도 금지 테스트 |
| 3 | **제공자 격리 일반화**(account home pin, 자격증명 env 분리, cwd 검증) | `agent-launch-account-env.ts:35-52`, `structured-session-child-env.ts:22-60`, `agent-session-launch-directory.ts:9-49` | `agent_runtime.py:106-115,595-644,1109-1112`(A1,A2,R5,R6) | M | 없음 | 합성: 교차 키 미포함, `CLAUDE_CONFIG_DIR`/`CODEX_HOME` pin, cwd 삭제 시 `runtime_cwd_missing` |
| 4 | **이벤트 disposition 표 + blocking request 응답표** | `provider-frame-disposition.ts:6-236`, `codex-server-request-disposition.ts:28-77`, `claude-structured-inbound-control.ts:15-73` | `agent_runtime.py` 파서 3종 + `adapters.py:118-124`(A5,R8) | S~M | 없음 | 픽스처 기반: 미지 kind가 (a) exec에서 `timeline-substantive` 폴백, (b) 1회 호출에서 안전 무시, (c) 도구 프레임은 위반 — 3분기 각각 단위 테스트 |
| 5 | **탐지/버전 메타데이터**: alias·필수동반명령·min_version·flag 캐시, `missing/unknown/too_old` 3분류 | `tui-agent-detection-commands.ts:20-74`, `local-agent-cli-presence.ts:143-225`, `codex-cli-installation.ts:3-22`, `provider-executable-missing.ts:5-23` | `providers.py`(P1,P4,P5) + `agent_runtime.py:425-445,465-542` | S | 없음 | 단위: PATH 픽스처로 alias 탐지, 버전 파싱, `unknown` 보존, preflight fail-closed 유지 확인 |

우선순위 판단: **1번이 가장 크다.** 나머지는 1번이 만든 provider fact 구조 위에서 싸게 얹힌다. 3번은 보안(교차 키)이라 순위와 무관하게 먼저 해도 된다.

---

## 12. 미확인 / 모순 / 주의

1. **미확인**: Orca가 Claude `stream-json`의 **최종 응답 텍스트**를 어떤 우선순위로 고르는지(assistant content vs `result.result`)는 번역기(`claude-structured-item-translation.ts`, 대용량)와 저널 item 생성 경로를 전부 읽지 않아 단정하지 않았다. 확인한 것은 "`message:result` 계열이 턴 종료 신호이고, `result` subtype별 성공/실패 판정이 별도 함수"(`claude-structured-provider-fallback.ts:50-79`)까지다.
2. **부재 확인(전수 아님)**: `HEAD 51dcef8`의 `src/main`에서 `codex exec --json` 타입 문자열(`thread.started`/`item.completed`/`turn.completed`) 파싱 코드를 검색으로 찾지 못했다. grep 기반 부재 주장이므로 "없다"가 아니라 "이 HEAD에서 찾지 못했다"로 읽어야 한다.
3. **모순처럼 보이는 지점**: Orca는 `unverifiable` 버전을 경고 후 허용(`codex-cli-installation-error.ts:28-30`)하지만, 동시에 `provider_verified: False`를 명시하고 안전 플래그 미검증 상태를 별도로 표시한다. TalkAbout은 전자만 보면 더 엄격하고, 후자(증거 등급 표시)는 Orca가 더 세밀하다. **둘은 대체 관계가 아니라 보완 관계다.**
4. **주의(라이선스)**: Orca는 MIT(`LICENSE`). 위 코드블록은 분석 인용이며, 실제 이식 시 저작자 표시·라이선스 고지가 필요하다. 본 보고서의 제안은 대부분 **개념 이식**(capability 표, disposition 분류, 타입화 실패)이라 코드 복사는 최소화할 수 있다.
5. **주의(범위)**: `src/main/persistence/`, `sqlite/`, `pty/`, `hang-watchdog/`는 task-3 범위다. 본 보고서는 provider 계층에서 관찰한 경계(예: 종료 증명 사다리)만 인용했고 그 계층의 설계 판단은 하지 않았다.
6. **주의(TalkAbout 상태)**: Grok CLI는 `providers.py:55-58`에서 `gated`이고 `docs/PROVIDERS.md:22,36-55`에 루프백 S1만 통과로 기록되어 있다. 따라서 6장의 Grok 격리 코드를 "검증된 모범"으로 일반화할 때는 **합성 테스트 근거**임을 유지해야 한다.

---

## 부록 — 인용한 Orca 파일 (상대경로: `orca-analysis/`)

```
src/shared/agent-session-capabilities.ts
src/shared/agent-session-resume.ts
src/shared/agent-resume-argv.ts
src/shared/agent-session-record.ts
src/shared/agent-session-account-home.ts
src/shared/agent-session-failure.ts
src/shared/agent-session-usage-limit.ts
src/shared/agent-session-usage-limit-words.ts
src/shared/agent-session-request-timeout.ts
src/shared/rate-limit-types.ts
src/shared/agent-cli-installation-problem.ts
src/shared/codex-cli-installation.ts
src/shared/agent-launch-account-env.ts
src/shared/agent-hook-scrub-safe-env.ts
src/shared/tui-agent.ts, src/shared/tui-agent-config.ts, src/shared/tui-agent-config-types.ts
src/shared/tui-agent-detection-commands.ts
src/shared/commit-message-agent-spec.ts, src/shared/commit-message-agent-specs-primary.ts
src/shared/claude-background-run-isolation.ts
src/main/native-chat/agent-session-wire/structured-agent-definition.ts
src/main/native-chat/agent-session-wire/structured-agent-registry.ts
src/main/native-chat/agent-session-wire/claude-stream-json-frame-schema.ts
src/main/native-chat/agent-session-wire/provider-frame-disposition.ts
src/main/native-chat/agent-session-wire/unhandled-provider-frame.ts
src/main/native-chat/agent-session-journal/journal-host-database.ts
src/main/runtime/structured-agent-session-runtime.ts
src/main/runtime/structured-agent-launch-composition.ts
src/main/runtime/structured-agent-runtime-registrations.ts
src/main/runtime/structured-session-child-env.ts
src/main/runtime/agent-session-record-store.ts
src/main/runtime/agent-session-launch-directory.ts
src/main/codex/codex-structured-agent-definition.ts
src/main/codex/codex-structured-thread-open.ts
src/main/codex/codex-structured-permission-policy.ts
src/main/codex/codex-structured-notification-retry.ts
src/main/codex/codex-server-request-disposition.ts
src/main/codex/codex-mcp-tool-approval.ts
src/main/codex/codex-usage-limits.ts
src/main/codex/codex-cli-installation-error.ts
src/main/codex/app-server/codex-app-server-notification-schema.ts
src/main/codex/codex-home-paths.ts
src/main/codex-accounts/runtime-home-service-home-routing.ts
src/main/claude/claude-stream-json-connection.ts
src/main/claude/claude-structured-inbound-control.ts
src/main/claude/claude-structured-resume-point.ts
src/main/claude/claude-structured-provider-fallback.ts
src/main/claude/claude-cli-flag-support.ts
src/main/claude/claude-config-dir-pin.ts
src/main/claude/claude-child-process-environment.ts
src/main/claude/claude-structured-item-translation.ts
src/main/claude/claude-usage-limits.ts
src/main/claude-accounts/environment.ts
src/main/claude-accounts/claude-profile-paths.ts
src/main/acp/acp-session-reopen-failure.ts
src/main/acp/acp-conversation-copy.ts
src/main/provider-process/provider-executable-missing.ts
src/main/provider-process/provider-request-timeout.ts
src/main/provider-process/provider-stdio-write-queue.ts
src/main/providers/working-directory-validation.ts
src/main/providers/pty-default-cwd.ts
src/main/providers/pty-path-safety.ts
src/main/providers/local-pty-spawn-environment.ts
src/main/agent-hooks/local-agent-cli-presence.ts
src/main/rate-limits/claude-usage-error-classification.ts
```
