"""Human coordination dashboard: what is producing and what the human must decide.

TalkAbout-owned, read-only projection of room state for the person moderating a
class-room of agents (docs/CLEAN_ROOM_IMPLEMENTATION.md). It never mutates room
state, never marks anything seen, never calls a model, and only reports facts the
server already committed. Decisions come from a fixed vocabulary so consumers
fail closed on unknown values. Artifact summaries are counts and ids, not content.
"""

HUMAN_DECISION_SET = frozenset({
    'approve_pending_controls',   # a control request awaits the human's approval
    'release_creation_artifact',  # co-creation creator draft awaits human adoption
    'resume_or_close',            # room is paused; the human must resume or close
    'release_dispatch',           # plan dispatch is held until the human releases it
})

DECISION_TITLES = {
    'approve_pending_controls': 'Pending control requests need your approval',
    'release_creation_artifact': 'A co-creation draft is ready for your adoption',
    'resume_or_close': 'Discussion is paused -- resume or close',
    'release_dispatch': 'Plan dispatch is held -- review and release',
}


def room_production(room, *, membership_revision):
    """Project production status and open human decisions (pure, detached).

    ``room`` is an internal room dict; only a fixed set of fields is read and
    nothing is copied back. Decisions are ordered by the closed vocabulary.
    """
    decisions = []
    controls = room.get('controls') or {}
    if any(isinstance(c, dict) and c.get('state') == 'pending' for c in controls.values()):
        decisions.append('approve_pending_controls')
    if (room.get('mode') == 'co_creation' and room.get('mode_status') == 'awaiting_adoption'
            and room.get('creation_artifact_id') in (room.get('artifacts') or {})):
        decisions.append('release_creation_artifact')
    if room.get('state') == 'paused':
        decisions.append('resume_or_close')
    if room.get('dispatch_held'):
        decisions.append('release_dispatch')

    artifacts = []
    for aid, artifact in (room.get('artifacts') or {}).items():
        if not isinstance(artifact, dict):
            continue
        versions = artifact.get('versions') or []
        latest = versions[-1] if versions and isinstance(versions[-1], dict) else None
        artifacts.append({
            'artifact_id': aid,
            'revision': artifact.get('revision', 0),
            'version_count': len([v for v in versions if isinstance(v, dict)]),
            'latest_ai_generated': latest.get('ai_generated') if latest else None,
            'adopted_version': artifact.get('adopted'),
        })
    artifacts.sort(key=lambda item: item['artifact_id'])

    return {
        'room_id': room.get('id'),
        'room_state': room.get('state'),
        'mode': room.get('mode'),
        'mode_status': room.get('mode_status'),
        'deliverable': room.get('deliverable'),
        'membership_revision': membership_revision,
        'last_seq': room.get('last_seq'),
        'open_human_decisions': sorted(d for d in HUMAN_DECISION_SET if d in decisions),
        'artifacts': artifacts,
    }
