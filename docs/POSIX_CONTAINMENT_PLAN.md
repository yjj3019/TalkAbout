# POSIX 실행 격리 계획 / POSIX containment plan

**C1 IMPLEMENTED · C2 WITHHELD · 확인일: 2026-10-26**

POSIX **실행은 여전히 차단**되어 있다(`talkabout/cli.py`의 실행 거부, `talkabout/adapters.py`의 fail-closed).
이번에 구현한 것은 **격리 계층 C1**이고, 사용자 경로를 여는 C2는 P4 미구현 때문에 의도적으로 보류했다.
이 문서를 근거로 "POSIX 지원"이나 "sandbox"를 주장하지 않는다.

## 0. 현재 상태 (2026-10-26)

| 단계 | 상태 | 증거 |
| --- | --- | --- |
| C1 격리 계층(P1·P2·P3·P5·P6) | **구현** | `talkabout/processes.py`의 `stop_posix_group`, `tests/test_posix_containment.py` 9건 |
| Linux 실행 검증 | **통과** | WSL Ubuntu(Python 3.12)에서 3회 연속 9 passed, flake 없음 |
| Linux CI 잡 | **추가** | `.github/workflows/safe-regression.yml`의 `posix-containment`(ubuntu-latest) |
| P4 supervisor 사망 시 자식 self-kill | **미구현** | 아래 §7 참조 |
| C2 CLI 활성화 | **보류** | P4 없이 열면 supervisor가 SIGKILL로 죽었을 때 자식이 남는다 |

> 주의: 이 프로젝트의 기본 CI는 `windows-latest`였다. POSIX 코드는 그동안 **어떤 CI에서도 실행되지 않았다**는 뜻이며,
> 그래서 Linux 잡을 새로 추가했다.

## 1. 왜 지금 열지 않는가

- Windows Job Object(`KILL_ON_JOB_CLOSE` + no-breakaway + `CREATE_SUSPENDED`)는 supervisor가 죽으면 자식도 함께 죽는다.
  POSIX에 등가물이 없으면 자식이 부모보다 오래 살아남아 lease·예산 회계가 틀어진다.
- Windows 개발 환경에서는 POSIX kill 경로를 직접 검증할 수 없다. 그래서 WSL(Linux)과 Linux CI 잡을 증거로 쓴다.
- 순서는 **계획 → C1 → Linux 검증 → P4 → C2 활성화**다. C1만으로는 사용자 경로를 열지 않는다.


## 2. 필요한 속성 (containment가 아니라 lifetime)

외부 저장소 분석에서 얻은 결론: POSIX에서 "sandbox"를 주장하려면 namespace·rlimit·seccomp가 필요하지만,
TalkAbout이 실제로 요구하는 것은 **누출 없는 수명**이다. 목표 속성은 다음 6개다.

| # | 속성 | 의미 |
| --- | --- | --- |
| P1 | 프로세스 그룹 격리 | 자식을 `start_new_session`으로 띄워 pgid를 소유한다 |
| P2 | 부모 우선 종료 | 부모에게 먼저 SIGTERM, grace 후 자손으로 확장 |
| P3 | 회수 증명 | SIGKILL 후 **생존자 재확인**이 통과해야만 "종료됨"으로 기록 |
| P4 | 고아 회수 | 부모가 SIGKILL로 죽어도 자식이 스스로 `killpg(self)` 후 종료 |
| P5 | 재사용 가드 | PID 재활용을 오인하지 않는다(생성 시각·세션 id 대조) |
| P6 | 유한 종료 | 정지 경로 자체가 상한을 갖는다(이미 `bounded_shutdown`으로 반영) |

참조 구현(개념만 참조, 코드 복사 없음): `hermes-agent/tools/environments/local.py`의 pgid/hands-on kill,
`hermes-agent/agent/managed_worker.py`의 owner 사망 시 self-kill, `orca-analysis`의 `exited / live / unverifiable`
3값 verdict 어휘. 세 저장소 모두 MIT이며 코드를 복사하지 않았다.

## 3. 거부하는 것

- **sandbox 주장 금지**: namespace·seccomp·rlimit를 넣는지 않는 한 "격리된 sandbox"라고 부르지 않는다.
- **tty 역추적 미도입**: Orca의 `posix-pty-process-groups`는 PTY 호스트 전제라, 자식을 직접 spawn하는
  TalkAbout에는 과설계다.
- **fallback 금지**: 회수 증명이 실패하면 성공으로 넘어가지 않는다(`process_exit_unconfirmed`로 남긴다).
- **macOS 미지원**: 그룹 멤버 열거가 Linux `/proc` 기반이다. `/proc`이 없으면 "그룹이 비었다"를 증명할 수 없으므로
  POSIX 지원은 **Linux 한정**이며, macOS는 이 경로에서 fail-closed다(`process_exit_unconfirmed`).

## 4. 테스트 계획 → 실제 구현 (`tests/test_posix_containment.py`, 9건)

Windows에서는 실행 불가이므로 전부 `skipif(os.name == 'nt')`이고, 증거는 **WSL Linux 로컬 실행**과
**`posix-containment` CI 잡**이다.

| 계획 항목 | 구현된 테스트 |
| --- | --- |
| 그룹 격리(P1) | `test_new_session_makes_the_child_its_own_group_leader` |
| 자손 회수(P2·P3) | `test_stop_removes_a_sigterm_ignoring_descendant` |
| 리더 종료 후 그룹 소거 | `test_group_is_swept_after_the_leader_exits` |
| 상한(P6) + 멱등 | `test_stop_is_idempotent_and_bounded` (9초 이내) |
| PID 재사용 가드(P5) | `test_reused_pid_is_refused_before_any_signal` |
| 자기 그룹 보호 | `test_own_group_is_never_signalled`, `test_invalid_group_is_refused` |
| fail-closed | `test_unreadable_process_table_fails_closed` |
| 옵션 안전성 | `test_shell_and_creationflags_are_refused` |

**실행 증거(2026-10-26)**: WSL Ubuntu / Python 3.12에서 **3회 연속 9 passed**(flakes 없음).

## 5. 관문 (통과 전 활성화 금지)

- ~~위 테스트가 Linux에서 반복 실행으로 통과할 것~~ → **충족**(WSL 3회 + CI 잡).
- **P4(아래 §7) 구현** — 아직 미충족. 이것이 C2 보류의 유일한 이유다.
- `talkabout providers`/`cli connect`가 POSIX에서 어떤 범위로 허용되는지 문서에 명시할 것 — C2와 함께.
- 실제 공급자 시험은 POSIX 활성화 **이후** 별도 승인으로 한다(현재 NOTRUN).

## 6. 작업 분해

| 단계 | 내용 | 상태 |
| --- | --- | --- |
| C1 | `processes.py`에 POSIX 경로(세션·pgid·종료 사다리·회수 증명) | **완료** — 코드 + 9 테스트 + Linux CI |
| C2 | `cli.py`/`adapters.py`의 POSIX 거부 해제 + 안내 문구 | **보류**(P4 대기) |
| C3 | 문서 갱신(STATUS §4의 `POSIX 실행` 관문) | C2와 함께 |

## 7. 남은 필수 항목: P4 (supervisor 사망 시 자식 self-kill)

C1은 supervisor가 살아 있는 동안의 회수를 증명한다. supervisor가 `SIGKILL`로 죽으면 POSIX에는 Job Object 등가물이
없어 자식이 남는다. 구현 후보는 두 가지이며, 둘 다 아직 채택하지 않았다.

1. `prctl(PR_SET_PDEATHSIG)` — Linux 전용. **스레드가 만든 자식에서는 스레드 종료 시 발화**할 수 있어,
   다중 스레드 supervisor에서 조기 사망 위험이 있다. 채택하려면 spawn을 단일 스레드 구간으로 격리해야 한다.
2. 작은 escort 프로세스(`tools/`에 두는 자체 스크립트)가 부모 pid를 감시하다 부모가 사라지면 같은 그룹을
   정리하고 종료. 플랫폼 독립이지만 프로세스 한 겹이 늘고, 실행 대상 검증 규칙(지문·argv)을 다시 확인해야 한다.

P4가 들어오기 전까지 POSIX 실행은 **열지 않는다**. 그동안 이 계층은 제품 경로에서 도달 불가능하다.

| C3 | 문서 갱신(STATUS §4의 `POSIX 실행` 관문 해소) | 문서 |

현재 상태: **미착수**. 이 문서는 C1의 요구사항을 고정한 것이지 구현이 아니다.
