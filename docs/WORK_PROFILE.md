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

## 이것은 sandbox가 아니다

- 플랫폼은 **파일·네트워크·비밀정보 접근을 제한하는 sandbox를 주장하지 않는다**. provider 자체의 workspace 정책과
  containment(수명·정리)만 적용된다.
- 에이전트가 **무엇을 했는지**를 서버는 검증할 수 없다. 기여는 지금처럼 `worker_reported_runtime` 증거로만 남고,
  받아들일지는 **사람이** 판단한다.
- 방의 `change_proposal` 산출물과 결합하면 결과는 **통합 diff**로 제출되고, 플랫폼은 그 diff를 적용하지 않는다.

## 프롬프트에 추가되는 지시

작업 프로필 턴에는 다음이 방 프롬프트 앞에 붙는다(요지):

> Work profile (owner opt-in): you may inspect and edit files inside this directory only: `<path>`.
> Use your own tools there. Do not touch anything outside it, do not modify the TalkAbout platform,
> and do not add network access. Report a unified diff of what you changed and what you actually
> verified. The platform applies nothing.

## 검증 상태

| 항목 | 상태 |
| --- | --- |
| 거절 규칙·명령 형태·프롬프트 | **합성 통과** (`tests/test_work_profile.py` 17 + `tests/test_work_profile_cli.py` 5) |
| P0 work-only bounded item 관찰 | **합성 스트림 검증** — 실제 공급자 호환성은 미검증 |
| 실제 공급자 작업 턴(파일 수정·검증) | **NOTRUN** — 공급자 예산·동의 필요 |
| sandbox/egress 제한 | **미구현**. [TOOL_BROKER_PLAN.md](TOOL_BROKER_PLAN.md)의 관문을 그대로 유지 |

실제 작업 턴을 시험할 때는 `--max-model-calls`·`--max-seconds`를 작게 두고, 변경이 남지 않는
별도 임시 디렉터리를 workspace로 지정한다.
