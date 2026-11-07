# R7 권고 적용 후속 구현

- 작업 기준: 2026-11-07, R7 비교 보고서의 P0 권고. (분석 시점은 2026-11-07; 구현·검증은 사용자 요청 뒤 이어진 세션에서 수행.)
- 범위: bounded pull worker에서 바로 보완 가능한 stop/result 저장 경합만 수정·테스트한다. 비교 실험 프레임워크, 새 provider adapter/session bridge, external board 연동, filesystem sandbox는 미구현이며 이 문서는 추가 제품 범위 승인이 아니다.

## 발견한 회귀 위험

`Worker.step()`은 provider가 반환한 결과와 동일 `complete_key`를 journal의 `result_saved`로 내구 저장한 다음 complete를 제출한다. 저장 이후 그러나 제출 전 stop이 관찰되면 예전 경로는 상태를 `stopped_before_submit`으로 종료했다. 이 상태는 `Journal.next()`에서 제외되어 `--resume-worker` 이후에도 이미 실행된 call의 저장 결과를 찾을 수 없었다. 새 provider call은 하지 않았지만, 성공한 runtime 결과를 복구할 수 없는 terminal row로 잃었다. 이는 stop safety를 유지하면서 해결할 수 있다. `result_saved`는 그대로 두고 publish를 멈춰야 한다.



## 변경

1. **저장된 결과 보존:** runtime 결과가 `result_saved`로 커밋된 뒤 stop을 확인하면 worker는 `result_pending`을 반환하고 journal row와 complete payload/key를 유지한다. 이 표시는 외부 cancel 성공도 result publication도 주장하지 않는다. stop flag가 켜져 있는 동안 다음 invocation은 result_saved가 있어도 제출하지 않는다. 명시적 local `--resume-worker` 후에는 같은 body/key로 complete만 재전송하고 provider runtime은 다시 호출하지 않는다.

2. **회귀 테스트:** `tests/test_review_regressions.py`는 provider 결과의 durable-save 이후, publish 이전에 stop을 주입해 result row 보존, stop-flag 상태에서 publish 차단, 명시 resume 후 동일 complete payload/key 및 단 한 번의 provider call을 검사한다.

## 보존한 안전 경계

- pause/close 및 server-side generation/membership fence는 여전히 외부 effect rollback을 뜻하지 않는다. Stop은 새 전송을 막는 로컬 신호이지 원격 실행 종료 증거가 아니다.
- `/start` 요청 후 불명 응답·runtime timeout/crash는 `uncertain`이며 자동 재시도 금지. 응답 유실 뒤 같은 attempt의 reservation이 여전히 `reserved`로 확인되면 그 start 요청은 commit되지 않은 상태로 판정하고, 새 입력 manifest를 같은 idempotency key로 재전송하지 않는다. worker는 기존 attempt를 `pre_call_retry`로 retire한 뒤 정상 lease expiry/requeue를 기다린다. 재시도는 새 attempt/reservation identity로만 가능하다.
- 성공한 runtime completion과 게시된 방 메시지/adopted artifact는 서로 다른 상태다.
- 사전 start 예약은 아직 call authorization이 아니다. provider 비용·실행은 오직 서버의 명시 `execute:true` start 응답 뒤에 시작한다.

## 검증 상태

- Targeted worker/reservation/stop suites after the latest pre-call reconciliation change: **38 passed** (`tests/test_worker_reservations.py`, `tests/test_worker_api.py`, `tests/test_review_regressions.py`, `tests/test_journal_stop_flag.py`) using ephemeral `uv run` from locked requirements.
- `tests/test_review_regressions.py::test_stop_after_runtime_result_saved_preserves_exact_result_for_explicit_resume` also passed in the focused run.
- Targeted `tests/test_required_predecessor_server.py` and `tests/test_required_input_failure.py`: 22 passed, 2 PostgreSQL cases skipped because no local `initdb`/isolated cluster was available.
- `git diff --check`: passed. Documentation contract: `docs_check 0 new, 0 known` (rerun after final report edit).
- Existing Starlette/httpx deprecation warning only.
- Local `tools/run_safe_regression.py` full guarded run after CI fixes: **Python 2087 passed, 136 skipped, 7 deselected, 0 failed**; Node suite exit 0 (see the per-test `node.log` for exact count). PostgreSQL-backed tests were skipped because this local invocation had no isolated `--postgres-config` fixture; CI creates an ephemeral PostgreSQL cluster. CI also has a separate POSIX containment job.
- Upstream repository test suites, live providers, PostgreSQL concurrency and deployment were not run. No production calls, external credentials, DB access, merge, commit or deployment.

## 2026-11-08 Implementable P0 follow-up

- HTTP runtime shutdown previously returned `True` after closing its local client even when a provider POST was still in flight. `bounded_call` interpreted truthy shutdown as confirmed stop; Worker could mark that attempt `cancelled` despite lacking provider cancellation acknowledgment. `HttpChatRuntime` now tracks and serializes the in-flight request, refuses to close the client underneath it, and returns false from shutdown while request is outstanding. A cancellation observed after response records `outcome_unknown`; Worker reports `uncertain`/`stop_unconfirmed`, not confirmed remote stop. Synthetic loopback tests cover adapter timeout and worker stop after request dispatch. No real provider used.
- Reservation recovery handles a `409` after lease expiry only from current task projection. For a regular task requeued with same attempt/fences while reserved-only, worker resumes from that exact `queued` projection and lets the next claim create a fresh attempt/reservation. Same-fence leased-but-not-yet-reconciled state remains `retry_wait`; changed fence, plan-node, cancellation or other terminal states fail closed. Never infer `reserved` from generic `409`, and never replay `start` with rebuilt input manifest.
- Stop after server reservation but before `/start` now retains `start_pending`; local stop does not cancel server reservation. Recovery remains explicit and reports the server's current reservation state.
- Added response-loss replay contract for co-creation artifact: one version/message, no adoption before owner action. Corrected stale state list and start recovery text in `docs/REFERENCE_WORKER.md` and `docs/RUNNER_RESERVATIONS_PLAN.md`.
- Focused R7 suites after follow-up additions: **115 passed, 1 skipped** across worker/start, HTTP runtime/escort, saved-result, co-creation, and plan reservation budget tests. The platform skip is explicit.
- Full local guarded runner after these changes: **Python 2090 passed, 136 skipped, 7 deselected, 0 failed; Node 430 passed, 0 failed**. PostgreSQL fixture/Windows privilege skips are environmental; no provider calls were made. Docs check and `git diff --check` are rerun before finalization.

## 2026-11-07 Safe regression CI follow-up

The prior pushed `c6761d6` GitHub `Safe regression` failure had 12 cases in three groups: 9 PowerShell helper tests, 2 JavaScript MIME checks, and one scheduler test with a fixed-sleep race. The direct root cause was isolated to each:

- PowerShell helper behavior tests intentionally use synthetic inputs, `-DryRun`, loopback URL and a temporary state directory. The CI audit hook runs inside pytest's process and correctly rejected `pwsh` as an unapproved executable. Added a temporary in-process audit context active only around those tests' `subprocess.run` calls; it always restores the strict default after each launch. The context does not bypass private-account-path or non-loopback socket guards. The test invocations themselves remain dry-run with synthetic inputs.
- `/web/{name}` used `FileResponse`, whose MIME lookup in the CI image returned `text/plain` for `.mjs`, which browsers reject under `nosniff`. The route now assigns `text/javascript` for `.mjs` and retains suffix-derived MIME for other static assets.
- `test_scheduler_waits_for_owner` slept for a fixed interval after setup completion and raced the scheduler's 500ms cadence. It now waits on an `Event` set by `tick_all`, with a bounded timeout. It still asserts no tick occurred while setup remained locked.

**Local verification:** the exact affected tests passed: helper 20, work-plan MIME 2, scheduler 1. The full guarded local runner on 2026-11-08 completed **Python 2091 passed, 136 skipped, 7 deselected, 0 failed; Node 430 passed, 0 failed**. A later focused verification of the cancellation-result race and provider boundaries passed **135 passed, 1 skipped**. The 136 skips are explicit platform/isolated-PostgreSQL cases; this local run did not create a PG cluster. GitHub CI's POSIX containment job remains separately exercised by its workflow. Final docs check is `0 new, 0 known`; diff whitespace check passed.

These R7/CI fixes were committed and pushed in `cccfc20` and `f4e923d`; this section records additional uncommitted P0 follow-up implementation. Existing user dirty changes in docs/Notion UI were committed separately in `339fe7b` and `1f293c9`.
