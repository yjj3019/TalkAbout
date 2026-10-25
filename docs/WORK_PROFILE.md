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
| 실제 공급자 작업 턴(파일 수정·검증) | **NOTRUN** — 공급자 예산·동의 필요 |
| sandbox/egress 제한 | **미구현**. [TOOL_BROKER_PLAN.md](TOOL_BROKER_PLAN.md)의 관문을 그대로 유지 |

실제 작업 턴을 시험할 때는 `--max-model-calls`·`--max-seconds`를 작게 두고, 변경이 남지 않는
별도 임시 디렉터리를 workspace로 지정한다.
