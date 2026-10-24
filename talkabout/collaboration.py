"""Bounded shared text and verifiable references, never proof of model reading.

The caller must supply an ACL-filtered public room. No retrieval, provider call,
summary generation or permission grant happens here.
"""
import copy

from .runtime import MAX_RUNTIME_INPUT, RuntimeInputError, runtime_prompt

MAX_SHARED_TEXT_BYTES = 72000
MAX_MESSAGES = 8
MAX_VERSIONS = 4


def excerpt(text, budget):
    try:
        raw = text.encode('utf-8')
        selected = raw[:max(0, budget)].decode('utf-8', errors='ignore')
    except (AttributeError, UnicodeError):
        raise RuntimeInputError('invalid_runtime_context') from None
    used = len(selected.encode('utf-8'))
    return selected, {'utf8_start': 0, 'utf8_end': used,
                      'total_utf8_bytes': len(raw), 'truncated': used < len(raw)}


def source_id(source):
    prefix = 'talkabout:room:' + source['room_id']
    if source['kind'] == 'message':
        return prefix + ':message:' + source['message_id']
    if source['kind'] == 'artifact_version':
        return prefix + ':artifact:' + source['artifact_id'] + ':version:' + source['version_id']
    return prefix + ':file:' + source['ref']['file_id'] + ':version:' + source['ref']['version_id']


def _omissions(all_items, selected, key):
    missing = [item for item in all_items if key(item) not in selected]
    seqs = [item['seq'] for item in missing if 'seq' in item]
    return {'count': len(missing), 'seq_range': [min(seqs), max(seqs)] if seqs else None}


def project_manifest(manifest, messages, artifacts, file_refs=()):
    if not manifest:
        return manifest
    visible_messages = {m['id'] for m in messages}
    visible_versions = {(a['id'], v['id']) for a in artifacts for v in a['versions']}
    result = copy.deepcopy(manifest)
    sources = manifest.get('sources', [])
    result['sources'] = [copy.deepcopy(source) for source in sources if
        (source['kind'] == 'message' and source['message_id'] in visible_messages) or
        (source['kind'] == 'artifact_version' and (source['artifact_id'], source['version_id']) in visible_versions) or
        (source['kind'] == 'file_excerpt' and source['ref'] in file_refs)]
    result['redacted_source_count'] = len(sources) - len(result['sources'])
    return result


def project_task(task, messages, artifacts):
    """Redact newly added collaboration metadata at the viewer's history boundary."""
    result = copy.deepcopy(task)
    visible = {m['id'] for m in messages}
    if result.get('result_message_id') not in visible:
        result.pop('result_message_id', None)
    if result.get('handoff', {}).get('message_id') not in visible:
        if 'handoff' in result:
            result['handoff'] = {'result_available': False}
    target = result.get('review_target')
    if target and not any(a['id'] == target['artifact_id'] and any(v['id'] == target['version_id'] for v in a['versions']) for a in artifacts):
        result.pop('review_target', None)
        result['review_target_available'] = False
    if result.get('input_manifest'):
        result['input_manifest'] = project_manifest(result['input_manifest'], messages, artifacts, result.get('attachment_refs', []))
    return result


def shared_context(room, task):
    messages = sorted(room['messages'], key=lambda item: item['seq'])
    versions = [(artifact, version) for artifact in room['artifacts'] for version in artifact['versions']]
    chosen_messages = []
    task = project_task(task, messages, room['artifacts'])
    handoff = task.get('handoff')
    for mid in [room.get('pinned_message_id'), (handoff or {}).get('message_id')]:
        match = next((item for item in messages if item['id'] == mid), None)
        if match and match not in chosen_messages:
            chosen_messages.append(match)
    for item in reversed(messages):
        if item not in chosen_messages and len(chosen_messages) < MAX_MESSAGES:
            chosen_messages.append(item)
    target = task.get('review_target')
    chosen_versions = []
    if target:
        chosen_versions.extend((a, v) for a, v in versions
                               if a['id'] == target['artifact_id'] and v['id'] == target['version_id'])
    for artifact, version in reversed(versions):
        if artifact.get('adopted') == version['id'] and (artifact, version) not in chosen_versions:
            chosen_versions.append((artifact, version))
    for artifact in sorted(room['artifacts'], key=lambda a: max(v['seq'] for v in a['versions']), reverse=True):
        pair = (artifact, artifact['versions'][-1])
        if pair not in chosen_versions:
            chosen_versions.append(pair)
    chosen_versions = chosen_versions[:MAX_VERSIONS]
    budget = MAX_SHARED_TEXT_BYTES
    inputs, proposals, references = [], [], []
    # Exact review target and the pinned/handoff messages have priority over bulk history.
    for artifact, version in chosen_versions:
        text, limits = excerpt(version['text'], min(12000, budget))
        budget -= limits['utf8_end']
        reference = {'kind': 'artifact_version', 'room_id': room['id'], 'artifact_id': artifact['id'],
                     'version_id': version['id'], 'revision': version['version_revision'],
                     'actor_id': version['actor_id'], 'seq': version['seq'], **limits}
        reference['source_id'] = source_id(reference)
        references.append(reference)
        proposals.append({**reference, 'text': text, 'head_snapshot_revision': artifact['revision'], 'adopted': artifact.get('adopted') == version['id'],
                          'sources': [excerpt(value, 256)[0] for value in version.get('sources', [])[:4]],
                          'omitted_sources': max(0, len(version.get('sources', [])) - 4), 'untrusted_data': True})
    for index, item in enumerate(chosen_messages):
        text, limits = excerpt(item['text'], min(6000, budget // (len(chosen_messages) - index)))
        budget -= limits['utf8_end']
        reference = {'kind': 'message', 'room_id': room['id'], 'message_id': item['id'],
                     'actor_id': item['actor_id'], 'seq': item['seq'], **limits}
        reference['source_id'] = source_id(reference)
        references.append(reference)
        inputs.append({key: item[key] for key in ('id', 'actor_id', 'actor_kind', 'display_name', 'seq', 'kind')}
                      | {'text': text, **limits, 'source_id': reference['source_id'], 'untrusted_data': True})
    inputs.sort(key=lambda item: item['seq'])
    selected_messages = {item['id'] for item in chosen_messages}
    selected_versions = {v['id'] for _, v in chosen_versions}
    manifest = {'snapshot_seq': room['last_seq'], 'event_range': [min((r['seq'] for r in references), default=room['last_seq']), room['last_seq']],
                'sources': references, 'summary': {'status': 'not_generated', 'covered_event_range': None},
                'omissions': {'messages': _omissions(messages, selected_messages, lambda m: m['id']),
                              'artifact_versions': _omissions([v for _, v in versions], selected_versions, lambda v: v['id'])},
                'evidence': 'prepared_worker_input_not_model_read_receipt'}
    return {'inputs': inputs, 'artifact_proposals': proposals, 'input_manifest': manifest,
            'handoff': copy.deepcopy(handoff), 'review_target': copy.deepcopy(target),
            'pinned_message_id': room.get('pinned_message_id') if room.get('pinned_message_id') in selected_messages else None,
            'review_target_available': bool(target and any(v['version_id'] == target['version_id'] for v in proposals))}


def add_file_sources(context, excerpts, *, available_count=None):
    context['input_manifest']['omissions']['files'] = {
        'count': max(0, (len(excerpts) if available_count is None else available_count) - len(excerpts))}
    for item in excerpts:
        source = {'kind': 'file_excerpt', 'room_id': context['room_id'], 'ref': copy.deepcopy(item['ref']),
                  'start': item['start'], 'end': item['end'], 'truncated': item['truncated']}
        source['source_id'] = source_id(source)
        context['input_manifest']['sources'].append(source)


def fit_context(context):
    """Enforce the actual serialized prompt limit, including JSON escaping/files.

    Text reductions update their provided ranges. Metadata is never silently
    discarded, and an unavailable budget remains a pre-dispatch input error.
    """
    manifest = context['input_manifest']
    manifest['budget'] = {'max_serialized_utf8_bytes': MAX_RUNTIME_INPUT}
    target = context.get('review_target') or {}
    while True:
        try:
            runtime_prompt(context)
            return
        except RuntimeInputError as error:
            if str(error) != 'invalid_runtime_budget':
                raise
        candidates = []
        for group in ('inputs', 'artifact_proposals', 'file_excerpts'):
            for item in context.get(group, []):
                length = len(item['text'].encode('utf-8'))
                protected = (group == 'inputs' and item.get('id') == context.get('pinned_message_id')) or (
                    group == 'artifact_proposals' and item.get('version_id') == target.get('version_id'))
                if length > (64 if protected else 0):
                    candidates.append((not protected, length, group, item, 64 if protected else 0))
        if not candidates:
            raise RuntimeInputError('invalid_runtime_budget')
        _, length, group, item, floor = max(candidates, key=lambda value: value[:2])
        item['text'], limits = excerpt(item['text'], max(floor, length // 2))
        if group == 'file_excerpts':
            item['end'] = item['start'] + len(item['text'])
            item['line_end'] = item['line_start'] + item['text'][:-1].count('\n')
            item['truncated'] = True
            sid = source_id({'kind': 'file_excerpt', 'room_id': context['room_id'], 'ref': item['ref']})
            ref = next(source for source in manifest['sources'] if source['source_id'] == sid)
            ref.update(end=item['end'], truncated=True)
        else:
            item['utf8_end'] = limits['utf8_end']
            item['truncated'] = item['utf8_end'] < item['total_utf8_bytes']
            ref = next(source for source in manifest['sources'] if source['source_id'] == item['source_id'])
            ref.update(utf8_end=item['utf8_end'], truncated=item['truncated'])


def validate_manifest(manifest, room, task):
    """Validate worker-reported immutable references against current authorized data.

    This proves reference existence and scope, not delivery, comprehension or a
    provider attestation. Legacy clients may omit the manifest.
    """
    from .domain import require
    if manifest is None:
        return {'evidence': 'legacy_input_unverified', 'sources': []}
    require(isinstance(manifest, dict) and type(manifest.get('snapshot_seq')) is int
            and 0 <= manifest['snapshot_seq'] <= room['last_seq'], 'invalid_input_manifest', 422)
    sources = manifest.get('sources')
    require(isinstance(sources, list) and len(sources) <= MAX_MESSAGES + MAX_VERSIONS + 4, 'invalid_input_manifest', 422)
    known_messages = {m['id']: m for m in room['messages']}
    known_artifacts = {a['id']: a for a in room['artifacts']}
    verified = []
    seen = set()
    for source in sources:
        require(isinstance(source, dict) and source.get('room_id') == room['id'], 'invalid_input_source', 422)
        kind = source.get('kind')
        identifiers = {'message': ('message_id',), 'artifact_version': ('artifact_id', 'version_id'), 'file_excerpt': ()}
        require(isinstance(kind, str) and kind in identifiers, 'invalid_input_source', 422)
        require(all(isinstance(source.get(key), str) and 1 <= len(source[key]) <= 128 for key in identifiers[kind]), 'invalid_input_source', 422)
        if kind == 'message':
            original = known_messages.get(source.get('message_id'))
        elif kind == 'artifact_version':
            artifact = known_artifacts.get(source.get('artifact_id'))
            original = next((v for v in artifact['versions'] if v['id'] == source.get('version_id')), None) if artifact else None
            require(original and type(source.get('revision')) is int and source['revision'] == original['version_revision'], 'invalid_input_source', 422)
        elif kind == 'file_excerpt':
            ref = source.get('ref')
            scoped_task = next((t for t in room['tasks'] if t['id'] == task['id']), {})
            require(isinstance(ref, dict) and ref in scoped_task.get('attachment_refs', []), 'invalid_input_source', 422)
            read = task.get('_file_reads', {}).get(f"file-read:{task['id']}:{task['attempt']}:{ref['file_id']}")
            require(type(source.get('start')) is int and type(source.get('end')) is int and read
                    and read['range'][0] == source['start'] <= source['end'] <= read['range'][1], 'invalid_input_source', 422)
            normalized = {k: copy.deepcopy(source[k]) for k in ('kind', 'room_id', 'ref', 'start', 'end')}
            original = None
        else:
            require(False, 'invalid_input_source', 422)
        if kind != 'file_excerpt':
            require(original and type(source.get('seq')) is int and original['seq'] <= manifest['snapshot_seq']
                    and source.get('actor_id') == original['actor_id'] and source.get('seq') == original['seq'], 'invalid_input_source', 422)
            total = len(original['text'].encode('utf-8'))
            end = source.get('utf8_end')
            require(type(source.get('utf8_start')) is int and source['utf8_start'] == 0 and type(end) is int and 0 <= end <= total
                    and type(source.get('total_utf8_bytes')) is int and source['total_utf8_bytes'] == total
                    and source.get('truncated') is (end < total), 'invalid_input_source', 422)
            try:
                original['text'].encode('utf-8')[:end].decode('utf-8')
            except UnicodeError:
                require(False, 'invalid_input_source', 422)
            keys = ('kind', 'room_id', 'actor_id', 'seq', 'utf8_start', 'utf8_end', 'total_utf8_bytes', 'truncated')
            normalized = {key: source[key] for key in keys}
            normalized.update({key: source[key] for key in (('message_id',) if kind == 'message' else ('artifact_id', 'version_id', 'revision'))})
        sid = source_id(normalized)
        require(source.get('source_id') == sid and sid not in seen, 'invalid_input_source', 422)
        seen.add(sid)
        verified.append({**normalized, 'source_id': sid})
    target = task.get('review_target')
    if target:
        require(any(s.get('artifact_id') == target['artifact_id'] and s.get('version_id') == target['version_id'] for s in verified), 'review_target_missing_from_input', 422)
    return {'snapshot_seq': manifest['snapshot_seq'], 'sources': verified,
            'evidence': 'worker_reported_server_validated_refs_not_model_read_receipt'}
