# Release evidence and requirements trace

2026-10-25 follow-up: **S1 now has actual evidence for one runtime.** With a memory-backed loopback fixture and Codex CLI 0.164.0 on its native login, the worker-reported connection test passed, two contributions were published as `worker_reported_runtime`, and the reconnect reused the exact receipt with `connection_check_calls: 0`. The trial also found and fixed a 0.164.0 protocol difference (a pre-turn advisory `item.completed`/`error`). The Codex model-start budget is exhausted (6 lifetime); S1 for a second provider and the whole S2 gate remain unverified. See [actual trial record](validation/actual-trial-2026-10-25.json). The 2026-10-17 rows below keep their original dates.

2026-10-20 local follow-up: the matrix below describes its named 2026-10-17 baseline. Exact live-receipt reconnect reuse, safe pre-call recovery and scoped journey evidence now have later local fixture results; see the [current consistency record](validation/predeployment-consistency-review-2026-10-20.json). Actual S1/S2 runtime acceptance remains unverified. Historical PR/approval statements are not current remote-state evidence or execution authorization.

Reviewed: 2026-10-17. Baseline: `d09dcc248f7dccfacdc62f6a7df7a3e45566bd44` (PR10). **DEVELOPMENT ONLY; REAL-RUNTIME RELEASE GATE NOT PASSED**.

PR8, PR9 and PR10 are open drafts. Their unchanged heads are respectively `a4fde4206fb480a2138106a29d9a80e42cd9043a`, `cdb5b66cefd5ebd2efb60bae578b65ee84df2dcc`, and the baseline above. No merge or deployment is part of this work.

`implemented` means a reachable code path exists; `synthetic` means fixtures exercise that path; `actual` requires execution against the named real runtime and environment. An authenticated worker report is evidence from that worker, not vendor attestation. Installation, token issuance, copied commands and mock receipts do not establish actual readiness.

## Fixed vertical scenarios

| Requirement | Implemented evidence | Synthetic evidence at baseline | Actual evidence | Blocker / release gate |
| --- | --- | --- | --- | --- |
| S1.1 Register and authorize one agent | `identity.py` identity/credentials; authenticated human consent UI | `test_identity.py`, connection UI tests | 2026-10-25: room-scoped expiring credential issued through the API in a memory fixture, then revoked | Explicit credential consent; no automatic issuance; token loss requires inspect/revoke/reissue |
| S1.2 Launch runtime from its own computer | `cli.py connect`, `agent_runtime.py probe/preflight`, Windows Job containment | `test_agent_runtime.py`, CLI protocol fixtures | 2026-10-25: native Codex CLI 0.164.0 launched under the Job containment path | Native Codex/Claude executable and supported safety flags required; shell wrappers and POSIX blocked; Grok blocked |
| S1.3 Verify a response | `connector.py verify_runtime` challenge/session receipt; server credential binding and expiry | Challenge mismatch, lost completion, uncertain-call and stop tests | 2026-10-25: worker-reported test passed (receipt `dadf36c2`), not vendor attestation | Worker-reported challenge is not provider attestation; native login state unknown |
| S1.4 Accept room invitation and contribute | `cli.py join`, Core membership/start gate, Worker claim/complete | CLI join; synthetic HTTP/Chrome; durable output replay | 2026-10-25: two contributions published as `worker_reported_runtime` | Room-scoped permission and accepted membership; active worker plus valid receipt before start |
| S1.5 Reconnect without duplicate work | Journal durable inbox, random claim keys, saved-result replay, exclusive journal writer | `test_worker_api.py`, `test_worker_ownership.py`, `test_connector_verification.py` | 2026-10-25: reconnect reused the exact receipt with `connection_check_calls: 0`; no duplicate task attempt or message | Uncertain calls must stay blocked; a changed profile or expired receipt still needs a fresh test |
| S2.1 Two different runtimes converse | Per-room/generation/membership runtime pool; bounded scheduling | Individual adapter protocol tests and mode scheduling; no single baseline real two-runtime acceptance record | NOTRUN | Need a combined two-agent scenario and actual Codex + Claude approval; never substitute two synthetic agents as actual |
| S2.2 Pause/recover and reject stale output | Room generation invalidation, lease heartbeat, completion fences, stop journal | Mode pause and worker pre-call fence tests | NOTRUN with real provider processes | CLI currently exits on any stale/unavailable task, including confirmed pre-call cancellation; test safe continuation separately from uncertain post-call failure |
| S3 Read an attachment | Actual UTF-8 bytes/private storage/API, immutable reference, current consent/lease bounded reads | PR10: 406 Python/112 Node, Chrome KO/EN, new temporary PostgreSQL | Actual HTTP/browser/storage yes; actual model processing NOTRUN | Images/PDF/Office, malware scan and physical purge unimplemented; default disabled; preserve this follow-up |
| S4 Isolated command/MCP | Internal memory policy and fake adapters only; runtime model tools disabled | Foundation unit tests only | NOTRUN / NOT CONNECTED | No arbitrary command or external MCP execution approved; preserve design, do not activate |

## Change budget and completion criteria

1. Reproduce S1/S2 defects with static/synthetic evidence before changing code. Keep actual calls behind explicit user approval.
2. Fix proven reconnect and pause/recovery blockers while retaining fail-closed unknown-call handling, duplicate writer exclusion, credential isolation, Grok/POSIX restrictions and provider budget ceilings.
3. Add an integrated S1/S2 synthetic acceptance scenario, including exact-once result replay and pause-before-call recovery; label its evidence synthetic.
4. Explain only those reachable actions in KO/EN onboarding: which computer owns the runtime, supported environment, one next step, current receipt/time/failure, invitation and response evidence. Do not claim completion based on identity or token alone.
5. Run appropriate regressions and real Chrome desktop/mobile against new synthetic fixtures. No operating mutation, real provider call, push, merge or deployment.
6. Return exact local commit and the minimum real trial command, transmitted content, invocation cap, isolation boundary and billing limitation. **S1/S2 actual release completion remains BLOCKED until explicitly approved real trials pass.**

The large proposed tool/file roadmap is not a completion claim. UI appearance is not a release gate substitute. Existing attachments and MCP proposals remain preserved.

## Implemented resolutions in this review branch

| Trace | Resolution and regression evidence | Remaining actual gate |
| --- | --- | --- |
| S1.5 / R1 | `connector.verify_runtime` reuses only the exact completed local credential/runtime binding, authentication mode, transport, executable/version fingerprint and current unexpired server receipt ID. Only a digest of the local profile is persisted. The original credential journal key is retained so an upgrade cannot bypass a legacy uncertain call. Reconnect makes zero new challenge calls only for a matching profile. Expiry, replacement, another token, changed profile and explicit retry require a fresh test; saved/unknown calls stay blocked without explicit retry. Dedicated profile/reconnect regressions cover these cases | Actual native-provider reconnect NOTRUN; native session continuity across process restarts is not claimed. Worker resumes platform journal and uses bounded current room context |
| S2.2 / R2 | `Worker.safe_to_continue_after` and CLI drain only known pre-call stale/unavailable entries. A claim lost to pause/generation change is retired without a model call. Post-call uncertainty, stop and heartbeat errors remain blocked. Integrated CLI pause/claim-race recovery and two labelled synthetic-worker dialogue tests added | Actual two-provider dialogue and pause/reconnect NOTRUN. No claim of real in-flight cancellation |
| S1.3–4 / R3 | Core derives each new agent message's execution classification from current authenticated connection/receipt, not submitted metadata. Participation aggregates are limited to current membership/history/generation and receipt ID. Legacy/unknown and synthetic messages never become real-provider proof. Tests cover forged metadata, receipt replacement and generation reset | Authenticated worker reports remain self-reported runtime evidence, not vendor attestation |
| S1 onboarding / R4 | Runtime choice precedes identity registration. Existing handles reopen their journey without duplicate registration. A room selector, one next action, last contact and failure state explain invitation/acceptance/response/recovery. The current server state is re-read after reload. Only agent/room IDs and runtime choice are kept in the URL; credentials and API billing consent are not persisted. Room/credential/start actions remain explicit | This is development guidance for proven code paths, not a completed actual-runtime user acceptance test |

Execution-environment inspection was limited to PATH metadata: Windows `nt`; native executables named Codex, Claude and Grok were found without invoking them. Versions, safety-flag support, login status, account files and inference remain uninspected/NOTRUN. An installed Grok executable does not remove the Grok isolation gate. See [minimum actual trial and cost boundary](REAL_RUNTIME_TRIAL_PLAN.md).

개발 코드와 합성 검증의 통과는 실제 사용 릴리스 완료가 아닙니다. S1/S2의 실제 검증은 승인 대기이며, 구현된 수정·테스트·실제 실행 결과를 같은 열에 섞지 않습니다. S3 첨부와 S4 도구/MCP 후속은 이번 수정에서 확대하거나 활성화하지 않았습니다.

## Development verification result

See the [sanitized validation record](validation/onboarding-readiness-2026-10-17.json). The initial full run on a new temporary PostgreSQL cluster passed 418 cases and skipped one POSIX-only case; one new EN Chrome assertion expected the wrong invitation label. After correcting that test, both KO/EN Chrome cases passed. The final focused synthetic S1/S2/worker regression passed 44 cases, including six new profile/legacy-recovery cases. Overlaying these current results verifies 425 unique Python cases, one skip and no unresolved failures; this is not a second all-green full-suite invocation. Node passed 130 cases. No real provider, operating deployment, existing database, account file, push or merge was used.
