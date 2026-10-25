# r2 — hermes-agent: 상태계층 · 동시성 · 관측 · 게이트웨이 정밀 분석

**트랙 명제**: TalkAbout은 SQLite journal을 **조정 채널**로 쓰다가 결함을 만들었다(`worker.py:391-400`, `:172-175`). hermes-agent가 `hermes_state_lockguard.py`·`lockowners.py`·`readpool.py`·`wal.py` 계열로 같은 문제를 정면으로 다루는가?

**판정: 다룬다. 그리고 이 트랙의 명제는 절반이 틀렸다.** hermes에서 TalkAbout 결함 #1에 해당하는 직접 해법은 `lockguard`/`lockowners`가 **아니다**. 그 둘은 POSIX 전용이며 Windows에서 no-op이다(`hermes_state_lockguard.py:17,50-53`). 진짜 해법은 훨씬 평범한 세 파일에 있다: `hermes_state_errors.py`(오류 3분류) · `hermes_state.py:1008-1125`(시간예산+지터 재시도) · `hermes_cli/sqlite_util.py:18-67`(busy_timeout 단일 노브). **이 3개가 TalkAbout의 실제 결함을 고친다.**

---

## 0. 결론 요약 (한 표)

| # | 항목 | 판정 | 비용 | TalkAbout 부착 위치 | 최적 저장소 |
|---|---|---|---|---|---|
| A1 | **journal 오류 3분류** — transient는 재시도, 손상은 실패, 불명은 전파. "stop"으로 승격 금지 | `[채택]` | S | `talkabout/worker.py:172-175,391-400` | **hermes** |
| A2 | **시간예산 + 지터 재시도 계층** (busy_timeout 아래에 재시도가 있어야 함) | `[채택]` | S | `talkabout/worker.py:172-179` (신규 `_read_with_patience`) | **hermes** |
| A3 | **20ms 폴링 커넥션 제거** — 살아있는 DB를 프로브용으로 열지 않는다 | `[채택]` | S | `talkabout/worker.py:391-400` | **hermes** |
| A4 | **"불확실성은 파괴적 행동을 승인하지 않는다"** — 증명 실패 시 상태를 덮어쓰지 않음 | `[채택]` | S | `talkabout/worker.py:394-396` | **hermes** |
| B1 | 부분 실패 시 원자적 스키마 catch-up (트랜잭션 내 postcondition 검증) | `[차용]` | M | `talkabout/store.py` | hermes |
| B2 | in-place 행 재작성 + CAS digest 스테일 판정 | `[차용]` | M | `talkabout/store.py`, `talkabout/retention.py` | hermes |
| B3 | 구조적 손상 **sticky 래치** (스스로 해제 안 함) | `[채택]` | S | 신규 `talkabout/storage_health.py` → `api.py`, `worker.py` | hermes |
| C1 | 락 소유자 증명 — **Windows Restart Manager** | `[채택]` | S | `talkabout/processes.py` | **hermes** |
| C2 | `lockguard`(OFD 락) · `lockowners`(`/proc/locks`) | `[제외]` | — | — | hermes(POSIX 전용, Windows 무효) |
| D1 | 단일 async 로그 큐 + 마스킹 포매터 + 세션 상관 태그 | `[채택]` | M | 신규 `talkabout/logging.py` → `worker.py`, `runtime.py`, `api.py` | **hermes** |
| D2 | Windows 로그 로테이션 함정(WinError 32) | `[채택]` | S | 위와 동일 | **hermes** |
| E1 | **bounded exit escort** — 종료 경로에 상한을 두는 구조 | `[채택]` | S | `talkabout/runtime.py:51-59` | **hermes** |
| E2 | 시작 progress lease + CPU fallback 상한 | `[차용]` | M | 신규 watchdog | hermes |
| F1 | 인가 판정 **사다리 + 최종 default deny** | `[대조]` | — | `talkabout/participation.py` | hermes |
| F2 | "판정(peek) vs 소비(admit)" 분리 | `[채택]` | S | `talkabout/participation.py` | **hermes** |
| F3 | 관리자 권한 Windows에서 `mode=0o700` 디렉터리 DACL 함정 | `[채택]` | S | `talkabout/text_files.py:42`, `agent_runtime.py:588` | **hermes** |
| G1 | 오류 시그니처 기반 incident dedup + ack | `[채택]` | M | 신규 `talkabout/incidents.py` → `worker.py` | **hermes** |
| G2 | terminal 행 prune 후에도 idempotency 보존(tombstone) | `[차용]` | M | `talkabout/worker.py`(journal) | hermes |
| G3 | leaser 소유 증명 = `(pid, process_start_time)` | `[차용]` | M | `talkabout/domain.py:117-125`, `worker.py` lease | **hermes** |

> `[제외]` 판정 근거: hermes 상태계층의 **절반 이상이 POSIX/WAL 세대 물리학 전용**이다. Windows 우선 프로젝트인 TalkAbout에 그대로 옮기면 비용만 쓰고 아무것도 얻지 못한다(§4).

---

## 1. 실측 — 브리프 정정 2건

브리프(`analysis/BRIEF-R2.md:26`)는 "`hermes_state*.py` 70여 개"라고 적었다. **실측 60개**다.

```
$ (Get-ChildItem hermes-agent/hermes_state*.py).Count          →  60
$ (Get-ChildItem hermes-agent/hermes_state_mutation*.py).Count →  10
```

또 브리프/task-7이 지목한 `hermes_state_db.py`는 **없다**. 대응물은 `hermes_state_dbfile.py`(858줄)다. `hermes_state_lockguard.py`·`lockowners.py`·`wal.py`·`readpool.py`·`holders.py`·`reconcile.py`·`repair.py`·`prune_rewrite.py`·`local_migration.py`·`health.py`·`maintenance.py`는 전부 실존한다(직접 확인).

60개 중 **전수 정독은 12개**(lockguard, lockowners, readpool, dbfile, errors, wal 일부, reconcile 일부, prune_rewrite, health, local_migration, holders 일부, schema 일부)만 했다. 나머지 48개는 파일명·docstring으로 역할만 판정했고 §11에 "미확인"으로 분리했다.

**계열 분류(실측)**

| 계열 | 파일 | 판정 |
|---|---|---|
| 락/WAL 물리 | `lockguard`, `lockowners`, `holders`, `wal`, `dbfile` | POSIX 전용 다수 → `[제외]` (단 `holders`의 Windows RM 경로는 `[채택]`) |
| 커넥션 예산 | `readpool` | `[차용]` (상수·계층 구조) |
| 오류 분류 | `errors`, `health` | **`[채택]` 최우선** |
| 스키마/마이그레이션 | `schema`, `reconcile`, `repair`, `profile_repair`, `local_migration` | `[차용]` |
| 보존/압축 | `prune_rewrite`, `compression`, `worker_compression` | `[차용]` |
| 트랜스크립트 도메인 | `messages`, `sessions`, `search`, `fts`, `timeline`, `titles`, `todo`, `usage` | `[제외]` (도메인 불일치) |
| 권한/정체성 | `identity`, `keys`, `registry`, `gateway` | `[제외]` (TalkAbout 자체 모델이 더 정교) |
| 프로세스 격리 | `pidns`, `portability`, `coverage` | 미확인 |

---

## 2. ★최우선 — TalkAbout journal 결함의 직접 해법 (Q1 · Q8)

### 2.1 결함 재확인 (내가 직접 읽은 코드)

`talkabout/worker.py:172-175`:

```python
def stop_requested(self):
    with closing(sqlite3.connect(self.path, timeout=1)) as connection, connection:
        row = connection.execute("SELECT v FROM settings WHERE k='stop'").fetchone()
        return bool(row and json.loads(row[0]))
```

`talkabout/worker.py:391-400`:

```python
def watch_stop():
    while not finished.wait(0.02):
        try:
            requested = self.journal.stop_requested()
        except sqlite3.Error:
            requested = True          # ← 여기
        if requested:
            stop_seen.set()
            cancel.set()
            return
```

결과: `cancel.set()` → `bounded_call`이 `RuntimeTimeout` → `worker.py:413-416`에서 `self.journal.save(tid, "cancelled" if error.runtime_stopped else "uncertain", payload)`. **일시적 SQLite 락 경합 한 번이 진행 중 turn을 `cancelled`로 커밋한다.** 결함 확인.

추가 확인 사항(브리프에 없던 것):

1. **`stop_requested()`는 10곳 이상에서 호출되는데 예외 처리가 제각각이다.** `worker.py:238-242`의 `step()`은 `try` 밖에서 호출한다:
   ```python
   check_deadline(self.run_deadline)
   if self.journal.stop_requested():        # ← 예외 처리 없음
   ```
   같은 일시 오류가 (a) watcher 스레드에서는 "turn 취소", (b) `step()`에서는 **worker 루프 예외 전파**로 서로 다른 결과를 만든다. `connector.py:22,71,80,86`, `cli.py:176,218,324`도 같은 함수를 부른다.
2. **`tests/test_journal_connection_lifecycle.py:83-97`이 "예외를 밖으로 던지는 계약"을 이미 고정하고 있다.**
   ```python
   monkeypatch.setattr(connection_type, 'execute', fail)   # sqlite3.OperationalError
   with pytest.raises(sqlite3.OperationalError, match='synthetic query failure'):
       journal.stop_requested()
   assert counts == {'created': 1, 'live': 0, 'peak': 1}
   ```
   즉 수정은 `stop_requested()` 안에서 예외를 삼키는 방식이면 안 된다. **호출 지점(watcher)에서** 고쳐야 한다. 같은 파일 `:68-80`은 호출 100회에 커넥션 100개 생성을 **정상 동작으로 단정**한다(`assert counts == {'created': 100, ...}`). 즉 "매 호출 신규 커넥션"은 누수 방지용 **의도된 설계**이고 테스트로 고정돼 있다. → 수정 시 이 테스트도 함께 갱신해야 한다(비용에 포함).

### 2.2 브리프 정정 3건 (모순 명시)

| 브리프 주장 | 실측 | 근거 |
|---|---|---|
| "`worker.py:172-175`(20ms마다 신규 연결, **busy_timeout 없음**)" | **busy_timeout은 있다.** `sqlite3.connect(timeout=1)`은 busy handler를 1000ms로 설정한다. Python `timeout` 파라미터가 곧 busy_timeout이다 | `worker.py:173`; hermes도 같은 등식을 명시한다 — `hermes_cli/sqlite_util.py:33-34` "``busy_timeout_ms`` is the single busy knob: it is passed as ``connect(timeout=)`` AND set as the explicit PRAGMA" |
| — (없음) | **없는 것은 busy handler *아래*의 재시도 계층이다.** hermes는 1초 timeout을 일부러 쓰되 그 위에 지터 재시도를 얹는다. TalkAbout은 1초에서 죽은 뒤 그 오류를 stop으로 승격한다 | `hermes_state.py:479-501` |
| 1라운드: "journal 락 오류를 stop 승격" | **원인 서술이 부정확.** 오류를 stop으로 승격하는 코드는 `worker.py:391-400`이고, 20ms 폴링은 **승격 확률을 높이는 조건**이지 승격의 원인이 아니다. 둘은 인과 사슬이지 별개 결함이 아니다 | `worker.py:392`(`wait(0.02)`) → `:173`(timeout=1) → `:395-396`(승격) |

세 번째 정정이 중요하다: 1라운드는 두 결함을 병렬로 나열했지만, 실제로는 **"20ms마다 새 커넥션 → 경합 → 1s timeout → 예외 → stop 승격"** 단일 인과 사슬이다. 따라서 처방도 하나다(§2.6).

### 2.3 해법 1 — 오류 3분류 (`[채택]`, S)

hermes의 핵심 predicate는 **결과 코드 우선, 텍스트는 폴백**이다(`hermes_state_errors.py:43-56`):

```python
_SQLITE_LOCK_CODES = (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)

def _sqlite_primary_code(exc_or_str) -> int | None:
    code = getattr(exc_or_str, "sqlite_errorcode", None)
    return code & 0xFF if isinstance(code, int) else None

def is_sqlite_lock_error(exc_or_str) -> bool:
    """SQLITE_BUSY / SQLITE_LOCKED: wait and retry, never treat as damage. ...
    A known result code decides; only without one ... does the text."""
    code = _sqlite_primary_code(exc_or_str)
    if code is not None:
        return code in _SQLITE_LOCK_CODES
    text = str(exc_or_str).lower()
    return "locked" in text or "busy" in text
```

그리고 **3버킷**을 한 predicate로 묶어 retry와 HTTP 503/500 분기가 서로 어긋나지 않게 한다(`hermes_state_errors.py:65-70`):

```python
def is_transient_sqlite_error(exc: BaseException) -> bool:
    """"Busy right now", not "damaged": one predicate so retry and the HTTP
    503-vs-500 split cannot drift apart."""
    return isinstance(exc, sqlite3.OperationalError) and (
        is_sqlite_lock_error(exc) or any(marker in str(exc).lower() for marker in _TRANSIENT_SQLITE_MARKERS)
    )
```

`_TRANSIENT_SQLITE_MARKERS`에 `"disk i/o error"`가 포함된다(`:32-34`). 손상은 **의도적으로 제외**한다: "Corruption is deliberately absent: a malformed store must surface, not be retried into a timeout"(`:30-31`).

**TalkAbout 이식**: `talkabout/worker.py`에 `except sqlite3.Error: requested = True` 대신:

```python
except sqlite3.Error as exc:
    if is_transient(exc):        # BUSY/LOCKED/IOERR → 이번 폴링만 건너뛴다
        continue                 # stop 판정을 갱신하지 않는다 (이전 값을 유지)
    raise                        # 손상/불명은 조용히 승격하지 않고 드러낸다
```

가 최소 변경이다. 다만 §2.6이 더 근본적이다.

> **Orca 대비**: 1라운드는 같은 항목을 `[채택]` Tier1 #3으로 두고 Orca `sqlite-read-failure.ts:1`(3분류)를 근거로 들었다(`analysis/00-SYNTHESIS.md:87`). 나는 그 파일을 이번에 직접 읽지 않았다. 다만 hermes 쪽이 **분류 + 재시도 예산 + 소유자 보고 + 호출부 계약**을 코드로 함께 제시한다는 점에서 더 구체적이다. **최적 저장소: hermes.** (Orca 인용은 1라운드 보고서 경유이며 1차 확인 아님 — §11)

### 2.4 해법 2 — 시간예산 + 지터 재시도 (`[채택]`, S)

hermes는 "왜 1초 timeout인가"를 명시한다(`hermes_state.py:479-486`):

```python
# ── Write-contention tuning ──
# SQLite's deterministic busy handler convoys under many hermes processes: keep its
# timeout short (1s) and retry with random jitter. Patience is TIME-based (a sibling
# legitimately holds the lock for seconds: checkpoint at close, VACUUM, recovery, FTS
# optimize); attempt-counted budgets destroyed turns on a healthy store. Transcript
# writes (failure aborts the turn) get the long budget; observation-only activity
# writes sit on the response-critical path and get a sub-second one.
_WRITE_PATIENCE_S, _TRANSCRIPT_WRITE_PATIENCE_S, _ACTIVITY_WRITE_PATIENCE_S = 20.0, 60.0, 0.5
_WRITE_RETRY_MIN_S, _WRITE_RETRY_MAX_S = 0.020, 0.150  # fast jitter for the first _SLOW_AFTER_S
_WRITE_RETRY_SLOW_AFTER_S = 2.0
_WRITE_RETRY_SLOW_MIN_S, _WRITE_RETRY_SLOW_MAX_S = 0.250, 1.000
```

재시도 술어(`hermes_state.py:1450-1462`):

```python
def _sleep_before_write_retry(self, deadline: float, patience_s: float) -> bool:
    """Sleep one jitter interval if the budget allows; True = retry, False = deadline passed."""
    now = time.monotonic()
    if now >= deadline:
        return False
    slow = now - (deadline - patience_s) >= self._WRITE_RETRY_SLOW_AFTER_S
    jitter = random.uniform(*((self._WRITE_RETRY_SLOW_MIN_S, self._WRITE_RETRY_SLOW_MAX_S) if slow
                              else (self._WRITE_RETRY_MIN_S, self._WRITE_RETRY_MAX_S)))
    time.sleep(min(jitter, max(deadline - now, 0.001)))
    return True
```

세 가지가 이식 대상이다: (1) **예산은 시간 기준**(attempt-count 아님) — 근거가 docstring에 있다. (2) **jitter** — busy handler의 결정론적 convoy를 깬다. (3) **데드라인을 넘기지 않는다**(`min(jitter, ...)`).

TalkAbout `Journal`에 그대로 얹는 형태:

```python
_READ_PATIENCE_S = 2.0          # stop 폴링용: 짧게
def stop_requested(self):
    deadline = time.monotonic() + _READ_PATIENCE_S
    while True:
        try:
            with closing(sqlite3.connect(self.path, timeout=1)) as c, c:
                row = c.execute("SELECT v FROM settings WHERE k='stop'").fetchone()
                return bool(row and json.loads(row[0]))
        except sqlite3.Error as exc:
            if not is_transient(exc) or not _sleep_before_retry(deadline, _READ_PATIENCE_S):
                raise
```
20ms 폴링 주기(`worker.py:392`)에서 2초 예산이면 turn 취소 판정이 최대 2초 늦어진다. `worker.py:414`가 `cancelled`/`uncertain`을 나누듯, **"모른다"는 상태를 하나 더 두는 것이 정답**이다(§2.6).

### 2.5 해법 3 — 폴링 커넥션 제거 (`[채택]`, S)

hermes가 "살아있는 DB를 프로브하려고 열지 마라"를 강제하는 방식은 3겹이다.

**(a) 커넥션 레지스트리** — 이 프로세스가 연 DB를 추적해 raw open을 거부한다(`hermes_cli/sqlite_safe_read.py:1-7,147-178`): > "POSIX advisory locks are cancelled **process-wide** by `close()` on *any* fd for that file ... This module tracks live connections so raw reads happen only when none exist."

**(b) 프로브는 커넥션 없이** — 헤더는 캐시된 fd의 `pread`로 읽고, 살아있는 커넥션이 있으면 프로브를 아예 거부한다(`hermes_state_dbfile.py:63-70`, `:619-633` `_preopen_header` "unless *force*, a path this process already has a connection to").

**(c) 커넥션 수 상한 + 계층** — `readpool.py:35-56`:

```python
_READ_POOL_MAX = 8              # Ceiling on read-only connections ALIVE at once against one database FILE
_READ_POOL_PROCESS_MAX = 24     # Ceiling ALIVE in this PROCESS across every state.db
_HANDLES_PER_PATH_WARN = 4      # Warn past this many SessionDB handles on one file in one process
_FD_HEADROOM_RESERVE = 64       # Descriptors kept in reserve for everything that is NOT this module
```

그리고 "왜 3계층인가"가 주석에 있다(`:39-44`): per-file 상한만 두면 프로세스가 프로파일 수만큼 늘어 EMFILE이 된다 — "the same shape as the per-instance bug, one level out (#98573)".

**(d) 커넥션 수명 규율** — `hermes_cli/sqlite_util.py:4-6,70-79`: > "`with conn:` only commits — it never closes, so each call leaked a connection and its WAL/SHM fds until GC, exhausting `RLIMIT_NOFILE` on long-running gateways"

TalkAbout의 20ms 폴링은 정확히 이 목록의 **(b)·(c)** 위반이다: 살아있는 journal에 초당 50개 fd를 열고 닫는다. `test_journal_connection_lifecycle.py:68-80`이 이를 "정상"으로 고정하고 있으므로, 이식 시 테스트를 함께 고쳐야 한다.

**가장 싼 처방**(비용 S): 폴링 스레드가 **자기 전용 read-only 커넥션 1개를 재사용**한다.

```python
self._probe = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=1)
```

`mode=ro`는 hermes도 쓰는 형태다(`hermes_state_holders.py:27` `read_only_db_uri`, `hermes_state.py:765-773`). WAL이므로 리더는 라이터를 막지 않는다(`hermes_state.py:862-867`: "WAL readers never block on the writer").

### 2.6 해법 4 — 핵심: stop 신호를 SQLite에서 읽지 않기 (`[채택]`, S)

이것이 **가장 임팩트 큰 발견**이다. hermes가 같은 문제(크로스 프로세스 신호 전달)를 푸는 방식은 "신호를 DB에서 폴링"이 **아니다**. 그리고 그 이유가 코드 주석에 원칙으로 적혀 있다.

hermes가 "소유자 생존을 증명할 수 없을 때" 취하는 방향(`cron/executions.py:134-149`):

```python
def _owner_is_live(pid: int, started_at: Optional[int]) -> bool:
    try:
        from gateway.status import _pid_exists
        if not _pid_exists(pid):
            return False
    except Exception:
        return True  # fail safe: inability to prove death must not rewrite state
    if started_at is None:
        return pid == os.getpid()
    current = _process_start_time(pid)
    if current is None:
        return True  # cannot compare -> cannot prove death; a misread must not rewrite state
```

Windows 파일 점유 스캔도 같다(`hermes_state_holders.py:425-431`): > "A scan failure is represented as an unknown holder. Structural maintenance must not assume quiescence ..."

`hermes_state_reconcile.py:271-292`의 integrity 스캔도 예산 초과 시 **write를 하지 않는다**(fail closed = 쓰지 않음).

**TalkAbout과 정확히 반대다.**

| | 증거를 얻지 못했을 때 | 결과 |
|---|---|---|
| TalkAbout `worker.py:395-396` | `requested = True` → `cancel.set()` | **진행 중 turn 파괴** (`cancelled` 커밋) |
| hermes `executions.py:140,145` | `return True` (소유자 살아있다고 간주) → 상태 변경 없음 | 진행 중 작업 유지 |
| hermes `holders.py:428-431` | "unknown holder"로 간주 → 유지보수 거부 | 파괴적 작업 미실행 |
| hermes `reconcile.py:275` | 예산 초과 → 쓰지 않음 | 부분 마이그레이션 없음 |

즉 원칙은 "fail closed"가 아니라 **"불확실성은 파괴적 행동을 승인하지 않는다"** 이다. hermes에서 이 방향이 옳은 이유는 명시돼 있다: turn을 취소하면 되돌릴 수 없고, 취소를 2초 늦추면 다음 폴링이나 lease 만료가 회수한다. TalkAbout의 lease/deadline fence(`worker.py:360-364`)가 이미 "늦게 멈추면 회수된다"를 보장하므로, **stop 신호는 늦어도 안전하고 빨라서 위험하다**.

**따라서 최종 처방은 코드 3줄이다** — `worker.py:394-396`:

```python
except sqlite3.Error as exc:
    if is_transient(exc):
        continue          # 이번 주기는 판정하지 않는다. cancel/set 금지.
    raise                 # 손상은 조용히 삼키지 않는다
```

그리고 `step()`(`worker.py:238`)의 무방비 호출도 같은 정책으로 감싼다.

**왜 DB 폴링을 아예 없애지 않나**: TalkAbout은 CLI(`cli.py:160-164` `journal.set_setting("stop", True)`)가 **다른 프로세스에서** stop을 켜는 구조다. 크로스 프로세스 신호이므로 DB든 파일이든 공유 매체가 필요하다. hermes의 대응물은 `gateway/control_socket.py`(제어 소켓)지만 이는 상시 데몬 전제라 TalkAbout에 과하다. **현실적 최적해는 "신호 매체는 유지하되, 읽기 실패를 신호로 해석하지 않기"** 다.

### 2.7 결함 #1 최종 패치 스케치

| 단계 | 변경 | 파일:라인 | 비용 |
|---|---|---|---|
| 1 | `is_transient(exc)` predicate 추가 (결과코드 우선) | `talkabout/worker.py` 상단 | S |
| 2 | `stop_requested()`에 시간예산+jitter 재시도. 예외는 그대로 전파(기존 테스트 계약 유지) | `worker.py:172-175` | S |
| 3 | watcher: transient → `continue`, 손상 → `raise`(승격 금지) | `worker.py:391-400` | S |
| 4 | 폴링 커넥션을 read-only 1개 재사용으로 | `worker.py:172-179` | S |
| 5 | `step()`의 무방비 호출에 동일 정책 | `worker.py:238` | S |
| 6 | 회귀 테스트 3건: (a) transient가 `cancelled`를 만들지 않음, (b) 손상은 전파, (c) 폴링 커넥션 1개 | `tests/test_journal_connection_lifecycle.py:68-97` 갱신 | S |

총 **비용 S(1일 이내)**. 1라운드가 S로 잡은 것과 일치하되, hermes 근거로 처방이 "분류+재시도+판정방향" 3축으로 구체화됐다.

---

## 3. 상태 스키마 · 마이그레이션 · 복구 (Q2)

### 3.1 버전 관리는 "데이터 마이그레이션 전용", 컬럼은 선언적 (`[차용]`, M)

`hermes_state_schema.py:940-956`:

```python
def _init_schema(self):
    """Create tables and FTS if missing, reconcile columns, run data migrations. Column
    additions are declarative via _reconcile_columns(), so reordered migrations can
    never skip a column; schema_version remains for data migrations only."""
    report_startup_progress(600.0, phase="state_db_init_schema")
    cursor.executescript(SCHEMA_SQL)
    self._reconcile_columns(cursor)
```

핵심 아이디어: **스키마 정본(`SCHEMA_SQL`)을 실제로 실행해서 기대 컬럼을 도출**한다(`:725` "execute SCHEMA_SQL in an in-memory database and read"), 그 다음 라이브 테이블과 diff해서 누락 컬럼을 `ADD COLUMN` 한다(`:773-775`). 버전 번호 게이트에 기대지 않으므로 "순서가 뒤바뀐 마이그레이션"이 컬럼을 건너뛸 수 없다. TalkAbout `store.py`의 PostgreSQL 마이그레이션에 그대로 대응하는 개념은 아니지만(hermes는 SQLite, TalkAbout은 PG), **"정본에서 기대 상태를 계산해 diff"** 라는 원리는 이식 가능하다.

### 3.2 부분 실패 복구: 트랜잭션 내 postcondition (`[채택]`, S)

`hermes_state_reconcile.py:219-256` — `_apply_additive_schema`:

```python
"""... Missing SCHEMA_SQL tables and columns, in ONE ``BEGIN IMMEDIATE`` transaction.
The declared shape is verified inside the transaction before COMMIT: a column
the shared reconciler could not add (it logs and continues) or an expired budget rolls the
store back to exactly what it was, never a partial upgrade."""
conn.execute('BEGIN IMMEDIATE')
try:
    apply_declared_schema(cursor)
    for statement in schema_read_probe_statements():   # Postcondition, still inside the transaction
        ...                                            # every declared table/column resolves
    budget.check()
    conn.execute('COMMIT')
except BaseException as exc:
    with contextlib.suppress(sqlite3.Error):
        conn.execute('ROLLBACK')
```

**"reconciler가 실패하면 로그만 남기고 계속한다"** 는 전제를 인정하고, **커밋 직전에 postcondition을 검사**해 부분 업그레이드를 원천 차단한다. 그리고 **시간예산**(`_Budget`, `:190-216`)이 `set_progress_handler`로 SQLite에 주입돼 만료 시 `interrupted`로 롤백된다(`:205-211`).

TalkAbout 부착: `talkabout/store.py`의 스키마 초기화에 "DDL 후 검증 쿼리, 실패 시 전체 롤백" 패턴. 비용 M (PG라서 savepoint/statement_timeout 조합 검증 필요).

### 3.3 in-place 재작성 안전성: CAS digest (`[차용]`, M)

`hermes_state_prune_rewrite.py:133-160` — prune이 행을 **제자리에서** 고치는 commit: > "Each `(original, replacement)` ... Its row gets one archived twin of its pre-prune self ... Unchanged rows, model order, row ids and concurrently appended rows are untouched ..."

스테일 판정이 두 겹이다(`:151-157`): digest가 있으면 **stored-row CAS digest**(`DB_ROW_SNAPSHOT`)로 전체 컬럼 비교, 없으면 **representation-safe loader-lens 비교** — "raw storage bytes are never compared against a loaded/normalized view".

예외를 두 갈래로 나눈다(`hermes_state_errors.py:169-179`): `PruneRowUnresolvedError` — 행을 정확히 지목 불가 → **쓰기 전에** raise, 호출자가 전체 writer로 폴백. `PruneRowStaleError` — "다른 writer의 세대다" → 폴백 금지("that writer archives rows it never compared and would republish the stale held text over the newer one").

TalkAbout 대응: `talkabout/retention.py`. 다만 TalkAbout 마이그레이션 게이트는 이미 "LLM이 cursor·heartbeat·idempotency를 관리하지 않는다"는 원칙이 있어 `[대조]`에 가깝다. **비용 M, 우선순위 낮음.**

### 3.4 손상은 sticky 래치 (`[채택]`, S) — TalkAbout에 없는 것

`hermes_state_health.py:1-20`: > "A structurally corrupt state.db used to show up as an empty or partial Desktop sidebar, green readiness and a 500 from `/api/sessions`: every surface guessed on its own and none said "the store is damaged" (#72046). This module is the single place that fact is recorded." / > "The latch never clears on its own. A corrupt image does not heal, and a store that flickers between "ok" and "corrupt" is the silent failure this replaces. ... No marker is written to disk: the file it would describe is the one that is damaged."

API는 4개뿐이다(`:38-39,60-102`): `STORAGE_OK/STORAGE_CORRUPT`, `mark_storage_corrupt`, `storage_state`, `reset_storage_state`(테스트/검증된 복구 전용).

TalkAbout은 SQLite journal이 손상되면 어떤 일이 나는가? `Journal.__init__`(`worker.py:122-127`)이 `sqlite3.connect` + `executescript`을 그냥 실행한다. 손상 시 예외가 올라가 worker가 죽고, 그 다음 실행에서도 같은 파일로 다시 시도한다. **"store가 손상됐다"는 사실이 어디에도 latch되지 않는다.** → 신규 `talkabout/storage_health.py`(30줄 내외) + `api.py` 상태 엔드포인트 노출. **비용 S, 효과 큼.**

---

## 4. 락 소유권 증명 (Q3) — Windows가 여기서 갈린다

### 4.1 `lockguard` / `lockowners` / `holders`: 셋 다 POSIX 중심 `[제외]`

| 파일 | 하는 일 | Windows |
|---|---|---|
| `hermes_state_lockguard.py` (197줄) | WAL 세대를 지키는 두 POSIX 락을 **OFD 락**으로 복제해 stray `close()`가 취소 못 하게 | **`no-op`** — `:17` "No-op on Windows"; `:50-53` `if os.name == "nt": fcntl = None` |
| `hermes_state_lockowners.py` (124줄) | `/proc/locks`를 3회 union 파싱해 **누가 write 락을 잡았는지 pid로 지목** | `:86-87` `if not sys.platform.startswith("linux"): return []` |
| `hermes_state_dbfile.py` (858줄) | 삭제된 WAL 세대 holder 스캔, retired generation 캡처 | Windows는 대부분 빈 결과(`:367-368`) |

`lockguard`의 docstring이 이 트랙의 명제에 대한 답을 이미 준다(`:1-17`): 문제는 "락 오류"가 아니라 **한 프로세스 안의 stray open/close가 프로세스 전체의 POSIX 락을 취소하는 것**(sqlite.org/howtocorrupt §2.2)이다. **이 문제는 Windows에 존재하지 않는다.** 그러므로 TalkAbout의 결함에 `lockguard`는 해법이 아니다. `[제외]`.

`lockowners`의 **개념**은 이식 가능하다: "데이터베이스가 잠겨 있다"는 오류는 **피해자만 말하고 가해자를 말하지 않는다**(`:3-7`). 그리고 만료 시점에 holder를 로그로 남긴다(`:115-124`):

```python
def log_write_lock_holders(db_path, patience_s: float) -> None:
    """One WARNING naming the write-lock holders at the moment a writer gave up waiting."""
```

이건 `[차용]`이다 — TalkAbout도 journal 타임아웃 시 "누가 잡고 있었나"를 남기면 진단이 극적으로 쉬워진다.

### 4.2 Windows에서의 정답: Restart Manager (`[채택]`, S) — **임팩트 큰 발견**

`hermes_state_holders.py:369-422`는 Windows에서 **Restart Manager API**(`rstrtmgr.dll`)로 "이 파일을 누가 열고 있는가"를 열거한다:

```python
def _windows_restart_manager_holders(db_path: Path) -> list[tuple[int, str]]:
    """Return foreign processes using state.db or a WAL sidecar via Windows Restart Manager."""
    start, register, get_list, end = (
        api.RmStartSession, api.RmRegisterResources, api.RmGetList, api.RmEndSession)
    resources = [path for path in _sqlite_family(db_abspath) if os.path.exists(path)]
    for _ in range(4):                       # ERROR_MORE_DATA 재시도 (프로세스 집합이 변한다)
        if rc == 0:
            return [(int(apps[i].process.pid), db_abspath) ... if pid != os.getpid()]
```

패키지 의존성은 `ctypes`뿐이고, `_sqlite_family`는 main + `-wal` + `-shm` 3파일을 한 번에 등록한다(`:364-366`).

**TalkAbout에 왜 결정적인가**: `docs/STATUS.md:44-52`의 남은 관문에 "POSIX 실행 차단"과 운영 배포가 있다. TalkAbout은 **Windows Job Object만** 지원하고(`processes.py`), journal/PG 파일을 누가 잡고 있는지 알 방법이 없다. Restart Manager는 **추가 의존성 0**으로 Windows에서 "누가 이 파일을 잡고 있나"를 답한다. 부착 위치: `talkabout/processes.py`(이미 프로세스 계층이 있는 유일한 파일). **비용 S.**

주의: hermes의 `foreign_state_db_holders`는 스캔 실패를 **"unknown holder"로 간주**한다(`:425-431`). 같은 원칙(§2.6)을 TalkAbout에도 적용해야 한다 — 스캔 실패를 "아무도 안 잡고 있다"로 읽으면 안 된다.

### 4.3 좀비 락 해소: `(pid, start_time)` 지문 (`[차용]`, M)

hermes는 PID 존재만으로 생존을 판정하지 않는다. `cron/AGENTS.md`가 이를 불변식으로 못박는다: > "Worker liveness is `(worker_pid, worker_started_at)` — the start-time fingerprint ... recorded at claim time — never bare PID existence, or a recycled PID gets killed on reclaim."

실코드(`cron/executions.py:141-149`)는 지문이 **일치하지 않아도 죽었다고 단정하지 않는다**: "Drifted same-host readings (#117505) are not proof of death; a live misread is still bounded by the stale-claim sweep below."

그리고 live owner의 stale 상한은 **상수 하나가 아니라 기존 노브에서 유도**한다(`:152-170`): > "... Derived from the existing knobs, never a bare wall-clock constant: `max(3 × HERMES_CRON_TIMEOUT, cron script timeout, 7200)`. Returns `None` (never reclaim live owners — today's behaviour) when the inactivity timeout is 0/unlimited ..."

TalkAbout 대응: `worker.py` lease 갱신 + `domain.py:117-125`의 attempt 예산. 현재 lease 만료만으로 회수하므로 **PID 재사용/좀비 문제는 아직 없지만**, 향후 프로세스 단위 소유권을 도입하면 이 패턴이 필요하다. **비용 M, 보류 가능.**

---

## 5. 관측 — `logging` import 0건에 대한 이식안 (Q4)

TalkAbout 결함 6건 중 "관측 계층 부재"(`00-SYNTHESIS.md:§7`)의 직접 해법이 여기 있다.

### 5.1 구조 (`[채택]`, M)

`hermes_logging.py:1-7` (모듈 docstring이 곧 설계 명세다): > "Log files: agent.log (INFO+, everything), errors.log (WARNING+), gateway.log ..., gui.log ... All are rotating files driven through **one async queue** and formatted with **`RedactingFormatter`** so secrets never reach disk."

5개 요소:

| 요소 | 코드 | TalkAbout 적용 |
|---|---|---|
| **파일별 레벨 분리** | `:1-7` (agent/errors/gateway/gui) | `journal.log`(INFO+) + `errors.log`(WARNING+) 2개면 충분 |
| **단일 async 큐** | `:803-858` `_NonFormattingQueueHandler` + `QueueListener` — "Emitting threads never block on file I/O or the rotation lock" | worker/runtime 스레드가 로그 I/O로 멈추면 안 됨. **필수** |
| **마스킹 포매터** | `:390` `RedactingFormatter` (from `agent.redact`) | TalkAbout은 `agent_runtime.py:35-97` stderr 허용목록이 이미 강함 → 로그 sink에도 같은 규율 필요 |
| **세션 상관 태그** | `:253-291` — thread-local session id + **LogRecord factory**로 `%(session_tag)s` 주입 | `worker.py`의 turn에 `task_id`를 태그로. turn 상관 ID는 1라운드 Tier2 #5와 동일 항목 |
| **crash flush** | `:861-887` `flush_log_queue()` / `drain_log_queue(timeout)` | 종료 경로에서 상한 있는 drain |

stderr 프록시까지 마스킹한다는 점이 눈에 띈다(`:166-187`, `class _RedactingStdio`: "Text stream proxy that redacts each write: a detached gateway's stdout/stderr IS a ..."). 즉 **파일 sink뿐 아니라 스트림 sink도 같은 choke point를 통과**한다.

**상관 태그 구현이 특히 영리하다**(`:263-288`):

```python
def _install_session_record_factory() -> None:
    """Replace the global LogRecord factory with one that adds ``session_tag``. Unlike a Filter, the
    record factory runs for EVERY record in the process (propagated and third-party-handled ones
    included), so ``%(session_tag)s`` never KeyErrors."""
```

`Filter`가 아니라 **factory**를 교체해 서드파티 로거 레코드까지 태그가 붙고 포맷 문자열이 KeyError를 내지 않는다. TalkAbout이 `logging`을 도입할 때 그대로 베낄 가치가 있는 10줄이다.

### 5.2 Windows 로그 로테이션 함정 (`[채택]`, S) — **TalkAbout이 곧 밟을 지뢰**

`hermes_logging.py:75-101`: > "Windows-ONLY swap (#44873): stdlib `RotatingFileHandler.doRollover()` calls `os.rename()`, which fails with `PermissionError [WinError 32]` whenever **another process holds an append handle** on `agent.log` — essentially always in Hermes (TUI, gateway, hy_memory, MCP servers, CLI commands all log) — pinning the file at the size threshold and spamming stderr on every emit. `concurrent-log-handler` serializes rollover with a cross-process lock."

TalkAbout은 **worker + API 서버 + CLI가 같은 journal 디렉터리에 로그를 쓰게 될 구조**다. 즉 WinError 32 조건이 그대로 성립한다. 처방은 둘 중 하나: (1) `concurrent-log-handler` 채택(포털ocker 필요, `:38-72`가 그 probe까지 구현 — 실패 시 stdlib 폴백), (2) **프로세스별 파일 분리**(`worker-<pid>.log`) — TalkAbout 규모에서는 이게 더 싸다.

`_portalocker_probe()`(`:38-72`)는 "번들 환경에서 portalocker가 깨졌을 때 조용히 모든 레코드를 잃는" 사고를 막기 위해 **스크래치 파일로 실제 락을 잡아보는** probe다. 벤더링 환경의 좋은 선례.

---

## 6. 감독 (Q5) — `runtime.py:51-59` 무제한 shutdown의 직접 해법 (`[채택]`, S)

TalkAbout 결함 #2(`00-SYNTHESIS.md:159`): `runtime.py:51-59`가 `shutdown()`을 상한 없이 호출한다. hermes의 정답은 `hermes_startup_watchdog.py`의 **exit escort**다(`:264-345`). `_fire`의 docstring이 원칙을 선언한다:

> "Forensics, then exit — with the exit itself independently bounded. Everything producing forensics (logging, dump record, faulthandler, ledger) can block: the wedged main thread may hold the logging handler lock, the disk may be hung. **None of that may stop the respawn, so the escort thread starts FIRST.** ``os._exit`` is async-signal-safe and lock-free by design."

```python
def _exit_escort(self) -> None:
    """Hard-exit if the forensic fire path wedges (bounded-exit seam).
    Deliberately free of log handlers, filesystem access, module loads and any
    lock shared with application code: only a sleep, an Event check and the exit seam."""
    self._sleep(_FIRE_EXIT_BOUND_S)
    if self._fire_done.is_set():
        return
    self._exit(self.exit_code)
```

`_FIRE_EXIT_BOUND_S = 10.0`(`:84`), `_LEDGER_JOIN_TIMEOUT_S = 5.0`(`:78`).

**핵심 통찰 2개**: (1) **종료를 책임지는 감시자는 종료 대상과 다른 코드 경로에 있어야 한다.** 상한이 필요한 순간은 바로 "상한을 구현하는 코드가 멈춘" 순간이므로, 상한 구현을 같은 스레드/락에 두면 무의미하다. (2) **정리(forensics)와 종료(exit)를 분리하고, 정리 실패가 종료를 막지 못하게** 한다.

TalkAbout 이식(`runtime.py:51-59`):

```python
def stop():
    cancel.set()
    shutdown = getattr(runtime, 'shutdown', None)
    if callable(shutdown):
        done = threading.Event()                    # 상한 있는 실행: shutdown이 멈춰도 stop()이 돌아온다
        threading.Thread(target=lambda: (shutdown(), done.set()), daemon=True).start()
        if not done.wait(_SHUTDOWN_BOUND_S):        # 예: 5초
            forced.append(True)                     # 강제 종료 경로(Job Object)로 승격
```

`processes.py`에 Windows Job Object가 이미 있으므로, 상한 초과 시 Job Object 강제 종료로 승격하는 것이 자연스럽다. **비용 S.** (1라운드는 Orca `daemon-shutdown-deadline.ts:3`을 근거로 들었으나, 상한 구조를 코드로 보여주는 쪽은 hermes다. **최적: hermes.**)

### 6.1 progress lease + CPU fallback (`[차용]`, M)

`hermes_startup_watchdog.py:10-18`: > "Order of authority: (1) phase-owned progress leases (`report_startup_progress`) — authoritative, prove the *startup path itself* is alive, work for I/O-bound phases ... with ~zero CPU; (2) process-wide CPU progress, capped at `_MAX_CPU_EXTENSIONS` since an unrelated thread burning CPU must not hide a parked startup thread forever."

상한 3중(`:64-84`): `_CPU_PROGRESS_MIN_S=1.0`, `_MAX_CPU_EXTENSIONS=3`("anything longer must hold an explicit phase lease"), `_MAX_LEASE_S=900.0`, `_FIRE_EXIT_BOUND_S=10.0`. 그리고 **워치독이 애플리케이션을 방해하면 안 된다**는 fail-open 원칙(`:26`): "Everything is best-effort: a watchdog failure must never affect the startup it observes." 모든 공개 함수가 `never raises`(`:429,457,486,501`).

TalkAbout은 worker 루프 자체가 감독 대상이다(장시간 turn). `_WRITE_PATIENCE_S`처럼 **"정상적으로 오래 걸리는 작업"과 "멈춘 작업"을 구분하는 명시적 lease**가 없으면 stall 감지는 오탐만 만든다. **비용 M, 2단계.**

### 6.2 루프 가드 (`[채택]`, S)

`gateway/bot_loop_guard.py:1-6`: > "``{PLATFORM}_ALLOW_BOTS`` only decides admission, so two Hermes profiles replying to each other never stop. The guard counts admitted bot messages per conversation and drops further ones for ``cooldown_seconds`` once ``max_events`` land inside ``window_seconds``."

기본값 `max_events=20, window=300s, cooldown=600s`(`:22-27`). 메모리 상한은 window당 1회 sweep(`:132-141`).

TalkAbout 대응이 **정확히 있다**: "합성 runtime이 실제 공급자로 둔갑"하는 문제, 그리고 S2(2-runtime 대화) 관문. 두 agent가 서로에게 무한히 응답하는 것은 TalkAbout에서 실제 위험이다(round-robin `speak` task). `worker.py`의 turn 예산(`domain.py:117-125`)이 attempt는 제한하지만 **대화 수준 루프**는 제한하지 않는다. **부착: `talkabout/participation.py` 또는 `modes.py`. 비용 S.** 이건 1라운드에 없던 항목이다.

---

## 7. 권한/ACL (Q6)

### 7.1 `authz_mixin`의 판정 사다리 (`[대조]` 우위)

`gateway/authz_mixin.py:631-644` — 판정 순서가 docstring에 **열거**돼 있다:

```python
def _is_user_authorized(self, source, *, allow_adapter_delegation: bool = True) -> bool:
    """Whether a user may use the bot.

    Order: trusted-upstream delegation, chat-scoped group allowlists, ``{PLATFORM}_ALLOW_BOTS``,
    per-platform allow-all, adapter role auth, pairing store, env/config allowlists,
    ``GATEWAY_ALLOW_ALL_USERS``, default deny. A bot-authored message that any of these admits
    is still refused while its chat's loop guard is cooling down.
    """
```

실제 코드도 같은 순서로 `return True`를 나열하고 마지막이 `return _env_truthy("GATEWAY_ALLOW_ALL_USERS")`(`:694`) → `return "*" in allowed_ids or _principal_matches_allowlist(...)`(`:714`)다. **최종 default deny.**

TalkAbout은 이미 `docs/PERMISSIONS.md` 카탈로그 + `계정 scope ∩ membership ∩ credential scope`(`BRIEF-R2.md:40`)로 더 정교하다. `[대조]`. 다만 hermes에서 가져올 **구조적 장치 2개**가 있다:

**(a) 판정(peek)과 소비(admit)의 분리** (`[채택]`, S) — `:617-619`:

```python
def _admit_bot_message(self, source: SessionSource) -> bool:
    """Count one authorized bot-authored inbound message. ...
    The inbound handler calls this once per message; ``_is_user_authorized`` only peeks because it is asked several times."""
```

TalkAbout 결함 3(`participation.py:73-76`, "ack이 배달 증거 아님")과 같은 계열의 문제다. 권한 판정이 여러 번 호출되는 경로에서 **부작용(쿼터 소모·ack 기록)은 별도 함수로 1회만** 일어나야 한다. `talkabout/participation.py`에 해당 분리가 있는지 나는 확인하지 못했다 — **미확인**.

**(b) 판정 결과를 최종 단계에서 다시 거르기** (`:639-644`) — allowlist 통과 후 loop guard가 최종 거부. TalkAbout의 `credential scope`/`membership` 다층과 같은 사상. `[대조]`.

### 7.2 `hermes_dir_acl` — Windows DACL 함정 (`[채택]`, S) — **TalkAbout이 밟을 수 있는 지뢰**

`hermes_dir_acl.py:1-9`: > "Since CPython 3.13, ``os.mkdir(path, 0o700)`` on Windows applies a **protected DACL** of exactly SYSTEM, Administrators and OWNER RIGHTS. An elevated process's default owner is ``BUILTIN\Administrators``, so OWNER RIGHTS resolves to Administrators too, and the user's own non-elevated token (where Administrators is deny-only) gets **``WinError 5``** on the directory and on everything created inside it (#139845, #139485 ...). A plain ``mkdir`` inherits its parent's ACL instead, which under ``%LOCALAPPDATA%`` names the user, so the **``mode=0o700`` sites are the ones that strand the user.**"

**TalkAbout에 정확히 이 코드가 있다**: `talkabout/text_files.py:42` — `if create:self.root.mkdir(parents=True,exist_ok=True,mode=0o700)`, `talkabout/agent_runtime.py:588` — `git.mkdir(mode=0o700)`.

즉 관리자 권한으로 설치/실행한 뒤 일반 사용자로 실행하면 **`text_files` 루트와 git 디렉터리에 WinError 5**가 난다. `tests`가 `tmp_path`(일반 사용자)에서 돌기 때문에 이 결함은 테스트로 잡히지 않는다. 처방: `mkdir(mode=0o700)` 제거(Windows에서는 ACL 상속이 더 안전) 또는 hermes식 승격 감지 후 명시적 ACE 부여. hermes는 상승 여부를 **한 곳에서만** 판정한다(`:75-79` `process_elevation`, "The ONE elevation probe"). **부착: `talkabout/text_files.py:42`, `agent_runtime.py:588`. 비용 S.** TalkAbout의 "POSIX 실행 차단" 관문과 직접 연결된다.

추가로 hermes는 승격 grant가 **reparse point를 절대 따라가지 않는다**(`:19-27`): > "an elevated grant never goes through a junction, symlink or other reparse point ... same-user medium-integrity code can swap any entry under the home for a junction to e.g. ``C:\Program Files\X``, and a grant that followed it would hand the user full control of the target across the UAC boundary."

Windows 권한 상승 경계를 다루는 프로젝트라면 이 위협모델은 필수 지식이다.

---

## 8. cron 큐 · incidents (Q7)

### 8.1 at-most-once + tombstone (`[차용]`, M)

`cron/delivery_queue.py:1-8` — 모듈 docstring이 정책 선언이다: > "A gateway claims each row at most once. If that gateway dies after claiming, the outcome is marked **unknown and never retried: losing a delivery is safer than duplicating a possibly-completed send.**"

TalkAbout은 이미 이 원칙을 갖고 있다(`worker.py:262-264` `call_started → uncertain`, 자동 재시도 금지). `[대조]`. 다만 **2개 메커니즘**이 더 있다.

**(a) terminal prune 후에도 idempotency 유지 = tombstone 테이블** (`:39-74`):

```python
def _prune_terminal_unlocked(conn):
    """Redact terminal payloads and retain only bounded outcome metadata."""
    conn.execute("""UPDATE deliveries SET job_json='{}', content=''
       WHERE status IN ('delivered','failed','unknown','suppressed') AND (... != '')""")
    conn.execute("""INSERT OR IGNORE INTO delivery_tombstones
       (execution_id, terminal_status, finished_at)
       SELECT execution_id, status, finished_at FROM deliveries WHERE ...
       ORDER BY julianday(finished_at), ... LIMIT ?""", (excess,))
```

terminal 행을 **bounded retention**(`MAX_TERMINAL_DELIVERIES = 1000`, `:35`)으로 지우면서, 지운 것의 `(execution_id, terminal_status)`를 tombstone에 남겨 **재전송 producer가 "이미 끝났다"는 답을 계속 받는다**(`:177-187` enqueue가 tombstone을 먼저 조회). TalkAbout journal은 terminal 행을 지우면 idempotency 답이 사라진다. `[차용]`, `worker.py` journal retention 설계에 직결. **비용 M.**

**(b) terminal payload 즉시 redaction** (`:41-45`) — payload를 비우고 outcome 메타데이터만 남긴다. hermes는 `_finish`에서도 에러를 마스킹한다(`:259-263` `redact_sensitive_text(..., force=True, redact_url_credentials=True)`). TalkAbout 결함 6(관측 부재)와 함께 가야 할 항목: **로그/저장소에 남기는 에러는 반드시 마스킹**.

**(c) 소유자 fence** (`:264-279`) — `_finish`가 `WHERE execution_id=? AND status='delivering' AND owner_process_id=? AND owner_pid=?`로 갱신한다. 즉 **lease가 아니라 프로세스 정체성으로 fence**한다. TalkAbout은 lease+attempt fence를 쓴다. `[대조]`(TalkAbout이 오히려 유연). 다만 `recover_abandoned`(`:282-325`)가 "같은 프로세스인데 active 집합에 없으면 → unknown"으로 판정하는 이중 조건은 참고할 만하다.

**(d) pending vs delivering 구분** (`:359-400`) — `_terminalize_wait_timeout`: > "A row still ``pending`` was provably never attempted, so it is left queued ... That is a deferral, not a failure ... Only a row caught mid-send is uncertain and gets fenced ``unknown``."

TalkAbout `worker.py:145`의 `safe_pre_call_retry` / `runtime_started` 구분과 **동일 사상**. `[대조]`.

### 8.2 incidents — 오류 시그니처 dedup (`[채택]`, M) — **TalkAbout에 완전히 없는 것**

`cron/incidents.py:1-11`: > "groups the *failures* into incidents keyed by ``(job_id, error signature)`` so the same job failing with the same error does not re-ping the operator every run once acknowledged. Lifecycle: ``detected`` → ``alerted`` → ``closed``."

시그니처 정규화가 실용적이다(`:114-138`). 매번 달라지는 **소요시간만** 지우는 `_DURATION_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|s|secs?|...|h|hrs?|hours?)\b")`를 적용한다:

```python
def _error_signature(job_id: str, error: str) -> str:
    normalized = _DURATION_RE.sub("#s", _normalize_error(error))[:_MAX_SIGNATURE_ERROR_CHARS]
    return hashlib.sha256(job_id.encode() + normalized.encode()).hexdigest()[:12]
```

> "Measured durations ("idle for 603s", "retry in 12.5 seconds") differ run to run for the same failure, so they are masked out of the signature. Status codes and other numbers still count."

즉 **매번 달라지는 숫자(소요시간)만 마스킹**하고 상태코드(429/401)는 보존한다 → 같은 실패가 같은 incident로 모인다. `resolved`(자동) vs `closed`(운영자 ack)를 구분해(`:242-257`), "몇 주째 초록인 job에 open incident 32개" 사고를 막는다.

TalkAbout 결함 3(ack이 배달 증거 아님) + 결함 6(관측 부재)의 **결합된 해법**이 여기 있다. `worker.py`의 turn 실패를 incident로 승격하면 "운영자가 같은 실패 알림을 무한히 받는" 문제가 사라진다. **부착: 신규 `talkabout/incidents.py`(250줄 이내) + `worker.py` 실패 경로. 비용 M.**

### 8.3 실행 원장은 재시도 큐가 아니다 (`[대조]`)

`cron/executions.py:1-6`: > "The ledger records what is known about each attempt; **it is not a retry queue.** Interrupted attempts become ``unknown`` only after their owner process is proved gone — a start-time reading that fails to match the claim-time fingerprint is not proof of death. **Terminal states are immutable.**"

TalkAbout `docs/ARCHITECTURE.md`의 "Event가 진실 원천" + `call_started→uncertain` 금지가 같은 철학이다. `[대조]`. "terminal 상태 불변"은 TalkAbout `journal.save`에 명시적으로 있는지 **미확인**.

---

## 9. TalkAbout 결함 6건 재판정 (Q8)

`analysis/00-SYNTHESIS.md:§7`의 6건에 대해 hermes가 **직접 코드 해법**을 주는지 판정.

| # | 결함 | hermes 직접 해법 | 근거 | 판정 |
|---|---|---|---|---|
| 1 | journal 락 오류 → stop 승격 (`worker.py:391-400`, `:172-175`) | **있다. 3중** — (a) 오류 3분류 (b) 시간예산+jitter 재시도 (c) 판정 방향 역전 | `hermes_state_errors.py:43-70`, `hermes_state.py:1450-1462`, `cron/executions.py:134-149`, `hermes_state_holders.py:425-431` | **`[채택]` 최우선** |
| 2 | 무제한 `shutdown()` (`runtime.py:51-59`) | **있다** — bounded exit escort (상한 구현을 종료 대상과 분리) | `hermes_startup_watchdog.py:264-345`, `_FIRE_EXIT_BOUND_S=10.0`(`:84`) | **`[채택]`** |
| 3 | ack이 배달 증거 아님 (`participation.py:73-76`) | 부분적 — "판정(peek) vs 소비(admit)" 분리 | `gateway/authz_mixin.py:617-619` | `[차용]` |
| 4 | reduced-motion CSS 오염 (`web/style.css:1692-1709`) | **없다** (UI 계층 무관) | — | 해당 없음 |
| 5 | 실행 전 실패가 attempt 소모 (`domain.py:117-125`) | **있다** — `pending`(증명된 미시도) vs `delivering`(불확실) 구분 | `cron/delivery_queue.py:359-400` | `[차용]` |
| 6 | 관측 계층 부재 (`logging` import 0건) | **있다. 가장 완성도 높음** — async 큐 + 마스킹 + 상관 태그 + crash flush | `hermes_logging.py:1-7,253-291,803-887` | **`[채택]`** |

**신규 발견 3건** (1라운드 목록에 없음. 이번 트랙에서 직접 확인):

| # | 신규 결함 | 근거 | 비용 |
|---|---|---|---|
| N1 | `stop_requested()`가 `step()`(`worker.py:238`)에서 **예외 처리 없이** 호출됨 → transient 오류가 worker 루프를 죽인다(watcher에서는 turn 취소로, 여기서는 루프 사망으로, 같은 원인이 다른 결과) | `worker.py:238` vs `:391-400` | S |
| N2 | 관리자 권한 실행 시 `mode=0o700`이 Windows protected DACL을 만들어 **일반 사용자에게 WinError 5** | `text_files.py:42`, `agent_runtime.py:588` + `hermes_dir_acl.py:1-9` | S |
| N3 | 손상 journal에 대한 **sticky 손상 표시 없음** — 손상 시 매 실행마다 같은 파일로 재시도, API는 계속 "정상"으로 응답 | `worker.py:122-127` + `hermes_state_health.py:1-20` | S |

---

## 10. 라이선스

`hermes-agent/LICENSE` 직접 확인:

```
MIT License
Copyright (c) 2025 Nous Research
```

(17줄, 표준 MIT. HEAD `930e5ed5`, 2026-10-25 09:39:38 -0400 — 브리프 `930e5ed`와 일치) → 외부 코드 복사 시 **저작권 고지 + MIT 라이선스 전문 포함** 필요(브리프 §4.7). 본 보고서의 이식 제안은 대부분 **개념·상수·구조**이며, 코드를 그대로 옮기는 항목은 `_sleep_before_write_retry`(약 13줄), `is_sqlite_lock_error`(약 8줄), `_windows_restart_manager_holders`(약 50줄) 정도다. 복사 시 파일 상단에 출처 주석을 남길 것.

---

## 11. 미확인 · 한계

정직하게 남긴다. 아래는 **근거로 쓰지 않았다**.

1. **`hermes_state*.py` 60개 중 48개를 docstring/파일명으로만 판정.** 특히 `maintenance`, `repair`(1009줄), `schema`(1317줄), `gateway`(961줄), `portability`, `registry`, `identity`의 내부는 읽지 않았다.
2. **`hermes_state_wal.py`는 254-340(`apply_wal_with_fallback`)만 읽었다.** 579줄 중 절반 이상 미확인. "never downgrade to DELETE if the on-disk header reports WAL"(`:265-267`)라는 불변식만 확인했다.
3. **`gateway/authz_mixin.py`(668줄) 중 600-714(판정 경로)만 읽었다.** 브리프 지시대로 인가 판정 경로로 한정. `gateway/config*.py`(45KB)는 **읽지 않았다**.
4. **`cron/scheduler.py`(3911줄), `jobs.py`(3014줄), `scheduler_delivery.py`(1724줄)는 읽지 않았다.** `cron/AGENTS.md`의 불변식 서술로만 참조했다(그 문서는 개발 가이드이므로 1차 코드 근거로 쓰지 않았다).
5. **`tui_gateway/checkpoints.py`, `change_watcher.py`, `compute_host.py`: 존재만 확인, 내용 미확인.**
6. **Orca `sqlite-read-failure.ts`를 직접 읽지 않았다.** §2.3의 Orca 비교는 `analysis/00-SYNTHESIS.md:87` 인용이다.
7. **TalkAbout 쪽 미확인**: `participation.py:73-76`의 실제 코드를 이번에 직접 읽지 않았다(1라운드 인용). `store.py`의 마이그레이션 구조, `web/style.css` 결함 4는 이 트랙 범위 밖이라 확인하지 않았다.
8. **`hermes_state_lockguard.py`의 Windows no-op 판정은 문서 주석(`:17`)과 플랫폼 분기(`:50-53`)로 확인했다.** 실제 Windows 런타임에서 import가 어떻게 동작하는지 실행 검증은 하지 않았다.
9. **`hermes_state_reconcile.py`의 1-180, 320-514 및 `hermes_state_holders.py`의 1-360은 읽지 않았다.**

---

## 12. 적용 순서 제안

`docs/STATUS.md:44-52`의 남은 관문과 겹치는 순서로 배치했다.

| 순서 | 작업 | 근거 | 비용 | 왜 이 순서인가 |
|---|---|---|---|---|
| D1 | **결함 #1 수리** (3분류 + 재시도 + 판정방향 + 폴링 커넥션) + 회귀 3건 | §2.3~2.7 | S | turn 오취소는 정확성 결함. S2(실제 2-runtime 대화) 검증 전에 고쳐야 S2 실패와 구분된다 |
| D1 | **결함 #2 수리** (bounded exit escort) | §6 | S | 위와 같은 파일군(`worker.py`/`runtime.py`), 같은 날 가능 |
| D2 | **Windows DACL 함정** (`mode=0o700` 2곳) | §7.2 | S | 운영 배포 관문 직결. 테스트로 안 잡히는 종류 |
| D2 | **관측 계층 v1** (async 큐 + 마스킹 + turn 상관 태그) | §5.1 | M | 이후 모든 디버깅 비용을 낮춘다. 먼저 넣을수록 이득 |
| D3 | **incidents** (오류 시그니처 dedup + ack) | §8.2 | M | 관측 v1 위에 얹힌다. 결함 3·6 결합 해법 |
| D3 | **storage health latch** | §3.4 | S | 손상 시 조용한 오작동을 막는다 |
| D4 | **Windows Restart Manager holder probe** | §4.2 | S | 운영 배포·진단. "누가 잡고 있나"를 답할 수 있게 |
| D5 | **tombstone idempotency + (pid, start_time) 소유 증명** | §8.1, §4.3 | M | journal retention 도입 시점에 함께 |

**한 문장 요약**: TalkAbout 결함 #1의 해법은 화려한 락 라이브러리가 아니라 **"일시 오류를 재시도하고, 증명 실패를 신호로 해석하지 않는다"** 는 40줄이며, 그 원칙이 hermes의 `hermes_state_errors.py`·`hermes_state.py:1450-1462`·`cron/executions.py:134-149`에 각각 코드로 박혀 있다.
