# 03. 런타임 · PTY 세션 지속성 · 재접속 계층
분석 대상: `orca-analysis/` (HEAD `51dcef8`, 2026-10-25), Orca = Electron/TypeScript 데스크톱 + CLI.
비교 기준: `TalkAbout` (Python + PostgreSQL, 정본 `docs/ARCHITECTURE.md`).
규칙: `analysis/BRIEF.md` — 근거 경로+라인 필수, 추측 금지, 판정 태그, 한국어.

> **한 줄 결론.** Orca의 런타임 계층은 "**PTY 프로세스를 앱과 분리된 상주 daemon에 위임하고, 앱은 소켓으로 붙었다 떨어졌다 한다**"는 한 문장으로 요약된다.
> 그 부산물로 스크롤백 영속(`terminal-history`), cold restore, 재부착(reattach), 프로세스 트리 정리, daemon crash-loop 상한이 매우 두텁다.
> TalkAbout은 **이미 다른 축(Event 순서·journal 상태기계·lease)에서 더 강하다**. 따라서 이 보고서의 대부분 항목은 `[대조]`이고,
> TalkAbout에 실제로 구멍이 있는 것은 **POSIX 프로세스 격리**, **shutdown의 유한성**, **journal 락 오인**, **감독자 부재**, **상관 ID 부재**다.

---
## 0. 판정 요약
| # | 질문 | Orca 근거 강도 | TalkAbout 현황 | 판정 |
| --- | --- | --- | --- | --- |
| 1 | PTY 스크롤백 영속·재시작 복원 | 매우 강 (checkpoint + 증분 log + GC) | 영속 대화 세션 개념 없음(1회 호출) | `[차용]` |
| 2 | 재부착·세션 토큰, 앱 사망 시 생존 | 매우 강 (detached daemon + 토큰 파일) | 프로세스 분리 없음(worker 일회 호출) | `[대조]` |
| 3 | hang/stall 감지·타임아웃 계층 | 중 (Electron 메인스레드 전용) | 호출 단위 유한 타임아웃 4중 | `[차용]` |
| 4 | 자식 프로세스 정리·좀비 방지 | 매우 강 (Job Object·pgid·ttys·verdict) | Windows는 강, POSIX 미지원 | `[차용]` |
| 5 | 원격 실행 추상화 (ssh/wsl/vm) | 강 (`IPtyProvider` 단일 계약) | 원격 실행 없음(로컬 CLI만) | `[제외]` |
| 6 | SQLite 방식 + `foreign-sqlite-readers` | 중 (캐시/버전 리빌드 패턴) | journal은 SQLite, 서버는 PostgreSQL | `[차용]` |
| 7 | 재연결 시 놓친 event 따라잡기 | 중 (PTY=seq 스냅샷, 원격=재구독) | cursor + snapshot rebase | `[대조]` |
| 8 | 관측·진단 (로그·상관ID·crash) | 매우 강 (span/NDJSON/redactor/bundle) | 구조적 로깅 없음 | `[채택]` |
| 9 | TalkAbout 파일별 개선점 | — | 아래 §9 (8건) | — |
| 10 | 후보 (a)~(e) 판정 | — | 아래 §10 | — |

---
## 1. 분석 범위와 미확인 사항
읽은 범위(전부 실제 파일 확인): `src/main/daemon/`(비테스트 231파일 중 핵심 60여 개), `src/main/pty/`, `src/main/hang-watchdog/`,
`src/main/observability/`, `src/main/diagnostics/`, `src/main/sqlite/`, `src/main/foreign-sqlite-readers/`,
`src/packages/process-host/`, `src/main/providers/pty-provider-contract.ts`·`execution-host-provider-dispatch.ts`,
`src/main/terminal-history*.ts`, `src/shared/remote-runtime/control/`, `src/main/ssh/ssh-reconnect-*.ts`,
`src/main/ephemeral-vm/`, `src/main/daemon/macos-daemon-launchd.ts`.

**미확인 / 발견하지 못한 것** (존재한다고 단정하지 않음):

- **에이전트 CLI 자체의 무출력(hang) 감지기**는 찾지 못했다. `stall|inactivity|no output for` 계열 패턴을 `src/**/*.ts` 전체에서 검색했으나
  나온 것은 CLI 인자·문서 문자열이었고, PTY 출력 정지를 신호로 삼는 감시기는 없었다. Orca의 hang 감지는 **Electron 메인스레드 전용**이다(§4).
- `src/main/ghostty/`는 터미널 테마·설정 파일 파서/매퍼다(`discovery.ts`, `mapper.ts`, `theme-resolution.ts`). 세션 영속과 무관 → `[제외]`.
- `src/main/managed-server/`, `src/main/orcad/`, `src/main/daemon/` 전체의 세부는 부분만 읽었다. 배포·마이그레이션 계층은 이 보고서 범위 밖으로 남긴다.
- `src/main/persistence/` 하위 627파일은 `terminal-topology/`, `restoring-sessions/`, `atomic-snapshot-writer.ts`만 확인했다. 창/레이아웃 스냅샷 계층이며 PTY 스크롤백과는 별개 파일이다.

---
## 2. Q1 — PTY 스크롤백/상태 영속과 재시작 복원
### 2.1 저장 위치
```
<userData>/terminal-history/<encodeURIComponent(sessionId)>/
  meta.json          # cwd, cols, rows, startedAt, endedAt, exitCode
  checkpoint.json    # ANSI 스냅샷 (최대 200MB)
  output.log         # OCKL v1, 길이 prefix 프레임 증분 로그 (최대 5MB)
```

- 루트: `src/main/terminal-history-paths.ts:5` `HISTORY_DIR_NAME = 'terminal-history'`, 같은 파일 `:6` `terminal-history-wsl`(WSL은 distro별 하위 디렉터리), `:9` `.pending-delete`.
- 세션 디렉터리명: `src/main/daemon/history-paths.ts:3` — `encodeURIComponent(sessionId)`로 슬래시·콜론을 이식 가능한 이름으로 인코딩.
- 세션 ID 자체가 `worktreeId<SEP>uuid8` 형태: `src/main/daemon/pty-session-id.ts:7` `mintPtySessionId`. 경로 traversal 방어도 별도 함수로 존재(`:23` `isSafePtySessionId`, 512자·NUL·상대경로 탈출 거부).
- 파일 권한은 umask를 신뢰하지 않고 강제: `src/main/daemon/daemon-private-file-modes.ts:5-6` `PRIVATE_DIR_MODE=0o700`, `PRIVATE_FILE_MODE=0o600`. Windows는 POSIX mode bit 무시(`:9`).
### 2.2 포맷
**`checkpoint.json`** (`src/main/daemon/daemon-checkpoint-file.ts:7-27`) — JSON 하나에:
`snapshotAnsi`(현재 화면), `scrollbackAnsi`(히스토리), `rehydrateSequences`(모드 복원 escape), `pendingEscapeTailAnsi`,
`cwd`, `cols`, `rows`, `modes`(bracketedPaste/mouseTracking/applicationCursor/alternateScreen/kittyKeyboardFlags…),
`scrollbackLines`, `lastTitle`, `terminalOwner`, **`generation`**, **`pendingOutputSeq`**, `checkpointedAt`.

- `generation`은 `output.log` 헤더의 generation과 짝을 이룬다(`:25` 주석). 이 쌍이 "스냅샷 + 그 이후 증분"의 경계를 정의한다.
- `pendingOutputSeq`는 "이 스냅샷에 포함된 마지막 daemon pending batch 번호"다. 복원 시 **스냅샷에 이미 들어간 바이트를 재생하지 않기 위한 워터마크**다.

**`output.log`** (`src/main/daemon/terminal-history-log.ts`) — 자체 바이너리 프레임:

```ts
const LOG_MAGIC = 'OCKL'            // :5
const LOG_FORMAT_VERSION = 1        // :6
export const LOG_HEADER_BYTES = 9   // :7  (magic4 + version1 + generation4)
const FRAME_BATCH = 0x01; const FRAME_OUTPUT = 0x02; const FRAME_RESIZE = 0x03; const FRAME_CLEAR = 0x04
```

디코더는 **끊긴 꼬리(torn append)에서 완전한 prefix까지만 재생**하고 `truncatedTail`로 표시한다(`:11-13`, `:78-86`).
더 중요하게, **batch seq 불연속이면 로그 전체를 폐기**한다(`:104-108` `if (current && seq !== current.seq + 1) return null`) — "빠진 바이트 위로 재생하면 복원이 오염된다"는 주석이 붙어 있다.
프레임마다 payload 길이를 기록하므로 부분 기록을 구조적으로 감지할 수 있다. 이건 TalkAbout journal(SQLite)에는 필요 없는, **파일 append 로그特有의 설계**다.
### 2.3 용량 상한 — 계층별로 다르다
| 상수 | 값 | 근거 |
| --- | --- | --- |
| 데스크톱 스크롤백 행 기본/최소/최대 | 5,000 / 1,000 / 50,000 | `src/shared/terminal-scrollback-policy.ts:1-3` |
| daemon **live** 스크롤백 행 | 1,000 (env override 100~5,000) | `src/main/daemon/daemon-session-scrollback-window.ts:5`, `:8-9` |
| **복원** 시 스크롤백 행 | = 데스크톱 기본값(5,000) | `src/main/daemon/daemon-restore-scrollback-depth.ts:4` |
| 출력 백로그 char cap | `max(2MB, rows × 120)` | `src/shared/terminal-scrollback-policy.ts:31-37` |
| checkpoint.json | 200,000,000 B | `src/main/daemon/terminal-history-file-limits.ts:4` |
| output.log | 5,242,880 B | `terminal-history-file-limits.ts:2`, `terminal-history-session-writer.ts:25` |
| meta.json | 65,536 B | `terminal-history-file-limits.ts:1` |
| 레거시 scrollback 파일 | 16,777,216 B | `terminal-history-file-limits.ts:5` |
| 복원 가능 세션 수 | 10,000 (최신 우선) | `src/main/daemon/terminal-history-restorable-retention.ts:1` |
| cold restore 페이로드 캐시 | 16MB (LRU) | `src/main/daemon/cold-restore-payload-cache.ts:37` |
| 호스트 전체 PTY 수 | 256 (env 1~4096) | `src/main/daemon/host-terminal-ledger.ts:22-25` |

핵심 아이디어: **"live는 작게(1,000행), durable은 크게(5,000행)"**. 주석이 명시한다 —
`daemon-session-scrollback-window.ts:4` "Bound live grid memory across many terminals; durable history supplies the deeper restore window."

200MB 상한을 넘으면 **가장 오래된 행부터 버리되, 이진탐색으로 최대 행 수를 찾아** 저장한다(`src/main/daemon/terminal-checkpoint-serializer.ts:237-268`).
`BoundedJsonWriter`가 byte 예산 안에서 JSON을 조립하고(`terminal-checkpoint-serializer.ts:40-88`), 초과 시 `replayTerminalSnapshot`으로 headless emulator를 재생해 행 수를 줄인다. **스냅샷 자체가 저장 불가능해지지 않는다.**
### 2.4 영속 스케줄
- checkpoint 간격 **5초**, 연속 full checkpoint cooldown **45초**: `src/main/daemon/daemon-pty-runtime-state.ts:155,158`.
  타이머는 **dirty-gate** — 변경 없는 터미널은 타이머를 아예 걸지 않는다(`src/main/daemon/daemon-pty-checkpoint-scheduler.ts:17-25`).
- output.log가 5MB에 도달하면 `appendIncrements`가 `'needs-checkpoint'`를 반환하고, 호출자가 full snapshot 후 로그를 새 generation으로 재개한다
  (`terminal-history-session-writer.ts:46-56`, `daemon-pty-checkpoint-persistence.ts:52-70`).
- 증분 append와 checkpoint는 **atomic 전제조건**을 지킨다 — "Drain and snapshot atomically so warm reattach cannot append records already in the checkpoint"
  (`daemon-pty-checkpoint-persistence.ts:24`). 실제 교환은 `takePendingOutput` RPC 하나로 `{records, seq, overflowed, snapshot}`을 한 번에 받는다
  (`src/main/daemon/types.ts:248-256`).
- **유실 감지**: `overflowed: true`면 "Dropped records leave a log gap; re-anchor with a full snapshot"(`daemon-pty-checkpoint-persistence.ts:45-48`).
  seq는 **nondecreasing batch 번호**로 "Persist nondecreasing batch numbers to detect lost records"(`daemon/types.ts:252`).
- 파일 기록은 tmp 작성 → `rename`(checkpoint), append 모드(로그). tmp 모드도 `tightenPathMode`로 0600 재적용(`terminal-history-session-writer.ts:95-101`).
- **삭제는 2단계**: 먼저 `.pending-delete/<uuid>`로 rename(즉시 도달 불가), 실제 재귀 삭제는 비동기 + 재시도 30초/120초, 실패분은 다음 기동 시 drain
  (`src/main/daemon/terminal-history-session-tombstone.ts:14-16`, `:38-46`, `:110-121`). "Tombstone first so worktree deletion need not await reclamation of large history trees"(`history-manager.ts:277`).
- **GC**: 5분 이상 된 것만, live worktree 집합이 **비어 있으면 전체 스킵**(상태 로딩 실패로 복구 가능한 이력을 지우지 않기 위해), 동시성 16·32개마다 이벤트루프 양보
  (`src/main/terminal-history-gc.ts:20`, `:76-80`, `:23-24`).
- **복원 억제 규칙**: `meta.endedAt !== null`이면 정상 종료 세션이므로 cold restore하지 않는다(`src/main/daemon/history-reader.ts:97`).
  반대로 daemon이 비정상 종료하면 `endedAt`이 null인 채 남아 복원 후보가 된다. 이 규칙 때문에 `dispose()`가 "Mark orderly teardown clean"으로 endedAt을 반드시 채운다(`history-manager.ts:301-314`).
- **recovery freeze / quarantine**: 복원 직후 새 writer가 기존 스크롤백을 덮어쓰지 못하도록 sessionId 단위 freeze token을 잡고(`history-manager.ts:104-124`),
  읽을 수 없는 복구 데이터는 quarantine 디렉터리로 격리한 뒤 재생성한다(`history-manager.ts:63-70`).
- **손상 내성**: checkpoint는 `isTerminalCheckpointFile`로 필드 타입까지 검증하고(`terminal-history-checkpoint-reader.ts:33-57`),
  실패하면 `{status:'unreadable'}`로 떨어져 복원을 포기할 뿐 앱을 죽이지 않는다. writer는 오류 시 해당 세션 history를 **비활성화**하고 계속 동작한다
  (`history-manager.ts:318-321` `handleWriteError`).

**판정**: `[차용]`. TalkAbout은 1회성 runtime 호출이라 "터미널 스크롤백"은 필요 없다. 그러나 **"durable 스냅샷 + 증분 로그 + seq 워터마크 + 세대 번호"** 구조는
TalkAbout의 향후 "긴 agent 세션 transcript 증분 저장"에 그대로 이식 가능한 패턴이다. 비용 M. 부착 위치: `talkabout/agent_runtime.py`(현재 스트림을 메모리 queue로만 소비) + `talkabout/text_files.py` 계열.

---
## 3. Q2 — 재부착(reattach)과 세션 토큰, 그리고 "앱이 죽어도 사는가"
### 3.1 구조: 앱 ≠ 프로세스 소유자
PTY를 소유하는 것은 Electron 앱이 아니라 **detached daemon 프로세스**다. 앱은 Unix 소켓/Windows named pipe로 붙는다.

- 엔드포인트 경로: `src/main/daemon/daemon-spawner.ts:122-133` — Windows는 `\\.\pipe\orca-terminal-host-v<PROTOCOL>-<hash(runtimeDir,12)>`, POSIX는 `<runtimeDir>/daemon-v<PROTOCOL>.sock`.
- 토큰: `daemon-server.ts:123` `const token = randomUUID()` → **daemon이 기동 시 스스로 생성**하고 `writeFileSync(tokenPath, token, { mode: 0o600 })`로 공개(`daemon-endpoint-lifecycle.ts:77`).
  클라이언트는 그 파일을 읽어 hello에 실어 보낸다(`daemon/client.ts:114`, `daemon-client-hello-handshake.ts:33-44`).
  즉 **토큰은 앱이 아니라 daemon이 소유**하고 파일로 전달된다 — 앱 재시작 후에도 같은 daemon에 붙을 수 있는 이유다.
- hello는 `{version, token, clientId, role:'control'|'stream', streamFraming}`(`daemon-client-hello-handshake.ts:33-44`)이며 응답에 daemon identity
  `{pid, startedAtMs, launchNonce, entryPath, appVersion, spawnerExecPath}`가 온다(`:133-166`). 이 identity로 "내가 붙은 daemon이 맞나"를 매번 대조한다.
- **엔드포인트 소유권 프로토콜**(`daemon-endpoint-ownership.ts`): 임의 경로 `.p<5byte hex>`에 먼저 bind → `linkSync`로 canonical 이름 획득 → 실패 시
  "죽었음을 증명한 경우에만" 교체. 판정 3값: `occupied`(adopt) / `lost`(이 daemon은 서비스하지 않음) / `inconclusive`(모르면 건드리지 않음).
  `:1` 주석: "Only publishers replace canonical entries they prove dead."
  재검증 절차가 인상적이다 — inode 캡처 → probe → 사망 증명 → inode 재확인 → **재-probe**(inode 재활용 방어) → rename → 게시 후 소유 확인(`daemon-endpoint-ownership.ts:101-133`, `:198-210`).
  "A probe that threw classified nothing, which is never proof of death"(`daemon-endpoint-ownership.ts:88`).
- Windows는 named pipe가 이름 배타 + 프로세스 종료 시 소멸이라 프로토콜이 단순(`daemon-endpoint-ownership.ts:47-49`).
### 3.2 재부착
- 세션 attach는 **spawn RPC에 `attachOnly: true`** 로 표현된다(`src/main/providers/pty-provider-contract.ts:81`).
  "Attach the named session atomically or fail without creating a process."
- `expectedIncarnationId` / `expectedIncarnationIsAuthoritative`(`pty-provider-contract.ts:83-88`)로 **incarnation fence**를 건다. stale한 부착 요청이 새 프로세스를 만들지 못하게 한다.
- 부착 성공은 `isReattach === true`로만 인정되고, 그때만 라우트를 기록한다(`daemon-session-owner-attach.ts:33-41`).
- 다중 provider 상황에서 owner를 모르면 `TerminalSessionOwnerUnverifiedError`로 **fail closed**(`daemon-session-owner-attach.ts:56-62`, `:74-79`). 추측 부착 없음.
### 3.3 앱이 죽으면?
**살아남는다.** 근거 3개:

1. **detached + unref**: 하위 daemon을 띄운 뒤 `child.disconnect(); child.unref()`(`daemon-launched-child.ts:203-204`) — "Release parent event-loop references while the detached daemon keeps running."
2. **Linux: 자기 cgroup scope**. daemon을 `systemd-run --user --scope --unit=orca-daemon-<nonce>.scope --property=TimeoutStopSec=5s --collect`로 띄워
   부모 서비스의 unit-scoped shutdown에서 분리한다(`daemon-cgroup-scope.ts:260-283`). 전제조건으로 `loginctl show-user … Linger`까지 확인하고,
   아니면 명시적으로 경고한다 — "terminals survive a service restart"(`daemon-user-manager-lifetime.ts:1`, `:59-74`).
3. **macOS: launchd job**. 별도 bundle을 materialize해 launchd job으로 등록·상태 조회(`macos-daemon-launchd.ts:1-40`), 번들 교체 시 retire/recovery 경로 존재.
   추가로 **로그인 세션 감시**(`loginSessionWatch`)로 "사용자 로그아웃"은 감지하되, 앱 종료는 감지하지 않는다(`daemon-entry.ts:203-262`).

앱이 **정상 종료**할 때는 daemon에 `shutdownIfIdle`만 보내고 끊는다 — 살아있는 세션이 있으면 retire 거부(`daemon-pty-event-subscriptions.ts:220-226`,
`daemon-pty-checkpoint-persistence.ts` 상단 계열). 즉 **PTY는 그대로 살아 있고**, 다음 기동 시 warm reattach.
앱이 **비정상 종료**하면 소켓만 끊기고 daemon은 계속 산다.

daemon이 **죽으면** leftover 정리를 별도 watcher가 맡는다 — `daemon-scope-death-watch.ts:5-20`은 `/bin/sh`로
"stdin pipe EOF → `/proc/<daemonPid>`가 사라질 때까지 대기(최대 5s) → 내 cgroup이 그 unit이면 `systemctl --user stop`"을 수행한다.
주석: "The pipe closes even on SIGKILL; a live/reused owner PID always vetoes cleanup." → **daemon 사망 시 잔여 terminal 프로세스/scope를 회수**하는 장치.
### 3.4 손상/좀비 방지 부가 장치
- **PID+시작시각+cmdline 3중 신원 확인**: `daemon-pid-identity.ts:53-86` `inspectDaemonProcessIdentity` — `process.kill(pid,0)`으로 살아있음 확인 후
  Windows는 `commandLine` + `startedAtMs`(허용오차 10초), POSIX는 `/proc/<pid>/cmdline` + start time. ESRCH만 "없음"의 증거, EPERM은 `unknown`(`daemon-pid-identity.ts:56-60`).
  "Inspection failures are unknown, never proof that a live daemon lost ownership."
- **crash-loop 상한**: `daemon-respawn-throttle.ts:13-14` `maxAttempts=5 / windowMs=60_000` 슬라이딩 윈도. 초과 시 `DaemonCrashLoopError`
  ("has failed N times in a row; refusing to respawn it for another Xs… check the daemon log"). 성공 fork만으로 리셋하지 않는다(`daemon-respawn-throttle.ts:35-38` 주석).
- **호스트 전체 PTY 상한 공유**: `<tmp>/orca-daemon-host-terminals/<pid>.json`에 자기 사용량을 쓰고 남의 것을 읽어 합산(`host-terminal-ledger.ts:41-57`,
  `:186-215`). 60초마다 refresh, 5분 stale 만료, 죽은 PID 항목은 삭제. 프로필이 여러 개여도 OS PTY 고갈을 막는다.

**판정**: `[대조]`. TalkAbout은 세션을 유지하지 않으므로 reattach 자체가 필요 없다. 다만 **"프로세스 소유자와 앱을 분리하고 소유 증명(pid+시작시각+cmdline)으로만 회수한다"**는
원칙은 TalkAbout worker의 journal 락/점유 검증에 그대로 대응한다(§9-6). `[차용]` 비용 S.

---
## 4. Q3 — hang/stall 감지 기법과 타임아웃 계층
### 4.1 무엇을 신호로 삼나 — 하트비트 "무변화"
Orca의 hang watchdog은 **CPU도 출력도 아닌, Electron 메인스레드의 하트비트 공백**을 신호로 삼는다.

```
src/main/hang-watchdog/hang-watchdog-worker-protocol.ts
:1  HANG_WATCHDOG_HEARTBEAT_INTERVAL_MS = 2_000
:2  HANG_WATCHDOG_TIMEOUT_MS           = 45_000
:3  HANG_WATCHDOG_CHECK_INTERVAL_MS    = 5_000
```

- 메인스레드는 2초마다 워커에 `{type:'heartbeat'}`를 postMessage한다(`main-thread-hang-watchdog.ts:68-70`).
- 워커는 5초마다 tick해서 마지막 heartbeat 이후 경과가 45초를 넘으면 hang으로 판정한다(`hang-watchdog-worker.ts` 계열 → `hang-watchdog-detection-loop.ts:30-44`).
- **오탐 억제 3가지** (이게 이 계층의 핵심이다):
  1. **시스템 슬립 보정**: `tickGap > checkIntervalMs * 3`이면 슬립으로 간주하고 무응답 창을 리셋(`hang-watchdog-detection-loop.ts:38-41`, 주석 "System sleep suspends the watchdog too").
  2. **해소(resolved) 판정**: hang 후 heartbeat가 재개되면 `onHangResolved`를 호출하고 marker를 `selfRecovered: true`로 **덮어쓴다**(`hang-watchdog-detection-loop.ts:21-27`). 주석: "Heartbeats resumed after a detected hang — the main thread was stalled, not deadlocked."
  3. **관측만 한다**: `recordHangObservation`은 marker를 쓸 뿐 프로세스를 죽이지 않는다. `main-thread-hang-watchdog-entry.ts:14` 주석 — **"Observation only: a false positive must never kill a live main thread mid-write."** 이건 명시적 설계 결정이다.
- **개발 환경/비패키지 제외, darwin 한정, 관측 동의(observability consent) 필요** (`main-thread-hang-watchdog.ts:27-33`). debugger 일시정지를 hang으로 오인하지 않기 위해서다(주석 `:30-31`).
- **다음 기동에서 소비**: marker는 `<userData>/main-thread-hang.json`(0600), 64KB 초과 시 무시(`hang-detection-marker.ts:12-33`).
  이전 실행 marker는 기동 직후 `.previous`로 rename해 두고(`previous-hang-detection.ts:15-24`) trace sink가 열린 뒤
  `recordDurableCrashBreadcrumb('main_thread_hang_detected', {unresponsiveMs, previousPid, selfRecovered})`로 기록한다(`previous-hang-detection.ts:27-42`).
  → **"지난 실행이 hang했었다"는 증거가 다음 세션 진단에 남는다.**
### 4.2 "정지"를 다른 축으로 보는 보조 계층
| 계층 | 신호 | 값/위치 |
| --- | --- | --- |
| 이벤트루프 지연 프로브 | `monitorEventLoopDelay` p50/p99/max를 1초 창으로 NDJSON append | `src/main/diagnostics/event-loop-delay-probe.ts:34-56` |
| RPC 절대 데드라인 | 호출자 절대시각을 남은 시간으로 변환해 leaf RPC에 전달 | `daemon-request-deadline.ts:1-20` |
| daemon 종료 상한 | 5초 | `daemon-shutdown-deadline.ts:3` |
| hello 응답 상한 | "An accepted socket can still be a hung daemon; bound the hello wait" | `daemon-client-hello-handshake.ts:117-122` |
| 프로세스 테이블 조회 상한 | `ps` 1초 / 2초, 출력 1MB·32MB 상한 | `pty/posix-pty-process-groups.ts:9-10`, `pty-descendant-termination.ts:14` |
| descendant kill 검증 | grace 2,000ms + verify 3,500ms | `pty-descendant-termination.ts:12`, `pty-descendant-exit-verification.ts:15` |
| ask/reply 타임아웃 | 기본 600s, 최대 1,800s, client grace 5s | `src/shared/orchestration-ask-timeout.ts:1-3` |
| 메시지 대기 | 120s | `src/shared/orchestration-message-wait-timeout.ts:1` |

패턴: **모든 대기는 (상대 timeout + 절대 deadline + 상위 예산) 3중으로 표현되고, 상위 계층이 하위로 남은 예산을 내려준다.**
### 4.3 한계 — 에이전트 프로세스 hang은 감지하지 않는다
위 계층들은 전부 **앱 자신(main thread, RPC, 프로세스 테이블, 종료)** 을 지킨다.
PTY 안에서 도는 codex/claude CLI가 출력 없이 멈춘 경우를 판정하는 코드는 §1에서 적은 대로 찾지 못했다.
(터미널 UX 영역에서는 사용자가 보고 판단하는 전제로 보인다. 이 부분은 **미확인**.)

**판정**: `[차용]` — Orca의 hang watchdog은 "관측만, 죽이지 않음", "슬립 보정", "해소 판정", "다음 기동에서 브레드크럼"이라는
**4가지 오탐 억제 규칙을 가진다**. TalkAbout에 없는 부분은 (i) **worker 자신의 정지 감지**, (ii) **hang 증거의 내구 기록**이다. 비용 S. 부착: `talkabout/worker.py` main loop + 신규 heartbeat 파일.

---
## 5. Q4 — 자식 프로세스 정리, 좀비/고아 방지
### 5.1 POSIX: 프로세스 그룹 + TTY 기반 그룹 발견
- 일반 자식은 그룹 kill: `process-host/src/process-tree-termination.ts:44` `process.kill(-child.pid, signal)`. 전제조건 명시(`:12` 주석 "POSIX children must be detached: signaling -pid targets the process group").
- kill 후 **정지 확인**: `waitForPosixProcessGroupQuiescence`가 `ps -axo pgid=,state=`를 25ms 간격으로 조회해 **모든 구성원이 `Z`(좀비)** 이거나 그룹이 ESRCH가 될 때까지 기다린다(`process-tree-termination.ts:81-97`). 상한 2초(`:16-17`).
  → "signal 보냈다"와 "실제로 죽었다"를 구분한다.
- PTY는 더 어렵다. 셸이 job을 만들고 setsid로 세션이 갈라진다. `pty/posix-pty-process-groups.ts:191-220` `getPosixPtyProcessGroups`:
  - root의 **controlling tty**를 먼저 구하고, 그 tty를 공유하는 프로세스를 모은 뒤,
  - Linux에서는 `/proc/<pid>/stat`의 **session id == rootPid** 조건으로 후보를 좁히고(`posix-pty-process-groups.ts:99-110`, `:207-210`),
  - "dev daemon이 같은 PTY를 공유하면 Orca 자신을 그룹 시그널하지 않는다"(`posix-pty-process-groups.ts:202-203`) — self-kill 방어.
  - BusyBox `ps`가 컬럼을 잘라 **다른 터미널의 device number와 충돌**하는 문제를 명시적 width로 방어(`posix-pty-process-groups.ts:11-16`).
  - ps dialect(`-p/-t` 지원 여부)까지 probe하고 실패하면 `-e -o` 전체 조회로 폴백(`posix-pty-process-groups.ts:96-115`).
### 5.2 프로세스 테이블 스냅샷 + 신원(identity) 기반 종료
`src/main/pty-descendant-termination.ts`:
- `ps -axo pid=,ppid=,pgid=,tpgid=,lstart=` 스냅샷(`pty-descendant-termination.ts:44-77`). `capturedAtMs`를 **스캔 전에** 찍는다 — "a capture-second PID cannot appear older after a clock rollover"(`:44`).
- `lstart`(초 단위 시작시각) + `pgid` 조합이 PID 재활용에 대한 identity다. 중복 PID 행은 `"정체 불명"으로 처리(`pty-descendant-termination.ts:134-140`).
- `collectDescendantRows`로 트리 수집, `DescendantSnapshot`에 `capturedAtMsByPid`, `reDerivedPids`까지 저장(`pty-descendant-termination.ts:19-33`).

`src/main/pty-descendant-exit-verification.ts`:
- **3값 verdict**: `'exited' | 'live' | 'unverifiable'`(`pty-descendant-exit-verification.ts:99`). "unverifiable means the available observations cannot prove exit or liveness."
- "An unreadable process table is never proof that a stopped descendant exited"(`pty-descendant-exit-verification.ts:101`).
- 정체 재검증 옵션 `requireIdentityBeforeSignal`(`pty-descendant-exit-verification.ts:95`) — 시그널 직전에 identity를 다시 확인한다.
### 5.3 Windows: taskkill + **판정(verdict)**
`src/packages/process-host/src/windows-tree-kill.ts`:
- `taskkill /pid <pid> /t /f`(시스템 바이너리 절대경로, `:23`).
- **오히려 root를 빨리 죽이면 안 된다**: "killing the root before taskkill reads it can orphan descendants"(주석 `:10-11`) → deadline 30초(`:11`),
  root exit 후에도 최대 2초 더 기다린다(`:12`, "Windows may reuse the PID once Node releases its handle").
- verdict 3값 `'live' | 'unverifiable' | 'exited'`(`:7`). `unverifiable`일 때만 root 단독 kill로 폴백(`:88-90`).
- WeakMap으로 **child handle**에 매핑(`:32-33`) — "Keyed by handle, not pid: a reused pid must never join another tree's kill."
- taskkill 프로세스를 `PRIORITY_NORMAL`로 승격해 백그라운드 스케줄러 패널티를 피한다(`:104-111`).
- kill gate 훅: `process-tree-kill-gate.ts:1-2` — "Main installs its Chromium kill guard here; daemon, relay, and CLI cannot import main code."
  PID 기반 tree walk이 거부되면 **root는 handle로 죽이되 그룹 kill은 포기**한다(`process-tree-termination.ts:33-41`).
### 5.4 그 밖의 좀비 방지
- **daemon 사망 시 scope 회수**: §3.3의 `startDaemonScopeDeathWatch`.
- **종료 예산 동시성**: `terminal-descendant-shutdown.ts:16-38` — 동일 시점 shutdown 스캔을 공유하고 25ms만 캐시해 프로세스 테이블 조회 폭주를 억제.
- **spawn 자체를 워커로 오프로드**: `worker-thread-child-process.ts:13` 주석 — "Windows code-integrity hashing can block CreateProcessW for seconds on the calling loop. Spawn in a worker and fail closed if unavailable."
  worker가 죽으면 자식 종료를 **주장하지 않는다**: "Worker death closes stdin for graceful stop but proves no child exit"(`worker-thread-child-process.ts:192`).

**판정**: `[차용]`. TalkAbout `processes.py`는 **Windows에서는 이미 Orca보다 단순하지 않게 잘 되어 있다**(§9-1). 부족한 것은
(1) POSIX 전면 미지원, (2) "kill했다"와 "죽었다"의 verdict 구분 부재. 비용 L(POSIX) / S(verdict 도입).

---
## 6. Q5 — 원격 실행 추상화 (ssh/wsl/container/VM)와 로컬의 통일
Orca는 **PTY 수준의 단일 인터페이스**와 **경로 라우팅 수준의 dispatch** 두 층으로 원격을 흡수한다.
### 6.1 층 1: `IPtyProvider` — 로컬/daemon/SSH/VM이 같은 계약
`src/main/providers/pty-provider-contract.ts:114-219`. 메서드 수가 매우 많지만 핵심은 **"로컬과 원격이 같은 의미론을 갖도록 강제"** 하는 능력 플래그다:

| 필드/메서드 | 의미 |
| --- | --- |
| `providerGeneration?` | "SSH providers only: relay session generation that registered them" (`:116`) |
| `supportsFencedTermination?` / `killFenced` | fence된 종료 지원 여부 + 수행 (`pty-provider-contract.ts:135-137`) |
| `attach(id)` | 세션 재부착 (`pty-provider-contract.ts:139`) |
| `probePtyLiveness?` | "false only when the provider answered that the PTY is absent" (`pty-provider-contract.ts:142`) — 모르면 null |
| `writeWithSettlement` | "Durable claims require real write settlement; fire-and-forget providers must report inability to settle" (`pty-provider-contract.ts:145`) |
| `getBufferSnapshot?` | "Authoritative provider model snapshot after monitoring-stream gaps" (`pty-provider-contract.ts:157-161`) |
| `canProvideAuthoritativeBufferSnapshot?` | **이 PTY가 seq-안전 스냅샷을 줄 수 있는지** 사전 질의 (`pty-provider-contract.ts:162`) |
| `getAppliedSize?` | "Applied size readback detects dropped/clamped remote resize requests" (`pty-provider-contract.ts:165-167`) |
| `serialize(ids)` / `revive(state)` | 세션 상태 직렬화/복원 (`:199-200`) |
| `shutdown(id, {deadlineMs, expectedIncarnationId, expectedOwnerClientInstanceId})` | 절대 데드라인 + incarnation fence (`pty-provider-contract.ts:170-186`) |

설계 포인트: **원격이 못 하는 것을 "거짓 성공"으로 감추지 않는다.** 지원하지 않으면 optional 필드를 생략하고, 호출자가 그 부재를 capability로 해석한다.
### 6.2 층 2: 실행 호스트 라우팅은 fail closed
`src/main/providers/execution-host-provider-dispatch.ts`:
- 실행 호스트 ID를 `local` / `ssh:<id>` / `runtime:<id>`로 파싱(`:37-66`).
- 로컬 프로세스가 스스로 할 수 없는 작업은 **절대 이 기기로 폴백하지 않는다**:
  `UnresolvableExecutionHostError` — "Refusing to fall back to this machine."(`:16-25`), `SshHostNotServedError`(`:88-96`).
- git/filesystem도 같은 라우팅 타입을 공유한다(`:33-35`).
### 6.3 원격 연결 복구는 별도 래더
- SSH 재연결: `ssh-reconnect-ladder.ts:5-11` `STABLE_CONNECTION_MS=60_000`, `RELAY_REESTABLISH_BUDGET_MS=20_000`,
  그리고 **flap 지연 상한을 relay grace에서 역산**한다(`:13-20`): "Cap flap delay within minimum relay grace after handshake/reattach budgets, or retry could arrive after relay shutdown destroys PTYs."
- 오류 분류: 인증 실패/호스트키 거부는 **영구 실패**(재시도 금지), 네트워크 계열만 transient(`ssh-reconnect-error-classification.ts:1-20`, `:139-155`).
  OpenSSH의 산문 메시지까지 조각 매칭으로 분류(`:104-137`).
- 공유 control 채널 재연결: `remote-runtime-shared-control-reconnect.ts:3-4` active 8단계(250ms~30s) / idle 11단계(최대 300s) 백오프.
  `retryNow()`는 "OS resume / browser online should advance an already-scheduled reconnect, not start a new one"(`:69-80`).
- VM: `ephemeral-vm/runtime-service.ts`의 start/suspend/resume/cleanup 상태기계 + `runtime-attachment.ts`는
  "cleaned 상태 런타임은 attach 금지"로 fail closed(`:19-21`).

**판정**: `[제외]`. TalkAbout은 **"플랫폼이 모델을 실행하지 않는다"**는 P0 원칙(`docs/ARCHITECTURE.md`)을 갖고 있어 참가자 로컬 실행이 정본이다.
원격 실행 추상화를 도입하면 이 원칙과 정면으로 충돌한다. 단 **"지원 여부를 capability로 표현하고, 불확실하면 fail closed"** 라는 표현 방식은
TalkAbout `providers.py`(transport `cli`/`http`)에 참고 가치가 있다 → `[차용]` 비용 S.

---
## 7. Q6 — SQLite 사용 방식과 `foreign-sqlite-readers`의 목적
### 7.1 Orca의 SQLite 사용: "언제나 캐시, 진실은 파일/원격"
`src/main/sqlite/versioned-cache-database.ts` 한 파일에 정책이 압축되어 있다.

```ts
// :1  Rebuild unreadable/stale cache schemas: source files remain authoritative,
//     so replacement costs only a rescan instead of stranding the feature.
```

- **마이그레이션 = 스키마 버전 불일치 시 파일 삭제 후 재생성**. ALTER 누적이 아니다.
  `isStaleSchema`가 `meta.schema_version`을 읽어 다르면 `removeVersionedCacheDatabase(path)`로 `''/'-wal'/'-shm'/'-journal'` **4개 파일을 모두 삭제**하고 다시 만든다(`versioned-cache-database.ts:110-116`).
  주석: "Unlink stale caches; dropping multi-GB tables can exceed scanner readiness deadlines."(`versioned-cache-database.ts:45-46`) — 즉 ALTER/DROP 비용을 회피하는 선택.
- **손상은 1회만 재시도**: `SQLITE_CORRUPT|SQLITE_NOTADB|file is not a database|database disk image is malformed` 매칭 시 삭제 후 재생성, 두 번째 실패는 그대로 throw(`versioned-cache-database.ts:29-49`, `:68-77`). "a second failure on a file we just created is not corruption."
- **PRAGMA 순서가 의미를 갖는다**(`openWithPragmas`, `versioned-cache-database.ts:79-96`):
  `busy_timeout=5000` → `page_size` → `auto_vacuum=INCREMENTAL` → `journal_mode=WAL` → `synchronous=NORMAL` → `journal_size_limit=8MB`.
  주석: "Set busy timeout before WAL pragmas so another process's lock can clear", "Enable incremental vacuum before tables exist so purges can return pages in bounded steps", "WAL readers see committed state while writers publish atomically".
- **동시 접근**: WAL + busy_timeout 5초가 다중 프로세스 접근의 전부다. Orca는 SQLite를 **프로세스 간 조정 채널로 쓰지 않는다**(조정은 소켓·파일 락·ledger 파일).
- **권한**: DB와 `-wal`/`-shm`까지 0600(`harden-database-files.ts:6-19`), Windows는 userData DACL에 위임(`:7-10`).
- **오류 분류**(`sqlite-read-failure.ts`): `contended`(BUSY/LOCKED, 재시도 가능) / `wal-index-unavailable`(CANTOPEN + DB는 존재 → 9p·SMB·SSHFS에서 WAL shared memory 없음, **재시도 무의미**) / `unreadable`.
  `:1` 주석: "retry transient contention, but missing wal-index is structural; opaque driver text must not conflate them."
- **statement cache 256개 상한**, DDL 감지 시 캐시 전체 무효화, PRAGMA와 `SELECT *`는 캐시 제외(`sync-database.ts:22-38`, `:86-100`).
### 7.2 `foreign-sqlite-readers`의 목적
**"남의 앱 DB를 메인스레드에서 열지 않는다."**

- 대상: Cursor 데스크톱 프로필 DB(`ItemTable`에서 `cursorAuth/*` 키 읽기 — `readers/cursor-profile.ts:9-12`), OpenCode 바인더 세션 DB.
- 왜 위험한가: `readers/cursor-profile.ts:27` 주석 — **"Open only in a worker: the database and its WAL can each be multiple gigabytes."**
  WAL index rebuild가 4~6초 걸린 사례가 있어 main thread가 그동안 얼어붙는다.
- 구조: `ForeignSqliteReaderLane` — worker thread 1개 + 요청 큐(상한 8) + 타임아웃 + 유휴 teardown.
  - 타임아웃 **cursorProfile 10s / openCode 60s**(`foreign-sqlite-reader-client.ts:22-27`), 유휴 30s/120s(`:28-33`).
  - `awaitRetirement: true` — **"A timed-out read can still be inside SQLite; never start a second thread beside it."**(`foreign-sqlite-reader-client.ts:65-66`)
  - 연속 사망 2회면 crash loop 처리, 실패는 **fallback 값**으로 반환하고 main 스레드로 폴백하지 않는다(`foreign-sqlite-reader-client.ts:109-111` "A failed worker read must never fall back to blocking the main thread").
  - in-flight dedupe: 같은 키의 동시 읽기는 하나로 공유, 키는 "결과를 바꿀 수 있는 모든 입력"을 인코딩(`foreign-sqlite-reader-client.ts:76-88`). cursor를 키에 포함해 "A restarted poll must not deduplicate against an older cursor still in flight"(`foreign-sqlite-reader-client.ts:155-158`).
- 읽기 옵션: `{readonly: true, fileMustExist: true, timeout: 250ms}`(`readers/cursor-profile.ts:35-40`).
- 결과는 zod로 검증하고(`readers/cursor-profile.ts:39-44`), 실패는 `cursorProfileReadFailure()` 같은 **명시적 실패 상태**로 반환한다.

**판정**: `[차용]`. TalkAbout은 서버가 PostgreSQL, worker가 로컬 SQLite journal이라 "다중 프로세스 SQLite"가 실제로 존재한다
(`worker.py:122-127`). Orca의 (a) `busy_timeout` 선행, (b) WAL companion 부재를 **구조적 실패**로 분류, (c) 손상 시 1회 재생성 정책이 직접 이식 가능하다. 비용 S.

---
## 8. Q7 — 재연결 시 상태 복원: cursor인가 스냅샷인가
Orca는 **두 가지 다른 답**을 계층별로 갖고 있다.
### 8.1 PTY 출력: seq 워터마크 + 스냅샷 (cursor 아님)
- `PtyProviderBufferSnapshot`은 `seq: number` + `source:'headless'`를 포함한다(`pty-provider-contract.ts:30-48`).
  주석: "Flags proven at this snapshot seq; absent is unknown, never zero"(`:38`).
- 재부착 시 소비자는 (1) `getBufferSnapshot(id, {scrollbackRows})`로 권위 스냅샷을 받고, (2) checkpoint의 `pendingOutputSeq`와 대조해 그 이후 바이트만 재생한다(§2.2).
- 스트림이 유실됐을 가능성은 `canProvideAuthoritativeBufferSnapshot`으로 **사전 질의**하고(§6.1), 유실 시 `DataGapEvent`로 알린다(`daemon-stream-keep-tail-drop.ts:6`).
- **백그라운드 세션은 tail만 남기고 drop**한다: 세션당 512KB, 최소 64KB, 전체 2MB 예산을 세션 수로 나눠 분배(`daemon-stream-keep-tail-drop.ts:35-39`).
  주석: "Keep enough tail for a full TUI repaint; the 2x drop cap limits trimming churn." **drop은 "조용한 손실"이 아니라 gap 이벤트로 승격**된다.
- daemon의 `onReplay`는 **아무것도 하지 않는다**(`daemon-pty-event-subscriptions.ts:25-27`) — 재생 개념을 두지 않고 "스냅샷으로 재앵커"하는 설계다.
### 8.2 원격 control 채널: cursor가 아니라 "재구독 + 권위 재조회"
- `remote-runtime-shared-control-protocol.ts`에는 subscribe/unsubscribe만 있고 **cursor/replay API가 없다**(`getCleanupRequest`가 나열하는 메서드 목록, `:64-100`).
- 끊기면 백오프로 재연결하고 **구독을 처음부터 다시 건다**(`remote-runtime-shared-control-reconnect.ts`). 재구독 시점의 서버 상태가 권위다.
- 구독별 정리(unsubscribe)를 명시적으로 추적해 재연결 후 고아 구독을 남기지 않는다(`:64-100`, `remote-runtime-shared-control-subscriptions.ts`).
- retired request id로 **이전 연결의 응답이 새 연결에 섞이지 않게** 한다(`remote-runtime-shared-control-retired-request-ids.ts`).
### 8.3 비교
| 축 | Orca | TalkAbout |
| --- | --- | --- |
| 놓친 이벤트 추적 | PTY: seq 워터마크 + 스냅샷 재앵커 / 원격: 재구독 | **opaque vector cursor + event_id dedupe** (`worker.py:220-233`) |
| gap 복구 | `DataGapEvent`, `overflowed` → full snapshot | 409/410 → `agents/me/snapshot` rebase (`worker.py:223-232`) |
| 중복 억제 | seq batch 단조 증가 검증 | `INSERT OR IGNORE INTO inbox` (event_id PK) (`worker.py:139`) |
| 순서 보장 | 스트림 순서 + gap | room row lock + per-room seq (`ARCHITECTURE.md:83-87`) |

**판정**: `[대조]`. **이 축은 TalkAbout이 더 낫다.** TalkAbout은 cursor·snapshot rebase·event_id dedupe·retention gap 대응이
문서 계약(`docs/REFERENCE_WORKER.md:26-28`)과 코드에 모두 있다. Orca에서 참고할 것은 하나 —
**"손실을 조용히 넘기지 않고 gap 이벤트로 승격하고, 손실 구간은 스냅샷으로 재앵커한다"** 는 규칙을 retention/backpressure 경로에 명문화하는 것. 비용 S.

---
## 9. Q8 — 관측·진단 (로그 구조·상관 ID·crash reporting)
### 9.1 span 기반 tracing — OpenTelemetry 유사 자체 구현
`src/main/observability/tracer.ts`:
- `AsyncLocalStorage`로 부모 span을 await 경계 너머로 전파(`:1-3`, `:53`).
- `traceId`(16B) / `spanId`(8B) / `parentSpanId` / `startTimeUnixNano`(문자열) / `durationMs` / `attributes` / `events` / `exit`
  — Effect 호환 envelope로 NDJSON 기록(`tracer.ts:139-168`).
- exit는 3값 union: `Success` / `Failure(cause)` / `Interrupted(cause)`(`tracer.ts:193-199`). 취소와 실패를 **구분**한다.
- **모든 실패는 흡수된다**: "Optional tracing failures must not fail the instrumented operation"(`tracer.ts:170-178`), sink 없으면 `noopSpan`(`:42-53`).
- 계측은 **의미 있는 단위에만** 붙는다: `git.exec`, `worktree.<stage>`, `updater.<stage>`, `crash.breadcrumb`, `secure-path.windows-acl`
  (`instrumentation.ts:148-200`, `:268-292`).
- **샘플링**: 250ms 미만 성공 git 호출은 (서브커맨드, cwd) 키로 60초 창 60건까지만 기록(`instrumentation.ts:4-7`, `:102-130`).
  "union phase intervals to avoid double-counting concurrent work"로 소요시간을 합산한다(`instrumentation.ts:201-215`).
### 9.2 파일 sink — 회전·권한·동기 flush
`src/main/observability/local-file-sink.ts`:
- 10MB × 10파일 회전(`:19-20`), 200ms 배치(`:21`), 버퍼 32건에서 조기 flush(`local-file-sink.ts:18`).
- 디렉터리 0700 / 파일 0600을 **열린 fd에 fchmod**(`local-file-sink.ts:78-88`), 회전 시에도 패밀리 전체 chmod 재적용(`:55-61`).
- rename 실패(CIFS 등)를 삼키고 계속(`:104-125`) — "Permission hardening must not stop trace writes."
- `flush()`는 **동기**다. "Flush synchronously so buffered diagnostics can survive crash boundaries"(파일 `:1`), 그리고 crash breadcrumb 기록 직전 `flushActiveSink()`를 호출(`durable-crash-breadcrumb.ts:36`).
### 9.3 daemon 로그 — 다른 규칙
`src/main/daemon/daemon-file-log.ts`:
- 5MB × 3파일(`:6-7`), **한 줄 = JSON 하나** (`{src:'daemon', ts, pid, event, ...details}`) (`daemon-file-log.ts:74-86`).
- 첫 줄 주석이 정책이다: **"Daemon diagnostics must never affect lifecycle or include terminal content or tokens."**(`:1`)
- 쓰기 실패 시 로그를 **영구 비활성화**(`daemon-file-log.ts:40-48`, `:99-103`)하고 daemon은 계속 돈다.
- 위치: `<userData>/logs/{main.trace.ndjson, orcad.trace.ndjson, daemon.log}` (`logs-directory.ts:21-35`).
### 9.4 상관 ID의 실제 연결 방식
- **span 트리**가 상관 ID다. `crash.breadcrumb` span의 attributes에 `breadcrumb.name`/`breadcrumb.data`가 들어가 traceId로 묶인다(`durable-crash-breadcrumb.ts:18-36`).
- `getMainProcessLifecycleIdentity()`를 모든 브레드크럼에 병합(`durable-crash-breadcrumb.ts:10-17`) — "Main identity distinguishes renderer replacement from an app relaunch." 즉 **재시작 간 상관**까지 고려.
- 브레드크럼 이름은 화이트리스트로 sanitize(`:20-30`), 반복은 coalesce key + `minIntervalMs`로 억제하고 억제 카운트를 `suppressedSinceLast`로 남긴다(`:60-96`).
### 9.5 개인정보·비밀 제거
`src/main/observability/redactor.ts`:
- 라벨 기반 KV(`api_key|token|secret|password|bearer|authorization`) + **프로바이더별 패턴**(Anthropic/OpenAI/GitHub/AWS/JWT/Slack/PEM) (`:6-30`).
- URL userinfo 제거, `.env` 라인 통째 마스킹(`redactor.ts:32-40`).
- **키 이름 블록리스트**로 값 분류가 불가능한 경우를 방어(`redactor.ts:42-90`), 서버 모드에서는 telemetry identity 키까지 추가 제거(`:62-70`).
- sink·bundle·server ingest **3지점에서 idempotent** 적용(파일 `:1-2`).
- 동의 게이팅: `DO_NOT_TRACK`/`ORCA_TELEMETRY_DISABLED`는 업로드만 끄고 로컬 로그는 유지, `ORCA_DIAGNOSTICS_DISABLED`/CI는 둘 다 끈다(`observability/index.ts:1-5`, `:62-92`).
### 9.6 진단 번들
- `collectDiagnosticBundle`은 수집 전 `sink.flush()`를 호출해 "사용자의 마지막 행동"을 포함시킨다(`observability/index.ts:184-199`).
- daemon 로그는 **glob하지 않고 명시 파일명**으로 수집한다("collection does not glob the directory", `:211-214`).
- `install_id`는 번들에 넣지 않는다("Never include install_id: bundles use independent submission IDs", `observability/index.ts:183`).

**판정**: `[채택]`. TalkAbout에는 **구조적 로깅 계층이 전혀 없다** — `talkabout/*.py` 전체에 `logging` import 0건,
`print(` 30건(`cli.py`/`container.py` 출력용), `api.py:55`의 `request_id`는 **응답 body에만** 존재한다.
상관 ID·로그 상한·redaction·crash breadcrumb 4종을 최소 형태로 이식하는 것이 가장 비용 대비 효과가 크다. 비용 M. 부착: `talkabout/worker.py`, `runtime.py`, `api.py`.

---
## 10. Q9 — TalkAbout 구체 개선점 (파일:라인)
먼저 **이미 잘 되어 있는 것**을 인정한다. Orca보다 나은/동등한 지점이다.

| TalkAbout | 근거 | Orca 대응 |
| --- | --- | --- |
| Windows Job Object kill-on-close + **CREATE_SUSPENDED로 assign 후 resume** | `processes.py:120-128` | 동등 (`windows-pty-job` 계열) |
| Job accounting 폴링으로 **active==0까지 대기 후 종료 보고** | `processes.py:85-98` | 동등 이상 (`waitForPosixProcessGroupQuiescence` 대응) |
| launch/shutdown 직렬화(`ProcessSupervisor`) 로 취소 후 늦은 launch 차단 | `processes.py:148-162` | 유사 (`daemon-pid-file` claim) |
| 런타임 1회 호출에 대한 **4중 유한 타임아웃** | `worker.py:407-410` (`min(runtime_timeout, task.deadline-now, lease_remaining, run_deadline)`) | Orca에 등가물 없음(개별 상수만) |
| `runtime_started` 이전 실패만 재시도 허용 (`safe_pre_call_retry`) | `worker.py:145-148`, `:309-315` | Orca에 등가물 없음 |
| crash 시 `call_started → uncertain`으로 **blind 재호출 금지** | `worker.py:262-264`, `REFERENCE_WORKER.md:30` | — |
| cursor + 409/410 snapshot rebase + event_id dedupe | `worker.py:220-233` | §8.3 |
| journal backpressure 시 **rollback으로 event 보존** | `worker.py:151-155` (`with self.db` 안에서 raise) | Orca는 drop + gap 승격 |
| OS 락 기반 단일 writer lease | `connector.py:215-242` | 유사 (`ConnectorLease` ↔ `host-terminal-ledger`) |
| 컨테이너/자식 환경에서 credential 화이트리스트 | `agent_runtime.py:106-115` | 유사 (`pty/*-env.ts`) |

### 개선점 8건 (임팩트 순)
#### 9-1. `[채택]` journal 락 오류를 "stop 요청"으로 오인한다 — **정확성 버그**
```python
# talkabout/worker.py:391-400
def watch_stop():
    while not finished.wait(0.02):
        try:
            requested = self.journal.stop_requested()
        except sqlite3.Error:
            requested = True          # ← 락 경합을 stop 의도로 승격
        if requested:
            stop_seen.set(); cancel.set(); return
```

`Journal.stop_requested()`는 **호출마다 새 SQLite 연결**을 연다(`worker.py:172-175`: `sqlite3.connect(self.path, timeout=1)`).
같은 파일에 `busy_timeout` PRAGMA가 없고(`worker.py:123-124`는 `journal_mode=WAL`/`synchronous=FULL`만 설정),
WAL 체크포인트나 다른 연결의 배타 구간과 겹치면 `sqlite3.OperationalError`가 날 수 있다.
그 순간 **진행 중인 모델 호출이 취소되고 `stop_seen`이 세워져 `cancelled`로 커밋**된다(`worker.py:413-416`).
사용자는 "중지"를 누른 적이 없는데 턴이 취소되는 경로다.

수정: (a) `busy_timeout=3000`을 공유 연결에 설정, (b) `OperationalError`(락/일시 오류)와 "설정값이 실제 true"를 구분해
**알 수 없음(unknown)은 stop이 아니다**로 처리. Orca의 `sqlite-read-failure.ts` 3분류(contended / wal-index-unavailable / unreadable)를 그대로 참고.
비용 **S**. 부착: `worker.py:123-124`, `worker.py:391-400`, `worker.py:172-179`.
#### 9-2. `[채택]` `bounded_call`의 stop 경로에 시간 상한이 없다
```python
# talkabout/runtime.py:51-59
def stop():
    cancel.set()
    shutdown = getattr(runtime, 'shutdown', None)
    if callable(shutdown):
        stopped = bool(shutdown())    # ← 무제한 대기 가능
```

`bounded_call`의 존재 이유가 "bound"인데, 타임아웃으로 빠져나가는 유일한 경로가 **상한 없는 `shutdown()`** 을 호출한다.
`shutdown()`이 blocking이면 bounded_call 전체가 무한 대기가 된다. 이후 `worker.py:409`의 4중 타임아웃도 무력화된다.
부수적으로 `_JSONLTransport.close()`는 `supervisor.stop()` → `stop_windows_job`에서 최대 5초 + `process.wait(timeout=1)`을 기다린다(`agent_runtime.py:315-327`, `processes.py:86-98`).

수정: shutdown을 별도 스레드에서 실행하고 `join(deadline)`으로 상한을 건다. 미확인 종료는 `RuntimeTimeout(runtime_stopped=False)`로 보고.
Orca의 `DAEMON_SHUTDOWN_TIMEOUT_MS = 5_000` + `createDaemonShutdownDeadline(..., () => process.exit(0))` 패턴(`daemon-shutdown-deadline.ts:3-20`)이 정확히 같은 문제를 푼다.
비용 **S**. 부착: `runtime.py:33-72`, `connector.py:116-119`(finally의 `shutdown()`도 동일).
#### 9-3. `[채택]` stop 감시 폴링이 초당 50회 새 DB 연결을 만든다
- `worker.py:392` `while not finished.wait(0.02)` → 20ms 주기
- `worker.py:394` 매 회 `stop_requested()` → 매번 `sqlite3.connect()` + `SELECT` + `close`
- `connector.py:68-78`의 `watch_stop`도 20ms 주기
- `worker.py:80`과 `worker.py:382`의 heartbeat는 별개 스레드

호출 1건(수 초~수십 초) 동안 250~2,500회 연결이 열린다. `journal_capacity` 백프레셔와 겹치면 9-1의 오탐 확률이 함께 올라간다.
수정: (a) 장수명 read-only 연결 1개를 watcher 스레드가 소유, (b) 폴링 주기를 20ms → 200~500ms로 올리고(중지 지연은 여전히 사람 체감 이하), (c) `file_status`/별도 signal 플래그 파일 대안 검토.
Orca는 이 문제를 **별도 제어 흐름 + 명시적 pause/resume RPC**로 푼다(`daemon-pty-event-subscriptions.ts:208-212` "resume producers so warm reattach does not wait for a pause failsafe").
비용 **S**. 부착: `worker.py:172-180`, `:391-400`, `connector.py:68-78`.
#### 9-4. `[채택]` worker를 감독하는 주체가 없다 — crash-loop 상한 부재
TalkAbout의 crash 복구 설계는 훌륭하다(`REFERENCE_WORKER.md:24-33`의 지점별 표). 그러나 **"누가 worker를 다시 띄우는가"** 가 문서·코드 어디에도 없다.
`ConnectorLease`는 중복 실행만 막고(`connector.py:215-242`), worker 프로세스가 즉사하면 lease 파일은 OS가 닫아 자동 해제되며,
운영자는 queue 상태를 보고 수동으로 재실행한다. 재실행이 즉시 반복 실패하면 **무한 재시작 루프**를 막을 장치가 없다.

수정: 재시작 감독 루프(별도 프로세스) + 슬라이딩 윈도 상한. Orca `daemon-respawn-throttle.ts:13-14`(5회/60초)와
`DaemonCrashLoopError`의 메시지("refusing to respawn it for another Xs … check the daemon log")를 그대로 concept 차용.
비용 **M**. 부착: `connector.py`(lease 옆), `cli.py`(run 루프), `docs/REFERENCE_WORKER.md`(계약에 "재시작 정책" 절 추가).
#### 9-5. `[채택]` 상관 ID 있는 로그가 없다
현재 관측 수단은 (i) CLI `print` JSON, (ii) journal 테이블, (iii) 서버 API 오류 body `request_id`(`api.py:55`)뿐이다.
`request_id = ident()`은 **응답에만** 실리고 로그에 남지 않는다. worker 쪽에는 turn/task 식별자가 로그로 승격되는 경로가 없다.
`_beat`(worker.py:459-468)가 서버에 stage를 남기지만 이는 **서버 상태**이지 로컬 진단 로그가 아니다.

수정(최소): `TurnTrace` 컨텍스트 — `{run_id, task_id, attempt, generation, membership_revision}`을 만들어
`worker.step` 진입 시 생성, JSON line one-per-event로 `logs/worker-<run>.ndjson`에 기록. 회전 10MB×3, 0600.
터미널 내용/credential은 절대 넣지 않는다(Orca `daemon-file-log.ts:1`의 정책 문장을 그대로 계약으로).
비용 **M**. 부착: `worker.py`(step), `runtime.py`(bounded_call 경계 이벤트), `api.py`(request_id를 로그에도), `docs/ARCHITECTURE.md`에 "관측" 절 신설.
#### 9-6. `[차용]` `stop_windows_job`의 고정 크기 멤버 버퍼
```python
# talkabout/processes.py:76-79
size = 8 + ctypes.sizeof(ctypes.c_size_t) * 4096
members = ctypes.create_string_buffer(size)
if not kernel.QueryInformationJobObject(job, 3, members, size, None):
    raise ProcessCleanupError('job_members_query_failed')
```

job 구성원이 4,096개를 넘으면 `ERROR_MORE_DATA`로 실패한다. 이어서 `:85` `TerminateJobObject`는 실행되지만,
`:94-98`의 개별 handle `WaitForSingleObject` 확인은 건너뛰고 예외가 올라가 `cleanup_confirmed=False`가 된다.
실제로는 job close로 모두 죽으므로 안전하지만, **"정리 미확인"이라는 잘못된 신호**가 남는다.
Orca의 방식은 애초에 **멤버 목록을 열거하지 않고** `taskkill /t`의 종료 코드를 verdict로 삼는 것이다(`windows-tree-kill.ts:6-13`, `:108-109`).
수정: 실패 시 size를 키워 1회 재시도하고, 끝내 실패하면 "exit unconfirmed"를 별도 상태로 보고(예외 아님).
비용 **S**. 부착: `processes.py:72-102`.
#### 9-7. `[차용]` POSIX 격리 미지원 — 참가자 환경의 절반이 실행 불가
```python
# talkabout/processes.py:111-112
if os.name != 'nt':
    raise ProcessCleanupError('runtime_containment_unsupported')
```

`agent_runtime.py:102-103`의 `_containment_supported()`도 `os.name == 'nt'`만 참이고, `agent_runtime.py:3-4` docstring이
"POSIX is deliberately unsupported"라고 명시한다. 이는 **의도된 fail-closed**이며 옳은 태도다. 다만 결과적으로
Linux/macOS 참가자는 P0에서 아예 참여할 수 없다.

Orca에서 이식 가능한 부품(전부 MIT, `LICENSE` — 코드 복사 시 저작자 표시·라이선스 고지 필요):
- `pty/posix-pty-process-groups.ts:191-220` — controlling tty + session id 기반 그룹 발견(self-kill 방어 포함)
- `process-host/src/process-tree-termination.ts:44` `process.kill(-pgid)` + `:81-97` `Z` 상태까지 대기
- `pty-descendant-exit-verification.ts` — `exited/live/unverifiable` verdict로 "죽였다≠죽었다" 구분
- `daemon-cgroup-scope.ts` 계열은 Electron 전용이라 제외. Python은 `os.setsid()` + `prctl(PR_SET_PDEATHSIG)`/`DoubleFork` 중 택일.

비용 **L**(새 플랫폼 + 테스트 매트릭스). 부착: `processes.py`에 `PosixManagedProcess` 추가, `agent_runtime.py:102-103` 확장, `docs/REFERENCE_WORKER.md`의 adapter 검증 절 갱신.
→ Top 5에서 제외한 이유는 비용이며, **전략적으로는 가장 큰 항목**이다.
#### 9-8. `[차용]` retention은 fixture 전용, 삭제 절차가 계약에 없다
```python
# talkabout/retention.py:31-33
def compact_room_fixture(room, *, synthetic_fixture=False, now=None, policy=DEFAULT_RETENTION):
    if synthetic_fixture is not True:
        raise ValueError("synthetic_fixture_required")
```

프로덕션 삭제 경로가 없다는 것은 의도된 보수성이다. 다만 `first_retained_seq`만 갱신하고(`retention.py:41`)
**삭제의 관측 가능성**이 없다(무엇을 몇 건 지웠는지, 실패 시 어떻게 되는지).
Orca의 2단계 삭제(tombstone → 비동기 회수 + 재시도 + 기동 시 drain, `terminal-history-session-tombstone.ts:14-16`, `:110-121`)와
GC 안전규칙(최소 나이 5분, live 집합이 비면 전체 스킵, `terminal-history-gc.ts:20`, `:76-80`)이 그대로 참고된다.
PostgreSQL에서는 "이름 변경" 대신 `deleted_at` 마킹 + 배치 물리 삭제 + 실패 재시도로 사상하면 된다.
비용 **M**. 부착: `retention.py`, `store.py`, `docs/RETENTION_BOUNDS.md`.
### 10.1 문서 갭
- `docs/ARCHITECTURE.md`에 **관측(로그·상관ID·진단)** 절이 없다. Orca는 `observability/`라는 1급 디렉터리를 갖고 그 정책을 코드 주석에 못박는다.
- `docs/REFERENCE_WORKER.md`의 `crash 지점별 복구` 표(`:24-33`)에 **"worker 프로세스 자체가 죽었을 때 재시작 주체·상한"** 행이 없다.
  `heartbeat·중단·오프라인`(`:37-43`)에도 "hang 감지 신호 정의"가 없다 — 현재는 시간(deadline)만 있고 **신호**가 없다.
- `docs/ARCHITECTURE.md:89` "서버 차단과 remote process 중지는 다릅니다"는 Orca보다 명확한 좋은 문장이다. 이 원칙을 유지하면서 위 3개 절만 보강하면 된다.

---
## 11. Q10 — TalkAbout에 정말 부족한 것만 (후보 a~e 판정)
### (a) CLI 자식 프로세스 고아화/좀비
**판정: 부분 인정 — Windows는 이미 충분, POSIX가 실질 공백.**

TalkAbout 현 처리:
- `CREATE_SUSPENDED`로 생성 → Job Object(`KILL_ON_JOB_CLOSE`, breakaway 금지)에 **assign 성공 후** resume(`processes.py:120-128`). 즉 자식이 사용자 코드를 실행하기 전에 격리가 보장된다.
- `TerminateJobObject` → `Accounting.active==0`까지 5초 대기 → 개별 handle `WaitForSingleObject`(`processes.py:85-98`). 좀비 잔존 시 `process_exit_unconfirmed`로 실패 보고(`:97`).
- worker가 SIGKILL당해도 job handle이 커널에 의해 닫히므로 자식 트리 전체가 종료된다(KILL_ON_JOB_CLOSE의 정의).
- POSIX는 **실행 자체를 거부**(`processes.py:111-112`).

남은 구멍: 4,096 초과 멤버의 잘못된 "미확인" 신호(§9-6), POSIX 전무(§9-7).
Orca 차용 가치: `taskkill`/`ps` 기반이 아니라 **verdict(`exited/live/unverifiable`)** 로 정리 결과를 3값 보고하는 규약.
### (b) hang 감지·유한 타임아웃
**판정: 부분 인정 — 유한 타임아웃은 이미 4중, "감지"와 "유한 shutdown"이 없다.**

TalkAbout 현 처리(이미 강함):
- `bounded_call`이 20ms 폴링으로 deadline/cancel 확인(`runtime.py:60-72`).
- 호출 예산은 `min(runtime_timeout, task.deadline-now, lease_remaining, run_deadline)`(`worker.py:407-409`).
- heartbeat 스레드가 별도 제어 흐름이고 실패 시 `cancel.set()`(`worker.py:380-389`), 결과는 `uncertain`(`:417-418`).
- 서버 측 lease/deadline/attempt 예산이 상위 상한(`ARCHITECTURE.md:74-75`).

없는 것: (i) **shutdown 자체의 상한**(§9-2), (ii) worker 프로세스 liveness를 밖에서 보는 감시자, (iii) hang 증거의 내구 기록.
Orca의 `hang-detection-marker` + `previous-hang-detection`이 (iii)의 정확한 구현이고, "관측만, 죽이지 않음"·"슬립 보정"·"해소 판정" 3규칙이 오탐 억제의 답이다.
### (c) worker 재시작 복구
**판정: 이미 설계·구현됨. 남은 것은 "감독"뿐.**

TalkAbout 현 처리: journal 상태기계(`ready→claimed→call_started→result_saved→done`)와
`call_started → uncertain`(재호출 금지) 규칙이 `worker.py:262-264`·`:412-423`에 구현되어 있고, `REFERENCE_WORKER.md:24-33`이 지점별로 문서화한다.
`claim_key`/`complete_key`의 durable idempotency(`worker.py:285`, `:432`)도 있다.
없는 것은 crash-loop 상한 + 재시작 주체(§9-4)뿐이다. → `[채택]`은 이 좁은 부분에만 해당한다.
### (d) 재연결 시 놓친 event 따라잡기
**판정: 이미 TalkAbout이 더 잘한다. 부족하지 않다.**

- cursor: `worker.py:220-222`, snapshot rebase: `:223-232`(409/410), event_id dedupe: `:139`.
- 서버 계약: `ARCHITECTURE.md:87` "재접속·timeout은 신호 없이 재조회하여 missed wake-up을 복구합니다."
- retention gap 후 rebase가 **queued task를 재발견**한다(`worker.py:228-232`) — 이건 Orca에 없는 TalkAbout 고유의 좋은 설계다.
- 참고할 것은 하나: 손실 구간을 **gap 이벤트로 승격**하고 스냅샷으로 재앵커한다는 Orca의 규칙(§8.1)을 backpressure 경로 문서에 명시하는 것. 비용 S.
### (e) 로그 상관 ID
**판정: 인정. 가장 명확한 공백.**

TalkAbout에는 로깅 계층이 없다(`logging` import 0건, `print` 30건). `api.py:55`의 `request_id`는 응답 전용이고 로그로 이어지지 않는다.
Orca 대응: `tracer.ts`의 traceId/spanId + `daemon-file-log.ts`의 one-JSON-per-line + `redactor.ts`의 3지점 idempotent 마스킹 + `durable-crash-breadcrumb.ts`의 crash 생존 flush.
TalkAbout 규모에서는 **span 트리까지 필요 없고, "turn 하나 = run_id 하나 = 로그 파일 하나"** 로 축소하면 M 비용으로 90%를 얻는다.
### 후보 판정 요약
| 후보 | 판정 | 근거 요약 | Orca에서 가져올 것 | 비용 |
| --- | --- | --- | --- | --- |
| (a) 고아/좀비 | **부분 인정** (POSIX만) | Windows는 Job Object로 이미 격리·검증됨 | verdict 3값 규약, pgid+session id 발견 | L |
| (b) hang 감지/유한 타임아웃 | **부분 인정** | 타임아웃은 4중, shutdown만 무한 | marker + detection loop + 해소/슬립 규칙 | S |
| (c) worker 재시작 복구 | **이미 구현** | journal 상태기계 + uncertain 금지 | crash-loop 슬라이딩 윈도 | M |
| (d) 재연결 event 따라잡기 | **부족하지 않음** | cursor+snapshot+dedupe 모두 있음 | gap 승격 규칙만 | S |
| (e) 로그 상관 ID | **인정** | 로깅 계층 자체가 없음 | one-JSON-per-line + redaction + crash flush | M |

---
## 12. Top 5 실행 제안
| 순위 | 제안 | 왜 (근거) | TalkAbout 부착 위치 | Orca 참조 | 태그 | 비용 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | journal 락 오류를 stop으로 오인하지 않게 수정 + `busy_timeout` | `sqlite3.Error` → `requested=True`가 진행 중 turn을 `cancelled`로 커밋시킨다 (`worker.py:391-400`, `:413-416`) | `worker.py:123-124`, `:172-179`, `:391-400` | `sqlite-read-failure.ts:1` (contended/wal-index/unreadable 3분류) | `[채택]` | S |
| 2 | `bounded_call` stop 경로에 shutdown 시간 상한 | 유일한 타임아웃 탈출로가 무제한 `shutdown()`이다 (`runtime.py:51-59`) | `runtime.py:51-59`, `connector.py:116-119` | `daemon-shutdown-deadline.ts:3` (5초 + 강제 exit) | `[채택]` | S |
| 3 | stop 감시 폴링 핫패스 제거(장수명 연결 + 200ms) | 20ms마다 새 SQLite 연결 → 초당 50회, 9-1 오탐 확률 상승 (`worker.py:392-394`, `connector.py:69`) | `worker.py:172-180`, `:391-400`, `connector.py:68-78` | `daemon-pty-event-subscriptions.ts:208-212` (명시적 pause/resume) | `[채택]` | S |
| 4 | worker 재시작 감독 + crash-loop 슬라이딩 윈도 상한 | 재시작 주체·상한이 코드·문서에 없다 (`REFERENCE_WORKER.md:24-33`에 행 없음) | `connector.py`(lease 옆), `cli.py`(run 루프), `docs/REFERENCE_WORKER.md` | `daemon-respawn-throttle.ts:13-14` (5회/60초) + `DaemonCrashLoopError` 메시지 | `[채택]` | M |
| 5 | turn 단위 상관 ID + JSON-line 로그 + crash flush | `logging` 0건, `request_id`는 응답 body 전용 (`api.py:55`) | `worker.py`(step), `runtime.py`, `api.py`, `docs/ARCHITECTURE.md`(관측 절 신설) | `daemon-file-log.ts:1`, `:74-86` + `local-file-sink.ts:19-21` + `redactor.ts` + `durable-crash-breadcrumb.ts:36` | `[채택]` | M |

**각주 — Top 5에서 제외했지만 전략적으로 가장 큰 항목:** §9-7 POSIX 격리(비용 L). TalkAbout의 P0는 Windows만 참가 가능하며,
이는 `processes.py:111-112`의 **의도된 fail-closed**다. 해제하려면 `posix-pty-process-groups.ts` 계열의 그룹 발견 로직과
`exited/live/unverifiable` verdict를 Python으로 옮기는 별도 트랙이 필요하다. Orca 코드를 복사할 경우 MIT `LICENSE` 고지가 필요하다.

---
## 13. 서로 모순되는 관찰 (숨기지 않음)
1. **"Orca는 프로세스를 앱보다 오래 살린다" vs "daemon 사망 시 scope를 강제 stop한다"** — 둘 다 사실이고 모순이 아니다.
   `daemon-scope-death-watch.ts`는 **daemon 자신이 죽은 뒤** 잔여 PTY/scope를 회수하는 장치이고(`$1` = daemon pid),
   앱 종료 시 daemon을 죽이는 장치가 아니다. 처음 읽을 때 앱-수명 결박으로 오해하기 쉬워 명시한다.
2. **"Orca는 hang을 감지한다"** — 감지 대상은 **Electron 메인스레드**뿐이다. 에이전트 CLI의 무출력 정지는 감지하지 않는다(§1 미확인).
   TalkAbout이 이 항목에서 뒤진다고 단정할 수 없다. 뒤지는 것은 **증거 보존**과 **shutdown 상한**이다.
3. **"SQLite는 프로세스 간 조정에 약하다"** — Orca는 이 약점을 피해 간다. SQLite는 전부 캐시이고, 조정은 소켓·파일 락·ledger 파일이 맡는다.
   반대로 TalkAbout은 **SQLite journal을 조정 채널로 쓴다**(`worker.py:172-180`의 stop 플래그). 이 설계 차이가 9-1·9-3 결함의 뿌리다.
   → TalkAbout의 선택 자체는 합리적이지만(단일 파일, OS 락), **읽기 경합을 제어 채널로 쓰는 부분만** 고치면 된다.

---
## 부록 A — 인용 파일 색인
Orca (`orca-analysis/` 기준 상대경로):
`src/main/daemon/` — `daemon-entry.ts`, `daemon-server.ts`, `daemon-spawner.ts`, `daemon-endpoint-lifecycle.ts`, `daemon-endpoint-ownership.ts`,
`daemon-client-hello-handshake.ts`, `daemon-session-owner-attach.ts`, `daemon-pid-identity.ts`, `daemon-cgroup-scope.ts`,
`daemon-user-manager-lifetime.ts`, `daemon-scope-death-watch.ts`, `daemon-launched-child.ts`, `daemon-respawn-throttle.ts`,
`host-terminal-ledger.ts`, `macos-daemon-launchd.ts`, `daemon-file-log.ts`, `daemon-shutdown-deadline.ts`, `daemon-request-deadline.ts`,
`history-manager.ts`, `history-paths.ts`, `history-reader.ts`, `daemon-checkpoint-file.ts`, `daemon-session-scrollback-window.ts`,
`daemon-restore-scrollback-depth.ts`, `daemon-pty-runtime-state.ts`, `daemon-pty-checkpoint-scheduler.ts`, `daemon-pty-checkpoint-persistence.ts`,
`daemon-pty-event-subscriptions.ts`, `daemon-pty-daemon-recovery.ts`, `terminal-history-file-limits.ts`, `terminal-history-log.ts`,
`terminal-history-session-writer.ts`, `terminal-history-metadata.ts`, `terminal-history-checkpoint-reader.ts`, `terminal-history-cold-restore-info.ts`,
`terminal-history-restorable-retention.ts`, `terminal-history-session-tombstone.ts`, `terminal-history-recovery-quarantine.ts`,
`terminal-checkpoint-serializer.ts`, `cold-restore-payload-cache.ts`, `pty-session-id.ts`, `daemon-private-file-modes.ts`,
`daemon-stream-keep-tail-drop.ts`, `terminal-descendant-shutdown.ts`, `types.ts`
`src/main/` — `terminal-history-paths.ts`, `terminal-history-gc.ts`, `pty-descendant-termination.ts`, `pty-descendant-exit-verification.ts`,
`pty/posix-pty-process-groups.ts`, `providers/pty-provider-contract.ts`, `providers/execution-host-provider-dispatch.ts`,
`hang-watchdog/*.ts` (8파일), `observability/{tracer,local-file-sink,logs-directory,redactor,index}.ts`,
`diagnostics/event-loop-delay-probe.ts`, `crash-reporting/durable-crash-breadcrumb.ts`,
`sqlite/{versioned-cache-database,sqlite-read-failure,harden-database-files,sync-database}.ts`,
`foreign-sqlite-readers/{foreign-sqlite-reader-client,foreign-sqlite-reader-entry,foreign-sqlite-reader-protocol}.ts` + `readers/cursor-profile.ts`,
`ssh/{ssh-reconnect-ladder,ssh-reconnect-error-classification}.ts`, `ephemeral-vm/{runtime-service,runtime-attachment}.ts`,
`worker-thread-child-process/worker-thread-child-process.ts`
`src/packages/process-host/src/` — `process-tree-termination.ts`, `windows-tree-kill.ts`, `process-tree-kill-gate.ts`, `bounded-output-sink.ts`
`src/shared/` — `terminal-scrollback-policy.ts`, `orchestration-ask-timeout.ts`, `orchestration-message-wait-timeout.ts`, `remote-runtime/control/*.ts`

TalkAbout (`TalkAbout/` 기준 상대경로):
`talkabout/processes.py`, `talkabout/runtime.py`, `talkabout/worker.py`, `talkabout/connector.py`, `talkabout/agent_runtime.py`,
`talkabout/retention.py`, `talkabout/file_migration.py`, `talkabout/api.py`
`docs/ARCHITECTURE.md`, `docs/REFERENCE_WORKER.md`
## 부록 B — 라이선스
Orca는 MIT(`orca-analysis/LICENSE`). 이 보고서는 **개념·설계 비교만** 했고 코드를 복사하지 않았다.
§9-7처럼 실제 코드를 이식할 경우 저작자 표시와 라이선스 전문 고지가 필요하며, TalkAbout `THIRD_PARTY_NOTICES.md`에 항목을 추가해야 한다.
