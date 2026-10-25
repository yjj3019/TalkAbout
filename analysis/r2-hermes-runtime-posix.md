# R2 · hermes-agent 런타임·POSIX 격리·프로세스 수명 분석

대상: `hermes-agent/` (HEAD `930e5ed`, 2026-10-25, MIT — `hermes-agent/LICENSE:1-3` "MIT License / Copyright (c) 2025 Nous Research").
비교: `orca-analysis/` (1라운드 결론 = `analysis/03-runtime.md`), `opencodex/`(교차 확인만).
기준 프로젝트: TalkAbout (`talkabout/processes.py`, `runtime.py`, `agent_runtime.py`, `worker.py`).
규칙: `analysis/BRIEF-R2.md`.

> **한 줄 결론.** hermes는 POSIX에서 **"강한 격리"를 하지 않는다**. 대신 **"누출 없는 수명(lifetime)"** 을 매우 엄격하게 만든다 —
> `start_new_session` + pgid, parent-first 강제 종료 사다리, **kill receipt를 쓰기 전 생존자 재확인**, owner 사망 시 자식이 스스로
> `killpg` 후 `_exit`, systemd scope cgroup 회수, PID 재활용 가드. **TalkAbout `talkabout/runtime.py:51-59`의 무제한 `shutdown()` 결함에 대한
> 직접 해법이 hermes에 실재한다**(`tools/environments/base.py:744-776` + `agent/deadline.py:304-...`).
> 따라서 최종 판정은 **POSIX 격리 = hermes가 Orca보다 낫다**(Python 동일 스택 + 유한 shutdown + 검증된 회수). 단 **sandbox 보안**은 hermes에도 없다.

---

## 0. 판정 요약

| # | 질문 | hermes 답 | 근거 강도 | TalkAbout 판정 | 최적 저장소 | 비용 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | POSIX에서 무엇으로 격리하나 | **setsid/pgid + (가능하면) systemd scope cgroup**. namespace·rlimit·seccomp **없음** | 매우 강(부재까지 명시적) | `[채택]` pgid+session / `[차용]` scope | **hermes** | M |
| 2 | 고아·좀비 회수 | kill ladder + **생존자 검증 후 receipt** + owner-death self-kill + cgroup reaper | 매우 강 | `[채택]` | **hermes** | M |
| 3 | 타임아웃·hang·강제종료 사다리 | 통합 deadline 계층 + turn 무진행 감지 + CPU 신호 확장 + bounded exit | 매우 강 | `[채택]` `talkabout/runtime.py:51-59` **직접 해법 있음** | **hermes** | S~M |
| 4 | 로컬/원격 백엔드 추상화 | `BaseEnvironment` ABC + backend table + `pid_scope` | 강 | `[제외]`(P0 원칙) / `[차용]` `pid_scope` 어휘 | hermes(개념만) | S |
| 5 | 서브에이전트 병렬 | in-process delegation + managed worker(별도 프로세스) | 중 | `[대조]`(TalkAbout journal이 우위) | — | — |
| 6 | startup watchdog | 300s 상한 + phase lease + **CPU 진행 신호**(최대 3회) + forensics dump 후 강제 exit | 매우 강 | `[채택]` | **hermes** | S |
| 7 | **최종: POSIX 격리 참조** | — | — | **hermes 우선, Orca는 보조** | **hermes** | M |

**핵심 재정의(이 보고서의 가장 중요한 판단).** TalkAbout이 필요한 것은 "sandbox"가 아니라 **"누출 없는 프로세스 수명"** 이다.
1라운드에서 내가 "POSIX 격리 = 비용 L"로 적은 것은 **containment(보안 격리)** 기준이었다. 그 기준이면 hermes도 부적합하다(§2.5).
그러나 기준을 **"고아 0 / 좀비 0 / 강제 종료 검증 / shutdown 유한"** 으로 잡으면 hermes가 실증한 형태로 **M**에 도달한다(§8).

---

## 1. 범위와 방법 (21,220파일 전수 정독 금지 준수)

`analysis/BRIEF-R2.md:67` 규칙에 따라 전수 정독하지 않았다. **질문별로 파일을 지목해 읽었다.**

읽은 파일(전부 실제 확인): `tools/process_registry.py`(2,527줄 중 spawn/kill/termination 구간),
`tools/process_registry_termination.py`(191 전문), `tools/process_registry_checkpoint.py`(159 전문),
`tools/environments/local.py`(POSIX teardown 구간 780-1,150), `tools/environments/base.py`(ABC·wait·execute 구간 505-780),
`tools/environments/docker_ownership.py`(grep), `tools/terminal_tool_backends.py`(grep), `agent/deadline.py`(1-481 중 1-140·190-330·380-481),
`agent/turn_liveness.py`(1-220), `agent/managed_worker.py`(130-205), `agent/transports/codex_app_server.py`(40-105),
`gateway/cgroup_cleanup.py`(128 전문), `gateway/session_stall.py`(76 전문), `gateway/status.py`(395-470), `hermes_startup_watchdog.py`(340-435),
`hermes_state_pidns.py`(128 전문), `hermes_cli/local_runtime/processes.py`(142 전문), `hermes_cli/resource_limits.py`(grep),
`hermes_cli/main_desktop.py`(1148-1188), `hermes_cli/config_defaults.py`(1359-1385), 그리고 각 디렉터리의 `AGENTS.md`(설계 계약 진술).

**미확인 / 찾지 못한 것** (존재를 단정하지 않음):
- 로컬 POSIX 자식에 대한 **rlimit / seccomp / landlock / bwrap / namespace 격리**: `bwrap|nsjail|unshare|setrlimit|RLIMIT_|seccomp|landlock|chroot|CLONE_NEW`를
  비테스트 `.py` 전체에서 grep → 나온 것은 ① 데스크톱 Electron Chromium sandbox용 `unshare` **탐지**(`hermes_cli/main_desktop.py:1157-1173`),
  ② hermes **자기 프로세스**의 `RLIMIT_NOFILE` 상향(`hermes_cli/resource_limits.py:53-78`), ③ `cron/lifecycle_guard.py:316`의 **금지 명령 목록**뿐이었다.
- `opencodex/`는 내 범위가 아니다. 최종 판정의 3자 비교를 위해 **2개 파일만** 열었다(§8.3): `opencodex/LICENSE`(MIT 확인),
  `opencodex/src/adapters/coding-agent/turn.ts`(kill ladder 391-421). 나머지는 **미확인**이며 다른 트랙 판정을 존중한다.

---

## 2. Q1 — POSIX에서 무엇으로 격리하나

### 2.1 계층 요약

| 계층 | hermes 사용 | 근거 | 비고 |
| --- | --- | --- | --- |
| new session / own process group | **예** | `tools/environments/local.py:1109-1114`, `:1116-1123` | pgid + leader start-time을 Popen 객체에 스탬프 |
| systemd scope(cgroup) | **조건부**(Linux + user bus + linger) | `tools/process_registry.py:104-124`, `:249-308`, `:330-348` | 불가하면 **명시적 degrade 경고** |
| PID namespace | **아니오** — **신원(identity)으로만** 사용 | `hermes_state_pidns.py:1-17`, `:52-60` | 격리가 아니라 "이 holder PID를 믿어도 되나" 판정 |
| rlimit | **아니오**(자식에 미적용) | `hermes_cli/resource_limits.py:53-78` | hermes 자신의 fd 한도만 |
| seccomp / landlock / bwrap / nsjail | **없음**(로컬 터미널 경로) | grep 결과 §1 | Docker 백엔드에만 `--pids-limit 64`·`no-new-privileges`(`tools/environments/docker_ownership.py:56`) |
| env 화이트리스트/스크럽 | **예** | `hermes_cli/local_runtime/processes.py:90-107`, `agent/transports/codex_app_server.py:96-105` | TalkAbout 동등 → `[대조]` |

### 2.2 session + process group (핵심)

`tools/environments/local.py:1109-1114`:

```python
proc = subprocess.Popen(
    args, text=True, env=_make_run_env(self.env), encoding="utf-8", errors="replace",
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
    start_new_session=True, cwd=self.cwd,
    **({"creationflags": windows_hide_flags()} if _IS_WINDOWS else {}))
```

직후 `:1115-1123`에서 **pgid와 leader 시작시각을 스탬프**한다:

```python
proc._hermes_pgid = os.getpgid(proc.pid)
proc._hermes_pgid_start = get_process_start_time(proc.pid)   # PID/PGID 재활용 탐지용 baseline
```

이 스탬프가 이후 모든 kill의 **PID 재활용 가드** 근거가 된다(`_leader_is_ours`, `:898-925`). 주석이 이유를 밝힌다
(`:901-907`): "same-host readings drift ~1 s on macOS(#117505), and exact equality made the guard refuse to kill live, legitimately-owned groups."
→ **동일 호스트에서 두 번 읽은 start time은 정확히 같지 않다**는 실측을 반영한 drift-tolerant 비교(`gateway/status.py:457-461`, tolerance 200 = 2초).

`tools/process_registry.py:1218-1222`는 **cgroup을 써도 session 분리가 여전히 필요**함을 명시한다:

```python
# start_new_session is REQUIRED with systemd-run --scope too: the scope does not
# give the worker a new session, so from an interactive TUI the worker would
# share the foreground process group and background spawns would stop the whole
# session (observed as dead TUIs in state T). Cgroup isolation is unaffected —
# the scope attaches to the invoked process, not the spawning session.
```

**TalkAbout 관점**: 이건 TalkAbout의 누락 지점과 정확히 일치한다. TalkAbout은 POSIX 경로가 없어서
`start_new_session`도 pgid 스탬프도 없다(`talkabout/processes.py:111-112`에서 즉시 예외). `[채택]` 비용 **S**.

### 2.3 systemd scope(cgroup) — 조건부·명시적 degrade

`tools/process_registry.py:104-124`의 주석이 설계 근거를 그대로 남긴다:

```python
# ``systemd-run --user --scope`` gives the worker its own transient cgroup. Usability is
# ... systemd-oomd to kill the ENTIRE gateway — taking down the messaging control plane and silently losing the
# active turn. We probe whether ``systemd-run --user --scope`` is actually usable ...
```

- probe: `:249-308` (`systemd-run --user --scope`로 실제로 cgroup이 만들어지는지 실행해 확인, 실패 시 사유 로그).
- argv 빌더: `:179-192`(`_systemd_scope_argv`), `:330-348`(`_build_systemd_scope_argv`, memory max 포함).
- **실패는 예외가 아니라 정책**: `RestartSafeScopeUnavailable`(`:349-357`) + `_warn_scope_degraded_once`(`:358-380`)
  → "worker shares the gateway cgroup"라고 **한 번 경고**하고 계속 간다.
- teardown: `_stop_systemd_unit`(`:473-507`) — scope stop이 **authoritative cleanup**이다(`:1246-1249` comment:
  "Scope teardown is the authoritative cleanup for the worker cgroup").

Orca도 같은 아이디어를 `daemon-cgroup-scope.ts`에서 구현하지만(1라운드 §3.3), Electron 앱 수명에 결박된 형태다.
hermes 쪽이 **"없으면 어떻게 되는가"를 코드로 정의**해 두었다는 점에서 TalkAbout 이식이 쉽다. `[차용]` 비용 **M**.

### 2.4 PID namespace — 격리가 아니라 신원

`hermes_state_pidns.py:1-17`은 목적을 명확히 못박는다: "A `pid=<n>` holder is relative to its writer's PID namespace;
a reader in a sibling namespace … sees a live holder as absent and would reclaim its unexpired row."
즉 **namespace 안에 가두는 코드가 아니라, namespace가 다를 때 PID를 믿지 않는 코드**다.

두 정책(같은 파일 `:9-13`): TTL 있는 lease holder는 **STRICT**(불확실하면 만료까지 defer — "a false reclaim ends a live turn"),
만료 없는 flock은 **LEGACY**(unstamped도 계속 probe — 아니면 고아 락 정리가 영구 불가). `:96-107`의 `_qualify`가 그 행렬이고,
`:104-105`는 "our own lookup failed: unknown authority is not authority"로 **fail-closed**한다.

**TalkAbout 관점**: TalkAbout은 `ConnectorLease`로 OS 락을 잡지만(`talkabout/connector.py:215-242`) namespace 구분이 없다.
컨테이너·`PrivatePIDs=` 환경에서 여러 프로세스가 한 journal을 볼 계획이 없다면 지금은 불필요 → `[제외]`(이유: 현재 배포 형태가 단일 namespace).
단, **"PID+시작시각 스탬프를 홀더 문자열에 박는다"** 는 형식은 참고 가치가 있다 → `[차용]` S.

### 2.5 없는 것 — 그래서 "sandbox"가 아니다

로컬 POSIX 경로에는 rlimit/namespace/seccomp가 없다(§1 grep). hermes가 자식에 부여하는 것은
**"자기 session/pgid + (있으면)자기 cgroup"** 뿐이고, 파일시스템·네트워크·권한 경계는 아니다.
유일하게 실제 자원 상한이 있는 곳은 Docker 백엔드다: `tools/environments/docker_ownership.py:56`
`["--security-opt", "no-new-privileges", "--pids-limit", "64", ...]`.

**판정**: `[채택]` pgid+session / `[차용]` 시스템 scope / `[제외]` pidns(현 배포형태) — **비용 M**. 부착: `talkabout/processes.py`.

---

## 3. Q2 — 자식 프로세스 정리, 고아/좀비 회수

### 3.1 강제 종료 사다리 (부모 먼저 → 자식 → SIGKILL)

`tools/process_registry_termination.py:33-131` `_terminate_host_pid`. 순서가 계약이다(`tools/AGENTS.md`에도 동일 문장):

1. **신원 재검증**: `:45-49` start-time mismatch면 **아무것도 죽이지 않는다** —
   "a leaked orphan beats tree-killing someone's browser."
2. **자손 스냅샷 먼저**: `:72-77` — "once the parent exits, psutil can no longer reliably find children".
3. **부모만 SIGTERM**: `:82-83` — "Let self-managing parents (notably Chromium/Electron) shut down their tree before touching children.
   Killing their zygotes first can turn a graceful browser shutdown into a crash dump."
4. **grace 대기**(`terminal.daemon_term_grace_seconds`, 기본 2.0초 — `tools/process_registry.py:1079-1081`): `:85-95`.
5. **살아남은 자손에게 SIGTERM**: `:99-104`.
6. **SIGKILL 단계**: `:115-130`. 여기서 **재스냅샷**한다(`:118-125`):
   "A parent that ignored SIGTERM … can spawn children the first snapshot never saw. Re-snapshot while it is still alive:
   once it is SIGKILLed they reparent to init and nothing can find them again."
7. **좀비 인식**: `_proc_alive`(`:23-30`)가 `status() != psutil.STATUS_ZOMBIE`까지 확인 — "already dead, just unreaped".

`psutil.wait_procs`를 **신뢰하지 않는다**는 주석(`:110-114`)이 실전 교훈이다:
"its gone/alive partition is deliberately NOT trusted: it reaps via Process.wait() and mis-partitions across zombie transitions".

### 3.2 회수 확인 — "죽였다"와 "죽었다"의 분리

`tools/process_registry_termination.py:143-161`:

```python
# SIGKILL / taskkill are asynchronous: the kernel needs a scheduling tick to
# tear the process down and the parent must reap it before poll()/isalive()
# stop saying "alive". Verifying survivors in that window flagged every
# escalated kill as incomplete.
_KILL_SETTLE_SECONDS = 1.0
```

`_post_kill_survivors`는 1초 동안 재시도하며 생존자를 모으고, **판정 불가는 생존자로 계산**한다(fail-closed, `:152-155`:
"anything unverifiable counts as a survivor, so a kill that leaves a live tree can never write a killed receipt").

그 결과가 `tools/process_registry.py:2166-2188`에서 **receipt를 거부**하는 형태로 나타난다:

```python
survivors = [] if signal_race_exited else self._post_kill_survivors(session)
if survivors:
    logger.warning("Kill incomplete for %s: %d process(es) still alive (%s) — session kept running", ...)
    return {"status": "error", "error": f"Kill incomplete: ...", "survivors": survivors, "process_running": True}
```

scope stop 실패도 같은 방식으로 receipt를 막는다(`:2189-2201`). **세션을 끝내지 않고 계속 kill 가능 상태로 남긴다**.

**이것이 1라운드에서 내가 Orca의 `exited/live/unverifiable` verdict를 채택하라고 한 이유와 같은 규율**이며,
hermes에는 **Python 코드로 이미 존재**한다. TalkAbout은 현재 `talkabout/processes.py:96-98`에서 `process_exit_unconfirmed`로 실패만 보고하고
`cleanup_confirmed`를 남기지만(`talkabout/agent_runtime.py:315-327`), **생존자 PID 수집·재시도·세션 유지**는 없다.

### 3.3 owner가 죽으면? — 이식 가능한 PDEATHSIG 대체물

`agent/managed_worker.py:158-174`:

```python
# The owner's control pipe closing under a live worker means the owner died (a closing owner
# signals the worker itself). The agent is interrupted at once; cooperative tools get this long.
ORPHAN_GRACE_SECONDS = 10

def _die_with_owner():
    """Then end this worker and, on POSIX, every descendant still in its process group: the owner
    spawns it as a group leader (``start_new_session``). Tools that open their own session (the
    terminal tool, background processes meant to outlive the turn) are not in it. Never a group
    this process does not lead."""
    def kill():
        if os.name != 'nt' and os.getpgrp() == os.getpid():
            import signal
            os.killpg(os.getpid(), signal.SIGKILL)
        os._exit(1)
    threading.Timer(ORPHAN_GRACE_SECONDS, kill).start()
```

**이 12줄이 Linux `prctl(PR_SET_PDEATHSIG)`의 이식 가능한 대체물**이다. 조건 `os.getpgrp() == os.getpid()`가
"내가 리드하지 않는 그룹은 절대 건드리지 않는다"를 보장한다(Mac에서도 동작 — PDEATHSIG는 Linux 전용인데 이건 아니다).

TalkAbout에 그대로 대응시키면: `talkabout/runtime.py:33-72` `bounded_call`의 `stop()`이 **자기 프로세스 그룹에 self-kill 타이머를 걸고 즉시 반환**하게
만들면, 무제한 `shutdown()` 문제(1라운드 Top 5 #2)가 사라진다. `[채택]` 비용 **S**.

### 3.4 owner가 *아닌* 잔여물 — cgroup reaper

`gateway/cgroup_cleanup.py:1-9`가 역할을 규정한다: systemd unit의 `ExecStopPost=`로 돌면서
"long-lived helpers the gateway doesn't track(adb, platform bridges) that would otherwise be orphaned in the cgroup and block `Restart=always`"를 SIGKILL한다.
`cgroup.kill` 대신 **per-PID kill**을 쓰는 이유도 실측이다(`:6-8`): "the kernel has returned `EINVAL` on the cgroup-wide kill while per-PID signal delivery still works."

안전장치 3중:
- `:43-56` **systemd가 띄웠을 때만** 동작(`/proc/<ppid>/comm == "systemd"`), 아니면 거부 — "reaping would SIGKILL that process". PID 1 지름길 없음(컨테이너에서 gateway가 PID 1일 수 있으므로).
- `:59-74` cgroup에 **살아있는 gateway**가 있으면 거부.
- `:99-111` 판정 이후 **다시 읽어서** kill("the guard above can take seconds … `others` may hold exited/reused PIDs").

### 3.5 크래시 후 재기동 시 고아 복구

`tools/process_registry_checkpoint.py:20-56`: 실행 중 프로세스 메타를 파일 락 하에 **atomic JSON**으로 기록
(명령은 `redact_sensitive_text`로 마스킹 — "Recovery uses command only for display", `:35-39`).

`recover_from_checkpoint`(`:72-159`)의 판정이 정확하다:
- `pid_scope != "host"` → **복구하지 않음**("in-sandbox PIDs mean nothing once the env handle is gone", `:100-104`).
- start-time 불일치(`reused`) → **adopt도 signal도 안 함**(`:116-122`).
- **unreadable ≠ reused**: `:105-109` "A start-time probe that cannot be read is not proof the PID was reused — dropping it would leave a live child unsupervised,
  and marking it exited would invent a completion."
- 죽은 wrapper인데 scope가 남아 있으면 scope를 stop하고, 실패하면 **checkpoint에서 지우지 않고 다음 기동으로 미룬다**(`:123-131`).

**판정**: `[채택]`. 부착: `talkabout/processes.py`(신규 POSIX 경로), `talkabout/worker.py`(journal에 `pid_scope`/`host_start_time` 상당 필드), `talkabout/connector.py`(기동 시 고아 스윕). 비용 **M**.

---

## 4. Q3 — 타임아웃 계층, hang 감지, 강제 종료 사다리

### 4.1 통합 deadline 계층 — "우리 deadline ≠ 공급자 timeout"

`agent/deadline.py:1-13` docstring이 계약을 선언한다:
`resolve_timeout(config > env > default)`, `clamp_timeout`, `run_bounded_sync/async`, `kill_process_tree`.
불변식: "operation exceptions propagate unchanged … a timeout here is OUR deadline, not the provider's (classify `DeadlineExpired` distinctly from transport timeouts); `None`/non-positive means unbounded."

- `:57-59` `MAX_SAFE_TIMEOUT_S`: macOS `time_t` 오버플로(#83220), Windows `WaitForSingleObject` DWORD-ms 한계 실측까지 반영.
- `:120-132` `clamp_timeout`: NaN/비수치 → 경고 후 unbounded, 초과분 캡. (NaN이 `>0` 비교를 통과해 타임아웃을 **조용히 끄는** 사고를 막는다.)
- `:235-298` `run_bounded_async`: **daemon `threading.Timer`** 로 deadline 구동 — "a blocked event loop cannot disable it".
  2차 타이머가 `faulthandler.dump_traceback(all_threads=True)`로 **루프가 막힌 프레임**을 남긴다(`:221-232`, `:264-270`).
  timeout 시 task를 **abandon**(취소 후 await하지 않음) — "cancellation-shielded scopes are exactly the paths that wedge"(`:247-248`).
- `:304-...` `run_bounded_sync`: 동일 원리를 sync에. 타임아웃 시 워커를 abandon하고 `on_timeout`을 **호출자 스레드에서 best-effort** 실행
  — "every timeout leaks one daemon thread, so do NOT use per-item in hot loops"(`:313-314`)라는 비용도 명시.
- `:422-481` `kill_process_tree`: Windows는 `taskkill /F /T`(`:431-446`), POSIX는 **freeze→rescan→bottom-up signal→(자기 그룹일 때만) killpg**.
  `:463-475`의 주석이 Orca 1라운드의 발견과 같은 결론이다:
  "getpgid→killpg has an inherent TOCTOU shared by every killpg site; the psutil sweep below is identity-aware (PID + create time) and does not."
  그리고 `:470-472` `if pgid == pid:` — "the == check avoids signalling the caller's group".

### 4.2 ★ `talkabout/runtime.py:51-59` 무제한 shutdown 결함의 **직접 해법**

TalkAbout 결함(1라운드 §9-2): `bounded_call`이 타임아웃되면 유일한 탈출로가 `runtime.shutdown()`을 **상한 없이** 호출한다.
hermes는 이 문제를 **구조로** 푼다 — `tools/environments/base.py:744-776`:

```python
def _on_timeout() -> None:
    if proc_holder:
        self._kill_spawned_tree(proc_holder[0])

# Hard wall-clock backstop: ``_wait_for_process`` polls to ``effective_timeout`` on the
# tool thread; if that is the event-loop thread, or the wait never returns (Windows
# pipe/poll hang), every asyncio timer is silently disabled. ``run_bounded_sync`` drives
# expiry from a daemon worker + ``Event.wait`` so a blocked loop cannot disable it; the
# grace lets the inner loop return the partial-output 124 path.
from agent.deadline import run_bounded_sync
bound_s = float(effective_timeout) + _EXECUTE_WAIT_BOUND_GRACE_S     # grace = 2.0s (tools/environments/base.py:45)
bounded = run_bounded_sync(_spawn_and_wait, bound_s, label=..., on_timeout=_on_timeout)
...
if bounded.timed_out:
    result = {..., "returncode": 124}
    result["hermes_timed_out"] = True
```

핵심 3가지:
1. **호출자는 deadline + grace에서 반드시 돌아온다** — kill이 느리든 말든.
2. kill은 `on_timeout`으로 **호출자 스레드에서** 시작되지만 `bounded_call`의 반환을 붙잡지 않는다.
3. 반환값에 `hermes_timed_out: True` 플래그가 붙어 "명령의 exit 124"와 "**우리** deadline"을 구분한다(`:575-576` 주석).

부수적으로 `_wait_for_process`(`:508-588`)는 폴링 사이 **interrupt/yield를 매 tick 확인**하고(`:551-568`),
타임아웃 시 `_kill_and_join()`(`:569-576`), `KeyboardInterrupt/SystemExit`에도 `_kill_and_join()`(`:582-588`)로 **자식 orphan을 만들지 않는다**.

그리고 hard-exit 경로는 **0.5초**를 넘지 않는다(`tools/environments/base.py:65` `_HARD_KILL_BUDGET_S = 0.5`, `:110-113`).

**판정**: `[채택]`. 이것이 1라운드 Top 5 #2의 **구현 스펙**이다. TalkAbout에서는 `talkabout/runtime.py:33-72`를
"daemon worker + `join(timeout)` + `on_timeout`에서 kill 요청" 구조로 바꾸면 된다. 비용 **S**.

### 4.3 "무엇을 신호로 삼나" — turn 무진행(idle) + CPU

1라운드에서 Orca의 hang 감지는 **Electron 메인스레드 heartbeat 공백**뿐이었다. hermes는 **두 가지 신호**를 더 갖는다.

**(a) turn 무진행(idle) 감지** — `agent/turn_liveness.py:1-9`가 문제를 정확히 진술한다:
"A turn can wedge mid-flight with no error and its **durable lease still renewing**, so nothing ever frees the session."
- 기본값: `:23-24` `DEFAULT_TURN_LIVENESS_TIMEOUT_S = 600.0`, `POLL = 15.0`.
- **sleep 보정**: `:113-114` `AwakeIdleMeter` — "A sleeping host is not a stalled turn: time asleep never counts toward the bound."
  (1라운드에서 내가 Orca의 `tickGap > checkInterval*3` 슬립 보정을 채택하라고 한 것과 **같은 문제를 더 정식으로** 푼다.)
- **오탐 억제(핵심)**: `:127-148` — ① 관측 surface(로그/스택 덤프)는 **"복구 시작"만 주장**하고 abort를 주장하지 않는다,
  ② `_commit_abort(snapshot)`가 **거부권**을 갖는다(진행이 재개됐으면 abort 취소), ③ 확정 후에만 lease 갱신 중단·확정 surface.
- **경쟁 안전**: `:150-157` `(generation, activity_ts)`를 **활동 스탬프와 같은 락**에서 샘플링하고, commit 콜백이 같은 락에서 재검증한다(`:6-8`).
- **증거 보존**: `:159-194` stall 시 **턴 스레드 스택**을 덤프("the frame at the moment of the stall is the only direct evidence").
- 게이트웨이 알림 정책 분리: `gateway/session_stall.py:1-7` "Consumes `AIAgent.get_activity_summary()` as the **single progress source** …
  it never derives a parallel progress clock from turn-start or inbound timestamps", `:27-34` "Unknown progress holds the latch: observation gaps are not recovery."

**(b) CPU 진행을 신호로** — `hermes_startup_watchdog.py:355-411`. deadline 만료 시 우선순위가 코드로 고정돼 있다:
1. **phase progress lease** 있으면 무조건 존중(`:374-386`);
2. 없으면 **프로세스 CPU 델타 ≥ 1.0s**면 **최대 3회**까지 deadline 연장(`:59-69` 상수, `:387-403`),
   로그 문구가 "phases doing long legitimate work should call `report_startup_progress` instead";
3. 둘 다 없으면 fire(`:404-411`).
→ **"무출력/무진행"을 1차 신호로, "CPU 소비 중"을 2차 신호로 두는 계층**이다. 출력 로그가 없는 startup 구간에서 특히 유효하다.

### 4.4 강제 종료 사다리 요약(hermes 전체)

| 단계 | 무엇 | 어디 |
| --- | --- | --- |
| 1 | 취소 요청(협조) | `agent/managed_worker.py:188-196` (`request_hard_interrupt`) |
| 2 | 부모 SIGTERM | `tools/process_registry_termination.py:82-83` |
| 3 | grace 대기(2.0s) | `:85-95`, `tools/process_registry.py:1079-1081` |
| 4 | 자손 SIGTERM | `:99-104` |
| 5 | 재스냅샷 후 SIGKILL | `:118-130` |
| 6 | scope stop(cgroup) | `tools/process_registry.py:2149`, `:2226-2233` |
| 7 | 생존자 검증(1.0s settle, fail-closed) | `tools/process_registry_termination.py:143-161` |
| 8 | receipt 기록 또는 **거부** | `tools/process_registry.py:2177-2221` |
| 9 | owner 사망 시 self-kill(10s) | `agent/managed_worker.py:158-174` |
| 10 | process 종료 후 cgroup 잔여 sweep | `gateway/cgroup_cleanup.py:77-111` |

**판정**: `[채택]` — `talkabout/runtime.py:51-59` 해법 **있음**(§4.2). 부착: `talkabout/runtime.py`, `talkabout/agent_runtime.py`, `talkabout/worker.py`. 비용 **S~M**.

---

## 5. Q4 — 원격/컨테이너 백엔드 추상화 (TalkAbout 판정: `[제외]`)

hermes는 **6종 내장 백엔드**를 하나의 ABC로 묶는다.

- ABC: `tools/environments/base.py:263-342` `BaseEnvironment(ABC)` — abstract는 사실상 `cleanup()` 하나뿐(`:339-342`)이고,
  핵심 동작은 공통 구현이다: `execute()`(`:669-789`), `_wait_for_process()`(`:508-588`), `_kill_process()`(`:614`), `fetch_file()`(`:344-...`).
  즉 **"spawn/wait/kill/timeout 계약은 공통, 전송만 교체"**.
- 레지스트리: `tools/terminal_tool_backends.py:12-16`(6개 import), `:52` `_BUILTIN_BACKENDS = "local, docker, modal, daytona, vercel_sandbox, ssh"`,
  `:56-72` 백엔드별 kwargs 테이블. 새 백엔드는 `elif`가 아니라 **테이블 행 추가**(`tools/AGENTS.md` 규칙과 일치).
- 백그라운드 프로세스의 원격 실행: `tools/process_registry.py:1276-1304` `spawn_via_env` — 샌드박스 안에서
  `nohup bash -lc ... > log 2>&1; rc=$?; printf ... > exit; echo $! > pid` 래퍼로 띄우고 **pid/log/exit 파일**로 폴링한다.
  실패 시 "launch wrapper exited ... without a PID"로 **Popen 실패처럼 raise**(`:1297-1301`).
- **격리 수준이 백엔드별로 다르다는 사실을 타입으로 표현**: `pid_scope`가 `"host"`/`"sandbox"`(`tools/process_registry.py:565`),
  그리고 생존자 검증이 샌드박스에서는 **불가능함을 명시**(`tools/process_registry_termination.py:150-155`
  "Sandbox (env) sessions have no host-visible tree and are unverifiable by design").

**판정**: `[제외]` — TalkAbout P0는 "플랫폼이 모델을 실행하지 않는다"(`docs/ARCHITECTURE.md`)이므로 원격 실행 계층은 정면 충돌.
단 **`pid_scope` 어휘**(호스트 PID로 검증 가능한가 / 불가능한가를 데이터로 들고 다니기)는 TalkAbout journal에 그대로 쓸 수 있다 → `[차용]` **S**.
최적 저장소: **hermes**(개념만), 코드 이식은 없음.

---

## 6. Q5 — 서브에이전트 병렬 실행 vs TalkAbout S2

hermes에는 **두 가지** 자식 실행 형태가 있다.

1. **in-process delegation**(기본): `hermes_cli/config_defaults.py:1373-1375` `max_concurrent_children: 10`("Max parallel children per batch AND max concurrent background delegation units"),
   `:1379-1381` `max_spawn_depth: 1`, orchestrator 스위치. `run_agent.py:1184-1199`가 한 turn에서 `delegate_task` 호출 수를 cap.
2. **managed worker**(별도 프로세스): `agent/managed_worker.py` — control pipe로 stop/interrupt를 받고,
   **프로세스 시작 시 config를 freeze**(`:132-143`: "The session's frozen config … is what every config reader in this process returns afterwards"),
   owner 사망 시 self-kill(§3.3). 즉 hermes도 **"격리 경계 = 프로세스"** 를 선택적으로 쓴다.

**실패 전파**: `tools/AGENTS.md`(계약 진술)에 따르면 자식의 백그라운드 프로세스는 teardown에서 kill되고,
`orphaned_processes`/`unread_completions`로 부모 결과에 **명시적으로 올라온다**. `process_manage(action="handoff")`는 소유권을 부모로 이전한다
(`tools/process_registry.py`의 `transfer_ownership`).

**TalkAbout S2와 비교**:
- TalkAbout은 **서로 다른 벤더 런타임 2개**를 한 worker에서 `RoomRuntimePool`(limit 8, key = `(room_id, generation, membership_revision)`)로
  돌리고 `session_journal`로 `call_started→uncertain` 펜스를 건다(1라운드 §9 표). hermes delegation은 **같은 agent의 자식들**이라
  벤더 교차·durable journal이 없다 — `tools/AGENTS.md`가 직접 말한다: "**Durability:** background delegation is process-local;
  work that must survive restart uses `cronjob` or `terminal(background=True, notify_on_complete=True)`."
- hermes의 자식 무진행 감지는 **기본 꺼짐**이다: `hermes_cli/config_defaults.py:1366-1369` `child_timeout_seconds: 0` — "0 = no cap".
  TalkAbout은 항상 4중 유한 상한을 건다(`talkabout/worker.py:407-409`).

→ **판정**: `[대조]`. **TalkAbout이 우위**(durable journal + 항상 켜진 유한 상한 + 벤더 교차 격리).
hermes에서 가져올 것은 (a) `max_concurrent_children` 같은 **명시적 동시성 cap + 초과분 동기 실행** 정책,
(b) 자식 teardown 결과를 `orphaned_processes`로 **부모에게 보고**하는 어휘다. 비용 **S**.

---

## 7. Q6 — startup watchdog이 감시하는 것과 상한

`hermes_startup_watchdog.py:1` — "Startup-liveness watchdog — respawn a gateway that wedges **before its loop runs**".
즉 **이벤트 루프가 돌기 전** 구간(임포트·마이그레이션·어댑터 연결)의 교착을 잡는다. Orca 1라운드의 hang watchdog(메인스레드 heartbeat)과 목적이 같다.

- 상한: `:43-44` `DEFAULT_STARTUP_WATCHDOG_TIMEOUT_S = 300.0`, `_MIN_TIMEOUT_S = 30.0`; env override는 floor 클램프(`:118-135`).
- phase lease: `:222-243` `lease(expected_s, phase)` — 특정 단계가 "나 이만큼 더 걸린다"고 선언하면 **무조건 존중**(`:374-386`), `_MAX_LEASE_S = 900.0`(`:74`).
- CPU 확장: §4.3(b).
- fire 시 동작: `:264-325` `_fire` — **forensics 먼저**(faulthandler 덤프 + 한 줄 JSON 메타 `:147-154`), 그리고 exit.
  exit 자체도 `_exit_escort`(`:326-337`)로 **독립 상한**을 건다("Hard-exit if the forensic fire path wedges"). 최종 `os._exit`(`:343-345`).
- 종료 코드 `SERVICE_RESTART_EXIT_CODE = 75`(`:48`) — systemd `Restart=`가 의도적으로 재기동하게 만든다.
- 계측: `_mark_lifecycle_exit`(`:163-176`)로 lifecycle sentinel에 남긴다.

**왜 이게 TalkAbout에 유용한가**: TalkAbout worker도 "poll→claim→runtime 호출" 루프가 **돌기 전** 구간(프로바이더 binary probe,
journal 초기화, `runtime_environment` 조립)이 있다. `talkabout/cli.py:213-216`의 `probe()`가 실패하면 `SystemExit`이고, 그 전에 **교착하면 아무도 못 잡는다**.
그리고 이 watchdog의 **"lease > CPU > fire" 우선순위**는 TalkAbout의 "정상이지만 느린 startup"을 죽이지 않는 정책으로 그대로 쓸 수 있다.

**판정**: `[채택]` 비용 **S**. 부착: `talkabout/cli.py`(run 루프 진입 전), `talkabout/runtime.py`(lease 헬퍼).

---

## 8. ★ 최종 판정 — POSIX 격리 참조: **hermes vs Orca**

### 8.1 판정: **hermes가 낫다** (조건: 기준을 "lifetime"으로 잡을 때)

| 평가 축 | hermes | Orca(`posix-pty-process-groups` 계열) | 승자 |
| --- | --- | --- | --- |
| 언어/스택 이식성 | Python 3.14 (TalkAbout과 동일) | TypeScript/Electron | **hermes** |
| pgid 확보 | 직접 spawn하므로 `os.getpgid` 1회 + 스탬프(`tools/environments/local.py:1117-1123`) | tty·session-id로 **역추적**(`src/main/pty/posix-pty-process-groups.ts:191-220`) | **hermes**(TalkAbout은 자식을 직접 띄우므로 추적 불필요) |
| 그룹 종료 확인 | `killpg(pgid,0)` + `proc.poll()` 재수확(`tools/environments/local.py:859-877`) | `ps -axo pgid=,state=` 파싱 + `Z` 판정(`src/packages/process-host/src/process-tree-termination.ts:81-97`) | **hermes**(서브프로세스·BusyBox dialect 문제 없음) |
| setsid 이탈자 sweep | **있음**(`tools/environments/local.py:880-895` — 스냅샷 후 그룹 밖 생존자 kill) | 있음(pgid 재조회) | 동등 |
| PID 재활용 가드 | start-time fingerprint + drift tolerance(`tools/environments/local.py:898-925`, `gateway/status.py:444-461`) | inode/`lstart`+pgid(`pty-descendant-termination.ts`) | 동등 |
| **유한 shutdown** | **있음** — `run_bounded_sync` + `on_timeout`(`tools/environments/base.py:744-776`) | **없음**(Electron 앱이 소유자라 daemon 수명이 다름) | **hermes** ★ |
| **검증된 kill receipt** | `_post_kill_survivors` + receipt 거부(`tools/process_registry.py:2177-2221`) | `exited/live/unverifiable` verdict(에러 반환만) | **hermes**(세션 유지·재시도까지) |
| owner 사망 처리 | self-`killpg` + `_exit`(`agent/managed_worker.py:164-174`) | cgroup/launchd 소유자 분리(다른 문제) | **hermes** ★ |
| cgroup | systemd scope + **명시적 degrade**(`tools/process_registry.py:104-124, 349-380`) | systemd scope(daemon 전용) | 동등 |
| 진짜 격리(권한·fs·net) | **없음** | **없음** | 무승부 |

**★ 표시가 결정적이다.** TalkAbout 결함 2건(`talkabout/runtime.py:51-59` 무제한 shutdown, 생존자 미확인)은 **hermes에만 해법이 있다**.
Orca는 앱이 daemon을 소유하는 구조라 "호출자가 기다리지 않고 반환" 문제를 애초에 풀 필요가 없었다.

### 8.2 지목: TalkAbout이 실제로 참조할 코드 경로

| 우선 | 참조할 경로 | 무엇을 가져오나 |
| --- | --- | --- |
| 1 | `tools/environments/local.py:928-985` (`_kill_process_group_posix`) | TERM→wait 1s→KILL→wait 2s→**setsid 이탈자 sweep** 사다리 |
| 2 | `tools/environments/local.py:859-877` (`_wait_for_group_exit`) | `killpg(pgid,0)` + `proc.poll()` 재수확 루프 |
| 3 | `tools/environments/local.py:898-925` (`_leader_is_ours`) | PID/PGID 재활용 가드(drift-tolerant) |
| 4 | `tools/process_registry_termination.py:33-131` | **부모 먼저** grace → 자손 → 재스냅샷 → SIGKILL |
| 5 | `tools/process_registry_termination.py:143-161` + `tools/process_registry.py:2166-2221` | 생존자 검증 + **receipt 거부** |
| 6 | `agent/deadline.py:304-...` + `tools/environments/base.py:744-776` | **유한 shutdown**(daemon worker + `on_timeout`) |
| 7 | `agent/managed_worker.py:158-174` | owner 사망 시 self-kill(10s grace) |
| 8 | `gateway/cgroup_cleanup.py:43-111` | systemd `ExecStopPost` cgroup 잔여 sweep |
| 9 | `gateway/status.py:402-461` | identity-checked `terminate_pid`(force에 start-time 필수) |

**Orca는 보조로 1가지만**: 3값 verdict **어휘**(`exited/live/unverifiable`)를 API 타입으로 쓰는 형태.
hermes는 같은 판정을 생존자 목록으로 표현하므로 기능은 동등하지만, 어휘가 더 명시적이다 → `[차용]` S.

### 8.3 opencodex 교차 확인 (내 범위 밖, 최종 판정의 3자 비교용 2파일만)

`opencodex/src/adapters/coding-agent/turn.ts`는 **TalkAbout과 같은 모양**(턴 단위 coding-agent 실행)을 TypeScript로 푼다:
`timeoutMs` 기본 300s(`:53`), `killGraceMs` 2s(`:54`), `reapTimeoutMs`(`:41`), abort signal(`:412-416`), timeout 시 `kill()`+`stopStream()`+504(`:417-421`).

그러나 kill 사다리가 **직접 자식만** 대상이다(`:391-407`):

```ts
try { child.kill("SIGTERM"); } catch { /* already gone */ }
killTimer = setTimeoutFn(() => {
  try { child.kill("SIGKILL"); } catch { /* already gone */ }
}, killGraceMs);
```

POSIX에서 **pgid/setsid를 쓰지 않고**, 손자 프로세스 회수를 "MCP child가 stdin EOF로 스스로 죽는다"는 **협조에 의존**한다(`:400-402` 주석).
→ **POSIX 격리·회수 관점에서 hermes > opencodex**. (Windows는 `taskkill /T /F`로 동등 — `:29-30`, `:58-60`.)
opencodex는 MIT(`opencodex/LICENSE:1-3`)이므로 코드 인용은 가능하나, 이 기능에 대해선 인용할 것이 없다.

### 8.4 hermes가 **부적합**한 부분 (숨기지 않음)

TalkAbout이 "격리"의 의미를 **보안 경계**(파일·네트워크·권한)로 확장하면 **hermes도 Orca도 부적합**하다.
hermes 로컬 POSIX 자식은 부모와 같은 uid·같은 fs·같은 net namespace에서 돈다(§2.5). rlimit도 안 건다.
따라서 POSIX 차단 해제의 **정당한 근거**는 "샌드박싱했다"가 아니라 **"프로세스 수명을 누출 없이 관리하고 종료를 증명한다"** 여야 한다.
TalkAbout 문서(`docs/RELEASE_GAP_MATRIX.md`)에 이 구분을 명시하지 않으면, `talkabout/cli.py:203` 문구("verified Windows Job containment")를
POSIX에서 **과장**하게 된다 — 이건 1라운드에서 내가 강조한 "합성 테스트를 실제 검증으로 표시하지 않는다" 원칙과 같은 문제다.

---

## 9. TalkAbout 부착 계획

| # | 작업 | 부착 위치 | 근거(hermes) | 태그 | 비용 |
| --- | --- | --- | --- | --- | --- |
| 1 | `PosixManagedProcess`: `start_new_session=True` + pgid/start-time 스탬프 | `talkabout/processes.py` | `tools/environments/local.py:1109-1123` | `[채택]` | S |
| 2 | `_kill_process_group_posix` 동등물: TERM→1s→KILL→2s→setsid sweep | `talkabout/processes.py` | `tools/environments/local.py:928-985` | `[채택]` | S |
| 3 | 생존자 검증 + "미확인은 생존자" + receipt 거부 | `talkabout/processes.py`, `talkabout/agent_runtime.py:315-327` | `tools/process_registry_termination.py:143-161`, `tools/process_registry.py:2177-2199` | `[채택]` | S |
| 4 | 유한 shutdown: daemon worker + `join(timeout)` + `on_timeout` kill | `talkabout/runtime.py:51-59` | `tools/environments/base.py:744-776`, `agent/deadline.py:304-...` | `[채택]` | S |
| 5 | owner 사망 self-kill(10s grace, 자기 그룹만) | `talkabout/runtime.py`, `agent_runtime.py` | `agent/managed_worker.py:158-174` | `[채택]` | S |
| 6 | journal에 `pid_scope` + `host_start_time` 기록 → 기동 시 고아 스윕 | `talkabout/worker.py:115-127`(journal schema) | `tools/process_registry.py:565`, `tools/process_registry_checkpoint.py:72-159` | `[채택]` | M |
| 7 | startup watchdog(lease > CPU > fire) | `talkabout/cli.py`(run 진입), `runtime.py` | `hermes_startup_watchdog.py:355-411` | `[채택]` | S |
| 8 | turn 무진행 감지(600s/15s, sleep 보정, commit 거부권) | `talkabout/worker.py:380-400` | `agent/turn_liveness.py:113-157` | `[차용]` | M |
| 9 | systemd scope cgroup(있으면) + 명시적 degrade 로그 | `talkabout/processes.py` | `tools/process_registry.py:249-308, 349-380` | `[차용]` | M |
| 10 | 3값 verdict 어휘(`exited/live/unverifiable`) | `talkabout/processes.py` 반환형 | Orca `src/main/pty-descendant-exit-verification.ts:99` | `[차용]` | S |
| 11 | `talkabout/cli.py:203` 문구 정정: "containment" → "process-lifetime containment" | `talkabout/cli.py:203`, `docs/RELEASE_GAP_MATRIX.md` | §8.4 | `[채택]` | S |
| 12 | 자식 teardown 결과를 부모에 `orphaned_processes`로 보고 | `talkabout/connector.py` | `tools/AGENTS.md` 계약 | `[차용]` | S |

**순서 권고**: 4 → 3 → 1 → 2 → 6 → 11 → 7 → 5 → 12 → 8 → 9 → 10.
이유: 4·3은 **현재 Windows 경로의 결함**도 함께 고치고(무제한 shutdown은 플랫폼 무관), 1·2가 POSIX 차단 해제의 최소 집합이며,
11은 문서 정합성(다른 팀원 보고서와 충돌 방지)이다.

---

## 10. 1라운드 결론과의 모순 / 정정

1. **모순 아님(범위 정정).** 1라운드 `analysis/03-runtime.md` §9-7에서 POSIX 격리를 **비용 L, "전략적으로 가장 큰 항목"** 으로 적었다.
   그 L은 **containment(보안 격리)** 기준이었다. 이번 분석으로 기준을 **lifetime(누출·좀비·무한대기 0)** 으로 재정의하면 **M**이며,
   참조 구현이 hermes에 실재한다. → L 판정 자체를 철회하지 않되, **"무엇을 격리라고 부르는지"를 먼저 정해야 한다**는 조건을 붙인다.
2. **정정.** 1라운드 §4.1에서 "Orca에는 에이전트 CLI 무출력 감지기가 없다(미확인)"고 적었다. 그건 Orca에 대한 진술로는 유효하지만,
   **"stall 감지의 최적 출처"** 는 Orca가 아니라 hermes다(`agent/turn_liveness.py`, `gateway/session_stall.py`).
   Lead의 1라운드 통합 결론("stall 감지 = Orca에서 가져올 것")은 **출처를 hermes로 바꾸는 게 맞다**.
3. **정합.** 1라운드 Top 5 #2(무제한 `shutdown()`, S)는 그대로 유효하며, 이번에 **구현 스펙**(`run_bounded_sync` + `on_timeout`)이 확정됐다.
   난이도는 S로 유지된다.
4. **주의(모순처럼 보이는 것).** hermes의 startup watchdog은 프로세스를 **강제 종료**하지만(`os._exit(75)`),
   turn liveness는 **abort 후 lease 갱신 중단**이고, process kill은 **receipt를 거부**한다. 세 계층의 "실패 시 행동"이 다르다.
   이는 일관성 부족이 아니라 **"무엇을 보증할 수 있는가"에 따른 구분**이다(프로세스: 종료 증명 가능 / turn: 증명 불가 → unknown 유지).
   TalkAbout이 그대로 베끼면 계층별 계약을 먼저 문서화해야 한다.

---

## 11. 미확인 목록 (정직성)

- hermes의 **Windows** 경로는 이번에 부분만 봤다(`tools/process_registry_termination.py:54-62` taskkill /T /F, `gateway/status.py:424-441` identity 필수).
  TalkAbout은 Windows가 이미 강하므로 우선순위가 낮다.
- `tools/environments/docker.py`(1,231줄)·`ssh.py`·`modal.py`·`daytona.py`·`vercel_sandbox.py`의 내부 수명 관리는 **읽지 않았다**
  (§5 판정은 ABC·레지스트리·`pid_scope` 수준에서만 내렸고, 개별 백엔드의 회수 정확성은 **미확인**).
- `tests/` 픽스처는 계약 증거로 쓰지 않았다(테스트 이름만 참조: `tests/tools/test_kill_verify_tree_death.py`,
  `tests/agent/test_owned_process_cleanup.py`, `tests/gateway/test_cgroup_cleanup.py`). 실행 검증은 하지 않았다 — **clone은 read-only**다.
- **실측 없음**: Hermes를 실제로 돌려 kill 사다리를 관찰하지 않았다. 모든 판정은 **코드·주석·계약 문서** 근거다.
- `opencodex`의 나머지(12,883파일)는 **미확인**. §8.3은 2개 파일 근거로만 유보적으로 적었다.

---

## 부록 A — 라이선스

- hermes-agent: MIT (`hermes-agent/LICENSE:1-3`, "Copyright (c) 2025 Nous Research").
- orca: MIT (`orca-analysis/LICENSE`).
- opencodex: MIT (`opencodex/LICENSE:1-3`, "Copyright (c) 2026 opencodex contributors") — `analysis/BRIEF-R2.md:64` 요구대로 직접 확인했다.
→ 코드를 실제로 복사할 경우(§9의 1·2·4·5는 복사 가치가 있다) 저작자 표시 + 라이선스 전문 고지를 `THIRD_PARTY_NOTICES.md`에 추가해야 한다.
개념만 차용하면 고지 의무는 없으나, §9 표에 `[채택]`으로 표시된 항목은 사실상 알고리즘 이식이므로 **고지를 넣는 쪽을 권한다**.

## 부록 B — 인용 파일 색인

hermes-agent: `tools/process_registry.py`, `tools/process_registry_termination.py`, `tools/process_registry_checkpoint.py`,
`tools/environments/local.py`, `tools/environments/base.py`, `tools/environments/docker_ownership.py`, `tools/terminal_tool_backends.py`,
`tools/AGENTS.md`, `agent/deadline.py`, `agent/turn_liveness.py`, `agent/managed_worker.py`, `agent/transports/codex_app_server.py`,
`gateway/cgroup_cleanup.py`, `gateway/session_stall.py`, `gateway/status.py`, `hermes_startup_watchdog.py`, `hermes_state_pidns.py`,
`hermes_cli/local_runtime/processes.py`, `hermes_cli/resource_limits.py`, `hermes_cli/main_desktop.py`, `hermes_cli/config_defaults.py`, `LICENSE`.
orca-analysis: `src/main/pty/posix-pty-process-groups.ts`, `src/main/pty-descendant-exit-verification.ts`, `src/packages/process-host/src/process-tree-termination.ts`.
opencodex: `src/adapters/coding-agent/turn.ts`, `LICENSE`.
TalkAbout: `talkabout/processes.py`, `talkabout/runtime.py`, `talkabout/agent_runtime.py`, `talkabout/worker.py`, `talkabout/connector.py`, `talkabout/cli.py`.
