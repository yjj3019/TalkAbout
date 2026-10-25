# POSIX 실행 격리 계획 / POSIX containment plan

**PROPOSED / NOT IMPLEMENTED · 확인일: 2026-10-26**

이 문서는 계획이다. POSIX 실행은 여전히 차단되어 있다(`talkabout/processes.py`의 의도된 fail-closed,
`talkabout/cli.py`의 실행 거부). 이 문서를 근거로 "POSIX 지원"을 주장하지 않는다.

## 1. 왜 지금 열지 않는가

- 현재 격리는 **Windows Job Object**(`KILL_ON_JOB_CLOSE` + no-breakaway + `CREATE_SUSPENDED`)에만 있다.
  POSIX에 등가물이 없으면 자식이 부모보다 오래 살아남아 lease·예산 회계가 틀어진다.
- Windows 개발 환경에서는 POSIX kill 경로를 **실행 검증할 수 없다**. 이 프로젝트는 실행하지 않은 검증을
  통과로 적지 않으므로, POSIX 코드는 Linux CI에서만 검증 가능하다.
- 따라서 순서는 **계획 → Linux 전용 회귀 → 관문 통과 → 활성화**다.

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

- **sandbox 주장 금지**: namespace·seccomp·rlimit를 넣지 않는 한 "격리된 sandbox"라고 부르지 않는다.
- **tty 역추적 미도입**: Orca의 `posix-pty-process-groups`는 PTY 호스트 전제라, 자식을 직접 spawn하는
  TalkAbout에는 과설계다.
- **fallback 금지**: 회수 증명이 실패하면 성공으로 넘어가지 않는다(`unverifiable`로 남긴다).

## 4. 테스트 계획 (Linux CI 전용)

Windows에서는 실행 불가이므로 전부 `skipif(os.name == 'nt')`이고, **CI의 Linux 실행이 유일한 증거**다.

1. 부모 SIGKILL 후 자손이 남지 않는다(회수 증명 포함).
2. 부모 정상 종료 후 손자 프로세스(자식이 다시 spawn)가 남지 않는다.
3. grace 안에 죽지 않는 자식이 SIGKILL로 회수되고 `unverifiable`이 아닌 `exited`로 기록된다.
4. 정지 경로가 상한을 넘지 않는다(`bounded_shutdown` 회귀와 결합).
5. PID 재활용 시뮬레이션에서 다른 프로세스를 죽이지 않는다.
6. 실패 시 **성공을 보고하지 않는다**(fail-closed).

## 5. 관문 (통과 전 활성화 금지)

- 위 6개 테스트가 Linux CI에서 **반복 실행**(최소 2회, flake 확인)으로 통과할 것.
- `talkabout providers`/`cli connect`가 POSIX에서 어떤 범위로 허용되는지 문서에 명시할 것.
- 실제 공급자 시험은 POSIX 활성화 **이후** 별도 승인으로 한다(현재 NOTRUN).

## 6. 작업 분해

| 단계 | 내용 | 산출물 |
| --- | --- | --- |
| C1 | `processes.py`에 POSIX 경로(세션·pgid·종료 사다리·회수 증명) | 코드 + 6개 테스트 |
| C2 | `cli.py`의 POSIX 거부 해제 + 안내 문구 | CLI 변경 |
| C3 | 문서 갱신(STATUS §4의 `POSIX 실행` 관문 해소) | 문서 |

현재 상태: **미착수**. 이 문서는 C1의 요구사항을 고정한 것이지 구현이 아니다.
