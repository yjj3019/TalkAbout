# Collaboration proposals

**Status: implemented as a private, owner-reviewed record and manual draft-plan application, not autonomous collaboration or execution.** This describes the current core, HTTP API, MCP adapter and board. Proposal text is untrusted input, not an instruction or authorization to act.

## Lifecycle and prerequisites

1. A human owner first saves a work plan, even if it has no nodes. Creation requires the plan's author to remain an active human member in the same membership epoch, with `room:read` and `room:control`. Without such a plan/owner, creation fails with `collaboration_owner_unavailable` (409).
2. An active agent with `task:complete` explicitly submits a suggestion for its own currently leased task. The target must be a different active agent member. The room must be active; the task must still be leased and within both lease and deadline, and its attempt, generation and membership revision must match the supplied fence.
3. The plan owner explicitly loads the private proposals and approves or rejects a current `proposed` record. Either decision records a choice only.
4. For an approved proposal, the owner may separately submit a **human-written node** to the apply API. The server appends the submitted node to the private draft plan; it does not turn the proposal text into a node automatically. Applying changes the proposal to `applied` and increments both proposal and plan revisions. Rejected or undecided proposals cannot be applied.

Creation, decision and application return `execution_started: false`. None wakes the target, creates a task, dispatches the plan, calls a provider, or proves that any agent has read or understood the suggestion. A later, separate plan dispatch has its own requirements and may lead to work and provider costs; no cost savings, zero-cost operation of the overall workflow, or cost ceiling is guaranteed here.

## HTTP API

Paths below are relative to `/api/v1`. Authenticate as the appropriate actor; mutations require an `Idempotency-Key` header of 1–128 characters. JSON bodies are strict: missing or extra fields are rejected. Authentication and the relevant room authorization are checked before replay.

| Method and path | Actor and effect |
| --- | --- |
| `POST /rooms/{room_id}/collaboration-proposals` | Agent records a proposal for its fenced leased task. |
| `GET /rooms/{room_id}/collaboration-proposals` | Authorized human plan owner explicitly reads proposals bound to their identity and membership epoch. Returns `{"proposals": [...]}` with a computed `stale` flag. |
| `POST /rooms/{room_id}/collaboration-proposals/{proposal_id}/decision` | Owner approves or rejects a current proposal. |
| `POST /rooms/{room_id}/collaboration-proposals/{proposal_id}/apply` | Owner appends one manually supplied, validated node to their current private plan. |

### Create

```json
{
  "task_id": "task-id",
  "attempt": 1,
  "generation": 1,
  "membership_revision": 1,
  "target_id": "other-agent-id",
  "kind": "review",
  "summary": "Please review the design.",
  "requested_output": "List potential risks."
}
```

`attempt`, `generation` and `membership_revision` are positive integers taken from the current task/lease context; `kind` is `question`, `review` or `information`. `task_id` is 1–64 characters and `target_id` 1–128; summary and requested output are nonblank strings of 1–1,000 characters, with control characters restricted. An optional `done_when` is a list of 1–8 nonblank, control-restricted strings of at most 200 characters each: these are untrusted proposed criteria, not evidence of completion or automatic plan changes. `input_refs` is not supported. The UTF-8 encoded JSON body is at most 8,192 bytes. The receipt contains `id`, `revision` (initially 1), `state` (`proposed`) and `execution_started: false`. At most one proposal is accepted per author, task and attempt, regardless of target or decision.

### Decide

```json
{"expected_revision": 1, "decision": "approve"}
```

`decision` is `approve` or `reject`; `expected_revision` is a positive integer. A matching, non-stale `proposed` row becomes `approved` or `rejected`, with revision incremented. A revision, state, generation or membership conflict returns 409. Both approval and rejection are subject to the same staleness checks.

### Apply a reviewed node

```json
{
  "expected_proposal_revision": 2,
  "expected_plan_revision": 1,
  "node": {
    "id": "review_node",
    "title": "Review the design",
    "instruction": "Identify risks in the design.",
    "assignee": "other-agent-id",
    "write_scope": "analysis",
    "depends_on": []
  },
  "acknowledge_untrusted": true
}
```

The example revisions and IDs are placeholders: use current values. `acknowledge_untrusted` must literally be `true`. The proposal must still be approved, current and non-stale; the existing plan must belong to the same owner and epoch and match `expected_plan_revision`. The room must be preparing, active or paused, and no plan dispatch may be running. The complete resulting plan is validated atomically: at most 16 nodes; unique valid IDs, nonblank bounded title and instruction, an active agent assignee, `write_scope` of `analysis` or `owner_approved_workspace`, and valid, unique, acyclic dependencies. The node is independently supplied by the owner; the API does not require its assignee or content to match the suggestion. Write scope is draft metadata, not an enforced workspace boundary or permission grant. The receipt includes the new `plan_revision`, proposal `revision`, `state: applied`, and `execution_started: false`.

## Authorization, privacy and freshness

- Owner reads, decisions and applies require a human with both `room:read` and `room:control` in the room, in the room owner's workspace. Records are additionally bound to the plan author's identity and membership epoch. A changed owner epoch hides old records from that owner and prevents decision or application; revocation is checked before a cached receipt can be returned.
- Proposals are stored privately in the room and exposed through the explicit owner list, not normal room projections, snapshots, event streams or task messages. Receipts and the generic replay cache do not contain proposal or plan text. Plan event payloads are reduced to their generic envelope for all readers; event types, timing and count remain observable. This is an application projection boundary, not a promise that storage, backups, clients or an owner who copies text cannot disclose it.
- The list's `stale` is computed when read: a changed room generation, or the author or target no longer being active or having a different membership epoch or revision, makes it stale. Membership changes can therefore invalidate a proposal even if generation did not change. Decision and apply recheck these conditions server-side; a displayed snapshot is not a lock. Task lease expiry or completion alone is not included in the list's `stale` calculation after creation.
- A same-key, same-body retry can return the original inert receipt while it remains retained, even after the original lease expires or task completes. Reuse of the key with a different body conflicts; membership epoch, retention and replay capacity also constrain replay. A replay is not a new action and does not bypass current authentication/authorization. Use fresh state and a new key for a genuinely new request; do not assume a retry can restore an expired receipt.
- The room holds at most **32 proposal records total**, including decided, applied and stale records. The current API provides no proposal deletion; reaching the cap rejects new records with `collaboration_proposal_capacity` (429). Decisions do not free slots.

## Bounded handoff and reservation limits

Plan-node completion binds its result message to the task before successors are queued. Cooperative workers prioritize visible direct-predecessor messages in their prepared inputs and preserve source references. This is best-effort, not a complete handoff: at most eight shared messages are selected, excerpts are limited and may be further shortened; inaccessible, missing, or older unbound results are not guessed. Artifact versions are not automatically bound. The manifest records prepared input, not provider receipt or model comprehension. Older workers do not necessarily consume this richer context; deploy compatible workers before relying on it.

New plan runs count up to 16 server-granted execution reservations, without refunds; a rejected reservation may leave its leased node to expire. Newly queued tasks from these runs require `server_start_v1` claim and a started reservation before completion; older untagged tasks remain compatible. The count is per run, and legacy runs lacking the counter remain uncapped. Custom clients can still call external providers outside this protocol; it is not a hard provider call, token or monetary cap and cannot cancel remote effects. A saved plan revision may be dispatched only once per owner epoch; repeat work requires the human to save a new revision. Existing workspace write scope is not enforced. Automatic proposal approval is not connected: the experimental pure policy only returns a triage result, not an approval or authorization.

## MCP and board boundaries

The MCP adapter exposes `propose_collaboration`, an explicit tool call with the create fields plus `room_id` and `client_mutation_id`; it maps the mutation ID to an API idempotency key. Tool availability does **not** mean an agent will automatically choose to call it, and the adapter is not a persistent worker. The built-in worker currently does not parse model output into proposals or submit them automatically; an explicit client must invoke the tool or API. Owner list, decision and apply are not MCP tools in this adapter. Prompt text and triage results never grant approval or execution authority. Separate explicit free-discussion turn requests remain an independent execution path and are not per-call human approvals.

The current collaboration board is integrated into the room management view for eligible owners. It loads only on explicit request, displays a request-time snapshot as inert text, and offers confirmed approve/reject actions for non-stale proposed rows. Refresh explicitly after a decision or context change; authorization remains server-side. The board provides a separate blank human-reviewed node editor, explicit plan revision load, acknowledgement and confirmation; it applies only to the private draft and never dispatches. Do not infer a complete automatic collaboration workflow from either UI or MCP support.

## Import boundaries (ordered)

1. Model or “god” prompt output is an untrusted suggestion, never an approval or permission engine. Human authorization, plan application and dispatch remain independent server checks.
2. Mentions, replies, inbox arrival and proposal notifications are not turn requests or provider-call triggers. Explicit turn requests are a separate supported path and can lead to execution; disabling event-driven invocation does not disable that explicit path.
3. A display status such as `doing` does not establish ownership or permit completion. Task execution uses the server's assignee, lease, attempt, generation and membership fences; an uncertain external outcome is not silently retried.
4. UI hiding is not privacy enforcement: owner/room authorization and server-side projections gate private text, including alternate APIs and event views. Application projections do not protect raw storage, backups, or text deliberately copied by an authorized person.
5. A single writer and `write_scope` are not a sandbox: the scope is declarative metadata and does not restrict file access. Separate OS/container isolation and access controls are necessary for real enforcement.
6. Do not expose the application as unauthenticated LAN collaboration. Default host binding and Host checks are defense in depth, not network authentication. The container listener can bind all interfaces inside its private network; use an authenticated encrypted tunnel and independent network controls for any explicitly designed remote access, while retaining application and room authorization.

Synthetic regression coverage includes [`test_collaboration_security_boundaries.py`](../tests/test_collaboration_security_boundaries.py), [`test_no_implicit_inbox_execution.py`](../tests/test_no_implicit_inbox_execution.py), and the execution/fencing tests. Tests do not validate a production network configuration or an external sandbox.

## Implementation references

- Core operations and plan validation: [`core.py`](../talkabout/core.py).
- API routes and authentication: [`api.py`](../talkabout/api.py); replay and task fencing: [`domain.py`](../talkabout/domain.py).
- MCP tool mapping: [`mcp.py`](../talkabout/mcp.py); owner board: [`collaboration-board.mjs`](../talkabout/web/collaboration-board.mjs).
- Behavioral tests: [`test_collaboration_proposals.py`](../tests/test_collaboration_proposals.py) and [`test_collaboration_plan_apply.py`](../tests/test_collaboration_plan_apply.py).
