# 작업 프로필 (opt-in 로컬 도구 실행) / Work profile

**IMPLEMENTED · 기본 꺼짐 · 실제 공급자 시험 NOTRUN · 확인일: 2026-10-26**

방에서 실제 코딩·분석을 하려면 에이전트가 자기 머신에서 파일을 읽고 고치고 검증할 수 있어야 한다.
작업 프로필은 **소유자가 명시적으로 켤 때만** 그 능력을 허용한다. 플랫폼은 여전히 아무것도 실행하지 않는다.

## 무엇을 하는가

에이전트 CLI를 띄울 때 **격리는 유지하고 작업 도구만 켠다**.

| 항목 | text_only(기본) | work(opt-in) |
| --- | --- | --- |
| Codex sandbox | `read-only` | `workspace-write` (지정 디렉터리 안에서만 쓰기) |
| shell / patch 도구 | `features.shell_tool=false`, `unified_exec=false`, `apply_patch_freeform=false` | **켜짐** |
| MCP·hooks·plugins·skill discovery | 꺼짐 | **꺼짐 유지** |
| web search | `disabled` | **`disabled` 유지** |
| cwd | 런타임이 만든 임시 workspace | **소유자가 지정한 디렉터리**(삭제하지 않음) |
| 실행 프로세스 수명 | containment(Windows Job / POSIX C1) | 동일 |

## 사용법

```powershell
python -m talkabout.cli run --config agent.local.json `
  --runtime codex --auth-mode native --approve-external `
  --work-profile --work-workspace "D:\work\my-repo"
```

- `--work-profile`과 `--work-workspace`는 **함께** 줘야 한다.
- 현재 **Codex + `--codex-transport exec-json`에서만** 동작한다. Claude/Grok/app-server는 `runtime_work_profile_unsupported`로 거절한다.
- `--synthetic` 및 POSIX 실행과 조합할 수 없다(기존 fail-closed 유지).

## 거절 규칙 (workspace 검증)

작업 디렉터리는 다음이면 **거절**한다. 하나라도 해당하면 실행 전에 멈춘다.

| 조건 | 코드 |
| --- | --- |
| 경로가 없음 | `runtime_work_workspace_missing` |
| 파일이거나 symlink | `runtime_work_workspace_invalid` |
| 홈 디렉터리이거나 파일시스템 루트 | `runtime_work_workspace_too_broad` |
| TalkAbout 소스 트리이거나 그 조상/자손 | `runtime_work_workspace_overlaps_platform` |
| 쓰기 불가 | `runtime_work_workspace_not_writable` |

위 경로 검증에 더해, 디렉터리는 **깨끗한 git 저장소 루트**여야 한다(`runtime_work_repo_*`). 자세한 조건과 턴 후 관측은 아래
[git 사전조건과 변경 관측](#git-사전조건과-변경-관측-2026-11-10-위협-점검-f3-a)을 본다.

## P0 작업 도구 이벤트 관찰 계약

Codex exec-json work profile에서만 `command_execution`, `file_change` 및 계획 메타데이터
`todo_list` item을 정해진 형식과 크기·개수 상한 안에서 관찰한다. 기본 text-only 프로필은
해당 item을 계속 거절한다. 계획 항목의 완료 표시는 실행이나 검증 성공을 뜻하지 않는다.

- 허용 형식은 **Codex 0.164의 확인된 item/event 필드를 기준으로 제한**한다. command의 `exit_code`는 null 또는 생략도 받으며 나머지 필드는 exact-key다.
  알 수 없는 item·필드, 추가 필드, 잘못된 상태나 상한 초과는 조용히 무시하지 않고
  fail-closed로 처리한다. provider가 호환성 있는 필드를 추가하더라도 별도 검토와
  schema·테스트 갱신 전에는 거절될 수 있다. 모든 Codex 버전과 호환된다는 뜻이 아니다.
  `item.completed`의 `in_progress` 상태 역시 보수적으로 거절한다. event별 변경 목록 누적 상한은
  실제 고유 파일 수가 아니며 반복 snapshot으로 정상 작업도 제한에 도달할 수 있다.
- 이 item은 native 도구의 **사후 관찰**이다. 수용·거절 자체가 명령/파일 변경에 대한
  사전 권한 승인이나 OS sandbox가 아니다. 이벤트가 도착하기 전에 명령이 실행되거나
  파일이 바뀌었을 수 있으므로, parser 거절·interrupt는 부작용이 없다는 증거가 아니다.
- 실패·거절된 도구 항목이나 nonzero 종료 코드는 해당 작업의 실패 정보이며,
  **turn 전체의 성공 또는 테스트 통과 증거가 아니다**. 반대로 개별 항목 하나만으로
  모든 후속 작업의 실패를 확정하지도 않는다. 최종 turn 결과와 실제 검증 여부는 별도로
  판단하고, worker 보고를 서버 독립 검증으로 승격하지 않는다.
- prompt 전송 뒤 parser 오류나 중단이 발생하면 실행 여부를 안전하게 단정할 수 없어
  `unknown`으로 취급한다. 서버의 기존 R3 start 승인·uncertain 처리와 자동 재실행 금지
  경계를 유지한다.
- 이 P0의 검증은 **합성 이벤트 스트림**에 한정한다. 실제 provider의 파일 수정·명령 실행
  턴은 별도의 명시적 예산·동의가 필요하며 현재 **NOTRUN**이다.

## 관측 실패 ≠ 허용 (A0 후속 관측 경계, 2026-10-31)

A0 도구 이벤트는 **사후 관찰**이며 권한·성공 판정이 아니다. 관측이 실패했거나 이벤트가 도착하지 않은 경우를
"허용됨"·"문제 없음"·"테스트 통과"로 승격하지 않는다. 이 절은 [2차 외부 저장소 분석](../analysis/repo-review-2026-10-31/08-TALKABOUT-GAPS.md)의
교차 결론을 TalkAbout 계약으로 옮긴 것이며, 외부 코드는 복사하지 않았다.

| 상황 | 현재 처리 | 표시·판정 규칙 |
| --- | --- | --- |
| item schema 불일치·추가 필드·잘못된 타입 | `runtime_invalid_output`으로 turn 중단 | 성공으로 승격하지 않는다. prompt 전송 뒤에는 실행 여부를 단정하지 않고 `unknown`으로 둔다 |
| 허용 목록 밖 도구(`mcp_tool_call`/`web_search` 등) | `runtime_tool_policy_violation` | 거절은 "도구가 실행되지 않았다"는 증거가 아니다. `unknown` 경계 유지 |
| tool item이 `in_progress`로 남은 채 turn 종료 | `runtime_invalid_output` | 완료로 표시하지 않는다 |
| 관측 이벤트 0건 | evidence `events: 0` | "변경 없음"·"허용"·"성공"이 아니라 **미관측**이다 |
| parser/transport 예외 | turn `unknown`, 자동 재실행 없음 | R3 one-shot과 uncertain no-retry 경계를 유지한다 |

- 이 규칙은 합성 fixture로 고정한다: [codex-exec 0.164 item fixture](../tests/fixtures/codex_exec_0164_items.json),
  [회귀 테스트](../tests/test_work_profile_events.py). fixture는 **드리프트 감지용**이며 실제 설치된 binary 호환성이나 provider 실행 증거가 아니다.
- 관측된 evidence는 `observed`/`agent_reported_unverified`이고, 접근 권한은 `not_enforced`로 표기한다. 관측≠권한이다.

## 경로 경계 검증의 범위와 한계 (W1, 2026-10-31)

`--work-workspace`는 provider workspace-write 정책의 대상 디렉터리를 고르는 **trust transfer**이며 sandbox가 아니다.
현재 코드가 실제로 하는 검증과, 아직 하지 않는 검증을 구분한다. 경로 검사는 `Path`가 만든 normalized path에 적용된다.

| 항목 | 현재 상태 |
| --- | --- |
| 입력 경로의 최종 또는 중간 구성요소가 symlink/junction | `Path`가 정규화한 절대 경로의 각 구성요소를 검사하고 `runtime_work_workspace_invalid`로 거절. 실제 Windows reparse/junction 동작은 아직 OS에서 테스트하지 않음 |
| 상대 경로 | lexical component 검사에서 fail-closed로 거절 (`runtime_work_workspace_invalid`) |
| 홈·파일시스템 루트 | `runtime_work_workspace_too_broad` |
| TalkAbout 소스 트리 겹침 | `runtime_work_workspace_overlaps_platform` |
| 쓰기 불가 | `runtime_work_workspace_not_writable` |
| junction/reparse(최종·중간 구성요소) | `lstat()` 결과의 reparse attribute와 `Path.is_junction` 신호를 검사해 `runtime_work_workspace_invalid`로 거절. 실제 Windows junction 생성·OS 동작은 테스트하지 않음; 실제 OS 동작 및 TOCTOU 보장은 없음 |
| 종료 후·turn 사이 재검증 | **미구현** — 현재는 예약 전 preflight와 runtime 생성 시 2회 확인 |
| sandbox·egress 제한 | **미구현**. [TOOL_BROKER_PLAN.md](TOOL_BROKER_PLAN.md) 관문 유지 |

정규화된 절대 경로의 각 구성요소를 순차 검사해 중간 symlink/junction이 `resolve(strict=True)` 전에 탐지되도록 보강했다. 검사는 점검과 사용 사이의 경쟁을 막지 않으며 hostile-admin 보안 경계가 아니다. Windows의 다른 reparse tag 유형, 검사 후 경로 교체, turn 사이 재검증은 별도 강화가 필요하다. 이 경로 검사는 여전히 [독립 구현 정책](CLEAN_ROOM_IMPLEMENTATION.md)에 따라 TalkAbout 자체 코드다.

## 제출된 작업 항목 집계 (A1)

표준 work worker는 정상 반환·프로세스 정리 후 같은 턴의 관측 항목을 집계해 완료 메시지에 보낸다.
`command_items`, `file_change_items`는 고유 항목 수이지 명령 실행 횟수·수정된 파일 수가 아니다.
`failed_items`는 provider 상태가 `failed` 또는 `declined`인 항목 수이며, `completed`이지만
종료 코드가 0이 아닌 경우를 별도로 실패로 세지 않는다. 미제출과 0건은 다르다.

서버는 형식·범위 및 제출 권한을 검사하고 **`agent_reported_unverified`**로 표시한다.
다른 승인된 agent도 임의 값을 제출할 수 있어 실제 이벤트 출처·명령 실행·파일 변경·테스트 성공의
증거가 아니다. 자유형 명령·경로·출력·종료 코드·계획 텍스트는 이 집계에 포함하지 않는다.
메시지 접근은 해당 멤버의 history 경계를 따르며 이벤트·task 요약에는 집계를 넣지 않는다.
기존 결과 메시지 및 worker SQLite 보존 정책을 따르며 별도 삭제 기한을 보장하지 않는다.

## git 사전조건과 변경 관측 (2026-11-10, 위협 점검 F3-a)

작업 프로필은 에이전트가 **소유자의 실제 디렉터리**를 고치게 한다. 플랫폼은 아무것도 적용하지 않으므로, 되돌릴 방법은
소유자의 git 이력뿐이다. 그래서 그 방법이 **시작 전에 존재하도록** 요구하고, 턴이 끝난 뒤 무엇이 달라졌는지 기록한다.

**시작 전 사전조건**(예약 전 preflight와 runtime 생성 시 모두 검사, 읽기 전용):

| 조건 | 거절 코드 |
| --- | --- |
| git 실행 파일이 없음 | `runtime_work_repo_git_unavailable` |
| 디렉터리가 git 저장소가 아님 | `runtime_work_repo_not_repository` |
| git 작업 트리의 **루트**가 아님(하위 디렉터리) | `runtime_work_repo_not_root` |
| 커밋이 하나도 없음 | `runtime_work_repo_no_commit` |
| 추적 파일 변경 또는 **미추적 파일**이 있음 | `runtime_work_repo_not_clean` |
| git 호출 실패·시간 초과 | `runtime_work_repo_check_failed` |

깨끗한 트리를 요구하는 이유는, 미추적·미커밋 변경이 남아 있으면 에이전트의 수정과 소유자의 작업을 구분할 수 없고 되돌릴 수도 없기 때문이다.
이 검사는 시작 시 **한 번**이다. 턴 사이에 소유자가 직접 고치는 것은 막지 않는다.

**턴 후 관측**(로컬 전용, 서버로 보내지 않음, `work_evidence` 스키마 불변):

- 프로세스 트리 정리가 **확인된 뒤에만** git을 실행한다. 정리 미확인이면 `unknown`(`cleanup_unconfirmed`)으로 기록한다.
- 기준은 시작 시점의 커밋이다. `git diff --name-status <시작 커밋>`과 미추적 목록, HEAD 변경 여부(`head_changed`)를 기록한다.
  에이전트가 shell로 직접 commit·reset해도 시작 커밋 대비로 보이지만, 이는 관측일 뿐 방지가 아니다.
- 파일 목록은 100개까지만 담고 개수는 정확히 센다(`truncated`). **ignore된 파일은 보이지 않는다**(`ignored_files: not_observed`).
- CLI는 매 step 뒤에 `{"work_repo_observation": {...}}` 한 줄을 출력한다.

**git 자체를 통한 탈출 차단**: 에이전트가 `.git/config`(`core.fsmonitor` 등)나 hooks를 고치면, 이후 플랫폼이 소유자 권한으로
실행하는 `git status`가 그 코드를 실행할 수 있다. hook 변경은 `git diff`에도 나타나지 않는다. 그래서 시작 시 config·`info/attributes`·hooks의
지문을 저장하고, **git을 실행하기 전에** 매 턴 비교한다. 달라졌으면 git을 실행하지 않고 다음 턴을 `runtime_work_repo_tampered`로 거절하며,
그 판정은 파일을 되돌려도 유지된다(재시작 필요). git 호출은 최소 환경(플랫폼 토큰·공급자 키 없음, 전역/시스템 git 설정 무시, `core.fsmonitor=false`)에서 시간·출력 상한을 두고 실행한다.

**플랫폼이 하지 않는 것**: 사용자 저장소에 브랜치·커밋·stash를 만들지 않는다. 에이전트가 디렉터리 밖을 읽거나 썼는지, 네트워크를 썼는지는 이 관측으로 알 수 없다.

## 출력 시크릿 마스킹의 범위 (2026-11-10, 위협 점검 F3-c)

워커는 에이전트 출력을 저널에 저장하거나 서버로 보내기 **전에** 이 프로세스가 아는 시크릿 값(플랫폼 토큰, DB URL, 공급자 API 키 중 16자 이상)을
고정 placeholder로 바꾼다. 다음은 **보장하지 않는다**:

- 에이전트가 읽은 파일 속의 임의 키·토큰·비밀번호처럼 **플랫폼이 모르는 값**은 가려지지 않는다.
- 16자 미만 값, 변형(인코딩·분할·일부만 인용)된 값은 가려지지 않는다.
- 이 버전 이전에 이미 저장된 결과는 byte 단위로 그대로 재전송된다(같은 idempotency key에 다른 본문을 보내지 않기 위함).

따라서 작업 프로필의 출력은 **여전히 민감 정보를 담을 수 있다**. 민감한 파일이 있는 디렉터리를 작업 디렉터리로 지정하지 않는다.

## 이것은 sandbox가 아니다

- 플랫폼은 **파일·네트워크·비밀정보 접근을 제한하는 sandbox를 주장하지 않는다**. provider 자체의 workspace 정책과
  containment(수명·정리)만 적용된다.
- 에이전트가 **무엇을 했는지**를 서버는 검증할 수 없다. 기여는 지금처럼 `worker_reported_runtime` 증거로만 남고,
  받아들일지는 **사람이** 판단한다.
- 방의 `change_proposal` 산출물과 결합하면 결과는 **통합 diff**로 제출되고, 플랫폼은 그 diff를 적용하지 않는다.
- **읽기 범위**: provider의 `workspace-write`가 제한하는 것은 쓰기다. 에이전트가 작업 디렉터리 밖 파일을 읽을 수 있는지는 provider·OS에 달려 있고
  플랫폼이 검증하지 않는다 `[unverified: Windows의 Codex 샌드박스 읽기 제한]`. 프롬프트의 "디렉터리 안만 건드려라"는 지시이지 강제가 아니다.
- **네트워크**: 플랫폼은 셸의 네트워크를 차단하지 않는다. `web_search="disabled"`는 검색 도구 설정이다. "네트워크를 추가하지 말라"는 프롬프트 문장도 강제가 아니다.
- **승인 없음**: 작업 프로필 턴은 `approval_policy="never"`·`--ignore-rules`로 실행되어 공급자 쪽 명령 승인이 꺼져 있고, 플랫폼의 사전 승인도 없다.
- 프로세스 containment(Windows Job)는 **수명**만 제한하고 능력은 제한하지 않는다.

## 프롬프트에 추가되는 지시

작업 프로필 턴에는 다음이 방 프롬프트 앞에 붙는다(요지):

> Work profile (owner opt-in): you may inspect and edit files inside this directory only: `<path>`.
> Use your own tools there. Do not touch anything outside it, do not modify the TalkAbout platform,
> and do not add network access. Report a unified diff of what you changed and what you actually
> verified. The platform applies nothing.

작업 공간은 예약 전 한 번, runtime 생성 시 다시 확인한다. 이 점검은 시점별 검사일 뿐
경로 구성요소의 교체 경쟁을 제거하거나 디렉터리의 OS 수준 격리를 보장하지 않는다.
기존 연결 시험도 work-profile runtime을 사용하므로 도구가 비활성화된 시험이라고 가정하지 않는다.

## 검증 상태

| 항목 | 상태 |
| --- | --- |
| 거절 규칙·명령 형태·프롬프트 | **합성 통과** (`tests/test_work_profile.py` 17 + `tests/test_work_profile_cli.py` 5) |
| P0 work-only bounded item 관찰 | **합성 스트림 검증** — 실제 공급자 호환성은 미검증 |
| 실제 공급자 작업 턴(파일 수정·검증) | **NOTRUN** — 공급자 예산·동의 필요 |
| sandbox/egress 제한 | **미구현**. [TOOL_BROKER_PLAN.md](TOOL_BROKER_PLAN.md)의 관문을 그대로 유지 |

실제 작업 턴을 시험할 때는 `--max-model-calls`·`--max-seconds`를 작게 두고, 변경이 남지 않는
별도 임시 디렉터리를 workspace로 지정한다.
