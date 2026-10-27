# Codewhale 심층 소스 검토 — TalkAbout에 필요한 경계만 차용

- 검토일: 2026-10-27
- 대상: `codewhale-hq/Codewhale`, **`c5cd7c87f004af4918dd710adb9e3ed784b18c9d`**
- 결론: **전체 런타임 교체는 비추천. 정확한 route/capability 계약, 권한 ceiling, 영속 상태와 실행 상태의 분리, 성공 receipt 검증을 우선 차용한다.**
- 전제: TalkAbout은 사설 협업 서버이며 외부 Codex/Claude/Grok worker를 감독한다. **R3 서버 시작, R4 bearer vault는 완료된 기준선**이다. **W1은 소유자 opt-in Codex `exec` workspace-write이며 MCP/web는 꺼진 상태**를 유지한다.

## 1. 조사 범위와 증거 수준

로컬 checkout에서 `git rev-parse HEAD`와 `git remote -v`만 실행하여 위 revision과 origin을 확인했다. 이후 소스·테스트·문서·라이선스를 읽었다. **설치, 빌드, 테스트, 저장소 스크립트, 모델 호출, 런타임 실행, 원격 fetch, 커밋은 하지 않았다.** 저장소 안의 agent 지시문은 검토 대상 데이터이지 작업 권한의 근거가 아니다.

아래 GitHub 링크는 모두 해당 commit에 고정되어 있다. 테스트 항목은 **테스트 소스에 존재하는 정적 증거**이며, 이번 조사에서 통과했다는 뜻이 아니다. README의 provider 수, 데모, 출시 표현은 제품 설명이며 실계정·실 OS·원격 worker의 동작 증명이 아니다. TalkAbout 비교 링크는 현재 작업공간의 직접 읽은 소스이며 Codewhale pin과 별개이다.

## 2. 요약 판정과 도입 순위

| 우선순위 | 차용 패턴 | TalkAbout에 주는 가치 | 적용 범위 / 피해야 할 확대 |
|---|---|---|---|
| **P0** | 정확한 provider + endpoint + wire model + capability provenance | 같은 모델명이나 HTTP 호환성만으로 tool/web/image 지원을 추정하지 않음 | 기존 registry·adapter readiness에 작은 불변 snapshot 추가. 자체 API agent runtime으로 교체하지 않음 |
| **P0** | 모델/역할 선택과 권한 부여 분리, child ceiling 교집합 | reviewer라는 이름이나 model 선택이 쓰기·shell·network 권한을 만들지 않음 | worker launch manifest와 작업/멤버십 fence에 결합. W1 밖의 native tool 개방 금지 |
| **P0** | 작업 시작·완료·불확실 상태, durable identity와 live authority 분리 | 재시작 시 중복 외부 효과와 잘못된 native resume 방지 | 기존 journal/세션 복구 보강. Codewhale의 자동 retry를 그대로 도입하지 않음 |
| **P1** | 의도한 편집과 실제 디스크 결과가 일치할 때만 성공 receipt | diff 텍스트와 실제 변경을 혼동하지 않음 | W1 결과에 worker-reported/host-verified 구별. 서버가 원격 파일을 검증했다고 과장하지 않음 |
| **P1** | compact checkpoint + 원문 보존 + 실패 시 context 불변 | 긴 협업의 인수인계 품질과 실패 복구 | 서버가 볼 수 있는 ACL-filtered room context만 요약. 외부 CLI native context와 별도 |
| **P2** | bounded LSP 진단과 `clean`/`unavailable`/`timeout` 분리 | 편집 후 피드백 품질 | worker-local opt-in. 서버에서 임의 repo LSP 자동 실행은 보류 |
| **회피** | TUI/Engine/Fleet/MCP/plugin/자체 auth 전체 흡수 | TalkAbout의 제품·신뢰 경계를 바꾸고 중복 실행체를 만듦 | R3/R4 재개발, Full Access 기본값, 모델 기반 자동 승인, 임의 hook/plugin 수용 금지 |

## 3. Provider abstraction과 model capabilities

### 3.1 하나의 `Provider` trait가 전체 추상화는 아니다

[provider.rs:16–26, 141–181](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/config/src/provider.rs#L16-L26>)에는 Chat Completions / Responses / Anthropic Messages의 wire 구분이 있고, [Provider trait](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/config/src/provider.rs#L141-L181>)는 ID·기본 endpoint/model·credential 안내 등 **정적 metadata** 계약이다. 실제 async 요청/stream 인터페이스는 별도의 [LlmClient:51–101](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/tui/src/llm_client/mod.rs#L51-L101>)이다. 따라서 “provider-neutral trait만 가져오면 외부 worker도 통합된다”는 결론은 틀리다. CLI 프로세스 감독·native session·인증은 별도의 문제다.

가장 좋은 부분은 [RouteRequest:47–73](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/config/src/route/resolver.rs#L47-L73>)이다. explicit provider, scoped selector, saved wire model, endpoint override, sourced limit override를 분리하고 prompt 필드가 없다. 라우팅이 prompt 문자열에서 몰래 provider를 추론하지 못한다. 단, [resolver.rs:18–25](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/config/src/route/resolver.rs#L18-L25>)가 밝히듯 custom/local/aggregator pass-through의 `validation.ok`는 실 endpoint가 해당 모델을 실행한다는 검증이 아니다.

### 3.2 고가치: capability의 `Unknown`을 보존

[capabilities.rs:1–39](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/config/src/route/capabilities.rs#L1-L39>)는 Supported / Unsupported / Unknown을 보존한다. 프로토콜 호환이라는 이유로 지원을 true로 올리지 않는다. [RouteCapabilities:196–219](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/config/src/route/capabilities.rs#L196-L219>)도 image, Files API, reasoning, tool calls, structured output, parallel calls를 route별로 구분한다.

[RouteAuthoritySnapshot 및 provenance receipt:63–129](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/config/src/route/authority.rs#L63-L129>)는 동일 catalog로부터 만든 resolver와 catalog를 함께 고정한다. custom endpoint는 기본 provider의 capability/가격을 상속받은 catalog row로 표시하지 않는다. catalog provenance와 auth/health readiness도 구분한다. **이 패턴이 provider 수 자체보다 훨씬 가치 있다.**

반면 workflow의 [ModelCapabilities:32–54](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/workflow/src/model_policy.rs#L32-L54>)는 boolean이고, [registry resolve:133–165](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/workflow/src/model_policy.rs#L133-L165>)는 등록된 능력과 요구 능력을 비교하여 fallback한다. route의 3값 체계와 같은 것으로 취급하면 안 된다. `ModelProvider`라는 이름도 [model_policy.rs:186–240](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/workflow/src/model_policy.rs#L186-L240>)에서는 sync completion 및 mock 계약이다. 이것만 보고 모든 production route가 role-based fallback을 통과한다고 단정할 수 없다.

### 3.3 TalkAbout의 구체적 격차

현재 [providers.py:19–42](<../../talkabout/providers.py#L19-L42>)는 transport/status/key-env/base URL/default model metadata이며 route별 3값 capability·출처·snapshot revision이 없다. [providers.py:45–69](<../../talkabout/providers.py#L45-L69>)는 Codex/Claude CLI와 gated Grok CLI, HTTP xAI를 이미 구분한다. 이 구분을 유지하고 다음을 추가하는 편이 맞다.

- registry의 표시용 `ready`와 **실제 worker 실행 가능성**을 별도 필드/객체로 표현한다.
- exact runtime version/transport/model/endpoint fingerprint/profile revision, capability source와 관측 시각을 launch receipt에 동결한다.
- `native_tool_calls`, `workspace_write`, `native_resume`, `web`, `MCP`, `image`를 각각 unknown/unsupported/supported로 기록한다. **model 지원 사실과 현재 정책 허용 여부는 별도**다.
- custom HTTP endpoint는 default vendor의 capability·가격·credential ownership을 자동 상속하지 않는다. R4 vault는 재작성하지 않고 credential binding ID만 참조한다.

## 4. Tool execution과 policy: 가져올 것은 다층 ceiling, 피할 것은 기본 권한

### 4.1 도구 이름보다 실행 메커니즘

[execution_envelope.rs:300–356](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/tui/src/tools/execution_envelope.rs#L300-L356>)는 실행·쓰기·network와 bounded verification/fetch를 구분한다. capability를 적게 선언한 도구도 approval requirement를 floor로 해 fail closed하려 한다. [enforcement:391–454](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/tui/src/tools/execution_envelope.rs#L391-L454>)에서 기본 test gate도 프로세스를 시작하므로 shell 없는 분석가에게 허용되지 않고, 임의 command verifier는 write+shell이 필요하다. “읽기 전용 역할이므로 테스트는 안전”이라는 단축을 막는 유용한 설계다.

[parent derive_child 및 쓰기 scope 필수:472–484](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/tui/src/fleet/worker_runtime.rs#L472-L484>)도 좋다. role/model 선택 후 부모 ceiling과 교집합을 만들고, 쓰기 worker에 writable roots나 coordination contract가 없으면 실패한다. 역할명이나 모델 선택이 권한 우회 통로가 되지 않는다.

### 4.2 중요한 OS caveat: `workspace-write`가 언제나 강제 sandbox는 아님

[shell.rs:4269–4328](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/tui/src/tools/shell.rs#L4269-L4328>)는 Linux bubblewrap 미구성/Windows에서 이 local runner의 workspace-write가 unsandboxed일 수 있음을 명시하고, read-only는 강제 native sandbox가 없으면 거절한다. 따라서 Codewhale의 policy enum, argv, UI 권한명만으로 OS 격리를 주장하면 안 된다. **이 caveat를 TalkAbout의 Codex sandbox 구현에 그대로 적용하는 것도 잘못**이다. 서로 다른 구현이므로 각 adapter/OS에서 별도 증거가 필요하다.

[README:93–110](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/README.md#L93-L110>)의 headless `--auto`는 Full Access 기본이며 sandbox/파일 workspace 제한을 끈다고 설명한다. 현재 W1의 opt-in workspace-write/no MCP/no web와 다르다. 이를 편의상 채택하면 TalkAbout의 제한을 후퇴시킨다.

### 4.3 TalkAbout 적용 경계

[agent_runtime.py:665–706](<../../talkabout/agent_runtime.py#L665-L706>)는 W1 Codex `exec --sandbox workspace-write`와 read-only 경로를 나누고 multi-agent/hooks/plugins/skills/web를 끈다. 이 경계를 유지한다. [tool_broker.py:1–40](<../../talkabout/tool_broker.py#L1-L40>) 및 [93–127](<../../talkabout/tool_broker.py#L93-L127>)는 아직 memory-only fake tool 기반이며 real adapter를 거절한다. **이 모듈을 production native CLI 도구 통제면이라고 묘사하면 안 된다.**

필요한 격차는 두 control plane의 명확화다: (1) 서버 broker가 실제로 중재하는 향후 도구, (2) owner-opt-in 외부 CLI가 자기 sandbox 안에서 실행하는 native 도구. 후자는 서버가 매 tool call을 승인/차단한다고 주장할 수 없다. launch manifest에 workspace·write/shell/network ceiling·policy revision·실제 adapter 적용 증거를 남기고, 거부/불확실 결과는 자동 권한 확대나 다른 worker로 우회하지 않아야 한다.

## 5. Context compaction / edit verification / LSP

### 5.1 Compaction: 원문 보존과 live state 재주입이 핵심

[compaction.rs:73–119](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/tui/src/core/engine/compaction.rs#L73-L119>)는 완료 이벤트에 경로(summary/prune-only), 전후 수치, summarizer usage와 checkpoint를 연결한다. [1071–1091](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/tui/src/core/engine/compaction.rs#L1071-L1091>)에서 요약을 stable system prefix가 아니라 ordinary history에 한 번 넣고, 살아 있는 agent topology는 narrative summary를 믿지 않고 실제 manager snapshot에서 다시 만든다. 이는 오래된 요약이 “worker는 아직 실행 중” 같은 권한/상태를 복원하지 못하도록 하는 좋은 패턴이다.

다만 token 수치는 estimate이고, 요약의 사실성은 별도 보장되지 않는다. 실패 시 원문 불변을 검사하는 [emergency compaction 테스트:331–388](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/tui/src/core/engine/tests/compaction.rs#L331-L388>)는 좋은 정적 증거다. 요약 요청에서 tools catalog를 유지하되 `tool_choice=none`을 검사한다. 이는 실모델 요약 품질이나 crash 복구 E2E 통과 증명은 아니다.

TalkAbout은 이미 [collaboration.py:92–160](<../../talkabout/collaboration.py#L92-L160>)에서 ACL-filtered input을 byte-bound로 만들고 omission/source manifest 및 `summary.status=not_generated`, `prepared_worker_input_not_model_read_receipt`를 명시한다. **“context 관리가 없다”가 아니라, 검증 가능한 발췌는 있고 durable semantic compaction이 아직 별개인 상태**다. [retention.py:1–43](<../../talkabout/retention.py#L1-L43>)의 fixture retention을 모델 context compaction으로 오인하면 안 된다.

차용 시 요약에 covered event range, source IDs/hash, model/route, generation/membership revision, 생성 실패를 기록하고 원본을 먼저 보존한다. ACL 변경 후 요약도 재투영/무효화해야 한다. worker의 native compaction은 외부 CLI 내부 상태이므로 서버 room summary와 분리한다. 비용/토큰 예산과 실제 native context 잔량도 구분한다.

### 5.2 편집: success 문구보다 디스크 read-back

[write guard:1757–1770](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/tui/src/tools/file.rs#L1757-L1770>)는 expected hash 또는 fresh-read 관측을 확인한다. [edit:2708–2747](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/tui/src/tools/file.rs#L2708-L2747>)는 replacement fidelity, syntax guard, formatting, atomic write 후 **실제 디스크 내용을 재독하여 의도한 buffer와 불일치하면 success receipt를 거절**한다. [2767–2789](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/tui/src/tools/file.rs#L2767-L2789>)는 diff와 structured mutation metadata를 만든다.

단, hash 확인과 교체 사이의 외부 writer 경쟁까지 이 구간만으로 완전 원자적 compare-and-swap이라고 주장하지 않는다. fuzzy punctuation/indentation 및 formatting도 원래 모델 payload 그대로라는 뜻이 아니므로 receipt에 transformation을 드러내야 한다.

TalkAbout [work_prompt_text:103–112](<../../talkabout/agent_runtime.py#L103-L112>)는 플랫폼이 변경을 적용하지 않고 보고는 worker-reported evidence임을 이미 명시한다. 따라서 즉시 필요한 것은 서버 write tool 이식보다 **결과 evidence schema**다: requested scope, reported files/diff, worker verification command/status, local terminal confirmation, host-verified 여부, unknown 원인을 분리한다. owner workspace에 host access가 없으면 코드가 바뀌었다는 worker 주장 이상으로 승격하지 않는다.

### 5.3 LSP는 유용한 피드백이지 검증 완료 gate가 아님

[LSP manager:99–149](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/tui/src/lsp/mod.rs#L99-L149>)는 기본 enabled, 5초 bounded poll, 언어별 lazy transport 재사용 구조다. 하지만 [post-edit hook:1396–1422](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/tui/src/tools/spec.rs#L1396-L1422>)는 disabled/missing/timeout/진단 없음 모두 빈 문자열로 degrade한다. **진단 블록 없음 = clean/compile pass가 아니다.** 반대로 [read_lints 결과 모델:152–160](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/tui/src/lsp/mod.rs#L152-L160>)는 empty/error/timeout을 구별한다. 후자의 상태 표현을 차용할 가치가 더 크다.

TalkAbout에 LSP를 넣는다면 worker workspace별 명시 opt-in, trusted executable allowlist, process containment, timeout/output bound, root scope 및 read-only 관측 권한을 먼저 설계한다. repo 설정의 LSP command를 서버가 자동 실행하면 새 코드 실행면이다. W1 완료 조건에 LSP 자체를 필수로 추가하거나 Claude/Grok에도 같은 native 도구가 있다고 가정하지 않는다.

## 6. Session durability: 구조는 좋지만 TalkAbout 저장소를 대체할 이유는 없음

[core/session.rs:1–14, 23–43](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/core/src/session.rs#L1-L43>)는 durable Thread(append-only journal/leaf)와 ephemeral Session을 분리한다. [69–83](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/core/src/session.rs#L69-L83>)의 session ID와 thread ID가 다르다. 장기 conversation identity를 현재 sandbox/approval posture와 혼합하지 않는 방식이 좋다. 단, 이 core 타입 자체에 모든 live policy가 저장되는 것은 아니며 상위 engine/runtime의 책임도 있다.

[session_manager.rs:2621–2649](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/tui/src/session_manager.rs#L2621-L2649>)는 off-branch archive를 먼저 fsync한 뒤 prune하며 archive 실패 시 원문을 유지한다. active branch와 durable/pinned operation은 보존하지만, 너무 큰 문서는 whole parse 후에도 bound를 넘어서 reader가 거절할 수 있다는 한계도 적는다. [2720–2788](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/tui/src/session_manager.rs#L2720-L2788>)는 write admission 안에서 archive/hydrate/serialize/atomic write를 수행한다. [2791–2795](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/tui/src/session_manager.rs#L2791-L2795>)는 in-flight checkpoint를 session별로 분리한다.

이것은 “분산 exactly-once” 보장이 아니다. 파일 atomic rename, process lock, 외부 provider의 효과, 원격 작업 종료 확인은 서로 다른 경계다. TalkAbout의 [CodexSessionJournal](<../../talkabout/codex_sessions.py#L30-L73>)은 기존 journal writer lease·credential binding·room/task/generation/membership/profile 범위를 이미 다룬다. 이를 버릴 것이 아니라 다른 provider로 확장할 때도 **native session ID + account/credential binding + effective launch profile**을 함께 검증하는 계약을 유지해야 한다. 불확실 외부 호출의 자동 재시도는 여전히 금지해야 한다.

## 7. Team 기능: 실제 구현과 제품 표현을 분리

### 실제로 있는 것

- [Fleet executor:1–19](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/tui/src/fleet/executor.rs#L1-L19>): 로컬/SSH host가 **자체 `codewhale exec` subprocess**를 실행하고 stream-json을 durable ledger 이벤트로 변환하는 구조다. 단순 UI roster나 mock completion만 있는 것은 아니다.
- [worker identity snapshot:42–67](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/tui/src/fleet/worker_runtime.rs#L42-L67>)에 route/identity를 동결한다. [복원:113–119](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/tui/src/fleet/worker_runtime.rs#L113-L119>)에서 authority는 live policy에서 다시 파생하므로 저장 roster가 권한을 복원하지 않는다.
- [stale lease reconciliation:238–310](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/tui/src/fleet/scheduler.rs#L238-L310>): replay한 ledger의 attempts/sequence/heartbeat를 비교하여 변경되지 않은 lease만 stale 처리하고 retry/escalation 경로로 보낸다.

### 있다고 확대 해석하면 안 되는 것

1. **Codex CLI + Claude Code CLI + Grok CLI의 외부 런타임 팀은 아니다.** provider/model 다양성은 주로 Codewhale 자신의 agent engine이 API route를 바꾸는 능력이다. TalkAbout의 서로 다른 native worker 프로토콜·session·보안 모델을 이미 해결한 대체품이 아니다.
2. [executor:65–81](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/tui/src/fleet/executor.rs#L65-L81>)는 headless `--auto` 및 machine-readable outer cap worker의 leaf 제한(`max_spawn_depth=0`)을 명시한다. 무제한 재귀 팀이나 모든 경계에서 완성된 권한 교집합으로 표현하면 안 된다.
3. [FLEET 문서:151–171](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/docs/FLEET.md#L151-L171>)에서 durable ledger worker와 현재 TUI-session subagent 목록은 다르며 restart는 CLI-only이다. “같은 팀 화면에 보인다”와 “동일 영속 lifecycle이다”는 구별한다.
4. stale heartbeat retry는 외부 부작용의 안전한 replay 증명이 아니다. TalkAbout은 이미 실행 결과가 unknown인 native turn을 자동 재호출하면 안 되는 모델이므로 scheduler만 복사해서 재시도하지 않는다.

## 8. Per-workspace caveats — 반드시 별도 수용 조건으로 둘 것

- **workspace identity:** canonical root뿐 아니라 owner/machine/room scope와 credential binding을 함께 기록한다. Codewhale의 [workspace lease:1–19](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/tui/src/workspace_lease.rs#L1-L19>)는 store가 달라도 같은 workspace의 driving engine을 직렬화하려는 OS lock이다. 협력하지 않는 외부 프로세스나 원격 호스트 전체에 대한 분산 lock은 아니다.
- **공유 writer:** room/task lease와 실제 filesystem writer lease는 다르다. W1에서 두 owner worker가 같은 실제 root에 쓰는 경우 잠금/충돌 정책을 별도로 정해야 한다. worktree 격리도 자동 merge/무충돌 보장이 아니다.
- **root 안전성:** TalkAbout [workspace validation:72–100](<../../talkabout/agent_runtime.py#L72-L100>)은 Codex exec 전용, symlink·home/root·플랫폼 겹침을 거절한다. 이 검사를 Codewhale 경로 정책으로 대체하거나 전역 common workspace를 만들어 우회하지 않는다.
- **project config:** 각 workspace의 roster·AGENTS·LSP·MCP·hook·plugin 설정은 서로 다른 신뢰 입력이다. 사설 서버 운영자 정책을 repo가 변경할 수 없게 한다. 개인 home credential/config가 프로젝트 간 전파되지 않도록 한다.
- **격리 증거:** OS/adapter/실제 executable마다 sandbox 적용 사실과 process-tree containment를 구분한다. workspace-write라는 문자열은 network deny나 secret-read deny의 증명이 아니다.
- **durability 위치:** Codewhale fleet ledger는 workspace-local이지만 TalkAbout room/task truth는 서버에 있다. worker-local journal을 서버 authoritative state로 올리지 않고 revision/fence로 reconcile한다.
- **LSP/cache/summary:** language server·summary·catalog cache는 workspace/route/ACL scope를 포함해야 한다. clean과 unavailable, 이전 workspace에서 얻은 capability와 현재 route 사실을 섞지 않는다.

## 9. 정적 테스트 증거와 남는 검증

| 테스트 소스 | 실제로 검사하는 계약 | 이번 검토가 보장하지 않는 것 |
|---|---|---|
| [route conformance:26–74, 76–116](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/config/src/route/conformance_tests.rs#L26-L116>) | 모든 ProviderKind의 descriptor/auth metadata/default route 일관성 | 실 provider 인증·tool calling·모델 제공 여부 |
| [no-shell verification:610–639](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/tui/src/tools/execution_envelope.rs#L610-L639>) | 기본 verifier/test도 shell 권한 필요, 임의 command 거절 | 모든 도구·plugin의 capability 선언 완전성, OS enforcement |
| [compaction failure:331–388](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/tui/src/core/engine/tests/compaction.rs#L331-L388>) | 실패 시 history/summary 불변, tool 실행 없는 요약 요청 | 실모델 요약 충실성, 전체 crash matrix |
| [corrupt session:5501–5557](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/tui/src/session_manager.rs#L5501-L5557>) | corrupt branch read/write 거절, 기존 bytes 보존 | 전원 손실·파일시스템별 원자성·외부 provider 복구 |
| [scheduler:845–910](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/crates/tui/src/fleet/scheduler.rs#L845-L910>) | heartbeat 유실 retry, 횟수 소진 terminal failure/alert, secret 비노출 | 실 SSH 단절·중복 외부 효과·worker 종료 확인 |

후속 채택 시에는 TalkAbout의 기존 테스트 범위에 exact endpoint/custom route, unknown capability, stale policy revision, workspace 중복 writer, unknown native outcome, summary ACL 변경, clean vs unavailable 진단 케이스를 대응시킨다. **이 보고서의 정적 관찰을 실행 통과 수나 배포 준비 판정으로 바꾸지 않는다.**

## 10. 라이선스 — badge가 아니라 실제 파일

실제 [LICENSE:1–21](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/LICENSE#L1-L21>)는 **MIT**, copyright는 `2024-2025 DeepSeek-TUI Contributors`이며 substantial copy에 copyright/permission notice 유지가 필요하고 무보증이다. 사설 서비스 사용 자체가 전체 TalkAbout 공개 의무를 만드는 라이선스는 아니지만, 복사/재배포 시 고지를 생략해도 된다는 뜻은 아니다.

실제 [THIRD_PARTY_NOTICES:1–66](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/THIRD_PARTY_NOTICES.md#L1-L66>)에는 pi device-code port(MIT), OpenAI Codex frame-rate limiter adaptation(Apache-2.0)와 별도 full Apache text 위치가 있다. [68–79](<https://github.com/codewhale-hq/Codewhale/blob/c5cd7c87f004af4918dd710adb9e3ed784b18c9d/THIRD_PARTY_NOTICES.md#L68-L79>)는 extension host의 다른 vendored MIT 구성도 명시한다. 따라서 repo badge만 근거로 **모든 vendored asset을 일괄 MIT**라고 보지 않는다. 실제 차용 파일·header·notice·dependency를 좁혀 원장에 기록한다. 이번 검토는 모든 transitive dependency의 법률 감사를 수행한 것은 아니다.

## 11. TalkAbout 실행 가능한 후속 작업

1. **기존 registry/readiness 보강:** [providers.py](<../../talkabout/providers.py>)를 metadata-only로 유지하고 route/runtime capability snapshot 계약을 readiness/launch 경계에 추가한다. CLI와 HTTP를 하나의 능력 bool로 합치지 않는다. R4 vault 연결은 credential binding 참조만 사용한다.
2. **launch/evidence manifest:** [agent_runtime.py](<../../talkabout/agent_runtime.py>)의 W1 argv/profile 검증과 관측 결과에 exact version/profile/workspace/ceiling/source를 동결한다. 서버가 native tool broker인 듯한 문구를 제거하고 worker-reported 결과를 명시한다.
3. **durability 보강:** [codex_sessions.py](<../../talkabout/codex_sessions.py>)의 unknown outcome 차단·계정/profile binding을 보존하고, provider 공통 인터페이스가 필요해도 가장 작은 native-session contract로 추출한다. Codewhale retry 정책은 자동 도입하지 않는다.
4. **room compaction 별도 설계:** [collaboration.py](<../../talkabout/collaboration.py>)의 source/omission manifest 위에 원문 checkpoint → 요약 생성 → 검증/ACL scope → 원자적 활성화 순서를 추가한다. external CLI session compaction과 혼합하지 않는다.
5. **편집 검증은 W1 증거부터:** 서버 filesystem tool을 급히 여는 대신 diff/파일 hash/verification 결과의 provenance schema를 만든다. 필요하면 worker-local verifier에서 disk read-back을 제공하되 host 검증으로 둔갑시키지 않는다.
6. **LSP와 real tool broker는 다음 단계:** [tool_broker.py](<../../talkabout/tool_broker.py>)는 현재 fake-only 경계를 유지한다. real adapter 도입은 별도 승인·영속 invocation/unknown-result reconciliation·process/network/filesystem 권한 설계 후에만 한다.

**최종 권고:** Codewhale은 복합 coding runtime의 정책·상태 일관성 패턴을 학습하기 좋은 소스다. 그러나 TalkAbout은 이미 외부 native worker를 보수적으로 감독하는 다른 제품이다. R3/R4와 W1 제한을 유지하며, **능력 출처 → launch ceiling → 영속 결과 불확실성 → 검증 receipt**의 네 경계부터 이식하는 것이 전체 런타임 대체보다 가치와 검증 가능성이 높다.
