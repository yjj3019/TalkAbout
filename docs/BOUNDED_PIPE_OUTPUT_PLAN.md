# Bounded pipe 출력 설계안 (2026-11-11 승인·구현됨 — [SESSION_LOG](SESSION_LOG.md) 참조)

작성: 2026-11-11. 11-09 SESSION_LOG "미반영 후속 1"의 근본 해결안. 대상은 Windows(`os.name == 'nt'`)이며 POSIX는 범위 밖(현재도 `runtime_containment_unsupported`).

## 1. 문제 (관측 사실)

자식 프로세스의 stdout/stderr를 **임시 파일**에 받고, 부모가 `os.fstat().st_size`를 폴링해 한도를 확인한다. 폴링 간격 사이에 자식이 쓴 양은 막을 수 없다.

| 위치 | 한도 | 폴링 간격 | 용도 |
|---|---|---|---|
| `adapters.py:30` `execute_bounded` | 2 MiB (stdout+stderr 합) | 30 ms | CLIRuntime 실행, `agent_runtime.py:714`, `:1237` 호출 |
| `agent_runtime.py:1005` `_check_codex_exec_auth` | `MAX_LINE`=256 KiB (합) | 10 ms | `codex login status` |

한도 초과를 감지하면 즉시 중단·정리하지만, 감지 전에 디스크에 쓰인 양은 한도로 묶이지 않는다. `adapters.py:55-56` 주석이 이를 "해결이 아닌 문서화된 경계"로 명시한다. `_JSONLTransport`(`agent_runtime.py:361`)는 이미 pipe + reader thread 구조여서 대상이 아니다.

## 2. 설계 (권장안 A)

stdout/stderr를 파일 대신 **pipe로 받고, 바이트를 세는 reader thread**가 한도를 직접 강제한다. `_JSONLTransport._read`의 기존 패턴을 재사용한다.

- 신규 헬퍼 `BoundedCapture(process, limit)` (위치: `processes.py`, ~40줄).
  - stdout·stderr 각각 reader thread(daemon)가 고정 청크(예: 4096 B)로 읽는다.
  - 공유 카운터(lock)로 합계를 센다. 합계가 `limit`을 넘는 **그 청크에서** `exceeded=True`를 세우고 읽기를 멈춘다. 한도 초과분은 저장하지 않는다.
  - 읽기를 멈추면 pipe가 차서 자식의 write가 블록된다 → 디스크·메모리 증가 없음. 부모는 기존 폴링 루프에서 `exceeded`를 보고 `runtime_output_limit`을 던지고, 기존 `finally: supervisor.stop()`이 job을 죽인다.
  - 보관량: 한도(2 MiB) + 청크 이하. stdin은 부모가 쓴 ≤200000 B 파일이라 그대로 둔다.
- 두 호출부(`execute_bounded`, `_check_codex_exec_auth`)의 `TemporaryFile` 2개와 `fstat` 검사를 `BoundedCapture`로 교체. 에러 코드(`runtime_output_limit`/`runtime_failed`/`runtime_invalid_output`)와 "합계 한도" 의미는 유지.

### 대안과 기각 사유
- **B. 폴링 간격 단축**: burst를 막지 못함(간격 > 0). 기각.
- **C. Windows job 객체 제한**: 프로세스 수·메모리·CPU 시간 제한은 있으나 쓰기 바이트 상한은 없음(I/O rate control은 속도 제한이며 바이트 상한 아님) [unverified: 현재 Windows 문서 재확인 필요]. 기각.
- **D. 임시 디렉터리 쿼터**: 디스크 쿼터는 계정/볼륨 단위 설정이 필요하고 이식성이 낮음. 기각.

## 3. 영향 파일

`talkabout/processes.py`(헬퍼 추가), `talkabout/adapters.py`, `talkabout/agent_runtime.py`(`_check_codex_exec_auth`), `tests/test_bounded_capture.py`(신규), `docs/SESSION_LOG.md`·위 한계 주석 정정. 시그니처·에러 코드 변경 없음(호출부 5곳 호환).

## 4. Acceptance Criteria

1. 자식이 stdout에 1 GiB를 즉시 쏟아도: `runtime_output_limit`, 보관 바이트 ≤ `limit`+청크, 테스트 임시 디렉터리 파일 증가 0, 프로세스 종료 확인.
2. stderr만 폭주해도 동일. stdout+stderr **합계** 한도 의미 유지.
3. 정상 출력은 기존과 동일(`test_cli_adapters.py`, `test_process_cleanup.py` 무수정 통과).
4. 초과·타임아웃·취소 후 reader thread 잔존 0(`threading.enumerate()` 전후 비교).
5. launch 실패 경로에서 pipe 핸들 누수 없음(11-09의 launch cleanup 테스트 통과 유지).
6. `_check_codex_exec_auth`의 status 파싱 결과가 기존과 동일.
7. 전체 Python 스위트 무회귀(현재 기준 2114 passed / 0 failed).

## 5. 리스크와 실패 시나리오

- **reader 이상 종료 → 자식 write 블록 → 부모 hang**: reader 예외를 `failure`로 올리고 폴링 루프가 deadline/cancel로 항상 탈출. 기존 deadline 경로에 의존.
- **손자 프로세스가 pipe를 잡고 있어 EOF 미도달**: job kill이 트리를 죽이므로 reader는 daemon + `join(timeout)`으로 정리; 정리 미확인은 기존 `supervisor.stop() is not True` 경로 유지.
- **Windows pipe 버퍼가 작아 처리량 저하**: reader가 상시 drain하므로 정상 출력은 영향 작음. [unverified: 대용량 정상 출력 벤치 미측정] — 구현 후 측정.
- **인코딩/부분 UTF-8**: 청크 경계가 아닌 완료 후 전체를 decode하므로 현행과 동일.
- **남는 경계(범위 밖)**: 자식이 stdout이 아닌 **다른 파일**(work-profile workspace)에 쓰는 양은 이 설계로 막히지 않는다. 그건 provider workspace-write 정책과 containment의 몫.

## 6. 단계

1) `BoundedCapture` + 단위 테스트(폭주·정상·취소) → 2) `execute_bounded` 교체 → 3) `_check_codex_exec_auth` 교체 → 4) 전체 스위트 → 5) 주석·로그 정정. 예상 diff 약 150줄(테스트 포함), 4개 파일.
