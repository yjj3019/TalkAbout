/**
 * TalkAbout private work-plan draft editor (standalone module).
 *
 * Author + author's membership-epoch private draft. It reads and writes ONLY this
 * documented REST contract:
 *   GET/POST /api/v1/rooms/{rid}/work-plan
 *   GET/POST /api/v1/rooms/{rid}/work-plan/dispatch
 *   POST     /api/v1/rooms/{rid}/work-plan/dispatch/abort
 *
 * Saving a draft never dispatches work. A human owner must explicitly click the
 * start/stop controls (with the required confirmations) before any task, provider
 * call or message can be created. There is no automatic execution, polling,
 * retry, re-dispatch, or automatic status GET. A start/abort response is a
 * request-time snapshot only; the panel always says so and requires an explicit
 * "refresh execution status" before another start/stop. Replayed idempotency
 * responses are snapshots too, never proof of the current run state.
 * Draft dependencies do not execute on save; dispatch schedules declared ordering.
 * write_scope does not enforce filesystem access; execution evidence is
 * worker-reported, not server-verified.
 *
 * Host integration contract (app.mjs mounts this module):
 *   import {createWorkPlanView, WORK_PLAN_LABELS} from './work-plan.mjs';
 *   const view = createWorkPlanView({
 *     api,                              // same helper as app.mjs: (path,{method,body,key}) -> data, throws Error with .status
 *     getContext,                       // () => {room, user, authTicket, viewTicket, language} or null; MUST be fresh each call
 *                                       // viewTicket MUST be a PLAN-SCOPED ticket that increments only on a real
 *                                       // room switch / clearDetail / logout. Do NOT pass app.mjs `view.epoch`:
 *                                       // it also increments on every SSE refresh()->openRoom(same room), which
 *                                       // would purge the loaded draft on every event. Membership epoch,
 *                                       // membership_revision and effective permissions are validated separately
 *                                       // by the module on every render/response, independent of viewTicket.
 *     notify, confirm, clipboard, uuid, // optional; defaults use browser APIs
 *     document,                         // optional; defaults to global document
 *     t,                                // optional; when omitted WORK_PLAN_LABELS[language][key] is used
 *   });
 *   view.mount(containerElement);       // atomically resets the previous mount, renders an
 *                                       // unloaded panel and issues NO fetch (explicit load)
 *   view.load();                        // user-initiated "load draft" click only
 *   view.render();                      // call on room refresh; self-purges stale snapshots
 *   view.setLanguage('en');             // labels only, never refetches, keeps unsaved text
 *   view.clear();                       // room switch / logout / membership epoch change
 *   view.destroy();                     // remove everything
 *
 * The container passed to mount() MUST be dedicated to this module: it owns and clears
 * all of that container's children. Do not pass a shared room-panel root.
 * A 401 (session loss) hides the panel permanently until the host mounts again.
 *
 * `user` must be the server session user shape {id, display_name, handle, kind, scopes}
 * (talkabout/auth.py:82). Response validity is decided from fresh context snapshots
 * (room id, auth ticket, view ticket, current membership epoch, membership revision),
 * so a late response for a previous mount is discarded without touching the current one.
 * The server allows reads/writes only for the human who authored the draft and only
 * while that member's epoch matches (core.work_plan_authorize / stored _author/_epoch);
 * any other human in the room receives 404.
 */

const NODE_LIMIT = 16;
const TITLE_LIMIT = 200;
const INSTRUCTION_LIMIT = 2000;
const DEP_LIMIT = 15;
const ID_PATTERN = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/;
const WRITE_SCOPES = ['analysis', 'owner_approved_workspace'];
// Best-effort UI subset only: the server rejects every Unicode category C
// (Cc/Cf/Cs/Co/Cn, newline/tab excepted) and is the authoritative validator. This
// regex covers just ASCII C0/DEL so common cases are caught before the POST; it is
// NOT byte-identical to the server check and is not a security boundary.
const CONTROL = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/;

export const WORK_PLAN_LABELS = {
  ko: {
    workPlanTitle: '개인 작업 초안',
    workPlanScope: '이 초안은 해당 초안을 작성한 사람과 그 멤버십 epoch에서만 열람·수정할 수 있습니다. 실행 계획이 아니며 task 생성·에이전트 전달·의존성 실행·도구 권한을 강제하지 않습니다.',
    workPlanRevision: '리비전',
    workPlanLoading: '초안을 불러오는 중입니다…',
    workPlanLoad: '초안 불러오기',
    workPlanNotLoaded: '아직 초안을 불러오지 않았습니다. "초안 불러오기"를 눌러야 열람·편집할 수 있습니다.',
    workPlanStale: '방 상태·멤버십 또는 세션이 바뀌어 초안 표시를 지웠습니다. 다시 불러오세요.',
    workPlanSaving: '저장 중입니다…',
    workPlanSaved: '저장했습니다.',
    workPlanFailed: '초안을 불러오지 못했습니다. 잠시 후 다시 시도하세요.',
    workPlanUnavailable: '이 초안을 열람하거나 수정할 수 없습니다.',
    workPlanForbidden: '이 초안에 접근할 수 없습니다.',
    workPlanConflict: '다른 곳에서 초안이 수정되었습니다. 최신 초안을 다시 불러온 뒤 저장하세요.',
    workPlanRejected: '저장이 거부되었습니다. 방 상태와 입력 값을 확인하세요.',
    workPlanRoomClosed: '종료된 방에서는 초안을 저장할 수 없습니다.',
    workPlanEdit: '편집',
    workPlanReload: '다시 불러오기',
    workPlanDiscard: '편집 취소',
    workPlanCancel: '취소',
    workPlanAdd: '노드 추가',
    workPlanRemove: '삭제',
    workPlanSave: '저장',
    workPlanRetry: '다시 시도',
    workPlanEmpty: '저장된 초안이 없습니다. 편집으로 첫 계획을 작성하세요.',
    workPlanNodeCount: '노드',
    workPlanFieldId: 'ID',
    workPlanFieldTitle: '제목',
    workPlanFieldInstruction: '지시문',
    workPlanFieldAssignee: '담당 에이전트',
    workPlanAssigneePlaceholder: '담당자를 선택하세요',
    workPlanFieldWriteScope: '쓰기 범위',
    workPlanFieldDepends: '선행 노드',
    workPlanFieldRequired: '결과 메시지가 필요한 선행 노드',
    workPlanRequiredHelp: '선행 노드 중 선택하세요. 현재 워커는 최대 8개를 처리하며 초과 시 실행하지 않습니다. 결과 메시지만 대상으로 하며 파일·산출물 전달이나 모델의 이해는 보장하지 않습니다.',
    workPlanInvalidRequired: '필수 선행 노드는 중복 없이 선행 노드 목록에 포함되어야 합니다.',
    workPlanNone: '없음',
    workPlanDepsHelp: '의존성은 초안 설명일 뿐 실행 순서를 강제하지 않습니다.',
    workPlanNoAssignee: '이 방에 활성 에이전트가 없습니다. 먼저 에이전트를 초대하고 참여 상태를 활성으로 만드세요.',
    workPlanInactiveAssignee: '비활성',
    workPlanInactiveAssigneeHelp: '저장된 담당자 에이전트가 현재 비활성입니다. 활성 에이전트를 선택하기 전에는 저장할 수 없습니다.',
    workPlanInvalidGeneral: '입력이 올바르지 않습니다.',
    workPlanInvalidId: 'ID는 영문으로 시작하는 1–64자(영문·숫자·_·-)여야 하며 중복될 수 없습니다.',
    workPlanInvalidTitle: '제목은 1–200자이며 공백일 수 없습니다.',
    workPlanInvalidInstruction: '지시문은 1–2000자이며 공백일 수 없습니다.',
    workPlanInvalidControl: '제어 문자는 사용할 수 없습니다(줄바꿈·탭 제외).',
    workPlanInvalidDepends: '선행 노드 ID가 존재해야 하며 자기 자신을 가리킬 수 없습니다.',
    workPlanCycle: '선행 관계에 순환이 있습니다.',
    workPlanInvalidAssignee: '담당자는 이 방의 활성 에이전트여야 합니다.',
    workPlanInvalidWriteScope: '쓰기 범위는 analysis 또는 owner_approved_workspace만 가능합니다.',
    workPlanTooManyNodes: '노드는 최대 16개입니다.',
    workPlanUnsaved: '저장되지 않은 편집이 있습니다.',
    workPlanDiscardConfirm: '저장되지 않은 편집을 버리시겠습니까?',
    workPlanShareTitle: '선택 노드 공유 미리보기',
    workPlanShareHelp: '노드를 선택하면 미리보기 버튼으로 선택한 노드의 텍스트만 만들 수 있습니다. 기본 선택은 없습니다.',
    workPlanSharePreview: '선택 미리보기',
    workPlanShareCopy: '복사',
    workPlanShareTextLabel: '전송 준비용 텍스트(복사만 하며 게시·전송하지 않습니다)',
    workPlanShareNoSelection: '선택한 노드가 없습니다.',
    workPlanShareWarning: '이 텍스트를 사람이 직접 복사해 보낼 수 있습니다. 현재 방 구성원과 provider가 볼 수 있고, 초안은 철회할 수 없습니다. 실제 task 생성·의존성 실행·순서·성공·analysis 읽기 전용은 보장되지 않으며 쓰기 범위도 강제되지 않습니다. 선택한 노드의 선행 노드 ID도 함께 포함됩니다.',
    workPlanShareHeader: 'TalkAbout 개인 작업 초안(사람이 복사한 참고 자료, 실행 요청 아님)',
    workPlanShareUnsavedMark: '저장되지 않은 편집 포함',
    workPlanCopied: '복사했습니다.',
    workPlanCopyFailed: '복사하지 못했습니다.',
    workPlanAgentList: '담당자 선택',
    workPlanDispatchTitle: '계획 실행',
    workPlanDispatchScope: '이 패널은 저장된 초안을 사람이 명시적으로 실행할 때만 사용합니다. 자동 실행·자동 새로고침·자동 재시도는 없습니다.',
    workPlanDispatchSnapshot: '이 실행 상태는 마지막으로 받은 응답 기준입니다(자동 갱신 없음). 현재 상태는 "실행 상태 새로고침"으로 확인하세요.',
    workPlanDispatchRefreshRequired: '시작·중지 응답은 요청 시점 스냅샷입니다. 추가 시작·중지 전에 "실행 상태 새로고침"으로 현재 상태를 확인하세요.',
    workPlanDispatchNotChecked: '실행 상태를 아직 확인하지 않았습니다. "실행 상태 새로고침"을 눌러 확인하세요.',
    workPlanDispatchLoading: '실행 상태를 불러오는 중입니다…',
    workPlanDispatchStarting: '실행을 시작하는 중입니다…',
    workPlanDispatchAborting: '실행 중지를 요청하는 중입니다…',
    workPlanDispatchNoRun: '이 계획의 실행 기록이 없습니다.',
    workPlanDispatchRefresh: '실행 상태 새로고침',
    workPlanDispatchStart: '계획 실행',
    workPlanDispatchAbort: '실행 중지',
    workPlanDispatchAcksTitle: '실행 전 사람 확인',
    workPlanDispatchAckQueue: 'task가 큐에 등록되고 연결된 워커가 claim하면 provider 비용이 발생할 수 있음을 이해합니다.',
    workPlanDispatchAckDisclosure: '계획 텍스트가 담당 에이전트와 provider에 전달되고, 에이전트의 공개 결과·메시지로 다시 인용되어 방 구성원에게 노출·기록될 수 있음을 이해합니다.',
    workPlanDispatchAckUnenforced: 'write_scope는 선언값이며 파일 접근을 제한하는 샌드박스가 아님을 이해합니다.',
    workPlanDispatchAcksRequired: '세 가지 경고를 모두 확인해야 실행할 수 있습니다.',
    workPlanDispatchDirty: '저장되지 않은 초안이 있습니다. 먼저 초안을 저장한 뒤 실행하세요.',
    workPlanDispatchClosed: '종료된 방에서는 계획을 실행할 수 없습니다.',
    workPlanDispatchConfirm: '이 계획을 실행하시겠습니까? task가 큐에 등록되고 연결된 워커가 claim하면 provider 비용이 발생할 수 있습니다. 실행 후에는 자동 재시도나 자동 재dispatch가 없습니다.',
    workPlanDispatchConfirmDisclosure: '계획 텍스트는 담당 에이전트와 provider에 전달되고, 담당 에이전트의 공개 결과·메시지로 다시 인용되어 방 구성원에게 노출·기록될 수 있습니다.',
    workPlanDispatchAbortConfirm: '실행 중인 계획을 중지하시겠습니까? 이미 완료된 노드는 되돌리지 않으며 자동 재시도는 없습니다.',
    workPlanDispatchGeneration: '방 세대',
    workPlanDispatchRunTitle: '실행 상태',
    workPlanDispatchRunId: '실행 ID',
    workPlanDispatchNodesTitle: '노드별 실행 상태',
    workPlanDispatchNodeAssignee: '담당',
    workPlanDispatchNodeState: '상태',
    workPlanDispatchNodeTask: 'task',
    workPlanDispatchNodeFailure: '실패',
    workPlanDispatchNoProvider: '서버는 provider를 호출하지 않습니다.',
    workPlanDispatchWorkerClaim: 'task는 워커가 claim해야 실행됩니다.',
    workPlanDispatchWriteScopeUnenforced: 'write_scope는 서버가 강제하지 않습니다.',
    workPlanDispatchNoAuto: '부분 실행이 있을 수 있으며 자동 재시도·자동 re-dispatch는 없습니다.',
    workPlanDispatchPlanDisclosure: '계획 텍스트는 담당 에이전트와 provider에 공개되고, 에이전트의 공개 결과·메시지로 다시 인용되어 방 구성원에게 노출·기록될 수 있습니다.',
    workPlanDispatchEvidence: '실행 근거는 워커 보고 기반이며 서버가 검증하지 않습니다.',
    workPlanDispatchStarted: '시작',
    workPlanDispatchFinished: '종료',
    workPlanDispatchAbortReason: '중지 사유',
    workPlanDispatchFailed: '계획 실행 요청에 실패했습니다. 자동 재시도하지 않습니다.',
    workPlanDispatchInvalid: '실행 상태 응답이 올바르지 않아 표시하지 않았습니다.',
    workPlanDispatchStartConflict: '계획 실행이 시작되지 않았습니다. 계획 리비전·방 세대가 바뀌었거나 이미 실행 중입니다. 자동 재시도하지 않습니다.',
    workPlanDispatchAbortConflict: '계획 실행이 중지되지 않았습니다. 자동 재시도하지 않습니다.',
    workPlanDispatchWorkerConnectionRequired: '연결된 워커가 필요합니다. 워커 연결 상태를 확인한 뒤 다시 시도하세요. 자동 재시도하지 않습니다.',
    workPlanDispatchRoomBusy: '방에 대기·실행 중인 task 또는 turn request가 있어 계획을 시작할 수 없습니다. 현재 작업이 끝난 뒤 다시 시도하세요. 자동 재시도하지 않습니다.',
    workPlanDispatchAlreadyActive: '이미 실행 중인 계획이 있습니다. 실행 중지 또는 완료 후 실행 상태를 새로고침하세요. 자동 재dispatch하지 않습니다.',
    workPlanDispatchHeld: '이 방은 dispatch hold 상태입니다. hold를 해제한 뒤 다시 시도하세요. 자동 재시도하지 않습니다.',
    workPlanDispatchRevisionConflict: '계획 리비전이 바뀌었습니다. 실행 상태를 새로고침하고 최신 초안을 확인한 뒤 다시 시도하세요. 자동 재시도하지 않습니다.',
    workPlanDispatchStaleContext: '방 세대 또는 상태가 바뀌었습니다. 방 상태를 새로고침한 뒤 다시 시도하세요. 자동 재시도하지 않습니다.',
    workPlanDispatchAssigneeRequired: '담당 에이전트의 권한 또는 상태가 실행 조건을 충족하지 않습니다. 담당자를 확인한 뒤 다시 시도하세요. 자동 재시도하지 않습니다.',
    workPlanDispatchCapacity: 'task 용량이 부족해 계획을 시작할 수 없습니다. 기존 task를 정리한 뒤 다시 시도하세요. 자동 재시도하지 않습니다.',
    workPlanDispatchMissing: '실행할 계획을 찾을 수 없습니다. 초안을 다시 불러온 뒤 시도하세요. 자동 재시도하지 않습니다.',
    workPlanDispatchAlreadyStopped: '이미 중지됨: 실행 중이 아닌 계획입니다. 상태 새로고침으로 확인하세요. 자동 재시도하지 않습니다.',
    workPlanDispatchUnknown: '결과 불명 — 상태 새로고침으로 확인하세요. 새 실행·중지는 새로고침 전까지 잠깁니다.',
    workPlanDispatchRepeatPrevious: '이전 실행',
    workPlanDispatchRepeatWarning: '같은 계획 리비전은 다시 실행할 수 없습니다. 반복하려면 계획을 확인하고 새 리비전으로 저장하세요.',
    workPlanDispatchRepeatAck: '결과 불명 요청을 원래 키로 확인할 때만 선택합니다.',
    workPlanDispatchRepeatRequired: '같은 리비전을 새로 실행할 수 없습니다. 새 리비전을 저장하세요.',
    workPlanDispatchQueuedCount: '큐에 넣은 task',
    workPlanDispatchQueuedZero: '실행 불가: 큐에 넣은 task가 0개입니다. 노드 상태를 새로고침해 확인하세요. 자동 재dispatch하지 않습니다.',
    workPlanDispatchRunRunning: '실행 중',
    workPlanDispatchRunCompleted: '완료',
    workPlanDispatchRunPartial: '부분 실행',
    workPlanDispatchRunAborted: '중지됨',
    workPlanDispatchNodePending: '대기',
    workPlanDispatchNodeQueued: '큐에 등록됨',
    workPlanDispatchNodeCompleted: '완료',
    workPlanDispatchNodeFailed: '실패',
    workPlanDispatchNodeBlocked: '차단됨',
    workPlanDispatchPartialHelp: '일부 노드가 실패하거나 차단되었습니다. 아래 상태와 사유를 확인하고 필요한 결과를 검토하세요. 다시 실행하려면 계획을 검토해 새 리비전으로 저장해야 합니다.',
    workPlanDispatchFailedHelp: '이 노드는 완료되지 않았습니다. 표시된 사유와 task가 있으면 해당 task를 확인하세요. 자동 재시도하지 않습니다.',
    workPlanDispatchBlockedHelp: '이 노드는 차단되어 실행되지 않았습니다. 표시된 사유와 선행 노드 상태를 확인하세요.',
    workPlanDispatchDependencyHelp: '선행 노드가 실패하거나 차단되어 이 노드는 큐에 등록되지 않았습니다. 선행 노드의 상태와 사유를 검토하세요.',
    workPlanDispatchInputHelp: '필수 선행 결과를 확인하거나 전달할 수 없어 이 노드를 시작하지 않았습니다. 가시성·결과와 워커 상태를 확인하고 필요하면 새 계획 리비전을 저장하세요.',
    workPlanDispatchUncertainTaskHelp: 'task 결과가 불확실합니다. 실행 또는 외부 효과가 있었을 수 있으므로 task와 결과를 확인하고 중복 실행을 피하세요.',
    workPlanDispatchReplayStartHelp: '새로고침은 이전 시작 요청의 결과를 증명하지 않습니다. 실행 버튼을 사용할 수 있을 때 다시 누르면 확인 후 원래 요청과 키로 재전송합니다. 새 실행이 아닙니다.',
    workPlanDispatchReplayAbortHelp: '같은 실행이 여전히 실행 중으로 표시됩니다. 중지 버튼을 다시 누르면 확인 후 원래 요청과 키로 재전송합니다.',
  },
  en: {
    workPlanTitle: 'Private work draft',
    workPlanScope: 'This draft can be viewed and edited only by the person who wrote it, under the same membership epoch. It is not an execution plan and does not create tasks, hand work to agents, run dependencies, or enforce tool permissions.',
    workPlanRevision: 'Revision',
    workPlanLoading: 'Loading the draft…',
    workPlanLoad: 'Load draft',
    workPlanNotLoaded: 'The draft has not been loaded yet. Choose "Load draft" before viewing or editing.',
    workPlanStale: 'Room, membership or session state changed, so the draft display was cleared. Load it again.',
    workPlanSaving: 'Saving…',
    workPlanSaved: 'Saved.',
    workPlanFailed: 'Could not load the draft. Try again later.',
    workPlanUnavailable: 'This draft cannot be viewed or edited.',
    workPlanForbidden: 'Access to this draft is not allowed.',
    workPlanConflict: 'The draft was changed elsewhere. Reload the latest draft before saving.',
    workPlanRejected: 'The save was rejected. Check the room state and your input.',
    workPlanRoomClosed: 'Drafts cannot be saved in a closed room.',
    workPlanEdit: 'Edit',
    workPlanReload: 'Reload',
    workPlanDiscard: 'Discard edits',
    workPlanCancel: 'Cancel',
    workPlanAdd: 'Add node',
    workPlanRemove: 'Remove',
    workPlanSave: 'Save',
    workPlanRetry: 'Retry',
    workPlanEmpty: 'No saved draft yet. Start one with Edit.',
    workPlanNodeCount: 'Nodes',
    workPlanFieldId: 'ID',
    workPlanFieldTitle: 'Title',
    workPlanFieldInstruction: 'Instruction',
    workPlanFieldAssignee: 'Assign to agent',
    workPlanAssigneePlaceholder: 'Choose an assignee',
    workPlanFieldWriteScope: 'Write scope',
    workPlanFieldDepends: 'Depends on',
    workPlanFieldRequired: 'Predecessor result messages required',
    workPlanRequiredHelp: 'Select dependencies. Current workers handle at most 8 and will not execute if there are more. This concerns result messages only, not file or artifact delivery or model comprehension.',
    workPlanInvalidRequired: 'Required predecessors must be unique and included in the dependencies.',
    workPlanNone: 'none',
    workPlanDepsHelp: 'Dependencies describe the draft only; they do not enforce execution order.',
    workPlanNoAssignee: 'This room has no active agent. Invite an agent and make it active first.',
    workPlanInactiveAssignee: 'inactive',
    workPlanInactiveAssigneeHelp: 'The saved assignee is no longer active. Saving is blocked until you pick an active agent.',
    workPlanInvalidGeneral: 'The input is not valid.',
    workPlanInvalidId: 'IDs start with a letter, use 1–64 letters/digits/_/-, and must be unique.',
    workPlanInvalidTitle: 'The title must be 1–200 characters and not blank.',
    workPlanInvalidInstruction: 'The instruction must be 1–2000 characters and not blank.',
    workPlanInvalidControl: 'Control characters are not allowed (except newline and tab).',
    workPlanInvalidDepends: 'Dependency IDs must exist and must not point at themselves.',
    workPlanCycle: 'The dependencies contain a cycle.',
    workPlanInvalidAssignee: 'The assignee must be an active agent in this room.',
    workPlanInvalidWriteScope: 'The write scope must be analysis or owner_approved_workspace.',
    workPlanTooManyNodes: 'At most 16 nodes are allowed.',
    workPlanUnsaved: 'There are unsaved edits.',
    workPlanDiscardConfirm: 'Discard the unsaved edits?',
    workPlanShareTitle: 'Share preview for selected nodes',
    workPlanShareHelp: 'Select nodes, then build text for those nodes only. Nothing is selected by default.',
    workPlanSharePreview: 'Preview selection',
    workPlanShareCopy: 'Copy',
    workPlanShareTextLabel: 'Text prepared for sending (copy only; it is not posted or sent)',
    workPlanShareNoSelection: 'No node is selected.',
    workPlanShareWarning: 'A human can copy this text and send it manually. Current room members and providers may see it, and the draft cannot be revoked. This does not create tasks, run dependencies, guarantee order or success, or make analysis read-only, and the write scope is not enforced. The dependency IDs of the selected nodes are included too.',
    workPlanShareHeader: 'TalkAbout private work draft (human-copied reference, not an execution request)',
    workPlanShareUnsavedMark: 'including unsaved edits',
    workPlanCopied: 'Copied.',
    workPlanCopyFailed: 'Could not copy.',
    workPlanAgentList: 'Choose assignee',
    workPlanDispatchTitle: 'Plan execution',
    workPlanDispatchScope: 'Use this panel only when a human explicitly executes the saved draft. There is no automatic execution, polling, or retry.',
    workPlanDispatchSnapshot: 'This execution status reflects the last response received (no automatic refresh). Check the current state with "Refresh execution status".',
    workPlanDispatchRefreshRequired: 'Start and stop responses are snapshots from the request time. Check the current state with "Refresh execution status" before another start or stop.',
    workPlanDispatchNotChecked: 'Execution status has not been checked yet. Choose "Refresh execution status".',
    workPlanDispatchLoading: 'Loading execution status…',
    workPlanDispatchStarting: 'Starting execution…',
    workPlanDispatchAborting: 'Requesting the execution stop…',
    workPlanDispatchNoRun: 'This plan has no execution record.',
    workPlanDispatchRefresh: 'Refresh execution status',
    workPlanDispatchStart: 'Execute plan',
    workPlanDispatchAbort: 'Stop execution',
    workPlanDispatchAcksTitle: 'Human confirmations before execution',
    workPlanDispatchAckQueue: 'I understand tasks are queued and a connected worker claiming one may incur provider cost.',
    workPlanDispatchAckDisclosure: 'I understand the plan text is delivered to assignee agents and providers and may be quoted again in an agent\'s public results or messages, exposing and recording it to room members.',
    workPlanDispatchAckUnenforced: 'I understand write_scope is a declaration, not a sandbox restricting file access.',
    workPlanDispatchAcksRequired: 'All three warnings must be acknowledged before execution.',
    workPlanDispatchDirty: 'There are unsaved edits. Save the draft before executing it.',
    workPlanDispatchClosed: 'A plan cannot be executed in a closed room.',
    workPlanDispatchConfirm: 'Execute this plan? Tasks will be queued, and a connected worker claiming one may incur provider cost. There is no automatic retry or automatic re-dispatch.',
    workPlanDispatchConfirmDisclosure: 'The plan text is delivered to assignee agents and providers and may be quoted again in an agent\'s public results or messages, exposing and recording it to room members.',
    workPlanDispatchAbortConfirm: 'Stop the running plan? Nodes already completed are not reverted, and there is no automatic retry.',
    workPlanDispatchGeneration: 'Room generation',
    workPlanDispatchRunTitle: 'Execution status',
    workPlanDispatchRunId: 'Run ID',
    workPlanDispatchNodesTitle: 'Per-node execution status',
    workPlanDispatchNodeAssignee: 'Assignee',
    workPlanDispatchNodeState: 'State',
    workPlanDispatchNodeTask: 'task',
    workPlanDispatchNodeFailure: 'Failure',
    workPlanDispatchNoProvider: 'The server does not call a provider.',
    workPlanDispatchWorkerClaim: 'A worker must claim a task before it runs.',
    workPlanDispatchWriteScopeUnenforced: 'The server does not enforce write_scope.',
    workPlanDispatchNoAuto: 'Partial execution is possible; there is no automatic retry or automatic re-dispatch.',
    workPlanDispatchPlanDisclosure: 'Plan text is visible to assignee agents and providers and may be quoted again in an agent\'s public results or messages, exposing and recording it to room members.',
    workPlanDispatchEvidence: 'Execution evidence is worker-reported and is not verified by the server.',
    workPlanDispatchStarted: 'Started',
    workPlanDispatchFinished: 'Finished',
    workPlanDispatchAbortReason: 'Stop reason',
    workPlanDispatchFailed: 'The plan execution request failed. It is not retried automatically.',
    workPlanDispatchInvalid: 'The execution-status response was not valid, so it is not displayed.',
    workPlanDispatchStartConflict: 'Plan execution was not started. The plan revision or room generation changed, or a run is already active. It is not retried automatically.',
    workPlanDispatchAbortConflict: 'Plan execution was not stopped. It is not retried automatically.',
    workPlanDispatchWorkerConnectionRequired: 'A connected worker is required. Check the worker connection and try again. It is not retried automatically.',
    workPlanDispatchRoomBusy: 'Tasks are waiting or running, or a turn request exists, so the plan cannot start. Try again after the current work finishes. It is not retried automatically.',
    workPlanDispatchAlreadyActive: 'A plan run is already active. Stop it or wait for completion, then refresh the execution status. It is not redispatched automatically.',
    workPlanDispatchHeld: 'Dispatch is held for this room. Release the hold and try again. It is not retried automatically.',
    workPlanDispatchRevisionConflict: 'The plan revision changed. Refresh the execution status, check the latest draft, and try again. It is not retried automatically.',
    workPlanDispatchStaleContext: 'The room generation or state changed. Refresh the room state and try again. It is not retried automatically.',
    workPlanDispatchAssigneeRequired: 'An assignee agent does not meet the permission or state requirements for execution. Check the assignees and try again. It is not retried automatically.',
    workPlanDispatchCapacity: 'Task capacity is insufficient to start the plan. Clear existing tasks and try again. It is not retried automatically.',
    workPlanDispatchMissing: 'The plan to execute was not found. Reload the draft and try again. It is not retried automatically.',
    workPlanDispatchAlreadyStopped: 'Already stopped: the plan is not running. Refresh the execution status to check. It is not retried automatically.',
    workPlanDispatchUnknown: 'Outcome unknown — refresh the execution status to check. New starts and stops are locked until then.',
    workPlanDispatchRepeatPrevious: 'Previous run',
    workPlanDispatchRepeatWarning: 'This plan revision cannot be started twice. Review and save a new revision to run again.',
    workPlanDispatchRepeatAck: 'Use only to reconcile an uncertain request with its original key.',
    workPlanDispatchRepeatRequired: 'A new start requires saving a new plan revision.',
    workPlanDispatchQueuedCount: 'Tasks queued',
    workPlanDispatchQueuedZero: 'Not runnable: 0 tasks were queued. Refresh the node status to check. It is not redispatched automatically.',
    workPlanDispatchRunRunning: 'Running',
    workPlanDispatchRunCompleted: 'Completed',
    workPlanDispatchRunPartial: 'Partially completed',
    workPlanDispatchRunAborted: 'Stopped',
    workPlanDispatchNodePending: 'Pending',
    workPlanDispatchNodeQueued: 'Queued',
    workPlanDispatchNodeCompleted: 'Completed',
    workPlanDispatchNodeFailed: 'Failed',
    workPlanDispatchNodeBlocked: 'Blocked',
    workPlanDispatchPartialHelp: 'Some nodes failed or were blocked. Review their states, reasons and any results below. To run again, review the plan and save a new revision.',
    workPlanDispatchFailedHelp: 'This node did not complete. Review the reason shown and its task, if present. There is no automatic retry.',
    workPlanDispatchBlockedHelp: 'This node was blocked and did not run. Review the reason shown and the predecessor states.',
    workPlanDispatchDependencyHelp: 'A predecessor failed or was blocked, so this node was not queued. Review the predecessor states and reasons.',
    workPlanDispatchInputHelp: 'Required predecessor results could not be verified or delivered, so this node did not start. Check visibility, results and worker status; save a new plan revision if needed.',
    workPlanDispatchUncertainTaskHelp: 'The task outcome is uncertain. Execution or external effects may have occurred; inspect the task and results and avoid duplicate execution.',
    workPlanDispatchReplayStartHelp: 'A refresh cannot prove the outcome of the earlier start request. When Execute plan is available, choosing it again sends the original request and key after confirmation; it is not a new run.',
    workPlanDispatchReplayAbortHelp: 'The same run still appears running. Choosing Stop execution again sends the original request and key after confirmation.',
  },
};

export function createWorkPlanView(deps = {}) {
  const api = deps.api;
  if (typeof api !== 'function') throw new TypeError('work-plan: api is required');
  const getContext = typeof deps.getContext === 'function' ? deps.getContext : () => null;
  const doc = deps.document || (typeof document !== 'undefined' ? document : null);
  if (!doc) throw new TypeError('work-plan: document is required');
  const notify = typeof deps.notify === 'function' ? deps.notify : () => {};
  const ask = typeof deps.confirm === 'function'
    ? deps.confirm
    : (message) => (typeof globalThis.confirm === 'function' ? globalThis.confirm(message) : false);
  const copy = typeof deps.clipboard === 'function'
    ? deps.clipboard
    : (text) => {
      const clipboard = globalThis.navigator && globalThis.navigator.clipboard;
      if (!clipboard || typeof clipboard.writeText !== 'function') return Promise.reject(new Error('clipboard_unavailable'));
      return clipboard.writeText(text);
    };
  const newKey = typeof deps.uuid === 'function'
    ? deps.uuid
    : () => {
      if (!globalThis.crypto || typeof globalThis.crypto.randomUUID !== 'function') throw new Error('uuid_unavailable');
      return globalThis.crypto.randomUUID();
    };

  const state = {
    mountToken: 0,
    container: null,
    root: null,
    lang: 'ko',
    status: 'idle', // idle | loading | loaded | saving | saved | stale | failed | inaccessible | forbidden | conflict | rejected
    errorKey: '',
    blocked: false, // session loss (401): stay hidden until the host mounts again
    plan: null, // {revision:int, nodes:[...]}
    boundSnapshot: null, // ownership snapshot the rendered data belongs to
    baseRevision: 0,
    editor: [], // draft nodes while editing
    mode: 'view', // view | edit
    dirty: false,
    selected: new Set(),
    previewText: '',
    busy: false,
    dispatch: {
      status: 'idle', // idle | loading | loaded | starting | aborting
      busy: false,
      token: 0,
      run: null,
      queued: null, // tasks_queued from the explicit start response, tied to its run id
      queuedRunId: null,
      boundSnapshot: null,
      errorKey: '',
      // An uncertain start keeps its original key and body until a successful
      // replay. A later conflict cannot prove the first request did not commit.
      intent: null, // {action:'start'|'abort', key, body, runId, unknown}
      needsRefresh: false, // a start/abort response is only a snapshot; require explicit GET
      repeatAck: false, // extra acknowledgement for terminal same-revision re-run
      acks: {queue: false, disclosure: false, unenforced: false},
    },
  };

  const t = (key) => {
    if (typeof deps.t === 'function') return deps.t(key);
    const table = WORK_PLAN_LABELS[state.lang] || WORK_PLAN_LABELS.ko;
    return table[key] ?? WORK_PLAN_LABELS.ko[key] ?? key;
  };

  const make = (tag, text, className) => {
    const node = doc.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  };

  const button = (id, label, handler, disabled = false) => {
    const node = make('button', label);
    node.type = 'button';
    node.id = id;
    node.disabled = disabled;
    node.onclick = handler;
    return node;
  };

  const activeAgents = (room) => Object.entries(room && room.members ? room.members : {})
    .filter(([, member]) => member && member.kind === 'agent' && member.status === 'active')
    .map(([id]) => id);

  const allowed = (ctx) => !!(
    ctx
    && ctx.user
    && ctx.user.kind === 'human'
    && ctx.room
    && Array.isArray(ctx.room.effective_permissions)
    && ctx.room.effective_permissions.includes('room:read')
    && ctx.room.effective_permissions.includes('room:control')
  );

  const capture = (ctx) => {
    const room = ctx && ctx.room;
    const userId = ctx && ctx.user && ctx.user.id;
    return {
      rid: room ? room.id : null,
      uid: ctx && ctx.user ? ctx.user.id : null,
      authTicket: ctx ? ctx.authTicket : null,
      viewTicket: ctx ? ctx.viewTicket : null,
      epoch: room && userId && room.members ? (room.members[userId] || {}).epoch : null,
      membershipRevision: room ? room.membership_revision : null,
    };
  };

  const sameSnapshot = (ctx, snap) => {
    if (!ctx || !ctx.room || !ctx.user) return false;
    const room = ctx.room;
    const userId = ctx.user.id;
    return room.id === snap.rid
      && userId === snap.uid
      && ctx.authTicket === snap.authTicket
      && ctx.viewTicket === snap.viewTicket
      && (room.members ? (room.members[userId] || {}).epoch : null) === snap.epoch
      && room.membership_revision === snap.membershipRevision;
  };

  const RUN_STATES = new Set(['running', 'completed', 'partial', 'aborted']);
  const NODE_STATES = new Set(['pending', 'queued', 'completed', 'failed', 'blocked']);
  const RUN_STATE_KEYS = {
    running: 'workPlanDispatchRunRunning',
    completed: 'workPlanDispatchRunCompleted',
    partial: 'workPlanDispatchRunPartial',
    aborted: 'workPlanDispatchRunAborted',
  };
  const NODE_STATE_KEYS = {
    pending: 'workPlanDispatchNodePending',
    queued: 'workPlanDispatchNodeQueued',
    completed: 'workPlanDispatchNodeCompleted',
    failed: 'workPlanDispatchNodeFailed',
    blocked: 'workPlanDispatchNodeBlocked',
  };
  const DISPATCH_ERROR_KEYS = {
    worker_connection_required: 'workPlanDispatchWorkerConnectionRequired',
    room_busy: 'workPlanDispatchRoomBusy',
    plan_dispatch_active: 'workPlanDispatchAlreadyActive',
    plan_revision_already_dispatched: 'workPlanDispatchRepeatWarning',
    dispatch_held: 'workPlanDispatchHeld',
    work_plan_revision_conflict: 'workPlanDispatchRevisionConflict',
    stale_context: 'workPlanDispatchStaleContext',
    plan_assignee_required: 'workPlanDispatchAssigneeRequired',
    task_capacity: 'workPlanDispatchCapacity',
    plan_missing: 'workPlanDispatchMissing',
    plan_dispatch_not_running: 'workPlanDispatchAlreadyStopped',
  };
  const DEFINITIVE_DISPATCH_STATUSES = new Set([401, 403, 404, 409, 422, 429]);
  const safeText = (value, limit) => typeof value === 'string' && value.length <= limit ? value : null;
  const safeTimestamp = (value) => {
    if (value === null || value === undefined) return null;
    if (typeof value === 'string') return value.length <= 64 ? value : null;
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
  };
  const dispatchRun = (raw) => {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
    const id = safeText(raw.id, 128);
    const runState = raw.state;
    if (!id || !RUN_STATES.has(runState)) return null;
    if (!Number.isSafeInteger(raw.plan_revision) || !Number.isSafeInteger(raw.generation)) return null;
    if (!Array.isArray(raw.nodes) || raw.nodes.length > 64) return null;
    if (raw.write_scope_enforced !== false || raw.automatic_provider_call !== false
      || raw.plan_text_visible_to_assignees !== true || raw.evidence !== 'worker_reported_unverified') return null;
    if (raw.abort_reason !== null && raw.abort_reason !== undefined
      && (typeof raw.abort_reason !== 'string' || raw.abort_reason.length > 2000)) return null;
    const nodes = [];
    for (const item of raw.nodes) {
      if (!item || typeof item !== 'object' || Array.isArray(item)) return null;
      const nodeId = safeText(item.id, 64);
      const assignee = safeText(item.assignee, 128);
      const writeScope = safeText(item.write_scope, 64);
      const taskId = item.task_id === null || item.task_id === undefined ? null : safeText(item.task_id, 128);
      const failure = item.failure === null || item.failure === undefined ? null : safeText(item.failure, 2000);
      if (!nodeId || !assignee || !writeScope || !NODE_STATES.has(item.state)) return null;
      if ((item.task_id !== null && item.task_id !== undefined && taskId === null)
        || (item.failure !== null && item.failure !== undefined && failure === null)) return null;
      nodes.push({id: nodeId, assignee, write_scope: writeScope, state: item.state, task_id: taskId, failure});
    }
    return {
      id,
      state: runState,
      plan_revision: raw.plan_revision,
      generation: raw.generation,
      started_at: safeTimestamp(raw.started_at),
      finished_at: safeTimestamp(raw.finished_at),
      abort_reason: raw.abort_reason === null || raw.abort_reason === undefined ? null : raw.abort_reason,
      nodes,
      write_scope_enforced: false,
      automatic_provider_call: false,
      plan_text_visible_to_assignees: true,
      evidence: 'worker_reported_unverified',
    };
  };
  const resetDispatch = () => {
    const dispatch = state.dispatch;
    dispatch.status = 'idle';
    dispatch.busy = false;
    dispatch.token += 1;
    dispatch.run = null;
    dispatch.queued = null;
    dispatch.queuedRunId = null;
    dispatch.boundSnapshot = null;
    dispatch.errorKey = '';
    dispatch.intent = null;
    dispatch.needsRefresh = false;
    dispatch.repeatAck = false;
    dispatch.acks = {queue: false, disclosure: false, unenforced: false};
  };

  const sanitizeNodes = (raw) => {
    if (!Array.isArray(raw) || raw.length > NODE_LIMIT) return null;
    const nodes = [];
    for (const item of raw) {
      if (!item || typeof item !== 'object' || Array.isArray(item)) return null;
      const { id, title, instruction, assignee, write_scope: writeScope, depends_on: dependsOn } = item;
      if (typeof id !== 'string' || !ID_PATTERN.test(id)) return null;
      if (typeof title !== 'string' || title.length < 1 || title.length > TITLE_LIMIT) return null;
      if (typeof instruction !== 'string' || instruction.length < 1 || instruction.length > INSTRUCTION_LIMIT) return null;
      if (typeof assignee !== 'string' || !assignee) return null;
      if (typeof writeScope !== 'string' || !WRITE_SCOPES.includes(writeScope)) return null;
      if (!Array.isArray(dependsOn) || dependsOn.some((dep) => typeof dep !== 'string')) return null;
      const node = { id, title, instruction, assignee, write_scope: writeScope, depends_on: dependsOn.slice() };
      if (Object.hasOwn(item, 'required_predecessors')) {
        const required = item.required_predecessors;
        if (!Array.isArray(required) || required.some((dep) => typeof dep !== 'string')) return null;
        node.required_predecessors = required.slice();
      }
      nodes.push(node);
    }
    return nodes;
  };

  const sanitizePlan = (data) => {
    if (!data || typeof data !== 'object' || Array.isArray(data)) return null;
    if (!Number.isSafeInteger(data.revision) || data.revision < 0) return null;
    const nodes = sanitizeNodes(data.nodes === undefined ? [] : data.nodes);
    if (!nodes) return null;
    return { revision: data.revision, nodes };
  };

  const validateDraft = (nodes, room) => {
    if (!Array.isArray(nodes) || nodes.length > NODE_LIMIT) return 'workPlanTooManyNodes';
    const agents = new Set(activeAgents(room));
    const ids = new Set();
    const deps = new Map();
    for (const node of nodes) {
      const id = node && node.id;
      if (typeof id !== 'string' || !ID_PATTERN.test(id) || ids.has(id)) return 'workPlanInvalidId';
      ids.add(id);
      if (typeof node.title !== 'string' || !node.title.trim() || node.title.length > TITLE_LIMIT) return 'workPlanInvalidTitle';
      if (typeof node.instruction !== 'string' || !node.instruction.trim() || node.instruction.length > INSTRUCTION_LIMIT) return 'workPlanInvalidInstruction';
      if (CONTROL.test(node.title) || CONTROL.test(node.instruction)) return 'workPlanInvalidControl';
      if (!WRITE_SCOPES.includes(node.write_scope)) return 'workPlanInvalidWriteScope';
      if (typeof node.assignee !== 'string' || !agents.has(node.assignee)) return 'workPlanInvalidAssignee';
      if (!Array.isArray(node.depends_on) || node.depends_on.length > DEP_LIMIT) return 'workPlanInvalidDepends';
      if (new Set(node.depends_on).size !== node.depends_on.length) return 'workPlanInvalidDepends';
      if (Object.hasOwn(node, 'required_predecessors')
        && (!Array.isArray(node.required_predecessors)
          || new Set(node.required_predecessors).size !== node.required_predecessors.length
          || node.required_predecessors.some((dep) => !node.depends_on.includes(dep)))) return 'workPlanInvalidRequired';
      deps.set(id, node.depends_on.slice());
    }
    for (const [id, list] of deps) {
      for (const dep of list) {
        if (!ids.has(dep) || dep === id) return 'workPlanInvalidDepends';
      }
    }
    // Kahn's algorithm: any leftover node is part of a cycle.
    const remaining = new Map();
    for (const [id, list] of deps) remaining.set(id, new Set(list));
    while (remaining.size > 0) {
      const ready = [...remaining.entries()].filter(([, list]) => list.size === 0).map(([id]) => id);
      if (ready.length === 0) return 'workPlanCycle';
      for (const id of ready) remaining.delete(id);
      for (const list of remaining.values()) for (const id of ready) list.delete(id);
    }
    return '';
  };

  const collect = () => state.editor.map((node) => ({
    id: node.id,
    title: node.title,
    instruction: node.instruction,
    assignee: node.assignee,
    write_scope: node.write_scope,
    depends_on: node.depends_on.slice(),
    ...(Object.hasOwn(node, 'required_predecessors') ? {required_predecessors: node.required_predecessors.slice()} : {}),
  }));

  const statusText = () => {
    if (state.errorKey) return t(state.errorKey);
    switch (state.status) {
      case 'loading': return t('workPlanLoading');
      case 'saving': return t('workPlanSaving');
      case 'saved': return t('workPlanSaved') + ' ' + t('workPlanRevision') + ' ' + state.baseRevision;
      case 'failed': return t('workPlanFailed');
      case 'stale': return t('workPlanStale');
      case 'inaccessible': return t('workPlanUnavailable');
      case 'forbidden': return t('workPlanForbidden');
      case 'conflict': return t('workPlanConflict');
      case 'rejected': return t('workPlanRejected');
      default: return '';
    }
  };

  const dropContent = () => {
    state.plan = null;
    state.editor = [];
    state.mode = 'view';
    state.dirty = false;
    state.selected.clear();
    state.previewText = '';
    state.baseRevision = 0;
    state.boundSnapshot = null;
    resetDispatch();
  };

  const purge = () => {
    dropContent();
    state.status = 'idle';
    state.errorKey = '';
    state.busy = false;
    if (state.root) {
      state.root.replaceChildren();
      state.root.hidden = true;
    }
  };

  // Same mount, but the ownership snapshot changed (epoch / membership revision /
  // auth ticket / access). Drop the rendered draft even before the host calls clear().
  const purgeStale = () => {
    dropContent();
    state.status = 'stale';
    state.errorKey = '';
    state.busy = false;
  };

  const settle = (owner, snap, apply) => {
    // A response from a previous mount is discarded entirely; it must not touch the
    // current mount (mount/clear bump mountToken).
    if (owner !== state.mountToken) return false;
    const ctx = getContext();
    if (!ctx || !sameSnapshot(ctx, snap)) {
      // Same mount but the ownership snapshot changed while the request was in
      // flight: purge the stale draft instead of leaving old DOM behind.
      state.busy = false;
      purgeStale();
      render();
      return false;
    }
    state.busy = false;
    apply(ctx);
    return true;
  };

  // Shared failure handling. Previous-mount responses are discarded; same-mount
  // ownership changes purge everything; current-snapshot 401/403/404 purge draft text
  // (including any unsaved editor) exactly like the load path.
  const failRequest = (owner, snap, status, keepEditor) => {
    if (owner !== state.mountToken) return false;
    const ctx = getContext();
    if (!ctx || !sameSnapshot(ctx, snap)) {
      state.busy = false;
      purgeStale();
      render();
      return false;
    }
    state.busy = false;
    if (status === 401) {
      // Session is gone: hide for good (no re-render) until the host mounts again.
      state.blocked = true;
      purge();
      return true;
    }
    if (status === 403) {
      dropContent();
      state.status = 'forbidden';
      render();
      return true;
    }
    if (status === 404) {
      dropContent();
      state.status = 'inaccessible';
      render();
      return true;
    }
    state.status = 'rejected';
    state.errorKey = status === 422 ? 'workPlanInvalidGeneral' : '';
    if (keepEditor) state.mode = 'edit';
    render();
    return true;
  };

  const buildShareText = (plan) => {
    const selected = plan.nodes.filter((node) => state.selected.has(node.id));
    const lines = [t('workPlanShareHeader'), t('workPlanRevision') + ': ' + plan.revision];
    if (state.dirty) lines.push('(' + t('workPlanShareUnsavedMark') + ')');
    lines.push('');
    for (const node of selected) {
      lines.push('[' + node.id + '] ' + node.title);
      lines.push('  ' + t('workPlanFieldAssignee') + ': ' + node.assignee);
      lines.push('  ' + t('workPlanFieldWriteScope') + ': ' + node.write_scope);
      lines.push('  ' + t('workPlanFieldDepends') + ': ' + (node.depends_on.length ? node.depends_on.join(', ') : t('workPlanNone')));
      if (Object.hasOwn(node, 'required_predecessors')) lines.push('  ' + t('workPlanFieldRequired') + ': ' + (node.required_predecessors.length ? node.required_predecessors.join(', ') : t('workPlanNone')));
      lines.push('  ' + t('workPlanFieldInstruction') + ':');
      for (const line of node.instruction.split('\n')) lines.push('    ' + line);
      lines.push('');
    }
    return lines.join('\n');
  };

  const clearPreview = () => {
    state.previewText = '';
  };

  const shareCheckbox = (node) => {
    const label = make('label', undefined, 'wp-select');
    const input = make('input');
    input.type = 'checkbox';
    input.id = 'wp-select-' + node.id;
    input.checked = state.selected.has(node.id);
    input.onchange = () => {
      if (input.checked) state.selected.add(node.id); else state.selected.delete(node.id);
      // Any selection change invalidates the previous preview/copy target so a
      // deselected node can never be copied by mistake.
      clearPreview();
      render();
    };
    label.append(input, ' ' + node.title);
    return label;
  };

  const nodeCard = (node) => {
    const card = make('li', undefined, 'wp-node');
    const heading = make('strong', node.title);
    card.append(heading);
    card.append(make('p', t('workPlanFieldAssignee') + ': ' + node.assignee + ' · ' + t('workPlanFieldWriteScope') + ': ' + node.write_scope, 'wp-meta'));
    card.append(make('p', t('workPlanFieldDepends') + ': ' + (node.depends_on.length ? node.depends_on.join(', ') : t('workPlanNone')), 'wp-meta'));
    if (Object.hasOwn(node, 'required_predecessors')) card.append(make('p', t('workPlanFieldRequired') + ': ' + (node.required_predecessors.length ? node.required_predecessors.join(', ') : t('workPlanNone')), 'wp-meta'));
    const details = make('details');
    details.append(make('summary', t('workPlanFieldInstruction')), make('pre', node.instruction));
    card.append(details);
    card.append(shareCheckbox(node));
    return card;
  };

  // Unloaded / stale panel: no draft content, no false "empty draft" claim and no
  // edit path before the user explicitly loads the draft.
  const buildLoad = () => {
    const parts = [];
    parts.push(make('p', t(state.status === 'stale' ? 'workPlanStale' : 'workPlanNotLoaded'), 'wp-help'));
    parts.push(button('wp-load', t('workPlanLoad'), () => load(), state.busy));
    return parts;
  };

  const buildView = (room) => {
    const parts = [];
    if (!state.plan) return buildLoad();
    if (!state.plan.nodes.length) {
      parts.push(make('p', t('workPlanEmpty')));
    } else {
      const list = make('ul', undefined, 'wp-nodes');
      for (const node of state.plan.nodes) list.append(nodeCard(node));
      parts.push(list);
    }
    parts.push(make('p', t('workPlanRevision') + ': ' + state.plan.revision + ' · ' + t('workPlanNodeCount') + ': ' + state.plan.nodes.length, 'wp-revision'));
    const tools = make('div', undefined, 'wp-tools');
    tools.append(
      button('wp-edit', t('workPlanEdit'), () => startEdit(room), room.state === 'closed' || state.busy || !!state.dispatch.intent),
      button('wp-reload', t('workPlanReload'), () => reload(), state.busy || !!state.dispatch.intent),
    );
    parts.push(tools);
    parts.push(buildShare(room));
    return parts;
  };

  const buildShare = (room) => {
    const section = make('section', undefined, 'wp-share');
    section.append(make('h4', t('workPlanShareTitle')));
    section.append(make('p', t('workPlanShareHelp'), 'wp-help'));
    const preview = button('wp-preview', t('workPlanSharePreview'), () => {
      if (!state.plan) return;
      state.previewText = buildShareText(state.plan);
      render();
    }, !state.plan || state.selected.size === 0 || state.busy);
    const copyButton = button('wp-copy', t('workPlanShareCopy'), () => doCopy(), !state.previewText || state.busy);
    const tools = make('div', undefined, 'wp-tools');
    tools.append(preview, copyButton);
    section.append(tools);
    const textarea = make('textarea');
    textarea.id = 'wp-share-text';
    textarea.readOnly = true;
    textarea.rows = 8;
    textarea.value = state.previewText;
    section.append(make('label', t('workPlanShareTextLabel')));
    section.append(textarea);
    if (!state.previewText && state.selected.size > 0) section.append(make('p', t('workPlanShareHelp'), 'wp-help'));
    if (!state.selected.size) section.append(make('p', t('workPlanShareNoSelection'), 'wp-help'));
    section.append(make('p', t('workPlanShareWarning'), 'wp-warning'));
    return section;
  };

  const fieldRow = (labelText, control) => {
    const label = make('label', undefined, 'wp-field');
    label.append(make('span', labelText), control);
    return label;
  };

  const editorNode = (node, index, room) => {
    // While a save request is in flight every editing control is disabled in the real
    // DOM, so typing cannot land in an editor that the success handler is about to clear.
    const lock = (control) => {
      control.disabled = state.busy;
      return control;
    };
    const set = make('fieldset', undefined, 'wp-editor-node');
    set.append(make('legend', '#' + (index + 1)));
    const idInput = lock(make('input'));
    idInput.type = 'text';
    idInput.id = 'wp-id-' + index;
    idInput.value = node.id;
    idInput.maxLength = 64;
    idInput.oninput = () => { if (state.busy) return; node.id = idInput.value; state.dirty = true; };
    set.append(fieldRow(t('workPlanFieldId'), idInput));

    const titleInput = lock(make('input'));
    titleInput.type = 'text';
    titleInput.id = 'wp-title-' + index;
    titleInput.value = node.title;
    titleInput.maxLength = TITLE_LIMIT;
    titleInput.oninput = () => { if (state.busy) return; node.title = titleInput.value; state.dirty = true; };
    set.append(fieldRow(t('workPlanFieldTitle'), titleInput));

    const instructionInput = lock(make('textarea'));
    instructionInput.id = 'wp-instruction-' + index;
    instructionInput.rows = 4;
    instructionInput.maxLength = INSTRUCTION_LIMIT;
    instructionInput.value = node.instruction;
    instructionInput.oninput = () => { if (state.busy) return; node.instruction = instructionInput.value; state.dirty = true; };
    set.append(fieldRow(t('workPlanFieldInstruction'), instructionInput));

    const agents = activeAgents(room);
    const assignee = lock(make('select'));
    assignee.id = 'wp-assignee-' + index;
    if (!node.assignee || !agents.includes(node.assignee)) {
      // Explicit empty placeholder: no silent default to the first active agent.
      const placeholder = make('option', t('workPlanAssigneePlaceholder'));
      placeholder.value = '';
      placeholder.disabled = true;
      assignee.append(placeholder);
    }
    for (const agentId of agents) {
      const option = make('option', agentId);
      option.value = agentId;
      assignee.append(option);
    }
    const assigneeActive = agents.includes(node.assignee);
    if (!assigneeActive && node.assignee) {
      // Preserve the stored (now inactive) assignee instead of silently switching to
      // the first active agent; saving stays blocked until a human picks one.
      const option = make('option', node.assignee + ' (' + t('workPlanInactiveAssignee') + ')');
      option.value = node.assignee;
      assignee.append(option);
    }
    assignee.value = node.assignee || '';
    if (assigneeActive) node.assignee = assignee.value;
    assignee.onchange = () => { node.assignee = assignee.value; state.dirty = true; };
    if (!agents.length) set.append(make('p', t('workPlanNoAssignee'), 'wp-help'));
    if (!assigneeActive && node.assignee) set.append(make('p', t('workPlanInactiveAssigneeHelp'), 'wp-warning'));
    set.append(fieldRow(t('workPlanFieldAssignee'), assignee));

    const scope = lock(make('select'));
    scope.id = 'wp-scope-' + index;
    for (const value of WRITE_SCOPES) {
      const option = make('option', value);
      option.value = value;
      scope.append(option);
    }
    scope.value = node.write_scope;
    scope.onchange = () => { node.write_scope = scope.value; state.dirty = true; };
    set.append(fieldRow(t('workPlanFieldWriteScope'), scope));

    const depsWrap = make('div', undefined, 'wp-deps');
    depsWrap.append(make('p', t('workPlanFieldDepends'), 'wp-meta'));
    for (const other of state.editor) {
      if (other === node) continue;
      const depInput = lock(make('input'));
      depInput.type = 'checkbox';
      depInput.id = 'wp-dep-' + index + '-' + other.id;
      depInput.checked = node.depends_on.includes(other.id);
      depInput.onchange = () => {
        if (state.busy) return;
        const setDeps = new Set(node.depends_on);
        if (depInput.checked) setDeps.add(other.id); else setDeps.delete(other.id);
        node.depends_on = [...setDeps];
        state.dirty = true;
        render();
      };
      const depLabel = make('label', undefined, 'wp-dep');
      depLabel.append(depInput, ' ' + other.id);
      depsWrap.append(depLabel);
    }
    depsWrap.append(make('p', t('workPlanDepsHelp'), 'wp-help'));
    set.append(depsWrap);

    const requiredWrap = make('fieldset', undefined, 'wp-required');
    requiredWrap.append(make('legend', t('workPlanFieldRequired')));
    requiredWrap.append(make('p', t('workPlanRequiredHelp'), 'wp-help'));
    // Keep stale selections visible so a removed dependency can be explicitly repaired.
    const requiredChoices = new Set([...node.depends_on, ...(node.required_predecessors || [])]);
    for (const dep of requiredChoices) {
      const input = lock(make('input'));
      input.type = 'checkbox';
      input.id = 'wp-required-' + index + '-' + dep;
      input.checked = (node.required_predecessors || []).includes(dep);
      input.onchange = () => {
        if (state.busy) return;
        const required = new Set(node.required_predecessors || []);
        if (input.checked) required.add(dep); else required.delete(dep);
        node.required_predecessors = [...required];
        state.dirty = true;
        render();
      };
      const label = make('label', undefined, 'wp-dep');
      label.append(input, ' ' + dep + (node.depends_on.includes(dep) ? '' : ' ⚠'));
      requiredWrap.append(label);
    }
    set.append(requiredWrap);

    const remove = button('wp-remove-' + index, t('workPlanRemove'), () => {
      if (state.busy) return;
      if (!ask(t('workPlanDiscardConfirm'))) return;
      state.editor.splice(index, 1);
      state.dirty = true;
      render();
    }, state.busy);
    set.append(remove);
    return set;
  };

  const buildEditor = (room) => {
    const parts = [];
    parts.push(make('p', t('workPlanUnsaved'), 'wp-help'));
    for (let index = 0; index < state.editor.length; index += 1) parts.push(editorNode(state.editor[index], index, room));
    const tools = make('div', undefined, 'wp-tools');
    tools.append(
      button('wp-add', t('workPlanAdd'), () => {
        if (state.busy) return;
        const used = new Set(state.editor.map((node) => node.id));
        let counter = state.editor.length + 1;
        while (used.has('node' + counter)) counter += 1;
        state.editor.push({
          id: 'node' + counter,
          title: '',
          instruction: '',
          assignee: '', // explicit placeholder: the human must pick an active agent
          write_scope: 'analysis',
          depends_on: [],
        });
        state.dirty = true;
        render();
      }, state.busy || state.editor.length >= NODE_LIMIT),
      button('wp-save', t('workPlanSave'), () => save(room), state.busy),
      button('wp-cancel', t('workPlanDiscard'), () => cancelEdit(), state.busy),
    );
    parts.push(tools);
    return parts;
  };

  const render = () => {
    if (!state.root || !state.container) return;
    if (state.blocked) {
      // Session loss (401): never re-expose the panel before the host mounts again.
      state.root.replaceChildren();
      state.root.hidden = true;
      return;
    }
    const ctx = getContext();
    if (!allowed(ctx)) {
      purge();
      return;
    }
    if (state.boundSnapshot && !sameSnapshot(ctx, state.boundSnapshot)) {
      // The room / auth ticket / membership epoch or revision that owned the rendered
      // draft changed: drop the draft before the host gets around to clear().
      purgeStale();
    }
    if (state.dispatch.boundSnapshot && !sameSnapshot(ctx, state.dispatch.boundSnapshot)) resetDispatch();
    state.root.hidden = false;
    const parts = [];
    parts.push(make('h3', t('workPlanTitle')));
    parts.push(make('p', t('workPlanScope'), 'wp-scope'));
    const status = make('p', statusText(), 'wp-status');
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    parts.push(status);
    if (state.status === 'inaccessible' || state.status === 'forbidden' || state.status === 'failed') {
      parts.push(button('wp-retry', t('workPlanRetry'), () => reload(), state.busy));
    } else if (state.mode === 'edit') {
      for (const part of buildEditor(ctx.room)) parts.push(part);
    } else {
      for (const part of buildView(ctx.room)) parts.push(part);
    }
    for (const part of buildDispatch(ctx)) parts.push(part);
    state.root.replaceChildren(...parts);
  };

  // Explicit load only (mount never fetches). Rebinds the ownership snapshot that the
  // rendered data belongs to.
  const load = async () => {
    if (state.blocked || state.dispatch.intent || state.dispatch.busy) return;
    const ctx = getContext();
    if (!allowed(ctx)) {
      purge();
      return;
    }
    // A public load must never silently discard unsaved edits.
    if (state.dirty && !ask(t('workPlanDiscardConfirm'))) return;
    if (state.busy) return;
    const owner = state.mountToken;
    const snap = capture(ctx);
    // Selection/preview refer to the previous draft revision; drop them before loading.
    state.selected.clear();
    state.previewText = '';
    state.busy = true;
    state.status = 'loading';
    state.errorKey = '';
    render();
    try {
      const data = await api('rooms/' + encodeURIComponent(snap.rid) + '/work-plan', { method: 'GET' });
      settle(owner, snap, () => {
        const plan = sanitizePlan(data);
        if (!plan) {
          state.status = 'failed';
          render();
          return;
        }
        state.plan = plan;
        state.baseRevision = plan.revision;
        state.boundSnapshot = snap;
        state.editor = [];
        state.mode = 'view';
        state.dirty = false;
        state.selected.clear();
        state.previewText = '';
        resetDispatch();
        state.status = 'loaded';
        render();
      });
    } catch (error) {
      failRequest(owner, snap, error && error.status, false);
    } finally {
      if (owner === state.mountToken) state.busy = false;
    }
  };

  const startEdit = (room) => {
    if (room.state === 'closed' || state.busy || state.dispatch.intent || state.dispatch.busy) return;
    state.editor = (state.plan ? state.plan.nodes : []).map((node) => ({
      id: node.id,
      title: node.title,
      instruction: node.instruction,
      assignee: node.assignee,
      write_scope: node.write_scope,
      depends_on: node.depends_on.slice(),
      ...(Object.hasOwn(node, 'required_predecessors') ? {required_predecessors: node.required_predecessors.slice()} : {}),
    }));
    if (!state.editor.length) {
      state.editor.push({ id: 'node1', title: '', instruction: '', assignee: '', write_scope: 'analysis', depends_on: [] });
    }
    state.mode = 'edit';
    state.dirty = false;
    state.selected.clear();
    clearPreview();
    render();
  };

  const cancelEdit = () => {
    if (state.dirty && !ask(t('workPlanDiscardConfirm'))) return;
    state.editor = [];
    state.mode = 'view';
    state.dirty = false;
    render();
  };

  const save = async (room) => {
    if (state.blocked) return;
    const ctx = getContext();
    if (!allowed(ctx) || state.busy || state.dispatch.busy || state.dispatch.intent) return;
    if (room.state === 'closed') {
      state.errorKey = 'workPlanRoomClosed';
      render();
      return;
    }
    const nodes = collect();
    const editorBefore = JSON.stringify(state.editor);
    const problem = validateDraft(nodes, room);
    if (problem) {
      state.errorKey = problem;
      render();
      return;
    }
    const owner = state.mountToken;
    const snap = capture(ctx);
    const key = newKey();
    state.busy = true;
    state.status = 'saving';
    state.errorKey = '';
    render();
    try {
      const data = await api('rooms/' + encodeURIComponent(snap.rid) + '/work-plan', {
        method: 'POST',
        body: { expected_revision: state.baseRevision, nodes },
        key,
      });
      settle(owner, snap, () => {
        const revision = data && Number.isSafeInteger(data.revision) ? data.revision : state.baseRevision + 1;
        state.plan = { revision, nodes };
        state.baseRevision = revision;
        state.boundSnapshot = snap;
        if (JSON.stringify(state.editor) === editorBefore) {
          state.editor = [];
          state.mode = 'view';
          state.dirty = false;
        } else {
          // Defensive: a programmatic edit slipped in while the request was in flight;
          // keep it as unsaved input instead of silently dropping it.
          state.mode = 'edit';
          state.dirty = true;
        }
        state.selected.clear();
        clearPreview();
        resetDispatch();
        state.status = 'saved';
        render();
      });
    } catch (error) {
      const status = error && error.status;
      if (status === 409) {
        // No automatic retry of the mutation; refetch once to explain the conflict.
        await explainConflict(owner, snap);
        return;
      }
      failRequest(owner, snap, status, true);
    } finally {
      if (owner === state.mountToken) state.busy = false;
    }
  };

  const explainConflict = async (owner, snap) => {
    if (owner !== state.mountToken) return;
    try {
      const data = await api('rooms/' + encodeURIComponent(snap.rid) + '/work-plan', { method: 'GET' });
      if (owner !== state.mountToken) return; // previous mount: discard only
      const ctx = getContext();
      if (!ctx || !sameSnapshot(ctx, snap)) {
        state.busy = false;
        purgeStale();
        render();
        return;
      }
      const plan = sanitizePlan(data);
      state.busy = false;
      if (!plan) state.status = 'failed';
      else if (plan.revision !== state.baseRevision) state.status = 'conflict';
      else state.status = 'rejected';
      render();
    } catch (error) {
      // A failed conflict explanation (401/403/404 included) must drop the unsaved
      // editor too; the old draft text is never kept behind a failure.
      failRequest(owner, snap, error && error.status, true);
    }
  };

  const reload = async () => {
    if (state.dirty && !ask(t('workPlanDiscardConfirm'))) return;
    state.mode = 'view';
    state.editor = [];
    state.dirty = false;
    state.selected.clear();
    clearPreview();
    await load();
  };

  const doCopy = async () => {
    if (!state.previewText || state.busy) return;
    try {
      await copy(state.previewText);
      notify('copied', 'workPlanCopied');
    } catch (error) {
      notify('rejected', 'workPlanCopyFailed');
    }
  };

  const dispatchBusy = () => state.dispatch.busy || state.busy;
  const dispatchAcksReady = () => state.dispatch.acks.queue === true
    && state.dispatch.acks.disclosure === true
    && state.dispatch.acks.unenforced === true;
  const unknownIntent = () => state.dispatch.intent?.unknown === true;
  const dispatchActionsLocked = () => dispatchBusy() || unknownIntent() || state.dispatch.needsRefresh;
  const terminalRepeatRun = () => {
    const run = state.dispatch.run;
    if (!run || run.state === 'running' || !state.plan) return null;
    return run.plan_revision === state.plan.revision ? run : null;
  };
  const repeatPreviousText = (run) => t('workPlanDispatchRepeatPrevious') + ': '
    + run.id + ' · ' + t(RUN_STATE_KEYS[run.state]);
  const resolveIntentAfterRefresh = (run) => {
    const intent = state.dispatch.intent;
    if (!intent) return;
    if (intent.action === 'start') {
      // A status snapshot cannot identify this request, even when it contains a
      // run: an older run may be visible before the original start commits.
      // Permit only an explicit replay, retaining the exact body and key.
      intent.unknown = false;
      return;
    }
    if (run && run.id === intent.runId && run.state === 'running') {
      intent.unknown = false;
      return;
    }
    state.dispatch.intent = null;
  };

  const dispatchStatusText = () => {
    if (unknownIntent()) return t('workPlanDispatchUnknown');
    if (state.dispatch.errorKey) return t(state.dispatch.errorKey);
    if (state.dispatch.status === 'loading') return t('workPlanDispatchLoading');
    if (state.dispatch.status === 'starting') return t('workPlanDispatchStarting');
    if (state.dispatch.status === 'aborting') return t('workPlanDispatchAborting');
    const run = state.dispatch.run;
    if (!run) return t(state.dispatch.status === 'loaded' ? 'workPlanDispatchNoRun' : 'workPlanDispatchNotChecked');
    return t(RUN_STATE_KEYS[run.state])
      + ' · ' + t('workPlanRevision') + ': ' + run.plan_revision
      + ' · ' + t('workPlanDispatchGeneration') + ': ' + run.generation;
  };

  const buildDispatchRun = (run) => {
    const parts = [];
    parts.push(make('h4', t('workPlanDispatchRunTitle')));
    parts.push(make('p', t('workPlanDispatchRunId') + ': ' + run.id
      + ' · ' + t(RUN_STATE_KEYS[run.state])
      + ' · ' + t('workPlanRevision') + ': ' + run.plan_revision
      + ' · ' + t('workPlanDispatchGeneration') + ': ' + run.generation, 'wp-meta'));
    const times = [];
    if (run.started_at !== null) times.push(t('workPlanDispatchStarted') + ': ' + String(run.started_at));
    if (run.finished_at !== null) times.push(t('workPlanDispatchFinished') + ': ' + String(run.finished_at));
    if (times.length) parts.push(make('p', times.join(' · '), 'wp-help'));
    if (run.abort_reason) parts.push(make('p', t('workPlanDispatchAbortReason') + ': ' + run.abort_reason, 'wp-help'));
    if (run.state === 'partial') parts.push(make('p', t('workPlanDispatchPartialHelp'), 'wp-warning'));
    if (run.nodes.length) {
      parts.push(make('h5', t('workPlanDispatchNodesTitle')));
      const list = make('ul', undefined, 'wp-dispatch-nodes');
      for (const node of run.nodes) {
        const item = make('li', undefined, 'wp-dispatch-node');
        item.append(make('strong', node.id));
        item.append(make('p', t('workPlanDispatchNodeAssignee') + ': ' + node.assignee
          + ' · ' + t('workPlanDispatchNodeState') + ': ' + t(NODE_STATE_KEYS[node.state]), 'wp-meta'));
        if (node.task_id) item.append(make('p', t('workPlanDispatchNodeTask') + ': ' + node.task_id, 'wp-meta'));
        if (node.failure) item.append(make('p', t('workPlanDispatchNodeFailure') + ': ' + node.failure, 'wp-warning'));
        if (node.state === 'failed') item.append(make('p', t(node.failure === 'task_uncertain'
          ? 'workPlanDispatchUncertainTaskHelp' : node.failure === 'input_unavailable'
            ? 'workPlanDispatchInputHelp' : 'workPlanDispatchFailedHelp'), 'wp-help'));
        if (node.state === 'blocked') item.append(make('p', t(node.failure === 'dependency_failed'
          ? 'workPlanDispatchDependencyHelp' : 'workPlanDispatchBlockedHelp'), 'wp-help'));
        list.append(item);
      }
      parts.push(list);
    }
    const notes = make('ul', undefined, 'wp-help');
    for (const key of ['workPlanDispatchNoProvider', 'workPlanDispatchWorkerClaim',
      'workPlanDispatchWriteScopeUnenforced', 'workPlanDispatchNoAuto',
      'workPlanDispatchPlanDisclosure', 'workPlanDispatchEvidence']) notes.append(make('li', t(key)));
    parts.push(notes);
    return parts;
  };

  const buildDispatch = (ctx) => {
    if (!state.plan) return [];
    const parts = [];
    parts.push(make('h4', t('workPlanDispatchTitle')));
    parts.push(make('p', t('workPlanDispatchScope'), 'wp-help'));
    parts.push(make('p', t('workPlanDispatchSnapshot'), 'wp-help'));
    const status = make('p', dispatchStatusText(), 'wp-status');
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    parts.push(status);

    const disabled = dispatchBusy();
    const locked = disabled || unknownIntent();
    const fieldset = make('fieldset', undefined, 'wp-dispatch-acks');
    fieldset.append(make('legend', t('workPlanDispatchAcksTitle')));
    for (const [name, key] of [['queue', 'workPlanDispatchAckQueue'],
      ['disclosure', 'workPlanDispatchAckDisclosure'], ['unenforced', 'workPlanDispatchAckUnenforced']]) {
      const label = make('label', undefined, 'wp-dep');
      const input = make('input');
      input.type = 'checkbox';
      input.id = 'wp-dispatch-ack-' + name;
      input.checked = state.dispatch.acks[name] === true;
      input.disabled = locked;
      input.onchange = () => {
        if (dispatchBusy()) return;
        state.dispatch.acks[name] = input.checked === true;
        state.dispatch.errorKey = '';
        render();
      };
      label.append(input, ' ' + t(key));
      fieldset.append(label);
    }
    parts.push(fieldset);

    const repeat = terminalRepeatRun();
    if (repeat) {
      const repeatField = make('fieldset', undefined, 'wp-dispatch-repeat');
      repeatField.append(make('legend', t('workPlanDispatchRepeatPrevious')));
      repeatField.append(make('p', repeatPreviousText(repeat), 'wp-meta'));
      repeatField.append(make('p', t('workPlanDispatchRepeatWarning'), 'wp-warning'));
      const label = make('label', undefined, 'wp-dep');
      const input = make('input');
      input.type = 'checkbox';
      input.id = 'wp-dispatch-repeat-ack';
      input.checked = state.dispatch.repeatAck === true;
      input.disabled = locked || state.dispatch.needsRefresh || state.dispatch.intent?.action !== 'start';
      input.onchange = () => {
        if (dispatchBusy() || unknownIntent() || state.dispatch.needsRefresh) return;
        state.dispatch.repeatAck = input.checked === true;
        state.dispatch.errorKey = '';
        render();
      };
      label.append(input, ' ' + t('workPlanDispatchRepeatAck'));
      repeatField.append(label);
      parts.push(repeatField);
    }

    const running = state.dispatch.run?.state === 'running';
    const startDisabled = Boolean(dispatchActionsLocked() || !dispatchAcksReady() || state.dirty
      || ctx.room.state === 'closed' || running || state.dispatch.intent?.action === 'abort'
      || (repeat && (state.dispatch.intent?.action !== 'start' || !state.dispatch.repeatAck)));
    const tools = make('div', undefined, 'wp-tools');
    tools.append(button('wp-dispatch-start', t('workPlanDispatchStart'), () => startDispatch(), startDisabled));
    tools.append(button('wp-dispatch-refresh', t('workPlanDispatchRefresh'), () => refreshDispatch(), disabled));
    if (running) tools.append(button('wp-dispatch-abort', t('workPlanDispatchAbort'), () => abortDispatch(),
      dispatchActionsLocked() || state.dispatch.intent?.action === 'start'));
    parts.push(tools);
    if (state.dispatch.needsRefresh) parts.push(make('p', t('workPlanDispatchRefreshRequired'), 'wp-help'));
    if (state.dispatch.intent && !unknownIntent() && !state.dispatch.needsRefresh && !dispatchBusy()) {
      parts.push(make('p', t(state.dispatch.intent.action === 'start'
        ? 'workPlanDispatchReplayStartHelp' : 'workPlanDispatchReplayAbortHelp'), 'wp-warning'));
    }
    if (state.dirty) parts.push(make('p', t('workPlanDispatchDirty'), 'wp-help'));
    if (ctx.room.state === 'closed') parts.push(make('p', t('workPlanDispatchClosed'), 'wp-help'));
    if (!dispatchAcksReady()) parts.push(make('p', t('workPlanDispatchAcksRequired'), 'wp-help'));
    if (repeat && !state.dispatch.repeatAck) parts.push(make('p', t('workPlanDispatchRepeatRequired'), 'wp-help'));
    if (state.dispatch.run) {
      if (state.dispatch.queued !== null && state.dispatch.queuedRunId === state.dispatch.run.id) {
        parts.push(state.dispatch.queued === 0
          ? make('p', t('workPlanDispatchQueuedZero'), 'wp-warning')
          : make('p', t('workPlanDispatchQueuedCount') + ': ' + state.dispatch.queued, 'wp-meta'));
      }
      for (const part of buildDispatchRun(state.dispatch.run)) parts.push(part);
    } else {
      const notes = make('ul', undefined, 'wp-help');
      for (const key of ['workPlanDispatchNoProvider', 'workPlanDispatchWorkerClaim',
        'workPlanDispatchWriteScopeUnenforced', 'workPlanDispatchNoAuto']) notes.append(make('li', t(key)));
      parts.push(notes);
    }
    return parts;
  };

  const settleDispatch = (owner, snap, token, apply) => {
    if (owner !== state.mountToken) return false;
    if (state.dispatch.token !== token) return false;
    const ctx = getContext();
    if (!ctx || !sameSnapshot(ctx, snap)) {
      state.dispatch.busy = false;
      purgeStale();
      render();
      return false;
    }
    state.dispatch.busy = false;
    apply(ctx);
    return true;
  };

  const failDispatch = (owner, snap, token, error, action) => {
    if (owner !== state.mountToken) return false;
    if (state.dispatch.token !== token) return false;
    const ctx = getContext();
    if (!ctx || !sameSnapshot(ctx, snap)) {
      state.dispatch.busy = false;
      purgeStale();
      render();
      return false;
    }
    const status = error && error.status;
    const code = error && typeof error.code === 'string' ? error.code : '';
    const definitive = DEFINITIVE_DISPATCH_STATUSES.has(status);
    state.dispatch.busy = false;

    if (action === 'refresh') {
      if (status === 401) {
        state.blocked = true;
        purge();
        return true;
      }
      if (status === 403 || status === 404) {
        state.errorKey = '';
        dropContent();
        state.status = status === 403 ? 'forbidden' : 'inaccessible';
        if (status === 404 && DISPATCH_ERROR_KEYS[code]) state.errorKey = DISPATCH_ERROR_KEYS[code];
        render();
        return true;
      }
      // A failed explicit refresh cannot resolve an unknown mutation; keep the
      // lock and keep showing "outcome unknown" rather than inventing a result.
      if (unknownIntent()) {
        render();
        return true;
      }
      state.dispatch.status = 'loaded';
      state.dispatch.errorKey = DISPATCH_ERROR_KEYS[code] || 'workPlanDispatchFailed';
      render();
      return true;
    }

    if (definitive && (status === 401 || status === 403 || status === 404
      || !(action === 'start' && state.dispatch.intent?.uncertain))) {
      state.dispatch.intent = null;
    } else {
      // A rejection of a replay (including a retired-key conflict) says nothing
      // conclusive about the original attempt. Never rotate its key.
      if (state.dispatch.intent) {
        state.dispatch.intent.unknown = true;
        state.dispatch.intent.uncertain = true;
      }
      state.dispatch.status = 'loaded';
      state.dispatch.errorKey = '';
      state.dispatch.needsRefresh = true;
      render();
      return true;
    }

    if (status === 401) {
      state.blocked = true;
      purge();
      return true;
    }
    if (status === 403 || status === 404) {
      state.errorKey = '';
      dropContent();
      state.status = status === 403 ? 'forbidden' : 'inaccessible';
      // 403/404 always purge the private content. A known 404 plan_missing code may
      // still explain why dispatch failed, without exposing any draft text.
      if (status === 404 && DISPATCH_ERROR_KEYS[code]) state.errorKey = DISPATCH_ERROR_KEYS[code];
      render();
      return true;
    }
    state.dispatch.status = 'loaded';
    state.dispatch.errorKey = DISPATCH_ERROR_KEYS[code]
      || (status === 409 && action === 'start'
        ? 'workPlanDispatchStartConflict'
        : status === 409 && action === 'abort'
          ? 'workPlanDispatchAbortConflict'
          : 'workPlanDispatchFailed');
    render();
    return true;
  };

  const refreshDispatch = async () => {
    if (state.blocked || dispatchBusy()) return;
    const ctx = getContext();
    if (!allowed(ctx) || !state.plan) return;
    const owner = state.mountToken;
    const snap = capture(ctx);
    const token = ++state.dispatch.token;
    state.dispatch.busy = true;
    state.dispatch.status = 'loading';
    state.dispatch.errorKey = '';
    render();
    try {
      const data = await api('rooms/' + encodeURIComponent(snap.rid) + '/work-plan/dispatch', {method: 'GET'});
      settleDispatch(owner, snap, token, () => {
        const valid = data && typeof data === 'object' && !Array.isArray(data);
        const hasRun = valid && Object.prototype.hasOwnProperty.call(data, 'run');
        const run = hasRun && data.run !== null && data.run !== undefined ? dispatchRun(data.run) : null;
        const malformed = !valid || !hasRun || (data.run !== null && data.run !== undefined && run === null);
        state.dispatch.boundSnapshot = snap;
        state.dispatch.status = 'loaded';
        if (malformed) {
          // A malformed refresh must not erase the last human-visible run or
          // resolve an unknown mutation; the next explicit refresh is the only
          // way to replace it.
          state.dispatch.errorKey = unknownIntent() ? '' : 'workPlanDispatchInvalid';
          render();
          return;
        }
        state.dispatch.errorKey = '';
        resolveIntentAfterRefresh(run);
        state.dispatch.needsRefresh = false;
        if (!run || run.id !== state.dispatch.queuedRunId) {
          state.dispatch.queued = null;
          state.dispatch.queuedRunId = null;
        }
        const previousRunId = state.dispatch.run?.id;
        state.dispatch.run = run;
        if (previousRunId !== run?.id) state.dispatch.repeatAck = false;
        render();
      });
    } catch (error) {
      failDispatch(owner, snap, token, error, 'refresh');
    } finally {
      if (owner === state.mountToken && state.dispatch.token === token) state.dispatch.busy = false;
    }
  };

  const startDispatch = async () => {
    if (state.blocked || dispatchActionsLocked()) return;
    const ctx = getContext();
    if (!allowed(ctx) || !state.plan) return;
    if (state.dirty) {
      state.dispatch.status = 'loaded';
      state.dispatch.errorKey = 'workPlanDispatchDirty';
      render();
      return;
    }
    if (ctx.room.state === 'closed') {
      state.dispatch.status = 'loaded';
      state.dispatch.errorKey = 'workPlanDispatchClosed';
      render();
      return;
    }
    if (!dispatchAcksReady()) {
      state.dispatch.status = 'loaded';
      state.dispatch.errorKey = 'workPlanDispatchAcksRequired';
      render();
      return;
    }
    if (state.dispatch.run?.state === 'running') return;
    const prior = state.dispatch.intent;
    if (prior && prior.action !== 'start') return;
    const repeat = terminalRepeatRun();
    if (repeat && (!prior || !state.dispatch.repeatAck)) {
      state.dispatch.status = 'loaded';
      state.dispatch.errorKey = 'workPlanDispatchRepeatRequired';
      render();
      return;
    }
    const body = prior ? prior.body : {
      expected_plan_revision: state.plan.revision,
      expected_generation: ctx.room.generation,
      acknowledge_plan_dispatch: true,
      acknowledge_plan_disclosure: true,
      acknowledge_write_scope_unenforced: true,
    };
    let summary = t('workPlanDispatchConfirm') + '\n\n'
      + t('workPlanDispatchConfirmDisclosure') + '\n\n'
      + t('workPlanRevision') + ': ' + body.expected_plan_revision + '\n'
      + t('workPlanDispatchGeneration') + ': ' + body.expected_generation;
    if (repeat) summary += '\n\n' + repeatPreviousText(repeat) + '\n' + t('workPlanDispatchRepeatWarning');
    if (!ask(summary)) return;
    const owner = state.mountToken;
    const snap = capture(ctx);
    const token = ++state.dispatch.token;
    const key = prior ? prior.key : newKey();
    state.dispatch.intent = {action: 'start', key, body, unknown: false, uncertain: prior?.uncertain === true};
    state.dispatch.busy = true;
    state.dispatch.status = 'starting';
    state.dispatch.errorKey = '';
    render();
    try {
      const data = await api('rooms/' + encodeURIComponent(snap.rid) + '/work-plan/dispatch', {
        method: 'POST',
        body,
        key,
      });
      settleDispatch(owner, snap, token, () => {
        const valid = data && typeof data === 'object' && !Array.isArray(data);
        const run = valid ? dispatchRun(data.run) : null;
        const queued = valid && Number.isSafeInteger(data.tasks_queued) && data.tasks_queued >= 0
          ? data.tasks_queued : null;
        state.dispatch.boundSnapshot = snap;
        state.dispatch.status = 'loaded';
        if (!run) {
          // A 2xx without a valid run is a malformed success: the outcome is
          // unknown and the same idempotency key must be kept for replay.
          if (state.dispatch.intent) {
            state.dispatch.intent.unknown = true;
            state.dispatch.intent.uncertain = true;
          }
          state.dispatch.errorKey = '';
          state.dispatch.needsRefresh = true;
          render();
          return;
        }
        state.dispatch.intent = null;
        state.dispatch.errorKey = '';
        state.dispatch.needsRefresh = true;
        state.dispatch.repeatAck = false;
        state.dispatch.run = run;
        state.dispatch.queued = queued;
        state.dispatch.queuedRunId = queued === null ? null : run.id;
        render();
      });
    } catch (error) {
      failDispatch(owner, snap, token, error, 'start');
    } finally {
      if (owner === state.mountToken && state.dispatch.token === token) state.dispatch.busy = false;
    }
  };

  const abortDispatch = async () => {
    if (state.blocked || dispatchActionsLocked() || state.dispatch.intent?.action === 'start') return;
    const ctx = getContext();
    const run = state.dispatch.run;
    if (!allowed(ctx) || !state.plan || !run || run.state !== 'running') return;
    if (!ask(t('workPlanDispatchAbortConfirm'))) return;
    const body = {run_id: run.id, acknowledge_abort: true};
    const prior = state.dispatch.intent;
    const reusable = prior && prior.action === 'abort' && prior.unknown === false
      && prior.runId === run.id;
    const owner = state.mountToken;
    const snap = capture(ctx);
    const token = ++state.dispatch.token;
    const key = reusable ? prior.key : newKey();
    state.dispatch.intent = {action: 'abort', key, body, runId: run.id, unknown: false};
    state.dispatch.busy = true;
    state.dispatch.status = 'aborting';
    state.dispatch.errorKey = '';
    render();
    try {
      const data = await api('rooms/' + encodeURIComponent(snap.rid) + '/work-plan/dispatch/abort', {
        method: 'POST',
        body,
        key,
      });
      settleDispatch(owner, snap, token, () => {
        const valid = data && typeof data === 'object' && !Array.isArray(data);
        const next = valid ? dispatchRun(data.run) : null;
        state.dispatch.boundSnapshot = snap;
        state.dispatch.status = 'loaded';
        if (!next) {
          // As with start, a malformed 2xx is not proof that abort happened.
          if (state.dispatch.intent) {
            state.dispatch.intent.unknown = true;
            state.dispatch.intent.uncertain = true;
          }
          state.dispatch.errorKey = '';
          state.dispatch.needsRefresh = true;
          render();
          return;
        }
        state.dispatch.intent = null;
        state.dispatch.errorKey = '';
        state.dispatch.needsRefresh = true;
        state.dispatch.repeatAck = false;
        state.dispatch.run = next;
        if (next.id !== state.dispatch.queuedRunId) {
          state.dispatch.queued = null;
          state.dispatch.queuedRunId = null;
        }
        render();
      });
    } catch (error) {
      failDispatch(owner, snap, token, error, 'abort');
    } finally {
      if (owner === state.mountToken && state.dispatch.token === token) state.dispatch.busy = false;
    }
  };

  const view = {
    mount(container) {
      if (!container) throw new TypeError('work-plan: container is required');
      // Atomically discard the previous mount (root + in-memory draft) before the new
      // container can render anything, even if the host forgot clear().
      state.mountToken += 1;
      if (state.container) state.container.replaceChildren();
      purge();
      state.blocked = false;
      state.busy = false;
      state.container = container;
      state.root = make('section', undefined, 'work-plan');
      state.root.id = 'work-plan-module';
      state.root.hidden = true;
      const ctx = getContext();
      if (ctx && ctx.language) state.lang = ctx.language;
      container.replaceChildren(state.root);
      if (allowed(ctx)) state.boundSnapshot = capture(ctx);
      state.status = 'idle';
      state.errorKey = '';
      if (state.mountToken > 0) render();
      return view;
    },
    render,
    load,
    reload,
    setLanguage(language) {
      if (language !== 'ko' && language !== 'en') return;
      // Labels only: unsaved editor values live in state.editor, so they survive,
      // and no GET is issued.
      state.lang = language;
      render();
    },
    clear() {
      state.mountToken += 1;
      purge();
      if (state.container) state.container.replaceChildren();
      state.root = null;
      state.container = null;
    },
    destroy() {
      view.clear();
    },
    isDirty() {
      return state.dirty;
    },
  };
  return view;
}

export default createWorkPlanView;
