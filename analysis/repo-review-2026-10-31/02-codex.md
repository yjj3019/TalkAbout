# Codex 정적 소스 검토 — A0 통합 이후 최신 계약 중 미반영분

- 기준일: 2026-10-31. 대상: `openai/codex`, 고정 커밋 `67f0994b532b8740f14b5b738af7b7b2a915b1d6`.
- 대조 기준: TalkAbout main `0da71a8e519f4dc7591b9600f316d4f087f503b4`. TalkAbout은 A0에서 **Codex exec-json(0.159.2 기준, 0.164.0 관찰 보정)** 을 통합했고, app-server는 별도 opt-in(text-only)이다([agent_runtime.py](../../talkabout/agent_runtime.py#L6-L15)).
- **정적 분석만 수행**: GitHub contents API로 소스·JSON Schema·테스트·문서·라이선스 파일을 읽었다. clone, Rust 빌드, 테스트 실행, `codex` 바이너리 실행, 공급자·계정 호출 없음. 저장소의 지침·설치 명령은 신뢰하지 않는 데이터로만 취급했다. 코드 복사 없음.
- TalkAbout의 **R3 one-shot start / uncertain no-retry, R4 bearer-only vault, MCP/hooks/plugins/web 기본 차단, 서버가 모델을 실행하지 않음**은 불변 조건이다. 아래 제안은 이를 확장·재해석하거나 공급자 exactly-once를 주장하는 것이 아니다.
- 이 문서는 “최신 Codex가 좋다”는 근거가 아니다. **TalkAbout이 이미 통합한 계약이 pinned HEAD에서 어떻게 달라졌고, 무엇이 아직 반영되지 않았는지**를 fail-closed 관점에서 정리한 것이다. 모든 실측은 NOTRUN(4장).

## 요약 판정

Pinned HEAD에서 TalkAbout의 A0 관찰 계약과 **충돌하는 변경은 확인되지 않았다**. exec JSONL의 `thread.started`/`turn.completed`/`item.*` 이벤트 이름과, TalkAbout이 exact-key로 검증하는 `command_execution`·`file_change`·`todo_list` item 필드는 HEAD와 여전히 일치한다. `thread.started.thread_id`, `turn.failed.error.message`도 그대로다.

그러나 **버전 결합 지점 세 곳**이 실제로 움직였다. ① exec JSONL item 표면이 확장됐다(신규 `mcp_tool_call`/`collab_tool_call`/`web_search`/`error` item, `usage.cache_write_input_tokens`) — TalkAbout의 exact-key 거절은 이들을 만나면 fail-closed로 턴을 실패시키므로, “기능을 껐으니 나오지 않는다”는 가정의 검증이 필요하다. ② exec CLI가 새 전역 flag(`--ephemeral`, `--output-schema`, `--output-last-message`, `--thread-source`, `--cyber-access-program`)과 하위명령(`fork`, `review`)을 얻었고, 일부 `features.*` override는 **removed/no-op 호환 flag**가 되었다. TalkAbout의 help flag fingerprint는 “flag 존재”만 보므로 no-op 전환을 감지하지 못한다. ③ app-server 승인·권한 모델이 확장됐다(`approvalsReviewer`의 auto_review, granular `AskForApproval`, 풍부한 approval decision, `permissions` 응답의 필수 `GrantedPermissionProfile`). TalkAbout은 여전히 `decline`/`{}` 고정 응답을 보내며, 그 응답이 현행 스키마에서 유효한지 검증되지 않았다.

따라서 우선순위는 새 기능 도입이 아니라 **A0 계약의 버전 드리프트 감지와 fail-closed 응답 형태 검증**이다. exec-server·auto-review·remote sandbox는 TalkAbout의 서버-비실행 불변 조건과 W3 보류에 비추어 **도입 대상이 아니며**, 계약 어휘만 참고한다.

## 1. 핵심 발견: 실제 구현과 적용점

### F1 — exec JSONL item 표면 확장: A0의 exact-key는 유지되지만 “나올 수 있는 item”이 늘었다 (최우선)

HEAD의 `ThreadEvent`는 `thread.started`·`turn.started`·`turn.completed`·`turn.failed`·`item.started`·`item.updated`·`item.completed`·`error`로 TalkAbout이 허용하는 집합과 동일하다. `ItemCompletedEvent`의 `ThreadItemDetails`는 `agent_message`·`reasoning`·`command_execution`·`file_change`·`mcp_tool_call`·`collab_tool_call`·`web_search`·`todo_list`·`error`로 확장됐다. `command_execution`은 `{command, aggregated_output, exit_code, status}`(status: in_progress/completed/failed/declined), `file_change`는 `{changes:[{path,kind}], status}`, `todo_list`은 `{items:[{text,completed}]}`로 TalkAbout의 exact-key와 일치한다. 반면 `turn.completed.usage`에는 `cache_write_input_tokens`가 추가됐고(serde default), `error` event는 `{message}` 단일 필드다.

- 구현: [ThreadEvent 전체](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/exec/src/exec_events.rs#L8-L37>), [thread/turn/usage](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/exec/src/exec_events.rs#L39-L73>), [ThreadItemDetails 전체](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/exec/src/exec_events.rs#L96-L133>), [command/file_change/todo/error item 필드](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/exec/src/exec_events.rs#L148-L324>).
- TalkAbout 현재 계약: [work item exact-key·상한·상태 전이](<../../talkabout/agent_runtime.py#L46-L115>), [exec 파서의 item/terminal 처리](<../../talkabout/agent_runtime.py#L1026-L1131>). TalkAbout은 work profile에서 `command_execution`/`file_change`/`todo_list` 외 item type을 `runtime_tool_policy_violation`으로 거절한다([해당 분기](../../talkabout/agent_runtime.py#L1104-L1110)).
- 증거 한계: HEAD 소스에서 새 item type이 **어떤 feature gate로만 생성되는지** 전수 추적하지 않았다. TalkAbout이 `features.multi_agent=false`, `web_search="disabled"`, MCP·plugin 관련 false를 넣는다는 사실은 확인했지만([command 조립](../../talkabout/agent_runtime.py#L754-L789)), HEAD에서 그 override가 실제로 해당 item 생성을 막는지는 실행 검증하지 않았다(NOTRUN). `--json` 출력이 HEAD에서 동일 wire 형태(JSONL/stdout)인지는 CLI 코드와 event 타입만 확인했고 실제 실행하지 않았다.
- TalkAbout 적용: A0 parser를 “허용 item = 정확히 이 세 종류 + agent_message/reasoning”으로 문서·고정 fixture에 명시하고, **새 item type은 거절이 아니라 ‘정책 위반’으로 분류해 turn을 uncertain으로 종료**하는 현재 의미를 테스트로 고정한다. 아울러 exec 스키마 fixture를 버전 태그와 함께 보관해, provider 업그레이드 시 “거절되는 새 item” 목록이 리뷰 가능한 diff로 드러나게 한다. **provider 호출 없이 synthetic stream으로만** 검증한다.

### F2 — exec CLI 표면 확장과 하위명령: “flag 존재” fingerprint로는 의미 변화를 못 잡는다

HEAD `codex exec`는 `--strict-config`, `--skip-git-repo-check`, `--ephemeral`(세션 파일 미저장), `--ignore-user-config`, `--ignore-rules`, `--output-schema`, `--json`(alias `--experimental-json`), `--output-last-message`, `--thread-source`, `--cyber-access-program`을 전역으로 받고, 하위명령으로 `resume`/`fork`/`review`를 갖는다. TalkAbout은 `exec --help`와 `exec resume --help`에서 필요한 flag의 존재만 확인하고, 없으면 `runtime_version_unsupported`로 거절한다.

- 구현: [exec Cli 구조와 전역 옵션](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/exec/src/cli.rs#L10-L86>), [resume/fork/review 하위명령](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/exec/src/cli.rs#L172-L330>).
- TalkAbout 현재 계약: [help flag 검증 집합](<../../talkabout/agent_runtime.py#L668-L749>), [명령 조립](<../../talkabout/agent_runtime.py#L754-L789>). TalkAbout은 `--approve-for-me` 같은 자동 승인 flag를 **전달하지 않으므로** 조립 경로는 안전하다. 그러나 `_CONFIG_OPTIONS` 화이트리스트([agent_runtime.py](../../talkabout/agent_runtime.py#L125-L140))와 help fingerprint는 “새 flag가 생겼다”를 감지하지 않는다(설계상 존재 확인만).
- 증거 한계: HEAD에서 `codex exec`의 기본 approval policy/sandbox 값이 바뀌었는지(config 기본값) 소스 전체를 추적하지 않았다. `--ephemeral`이 resume 가능성에 미치는 영향도 실행 확인하지 않았다. TalkAbout이 설치된 CLI 0.159/0.164에서 이 HELP 집합이 그대로 통과하는지는 이번 고정 SHA와 다른 버전이므로 여기서 검증할 수 없다.
- TalkAbout 적용: 후보는 **capability fingerprint 확장**이다. (1) 현재 필수 flag 집합에 “자동 승인·우회 계열 flag가 존재하면 경고/차단” 규칙을 추가(예: `--approve-for-me`, `--dangerously-bypass-approvals-and-sandbox` 존재 감지), (2) `--json` alias 변경 감지, (3) resume 하위명령 형태(`resume <id>` vs `resume --last`) 고정. 전부 **로컬 `--help` 파싱과 synthetic parser 테스트**로 검증 가능하며 모델 호출이 필요 없다. 단, “차단했다”는 표현은 flag를 넘기지 않는다는 의미로 한정하고 sandbox 강제를 주장하지 않는다.

### F3 — config override가 조용히 no-op이 될 수 있다: `features.*` removed flag와 `web_search` 이동

HEAD features 목록에는 TalkAbout이 false로 넣는 이름들이 여전히 존재하지만, 일부는 **“removed compatibility flag retained as a no-op”** 이다. `ToolSearch`는 “no-op now that tool_search is always enabled”으로 표시되고, `PluginHooks`는 “Removed compatibility flag for plugin-bundled lifecycle hooks”다. 반면 `CodexHooks`·`Plugins`·`Apps`·`ViewImage`·`ShellTool`·`UnifiedExec` 등은 실 flag다. 또 legacy `web_search` 키는 `[features].web_search`로 매핑되는 경로가 있다. 즉 `-c features.tool_search=false`가 **에러 없이 무시**될 수 있고, `--strict-config`는 “알려진 removed flag”를 알 수 없으므로 이를 잡지 못한다.

- 구현: [Feature enum과 removed/no-op 주석](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/features/src/lib.rs#L101-L201>), [ToolSearch no-op·PluginHooks removed](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/features/src/lib.rs#L285-L325>), [ShellTool/ViewImage/CodexHooks/UnifiedExec](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/features/src/lib.rs#L109-L185>), [legacy web_search 키 매핑 언급](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/features/src/lib.rs#L840-L852>).
- TalkAbout 현재 계약: `--ignore-user-config`, `--strict-config`, `--ignore-rules` + 다수 `-c features.*=false`([command 조립](../../talkabout/agent_runtime.py#L754-L789)). `--strict-config`는 “알 수 없는 config 필드에서 에러”이므로 **removed flag는 통과**한다.
- 증거 한계: `-c features.tool_search=false`를 실제 CLI에 넣어 무시되는지 실행하지 않았다. features crate의 legacy 처리 전체(어떤 키가 aliasing되는지, 경고만 하는지)를 끝까지 읽지 않았다. TalkAbout이 쓰는 정확한 key 문자열과 HEAD `legacy.rs` 매핑의 일대일 대조도 완료하지 않았다(추정 금지).
- TalkAbout 적용: 후보는 **override 실효성 검사 계층**이다. (1) 버전별 “실효 flag / no-op flag” 표를 문서로 두고, no-op으로 확인된 key는 command에서 제거하고 그 사실을 진단에 남긴다. (2) 가능하면 app-server `config/read`류로 **적용된 config를 읽어** 기대값과 대조한다(단, 이는 provider 프로세스와 RPC이므로 별도 예산·승인 필요, 현재 NOTRUN). (3) `--strict-config` 실패는 fail-closed로 유지한다. “기능을 껐다”는 주장은 **명령에 넣었다**가 아니라 **provider가 확인해 줬다**로만 승격한다.

### F4 — 승인·권한 모델 확장: TalkAbout의 고정 거절 응답이 현행 스키마에서 유효한지 불명

HEAD app-server는 `approvalsReviewer`(user/auto_review/guardian_subagent — “auto_review는 위험 기반 결정을 하는 subagent”)를 도입했고, `AskForApproval`은 `untrusted|on-request|never` 또는 granular 객체다. command approval decision은 `accept|acceptForSession|acceptWithExecpolicyAmendment|applyNetworkPolicyAmendment|decline|cancel`로 늘었고(`cancel`은 turn 즉시 중단), permissions 승인 응답은 `permissions: GrantedPermissionProfile`과 `scope`를 **필수**로 요구하며 `strictAutoReview` 선택 필드가 있다. TalkAbout은 command/fileChange 요청에 `{'decision':'decline'}`, permissions 요청에 `{'permissions':{}, 'scope':'turn'}`, elicitation에 `{'action':'decline'}`을 보낸다.

- 구현: [ApproveReviewer 설명·enum](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/app-server-protocol/schema/json/v2/ThreadResumeParams.json#L52-L60>), [AskForApproval 정의(ThreadResumeParams)](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/app-server-protocol/schema/json/v2/ThreadResumeParams.json#L1607-L1702>), [command 승인 decision enum](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/app-server-protocol/schema/json/CommandExecutionRequestApprovalParams.json#L193-L283>), [권한 승인 응답 필수 필드](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/app-server-protocol/schema/json/PermissionsRequestApprovalResponse.json#L289-L320>), [available_decisions·effective_available_decisions](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/protocol/src/approvals.rs#L340-L410>).
- 증거 한계: **필수 필드가 늘었다는 스키마 사실**만 확인했고, 빈 `permissions:{}`가 서버에서 수용/거절되는지, `scope:'turn'`이 유효 enum인지, `decline`이 여전히 유효한지는 실행 검증하지 않았다. TalkAbout의 app-server 경로는 opt-in·text-only이며 실제 공급자 시험은 기존에도 NOTRUN이다.
- TalkAbout 적용: 후보는 **fail-closed 응답 검증**이다. (1) 거절 응답도 “스키마상 유효한 거절”이어야 턴이 정상 종료된다. 잘못된 거절 응답은 `runtime_protocol_mismatch`/`runtime_failed`로 이어지고, 그 턴은 결과 불명(`uncertain`)이 되어야 한다. (2) `cancel`과 `decline`의 의미 차이를 구분해야 한다. `decline`은 에이전트가 턴을 계속하고, TalkAbout은 프로세스를 중단시키므로 **부작용 불명**이다. (3) `approvalsReviewer`를 provider config에서 user로 고정할 수 있는지 확인하되, auto_review 승인을 **권한 상승으로 해석하지 않는다**. 이 항목은 [`TOOL_BROKER_PLAN.md`](../../docs/TOOL_BROKER_PLAN.md#L28-L42)의 Approval/ExecutionFence와 의미가 같고, 실제 도구 실행을 여는 근거가 아니다.

### F5 — sandbox는 “executor 소유 권한 프로필”로 진화했고 Windows sandbox는 별도 readiness를 가진다

sandbox 식별은 `None/MacosSeatbelt/LinuxSeccomp/WindowsRestrictedToken/WindowsMxc`로 나뉘고 `SandboxOverride`(정상/escalated/bypass-first)가 있다. 권한 모델은 filesystem access mode(`deny`> `write`> `read` 우선순위), `:special_path` 토큰의 **전방 호환 규칙**(새 토큰을 구버전이 거부하지 않고 warn-and-ignore), read-deny matcher, legacy `SandboxPolicy`→filesystem policy 변환 같은 계약을 갖는다. app-server에는 `WindowsSandboxReadiness`(ready/notConfigured/updateRequired)와 setup RPC가 있다. 즉 Windows에서도 sandbox는 **별도 설치·준비 상태를 가진 OS 기능**이며, 단순 containment와 다른 층이다.

- 구현: [SandboxType/Override와 Windows level](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/protocol/src/sandbox.rs#L8-L60>), [filesystem access mode 우선순위·special path 전방 호환](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/protocol/src/permissions.rs#L98-L160>), [read-deny matcher](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/protocol/src/permissions.rs#L315-L331>), [legacy sandbox→filesystem policy 변환](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/protocol/src/permissions.rs#L876-L882>), [WindowsSandboxReadiness ready/notConfigured/updateRequired](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/app-server-protocol/schema/json/v2/WindowsSandboxReadinessResponse.json#L1-L20>), [windows-sandbox-rs ACL API 사용(imports)](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/windows-sandbox-rs/src/acl.rs#L1-L40>).
- 증거 한계: 이 sandbox가 TalkAbout의 Windows 호스트에서 **실제로 작동/준비됨**을 검증하지 않았다. `--sandbox read-only|workspace-write`가 Windows restricted token 경로로 실제 집행되는지, readiness가 notConfigured일 때 어떤 오류가 나는지 실행하지 않았다. TalkAbout이 W1에서 쓰는 provider workspace-write 정책이 이 프로필 모델과 어떻게 매핑되는지도 정적 대조만으로는 불충분하다.
- TalkAbout 적용: **[`WORK_PROFILE.md`](../../docs/WORK_PROFILE.md#L83-L90)의 “sandbox 아님” 문구를 유지**하는 근거가 오히려 강화된다. 후보는 (1) provider가 보고하는 sandbox/readiness를 **표시만** 하고 권한 판단에 쓰지 않기, (2) Windows sandbox readiness를 “미확인” 상태로 사용자에게 노출하는 설계(향후, app-server RPC 필요)이며, **지금 구현하지 않는다**. [RUNNER_RESERVATIONS_PLAN.md](../../docs/RUNNER_RESERVATIONS_PLAN.md#L40-L55)의 실행 예산·수명과 sandbox는 별개 층이라는 서술을 유지한다.

### F6 — resume/세션 계약: 재개는 “영속 rollout이 있을 때만”이고, 공급자 내부 retry가 존재한다

exec `resume`는 세션/rollout 파일에 의존하며, 테스트는 `resume --last`가 기존 파일에 append하고, state DB에서 rollout이 누락되면 “repair”하는 경로를 검증한다. `--ephemeral`은 세션 파일을 저장하지 않는다. app-server `thread/resume`는 `excludeTurns`·`baseInstructions`·`developerInstructions` 등 파라미터를 갖는다. `ErrorNotification`은 `{error, threadId, turnId, willRetry}`로, **공급자 측 재시도가 있음**을 알리는 신호가 있다.

- 구현: [exec resume append 테스트](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/exec/tests/suite/resume.rs#L159-L236>), [rollout 누락 복구 테스트](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/exec/tests/suite/resume.rs#L258-L310>), [thread/resume 파라미터](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/app-server-protocol/schema/json/v2/ThreadResumeParams.json#L1607-L1702>), [ErrorNotification willRetry](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/app-server-protocol/schema/json/v2/ErrorNotification.json#L251-L268>), [한 세션에 동시에 하나의 Task](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/docs/protocol_v1.md#L10-L45>).
- TalkAbout 현재 계약: 매 턴 새 프로세스 + 동일 session resume, thread id 불일치 거부([exec](../../talkabout/agent_runtime.py#L1042-L1047)), 시작 승인 1회·uncertain no-retry([RUNNER_RESERVATIONS_PLAN.md](../../docs/RUNNER_RESERVATIONS_PLAN.md#L40-L55)), 늦은 outcome과 자동 재실행 금지([REPOSITORY_ADOPTION_PLAN.md](../../docs/REPOSITORY_ADOPTION_PLAN.md#L58-L75)).
- 증거 한계: resume 테스트는 mock 서버 기반이며 통과 결과가 아니다. `willRetry`가 true인 동안 TalkAbout이 무엇을 관찰하는지(같은 turn에서 재시도되는지, error event가 먼저 오는지)는 코드 실행 없이 알 수 없다. 또한 `--ephemeral` 기본값이 TalkAbout 명령에 영향을 주는지 확인하지 않았다.
- TalkAbout 적용: **(1) R3 문구를 정밀화**한다. “모델 호출 1회”가 아니라 “TalkAbout이 start를 1회 승인하고 재실행하지 않는다”이며, 공급자 내부 retry는 알 수 없고 `willRetry`는 진단 신호로만 기록한다. (2) `error`/`turn.failed` 도착 시 공급자 retry 가능성을 배제하지 않고 **결과 불명 = uncertain** 처리의 근거로 쓴다. (3) resume eligibility와 attempt를 혼동하지 않는다([07-TALKABOUT-GAPS.md](../repo-review-2026-10-27/07-TALKABOUT-GAPS.md#L46-L47)). (4) `--ephemeral`을 명시적으로 **사용하지 않음**을 계약에 적고, 향후 resume 불가는 `runtime_resume_unsupported`로 fail-closed 한다.

### F7 — exec-server/remote 환경은 “서버가 모델을 대신 실행”하는 방향이며 TalkAbout은 계약 어휘만 참고한다

HEAD에는 `exec-server`가 별도로 존재한다. JSON-RPC로 subprocess를 spawn/제어하고, `ws://IP:PORT` 기본 리스너, `--remote`+environment-id 등록, Noise relay, bearer/token 인증, capability discovery를 갖는다. README는 “labels support correlation, not authorization”, “forwarder는 요청을 replay하지 않고 실행 상태를 저장하지 않는다, 복구는 destination의 세션·출력 보존에 제한된다”, “destination이 session id·process·resumption을 소유한다”고 명시한다.

- 구현: [exec-server 개요·metadata는 권한 아님](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/exec-server/README.md#L23-L42>), [전송·인증(wss/loopback, 토큰)](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/exec-server/README.md#L43-L61>), [remote 등록·Noise relay·재연결 시 replay 없음·destination 소유](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/exec-server/README.md#L62-L77>), [exec-server-protocol 모듈 구성](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/exec-server-protocol/src/lib.rs#L1-L7>).
- 증거 한계: exec-server를 실행·연결하지 않았다. TalkAbout이 이 서버를 쓸 계획도 없다. relay framing·세션 resumption 세부는 이 문서 범위에서 검증하지 않았다.
- TalkAbout 적용: **도입 금지**. 원격/서버 측 실행은 [TOOL_BROKER_PLAN.md](../../docs/TOOL_BROKER_PLAN.md#L67-L77)의 명령 격리 관문을 열지 않는다. 참고할 것은 세 가지 문구뿐이다. ① correlation id는 권한이 아니다(방 ACL·lease·승인과 분리), ② 재연결 시 요청 replay를 하지 않고 “결과 불명”을 보존한다(R3·[A3](../../docs/REPOSITORY_ADOPTION_PLAN.md#L58-L75)), ③ capability·readiness 발견과 실제 권한 부여를 분리한다.

### F8 — Windows sandbox 준비 상태는 “기능”이 아니라 배포 전제다

Windows sandbox는 restricted token 경로와 setup/readiness가 있고, `notConfigured`/`updateRequired` 상태가 따로 있다. 즉 TalkAbout이 `--sandbox workspace-write`를 넘겨도 호스트가 준비되지 않았으면 실제 집행이 없을 수 있고, 그 사실을 클라이언트가 알아야 한다.

- 구현: [SandboxType WindowsRestrictedToken/WindowsMxc·level 매핑](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/protocol/src/sandbox.rs#L27-L60>), [WindowsSandboxReadiness enum](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/app-server-protocol/schema/json/v2/WindowsSandboxReadinessResponse.json#L1-L20>), [windows-sandbox-rs 트리(setup_main, deny_read_acl, elevated 등)](<https://github.com/openai/codex/tree/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/windows-sandbox-rs>).
- 증거 한계: readiness 판정 로직·설치 절차·실패 시 오류 코드는 읽지 않았다. TalkAbout Windows 호스트에서의 실제 상태도 NOTRUN.
- TalkAbout 적용: “containment(Job Object)는 sandbox가 아니다”([TOOL_BROKER_PLAN.md](../../docs/TOOL_BROKER_PLAN.md#L67-L71))를 유지하고, 향후 sandbox를 표시하더라도 **readiness=unknown이면 미집행으로 표시**한다. 이 항목은 문서·상태 표시 후보이며 실행 기능 도입이 아니다.

## 2. TalkAbout 적용 후보

아래는 기존 A0/R3 계약을 강화하는 후보이며, 구현 완료·provider 수용·sandbox 강제를 주장하지 않는다. 공급자 호출이 필요한 검증은 별도 예산 승인 전까지 NOTRUN이다.

| 순위 | 후보 | 단계·산출물 | gate(증거) | 비용 | 위험 |
|---|---|---|---|---|---|
| P1 | **A0 스키마 드리프트 fixture + 진단** — exec JSONL 고정 fixture, 허용/거절 item 표, 버전 태그 | A0 후속, 테스트 전용 | synthetic stream에서 신규 item type·추가 필드·상한 초과가 turn을 `uncertain`/`runtime_tool_policy_violation`으로만 종료시키고 성공으로 승격되지 않음. provider 호출 없음 | S | 낮음. fixture가 특정 버전에 낡을 수 있음 → 버전 태그·갱신 절차 필수 |
| P1 | **config override 실효성·위험 flag 감지** — no-op/removed flag 표, 자동승인·우회 flag 존재 감지, strict-config 실패 fail-closed 유지 | A0 보강, 로컬 help 파싱+문서 | `--help` 파싱 단위 테스트, 위험 flag 발견 시 차단/경고, 적용 config 미확인 시 “off 주장 금지” 문구 | S~M | 중간. help 텍스트 형식 의존, app-server config read는 provider RPC라 별도 승인 |
| P2 | **승인 거절 응답 형태 검증** — decline/cancel 의미, permissions 응답 필수 필드, elicitation 거절 | app-server opt-in 경로 보강 | synthetic 또는 mock transport로 응답 스키마 검증, 잘못된 응답은 `unknown` 처리, 실제 app-server 시험은 NOTRUN | M | 중간. 스키마 기반 추론과 실제 수용 여부의 간극 |
| P2 | **R3 문구·신호 정밀화** — “start 1회 승인, 재시작 없음”, `willRetry`/retry 신호는 진단 | 문서+이벤트 분류 | 늦은 outcome·재시도 신호가 성공/완료를 만들지 않음, uncertain 유지 | S | 낮음. 문구 변경이 제품 권한을 바꾸지 않게 리뷰 |
| P3 | **sandbox readiness 표시 설계(미구현)** — provider가 보고할 때만 “readiness unknown/notConfigured” 표시 | 설계 문서만 | 표시가 권한·성공 판정에 쓰이지 않음, W1 문구 유지 | L | 높음. sandbox 보장으로 오독될 위험 → 지금 착수 보류 |

**즉시 하지 않을 것**: exec-server/remote 실행, auto_review 승인 도입, app-server를 기본 transport로 전환, shell/web/MCP 기능 활성화, sandbox 집행 주장, Codex Rust 코드 복사. A0·A1·A2·A3·A4의 기존 gate([REPOSITORY_ADOPTION_PLAN.md](../../docs/REPOSITORY_ADOPTION_PLAN.md#L9-L17))를 대체하지 않는다.

## 3. 그대로 가져오면 안 되는 것

1. **exec-server/remote environment.** 원격 subprocess 실행·Noise relay·environment registry는 “서버가 실행을 소유”하는 구조다. TalkAbout의 서버-비실행·MCP/web 차단과 충돌한다.
2. **auto_review/guardian 승인.** `approvalsReviewer=auto_review`는 risk-based subagent가 승인한다. TalkAbout의 사람 승인·capability 원칙([TOOL_BROKER_PLAN.md](../../docs/TOOL_BROKER_PLAN.md#L28-L42))과 다르다. 승인 자동화는 도구 실행 관문을 우회한다.
3. **shell/unified_exec/apply_patch의 일반 활성화.** A0 work profile은 켜져 있으나 관찰 계약이며, 그 밖의 profile로 확대하지 않는다. `features.shell_tool`·`UnifiedExec`는 실 flag이므로 “꺼져 있다”는 주장을 버전별로 재확인해야 한다.
4. **`--dangerously-bypass-approvals-and-sandbox`·`--approve-for-me` 등 자동 승인·우회 flag.** TalkAbout 명령에 넣지 않는다. 발견 시 진단에 남긴다.
5. **provider exactly-once·내부 retry 부재 주장.** `willRetry`가 존재한다. R3 보장은 TalkAbout 스케줄러 범위로 한정한다.
6. **sandbox 집행 보장 차용.** `--sandbox` 인자·Windows sandbox readiness·ACL 구현은 provider/호스트 소유이며 TalkAbout의 containment와 별개다. “sandbox 안에서 실행된다”고 표시하지 않는다.
7. **Codex Rust/TS 코드 이식.** Apache-2.0이라 MIT 프로젝트에 코드를 넣는 것은 가능하지만, 이 문서의 제안은 설계 참고이며 복사·이식 계획이 아니다. NOTICE·attribution 의무도 별도다.

## 4. 검증 경계(NOTRUN)

- `codex` 바이너리 실행, `codex exec --json`, app-server 연결, resume, approval 왕복, sandbox 집행, Windows readiness를 **실행하지 않았다**. 모든 판정은 소스·JSON Schema·테스트 소스 읽기에 근거한다.
- Codex 테스트(`resume.rs`, `approval_policy.rs`)는 소스에 존재하는 assertion의 증거이며 통과 결과가 아니다. mock 서버 기반이다.
- TalkAbout 쪽도 이 문서로 코드·테스트를 변경하지 않았다. “A0가 최신 Codex와 호환된다”, “거절 응답이 유효하다”, “override가 실제로 기능을 끈다”는 주장은 하지 않는다.
- HEAD 전체의 exec 이벤트 생성 경로(feature gate 전수), config legacy 매핑 전체, permissions 프로필의 실제 집행, exec-server relay framing은 읽지 않았거나 부분적으로만 읽었다(추정 금지).
- A0·W1의 기존 합성 검증과 실제 공급자 시험(NOTRUN) 구분은 [WORK_PROFILE.md](../../docs/WORK_PROFILE.md#L104-L114)와 동일하게 유지한다.

## 5. 라이선스·의존성

- root [LICENSE](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/LICENSE#L1-L12>)는 **Apache License 2.0**. [NOTICE](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/NOTICE#L1-L6>)는 “OpenAI Codex, Copyright 2025 OpenAI”와 Ratatui(MIT) 유래 코드 고지를 담는다. Apache-2.0 재배포 시 라이선스 사본·변경 고지·NOTICE 전달 의무가 있고, MIT인 TalkAbout에 코드를 넣더라도 NOTICE/attribution을 별도 보존해야 한다.
- binaries·vendored Rust 의존성·`third_party/` 전체의 라이선스 실사를 완료하지 않았다. 서드파티 crate는 Cargo.lock 기준으로 별도 검토가 필요하다.
- 이 문서는 **설계 패턴의 독립 구현 참고**이며 Codex 코드 복사·이식·번들링을 하지 않았다. TalkAbout 코드베이스에는 어떤 Codex 소스도 추가되지 않았다.
- 참고: Codex 문서 페이지 상당수(`docs/exec.md`, `docs/sandbox.md`, `docs/config.md`)는 pinned SHA에서 외부 문서로 이동한 stub이며([exec](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/docs/exec.md#L1-L3>), [sandbox](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/docs/sandbox.md#L1-L3>)), 이 문서의 근거로 쓰지 않았다. 단, `requirements.toml`의 managed-only hooks 같은 운영 설정은 계속 존재한다([config.md](<https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/docs/config.md#L1-L12>)).

## 6. 발견별 판정 요약

| 발견 | 내용 | 판정 | TalkAbout 접점 |
|---|---|---|---|
| F1 | exec item 표면 확장, 허용 3종 exact-key는 유지 | 채택(P1, fixture) | A0 parser·`_WorkItems` 계약 |
| F2 | exec CLI flag·하위명령 확장, help fingerprint 한계 | 채택(P1, 감지) | preflight `_ensure_preflight` |
| F3 | `features.*` removed/no-op·`web_search` 이동, strict-config 사각 | 채택(P1, 실효성) | command 조립의 `-c` override |
| F4 | approvalsReviewer·granular approval·permissions 응답 필수 필드 | 채택(P2, 응답 검증) | app-server opt-in deny 응답 |
| F5 | executor 소유 권한 프로필, Windows sandbox readiness | 보류(표시만) | W1 “sandbox 아님” 문구 유지 |
| F6 | resume는 rollout 의존, 공급자 retry(`willRetry`) 존재 | 채택(P2, 문구·분류) | R3 보장 범위 정밀화 |
| F7 | exec-server 원격 실행·relay·destination 소유 | 도입 금지(어휘 참고) | W3 보류 유지 |
| F8 | Windows sandbox 준비 상태는 배포 전제 | 참고(P3) | 상태 표시 설계만 |

## 7. 읽은 범위와 확인하지 않은 것

실제로 연 파일과 범위를 남긴다. 이 목록에 없는 파일은 이 문서의 근거가 아니다.

| 파일 | 읽은 범위 | 용도 |
|---|---|---|
| `LICENSE` | 1-12행 | Apache-2.0 확인 |
| `NOTICE` | 전체 | attribution 의무 |
| `SECURITY.md` | 전체 | 보안 경계 문서 위치 |
| `docs/exec.md`·`docs/sandbox.md`·`docs/config.md` | 전체 | 이동 stub·managed hooks 언급 확인 |
| `README.md` | 1-40행 | 설치·제품 경계 |
| `codex-rs/exec/src/exec_events.rs` | 전체 | exec JSONL 계약 |
| `codex-rs/exec/src/cli.rs` | 전체 | exec flag·하위명령 |
| `codex-rs/protocol/src/sandbox.rs` | 전체 | SandboxType/Override |
| `codex-rs/protocol/src/items.rs` | 41-101행 + 항목 이름 grep | TurnItem 표면 |
| `codex-rs/protocol/src/approvals.rs` | enum/decision 관련 라인 grep | available_decisions·elicitation |
| `codex-rs/protocol/src/permissions.rs` | doc comment·enum 라인 grep | access mode·special path·deny matcher |
| `codex-rs/features/src/lib.rs` | Feature enum·removed 주석·legacy 라인 grep | override 실효성 |
| `codex-rs/app-server-protocol/schema/json/v2/TurnStartParams.json` | properties·SandboxPolicy def 파싱 | turn/start 계약 |
| `.../v2/ThreadResumeParams.json` | properties 1607-1702, defs | resume·approval·sandbox |
| `.../v2/ErrorNotification.json` | 251-268 | willRetry |
| `.../v2/WindowsSandboxReadinessResponse.json` | 전체 | readiness enum |
| `.../CommandExecutionRequestApprovalParams.json` | 193-283, 546-598 | decision enum·params |
| `.../PermissionsRequestApprovalResponse.json` | 289-320 | 필수 permissions/scope |
| `codex-rs/exec/tests/suite/approval_policy.rs` | 전체 | auto review·approve-for-me 정적 근거 |
| `codex-rs/exec/tests/suite/resume.rs` | 테스트 이름·관련 라인 grep | resume append·repair 정적 근거 |
| `codex-rs/exec-server/README.md` | 1-150행 | 원격 실행 계약 |
| `codex-rs/exec-server-protocol/src/lib.rs` | 전체 | 모듈 구성 |
| `codex-rs/docs/protocol_v1.md` | 1-80행 | Task/Turn 용어 |
| `codex-rs/windows-sandbox-rs/src/acl.rs` | 1-40행(imports) | Windows ACL API 사용 흔적 |
| `codex-rs/windows-sandbox-rs/` | 파일 트리 | setup/deny_read/elevated 구조 |

**확인하지 않음(이 문서의 주장 범위 밖)**

- `codex-rs/exec/src/lib.rs`(92KB), `event_processor_with_jsonl_output.rs`, app-server·exec-server 본체 소스는 열지 않았다. wire 출력 조립·세션 상태 전이의 세부는 근거가 아니다.
- `permissions.rs`(180KB), `approvals.rs`(20KB)는 doc/식별자 라인만 grep했다. 권한 판정 알고리즘 전체를 읽지 않았다.
- `ThreadStartParams`·`TurnCompletedNotification`·`ItemCompletedNotification`의 전체 필드 집합은 참조했으나 정밀 대조표를 만들지 않았다.
- Windows sandbox setup/ACL 집행·readiness 판정 로직, `sandbox_smoketests.py`는 읽지 않았다.
- `features/src/legacy.rs`의 전체 키 매핑과 TalkAbout이 쓰는 key 문자열의 일대일 대조는 완료하지 않았다.
- `codex` 실행·app-server 연결·sandbox 집행·resume·approval 왕복은 전부 NOTRUN이다.

## 8. 후속 체크리스트 (실행 전 승인 필요)

1. **fixture gate**: exec JSONL 고정 fixture를 버전 태그와 함께 저장하고, 신규 item type·추가 필드가 성공으로 승격되지 않는 테스트를 synthetic으로 추가한다.
2. **help fingerprint gate**: `exec --help`·`exec resume --help`에서 위험 flag(자동 승인·우회·sandbox bypass)을 감지하는 순수 파서 테스트를 추가한다. 실제 바이너리 실행 없이 문자열 fixture로 검증한다.
3. **override gate**: `features.*` 실효성 표를 만들고, no-op으로 확인되지 않은 key는 제거하지 않는다(확인 전 변경 금지). `--strict-config` 실패는 계속 fail-closed다.
4. **승인 응답 gate**: `decline`/`cancel`/permissions 응답 형태를 스키마에 대조하고, 잘못된 응답은 턴 성공이 아니라 `unknown`으로 남기는 테스트를 mock transport로 추가한다. 실제 app-server 시험은 별도 승인·NOTRUN.
5. **문구 gate**: R3 보장 문구를 “start 1회 승인·재실행 없음”으로 고정하고, 공급자 내부 retry 부재를 주장하지 않는다.
6. **금지 gate**: exec-server·remote environment·auto_review·sandbox 집행 주장·Codex 코드 복사가 diff에 없음을 리뷰한다.
7. **NOTRUN gate**: provider 실제 호출이 필요한 검증은 새 예산 승인 전까지 계속 NOTRUN으로 표기한다.

## 9. 이 문서가 주장하지 않는 것

- TalkAbout이 pinned HEAD와 완전 호환된다는 주장이 아니다. 확인한 것은 이벤트 이름과 work item 3종 필드의 일치뿐이다.
- TalkAbout이 거절한 item이 실제로 provider에서 생성되지 않는다는 주장이 아니다(feature gate 집행 미검증).
- `-c features.*=false`가 실제로 기능을 껐다는 주장이 아니다(no-op 가능성).
- TalkAbout의 app-server 거절 응답이 현행 스키마에서 수용된다는 주장이 아니다.
- sandbox 집행·Windows sandbox readiness·exactly-once 실행을 보장한다는 주장이 아니다.
- exec-server·remote 실행·auto_review 승인을 도입하자는 제안이 아니다.
- A0·A1·A2·A3·A4의 기존 gate를 대체하거나 실제 공급자 시험을 완료했다는 주장이 아니다.

## 참고 링크

- Codex 저장소: <https://github.com/openai/codex> (고정 `67f0994b532b8740f14b5b738af7b7b2a915b1d6`)
- TalkAbout 쪽 근거: [REPOSITORY_ADOPTION_PLAN.md](../../docs/REPOSITORY_ADOPTION_PLAN.md), [07-TALKABOUT-GAPS.md](../repo-review-2026-10-27/07-TALKABOUT-GAPS.md), [WORK_PROFILE.md](../../docs/WORK_PROFILE.md), [TOOL_BROKER_PLAN.md](../../docs/TOOL_BROKER_PLAN.md), [RUNNER_RESERVATIONS_PLAN.md](../../docs/RUNNER_RESERVATIONS_PLAN.md), [STATUS.md](../../docs/STATUS.md#L162-L173), [agent_runtime.py](../../talkabout/agent_runtime.py), [mcp.py](../../talkabout/mcp.py)
