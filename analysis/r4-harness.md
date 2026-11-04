# R4 계측 하네스 — `tools/perf_snapshot.py` (P0-0)

- 목적: R3에서 `.tmp-perf/`에 임시 벤치만 남았고 재실행 규약·비교 기능이 없었다. 이 도구는 표준 라이브러리 + 이미 설치된 fastapi/httpx만 쓰고, **서버 기동·PostgreSQL·외부 네트워크·실제 LLM 호출 없이** 돈다(MemoryStore fixture).
- 실행: `.\.venv\Scripts\python.exe -B tools\perf_snapshot.py`
  - `--json` JSON만 출력 · `--out PATH` JSON 저장 · `--baseline PATH` 이전 스냅샷과 변화율 · `--repeats N`(기본 21, 최소 5) · `--warmup N`(기본 3)
  - 기준선 만들기: `... --out perf-baseline.json` → 다음 실행 `... --baseline perf-baseline.json`
- fixture: `tests/conftest.py::build_fixture`(기존 헬퍼)를 재사용하고 방 3개를 추가, 방마다 메시지 40건 = **4방/40메시지**. 헬퍼 import가 불가능할 때만 동일 구성 인라인 대체 경로를 쓴다(중복 구현 최소화).

## 측정 정의

| 항목 | 정의 |
| --- | --- |
| `web_assets` | `talkabout/web/` 전 파일의 원본 바이트, `gzip.compress(data, 9)`, 총합·비율 |
| `http` | TestClient 인프로세스 wall-clock median(N=21) + `len(response.content)`. `/health`는 인증·DB 미들웨어가 없는 하네스 바닥값 |
| `domain` | fixture에서 `core.rooms()`·`core.events()` **1회** wall median + CPU `cpu_ms/call` |
| `query_count` | store 공개 메서드를 monkeypatch로 감싸 **최상위 호출 1회 = Postgres `connect()` 1회 상당**으로 계산. `snapshot`·`room_events` 호출 수도 별도 기록 |
| `sse_idle` | 직전 `next_cursor`를 이어 넘겨 '새 이벤트 없음' 경로 1회 반복 시 `MemoryStore.snapshot` 호출 수(클래스 속성 카운터 monkeypatch) |

- CPU 계측 한계: Windows `time.process_time()`은 15.625ms로 양자화되어 2~3ms 호출의 개별 delta가 0이 된다 → CPU는 `cpu_ms/call = 배치 총 CPU / 배치 호출 수`로만 낸다. JSON에 `cpu_method`·`cpu_quantum_ms`로 명시했다.

## 현재 측정값 (2026-11-03 06:24:20, 이 머신, repeats=21)

- `web_assets`: **338,249B → gzip9 89,545B(26.5%)**.
- `http` median: `/health` 3.74ms · `GET rooms` 11.62ms/103,582B · `GET room` 8.71ms/25,573B · `GET room events` 7.52ms/3,571B · `POST message` 9.23ms/307B.
- `domain`: `core.rooms()` 2.70ms wall / 2.73ms CPU · `core.events()` 2.30ms wall / 2.48ms CPU (fixture 이벤트 169건, 평균 223.6B).
- `query_count`: `core.rooms()` = snapshot 1회 **1 커넥션**. `core.events()` = snapshot 2 + `room_events` 4 + cursor/save_cursor 2 + listener 1 = **8**. 유휴 `core.events()` = snapshot 1 → **8**.
- `sse_idle`: 유휴 반복당 **1회**. r3 기준선 코드(`core.py:695/736`)는 2회 고정이었으므로 현재 워크트리에는 **P1-1/P1-2가 이미 적용된 상태**다(2로 돌아가면 회귀).

## 한계 — 절대값(ms) 비교 금지

1. 같은 코드·같은 머신에서도 wall-clock이 **1.49배 변동**했다(r3 기준선 §1.2: 172.90s vs 257.75s). 이 머신에서 연속 2회 실행만으로 `/health` median이 3.6ms → 11.9ms(+230%)로 흔들렸다. 따라서 **절대 ms는 성능 판정 근거가 아니다.**
2. 회귀 판정은 (a) `query_count`의 `connection_opens`·`snapshot_calls`, (b) `web_assets` 바이트, (c) `http.response_bytes` 같은 **결정적 카운터**와, (d) 같은 세션·같은 머신에서 `--baseline`으로 잰 **상대 변화율**만 쓴다.
3. MemoryStore 기준이므로 DB 접속·쿼리 시간은 0이다. Postgres에서는 `connection_opens`에 커넥션 비용이 곱해지며, 도구는 개수만 추정한다(실 DB 실측은 `--postgres-config` fixture 영역, 이 도구 범위 밖). 측정 대상은 **실행 중인 워크트리 코드**이므로 공유 머신의 다른 프로세스가 CPU를 쓰면 wall이 오염된다.

## CI 연결 제안

- `tools/run_safe_regression.py` **뒤에 별도 스텝**: `python -B tools/perf_snapshot.py --out .ci-results/<run>/perf.json --json` — `--json`은 stdout에 순수 JSON만 쓰고 저장 경로는 stderr로 안내하므로 그대로 아티팩트화할 수 있다.
- 1차 게이트는 wall-clock이 아니라 **결정적 카운터의 pytest 어서션**으로 둔다(`tests/test_r4_perf.py` 방식: 쿼리/snapshot 수·응답 바이트). `perf_snapshot.py`는 추세 기록·아티팩트·사람용 표 요약 담당.
- 추세 게이트가 필요하면 `--baseline .ci-results/<previous>/perf.json`으로 **같은 러너에서만** 비교하고, 임계는 카운터(변동 0) 기준으로 건다. wall-clock 임계 게이트는 금지(위 1.49배 변동).
- 라이선스: 외부 저장소 코드 복사 없음. t3code의 "전송 예산 테스트"·handy의 "지연 계측" 개념만 참고해 전부 자체 구현했다.
