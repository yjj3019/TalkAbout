#!/usr/bin/env python
"""TalkAbout 성능 스냅샷 - 재실행 가능한 계측 도구.

서버 기동·외부 네트워크·실제 LLM 호출 없음. PostgreSQL 없이 MemoryStore 계약 스토어로만 측정한다.
제품 코드·기존 테스트는 건드리지 않고, 기존 fixture 헬퍼(`tests/conftest.py:build_fixture`)를 그대로 재사용한다.

측정 항목
  1) web_assets : talkabout/web/ 파일별 원본 바이트 / gzip9 바이트 / 총합
  2) http       : TestClient 인프로세스 wall-clock 중앙값(N회) + 응답 바이트
                  (/health, GET rooms, GET room, GET room events, POST message 1건)
  3) domain     : 4방/40메시지 fixture에서 core.rooms() / core.events() 1회 CPU 시간 중앙값
  4) query_count: store 메서드를 monkeypatch로 감싸 연산 1회당 "DB 왕복 호출 수"(Postgres connect 상당)와
                  snapshot 호출 수를 센다. MemoryStore에는 DB가 없으므로 connect 대신 store 메서드 호출을 세고,
                  그 사실을 출력·JSON에 명시한다.
  5) sse_idle   : 유휴 SSE 1회 반복(core.events(wait_seconds=0))에서 store.snapshot 호출 수(현재 2회 예상)

실행
  .\\.venv\\Scripts\\python.exe -B tools\\perf_snapshot.py
  .\\.venv\\Scripts\\python.exe -B tools\\perf_snapshot.py --json
  .\\.venv\\Scripts\\python.exe -B tools\\perf_snapshot.py --baseline perf-baseline.json --json
  .\\.venv\\Scripts\\python.exe -B tools\\perf_snapshot.py --out perf-baseline.json

주의: 이 도구의 ms 값은 절대 성능 지표가 아니다. 같은 코드에서도 실행 간 1.49배 변동이 관측된 환경이므로
(analysis/r3-talkabout-baseline.md §1.2, r3-00-SYNTHESIS §5.2), 회귀 판정은 같은 머신의 **상대 비교** 또는
query_count/sse_idle/byte 같은 **결정적 카운터**로만 한다. `--baseline`이 그 상대 비교를 제공한다.
"""

from __future__ import annotations

import argparse
import functools
import gzip
import json
import platform
import statistics
import sys
import time
import unicodedata
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

# tests/conftest.py가 fastapi.testclient를 import할 때 나오는 deprecation 경고는 측정과 무관하다.
warnings.filterwarnings("ignore", message="Using .*starlette\\.testclient.*")

TOOL = "tools/perf_snapshot.py"
SCHEMA_VERSION = 1
TOKEN = "fixture-human"
FIXTURE_ROOMS = 4
FIXTURE_MESSAGES = 40
POST_MESSAGE_CHARS = 100
DEFAULT_REPEATS = 21
DEFAULT_WARMUP = 3

# PostgresStore에서 self.connect()를 여는 공개 store 메서드들 = DB 왕복 1회에 대응.
# MemoryStore는 같은 이름의 메서드를 가지므로 이름 기준으로 계측할 수 있다.
# room_events는 P1-2(이벤트 델타 조회) 이후 추가된 조회 메서드라 함께 센다(없으면 자동 skip).
CONNECTION_METHODS = (
    "setup_required", "auth", "snapshot", "room_events", "cursor", "save_cursor", "listener",
    "credential_active", "transaction", "read_transaction", "create",
    "tick_all", "provision", "revoke", "logout",
)

HTTP_NOTE = (
    "인프로세스 TestClient 값이다. MemoryStore 기준이므로 DB 왕복이 0이고, 여기에 DB 접속·"
    "쿼리 시간이 더해진 값이 실제 프로덕션 지연이다. /health는 인증·DB 접근이 없는 하네스 바닥값이다."
)


# --------------------------------------------------------------------------- formatting helpers
def configure_output():
    """파이프/CI로 리다이렉트된 출력만 UTF-8로 맞춘다(콘솔 TTY는 OS 기본 인코딩 유지).

    Windows 콘솔은 cp949로 리다이렉트되면 한글 표/JSON이 깨지거나 UnicodeEncodeError가 난다.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            if not stream.isatty():
                reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError, AttributeError):
            pass


def _dwidth(value):
    """East Asian wide 문자를 2칸으로 세는 표시 폭(표 정렬용)."""
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in str(value))


def render_table(headers, rows, aligns=None, indent="  "):
    aligns = aligns or ["l"] * len(headers)
    cells = [[str(cell) for cell in row] for row in rows]
    widths = [max([_dwidth(header)] + [_dwidth(row[i]) for row in cells]) for i, header in enumerate(headers)]

    def line(values):
        parts = []
        for i, value in enumerate(values):
            pad = " " * (widths[i] - _dwidth(value))
            parts.append(pad + value if aligns[i] == "r" else value + pad)
        return indent + "  ".join(parts).rstrip()

    output = [line(headers), indent + "  ".join("-" * width for width in widths)]
    output.extend(line(row) for row in cells)
    return "\n".join(output)


def _ms(value):
    return f"{value:.2f}"


def _pct(value):
    return "n/a" if value is None else f"{value:+.1f}%"


def percentile(samples, fraction):
    ordered = sorted(samples)
    if not ordered:
        return 0.0
    return ordered[min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))]


# Windows의 time.process_time()/thread_time()은 약 15.6ms(=1/64초) 단위로 양자화된다.
# 4방 fixture의 core.rooms()/core.events()는 1~6ms라 1회 단위 CPU 계측이 0으로 뭉개지므로,
# CPU는 "배치 총 CPU / 호출 수"로만 계산한다(호출 수를 늘려 양자화 오차를 나눈다).
CPU_QUANTUM_MS = 15.625


def timed_wall(fn, repeats, warmup=0):
    """fn() 1회의 wall-clock(perf_counter)을 repeats회 재고 중앙값/최소/p90을 낸다."""
    for _ in range(warmup):
        fn()
    wall = []
    result = None
    for _ in range(max(1, repeats)):
        started = time.perf_counter()
        result = fn()
        wall.append((time.perf_counter() - started) * 1000.0)
    return {
        "samples": len(wall),
        "wall_ms_median": round(statistics.median(wall), 3),
        "wall_ms_min": round(min(wall), 3),
        "wall_ms_p90": round(percentile(wall, 0.9), 3),
        "result": result,
    }


def measure_cpu_batch(fn, calls, warmup=1):
    """CPU 시간은 배치 총량(time.process_time)을 호출 수로 나눈 값으로 낸다(양자화 보정)."""
    for _ in range(warmup):
        fn()
    cpu0, wall0 = time.process_time(), time.perf_counter()
    for _ in range(calls):
        fn()
    cpu_total = (time.process_time() - cpu0) * 1000.0
    wall_total = (time.perf_counter() - wall0) * 1000.0
    return {
        "cpu_ms_per_call": round(cpu_total / calls, 3),
        "cpu_total_ms": round(cpu_total, 3),
        "wall_ms_per_call": round(wall_total / calls, 3),
        "cpu_batch_calls": calls,
        "cpu_quantum_ms": CPU_QUANTUM_MS,
    }


# --------------------------------------------------------------------------- 1. web assets
def measure_web_assets(web_dir):
    files = []
    for path in sorted(web_dir.iterdir()):
        if not path.is_file():
            continue
        data = path.read_bytes()
        compressed = len(gzip.compress(data, 9))
        files.append({
            "name": path.name,
            "bytes": len(data),
            "gzip9_bytes": compressed,
            "gzip9_ratio": round(compressed / len(data), 4) if data else 0.0,
        })
    total_bytes = sum(item["bytes"] for item in files)
    total_gzip = sum(item["gzip9_bytes"] for item in files)
    return {
        "root": "talkabout/web",
        "api_note": "/web/{name} allowlist는 app.mjs·state.mjs·style.css·favicon.svg·connect-agent.ps1이고 "
                    "index.html은 / 로 제공된다. 총합은 디렉터리 전체 파일 기준이다.",
        "files": files,
        "totals": {
            "files": len(files),
            "bytes": total_bytes,
            "gzip9_bytes": total_gzip,
            "gzip9_ratio": round(total_gzip / total_bytes, 4) if total_bytes else 0.0,
        },
    }


# --------------------------------------------------------------------------- 2. fixture
def _local_build_fixture(store):
    """tests/conftest.py:build_fixture와 같은 최소 fixture(헬퍼를 import할 수 없을 때만 쓰는 대체 경로)."""
    from talkabout.core import Core
    from talkabout.domain import AGENT_PERMISSIONS, HUMAN_PERMISSIONS, SELF_PERMISSIONS

    for pid, kind, name in [("human", "human", "Owner"), ("a", "agent", "Atlas"), ("b", "agent", "Birch")]:
        permissions = ((HUMAN_PERMISSIONS | {"rooms:create", "agent:manage", "credential:manage"})
                       if kind == "human" else (AGENT_PERMISSIONS | SELF_PERMISSIONS | {"rooms:create"}))
        store.provision({"id": pid, "owner": "fixture-owner", "kind": kind, "display_name": name,
                         "handle": name.lower()}, "fixture-" + pid, permissions)
    core = Core(store)
    room = core.create(TOKEN, {"topic": "Synthetic neutral topic"}, "room-one")
    for pid in ["a", "b"]:
        member = core.mutation(TOKEN, room["id"], "memberships", "invite-" + pid,
                               {"principal_id": pid, "permissions": sorted(AGENT_PERMISSIONS), "status": "pending"})
        core.accept("fixture-" + pid, room["id"], "accept",
                    {"expected_revision": member["revision"], "acknowledge_private": True})
    return store, core, room["id"]


def fixture_builder():
    """기존 테스트 헬퍼를 재사용하고, 불가능할 때만 인라인 대체 구현을 쓴다."""
    try:
        from conftest import build_fixture  # tests/conftest.py
        return build_fixture, "tests/conftest.py::build_fixture (기존 헬퍼 재사용)"
    except Exception as exc:  # pragma: no cover - environment dependent
        print(f"[perf_snapshot] conftest fixture import 실패({type(exc).__name__}) → 인라인 대체 구현 사용",
              file=sys.stderr)
        return _local_build_fixture, f"inline fallback ({type(exc).__name__})"


def build_perf_fixture():
    """4방/각 40메시지 fixture. 기존 build_fixture(1방) + 방 3개 생성 + 방마다 메시지 40건."""
    from talkabout.store import MemoryStore

    build, source = fixture_builder()
    store, core, first_room = build(MemoryStore())
    room_ids = [first_room]
    for index in range(1, FIXTURE_ROOMS):
        room_ids.append(core.create(TOKEN, {"topic": f"Synthetic perf room {index + 1}"},
                                    f"perf-create-{index}")["id"])
    for room_id in room_ids:
        for n in range(FIXTURE_MESSAGES):
            core.mutation(TOKEN, room_id, "messages", f"{room_id}-perf-msg-{n}", {
                "kind": "question",
                "text": ("q" * 400) + str(n),
                "client_message_id": f"perf-c-{n}",
            })
    return store, core, room_ids, source


def fixture_facts(store, room_ids):
    rooms = [store.rooms[room_id] for room_id in room_ids]
    events = [event for room in rooms for event in room["events"]]
    event_bytes = sum(len(json.dumps(event, ensure_ascii=False).encode("utf-8")) for event in events)
    return {
        "rooms": len(rooms),
        "messages_per_room": [len(room["messages"]) for room in rooms],
        "tasks_per_room": [len(room["tasks"]) for room in rooms],
        "events_total": len(events),
        "events_per_room": [len(room["events"]) for room in rooms],
        "avg_event_bytes": round(event_bytes / len(events), 1) if events else 0.0,
        "messages_text_chars": 400,
    }


# --------------------------------------------------------------------------- 3. domain
def measure_domain(core, repeats, warmup):
    measurements = []
    cpu_calls = max(60, repeats * 3)
    for name, description, call, items in (
        ("core.rooms()", "워크스페이스 전 방 projection 1회(스냅샷 + public_room + snapshot_bounds)",
         lambda: core.rooms(TOKEN), lambda result: len(result or [])),
        ("core.events()", "SSE 배치 1회(wait_seconds=0): 스냅샷 2회 + 이벤트 전량 로딩/필터 + 커서 저장",
         lambda: core.events(TOKEN), lambda result: len((result or {}).get("events", []))),
    ):
        wall_stats = timed_wall(call, repeats, warmup)
        cpu_stats = measure_cpu_batch(call, cpu_calls)
        measurements.append({
            "name": name,
            "description": description,
            "cpu_ms_per_call": cpu_stats["cpu_ms_per_call"],
            "cpu_total_ms": cpu_stats["cpu_total_ms"],
            "cpu_batch_calls": cpu_stats["cpu_batch_calls"],
            "cpu_quantum_ms": cpu_stats["cpu_quantum_ms"],
            "wall_ms_median": wall_stats["wall_ms_median"],
            "wall_ms_min": wall_stats["wall_ms_min"],
            "wall_ms_p90": wall_stats["wall_ms_p90"],
            "samples": wall_stats["samples"],
            "result_items": items(wall_stats["result"]),
        })
    return {
        "note": "wall은 time.perf_counter() 1회 median(DB 0, 프로세스 내부). CPU는 Windows 타이머가 약 15.6ms로 "
                "양자화되어 1회 단위 median이 0으로 뭉개지므로 '배치 총 CPU / 호출 수'로 계산했다"
                "(cpu_batch_calls회 연속 호출의 time.process_time() 총량을 나눈 값). MemoryStore라 DB 왕복·네트워크 비용은 0이다.",
        "cpu_method": "cpu_ms_per_call = time.process_time() batch total / cpu_batch_calls (15.6ms 양자화 보정)",
        "measurements": measurements,
    }


# --------------------------------------------------------------------------- 4. query_count
class StoreCallCounter:
    """store 인스턴스 메서드를 감싸 호출 수와 '커넥션 상당' 최상위 호출 수를 센다.

    최상위(depth==0) 호출만 커넥션 1회로 센다. MemoryStore.snapshot 내부의 self.auth는
    PostgresStore.snapshot이 같은 커넥션에서 _auth를 부르는 것과 같은 의미이므로 중복 계산하지 않는다.
    """

    def __init__(self, store, names=CONNECTION_METHODS):
        self.store = store
        self.names = names
        self.depth = 0
        self.calls = {}
        self.opens = {}
        self._saved = {}

    def __enter__(self):
        for name in self.names:
            method = getattr(self.store, name, None)
            if callable(method):
                # 원래 인스턴스 속성이었는지 기억한다. 클래스 속성을 인스턴스로 복제하면
                # 이후 클래스 단위 monkeypatch(예: sse_idle)가 가려지므로 복원 시 삭제해야 한다.
                self._saved[name] = (method, name in getattr(self.store, "__dict__", {}))
                setattr(self.store, name, self._wrap(name, method))
        return self

    def __exit__(self, *_exc):
        for name, (method, owned) in self._saved.items():
            if owned:
                setattr(self.store, name, method)
            elif name in self.store.__dict__:
                delattr(self.store, name)
        return False

    def _wrap(self, name, method):
        @functools.wraps(method)
        def wrapper(*args, **kwargs):
            self.calls[name] = self.calls.get(name, 0) + 1
            if self.depth == 0:
                self.opens[name] = self.opens.get(name, 0) + 1
            self.depth += 1
            try:
                return method(*args, **kwargs)
            finally:
                self.depth -= 1
        return wrapper

    @property
    def connection_opens(self):
        return sum(self.opens.values())

    def snapshot_calls(self):
        return self.calls.get("snapshot", 0)


def measure_query_count(store, core):
    """연산 1회당 store 호출 수(커넥션 상당)와 snapshot 호출 수를 센다.

    유휴 SSE 변형은 API의 SSE 루프처럼 직전 응답의 next_cursor를 넘겨 '새 이벤트 없음' 경로를 재현한다.
    커서 없이 호출하면 이벤트가 실려 2차 스냅샷 경로를 타므로, 유휴와 비유휴를 둘 다 기록한다.
    """
    state = {"cursor": core.events(TOKEN)["next_cursor"]}

    def events_idle():
        response = core.events(TOKEN, cursor=state["cursor"], wait_seconds=0)
        state["cursor"] = response["next_cursor"]
        return response

    # 유휴 측정 전에 이벤트 백로그를 소진한다(소진되지 않으면 유휴가 아니라 정상 배치 경로다).
    drained = 0
    for _ in range(60):
        if not events_idle()["events"]:
            break
        drained += 1

    measurements = []
    for name, description, call in (
        ("core.rooms()", "워크스페이스 목록 1회", lambda: core.rooms(TOKEN)),
        ("core.events()", "SSE 배치 1회(wait_seconds=0, 커서 없음 → 이벤트 포함 응답)",
         lambda: core.events(TOKEN)),
        ("core.events() idle", "유휴 SSE 1회(직전 next_cursor 전달 → 새 이벤트 없음)", events_idle),
    ):
        with StoreCallCounter(store) as counter:
            call()
        measurements.append({
            "name": name,
            "description": description,
            "connection_opens": counter.connection_opens,
            "opens_by_method": {key: value for key, value in sorted(counter.opens.items())},
            "snapshot_calls": counter.snapshot_calls(),
            "room_events_calls": counter.calls.get("room_events", 0),
            "calls_by_method": {key: value for key, value in sorted(counter.calls.items())},
        })
    return {
        "method": (
            "MemoryStore에는 DB가 없으므로 connect() 횟수를 직접 셀 수 없다. 대신 PostgresStore가 "
            "self.connect()를 여는 공개 store 메서드 호출을 'DB 왕복 1회 상당'으로 세었다(최상위 호출만 계산, "
            "snapshot 내부의 auth는 같은 커넥션으로 간주). snapshot_calls는 별도 실측값이다. "
            "따라서 이 값은 실 DB 왕복 실측이 아니라 커넥션 개수 추정이며, 실측은 --postgres 없이 불가능하다."
        ),
        "connection_semantics": "connection_opens = PostgresStore 기준 요청 1회당 새 커넥션 수 추정",
        "backlog_drain_iterations": drained,
        "measurements": measurements,
    }


# --------------------------------------------------------------------------- 5. sse_idle
def measure_sse_idle(core, iterations=3):
    """모듈 속성(클래스) 카운터를 monkeypatch해 유휴 SSE 1회 반복의 store.snapshot 호출 수를 센다.

    API의 SSE 루프(api.py:131 → core.events)처럼 직전 응답의 next_cursor를 이어 넘겨
    '새 이벤트 없음' 경로를 재현한다. 커서 없이 부르면 이벤트가 실려 2차 스냅샷 경로를 타므로
    유휴 측정이 아니다.
    """
    from talkabout.store import MemoryStore

    original = MemoryStore.snapshot
    per_iteration = []
    state = {"cursor": None, "calls": 0}

    def counted_snapshot(*args, **kwargs):
        state["calls"] += 1
        return original(*args, **kwargs)

    MemoryStore.snapshot = counted_snapshot
    try:
        state["cursor"] = core.events(TOKEN)["next_cursor"]  # 워밍업 1회 + 커서 확보

        def idle_events():
            response = core.events(TOKEN, cursor=state["cursor"], wait_seconds=0)
            state["cursor"] = response["next_cursor"]
            return response

        # 4방 40메시지 fixture에는 이벤트 백로그가 있으므로 먼저 소진해 '새 이벤트 없음' 상태로 만든다.
        drained = 0
        for _ in range(60):
            if not idle_events()["events"]:
                break
            drained += 1

        per_iteration = []
        empty_responses = []
        wall = []
        for _ in range(iterations):
            state["calls"] = 0
            wall0 = time.perf_counter()
            response = idle_events()
            wall.append((time.perf_counter() - wall0) * 1000.0)
            per_iteration.append(state["calls"])
            empty_responses.append(not response["events"])
        cpu_stats = measure_cpu_batch(idle_events, 40)
    finally:
        MemoryStore.snapshot = original
    baseline_expected = 2
    return {
        "note": "유휴(새 이벤트 없음) 상태에서 core.events(wait_seconds=0) 1회 반복이 부르는 store.snapshot 횟수. "
                "r3 기준선 코드는 core.py:695(1차) + core.py:736(응답 직전 ACL 재검사)으로 항상 2회였고, "
                "이 값이 1이면 P1-1(유휴 시 2차 스냅샷 생략)이 이미 적용된 것이다.",
        "baseline_expected_snapshot_calls": baseline_expected,
        "observed_snapshot_calls": per_iteration,
        "snapshot_calls_median": statistics.median(per_iteration) if per_iteration else 0,
        "matches_baseline_expectation": bool(per_iteration) and all(value == baseline_expected for value in per_iteration),
        "idle_confirmed": bool(empty_responses) and all(empty_responses),
        "backlog_drain_iterations": drained,
        "iterations": iterations,
        "cpu_ms_per_call": cpu_stats["cpu_ms_per_call"],
        "cpu_batch_calls": cpu_stats["cpu_batch_calls"],
        "wall_ms_median": round(statistics.median(wall), 3) if wall else 0.0,
    }


# --------------------------------------------------------------------------- 6. http
def measure_http(store, room_ids, repeats, warmup):
    import warnings

    with warnings.catch_warnings():
        # fastapi 0.162가 httpx 기반 TestClient에서 내는 deprecation 경고는 측정과 무관하다.
        warnings.simplefilter("ignore")
        from fastapi.testclient import TestClient
    from talkabout.api import create_app

    app = create_app(store, start_scheduler=False, allow_fixture_bearer=True)
    client = TestClient(app)
    auth = {"Authorization": "Bearer " + TOKEN}
    room_id = room_ids[-1]
    post_counter = {"n": 0}

    def run(method, path, body=None, headers=None, params=None):
        samples, sizes, statuses = [], [], []
        for _ in range(warmup):
            response = client.request(method, path, json=body, headers=headers or {}, params=params)
            if response.status_code >= 400:
                raise RuntimeError(f"HTTP warmup 실패: {method} {path} -> {response.status_code} {response.text[:200]}")
        for _ in range(repeats):
            started = time.perf_counter()
            response = client.request(method, path, json=body, headers=headers or {}, params=params)
            samples.append((time.perf_counter() - started) * 1000.0)
            if response.status_code >= 400:
                raise RuntimeError(f"HTTP 측정 실패: {method} {path} -> {response.status_code} {response.text[:200]}")
            statuses.append(response.status_code)
            sizes.append(len(response.content))
        return {
            "samples": len(samples),
            "median_ms": round(statistics.median(samples), 3),
            "min_ms": round(min(samples), 3),
            "p90_ms": round(percentile(samples, 0.9), 3),
            "response_bytes_median": int(statistics.median(sizes)),
            "status": statuses[0],
        }

    cases = [
        {"name": "GET /health", "method": "GET", "path": "/health", "headers": {}},
        {"name": "GET /api/v1/rooms", "method": "GET", "path": "/api/v1/rooms", "headers": auth},
        {"name": "GET /api/v1/rooms/{id}", "method": "GET", "path": f"/api/v1/rooms/{room_id}", "headers": auth},
        {"name": "GET /api/v1/rooms/{id}/events", "method": "GET", "path": f"/api/v1/rooms/{room_id}/events",
         "headers": auth, "params": {"wait_seconds": 0}},
    ]
    results = []
    for case in cases:
        measured = run(case["method"], case["path"], headers=case["headers"], params=case.get("params"))
        results.append({**case, **measured})

    # POST는 방을 변형하므로 GET 측정 뒤에 한다. 메시지 40건 방은 한도(40)에 닿아 있으니 한도만 올린다(벤치 관례).
    with store.transaction(room_id, TOKEN) as (_actor, room):
        room["limits"] = {**room["limits"], "messages": 200}
    post_warmup = min(warmup, 2)

    def post_message():
        index = post_counter["n"]
        post_counter["n"] += 1
        response = client.post(
            f"/api/v1/rooms/{room_id}/messages",
            json={"kind": "question", "text": "p" * POST_MESSAGE_CHARS, "client_message_id": f"perf-post-{index}"},
            headers={**auth, "Idempotency-Key": f"perf-post-{index}"},
        )
        if response.status_code >= 400:
            raise RuntimeError(f"POST 실패: {response.status_code} {response.text[:300]}")
        return response

    samples, sizes = [], []
    for _ in range(post_warmup):
        post_message()
    for _ in range(repeats):
        started = time.perf_counter()
        response = post_message()
        samples.append((time.perf_counter() - started) * 1000.0)
        sizes.append(len(response.content))
    results.append({
        "name": "POST /api/v1/rooms/{id}/messages",
        "method": "POST",
        "path": f"/api/v1/rooms/{room_id}/messages",
        "headers": {"Authorization": "Bearer " + TOKEN, "Idempotency-Key": "perf-post-<n>"},
        "samples": len(samples),
        "median_ms": round(statistics.median(samples), 3),
        "min_ms": round(min(samples), 3),
        "p90_ms": round(percentile(samples, 0.9), 3),
        "response_bytes_median": int(statistics.median(sizes)),
        "status": 200,
    })
    return {
        "client": "fastapi.testclient.TestClient (인프로세스, 서버 프로세스 미기동)",
        "note": HTTP_NOTE,
        "post_note": "POST는 방 상태를 바꾸므로 마지막에 측정했다(메시지 40건 방, limits.messages를 200으로 올린 뒤). "
                     "매 요청이 새 메시지를 1건 추가하므로 응답 시간은 반복 중 메시지 수 증가를 포함한다.",
        "cases": results,
    }


# --------------------------------------------------------------------------- baseline compare
COMPARE_ROOTS = ("web_assets", "http", "domain", "query_count", "sse_idle")


def flatten_metrics(value, prefix=""):
    metrics = {}
    if isinstance(value, dict):
        for key, item in value.items():
            metrics.update(flatten_metrics(item, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            label = item.get("name") if isinstance(item, dict) and isinstance(item.get("name"), str) else str(index)
            metrics.update(flatten_metrics(item, f"{prefix}[{label}]"))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        metrics[prefix] = value
    return metrics


def build_comparison(baseline, snapshot):
    previous = flatten_metrics({root: baseline.get(root) for root in COMPARE_ROOTS})
    current = flatten_metrics({root: snapshot.get(root) for root in COMPARE_ROOTS})
    rows = []
    for metric in sorted(set(previous) & set(current)):
        before, after = previous[metric], current[metric]
        delta = None if before == 0 else round((after - before) / before * 100.0, 2)
        rows.append({"metric": metric, "baseline": before, "current": after, "delta_pct": delta})
    return {
        "baseline_generated_at": baseline.get("generated_at"),
        "baseline_tool": baseline.get("tool"),
        "metrics": rows,
        "missing_in_current": sorted(set(previous) - set(current)),
        "new_in_current": sorted(set(current) - set(previous)),
        "note": "delta_pct는 (현재-기준)/기준×100이다. wall-clock은 같은 머신·같은 세션의 상대 비교로만 쓰고, "
                "다른 머신·다른 부하와 절대값 비교하지 않는다.",
    }


# --------------------------------------------------------------------------- human rendering
def render(snapshot):
    environment = snapshot["environment"]
    settings = snapshot["settings"]
    lines = [
        f"TalkAbout 성능 스냅샷 - {snapshot['generated_at']}",
        f"  python {environment['python']} · {environment['platform']}",
        f"  store=MemoryStore (PostgreSQL 없음) · fixture: {settings['fixture_rooms']}방 × "
        f"{settings['fixture_messages_per_room']}메시지 · repeats={settings['repeats']} (warmup {settings['warmup']})",
        f"  fixture 출처: {environment['fixture_source']}",
        "",
    ]

    web = snapshot["web_assets"]
    lines.append("[1] web_assets - talkabout/web/ 원본 vs gzip9 바이트")
    rows = [[item["name"], f"{item['bytes']:,}", f"{item['gzip9_bytes']:,}", f"{item['gzip9_ratio']:.3f}"]
            for item in web["files"]]
    rows.append(["TOTAL", f"{web['totals']['bytes']:,}", f"{web['totals']['gzip9_bytes']:,}",
                 f"{web['totals']['gzip9_ratio']:.3f}"])
    lines.append(render_table(["file", "bytes", "gzip9", "ratio"], rows, aligns=["l", "r", "r", "r"]))
    lines.append("")

    http = snapshot["http"]
    lines.append(f"[2] http - TestClient 인프로세스 wall-clock ms, N={settings['repeats']}")
    rows = [[case["name"], _ms(case["median_ms"]), _ms(case["min_ms"]), _ms(case["p90_ms"]),
             f"{case['response_bytes_median']:,}", str(case["status"])] for case in http["cases"]]
    lines.append(render_table(["case", "median", "min", "p90", "resp_bytes", "status"], rows,
                              aligns=["l", "r", "r", "r", "r", "r"]))
    lines.append(f"  note: {http['note']}")
    lines.append(f"  note: {http['post_note']}")
    lines.append("")

    domain = snapshot["domain"]
    facts = snapshot["domain_fixture"]
    lines.append(f"[3] domain - {facts['rooms']}방/방당 {facts['messages_per_room'][0]}메시지, "
                 f"CPU=process_time 배치총량/호출수")
    rows = [[item["name"], _ms(item["cpu_ms_per_call"]), _ms(item["wall_ms_median"]), _ms(item["wall_ms_min"]),
             str(item["samples"]), str(item["result_items"])] for item in domain["measurements"]]
    lines.append(render_table(["metric", "cpu_ms/call", "wall_ms_med", "min_ms", "n", "items"], rows,
                              aligns=["l", "r", "r", "r", "r", "r"]))
    lines.append(f"  fixture: events={facts['events_total']}건(방당 {facts['events_per_room']}), "
                 f"avg_event={facts['avg_event_bytes']}B, tasks/방={facts['tasks_per_room']}")
    lines.append(f"  CPU 방법: {domain['cpu_method']} (CPU 배치 {domain['measurements'][0]['cpu_batch_calls']}회)")
    lines.append(f"  note: {domain['note']}")
    lines.append("")

    query = snapshot["query_count"]
    lines.append("[4] query_count - 연산 1회당 store 호출 계측(Postgres connect 1회 상당 = 최상위 store 메서드 1회)")
    rows = []
    for item in query["measurements"]:
        opens = ", ".join(f"{key}×{value}" for key, value in item["opens_by_method"].items()) or "-"
        rows.append([item["name"], str(item["connection_opens"]), str(item["snapshot_calls"]), opens])
    lines.append(render_table(["operation", "conn_opens", "snapshots", "opens_by_method"], rows,
                              aligns=["l", "r", "r", "l"]))
    lines.append("  방법: " + query["method"])
    lines.append("")

    sse = snapshot["sse_idle"]
    lines.append("[5] sse_idle - 유휴 SSE 1회 반복(core.events(wait_seconds=0, 직전 커서))의 store.snapshot 호출 수")
    rows = [
        ["baseline_expected", str(sse["baseline_expected_snapshot_calls"])],
        ["observed", str(sse["observed_snapshot_calls"])],
        ["median", f"{sse['snapshot_calls_median']:g}"],
        ["matches_baseline_expectation", str(sse["matches_baseline_expectation"])],
        ["cpu_ms_per_call", _ms(sse["cpu_ms_per_call"])],
        ["wall_ms_median", _ms(sse["wall_ms_median"])],
    ]
    lines.append(render_table(["item", "value"], rows, aligns=["l", "r"]))
    lines.append(f"  note: {sse['note']}")
    lines.append("")

    comparison = snapshot.get("baseline")
    if comparison:
        lines.append(f"[6] baseline 비교 - 이전 스냅샷({comparison['baseline_generated_at']}) 대비 변화율")
        changed = [row for row in comparison["metrics"] if row["baseline"] != row["current"]]
        unchanged = len(comparison["metrics"]) - len(changed)
        changed.sort(key=lambda row: abs(row["delta_pct"] or 0), reverse=True)
        if changed:
            body = changed[:40]
            rows = [[row["metric"], f"{row['baseline']:,g}", f"{row['current']:,g}", _pct(row["delta_pct"])]
                    for row in body]
            lines.append(render_table(["metric", "baseline", "current", "delta"], rows,
                                      aligns=["l", "r", "r", "r"]))
            if len(changed) > len(body):
                lines.append(f"  ...변화 {len(changed)}개 중 상위 {len(body)}개만 표시")
        else:
            lines.append("  변화 없음")
        lines.append(f"  unchanged={unchanged}, missing_in_current={len(comparison['missing_in_current'])}, "
                     f"new_in_current={len(comparison['new_in_current'])}")
        if comparison["missing_in_current"]:
            lines.append("  missing: " + ", ".join(comparison["missing_in_current"][:6]))
        lines.append(f"  note: {comparison['note']}")

    lines.append("")
    if not comparison:
        lines.append("절대값 comparison 없이 단독 실행이다. 다음 실행에서 --baseline <이 파일>로 상대 비교하라.")
        lines.append("  저장: .\\.venv\\Scripts\\python.exe -B tools\\perf_snapshot.py --out perf-baseline.json")
    return "\n".join(lines)


# --------------------------------------------------------------------------- main
def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="TalkAbout 성능 스냅샷(재실행 가능·DB 불필요)")
    parser.add_argument("--json", action="store_true", help="JSON만 출력한다(사람용 표 생략)")
    parser.add_argument("--baseline", metavar="PATH", default=None, help="이전 JSON 스냅샷과 비교해 변화율 출력")
    parser.add_argument("--out", metavar="PATH", default=None, help="JSON 스냅샷을 파일로도 저장")
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS, help=f"케이스별 측정 횟수(기본 {DEFAULT_REPEATS}, 최소 5)")
    parser.add_argument("--warmup", type=int, default=DEFAULT_WARMUP, help=f"케이스별 워밍업 횟수(기본 {DEFAULT_WARMUP})")
    return parser.parse_args(argv)


def main(argv=None):
    configure_output()
    args = parse_args(argv)
    repeats = max(5, args.repeats)
    warmup = max(0, args.warmup)

    web = measure_web_assets(ROOT / "talkabout" / "web")
    store, core, room_ids, fixture_source = build_perf_fixture()
    facts = fixture_facts(store, room_ids)

    # HTTP(TestClient) 구간은 anyio 포털 스레드를 띄우고, time.process_time()은 프로세스 전체 CPU라
    # 그 스레드의 CPU까지 합산되어 cpu_ms가 wall_ms보다 커진다. CPU 계측은 TestClient 생성 전에 끝낸다.
    domain = measure_domain(core, repeats, warmup)
    query_count = measure_query_count(store, core)
    sse_idle = measure_sse_idle(core)
    http = measure_http(store, room_ids, repeats, warmup)

    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor() or None,
            "cwd": str(Path.cwd()),
            "store": "MemoryStore (contract test store; no PostgreSQL, no network)",
            "server_started": False,
            "network": "disabled - in-process TestClient only",
            "fixture_source": fixture_source,
        },
        "settings": {
            "repeats": repeats,
            "warmup": warmup,
            "fixture_rooms": FIXTURE_ROOMS,
            "fixture_messages_per_room": FIXTURE_MESSAGES,
            "message_text_chars": 400,
            "post_message_chars": POST_MESSAGE_CHARS,
            "token": TOKEN,
            "baseline_expected_sse_snapshots": 2,
        },
        "web_assets": web,
        "http": http,
        "domain_fixture": facts,
        "domain": domain,
        "query_count": query_count,
        "sse_idle": sse_idle,
    }

    if args.baseline:
        baseline_path = Path(args.baseline)
        if not baseline_path.is_file():
            print(f"[perf_snapshot] baseline 파일이 없다: {baseline_path}", file=sys.stderr)
            return 2
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        snapshot["baseline"] = build_comparison(baseline, snapshot)

    if args.out:
        out_path = Path(args.out)
        out_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[perf_snapshot] JSON 스냅샷 저장: {out_path}", file=sys.stderr)

    if args.json:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    else:
        print(render(snapshot))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
