# 04 — Orca 자격증명·권한·스킬 계층 분석

**분석 대상**: `orca-analysis/` (HEAD `51dcef8`, 2026-10-25) · MIT(`LICENSE`)
**범위**: `src/main/{credentials,ai-vault,ai-vault-search,orca-profiles,macos-keychain,managed-data-accounts,plugins,skills,preflight,ports,local-builds}/`,
`src/shared/{plugins,secret-*,secure-file,mcp-*,crash-report-redaction,agent-prompt-injection,ai-vault-search-transport}/`,
`skills/`, `skill-guides/`, `skill-stubs/`, `.claude/`, `AGENTS.md`, `.mcp.json`, `opencode.json`, `orca.yaml`
**대응**: `talkabout/{auth,identity,bounds,tool_broker,mcp,provision,attachments,adapters,agent_runtime,http_runtime}.py`, `docs/PERMISSIONS.md`

표기: `[채택]` 바로 쓸 수 있음 / `[차용]` 개념만 / `[대조]` TalkAbout이 이미 더 잘함 / `[제외]` 맞지 않음.
비용: **S**(반나절 이내) / **M**(1~3일) / **L**(1주 이상).
"미확인"은 읽지 않았거나 확인하지 못한 것이다. 추정으로 메우지 않았다.

---

## 0. 결론 요약

1. Orca의 유출 차단은 **텔레메트리 sink 한 곳에만** 있다. `src/main/observability/redactor.ts:1-2`가 "sink write, bundle collection, server ingest에서 idempotent redaction"을 선언하고 `redactString`/`redactValue`/`redactSpan`을 제공한다. 실제 호출 지점은 `tracer.ts:169`, `bundle.ts:124`, `orcad-remote-log-tail.ts:40`, `codex-rpc-rate-limit-probe.ts:299` **4곳**뿐이다.
2. **모델로 가는 프롬프트를 마스킹하는 코드는 발견하지 못했다.** Orca가 마스킹하는 것은 원격으로 나가는 터미널 출력(자기 capability 토큰 한정, `worker-terminal-redaction.ts:6-8`), 크래시 리포트/진단 텍스트, MCP `env` 표시, 세션검색 relay 응답이다.
3. Orca의 가장 전이 가치가 큰 자산은 **스킬 배포 기계**다. `SKILL.md` 스텁 + 바이너리 내장 정본 가이드 + `ORCA skills get` 재조회(`skills/orchestration/SKILL.md:15-17`)라는 2계층 구조, 그리고 plan/digest/lock/two-rename/백업으로 무너지지 않는 설치기(`plan.ts`, `execute.ts`, `swap.ts`).
4. Orca는 **MCP 설정을 쓰지 않는다**. 4개 후보 파일을 읽어 검사만 하고(`mcp-config.ts:45-70`), "존재하지만 못 읽는 설정은 starter로 덮어쓰기를 절대 허용하지 않는다"(`mcp-config-inspection.ts:90-98`)는 거부 규칙만 코드로 강제한다.
5. TalkAbout은 이미 Orca보다 **엄격한** 지점이 있다: stderr에서 고정 라벨만 노출(`agent_runtime.py:35-97`), 자식 프로세스 env allowlist(`adapters.py:19-27`), fake adapter 강제(`tool_broker.py:97`), write 실패의 intent 격리(`tool_broker.py:232-239`). 따라서 Orca를 "보안 상위호환"으로 복사하면 후퇴한다. 아래 §9의 (a)(b)(c) 세 가지로 한정해야 한다.

---

## 1. 자격증명 저장: 암호화·키 관리·평문 구간

### 1.1 저장 계층 구조

`[대조] Orca는 Electron `safeStorage`(= OS keychain 위임)를 유일한 암호화 백엔드로 쓰고, 파일은 봉투(envelope) 형식이다.`

| 계층 | 파일 | 근거 |
| --- | --- | --- |
| 추상화 | `src/shared/secret-store.ts` | `:4-11` `SecretStore` 4메서드. `:8-10` "weak/no protection을 capability와 분리 보고"(`describeProtectionGap`). `:26-38` globalThis 슬롯 주입, 미초기화 시 throw |
| Electron 래퍼 | `src/main/host/safe-storage-availability.ts` | `:5-8` 키체인 접근 불가가 판명되면 run 전체에서 safeStorage를 **withhold**("headless prompt hangs" 회피). `:21` "use this, never `electron.safeStorage`" |
| 파일 저장 | `src/main/credentials/encrypted-api-key-file-store.ts` | `:42-48` `~/.orca/<fileName>`. `:50-52` `prefix + kind + ':' + base64`. `:74-82` `encrypted`만 decrypt |
| 실제 사용처 | `src/main/opencode/opencode-go-api-key-store.ts:3-8`, `src/main/minimax/minimax-api-key-store.ts:3-8` | 봉투 prefix가 provider별로 다름 (`orca-minimax-api-key:v1:`) |
| macOS keychain | `src/main/macos-keychain/generic-password.ts:78-104` | `security find-generic-password -s -a -w` **읽기 전용**. 3초 타임아웃(`:3`, `:28-40`) |
| 플러그인 vault | `src/main/plugins/plugin-secrets-store.ts:27` | `<pluginData>/secrets.json.enc`, `{version:1, format:'electron-safe-storage-v1', ciphertexts}` |
| 클라우드 세션 | `src/main/orca-profiles/profile-cloud-session-store.ts:107-109` | `<profile>/account-session.json.enc` |

### 1.2 평문 노출 구간 — 코드로 확인된 것

**(A) safeStorage 불가 시 API key 평문 저장.** `encrypted-api-key-file-store.ts:120-137`:
`isEncryptionAvailable()`이 참이면 `encrypted`, 거짓이면 `console.warn(... in plaintext)` 후 `plaintext` 봉투를 쓴다(`:129-136`). 이건 설계된 degrade이고, UI에는 `protection()`이 `'plaintext' | 'sealed'`를 돌려줘 노출된다(`:103-113`). `secret-at-rest-protection.ts:1`의 주석이 정확하다 — "stored bytes, not current sealing capability"(키링을 나중에 설치해도 이미 쓴 평문은 안 바뀐다).

**(B) 같은 제품 안에서 정책이 다르다 — Orca 내부 모순.**
- API key: 평문 fallback 있음(`:129-136`).
- 플러그인 secret: `plugin-secrets-store.ts:11` "SafeStorage vault writes fail without OS encryption; never fall back to plaintext" → `:84-87`에서 거부.
- 클라우드 refresh token: `profile-cloud-session-store.ts:135-149` — 평문은 `allowsPlaintextOrcaCloudSession()`일 때만, 그 외엔 **memory-only**로 떨어뜨리고 재시작 시 소실. 게이트는 `profile-cloud-auth-config.ts:108-114` (`ORCA_CLOUD_ALLOW_PLAINTEXT_SESSION==='1' && NODE_ENV!=='production' && !packaged`).

→ `[차용]` "평문 허용은 env + 비패키지 + 비프로덕션 3중 조건의 명시적 스위치"는 그대로 쓸 만한 패턴이다. 지금 TalkAbout에는 이런 스위치가 없고(플랫폼이 credential을 갖지 않으므로) 필요해질 때 이 형태를 쓰면 된다.

**(C) 프로세스 메모리 캐시.** `:34` `cachedApiKey`, `:126`/`:137` 저장 시 캐시, `:141-143` 반환 시 캐시 우선. `clear()`만 해제(`:173-176`). 파일에서 지워도 메모리는 clear 전까지 남는다. **이상 아님**이지만 문서화 대상.

**(D) 미확인 구간 — 추정하지 않는다.**
- CLI 자식 프로세스에 provider key를 env로 넘기는 경로: `src/shared/git-credential-prompt-env.ts`(111줄)가 존재하나 **읽지 않았다**. Orca가 토큰을 URL에 넣지 않기 위한 장치로 보이나 확인 못 함 → **미확인**.
- `orcad`(헤드리스)와 데스크톱이 서로의 봉투를 복호화하지 못하는 문제: `saved-credential-readability.ts:5-6` 주석이 "Desktop safeStorage and orcad host keys cannot decrypt each other's files in ~/.orca"라고 명시. `envelopeFile()`은 "orcad's plaintext migration"용(`encrypted-api-key-file-store.ts:14-15`)이라고만 되어 있고 마이그레이션 코드는 **미확인**.

### 1.3 "읽을 수 없음 ≠ 없음" — 가장 값진 소규모 규칙

`[채택]` Orca는 credential 파일 재생성 판단에서 "읽기 실패"와 "부재"를 엄격히 구분한다.

- `src/shared/secure-file.ts:195-208` `isUnreadableError`: `EPERM/EACCES/EBUSY/EMFILE/ENFILE/EIO`. 주석: "Unread contents cannot authorize regeneration: rename can replace an unreadable file using parent rights, destroying the credential."
- `encrypted-api-key-file-store.ts:157-170`: `raw === null && isUnreadableError`이면 전용 `ApiKeyFileUnreadableError`(권한 문제)를, 그 외는 "could not be decrypted"로 분리.
- `saved-credential-readability.ts:29-70`: 존재하지만 prefix가 없으면 `'unreadable'`, base64 형식·store health 통과 후 decrypt 실패면 `'sealed-elsewhere'`(다른 Orca 앱이 봉인). `:72-77` `refuseCredentialSealedElsewhere`는 덮어쓰기를 사용자에게 거부하고 그 앱에서 바꾸라고 안내.
- 같은 원칙이 스킬 스캔에도 있다: `src/main/skills/discovery.ts:43-44` "Unavailable roots are unknown, not empty; retain last answers briefly without pinning permanently dead roots."
- 테스트 파일명 자체가 규칙을 박제한다: `src/main/codex/sta-4737-unreadable-is-not-absent.test.ts`.

→ **TalkAbout 부착**: `talkabout/provision.py:51-53`이 모든 실패를 `ValueError('database_identity_or_secret_mismatch_or_unavailable')` 하나로 뭉갠다(§8-⑥). `saved-credential-readability.ts`의 3분류(`unreadable` / `sealed-elsewhere` / 정상)를 그대로 옮길 자리다. 비용 **S**.

---

## 2. 유출 차단: 로그·에러·프롬프트·아티팩트

### 2.1 텔레메트리 sink의 단일 redactor

`[채택]` `src/main/observability/redactor.ts` 하나가 로그/스팬/번들/원격 로그를 모두 담당한다.

| 구성 | 라인 | 내용 |
| --- | --- | --- |
| 라벨 KV | `:6-7` | `(api[-_]?key|token|secret|password|bearer|authorization)` + `[:=]` + 값 |
| provider 패턴 | `:10-29` | `sk-ant-`, `sk-(proj-)?`, `gh[pousr]_`, `AKIA`, `aws_secret_access_key=`, JWT(`eyJ...x3`), `xox[baprsoe]-`, PEM 블록(lazy) |
| URL userinfo | `:32` | `(https?://)([^/@\s]+)@` → `$1[redacted]@` |
| .env 라인 | `:35`, `:135-179` | `^\s*[A-Z_][A-Z0-9_]*\s*=\s*\S.*` → `KEY=[redacted:env-value]`. ASCII 공백 직접 스캔 + 4종 line terminator 캐시(`:114-133`, `:139-173`) |
| 속성 키 차단 | `:42-60` | `env`, `authorization`, `cookie`, `set-cookie`, `headers.authorization` 등 |
| 서버 모드 추가 | `:63-68` | `install_id`, `distinct_id` — "bundles cannot re-identify telemetry history" |
| 구조 순회 | `:181-222` | WeakSet cycle 보호, Date 통과, 미지원 타입 `[unsupported:...]` |
| 순서 | `:92-112` | labeled-kv → provider → userinfo → env-line. 주석 `:100-108`: "provider shapes run first to retain specific tags", "labeled redaction wins over coincidental .env" |

호출 지점 4곳뿐: `src/main/observability/tracer.ts:169`, `src/main/observability/bundle.ts:124`, `src/main/managed-server/install/orcad-remote-log-tail.ts:40`, `src/main/rate-limits/codex-rpc-rate-limit-probe.ts:299`.
idempotent가 명시적 요구사항이다(`:91` "Idempotent so sink, bundle and server passes can safely repeat", 테스트 `redactor.test.ts:276-283`).

**중요한 한계**: 이 redactor는 **텔레메트리 경로에만** 걸린다. `console.warn`/`console.error` 직접 호출은 통과하지 않는다. 예: `encrypted-api-key-file-store.ts:94-97, 129-131, 152-155, 165-168`. (다행히 그 메시지들 자체는 credential 값을 담지 않고 파일명/provider 이름만 담는다 — 읽어서 확인함.)

### 2.2 크래시 리포트/진단 — 구조화된 상한 + 순서

`[채택]` `src/shared/crash-report-redaction.ts`

- 상한이 필드 종류별로 다르다: 일반 240자, 스택 4000자, breadcrumb name 80자, breadcrumbs 30개(`:7-10`). 키 이름이 `*path`로 끝나면 **값을 아예 버리고** `[redacted-path]`(`:60-63`) — "경로는 절대 싣지 않는다"를 값 검사가 아니라 스키마로 강제.
- `sanitizeCrashReportString`(`:37-53`)은 path → credential URL → secret assignment → provider 패턴 순.
- `PATH_PATTERNS`(`:27-35`)가 POSIX/드라이브/UNC/`%APPDATA%` 4계열을 커버하고, 주석 `:26`이 정확하다 — "Quoted paths retain spaces; unquoted paths stop at whitespace to preserve prose."
- VM 레시피 진단: `src/shared/ephemeral-vm-recipe-diagnostics.ts:38-48`이 `stripCredentialsFromMessage` + `orca://pair?code=[redacted]` + `"key":"[redacted]"` 정규식, `:126-141`이 재귀 JSON에서 secret key를 `[redacted]`로. 결과 객체는 `pairingCode`, `identityFile`, `identityAgent`, `proxyCommand`를 경로/값으로 치환(`:50-78`).
- orchestration 호환성 증거: `src/shared/orchestration-compatibility-evidence.ts:88-106` — 중첩 CLI 에러/구조화 로그에서 `SECRET_KEYS` 제거 후 `JSON.stringify`로 grep 검증(`orchestration-compatibility-evidence.test.ts:57`).

### 2.3 "화면/원격으로 나가는 텍스트"의 축 — 가장 좁고 정직한 부분

`[대조]` Orca가 터미널에서 마스킹하는 것은 **자기가 발급한 dispatch capability 토큰 하나뿐**이다.

`src/main/runtime/orchestration/worker-terminal-redaction.ts:6-8`:
`DISPATCH_CAPABILITY_PATTERN = /\bdcap_[A-Za-z0-9_-]{20,}\b/g` → `'[dispatch capability redacted]'`, 경고 `'Dispatch capability tokens were redacted from terminal output.'`.

이 좁은 범위 안에서의 구현은 정교하다:
- wrap된 논리 행을 이어 붙여 토큰 경계를 복원하고(`:45-95`), 매치를 각 행의 편집 구간으로 되돌린다(`:96-108`). wrap 정보가 없으면 `WRAP_COVERAGE_WARNING`으로 **커버리지 부족을 숨기지 않고 경고**(`:9-10`, `:144-148`).
- 적용처: `worker-output.ts:161`, `worker-terminal-archive-read.ts:16-17`, `worker-output-archive.ts:147`.

`[차용]` "마스킹 커버리지가 불완전하면 경고를 함께 반환한다"는 규약. TalkAbout의 `agent_runtime.py:329-353` `diagnostics()`가 이미 같은 정신(카운터·힌트만, 원문 없음)이지만, "마스킹했지만 불완전할 수 있음"을 표현하는 필드가 없다.

**일반 secret 마스킹은 없다.** 검색 범위(`src/**/*.ts`, `redact|mask|sanitize`)에서 터미널/파일 내용에 대한 범용 secret 스크럽은 `worker-terminal-redaction.ts`의 `dcap_` 외에 발견하지 못했다. → **이건 Orca가 못하는 것이지, 숨기는 것이 아니다.**

### 2.4 전송 등급(transport tier)별 필드 제거

`[채택]` `src/shared/ai-vault-search-transport.ts:5-17`: `transport === 'relay'`이면 `resumeCommand`를 빼고 `source`를 `{presence}`만 남긴다. `:19-33`은 `degradedRoots`의 경로를 `'Source root could not be verified.'`로 치환 — "Native error messages can repeat private paths even after root is removed."

→ **TalkAbout 부착**: `talkabout/mcp.py:131`이 성공 응답을 `json.dumps(data)`로 그대로 모델에 넘긴다. "로컬 경로·실행 정보는 로컬 transport에만, 원격 모델에는 존재 여부만" 규칙을 여기 붙인다. 비용 **S**.

### 2.5 MCP `env` 표시 마스킹

`[채택]` `src/shared/mcp-server-inspection.ts:8-11` — 키 이름 패턴(`api_key|auth|bearer|cookie|credential|password|private_key|secret|session|token`) **또는** 값 패턴(`sk-...|gh[pousr]_...|xox...`)이면 `'••••••••'`(`:84-87`). 값 패턴도 검사하는 점이 TalkAbout에 없는 부분이다(키 이름만 보면 `FOO=hunter2`를 놓친다).

### 2.6 에이전트 hook에 redaction 코드를 **주입**하는 패턴

`[차용]` `src/main/pi/agent-status-input-redaction-source.ts:1-60`은 TS 함수를 **문자열 목록**으로 만들고, `src/main/pi/agent-status-handler-source.ts:3,95`에서 생성되는 hook 스크립트에 스플라이스한다. 내용: 자격증명 경로(`.ssh`, `.mcp-secrets.env`, keyfile)를 참조하는 tool input은 `{redacted:true}`로 대체(`:3-8`, `:55`). 노드/문자 예산(`:12-13`), prototype 검사(`:30-38`), `toJSON` 실행 방지(data descriptor만 복사, `:43-51`).

→ 이건 "에이전트 측 hook 코드에 마스킹 로직을 만들어 넣는" 방식이다. TalkAbout은 에이전트를 띄울 때 hook을 주입하지 않으므로 직접 대응은 없지만, **"경로 참조 = 위험 신호로 간주"** 규칙은 그대로 쓸 수 있다. `[차용]`, 비용 **S**.

### 2.7 프롬프트 마스킹 — **없음**

`검색 근거`: `src/**`에서 `redact|maskSecret|sanitizeForLog|scrubSecret|\[REDACTED\]` 및 `export function (redact|mask|scrub|sanitize)` 전수 grep. 모델 입력(터미널 paste, ACP `mcpServers`/prompt, `commit-message-prompt`, `pr-checks-fix-prompt` 등)에 credential 마스킹을 거는 호출은 **발견하지 못했다**.

Orca에서 프롬프트 텍스트에 대해 하는 유일한 변형은 **제어문자 무력화**다: `src/shared/agent-prompt-injection.ts:170-184` `sanitizeAgentPromptText`가 ESC(`\x1b`)를 `'<ESC>'`로 치환하고, `:186-191`이 bracketed paste 프레임(`\x1b[200~`/`\x1b[201~`)으로 감싼다. 이유는 보안이 아니라 TUI 오작동 방지(Codex가 paste와 Enter를 같은 write에서 흘리는 문제, `:10-11`, `:19-21`)이지만, **프롬프트 본문이 터미널 제어 시퀀스가 되는 것을 막는다**는 부수 효과가 있다.

---

## 3. 계정 격리와 전환

### 3.1 HOME/config dir 격리 — 3가지 서로 다른 기법

| 기법 | 파일 | 근거 |
| --- | --- | --- |
| env var 오버라이드 홈 | `src/shared/skill-install-providers.ts:9-11` (`homeEnv`) | `CLAUDE_CONFIG_DIR`(:25), `GROK_HOME`(:63), `KIRO_HOME`(:94), `OPENCLAUDE_CONFIG_DIR`(:108) |
| XDG 분리 | `src/main/managed-data-accounts/service.ts:222-232` | 계정별 `XDG_DATA_HOME=<acct>/data`, `XDG_STATE_HOME=<acct>/state`, opencode는 `OPENCODE_AUTH_CONTENT=''`+`OPENCODE_DB='opencode.db'` **강제** |
| 관리형 홈 미러 | `src/main/codex/codex-home-paths.ts:23-34, 41-49` | `~/.codex` → `<userData>/codex-runtime-home/home`, `skills/hooks/plugins/prompts/AGENTS.md` 미러 대상 |

`[채택]` **오버라이드 홈의 안전성 검사**가 인상적이다: `skill-provider-runtime-roots.ts:28-39` `isUsableProviderHome`이 후보가 (a) 자기 자신의 dirname이면 거부(루트), (b) user home의 조상이면 거부 — "unrelated top-level folders cannot become skill roots". `:41-52`은 절대경로/길이 32768/NUL 포함을 거부. WSL은 로그인셸 1회 probe로 `$HOME` + 각 provider home을 NUL 구분으로 받아(`:18-24`, `:124-150`) 같은 검사를 posix semantics로 재적용.

### 3.2 전환 시 상태 오염 방지 — env baseline 캡처/복원

`[채택]` `src/shared/managed-data-account-environment.ts`

- `:11-16` `MANAGED_DATA_ACCOUNT_BASELINE_ENV_KEYS` = `XDG_DATA_HOME`, `XDG_STATE_HOME`, `OPENCODE_AUTH_CONTENT`, `OPENCODE_DB`.
- `:18-29` 오버레이 적용 **전에** 원본을 `ORCA_DATA_ACCOUNT_ORIGINAL_ENV`(JSON)로 캡처.
- `:31-83` `restoreManagedDataAccountEnvironment`: ① 자기 소유 표식(`ORCA_DATA_ACCOUNT_PROVIDER`/`_DATA_HOME`)이 있을 때만 동작, ② 각 키가 **자기가 심은 값과 정확히 일치할 때만** 복원(`:63-65` — 사용자가 중간에 바꾼 값은 건드리지 않음), ③ 원본이 없으면 지우지 않고 baseline으로 되돌림, ④ 소유 표식 키 삭제.
- `launch-environment.ts:30-42`: `selected.XDG_DATA_HOME`이 없으면 **중단**(부분 적용 금지), `OPENCODE_AUTH_CONTENT=''`로 "선택한 프로필의 자격증명을 우회할 수 있는 override를 clear".
- 인라인 인증값은 env에 남기지 않고 reference로 치환: `service.ts:167-198`(크기 16MiB 상한, baseline 개수 상한), `:200-216`에서 selection 일치까지 확인 후 복원.

### 3.3 계정 자격증명 "캡처"는 화이트리스트 스냅샷

`[채택]` `src/main/managed-data-accounts/credential-capture.ts`

- `:22-27` 정규 파일·비심링크·16MiB 이하만.
- `:29-80` opencode: `pragma query_only=ON`, **대화 테이블이 하나라도 행을 가지면 거부** — "Use an isolated OpenCode login directory; importing conversation databases is not supported."(`:51-60`), credential row 1~64개(`:61-64`), zod discriminated union으로 형식 검증(`:10-20`, `:75-77`).
- `:113-135` 스냅샷을 만들고 **다시 감사**한 뒤 publish, 실패 시 `-wal`/`-shm`까지 삭제.
- `service.ts:293-301` `assertOwned`: root나 대상이 심링크면 거부 + `realpath` prefix 검사.
- Claude 프로필: `claude-profile-provisioning.ts:134-154` `ACCOUNT_BOUND_STATE_KEYS`가 `userID`/`machineID`/`primaryApiKey`/`claudeCodeFirstTokenDate` + `*Cache`/`cached*`/`passes*`를 계정 결속으로 분류 — "복사된 Console key가 계정 자체 로그인을 덮어쓴다"(`:140-141`). `:206-250`은 staged write(`mode 0o600`) + `publishFileWithoutOverwrite`로 경쟁 조건 회피.

### 3.4 사용량(usage) 분리는 **미확인**

`src/main/{claude-usage,codex-usage,opencode-usage,usage,muse-usage}/`가 존재하지만 이번 범위에서 읽지 않았다. "계정별 usage 분리"가 어디까지 격리되는지는 **미확인**으로 남긴다. (`transcriptEnvironments`는 계정 목록 순서로 env 배열을 만든다 — `service.ts:158-165` — 하지만 usage 집계와의 연결은 확인 못 함.)

---

## 4. 플러그인 권한 모델

### 4.1 무엇을 할 수 있는가 — 닫힌 7개 capability

`[대조]` `src/shared/plugins/plugin-capabilities.ts:6-14`:
`workspace:read`, `terminal:send`, `notifications:show`, `storage`, `secrets`, `events:subscribe`, `settings:own`.

`:3-4` 주석이 태도를 규정한다: "Closed v0 capabilities fail unknown kinds instead of silently granting nothing." `:24-33`은 각 capability의 **사용자에게 보여줄 문구**를 코드에 고정하고 `:23`에서 "Consent copy shown verbatim; describe only what the host actually enforces"라고 못박는다.

`:36-43` `canonicalizeCapabilitySet` — 순서·중복·필드 순서를 정규화해 manifest 서식 변경이 동의를 깨지 않게 한다.

### 4.2 신뢰: deny-by-default 게이트 + 동의 지문

`[채택]` `src/shared/plugins/plugin-capability-gate.ts:24-51` — Electron-free 공용 게이트(desktop/serve/relay가 같은 판정).
- `:26-28` 표에 없는 method → `unknown_method`.
- `:29-35` `viaPanel`(샌드박스 panel)인데 panel-callable이 아니면 `panel_forbidden`.
- `:36-43` `grantedCapabilities === null` → `consent_required`. 주석: "Unknown, disabled and stale-consent plugins must be indistinguishable from ungranted capabilities." (정보 비노출)
- `:44-50` capability 미보유 → `capability_denied`.

`src/shared/plugins/plugin-consent-fingerprint.ts:19-31`: 동의 지문 = `capability 정규화 + (main이 있으면 '\0trusted-node-worker') + (instructional contribution이 있으면 '\0instructional-content:<hash>')`. 즉 **capability 목록이 그대로여도 신뢰 실행 코드나 지시 콘텐츠가 추가되면 동의가 무효**가 된다(`:19` 주석).

`:9-17` `hasInstructionalPluginContributions` = `keybindings` | `vmRecipes` | `agents`가 하나라도 있으면 참. 주석 `:26`: "Instructional bytes later execute under user/agent authority".

`[채택]` 그리고 이 지문을 **매 읽기마다 재검증**한다: `src/main/plugins/plugin-instructional-content-integrity.ts:6-27` — "Reverify instructional bytes on every read; discovery-time caches cannot prove they still match consent", 불일치 시 "instructional content changed since it was reviewed".

콘텐츠 무결성 일반형: `plugin-content-integrity.ts:15-33` (설치 트리 해시, dev 트리는 mutable로 예외).

### 4.3 sandbox인가? — **아니다. 정직하게 구분해야 한다.**

`[제외: sandbox라고 부를 수 없음]`
- `src/main/plugins/plugin-host-process.ts:1` `fork`로 out-of-process지만 **일반 Node 프로세스**다. VM/컨테이너/seccomp/파일시스템 격리는 이 파일에서 확인되지 않음.
- 결정적 근거: `src/main/plugins/plugin-audit-log.ts:4` — "Await host-API mutation intents before executing; **plugin workers' own Node access bypasses this log**."
- 따라서 Orca의 모델은 "**호스트 파사드(host API)는 capability로 제한, 워커 자체는 완전 신뢰**"이다. panel만 postMessage 브리지로 좁힌다(`plugin-host-api.ts:95-99` `panel` 플래그, `:248-255` 액션은 표에서 파생).

### 4.4 설치 신뢰와 아티팩트 경계

- `src/main/plugins/plugin-install-trust.ts:8-27`: `bundled`는 `stablyai.orca-*` 공식 identity만, 예약 identity는 로컬 경로 설치 금지, git/마켓은 **stablyai 조직**으로 resolve되어야 함.
- `src/main/plugins/plugin-artifact-validation.ts:9-14`: 선언된 아티팩트별 크기 상한(panel 10MB, worker 50MB, vm recipe 256KB 등). `:44-100` `resolveContainedPluginArtifact`는 `realpath` 기준으로 루트 밖 탈출·비정규 파일·상한 초과를 거부.
- `src/shared/plugins/plugin-manifest.ts:65-107`: `manifestVersion:1`, `engines.orca`는 `>=x.y.z` 문법만(`:29-32`), `pluginApi: z.literal(1)`, `capabilities` 최대 32개, `.strict()`.
- 호스트 API 표: `src/shared/plugins/plugin-host-api.ts:85-100`(name/since/scope/stability/capability/mutation/panel), `:109-240` 13개 method. `:90`의 `scope`가 `active-worktree` / `explicit-terminal` / `plugin-private` / `desktop` / `host-events`로 **자원 경계를 선언**한다. `terminal.sendText`는 `terminalId`를 **필수**로 받는다(`:33-37` "Explicit target prevents delayed writes following focus changes into another pane").

→ **TalkAbout 부착**: TalkAbout의 권한은 서버 카탈로그(`docs/PERMISSIONS.md`)지만 **표와 게이트가 분리되어 있다**(§8-⑮). Orca의 "표 하나에서 게이트·panel 액션·SDK를 파생"(`plugin-host-api.ts:248-255`)시키는 구조를 그대로 옮길 자리다. 비용 **M**.

---

## 5. 스킬 배포·주입 — **이 보고서의 핵심**

### 5.1 3계층 파일 구조

```
skills/<name>/SKILL.md          ← 설치되는 것 (스텁)
skill-guides/<name>.md          ← 바이너리에 내장되는 정본
skill-guides/<name>/references/*.md  ← 조건부로만 읽는 심화 문서
skill-stubs/_shared/cli-resolution.md ← 스텁 공통 블록 원본
```

실측(라인 수, 빈 줄 포함): `skills/orchestration/SKILL.md` 66줄, `skill-guides/orchestration.md` 204줄, `skill-guides/orchestration/references/*` 7개(coordinator-loop 67, messaging-and-gates 73, recovery-and-cleanup 175, worker-contract 72 등). `skill-guides/orca-per-workspace-env.md`는 376줄 + references 6개.

### 5.2 스텁의 존재 이유 — 드리프트 차단

`[채택]` `skills/orchestration/SKILL.md:15-17`:
> "This file is a discovery stub, not the usage guide. The full, version-matched Orca orchestration reference is served by the `orca` binary itself — kept out of this file on purpose so it can never drift from the binary that will actually run your commands."

스텁 본문 구성(`:1-11` front-matter, `:28-45` CLI 해결, `:47-66` 가이드 로드):
1. front-matter `name` + `description` (에이전트가 **언제 이 스킬을 로드할지** 판단하는 유일한 근거)
2. 이 세션에서 쓸 실행 파일 결정 규칙(`ORCA_CLI_COMMAND` → `ORCA_DEV_REPO_ROOT` → Linux 비-Orca 터미널이면 `orca-ide`(GNOME 스크린리더 회피) → `orca`)
3. `ORCA skills get orchestration` 실행해서 **지금 바이너리의** 가이드를 받으라고 지시
4. 실패 시 행동(`--reference` 미지원이면 `--full`; `runtime_access_denied`면 권한 상향 재실행, 재시작 금지; `skills get` 자체가 없으면 "업데이트하면 복구된다"고만 말하고 추측 금지) `:61-66`

### 5.3 생성 파이프라인

`config/scripts/generate-bundled-skill-guides.mjs`
- `CANONICAL_GUIDE_NAMES`(10개)와 `STUB_TOPICS`(10개)가 별도 목록. 주석: "Migration is one-way: old fat installs need new stubs to converge, so only add STUB_TOPICS".
- `GUIDE_ALIASES`: "Aliases preserve renamed discovery stubs; append ledger entries, never remove them."
- `composeStubProjection`: 가이드의 front-matter를 **바이트 그대로 재사용**하고 본문만 스텁으로 치환 → 라우팅 메타데이터가 stub/guide 사이에서 어긋나지 않는다.
- `composeFullMarkdown`: references를 `<!-- bundled-reference: <path> -->` 마커로 이어붙여 `--full` 응답을 만든다.
- `serializeEmbeddedModule`: 상수명 충돌 검사("Flattened guide/reference constant names can collide and silently serve the wrong reference").

`config/scripts/skill-stub-composition.mjs`
- 공통 블록 문법 `<!-- block: id -->` / 삽입 `<!-- shared: id -->`.
- **정확히 1회 삽입 강제**(`count !== 1`이면 throw), 그리고 **재인라인 금지** 검사: "re-inlining a copy beside the marker is exactly the drift this fragment ends". 즉 사람이 편해서 본문을 복사해 넣으면 생성기가 빨간불을 낸다.

산출물: `src/cli/bundled-skill-guides.ts:1` "Generated by ... Do not edit." → `src/cli/handlers/bundled-skill-guide-table.ts:23-27`에서 **지연 import**.

### 5.4 서빙 API

`src/cli/handlers/skill-guide-get.ts:72-106` — `orca skills get <topic> [--full|--reference <name>|--references] [--json]`.
`src/cli/handlers/skills.ts:7-23` — `skills list`(name+description만), `skills get`, `skills install`, `skills update`.
`skill-guide-get.ts:13-19` — `references/<file>.md`와 `references/<file>` 둘 다 수용(정규화), `:58-70` 미지의 reference는 **가능한 목록을 함께 반환**.

### 5.5 파일을 어디에 복사/링크하나

`[채택]` 정본 루트는 `<scope>/.agents/skills`(`skill-provider-destinations.ts:36`). provider별 경로 표는 `src/shared/skill-install-providers.ts:13-272`이며 `globalSegments`/`workspaceSegments`가 `null`이면 **정본 루트를 직접 읽는 에이전트**로 취급한다(`:3` 주석, `:17-19` codex, `:165-169` opencode, `:170-175` pi, `:176-181` amp, `:267-271` dsh).

provider별로 다른 것 = **디렉터리 관례 하나뿐**이고, 파일 형식은 모두 `SKILL.md`다. `bundled-skill-sources.ts:3-9`:
```ts
readonly name: string
/** LF-normalised `SKILL.md`, the only file a bundled skill ships. */
readonly text: string
/** sha256 of every LF-normalised `SKILL.md` any build shipped, this one included. */
readonly releasedDigests: readonly string[]
```
→ "**번들 스킬은 SKILL.md 한 파일만 배송한다**"가 타입으로 강제된다. `releasedDigests`는 과거에 배송된 모든 해시를 누적해 업데이트 분류 기준으로 쓴다.

경로 해석: `targets.ts:59-113`
- `:87-90` workspace scope에서는 **이미 존재하는** agent 폴더에만 link를 만든다(리포에 새 디렉터리를 만들지 않음).
- `:98-104` workspace 밖(상위 디렉터리로 탈출하는 realpath)은 후보에서 제외.
- `:94-111` realpath 기준 dedupe — 링크된 provider home이 같은 폴더를 두 번 계획하지 않게.
- `:41-57` `resolveBundledSkillRealPath`: 아직 없는 꼬리 경로를 가장 가까운 존재 조상의 realpath에 다시 붙인다.

### 5.6 설치 결정표 — 사용자 편집을 절대 덮지 않는다

`plan.ts:176-215` (계획은 순수 함수, 파일시스템은 주입됨)

| 현재 상태 | 결정 |
| --- | --- |
| `unreadable` | `leave('unreadable', false)` — 손대지 않음 `:177-179` |
| `current`(digest 일치) | `keep` `:184-185` |
| `released`(과거 배송본) | `update` `:186-187` |
| `absent` + shared 없음 | `write` `:189-190` |
| `absent` + shared가 current | `link` (심링크 지원 시) 아니면 `copy` `:195` |
| `edited` / `other` | `leave('edited'|'other', replaceable=true)` — `--force` 없으면 **덮지 않는다** `:209-211` |
| `--force` | backup 후 `replace` `:212-214` |
| pending cloud transaction | `leave('busy')` `:223-224` |
| 루트 쓰기 불가 | `leave('unwritable')` `:225-227` |

`:54-59` `WRITES_SHARED_CURRENT` = {keep, write, update, replace} — shared 복사본이 실제로 갱신되었을 때만 agent 링크를 건다. `:166-174` "Do not link until the shared-copy operation succeeds" + `requires: target.path`로 실행 순서를 데이터로 표현(execute.ts:188-189에서 `requires` 미완이면 `skipped`).

### 5.7 설치 실행 — 멱등·원자·복구

`[채택]` `execute.ts`
- `:214-235` **preview digest CAS**: `executeBundledSkillPlan(input, planDigest)`가 락 획득 후 **다시 계획**하고 digest가 다르면 `unrun('changed')`로 **아무것도 쓰지 않는다**. 사용자가 `--dry-run`으로 본 그대로만 실행된다.
- `:38-72` 락 경로를 **정렬**해서 획득(deadlock 방지), 실패 시 역순 해제.
- `:220-230` 락 획득 실패 → `'busy'`.
- `:80-100` 매 op 직전 `verifier`로 관찰값 재확인, 불일치면 `BundledSkillChangedError` → status `'changed'`(부분 성공을 성공으로 보고하지 않음).
- `:135-181` op 적용. `:154-158` `update`는 two-rename swap 후 남은 old를 정리하되, 실패를 삼키고 다음 계획의 cleanup으로 넘긴다("a leftover old copy is the next plan's cleanup, not a failed update").

`swap.ts`
- `:26-30` **백업은 스킬 루트 밖**: `~/.agents/.orca-skill-backups/<name>-<utc>-<uuid>`. 이유가 주석에 있다 — "Backups stay outside skill roots (agents would load them) and projects (they would pollute repos)."
- `:32-43` 이 설치기의 **유일한 delete**는 자기 자신의 `.orca-stage-*`/`.orca-old-*` sibling만. 그 외는 `bundled-skill-refused-delete`.
- `:82-106` `swapBundledSkillFolder`: stage → old로 rename → stage를 target으로 rename. POSIX에서 비어있지 않은 디렉터리를 원자 교체할 수 없다는 한계를 인정하고, 중간 실패 시 `old → target` 복원(`:100-103`).
- `:108-140` `replaceBundledSkillPath`: **외부(사용자) 콘텐츠는 disposable `.orca-old`가 아니라 백업 경로로** 이동한 뒤 새로 배치. 실패 시 백업을 되돌린다.
- `:45-80` `putBundledSkillFolder`: stage 후 verify → rename.

`skill-install-lock.ts:25-37` — lock 파일명 `sha256(path).lock`, 소유자/후보/해제 sibling 문법 정규식, 스테일 30분, PID 생존 확인(`:38-46`).

### 5.8 프롬프트 주입은 **하지 않는다** — TalkAbout과 정반대

`[대조]`

| | Orca | TalkAbout |
| --- | --- | --- |
| 지시의 소유자 | 로컬 에이전트 CLI(파일을 발견) | 서버(프롬프트에 넣어 전송) |
| 전달 매체 | `<home>/.agents/skills/**/SKILL.md` + `orca skills get` | `json.dumps(context)`를 stdin/ACP/HTTP body로 |
| 로컬 스킬 발견 | 전제로 활용 | **명시적으로 끈다** |

TalkAbout은 `adapters.py:89`에서 codex에게 `-c features.skip_host_skill_discovery=true -c skills.include_instructions=false`를 주고, `agent_runtime.py:563`에서도 동일 계열 플래그(`'skills.include_instructions=false'`)를 붙인다. claude는 `adapters.py:80-82`에서 `--tools ''`, `--restricted`, `--safe-mode`, `--strict-mcp-config`, `--setting-sources ''`, `--no-session-persistence`로 로컬 설정 주입 자체를 차단한다.

즉 **Orca의 파일 배포 방식을 TalkAbout 런타임에 그대로 이식하면 TalkAbout의 통제 모델이 깨진다.** 이식 가능한 것은 "버전 일치 가이드를 서버가 서빙하고, 클라이언트는 스텁만 갖는다"는 **프로토콜 구조**다(§9-b).

### 5.9 `bundled-skill-*` CLI 핸들러

`src/cli/handlers/bundled-skill-install-command.ts`
- `:35` `SHARED_FOLDER_ONLY = 'universal'` — "`--agent universal`은 공유 폴더만".
- `:40-76` `requestedAgents`: `undefined`=자동 감지, `[]`=공유 폴더만, 콤마 다중 허용, 미지 값은 **가능한 목록 전체를 포함한 에러**(`:66-72`), 과거 외부 skills CLI 키는 alias로 수용(`:37-38`).
- `:78-108` `resolveSelectedSkillNames`: `--all`과 `--skill` 동시 사용 거부, alias를 canonical name으로 접기, 정렬.
- `:177-185` 브리지된 셸(`ORCA_CLI_CWD`)에서는 **거부**: "writes to the machine that runs it ... Run the same command on the machine you want it on". — 원격/로컬 대상 혼동 방지.
- `:187-203` `--local`이면 workspace scope, 아니면 global.
- `:219-243` `--dry-run`: 계획 + `planDigest` 출력, "Rerun without --dry-run to install now".
- `:245-247` `process.exitCode = summary.exitCode` — 부분 실패를 종료 코드로 전달.
- `:266-276` 사람용 요약은 op별 문구(`describeOutcomeOp:132-143`): `changed` → "Changed since it was planned; nothing was written. Run it again to re-plan.", `skipped` → "Skipped: the shared copy it depends on did not land."

---

## 6. MCP 설정 위임

### 6.1 결론: Orca는 MCP 설정을 **쓰지 않는다**

`src/shared/mcp-config.ts:45-70` `MCP_CONFIG_CANDIDATES` 4개:

| format | relativePath | serversPath |
| --- | --- | --- |
| workspace | `.mcp.json` | `['mcpServers']` |
| cursor | `.cursor/mcp.json` | `['mcpServers']` |
| claude | `.claude.json` | `['mcpServers']` |
| claude | `.claude/mcp.json` | `['mcpServers']` |

`:93-103` `selectExistingMcpConfigCandidates` — **존재하는 파일만** 검사 대상. `:112-163` `inspectMcpConfigContent` — 크기 상한(`mcp-config-inspection-limits`) → JSON parse → `serversPath` 추출 → 서버별 요약(transport/status/command/url/env). `:72-75` `MCP_STARTER_CONFIG`가 정의되어 있으나 **`mcp-config.test.ts:9,148`에서만 사용**된다(grep 전수). 즉 **starter를 실제로 쓰는 코드 경로는 없다.**

### 6.2 위임의 실체 = 읽기 전용 검사 + 3개 거부 규칙

`src/renderer/src/components/settings/mcp-config-inspection.ts`
- `:21-48` 루트 디렉터리 + 후보 부모 디렉터리만 읽어 존재 여부 판정.
- `:90-98` **"Known existing but unreadable configs must never permit starter overwrite."** — 존재하지만 못 읽으면 `status:'invalid'` + `readError`. `exists:false`로 뭉개지 않는다.
- `:76-89` 읽기 성공 시에만 inspect, ENOENT만 missing으로 처리.

`src/shared/mcp-config.ts:105-110` `canInspectLocalMcpConfigRoot(rootPath, isWindowsHost)` — Windows가 아닌 호스트인데 드라이브/UNC 경로가 오면 **검사 거부**(원격 호스트에 로컬 경로가 주입되는 상황 차단).

### 6.3 진짜 "병합"이 있는 곳 — 계정 프로필 프로비저닝

`[채택]` `src/main/claude-accounts/claude-profile-provisioning.ts:182-204`
```ts
// Preserve account-only project and server entries during merging.
const MERGED_BY_ENTRY = new Set(['projects', 'mcpServers'])
```
- `mergeEntries`: `{...current, ...source}` 후 `projects`만 `hasTrustDialogAccepted === true`를 **현재 계정 값으로 보존**(`:197-201`).
- 호출부 `:234-242`: `MERGED_BY_ENTRY`인 키만 병합, 나머지는 source 값으로 덮되 **"Preserve keys Claude added only in this account folder"** — 즉 계정 폴더에만 추가된 키를 유지.
- `:161-180` 비교 시 Orca가 설치한 hook을 제거한 뒤 비교(`settingsWithoutOrcaHooks`) — "Ignore managed hooks to avoid copy/install churn on every launch".

Codex 쪽: `src/main/codex/config-toml-mcp-servers.ts:9-41` `readMcpServerTomlOwnership` — TOML에서 `mcp_servers` 테이블 헤더·키 경로를 스캔해 **어떤 서버 이름을 이 파일이 소유하는지** 판정하고, `mcp_servers = {...}` 인라인 루트 할당은 `ownsRoot=true`로 표시("Canonical inline root assignments own the whole table, which TOML forbids extending"). 이 소유권 정보를 미러가 사용: `codex-config-mirror.ts:178`, `config-settings-baseline.ts:21,84-86`(`mirroredMcpServers`, `mcpServerRoot`).

→ **패턴 요약**: ① 후보 파일을 **읽어** ② 소유/비소유 엔트리를 **구분**하고 ③ 내가 만든 것만 갱신하며 ④ 못 읽으면 아무것도 하지 않는다.

### 6.4 `.mcp.json` / `opencode.json` 실물

- `.mcp.json`(리포 루트): `mcpServers` 1개 — `orca-component-fixtures`, `type:'stdio'`, `command:'node'`, `args:["config/scripts/component-fixtures/fixtures-mcp-server.mjs"]`. **Orca가 생성한 것인지 미확인**(정적 파일만 확인).
- `opencode.json`(리포 루트): `permission.{'*':{'*':'allow'}, read:{'*.env':'ask','*.env.*':'ask','*.env.example':'allow'}, external_directory:{'*':'ask'}, doom_loop:'ask'}` + `lsp`. `[차용]` **`.env` 읽기를 ask로 올리고 `.env.example`만 allow** 하는 규칙은 값싸고 명확한 정책 표현이다. 비용 **S**.
- `orca.yaml`: `scripts.setup`만 존재(`node config/scripts/run-internal-dev-setup.mjs`, `pnpm install`). 워크스페이스 환경 레시피 키는 이 저장소에서 **미확인**(`environmentRecipes` 언급은 `skill-guides/orca-per-workspace-env.md`에만).
- `.claude/settings.json`: `extraKnownMarketplaces` + `enabledPlugins`(orca-typescript on, typescript-lsp off) — **정적 파일**, 프로그램이 쓰지 않는다.

---

## 7. preflight / ports / local-builds

### 7.1 preflight — "설치됨"과 "인증됨"의 분리

`[채택]` `src/main/preflight/agent-detection.ts:40-60` `PreflightStatus`:
`git:{installed}`, `gh:{installed, authenticated}`, `glab?`, `bitbucket?{configured, authenticated, account}`, `azureDevOps?{..., tokenConfigured}`, `gitea?{...}`.
→ **설치와 인증을 별도 필드로 보고**한다. TalkAbout의 "등록됨/연결됨/실제로 발언함" 3단 증거 구분과 정확히 같은 사상이다.

캐시 설계(`:65-90`):
- 앱 세션 전체 캐시 + WSL distro별 30s TTL(`:67-70`) — "a short TTL recovers unreachable distros whose failures appear as missing tooling".
- in-flight 중복 제거(`:71`), per-key generation + epoch로 **오래된 probe가 갱신된 캐시를 덮어쓰는 것 차단**(`:73-76`, `:89-90`).

`installed-agent-detection.ts:14-22`는 "어떤 agent CLI가 설치되어 있는가"만 판정한다 — 인증·실행 가능성은 별개.

### 7.2 ports — workspace 귀속 포트 스캔

`[제외]` `src/main/ports/local-workspace-port-scanner.ts:19-55`
- 리스닝 포트를 스캔해 worktree에 귀속(`normalizeWorkspacePortProbes`, `enrichPort`), 최대 200개(`:19`), 정렬.
- `:39-42` **metadata를 못 얻으면 eviction 금지**: "Missing metadata cannot prove listener loss; eviction would discard URLs only PTY output can restore (#11161)".
- `:26-33` 명령 타임아웃 후 cooldown, `:48-53` 실패를 `unavailable` 결과로 반환(예외 전파 안 함).

TalkAbout에는 로컬 dev server 개념이 없으므로 **[제외]**. 다만 `:39-42`의 "증거 부족 ≠ 부재" 원칙은 §1.3과 같은 계열로 참고 가치가 있다.

### 7.3 local-builds

- `[제외]` `src/main/local-builds/local-build-switch.ts:7-58`
- 사용자가 `.yml` 업데이트 매니페스트를 고르면 `loadLocalBuildCandidate`로 검증, **동일 버전이면 거부**(`:25-29`), `assertLocalBuildCompatibility`(`:30`), 확인 다이얼로그에 "live terminal N개 재연결"과 "state schema 호환"을 명시(`:31-41`), "same valid code signature as Orca or installation will stop"(`:41`).
- TalkAbout은 로컬 빌드 교체 시나리오가 없다. **[제외]**. `local-build-candidate.ts`와 `local-build-feed-server.ts`는 읽지 않았다 — **미확인**.

---

## 8. TalkAbout 구체 개선점 (파일:라인)

TalkAbout 소스를 실제로 읽고, **Orca에서 확인한 것과 대조해** 짚는다.

### ① prompt-context 비밀 검사가 exact substring뿐이다 `[채택]` 비용 S

`talkabout/agent_runtime.py:1088-1089`
```python
if any(secret in prompt for secret in self.blocked_values):
    raise AdapterError('runtime_secret_in_context')
```
`talkabout/http_runtime.py:155-156` 동일. `blocked_values`는 `adapters.py:73-74`에서 자른 소수(플랫폼 credential)뿐이다.

문제: (a) 방 참가자가 붙여넣은 형태가 조금만 달라도(`Bearer ` prefix, 따옴표, JSON escape, base64) 통과한다. (b) 검사 대상이 **자기 회전 credential**에 한정되어, 다른 서비스 키가 방 내용으로 흘러들어가는 것은 막지 않는다.

Orca `redactor.ts:10-29`의 provider별 정규식 집합 + `crash-report-redaction.ts:23-24`의 `SECRET_ASSIGNMENT_PATTERN`을 **거부 판정**에 재사용하면 (a)를 즉시 메운다. (b)는 "거부"가 아니라 "마스킹 로그 + 사후 경고"가 맞다(§9-a).

### ② `identity_operation` quota가 누적이라 장기 운영에서 마감된다 `[채택]` 비용 S

`talkabout/identity.py:153`: `if parts[0] != "connection": require(len(operations) < 2000, "identity_operation_quota", 429)`
`operations`는 operator principal에 누적되고 `:235`에서 계속 추가된다. 삭제·만료 로직이 없다(읽은 범위 내). 장기 운영 시 2000건 도달 후 **모든 identity mutation이 429**가 된다.

Orca의 해법이 정확히 이 문제다: `identity.py`와 같은 위치에 해당하는 `identity.runtime_check`는 rolling retention을 쓴다 — `identity.py:74-100` 주석 "A rolling one-hour ledger bounds storage and rate without imposing a lifetime quota. Known retired keys fail closed; after pruning, clients must use fresh keys".

부착: `identity.py:235` 직전에 `retain_until` 기반 prune(또는 `response_until` 만료 항목 제거). `DEFAULT_RETENTION.response_seconds`(`:5`에서 import)를 그대로 쓴다.

### ③ `identity.py`는 이미 Orca보다 잘한다 `[대조]`

- challenge 응답 비교가 `hmac.compare_digest`(`:121`), 원문 challenge는 완료 시 폐기(`:130`).
- session_id는 digest만 저장(`:123`) — 원문 미보관.
- credential은 `token_hash(raw)`만 DB에(`:227`), replay 시 `token_not_recoverable: True`(`:233`).
- `runtime_check`가 **credential binding + room binding**에 묶임(`:105`) → credential 회전 시 자동 `invalidated`(`:49-57`).

→ Orca `~/.orca/*.enc` + `saved-credential-readability.ts` 방식보다 TalkAbout 쪽이 **서버 측 강제**라는 점에서 우위다. Orca에서 가져올 것은 평문 감지·"sealed-elsewhere" 3분류 같은 **로컬 파일 계층의 세심함**뿐이고, 이는 TalkAbout의 `provision.py`에만 해당된다.

### ④ 실패 원인을 한 코드로 뭉갠다 `[채택]` 비용 S

`talkabout/provision.py:51-53`
```python
except Exception:
    # Never expose DSNs, SQL containing a verifier, libpq messages or passwords.
    raise ValueError('database_identity_or_secret_mismatch_or_unavailable') from None
```
의도(비밀 비노출)는 옳다. 그러나 `:49` `PostgresStore(runtime_dsn).ready()` 실패, role 불일치(`:29`), 기존 스키마 존재(`:35-38`), `installation_metadata` 불일치(`:28-30`)가 모두 같은 메시지가 되어 **운영자가 조치를 고를 수 없다**.

Orca `credentials/saved-credential-readability.ts:29-70`의 접근: 비밀을 노출하지 않으면서 **분류 코드**는 돌려준다(`'unreadable'` / `'sealed-elsewhere'` / `undefined`). 사람이 읽을 문장은 `refuseCredentialSealedElsewhere:73-77`처럼 **다음 행동을 지정**한다.

부착: `provision.py:51`의 `except`를 4~5개 `ValueError` 코드로 분해(`db_marker_mismatch`, `db_role_mismatch`, `db_schema_not_empty`, `db_unreachable`, `db_verifier_rejected`). DSN/SQL은 계속 노출하지 않는다.

### ⑤ 로그인 레이트리밋이 프로세스 메모리다 `[차용]` 비용 M

`talkabout/auth.py:42,51-57`: `self.failures = {}`, 락 안에서 최근 60초 실패 5회 제한. `:53-54` "1024 넘으면 `next(iter(...))` pop" — 삽입 순서 기반이라 LRU가 아니다.

문제: (a) 멀티 프로세스/재시작에서 우회. (b) 공격자가 1023개 키를 먼저 채우면 다른 사용자의 실패 기록이 밀려난다(정확히는 피해자 키가 evict되어 제한이 풀림).
Orca의 저장소 계층은 이 문제를 다루지 않지만(단일 데스크톱 앱), `identity.py:77-100`의 **rolling ledger + 상한 + 명시적 만료** 구조를 이식하면 (b)를 고칠 수 있다.

부착: `auth.py:51-57`을 Postgres 테이블(`login_failures(key, at)`) + 인덱스 기반 `count(*) where at > now()-60s`로. 비용 M이지만 rate-limit이 인증에서 유일한 무차별 대입 방어선임을 감안하면 우선순위가 높다.

### ⑥ PBKDF2 파라미터가 DB 값 신뢰 `[채택]` 비용 S

`talkabout/auth.py:11` `ITERATIONS = 600000`, `:25` `hashlib.pbkdf2_hmac(..., record["iterations"])`.
`:12` `ADMIN_SCOPES`, `:19` record에 `iterations`를 저장하되 **검증하지 않는다**. `human_passwords` 테이블이 변조되면 낮은 iteration으로 다운그레이드되어도 그대로 통과한다. 상한/하한 화이트리스트(예: `{600000}` 또는 `>= 210000`)가 필요하다.

Orca 대응: `credentials/saved-credential-readability.ts:56-58`이 payload를 base64로 **재인코딩해 원문과 비교**하고, 길이 0을 거부한다 — "Reject malformed base64 before it can be mistaken for a foreign sealed credential." 즉 **파싱 결과를 신뢰하기 전에 왕복 검증**하는 습관.

### ⑦ `dummy` verifier가 실제 record와 다른 시간을 쓴다 `[채택]` 비용 S

`talkabout/auth.py:44` `self.dummy = password_record("synthetic-dummy-verifier-only")` → 항상 `ITERATIONS`(600k).
`:59` `verify_password(body.get("password"), found["record"] if found else self.dummy)`.
계정이 존재하지만 `iterations`가 다른 값으로 저장되어 있으면, 없는 계정(dummy=600k)과 존재 계정(예: 210k)의 응답 시간이 달라져 **사용자 열거**가 가능해진다. `:60` `require(found and verified, "unauthorized", 401)`은 성공/실패를 하나로 만들지만 시간은 남는다.

`:25`에서 `record["iterations"]`를 쓰지 말고 화이트리스트에서 고른 값을 쓰거나, dummy를 요청별 실제 record의 iterations로 재계산해야 한다.

### ⑧ MCP 응답에 전송 등급 필터가 없다 `[채택]` 비용 S

`talkabout/mcp.py:131`:
```python
result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}], "isError": False}
```
`data`는 API 응답 그대로다. 실패 경로는 **상태 코드만** 노출해 훌륭하다(`:132-135` — `str(exc.response.status_code)`, 나머지는 `"API operation failed"`). 그러나 성공 응답에 relay/원격에 부적절한 필드(로컬 경로, 실행 정보)가 섞이면 그대로 모델·원격으로 나간다.

Orca `shared/ai-vault-search-transport.ts:5-17`이 정확한 해법이다 — transport가 `relay`면 필드를 제거하고 presence만 남긴다.

부착: `mcp.py:131` 직전에 `_project_for_mcp(data)` 같은 순수 함수 + 허용 필드 목록. 비용 S.

### ⑨ `mcp.py` instructions/version이 코드에 박제되어 서버와 드리프트한다 `[채택]` 비용 S

`talkabout/mcp.py:70`:
`"serverInfo": {"name": "TalkAbout", "version": "0.1.0"}, "instructions": "Text-only API adapter; ... post_message requires an assigned claimed task; no automatic model execution, ..."`
`:69` protocolVersion은 `{"2025-11-25", "2025-06-18"}` 하드코딩.

문제: 이 긴 instructions가 **프로토콜 계약의 유일한 모델 대상 설명**인데, 서버 동작이 바뀌어도 이 문자열은 안 바뀐다. Orca가 `skills/orchestration/SKILL.md:15-17`에서 정확히 이 문제를 지적하고 스텁+서버 서빙으로 해결했다.

부착(§9-b): `instructions`를 "행동 전에 `guide_get`을 호출하라"는 **짧은 포인터**로 줄이고, 실제 계약 문서는 `talkabout` 버전과 함께 서버가 서빙하는 리소스/툴로 옮긴다. MCP에는 `resources`가 있으므로 `talkabout://guide/protocol` 같은 리소스로 서빙하는 편이 자연스럽다. **미확인**: TalkAbout이 MCP `resources`를 지원할 계획인지 — 저장소에서 확인 못 함.

### ⑩ 첨부 업로드 sink에 secret 스캔이 없다 `[채택]` 비용 M

`talkabout/attachments.py:1` "Unwired memory-only UTF-8 attachments. No real parser, scanner or transfer."
`:83-91` `FakeFileInspector`만 존재 — 악성코드 스캐너 대체물이지 secret 스캐너가 아니다.
`:136-173` `upload()`: 파일명 규칙(`:139-141`), 확장자 화이트리스트 `.txt/.md/.py/.js`(`:102`), media type `text/plain`(`:142`), 1MiB(`:143`), strict UTF-8(`:144-145`), 제어문자 거부(`:146`), 해시(`:169`). 이 모든 검사를 통과한 **평문이 그대로 `store.blobs`에** 들어간다(`:171`).

이 저장소가 P0에서 영속화되는 순간 credential이 **불변 버전**으로 굳는다(P0 text artifact는 불변). Orca `crash-report-redaction.ts`를 업로드 시점에 적용하고, 마스킹 발생 시 `state`에 `secret_redacted` 같은 흔적을 남기는 편이 안전하다.

### ⑪ `tool_broker.py`는 Orca에 없는 정교함을 이미 갖고 있다 `[대조]`

- `:97` `require(type(self.adapter) is FakeToolAdapter, 'real_adapters_disabled')` — 실제 어댑터 주입을 **타입으로** 금지.
- `:208-212` `_approval_current`: write는 `approved_by` 필요 + `authorize_human` 재확인.
- `:232-239` 실패 시 write는 `unknown` + `intent_digest`를 `_unknown`에 넣어(`:237`) 이후 **같은 intent 재시도를 거부**(`:153` `uncertain_requires_reconciliation`). "An adapter may have acted before raising or losing authority"(`:233`).
- `:142-145` 두 개의 digest: `request_digest`(fence 포함, 멱등 비교용) vs `intent_digest`(fence 제외, 실행 의도 격리용). 이 분리는 Orca에 없다.

→ **여기서 Orca를 배울 것은 없다.** 오히려 Orca의 `plugin-audit-log.ts:4`가 인정한 "worker의 직접 Node 접근은 감사 로그를 우회한다"는 한계가 TalkAbout에는 없다(TalkAbout은 tool 자체를 broker가 소유).

다만 하나: `:180-187` `approve(invocation_id, human_id)`는 `authorize_human(human_id, room_id)`만 확인하고, **그 human이 해당 agent의 소유자인지** 또는 **자기 자신의 write를 자기가 승인**하는지는 검사하지 않는다. `docs/PERMISSIONS.md:48-51`이 "자기 승인 허용"을 제안하고 있으나, `artifact adoption`은 "자기 기여 version 채택 허용 제안, 이해관계 표시"(`:50`)로 더 좁다. 정책과 코드가 1:1이 되려면 `approve`가 **approval 정책(kind별 self-approval 허용 여부)** 을 받아야 한다.

### ⑫ `bounds.py`가 매 요청 room 전체를 직렬화한다 `[채택]` 비용 S

`talkabout/bounds.py:6-8`:
```python
require(len(room.get("events", [])) <= 2048, "event_capacity", 429)
require(len(json.dumps(room, ensure_ascii=False).encode("utf-8")) <= 2097152, "room_byte_capacity", 413)
```
이벤트 2048개 상한에서 매 snapshot/read마다 room 전체 JSON 직렬화가 발생한다. 응답 자체가 이미 직렬화되는 경로라면 이중 비용이다. `snapshot_bounds`(`:11-15`)는 room 64개에 대해 이를 반복한다.

`:1` 주석 "Explicit development capacities; paging/archival are not implemented." — 이게 진짜 문제다.
Orca `skills/discovery.ts:36-44`의 상한+TTL+retention 설계(고정 바이트 상한 256KB, 스캔 TTL 10s, LRU 1024, "unavailable은 unknown")는 페이징을 **나중에 붙일 때** 참고할 형태다. 비용 S(바이트 카운터를 room row에 유지).

### ⑬ `docs/PERMISSIONS.md`와 코드의 drift 위험 `[채택]` 비용 M

`docs/PERMISSIONS.md:3`: "**PROPOSED / NOT IMPLEMENTED · 확인일: 2026-10-13**".
`:5`가 유일한 카탈로그라고 선언하고 20개 권한을 표로 나열한다. 그런데 코드에서는 이 문자열들이 **산개**해 있다: `auth.py:12` `ADMIN_SCOPES`, `identity.py:9` `AGENT_SCOPES`, `identity.py:139`의 문자열 분기, `tool_broker.py`의 `file:*` 계열(`attachments.py:119,130,153,159,161,197,220,234,285,294,325,332`), `domain.py`의 `HUMAN_PERMISSIONS`/`AGENT_PERMISSIONS`/`SELF_PERMISSIONS`(미확인: 읽지 않음).

Orca의 구조가 해법이다: **표 하나(`PLUGIN_HOST_API_V0`)에서 게이트(`gatePluginHostCall`)와 panel 액션(`PLUGIN_PANEL_ACTIONS`)을 파생**시키고(`plugin-host-api.ts:248-255`), 표에 없는 것은 `unknown_method`로 실패(`capability-gate.ts:26-28`). 테스트가 세 구현의 판정이 동일함을 고정한다(`capability-gate.ts:4-5` "conformance tests pin identical decisions").

부착: `talkabout/permissions.py`에 `PERMISSIONS: dict[str, PermissionSpec]`(이름, 적용 주체, 적용 범위, 문서 문구)를 두고, ① `domain.py`의 집합들을 이 표에서 파생, ② `api.py`의 검사가 표를 참조, ③ `docs/PERMISSIONS.md`를 표에서 **생성**(또는 최소한 테스트로 일치 검증). 비용 M.

### ⑭ MCP tool 스키마 검증은 훌륭하다 `[대조]`

`talkabout/mcp.py:82-93`: 미지 tool 거부, `set(params) - {"name","arguments"}` 거부, arguments 타입을 `type(value) is expected`로 **엄격 비교**(bool이 int로 통과하는 Python 함정 회피), `room_id`/`task_id` 정규식, `client_mutation_id` 길이 1..128(`:110-112`).
`:144-157` `serve()`: 100000자 초과 또는 개행 없음은 framing 오류, parse 오류는 -32700, 응답은 `ensure_ascii=True`.

Orca에는 대응물이 없다(Orca는 MCP **클라이언트/설정** 쪽이고 서버를 만들지 않는다). **TalkAbout이 우위.** Orca에서 가져올 것은 없다.

### ⑮ `agent_runtime.py`의 stderr 처리 정책은 Orca보다 엄격하다 `[대조]`

`talkabout/agent_runtime.py:35-42`: `_STDERR_HINTS` = 고정 4분류(arguments/configuration/authentication/network) × 고정 바이트 라벨. `:62-97` `_safe_stderr_details`: `_CONFIG_KEYS`/`_CONFIG_OPTIONS`/`_CONFIG_TYPES` 화이트리스트에 있는 이름/옵션/타입만 추출. 주석 `:66-67`: "Arbitrary field names, enum values, paths and message text cannot be returned."
`:329-353` `diagnostics()`: 카운터·힌트·구조화 detail만. `stderr_reported_details`는 `(kind, key, actual, expected)` 튜플(`:349-352`).

Orca `redactor.ts`는 원문을 유지하고 패턴을 마스킹하는 **denylist** 방식이다. TalkAbout은 **허용 목록만 노출**하는 방식으로, 이 경로에서는 TalkAbout이 더 안전하다.

→ `[차용]` 다만 Orca의 `redactor.ts:5-7` (`LABELED_KV`가 `FOO_SECRET` 같은 word boundary를 보존한다는 주석)과 `crash-report-redaction.ts:37-53`의 **적용 순서 고정**은 "허용 목록 경로가 아닌 나머지 텍스트"(예: 모델 출력 요약, MCP 응답, 이벤트 본문)에는 여전히 필요하다.

---

## 9. 비교축 정리와 Orca에서 실제로 배울 3가지

### 9.1 축

| 축 | TalkAbout | Orca |
| --- | --- | --- |
| 강제 지점 | **서버** — `credential scope ∩ membership ∩ account scope` (`docs/PERMISSIONS.md:5`) | **로컬 데스크톱 신뢰 경계** — 호스트 파사드만 capability (`plugin-capability-gate.ts:24-51`), 워커는 완전 신뢰(`plugin-audit-log.ts:4`) |
| 증거 구분 | 3단(등록/연결/발언) — `identity.py:191-196` `execution_evidence` | 2단(installed/authenticated) — `preflight/agent-detection.ts:40-60` |
| 지시 소유자 | 서버 프롬프트(`adapters.py:92-94`) + 로컬 스킬 발견 **차단**(`:89`) | 로컬 파일(`.agents/skills`) + 서버는 가이드만 서빙 |
| 자격증명 소유 | DB에 hash만, 원문은 worker 측(`identity.py:227`, `:233`) | 로컬 파일 + OS keychain(`encrypted-api-key-file-store.ts`) |
| 마스킹 위치 | stderr 허용목록(`agent_runtime.py:62-97`), prompt substring 거부(`:1088`) | 텔레메트리 sink 단일 redactor(`observability/redactor.ts`) |
| 무결성 | revision 다층(`room_revision`, `membership_revision`, `artifact_revision`) | 트리 해시 + plan digest(`plan.ts:316-322`, `plugin-instructional-content-integrity.ts:6-27`) |

**모순처럼 보이는 지점(사실 확인)**: "Orca는 sandbox"라는 인상과 달리 plugin worker는 일반 Node `fork`이고(`plugin-host-process.ts:1`), 감사 로그도 우회된다고 코드가 자인한다(`plugin-audit-log.ts:4`). 반대로 "TalkAbout은 서버 강제"라지만 `bounds.py`·`auth.py` 레이트리밋은 프로세스 메모리에 있다(§8-⑤). **양쪽 모두 문서가 코드보다 강하게 읽힐 수 있는 지점이 있다.**

### 9.2 (a) 로그/프롬프트 마스킹 기법 → TalkAbout 부착

`[채택]` Orca `observability/redactor.ts`를 `talkabout/secrets_redaction.py`로 이식.
- **단일 choke point + idempotent**: 모든 sink에서 안전하게 반복 호출(`redactor.ts:91`). TalkAbout은 이미 `store.py`가 단일 저장 경로이므로 sink가 명확하다.
- **적용 순서 고정**: labeled-KV → provider 패턴 → URL userinfo → env 라인(`:92-112`). 순서를 바꾸면 `sk-ant-`가 `sk-`로 잘려 태그가 뭉개진다(`:9-10` 주석).
- **값뿐 아니라 키도 본다**: `shouldDropAttributeKey`(`:72-89`) — 구조화 로그에서 `token` 키는 값 검사 없이 통째로 버린다.
- **크래시 리포트의 스키마 규칙**: 키가 `*path`면 값 검사 없이 버린다(`crash-report-redaction.ts:60-63`). TalkAbout `diagnostics()`(`agent_runtime.py:349-352`)는 이미 원문을 안 담으므로, 이 규칙은 **모델 출력·이벤트 본문 요약**에 적용한다.

부착 위치(우선순위):
1. `talkabout/agent_runtime.py:1088` / `http_runtime.py:155` — substring 거부 → **패턴 거부 + 마스킹 로그** (S)
2. `talkabout/mcp.py:131` — 응답 투영 (S)
3. `talkabout/attachments.py:171` — 업로드 sink (M)
4. 이벤트/아티팩트 영속화(`store.py`) — **미확인**(store.py 내부를 읽지 않았음). 읽고 sink 지점 확정 필요.

**주의**: Orca redactor를 그대로 복사하면 `[redacted:...]` 태그가 TalkAbout의 digest/revision에 영향을 준다(마스킹하면 내용이 변하고 불변 버전 해시가 달라짐). 마스킹은 **저장 전**에 하고, 마스킹 여부를 별도 필드로 남기는 순서를 지켜야 한다. 이건 Orca에 없는 TalkAbout 고유 제약이다.

### 9.3 (b) 스킬 주입 규약 → TalkAbout 부착

`[채택]` 파일 배포가 아니라 **프로토콜 구조**를 이식한다.

1. **스텁/정본 분리**. 에이전트 측에 두는 것은 `name`+`description`+ "행동 전에 서버에서 정본을 받아라" 3문장뿐(`skills/orchestration/SKILL.md:15-17,47-59`). 정본은 서버가 버전과 함께 제공.
2. **정본은 서버 버전에 묶는다**. Orca는 바이너리에 임베드하고 `ORCA skills get <topic>`으로 꺼낸다(`skill-guide-get.ts:72-106`). TalkAbout은 이미 MCP 서버를 제공하므로 `resources/read`가 자연스러운 대응물이다(`mcp.py`에 미구현 — **미확인**, 현재 `resources` 지원 없음이 코드상 확인됨: `:70` capabilities에 `tools`만 선언).
3. **부분 로딩**. `--full`이 아니라 `--reference <name>`으로 조건부 문서만 받는다(`skill-guide-get.ts:86-98`). 이게 없으면 계약 문서가 프롬프트를 잡아먹는다.
4. **공유 블록의 단일 원본**. `skill-stub-composition.mjs`가 `<!-- shared: id -->` 삽입을 정확히 1회 강제하고 재인라인을 거부. → TalkAbout은 `docs/`의 계약 문구(참여 규칙, receipt 의미, "등록≠연결≠발언")를 여러 곳에 복사하고 있으므로, **생성기 + 드리프트 테스트**로 바꿀 자리다.
5. **별칭 원장**. `GUIDE_ALIASES`/`ALIASES`는 "append only, never remove"(생성기 주석) — 이미 배포된 스텁이 영원히 유효해야 하기 때문. TalkAbout의 MCP tool 이름 변경에도 같은 규칙이 필요하다.

부착:
- `talkabout/mcp.py:70` instructions → 짧은 포인터 + `version`을 단일 소스에서 주입 (S)
- `talkabout/mcp.py`에 `guide_get` tool(또는 `resources`) 추가 — `initialize` 결과에 "call guide_get before your first mutation" (M)
- `docs/` 계약 문구 단일 원본 + 생성기 (S)

### 9.4 (c) MCP 설정 위임/병합 패턴 → TalkAbout 부착

`[채택]` TalkAbout이 "MCP 서버"를 제공하는 쪽이므로, 사용자 환경에 **등록 도우미**를 만들 때 Orca의 4규칙을 그대로 쓴다.

1. **후보를 읽고 존재하는 것만 다룬다** (`mcp-config.ts:93-103`). 없는 파일에 starter를 만들더라도 **한 번 확인** 후.
2. **못 읽으면 아무것도 하지 않는다** (`mcp-config-inspection.ts:90-98`). `.mcp.json`이 권한 문제로 못 읽히는데 새로 쓰면 기존 서버 정의를 파괴한다.
3. **엔트리 단위 병합 + 소유권 구분** (`claude-profile-provisioning.ts:182-204`, `config-toml-mcp-servers.ts:9-41`). 내가 추가한 키만 갱신하고, 사용자가 추가한 형제 키와 `trust` 상태는 보존.
4. **원격/로컬 대상 혼동 금지** (`bundled-skill-install-command.ts:177-185`, `mcp-config.ts:105-110`). 원격 셸에서 실행되면 "이 명령은 실행되는 기계에 쓴다"고 거부.

부착: `talkabout/cli.py:62,134`(이미 `--prompt-token`으로 TTY 비밀 입력을 강제) 옆에 `talkabout mcp install` / `talkabout mcp status` 하위 명령. 비용 **S**. `.mcp.json`, `.cursor/mcp.json`, `.claude.json` 3후보 + `--dry-run` diff. **`[채택]`** — TalkAbout은 이 코드가 아예 없고, Orca의 거부 규칙은 그대로 옮길 수 있는 완성된 형태다.

### 9.5 추가로 채택할 것 (위 3개 밖)

- `[채택]` **plan digest CAS**(`execute.ts:214-235`): 사용자가 `--dry-run`으로 확인한 계획이 아니면 쓰지 않는다. TalkAbout의 "명시 확인·감사"(`docs/PERMISSIONS.md:26-30`) 원칙과 같은 정신이며, 실행 코드로 강제하는 형태가 없다. 비용 S.
- `[채택]` **"instructional content" 동의 지문**(`plugin-consent-fingerprint.ts:19-31`) + 매 읽기 재검증(`plugin-instructional-content-integrity.ts:6-27`). TalkAbout의 방 규칙·초대 고지·역할 프롬프트가 여기에 해당한다. 비용 M.
- `[차용]` **마스킹 커버리지 경고**(`worker-terminal-redaction.ts:9-10,144-148`): 마스킹했지만 불완전할 수 있음을 결과에 실어 보낸다.
- `[차용]` **`.env`만 ask**(`opencode.json` permission): TalkAbout worker 설정 가이드에 그대로.

---

## 10. Top 5 실행 제안

| # | 제안 | 태그 | 비용 | TalkAbout 부착 위치 | 근거(Orca) | 검증 방법 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `talkabout/secrets_redaction.py` — 단일 choke point, 적용 순서 고정(label→provider→userinfo→env), 키 이름 기반 drop, idempotent. sink 3곳에 연결: prompt 거부(`agent_runtime.py:1088`, `http_runtime.py:155`), MCP 응답 투영(`mcp.py:131`), 첨부 업로드(`attachments.py:171`) | `[채택]` | M | `talkabout/secrets_redaction.py` 신규 + 위 3파일 | `observability/redactor.ts:1-2,6-35,72-112,181-222`; `crash-report-redaction.ts:37-53`; `ai-vault-search-transport.ts:5-17` | 기존 636 테스트 유지 + 신규 단위테스트: `sk-ant-`/`ghp_`/`AKIA`/JWT/`KEY=value`/URL userinfo 6계열이 마스킹되고 정상 문장은 불변. `agent_runtime`에 "패턴이 prompt에 있으면 `runtime_secret_in_context`" E2E |
| 2 | MCP `instructions`를 짧은 포인터로 축소 + 서버 버전 단일 소스 주입 + `guide_get`(또는 `resources/read`)로 정본 서빙 | `[채택]` | S | `talkabout/mcp.py:69-70`(+ 신규 tool/리소스), `docs/` 계약 문구 단일 원본 | `skills/orchestration/SKILL.md:15-17,47-59`; `skill-guide-get.ts:13-19,72-106`; `skills.ts:7-23`; `skill-stub-composition.mjs` | `initialize` 응답에 `serverInfo.version`이 단일 소스와 일치. `guide_get` 미지 topic이 가능한 목록을 반환. 프롬프트 토큰 감소(문자 수) 측정 |
| 3 | `talkabout mcp install/status` 비파괴 설정 도우미: 3후보 읽기 → 엔트리 diff → `--dry-run` plan digest → 확인 후에만 병합. 못 읽는 파일은 중단. 원격 실행이면 거부 | `[채택]` | S | `talkabout/cli.py`(신규 서브커맨드), `talkabout/mcp.py`(등록 스펙 상수) | `mcp-config.ts:45-70,93-103,105-110`; `mcp-config-inspection.ts:90-98`; `claude-profile-provisioning.ts:182-204`; `config-toml-mcp-servers.ts:9-41`; `bundled-skill-install-command.ts:177-185` | 4케이스 테스트: ① 파일 없음 ② 기존 서버 있음(보존) ③ 깨진 JSON(중단, 무변경) ④ 읽기 불가(중단, 무변경). `--dry-run` 후 실제 파일 바이트 불변 확인 |
| 4 | 권한 카탈로그를 실행 가능한 표로 승격: `talkabout/permissions.py`의 `PermissionSpec` 표에서 `domain.py` 집합·`api.py` 게이트·`docs/PERMISSIONS.md` 문구를 파생. 표에 없는 이름은 실패 | `[채택]` | M | `talkabout/permissions.py` 신규, `domain.py`, `api.py`, `docs/PERMISSIONS.md`, 테스트 | `plugin-host-api.ts:85-100,109-240,248-255`; `plugin-capability-gate.ts:24-51`; `plugin-capabilities.ts:6-33` | 표 ↔ 문서 ↔ 코드 3자 일치 테스트. 미지 권한 이름이 403/500이 아니라 명시적 거부가 되는지. 기존 `docs/PERMISSIONS.md:5` "유일한 카탈로그" 선언이 코드로 참이 되는지 |
| 5 | 누적 상태의 유한 수명화: `identity.py:153` operation quota에 `response_until` 기반 prune, `auth.py:42-57` 레이트리밋을 Postgres로, `auth.py:25` iteration 화이트리스트 | `[채택]` | S+M | `talkabout/identity.py:145-153,235`, `talkabout/auth.py:25,42-57` | `identity.py:74-100`(rolling ledger, "Known retired keys fail closed"); `saved-credential-readability.ts:56-58`(왕복 검증) | 2000건 초과 후에도 identity mutation이 동작(prune 후). 동시 로그인 실패 6회가 **다른 프로세스**에서도 429. iteration 변조 record가 거부됨 |

**우선순위 판단**: #2·#3이 비용 S로 가장 빨리 끝나고 TalkAbout에 없는 기능을 채운다. #1은 효과가 가장 크지만 **마스킹이 불변 버전 해시에 영향을 준다**는 TalkAbout 고유 제약을 먼저 설계해야 한다(§9.2 주의). #4는 문서-코드 drift라는 실제 위험(`docs/PERMISSIONS.md:3` "PROPOSED / NOT IMPLEMENTED")을 없애므로 별도로 착수할 가치가 있다.

---

## 11. 미확인 목록 (추정으로 메우지 않음)

| 항목 | 상태 |
| --- | --- |
| `src/main/{claude-usage,codex-usage,opencode-usage,usage,muse-usage}/` 계정별 사용량 분리 | **미확인**(디렉터리 존재만 확인, 파일 읽지 않음) |
| `src/main/codex/config-settings-promotion.ts`, `codex-config-mirror.ts` 전체 병합 규칙 | **부분 확인**(`mcpServers` 소유권 관련 라인만) |
| `src/main/ai-vault/` 340개 파일의 세션 인덱스 저장 암호화 여부 | **미확인**(파일명만 확인. 로컬 SQLite 인덱스로 보이나 단정 안 함) |
| `src/shared/git-credential-prompt-env.ts`(111줄) — 자식 git 프로세스에 토큰을 넘기는 방식 | **미확인**(파일 존재만 확인) |
| `orcad`(헤드리스) ↔ 데스크톱 봉투 마이그레이션 구현 | **미확인**(`encrypted-api-key-file-store.ts:14-15` 주석만) |
| `.mcp.json`이 Orca에 의해 생성되는지 | **미확인**(정적 파일만 확인, 쓰기 코드는 grep 전수에서 없음) |
| `orca.yaml`의 `environmentRecipes` 스키마 | **미확인**(이 저장소의 `orca.yaml`에는 `scripts.setup`만 존재) |
| TalkAbout `talkabout/store.py`(450줄) 내부의 이벤트 영속화 sink와 마스킹 부착 지점 | **미확인**(읽지 않음. §9.2 부착 4번 확정 필요) |
| TalkAbout `talkabout/domain.py`의 `HUMAN_PERMISSIONS`/`AGENT_PERMISSIONS`/`SELF_PERMISSIONS` 구성 | **미확인**(import만 확인, 정의부 읽지 않음) |
| TalkAbout MCP `resources` 지원 계획 | **미확인**(현재 `mcp.py:70` capabilities에 `tools`만 선언) |
| `src/main/local-builds/local-build-candidate.ts`(279줄) | **미확인**(파일 존재만 확인) |
| `src/main/ports/advertised-url-watcher.ts`(320줄) | **미확인**(파일 존재만 확인) |

---

## 12. 라이선스

Orca는 MIT(`orca-analysis/LICENSE`). 위 §9에서 제안한 것은 **개념·구조**이며, 코드를 그대로 복사할 경우 `observability/redactor.ts`(287줄), `crash-report-redaction.ts`(109줄), `saved-credential-readability.ts`(77줄), `mcp-server-inspection.ts`(139줄) 등에 대해 **저작자 표시 + MIT 라이선스 고지**가 필요하다. `mcp-config.ts`(208줄)와 `claude-profile-provisioning.ts`의 병합 규칙은 로직이 짧아 재작성이 자연스럽지만, 그 경우에도 원출처를 주석으로 남기는 편이 안전하다. 스킬 문서(`skills/`, `skill-guides/`)는 복사 대상이 아니다(§9.3은 구조만 차용).
