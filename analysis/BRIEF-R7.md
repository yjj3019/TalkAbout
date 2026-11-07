# 7개 저장소 비교 분석: autoresearch · harness2 · OpenAlex · bloks · Tutti · session-peer · Artiboard

- 조사 시점: **2026-11-07 (Asia/Seoul)**
- 목적: 요청된 저장소의 구조·실행 모델·권한·증거·테스트 범위를 확인하고 TalkAbout에 옮길 수 있는 제품 개선 방향을 도출한다.
- 수행 범위: 공개 upstream의 고정 commit에서 README, 라이선스, 선택된 핵심 문서·구현·테스트 계약을 정적 검토했다. 실제 upstream 테스트, 모델/provider 실행, 운영 배포는 하지 않았다. 따라서 테스트 언급은 저장소에 정의되거나 포함된 증거이지, 이번 조사에서 실행해 통과시킨 결과가 아니다.
- 코드나 문서 자산을 복사하지 않았다. **설계 아이디어를 언급한 것은 라이선스 적합성 또는 법률 검토가 아니다.**

## 요약

| 저장소 | 무엇인지 | TalkAbout에 가장 관련 있는 것 | 주요 한계·주의 |
|---|---|---|---|
| [autoresearch](https://github.com/karpathy/autoresearch/tree/228791fb499afffb54b46200aca536f79142f117) | 단일 GPU 모델 학습을 반복 개선하는 agent 실험 레시피 | 고정 평가·baseline·입력 범위·결과 장부를 둔 실험 | 안내문은 무한 자율 루프를 권함. 편집한 Python을 로컬에서 실행하며 sandbox 보장 없음. 고정 5분/val_bpb는 학습에 특화 |
| [harness2](https://github.com/google-research/harness2/tree/6868817518876de8fd170c6a8354daca273b6191) | task별 agent harness를 propose-probe-compose로 개선·평가 | 후보 결과를 같은 task/budget으로 비교하고 구성·근거·실패 기록 | 모델 호출·비용이 크고 네트워크 기본 제한 없음. 자동 선택을 TalkAbout에서 사람 채택으로 바꿔야 함 |
| [OpenAlex](https://github.com/ourresearch/OpenAlex/tree/312af8772356104fc80cb4597113a5ab7ce3b027) | OpenAlex 구현 저장소를 나열하는 인덱스 | 각 구성요소의 소스·테스트·라이선스를 따로 검토하는 규율 | 지정 저장소 자체에는 제품 구현이나 LICENSE가 없음. 이름만 보고 일반화 금지 |
| [bloks](https://github.com/hamedgitty/bloks/tree/27494d1e3947ef5efe1ea8bbfdf64e2ba37d71a7) | 로컬 데스크톱에서 native CLI agent를 실행하는 workspace | 명시적 실행 승인, pre/post 변화 비교, 조건부 undo, 감사 장부 | **FSL-1.1-MIT**로 현재 경쟁 사용 제한. source는 sandbox가 아님. provider key를 plaintext 파일에 둔다고 문서화 |
| [Tutti](https://github.com/tutti-os/tutti/tree/207beeea554435618dffd3b579d0ffe112b065c4) | 로컬 데스크톱/daemon 기반 agent·workspace lifecycle | workflow와 canonical agent session 분리, durable dispatch/cancel recovery | OSS와 hosted “VM” 기능 구분 필요. group-collaboration은 OSS 저장소에 있다고 단정 불가 |
| [session-peer](https://github.com/abruption/session-peer/tree/902a5fe1d5d1e612769df16f5a93df0f08aa8dc8) | 로컬/SSH/선택 relay/MCP로 agent 세션 간 1:1 전달 | 제출·수신·소비·ACK·불명 상태를 서로 다른 증거로 구분 | 1:1 inbox 도구이지 room/artifact/scheduler 제품이 아님. 결과 불명은 자동 재전송하지 않음 |
| [Artiboard](https://github.com/yulmucoffee/artiboard/tree/be6d7e5d75b72066f1c46866c8e12d89df75ddc7) | 사람·코딩 agent 공용 task board + MCP | 원자 batch/optimistic revision, 계산된 UI, dependency task view | hosted board 역할은 전역 회원 모델이며 TalkAbout room ACL과 다름. immutable artifact ledger가 아님 |

**적용 결론:** TalkAbout은 이미 `room → task/run → immutable artifact → human adoption`의 상당 부분을 갖고 있다. 새 Kanban, native session bridge, 자율 코드 실행 제품을 중복 구현하기보다, 향후 명시 승인된 실행에만 **제한된 비교 실험 계약**(baseline, 고정 평가, 동일 예산, 후보별 격리 worktree, 비용·실행 근거, 사람 채택)을 보강하는 방향이 유망하다. 인증·fencing·no-auto-retry·artifact adoption 경계는 약화하지 않는다.

## 스냅샷 및 검증 범위

| Repository | 기본 브랜치 / 확인 HEAD | 날짜(UTC) | 라이선스 판정 |
|---|---|---:|---|
| karpathy/autoresearch | `master` / `228791fb499afffb54b46200aca536f79142f117` | 2026-03-26 | README가 MIT 주장하지만 pinned tree에 LICENSE 파일 없음, GitHub SPDX 확인 불가 — **파일로 검증된 SPDX 아님** |
| google-research/harness2 | `main` / `6868817518876de8fd170c6a8354daca273b6191` | 2026-11-02 | Apache-2.0. third-party/dataset 라이선스는 별도 |
| ourresearch/OpenAlex | `main` / `312af8772356104fc80cb4597113a5ab7ce3b027` | 2023-02-24 | 지정 인덱스 repo에는 LICENSE 없음. 하위 component별 별도 검토 |
| hamedgitty/bloks | `main` / `27494d1e3947ef5efe1ea8bbfdf64e2ba37d71a7` | 2026-11-07 | FSL-1.1-MIT: 현 버전에 경쟁 사용 제한, version별 2년 후 MIT future grant |
| tutti-os/tutti | `main` / `207beeea554435618dffd3b579d0ffe112b065c4` | 2026-10-08 | Apache-2.0 |
| abruption/session-peer | `main` / `902a5fe1d5d1e612769df16f5a93df0f08aa8dc8` | 2026-11-05 | MIT |
| yulmucoffee/artiboard | `main` / `be6d7e5d75b72066f1c46866c8e12d89df75ddc7` | 2026-11-06 | MIT |

표는 조사 중 확인한 commit dates이며, default branch와 checked SHA는 GitHub metadata/pages/Atom 또는 `git ls-remote`로 교차 확인했다. REST rate limit 때문에 모든 응답을 반복 확인할 수는 없었다. 코드는 pinned commit의 선택 파일을 정적으로 살폈다. clone/build/upstream test/model/provider/운영 실행은 하지 않았다.

## 저장소별 분석

### 1. autoresearch

**목적·형태:** 모델을 크게 바꾸지 않고 작은 연구 루프를 빠르게 도는 단일 GPU LLM 학습 예제다. README가 핵심 세 파일을 구분한다. `prepare.py`는 고정 데이터·평가 유틸리티, `train.py`는 agent가 수정하는 모델/optimizer/training loop, `program.md`는 사람이 편집하는 agent 지침이다. 학습 예산은 300초이며, `val_bpb`가 낮을수록 좋다. 이는 같은 실행 환경에서 후보를 비교하기 위한 값이지 하드웨어 간 보편 benchmark라는 주장은 아니다.

**흐름:** 새 run tag/branch, 데이터 준비, 결과표 header와 사용자 확인으로 시작한다. 지침은 `train.py`만 편집하고 평가·상수·dependency는 고정, baseline 먼저라고 한다. 후보를 git commit 후 log로 실행하고 점수·VRAM을 읽어 `keep`/`discard`/`crash`를 TSV에 기록한다. 개선이면 commit을 유지하고 아니면 시작 commit으로 되돌린다. 10분을 넘으면 실패로 간주한다. 하지만 같은 지침은 setup 이후 무한히 중단 없이 계속하라고 한다.

**한계:** 편집한 Python을 로컬에서 바로 실행하며 저장소 자체의 OS sandbox/egress 경계가 없다. “권한을 끄고 실행”은 README의 운영자 권고이지 code-enforced containment가 아니다. 고정 validation shard 파일명에도 불구하고 download URL의 `resolve/main`은 moving branch다. README는 MIT라 하지만 inspected tree에 LICENSE나 SPDX 선언이 없으므로 법적 상태는 별도 확인 전 미확정이다. 테스트/CI suite를 발견하지 못했다; 실험 metric/log와 notebook은 regression test가 아니다.

**TalkAbout 적용 권고:** finite scope와 baseline, frozen evaluator/data revision, bounded deadline/budget, candidate별 isolated checkout, commit/input/evaluator hashes를 연결한 attempt record. `keep/discard/crash/timeout/uncertain`은 명시적 terminal states로 기록하고 run summary는 artifact로 남기되 사람 채택과 plan dispatch는 분리한다. user의 uncommitted checkout에는 reset/revert하지 않는다. `5 min`이나 `val_bpb`를 일반 코드 작업에 강제하지 않는다.

**근거:** [README 개요/측정](https://github.com/karpathy/autoresearch/blob/228791fb499afffb54b46200aca536f79142f117/README.md#L7-L17), [README 설계 선택](https://github.com/karpathy/autoresearch/blob/228791fb499afffb54b46200aca536f79142f117/README.md#L61-L69), [program.md setup·scope·baseline](https://github.com/karpathy/autoresearch/blob/228791fb499afffb54b46200aca536f79142f117/program.md#L7-L39), [program.md 결과·반복 루프](https://github.com/karpathy/autoresearch/blob/228791fb499afffb54b46200aca536f79142f117/program.md#L64-L112), [prepare.py 데이터/예산](https://github.com/karpathy/autoresearch/blob/228791fb499afffb54b46200aca536f79142f117/prepare.py#L30-L88), [train.py 평가 종료](https://github.com/karpathy/autoresearch/blob/228791fb499afffb54b46200aca536f79142f117/train.py#L600-L625).

### 2. Google Research Harness²

**목적·형태:** 모델보다 agent의 harness를 개선하는 framework다. system prompt, guardrails, memory, tool guidance, subagents, skills, scripts, plugins를 편집 가능한 component로 둔다. Task recursion은 **Propose–Probe–Compose**: 후보 harness 변경 제안 → 같은 task에 실행해 관측 → 실행 evidence 기반 composition이다. Harness recursion은 adopted harness와 근거가 있는 lesson을 이후 task에 반영한다.

**흐름·재현성:** parallel recursion은 일반/domain 후보를 폭넓게 탐색하고 sequential은 이전 evidence를 누적한다. Domain 내부 task는 순차, domain끼리는 parallel 가능하다. dry-run/task/domain 제한/seed/k/budget 옵션 및 benchmark/executor/mode/model/task identity가 저장된다. 조건이 달라진 run은 같은 namespace에서 조용히 resume되지 않도록 막는다. Judging/reporting은 refinement와 분리하며, failed evaluation은 zero 점수가 아니라 unscored 상태다.

**한계:** repository docs는 executor 네트워크가 unrestricted이고 Codex sandbox가 disabled임을 공개하고 host firewall/DNS 수준 egress restriction을 권고한다. grader/rubric 외부 접근은 evaluation을 오염시킬 수 있다. Scripts/plugins는 harness-tree validation을 통과해도 실행코드다. `--max-tasks`는 전체가 아니라 **domain별**이며 `k` 및 domain 병렬성으로 API 비용이 빠르게 늘 수 있다. Ledger의 rollout/improver budget 상수는 workload budget model이고 측정된 실제 call count와 혼동하지 않는다. Apache license는 `third_party/`/dataset LICENSE를 포괄하지 않는다.

**TalkAbout 적용 권고:** 안정적인 평가기 있는 업무에만 future bounded candidate comparison. Input/task/evaluator/runtime identity fingerprint와 baseline/attempt artifacts, 명시된 cost ceiling, dry-run preview, 동일 조건 비교, nonzero fail/unscored state, human-only adoption. TalkAbout은 owner review/apply/dispatch/adopt를 분리하므로 Harness²의 autonomous selector를 채택 권한으로 두지 않는다. 현재 work-profile/worker는 sandbox, unrestricted scripts, egress firewall를 뜻하지 않는다.

**근거:** [README component model](https://github.com/google-research/harness2/blob/6868817518876de8fd170c6a8354daca273b6191/README.md#L68-L78), [README run and cost flow](https://github.com/google-research/harness2/blob/6868817518876de8fd170c6a8354daca273b6191/README.md#L110-L127), [README recursion/results](https://github.com/google-research/harness2/blob/6868817518876de8fd170c6a8354daca273b6191/README.md#L140-L181), [EXPERIMENTS modes](https://github.com/google-research/harness2/blob/6868817518876de8fd170c6a8354daca273b6191/docs/EXPERIMENTS.md#L8-L42), [task/cost/reproducibility](https://github.com/google-research/harness2/blob/6868817518876de8fd170c6a8354daca273b6191/docs/EXPERIMENTS.md#L44-L85), [egress/resume failure behavior](https://github.com/google-research/harness2/blob/6868817518876de8fd170c6a8354daca273b6191/docs/EXPERIMENTS.md#L93-L123), [run identity check](https://github.com/google-research/harness2/blob/6868817518876de8fd170c6a8354daca273b6191/harness2/cli/run_sweep.py#L209-L235), [Apache and third-party notices](https://github.com/google-research/harness2/blob/6868817518876de8fd170c6a8354daca273b6191/README.md#L199-L209).

### 3. OpenAlex (지정된 repository)

**범위 오해 방지:** `ourresearch/OpenAlex`는 service source monorepo가 아니라 **7개 separate repository를 안내하는 README index**다. 그 README에 OpenAlex scholarly catalog 소개와 `openalex-guts`, `openalex-elastic-api`, docs, website, tutorials, ngrams, elasticsearch 링크만 있다. root에는 implementation tree, tests, LICENSE, `pyproject.toml`, `package.json`이 없다. GitHub도 root SPDX를 선언하지 않는다. 이 repository 하나만 보고 OpenAlex system architecture/security/test/license를 말하면 안 된다.

Follow-up으로 연결된 `openalex-guts`만 sample 정적 확인: root MIT LICENSE 및 README, SQLAlchemy models와 merge/snapshot/tracking directories가 있다. `merge_work.py`는 개별 pair 또는 CSV로 old ID를 target ID에 merge하고 source row의 merge fields 및 dependent record-thresher references를 갱신하는 maintenance script다. 이 한 파일은 transaction/precondition/error-recovery contract가 없고 SQL을 string interpolation하므로 generic safe merge pattern의 근거가 아니다. 다른 child repos는 개별 audit하지 않았으며, sampled repos마다 license 상태가 다를 수 있다.

**TalkAbout 적용 권고:** index/umbrella repo를 구현 source로 오인하지 않고 dependency/source provenance를 분리하고, external repo license/commit/tests를 component별로 확인한다. TalkAbout artifacts/agents/tasks에는 immutable IDs를 우선한다. Future external object rename/merge mapping이 필요할 때만 explicit alias/redirect mapping + audit/revision을 둔다; text similarity로 artifact/principal/version을 자동 병합하지 않는다.

**근거:** [OpenAlex root index README](https://github.com/ourresearch/OpenAlex/blob/312af8772356104fc80cb4597113a5ab7ce3b027/README.md), [openalex-guts README](https://github.com/ourresearch/openalex-guts/blob/8b0e87d0589abb75e03b66dbc7e6b6a00896688f/README.md), [openalex-guts LICENSE](https://github.com/ourresearch/openalex-guts/blob/8b0e87d0589abb75e03b66dbc7e6b6a00896688f/LICENSE), [sample merge_work](https://github.com/ourresearch/openalex-guts/blob/8b0e87d0589abb75e03b66dbc7e6b6a00896688f/merge/merge_work.py#L8-L65).

### 4. bloks

**목적·형태:** local-first Electron desktop workspace with native provider CLIs, rooms, tools, local files, skills, memory, approval cards, optional server/cloud and paired devices. “Local” only describes where some workspace state is hosted; configured LLM/provider, voice and connector data still leave to those vendors/services.

**workflow:** provider risky action presents command/file/diff/page and blocks pending human response. Rules run before action, deny overrides allow. Before/after filesystem snapshots show diffs; conditional undo leaves later human/agent edits intact; rehearsal uses copy. Memory is readable Markdown with change journal and undo; Skill proposals are reviewed rather than silently replacing existing library. Consequential events may be hash chained.

**risks/license:** project labels hash chain “tamper-evident, not tamper-proof”; an actor who rewrites entire file can recalculate absent external signature/anchor. Security doc says workspace API keys are in `0600` plaintext `config.json` under `0700`, not encrypted at rest. It explicitly states no sandbox between an agent and rest of host beyond provider CLI unless user configures container. Undo/history caps mean some edits are untracked/unundoable. Current license FSL-1.1-MIT imposes a competing-use restriction; do **not copy source** into TalkAbout absent legal review/permission. These are project docs, not independent audit.

**TalkAbout fit:** if an explicitly approved filesystem work mode is added, bind approval to exact command/target/diff/scope; provide compare-before-restore rollback that skips modified paths and reports incomplete undo. A local hash chain is only tamper-evidence unless externally anchored. This reinforces existing TalkAbout boundary that `write_scope` is not OS sandbox. Never import plaintext-secret storage design. Given current TalkAbout is text artifacts and reference worker, this remains conditional, not an immediate file-undo feature.

**Sources:** [README rooms/agents](https://github.com/hamedgitty/bloks/blob/27494d1e3947ef5efe1ea8bbfdf64e2ba37d71a7/README.md#L283-L318), [README approvals/undo](https://github.com/hamedgitty/bloks/blob/27494d1e3947ef5efe1ea8bbfdf64e2ba37d71a7/README.md#L366-L426), [README memory/skills](https://github.com/hamedgitty/bloks/blob/27494d1e3947ef5efe1ea8bbfdf64e2ba37d71a7/README.md#L431-L463), [FSL license](https://github.com/hamedgitty/bloks/blob/27494d1e3947ef5efe1ea8bbfdf64e2ba37d71a7/LICENSE), [ledger limitation](https://github.com/hamedgitty/bloks/blob/27494d1e3947ef5efe1ea8bbfdf64e2ba37d71a7/server/ledger.ts#L1-L18), [memory compare-before-undo](https://github.com/hamedgitty/bloks/blob/27494d1e3947ef5efe1ea8bbfdf64e2ba37d71a7/server/memory-journal.ts#L1-L20), [filesystem rollback](https://github.com/hamedgitty/bloks/blob/27494d1e3947ef5efe1ea8bbfdf64e2ba37d71a7/server/checkpoints.ts#L565-L612), [security boundary/secrets](https://github.com/hamedgitty/bloks/blob/27494d1e3947ef5efe1ea8bbfdf64e2ba37d71a7/SECURITY.md#L12-L52), [security approvals/no host sandbox](https://github.com/hamedgitty/bloks/blob/27494d1e3947ef5efe1ea8bbfdf64e2ba37d71a7/SECURITY.md#L141-L152).

### 5. Tutti

**OSS product boundary:** checked repo is an Apache-2.0 local-first desktop monorepo/Go daemon with provider-neutral Go Agent Host. Its own README differentiates OSS and commercial Tutti·VM: do not assume hosted multi-user/group collaboration from this source. Code ownership guidance keeps product state/persistence in `services/tuttid`, lifecycle in `packages/agent/host`, UI/transport in desktop packages; this avoids two competing business cores.

**lifecycle:** Workspace Issue/task/run and canonical Agent Session/Turn/runtime operation are separate authorities; state is not mirrored. Current accepted graph revision projects actionable items; stale/pending/rejected graphs don't execute. Generic dispatch durably claims Run while holding Issue lock, releases lock before worktree/session launch, and settles launch failures through idempotent completion. Stop records pause intent first, then cancels the exact canonical turn, and only reports cancellation from canonical evidence. If cancellation fails, state remains running and dispatch paused; it doesn't fabricate success.

**recovery/races:** deterministic external submit ID plus lookup on lost response, not fresh re-send; cross-process owner leases/CAS and durable compensation for stale-launch cancellation; failed cancellation stays pending/retried. Event contracts are schema-first, typed by scope, generated for clients, with authoritative snapshots and revisioned deltas. Session Replay crosses actual UI/HTTP/Host/runtime/provider adapter and substitutes only provider process with a bounded cassette; replay hashes/inventory/size/isolation detect some fixture problems but are not live-provider, credential, billing or visual verification.

**TalkAbout fit:** fault-inject claim→launch→response loss→stop/cancel→restart and preserve exact operation IDs/uncertain states. If an external Issue provider is added, keep it as separate linked domain with reconciliation, not duplicate room/task truth. Schema generator only worth adding if multiple independent event consumers show drift. Tutti's resident side-session topology is not current TalkAbout’s bounded pull worker; defer until explicit product need.

**Sources:** [OSS-vs-VM README](https://github.com/tutti-os/tutti/blob/207beeea554435618dffd3b579d0ffe112b065c4/README.md#L24-L40), [ownership](https://github.com/tutti-os/tutti/blob/207beeea554435618dffd3b579d0ffe112b065c4/services/tuttid/AGENTS.md#L3-L20), [issue execution architecture](https://github.com/tutti-os/tutti/blob/207beeea554435618dffd3b579d0ffe112b065c4/docs/architecture/issue-execution.md#L1-L69), [stop/race recovery](https://github.com/tutti-os/tutti/blob/207beeea554435618dffd3b579d0ffe112b065c4/docs/architecture/issue-execution.md#L84-L160), [event contract design](https://github.com/tutti-os/tutti/blob/207beeea554435618dffd3b579d0ffe112b065c4/docs/architecture/business-event-stream.md#L11-L25), [replay boundaries](https://github.com/tutti-os/tutti/blob/207beeea554435618dffd3b579d0ffe112b065c4/docs/architecture/agent-session-replay.md#L1-L60), [Agent Host contracts](https://github.com/tutti-os/tutti/blob/207beeea554435618dffd3b579d0ffe112b065c4/packages/agent/host/README.md).

### 6. session-peer

**purpose:** Python CLI, native provider adapters and optional SSH/MCP/relay transport for 1:1 messages between existing Claude Code/Codex/Antigravity sessions. It is a native inbox bridge, not a room board, durable artifact store or server-side scheduler.

**evidence/failure:** session peer keeps distinct submitted/consumption/ACK/unknown. Target generation and provider-specific writer/lock evidence are checked before effects. Preflight is read-only and cannot grant send permission. On native send timeout, response loss, malformed or mismatched remote receipt, preserve unknown/no-auto-retry instead of guessing. Handoff writes durable intent and fences one attempt; late ACK does not rewrite previous timeout to success. Reply text/body is quoted untrusted data; peer device fingerprint binds a route but is not human/model identity or authorization.

**limits/test evidence:** checked CI spans selected Linux/macOS/Windows Python versions and suites target stale generation, malformed receipt and handoff states; tests weren't executed here. Some native receipt support is provider/platform-specific; fake queue test is not proof of actual model consumption. No fanout ordering or shared room/artifact lifecycle. No separate dynamic plugin loading; adapter registry declares required capabilities.

**TalkAbout fit:** preserve the evidence distinctions already in TalkAbout rather than adding a generic ACK state: accepted/queued ≠ worker call started ≠ worker-reported result ≠ human-adopted artifact. If a native session integration is later required, use explicit destination/capability and current target generation; preflight must remain separate from send; sanitize receipts and never auto resend unknown outcomes. Never bypass room ACL, scheduler assignment, durable event log, reservation or fencing by direct inbox send.

**Sources:** [README claim boundary](https://github.com/abruption/session-peer/blob/902a5fe1d5d1e612769df16f5a93df0f08aa8dc8/README.md#L12-L18), [adapter contract](https://github.com/abruption/session-peer/blob/902a5fe1d5d1e612769df16f5a93df0f08aa8dc8/session_peer_core/adapters.py#L10-L79), [sending/unknown outcome](https://github.com/abruption/session-peer/blob/902a5fe1d5d1e612769df16f5a93df0f08aa8dc8/session_peer_core/sending.py#L78-L132), [handoff idempotency/unknown](https://github.com/abruption/session-peer/blob/902a5fe1d5d1e612769df16f5a93df0f08aa8dc8/docs/handoff-runtime.md#L7-L39), [handoff failure tests](https://github.com/abruption/session-peer/blob/902a5fe1d5d1e612769df16f5a93df0f08aa8dc8/tests/core/test_handoff_runtime.py#L80-L185), [paired-device/native ACL distinction](https://github.com/abruption/session-peer/blob/902a5fe1d5d1e612769df16f5a93df0f08aa8dc8/docs/paired-devices.md#L244-L282), [reply framing](https://github.com/abruption/session-peer/blob/902a5fe1d5d1e612769df16f5a93df0f08aa8dc8/session_peer_core/replies.py#L252-L283).

### 7. Artiboard

**purpose:** code-rendered task board usable as hosted page/artifact, with MCP task/dependency/thread/notice and code-map tools. Latest reviewed `main` is `be6d7e5` (2026-11-06). After older R6 snapshot, recent commits include cross-process delete notification, preflight refusal for batches over 100 writes, and replacing fixed-delay page test waits with a real settle condition.

**data/concurrency:** path-addressed documents with per-document version; claim writes task+claim note in an atomic batch with `if_version`. Tests assert one winner under same-task contention. SQLite batches use transaction; Supabase backend uses RPC/realtime. Long task details/thread messages are split from compact task card. Computed board view/counters/graph/code map are projections, not model-generated UI. Errors carry stable code and failing batch index.

**permissions caveat:** hosted roles are global site member/admin/viewer, and all site members are seeded across projects; this isn't TalkAbout per-room ACL. Artifact sharing and UI hide do not substitute server ACL. Artiboard holds external branch/PR pointers, not TalkAbout-like immutable artifact versions/provenance.

**TalkAbout fit:** don't duplicate Kanban/dependencies just to plan. If integration asked, begin with explicit one-way link: board task ID ↔ TalkAbout room ID ↔ adopted artifact ID+revision; keep their status domains separate, no mirror loop. A bridge checks room permission on every read and discloses only authorized metadata. Useful invariants: atomic batch, revision conflict=>reread-and-decide, reject oversized batch before partial effects, computed views, and explicit cursors/delta for cross-process update.

**Sources:** [README board model](https://github.com/yulmucoffee/artiboard/blob/be6d7e5d75b72066f1c46866c8e12d89df75ddc7/README.md#L19-L22), [self-host/MCP](https://github.com/yulmucoffee/artiboard/blob/be6d7e5d75b72066f1c46866c8e12d89df75ddc7/README.md#L94-L112), [flow projections](https://github.com/yulmucoffee/artiboard/blob/be6d7e5d75b72066f1c46866c8e12d89df75ddc7/server/flow.js#L311-L360), [claim CAS](https://github.com/yulmucoffee/artiboard/blob/be6d7e5d75b72066f1c46866c8e12d89df75ddc7/server/flow.js#L469-L520), [task rights](https://github.com/yulmucoffee/artiboard/blob/be6d7e5d75b72066f1c46866c8e12d89df75ddc7/server/tasks.js#L66-L79), [SQLite batch](https://github.com/yulmucoffee/artiboard/blob/be6d7e5d75b72066f1c46866c8e12d89df75ddc7/server/store/sqlite.js#L187-L240), [delete delta commit](https://github.com/yulmucoffee/artiboard/commit/91a710c756ddf3d27506194daf44a79428bc2320), [latest commit feed](https://github.com/yulmucoffee/artiboard/commits/main.atom).

## TalkAbout 개선안 우선순위

### P0 — 보강하는 것은 증거 테스트, 새 제품 계층 아님

TalkAbout은 이미 [필수 predecessor input](../docs/REQUIRED_PREDECESSOR_INPUTS.md), [owner-reviewed collaboration proposals](../docs/COLLABORATION_PROPOSALS.md), [shared input manifest/hash](../docs/COLLABORATION_CONTEXT.md), [worker journal/uncertain](../talkabout/worker.py), [plan fencing/reservation](../talkabout/core.py), [non-attestation execution receipts](../docs/EXECUTION_RECEIPTS.md)를 갖고 있다.

- Add/keep failure-injection matrix for `queued → claim → runtime start → worker-reported result → artifact publish → human adoption`. Timeout/response loss/restart after external side effect must remain uncertain; do not mark success or allow fresh retry.
- Stop/cancel response must distinguish server's requested stop from actual external cancellation acknowledgment. Do not infer process exit from room state update.
- Input/evaluator/runtime provenance must distinguish worker assertion, server-validated source reference, and provider attestation. A model's “tests passed” or score claim is not execution evidence.

**Safety boundary:** retain room ACL, human owner authority, generation/membership fences, call reservations, human adoption, and no-auto-retry. Direct provider inbox and automatic artifact adoption must not bypass these.

### P1 — conditional bounded candidate comparison

The best new conceptual transfer from autoresearch/harness2 is a **bounded comparison mode** for explicitly opted-in task types with a stable evaluator. This is a recommendation only, not implemented. See also [the worker recovery follow-up](R7_IMPLEMENTATION.md) for a narrow P0 change that preserves `result_saved` work through an explicit stop.

1. Human specifies finite task set/scope, baseline artifact/input fingerprint, evaluator version, provider/runtime version, deadline, model-call and attempt budget.
2. Each candidate uses isolated worktree/checkout. Never reset or overwrite the user's uncommitted working copy. Terminal statuses use fixed vocabulary: `kept_candidate`, `discarded`, `crashed`, `timed_out`, `uncertain`.
3. Record candidate diff, input identity, evaluator/test output, stdout/stderr references, usage only when measured, price only when source price is authoritative, plus reason. Compare only same benchmark/evaluator/task subset/seed/runtime/budget.
4. Dry-run previews selected tasks, cost model/uncertainty, file scope, tool/egress permissions before any paid call. Caps must be server reservations and OS/runtime enforcement—not prompt instructions.
5. Agent may propose and explain. Human reviews the run and separately adopts an artifact. Proposal decision, plan apply, dispatch, and artifact adoption remain distinct.
6. Do not enable parallel candidates until each has isolated workspace, separate identity, concurrency and spend quota; shared checkout makes diffs/cost attribution ambiguous.

**Do not borrow:** indefinite `LOOP FOREVER`; automatic selector adoption; unpriced `k` expansion; `git reset` in user's checkout; unrestricted scripts/plugins; agent-editable evaluator; assuming work-profile is a sandbox. Harness² explicitly warns of unrestricted executor network and disabled sandbox.

### P1 — external task board / session bridge as one-way references

- If Artiboard integration is later requested, begin with explicit link: board task ID ↔ TalkAbout room ID ↔ adopted artifact ID+revision. Do not automatically mirror message streams or two-way task/room states.
- Session-peer-style native inbox send remains out of default product: it can bypass room events, scheduler, assignment ACL and reservation. Only add if a provider use-case is demonstrated and send is explicitly authorized with target/capability/preflight/no-retry-on-unknown semantics.
- Treat OpenAlex as an index, not a single licensed implementation. For any external dependency, pin component repository, revision, license and tests independently.

### P2 — defer multi-turn provider side session

Only revisit Tutti Side pattern if TalkAbout grows a native resident runtime controller and user need is established. Required invariants: separate ephemeral ID/source ID and event stream; explicit provider capability; open request idempotency; per-source/side serialization; buffer transient events until open commits; never write canonical room/artifact/task automatically; provider loss expires with no auto-resume; cleanup only exact created runtime; human promotion explicit. Current bounded pull worker does not need this extra state machine.

## TalkAbout crosswalk

| Surface | Current contract | Question for next design |
|---|---|---|
| [collaboration proposals](../docs/COLLABORATION_PROPOSALS.md) | Owner-only decisions; apply accepts a human-written node, not model text; no dispatch on apply | Show candidate evidence side by side for explicit human review, without auto-select |
| [shared context](../docs/COLLABORATION_CONTEXT.md) | Exact source refs/byte binding; not model-read proof | Bind experiment/evaluator identity to input fingerprint |
| [required predecessors](../docs/REQUIRED_PREDECESSOR_INPUTS.md) | Exact required messages fail closed | Ensure run identity includes full selected predecessor hash set, not just task ID |
| [worker](../talkabout/worker.py) | Durable journal, bounded calls, unknown post-call outcome not auto-repeated | Add fault-injection around every side-effect boundary before new runtime topology |
| [core plan/run](../talkabout/core.py) | Revision/generation/membership fences and reservations; human dispatch | Any experiment mode needs separate run identity/budget; never pretend `write_scope` enforces files |
| [execution receipts](../docs/EXECUTION_RECEIPTS.md) | Worker report is not vendor attestation | Keep score/result claims equally conservative |
| [room memory](../docs/ROOM_MEMORY.md) | Human preview only; no automatic save/injection | No cross-room lessons until source ACL, expiry/deletion and provider transfer gates exist |

## 재현 범위와 주의

- Code inspection and upstream test-contract review only. **No upstream test, build, provider call or production deployment was executed.**
- OpenAlex section covers named root index and selected `openalex-guts` sample only, not every child repo or production system.
- Bloks README/security are vendor-maintained assertions, not independent audit. FSL needs counsel before any derivative/competitive use.
- Autoresearch README says MIT but inspected pinned tree lacks LICENSE/SPDX; legal status unconfirmed before reuse.
- External package/dataset licenses remain separate even when repository root is Apache/MIT.
- Report does not imply product code changes or authorization to execute/merge/deploy. At the time of report creation, pre-existing unrelated local edits remained in `docs/ROOM_MEMORY.md`, `talkabout/web/index.html`, `talkabout/web/state.mjs`, and `tests/memory-preview.test.mjs`.
