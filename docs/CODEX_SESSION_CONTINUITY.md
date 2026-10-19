# Codex session checkpoints / Codex 세션 체크포인트

Status: IMPLEMENTED LOCALLY, SYNTHETICALLY TESTED; REAL PROVIDER RESUME UNVERIFIED. Reviewed 2026-10-19. This is an opt-in implementation increment on the proposed TalkAbout product, not a deployment or completed multi-runtime PoC.

## 사용 범위 / Scope

`run` and `connect` accept `--persist-provider-sessions` for Codex only. The default remains `exec-json`, persistence off, and `--max-model-calls=1`. The existing app-server adapter is selectable only with both `--codex-transport=app-server` and `--experimental-app-server`; this does not verify a real connection or enable additional tools.

현재 실제 어댑터는 검증된 고정 provider 계정 식별자를 제공하지 않습니다. 이메일, 로그인 방식, 홈 디렉터리로 계정을 추정하지 않습니다. 새 세션의 첫 완료는 저장할 수 있지만, 같은 범위의 후속 자동 재개는 `provider_session_identity_unavailable`로 차단됩니다. 재시작 전후 모두 이 제한이 적용됩니다. 합성 테스트의 고정 계정 식별자는 실제 계정 검증 근거가 아닙니다.

The actual adapter currently supplies no verified stable provider account subject. A fresh turn can be checkpointed, but subsequent automatic resume in that scope is blocked, including within the same worker. Email, auth mode and home paths never substitute for identity. There is no manual account-label override. The [official app-server documentation](https://learn.chatgpt.com/docs/app-server) describes account metadata and thread/turn operations; that metadata is not treated as proof of a stable account subject.

## Budgets / 호출 한도

`--max-room-calls=0..10` optionally limits room-purpose invocations within the existing total ceiling. It never increases the total. A fresh connection check normally consumes one total call; with the default total of one, no room call remains. Reusing a valid connection receipt consumes no new check call. CLI output reports check calls, remaining total calls, remaining room calls and the next action. These are external CLI invocation counts, not measured provider token usage or provider-internal retry counts. The GUI connection command is unchanged in this increment.

연결 확인과 방 발언의 한도를 구분하되 총 기본값 1은 유지합니다. 실제 호출이나 credential 발급을 이 문서의 예제로 실행하지 않습니다. 실제 접속 검증은 별도의 사용자 승인과 기존 실행 경계를 따라야 합니다.

## Local recovery / 로컬 복구

- The existing journal OS writer lease owns checkpoint writes. A second owner or concurrent same-room turn is rejected. SQLite settings hold the new namespace; no server API, DB schema migration or dependency was added.
- Scope binds credential/actor digest, verified executable/profile/version/transport, verified provider subject when available, room, generation and membership revision. Changed bindings fail closed; newer room epochs start fresh, stale epochs are rejected. Different rooms never share native IDs.
- `call_started` is committed before dispatch. Only a confirmed completed runtime turn can create a completed checkpoint. Stop requests and local process shutdown are recorded separately from provider completion. Interrupted or uncertain turns remain blocked; no automatic model recall occurs.
- Existing Worker recovery remains authoritative: `call_started` becomes `uncertain`; a durable `result_saved` response is resent with the same completion key after a lost submission response, without another model call. A repeated last task cannot be rerun through a native checkpoint.
- Native session/thread IDs are private local checkpoint data. CLI status exposes only counts and safe reason codes; no IDs, prompts, raw credentials, account subject or executable paths. Digests still require local journal access protection. This increment neither reads provider auth/session files nor changes authentication, billing or tool policy.

정지 직후 도착한 결과는 자동 제출하지 않고 불확실 상태로 남깁니다. 로컬 종료 성공만으로 provider 작업 종료를 확정하지 않습니다. 방 revision과 membership은 기존 서버 fencing을 따르며, 재개용 로컬 상태가 ACL을 대체하지 않습니다.

## Verification and remaining decisions / 검증과 남은 결정

Synthetic tests cover restart continuity with a fake verified subject; unavailable or changed identity; credential/profile/version changes; room and epoch isolation; same-room concurrency; lease ownership; interrupted/late completion; secret-safe status; saved-result submission recovery; both call ceilings; and the experimental transport gate. See the [validation record](validation/scoped-session-continuity-2026-10-19.json).

Actual Codex/Claude/Grok calls, new trial credentials, real account continuity, real reconnect and multi-runtime PoC: **not performed / unverified**. A trusted stable-identity interface must be established before enabling actual automatic resume. Full product acceptance, server-side durable session coordination and a GUI continuity control remain future work. No merge or deployment is part of this increment.

Follow-up local review: [stage-by-stage readiness and the cancellation correction](CODEX_CONTINUITY_REVIEW.md), with [full regression evidence](validation/scoped-session-full-review-2026-10-19.json). This later full run is distinct from the original 174-case partial regression above.
