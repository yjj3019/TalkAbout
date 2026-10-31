"""Read-only waiting reasons for room tasks.

Independent implementation for TalkAbout. The reason vocabulary, rules and shape
were designed for this repository from observable product behaviour; no external
source code, string, table, fixture or document text was copied. See
docs/CLEAN_ROOM_IMPLEMENTATION.md.

Boundaries:
- Observation only. These functions never mutate the room, never write events and
  never call a provider. An empty reason list means "not waiting" for terminal
  tasks and "not observed" otherwise; it never means allowed, verified or run.
- Closed vocabulary. Every returned value is in WAITING_REASONS. Reasons are
  derived from the current room/task fields at call time and are not stored.
"""
import time

# Stable vocabulary. Add values only with a contract test and UI/i18n coverage.
WAITING_REASONS = (
    'room_not_active',
    'dispatch_held',
    'awaiting_worker_claim',
    'worker_leased',
    'lease_expired',
    'awaiting_start_grant',
    'deadline_passed',
    'awaiting_human_resolution',
)
REASON_SET = frozenset(WAITING_REASONS)

ACTIVE_TASK_STATES = frozenset({'queued', 'leased'})
TERMINAL_TASK_STATES = frozenset({'completed', 'failed', 'cancelled', 'skipped'})


def _current_attempt_reservation(task):
    """Latest reservation for this attempt; internal fields never leave this module."""
    attempt = task.get('attempt')
    reservations = task.get('_reservations') or ()
    for item in reversed(list(reservations)):
        if isinstance(item, dict) and item.get('attempt') == attempt:
            return item
    return None


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def waiting_reasons(room, task, now=None):
    """Return ordered, closed-enum reasons for one task.

    Terminal tasks return []. States this projection does not model also return []
    instead of guessing, so an unknown state is not reported as blocked or allowed.
    """
    now = time.time() if now is None else now
    state = task.get('state')
    if state in TERMINAL_TASK_STATES:
        return []
    if state == 'uncertain':
        # Human resolution is recorded separately from automatic retry; never
        # collapse it into a generic "busy" reason.
        return ['awaiting_human_resolution']
    if state not in ACTIVE_TASK_STATES:
        return []

    reasons = []
    if room.get('state') != 'active':
        reasons.append('room_not_active')
    if room.get('dispatch_held'):
        reasons.append('dispatch_held')
    if state == 'queued':
        reasons.append('awaiting_worker_claim')
    else:
        lease_expires_at = _number(task.get('lease_expires_at'))
        if lease_expires_at is not None and now >= lease_expires_at:
            reasons.append('lease_expired')
        else:
            reasons.append('worker_leased')
    reservation = _current_attempt_reservation(task)
    if isinstance(reservation, dict) and reservation.get('state') == 'reserved':
        reasons.append('awaiting_start_grant')
    deadline = _number(task.get('deadline'))
    if deadline is not None and now >= deadline:
        reasons.append('deadline_passed')
    return reasons


def room_diagnostics(room, membership_revision=None, now=None):
    """Detached, read-only projection of task waiting reasons for one room.

    The caller must have already authorized room:read. This function does not check
    ACLs, does not filter by principal, and does not replace the room snapshot.
    """
    now = time.time() if now is None else now
    tasks = []
    raw_tasks = room.get('tasks') if isinstance(room.get('tasks'), dict) else {}
    for task in raw_tasks.values():
        if not isinstance(task, dict):
            continue
        tasks.append({
            'task_id': task.get('id'),
            'state': task.get('state'),
            'assignee': task.get('assignee'),
            'attempt': task.get('attempt'),
            'waiting_reasons': waiting_reasons(room, task, now),
        })
    tasks.sort(key=lambda item: (str(item['task_id']), str(item['state'])))
    return {
        'room_id': room.get('id'),
        'room_state': room.get('state'),
        'dispatch_held': bool(room.get('dispatch_held')),
        'last_seq': room.get('last_seq'),
        'membership_revision': membership_revision,
        'tasks': tasks,
    }
