"""Project task mutation snapshots against current room visibility.

Idempotency verifies identity, key, body and membership epoch separately. This
module does not alter that check or the stored response: it returns a detached
copy at the time of delivery, after the caller has performed operation ACLs.
"""
import copy

from .collaboration import project_manifest, project_task
from .domain import authorize, require
from .text_files import filter_refs, public_task


def project_task_response(room, actor, response):
    """Project a task or complete-response snapshot using current read/file ACLs.

    Call for both newly produced and replayed task responses, *after* replay's
    authorization and fingerprint/epoch checks. Never call for start/reservation
    grants: those are separate uncached protocol responses. Historical scalar
    values and content are not refreshed from the live task; only visibility is.
    Hidden completion messages become None, and an artifact with no visible
    historical versions is omitted. This may shrink a replay after ACL changes.
    """
    member = authorize(actor, room, 'room:read')
    # Local import avoids a core import cycle and ensures the same view and ACL
    # semantics used for reads, including history boundaries and actor visibility.
    from .core import public_room
    view = public_room(room, actor)
    visible_tasks = {task['id'] for task in view['tasks']}
    messages, artifacts = view['messages'], view['artifacts']
    visible_messages = {message['id'] for message in messages}
    visible_versions = {(artifact['id'], version['id']) for artifact in artifacts
                        for version in artifact['versions']}

    def task_projection(snapshot):
        require(isinstance(snapshot, dict) and snapshot.get('id') in visible_tasks
                and (actor['kind'] == 'human' or snapshot.get('assignee') == actor['id']),
                'not_found', 404)
        result = public_task(snapshot)
        if 'attachment_refs' in result:
            result['attachment_refs'] = filter_refs(actor, room, member, result['attachment_refs'])
        return project_task(result, messages, artifacts)

    def message_projection(snapshot):
        if not isinstance(snapshot, dict) or snapshot.get('id') not in visible_messages:
            return None
        result = copy.deepcopy(snapshot)
        if 'attachment_refs' in result:
            result['attachment_refs'] = filter_refs(actor, room, member, result['attachment_refs'])
        return result

    def artifact_projection(snapshot):
        if not isinstance(snapshot, dict):
            return None
        aid = snapshot.get('id')
        result = copy.deepcopy(snapshot)
        result['versions'] = [version for version in result.get('versions', [])
                              if (aid, version.get('id')) in visible_versions]
        if not result['versions']:
            return None
        for version in result['versions']:
            references = version.get('input_references')
            if not references:
                continue
            allowed = filter_refs(actor, room, member,
                [source['ref'] for source in references.get('sources', [])
                 if source.get('kind') == 'file_excerpt'])
            version['input_references'] = project_manifest(references, messages, artifacts, allowed)
            version['sources'] = [source['source_id'] for source in version['input_references']['sources']]
        return result

    require(isinstance(response, dict), 'not_found', 404)
    if 'task' not in response:
        return task_projection(response)
    result = copy.deepcopy(response)
    result['task'] = task_projection(response['task'])
    if 'message' in response:
        result['message'] = message_projection(response['message'])
    if 'artifact' in response:
        artifact = artifact_projection(response['artifact'])
        if artifact is None:
            result.pop('artifact', None)
        else:
            result['artifact'] = artifact
    return result
