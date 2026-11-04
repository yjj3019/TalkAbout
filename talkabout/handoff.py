"""Read-only aggregate projection of the current plan dispatch.

The caller must authorize the owner and the run/plan epoch before calling this
function. This projection is descriptive, not a completion or safety proof.
It deliberately does not return identifiers, plan text, or task contents.
"""

_NODE_STATES = ('pending', 'queued', 'completed', 'failed', 'blocked')


def project_handoff(room):
    """Summarize only the current run and its explicitly associated tasks.

    Missing, duplicate or inconsistent links are counted as unknown rather than
    inferred from task history. An acknowledgement does not establish the
    external effect of an uncertain attempt. This function never mutates room.
    """
    run = room.get('_plan_dispatch')
    if run is None:
        return {'run': None}
    if not isinstance(run, dict):
        return {'run': {'node_counts': {**dict.fromkeys(_NODE_STATES, 0), 'unknown': 0},
                        'uncertain_tasks': 0, 'acknowledged_uncertain_tasks': 0,
                        'unknown_associations': 1}}
    counts = {**dict.fromkeys(_NODE_STATES, 0), 'unknown': 0}
    nodes = run.get('nodes')
    tasks = room.get('tasks')
    unknown = 0
    uncertain = acknowledged = 0
    if not isinstance(nodes, list):
        nodes = []
        unknown += 1
    if not isinstance(tasks, dict):
        tasks = {}
        unknown += 1
    run_id = run.get('id')
    valid_run_id = isinstance(run_id, str) and bool(run_id)
    if not valid_run_id:
        unknown += 1
    # Repeated node or task identifiers are ambiguous, including when the first
    # matching task appears valid. Never count one task for multiple nodes.
    node_ids = [n.get('id') for n in nodes if isinstance(n, dict)]
    task_ids = [n.get('task_id') for n in nodes if isinstance(n, dict)]
    for node in nodes:
        if not isinstance(node, dict):
            counts['unknown'] += 1
            unknown += 1
            continue
        state = node.get('state')
        counts[state if isinstance(state, str) and state in _NODE_STATES else 'unknown'] += 1
        node_id, task_id = node.get('id'), node.get('task_id')
        if not isinstance(node_id, str) or not node_id or node_ids.count(node_id) != 1:
            unknown += 1
            continue
        if task_id is None:
            # Pending/blocked and failures before admission need no task. A queued
            # or completed node without one is inconsistent.
            if state not in ('pending', 'blocked', 'failed'):
                unknown += 1
            continue
        if not isinstance(task_id, str) or not task_id or task_ids.count(task_id) != 1 or not valid_run_id:
            unknown += 1
            continue
        task = tasks.get(task_id)
        plan = task.get('_plan') if isinstance(task, dict) else None
        if not (isinstance(task, dict) and task.get('id') == task_id
                and task.get('purpose') == 'plan_node' and isinstance(plan, dict)
                and plan.get('run_id') == run_id and plan.get('node_id') == node_id
                and isinstance(run.get('generation'), int) and not isinstance(run.get('generation'), bool)
                and task.get('generation') == run['generation']
                and isinstance(node.get('assignee'), str) and task.get('assignee') == node['assignee']):
            unknown += 1
            continue
        task_state = task.get('state')
        if task_state == 'uncertain':
            uncertain += 1
            resolution = task.get('resolution')
            if (isinstance(resolution, dict) and type(task.get('attempt')) is int
                    and type(resolution.get('attempt')) is int
                    and resolution['attempt'] == task['attempt']
                    and resolution.get('external_effect') == 'unknown'):
                acknowledged += 1
        elif not isinstance(task_state, str) or task_state not in (
                'queued', 'leased', 'completed', 'expired', 'cancelled', 'skipped', 'failed'):
            unknown += 1
    return {'run': {'node_counts': counts, 'uncertain_tasks': uncertain,
                    'acknowledged_uncertain_tasks': acknowledged,
                    'unknown_associations': unknown}}
