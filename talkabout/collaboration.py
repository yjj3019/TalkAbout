"""Bounded shared text and verifiable references, never proof of model reading.

The caller supplies an ACL-filtered public room. Nothing here calls a provider, reads
a file, generates a summary or grants a permission. What this module adds is the
difference between "the worker says it sent something" and "the server can check that
each reference exists, belongs to this room and viewer, matches the byte range, and
hashes to the stored bytes".
"""
import copy
import hashlib

from .agent_runtime import MAX_INPUT, prompt_text, work_prompt_text

MAX_SHARED_TEXT_BYTES = 72000
MAX_MESSAGES = 8
MAX_VERSIONS = 4
MAX_FILE_SOURCES = 4
# The worker-side negotiated ceiling for exact predecessor message inputs. The
# server stores up to 15 and accepts any per-claim capability in 1..15, but the
# current worker prepares at most this many before failing closed.
REQUIRED_PREDECESSOR_LIMIT = 8
# Thread context is enrichment, not a licence to revive old conversations: only the
# newest messages may seed an ancestor walk, and only a few hops of it.
MAX_RECENT_MESSAGES = 4
MAX_THREAD_ANCESTORS = 3
MAX_THREAD_HOPS = 3


class ContextBudgetError(ValueError):
    """The serialized context cannot fit without discarding required metadata."""

    def __init__(self, code='invalid_runtime_budget'):
        super().__init__(code)
        self.code = code


def excerpt(text, budget):
    """Cut text at a UTF-8 boundary and describe exactly what was kept."""
    try:
        raw = text.encode('utf-8')
        selected = raw[:max(0, budget)].decode('utf-8', errors='ignore')
    except (AttributeError, UnicodeError):
        raise ContextBudgetError('invalid_runtime_context') from None
    used = len(selected.encode('utf-8'))
    return selected, {'utf8_start': 0, 'utf8_end': used,
                      'total_utf8_bytes': len(raw), 'truncated': used < len(raw)}


def _sha256(text):
    """Bind a manifest source to the exact UTF-8 bytes it says were included."""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def input_fingerprint(sources):
    """Deterministic digest over the *claimed* source set and its byte bindings.

    Every claimed source_id is folded into the digest (in source_id sort order)
    together with its sha256; a claimed source without a byte binding contributes
    its id with an empty digest, so removing it (or leaving it unbound) changes
    the fingerprint. ``None`` means *no source was claimed at all*, so legacy
    unverified input and a claimed-but-unbound source can never compare equal.
    """
    rows = sorted((str(source.get('source_id')), str(source.get('sha256') or ''))
                 for source in sources if source.get('source_id'))
    if not rows:
        return None
    return hashlib.sha256('\n'.join('%s\t%s' % pair for pair in rows).encode('utf-8')).hexdigest()


def source_id(source):
    prefix = 'talkabout:room:' + source['room_id']
    if source['kind'] == 'message':
        return prefix + ':message:' + source['message_id']
    if source['kind'] == 'artifact_version':
        return prefix + ':artifact:' + source['artifact_id'] + ':version:' + source['version_id']
    return prefix + ':file:' + source['ref']['file_id'] + ':version:' + source['ref']['version_id']


def _revision(version, index):
    return version.get('version_revision', index + 1)


def _omissions(all_items, selected, key):
    missing = [item for item in all_items if key(item) not in selected]
    seqs = [item['seq'] for item in missing if 'seq' in item]
    return {'count': len(missing), 'seq_range': [min(seqs), max(seqs)] if seqs else None}


def project_manifest(manifest, messages, artifacts, file_refs=()):
    """Redact sources the current viewer may no longer see."""
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
    """Redact task metadata that points outside the viewer's history boundary."""
    result = copy.deepcopy(task)
    visible = {m['id'] for m in messages}
    if result.get('result_message_id') not in visible:
        result.pop('result_message_id', None)
    if 'handoff' in result and result.get('handoff', {}).get('message_id') not in visible:
        result['handoff'] = {'result_available': False}
    target = result.get('review_target')
    if target and not any(a['id'] == target['artifact_id'] and any(v['id'] == target['version_id'] for v in a['versions'])
                          for a in artifacts):
        result.pop('review_target', None)
        result['review_target_available'] = False
    if result.get('input_manifest'):
        result['input_manifest'] = project_manifest(result['input_manifest'], messages, artifacts,
                                                   result.get('attachment_refs', []))
    return result


def shared_context(room, task):
    """Build the bounded inputs a worker may hand to a runtime, with a manifest.

    Priority: visible exact direct predecessor messages, then pinned/handoff,
    then the newest messages (four when available), then visible ancestors, then newest
    remaining messages. Versions: adopted, then recent.
    Ancestors are only followed from the newest messages: an older thread is never
    revived merely because it exists or has more replies. Text is cut at UTF-8
    boundaries and every cut is described in the manifest.
    """
    messages = sorted(room['messages'], key=lambda item: item['seq'])
    versions = [(artifact, _revision(version, index), version)
                for artifact in room['artifacts']
                for index, version in enumerate(artifact['versions'])]
    # Claim/GET snapshots need not carry plan_node. Use only the matching fresh
    # public-room task, and only when its identity and fencing fields agree.
    current = next((item for item in room.get('tasks', []) if item.get('id') == task.get('id')), None)
    fence = ('assignee', 'attempt', 'generation', 'membership_revision', 'state')
    matching = (current is not None and all(key in task and current.get(key) == task[key] for key in fence)
                and current.get('generation') == room.get('generation')
                and current.get('membership_revision') == room.get('membership_revision')
                and current.get('state') == 'leased')
    plan = current.get('plan_node') if matching else None
    task = project_task(task, messages, room['artifacts'])
    by_id = {item['id']: item for item in messages}
    chosen_messages = []
    predecessor_ids = plan.get('predecessor_message_ids', []) if isinstance(plan, dict) else []
    if not isinstance(predecessor_ids, list):
        predecessor_ids = []
    required_opt_in = isinstance(plan, dict) and 'required_predecessor_message_ids' in plan
    required_ids = plan.get('required_predecessor_message_ids') if required_opt_in else []
    valid_required = (isinstance(required_ids, list)
                      and all(isinstance(mid, str) and mid for mid in required_ids)
                      and len(set(required_ids)) == len(required_ids)
                      and len(required_ids) <= MAX_MESSAGES)
    required_unavailable = required_opt_in and not valid_required
    if required_opt_in and valid_required:
        required_unavailable = any(sum(m['id'] == mid for m in messages) != 1 for mid in required_ids)
        predecessor_ids = required_ids + predecessor_ids
    else:
        required_ids = []
    for mid in predecessor_ids:
        match = by_id.get(mid) if isinstance(mid, str) else None
        if match and match not in chosen_messages and len(chosen_messages) < MAX_MESSAGES:
            chosen_messages.append(match)
    for mid in (room.get('pinned_message_id'), (task.get('handoff') or {}).get('message_id')):
        match = by_id.get(mid) if isinstance(mid, str) else None
        if match and match not in chosen_messages and len(chosen_messages) < MAX_MESSAGES:
            chosen_messages.append(match)
    newest = messages[-MAX_RECENT_MESSAGES:]
    for item in newest:
        if item not in chosen_messages and len(chosen_messages) < MAX_MESSAGES:
            chosen_messages.append(item)
    ancestors_left = min(MAX_THREAD_ANCESTORS, MAX_MESSAGES - len(chosen_messages))
    for seed in reversed(newest):
        if ancestors_left <= 0:
            break
        child, hops, path = seed, 0, {seed['id']}
        parent_id = child.get('reply_to')
        while parent_id and hops < MAX_THREAD_HOPS and ancestors_left > 0:
            parent = by_id.get(parent_id)
            if parent is None or parent['id'] in path or parent['seq'] >= child['seq']:
                break
            path.add(parent['id'])
            if parent not in chosen_messages:
                chosen_messages.append(parent)
                ancestors_left -= 1
            child, hops, parent_id = parent, hops + 1, parent.get('reply_to')
    for item in reversed(messages):
        if item not in chosen_messages and len(chosen_messages) < MAX_MESSAGES:
            chosen_messages.append(item)
    target = task.get('review_target')
    chosen_versions = []
    if target:
        chosen_versions.extend((artifact, revision, version) for artifact, revision, version in versions
                               if artifact['id'] == target['artifact_id'] and version['id'] == target['version_id'])
    for artifact, revision, version in reversed(versions):
        if artifact.get('adopted') == version['id'] and (artifact, revision, version) not in chosen_versions:
            chosen_versions.append((artifact, revision, version))
    for artifact in sorted(room['artifacts'], key=lambda a: max(v['seq'] for v in a['versions']), reverse=True):
        if not artifact['versions']:
            continue
        pair = (artifact, _revision(artifact['versions'][-1], len(artifact['versions']) - 1), artifact['versions'][-1])
        if pair not in chosen_versions:
            chosen_versions.append(pair)
    chosen_versions = chosen_versions[:MAX_VERSIONS]
    budget = MAX_SHARED_TEXT_BYTES
    required_set = set(required_ids)
    required_sizes = {}
    if required_opt_in and not required_unavailable:
        try:
            required_sizes = {mid: len(by_id[mid]['text'].encode('utf-8')) for mid in required_ids}
            required_unavailable = sum(required_sizes.values()) > budget
        except (KeyError, AttributeError, UnicodeError):
            required_unavailable = True
    if not required_unavailable:
        budget -= sum(required_sizes.values())
    inputs, proposals, references = [], [], []
    for artifact, revision, version in chosen_versions:
        text, limits = excerpt(version['text'], min(12000, budget))
        budget -= limits['utf8_end']
        reference = {'kind': 'artifact_version', 'room_id': room['id'], 'artifact_id': artifact['id'],
                     'version_id': version['id'], 'revision': revision,
                     'actor_id': version['actor_id'], 'seq': version['seq'], **limits,
                     'sha256': _sha256(text)}
        reference['source_id'] = source_id(reference)
        references.append(reference)
        proposals.append({**reference, 'text': text, 'head_snapshot_revision': artifact['revision'],
                          'adopted': artifact.get('adopted') == version['id'],
                          'kind': version.get('kind', 'text'),
                          'sources': [excerpt(value, 256)[0] for value in version.get('sources', [])[:4]],
                          'omitted_sources': max(0, len(version.get('sources', [])) - 4),
                          'untrusted_data': True})
    for index, item in enumerate(chosen_messages):
        if item['id'] in required_sizes and not required_unavailable:
            text, limits = excerpt(item['text'], required_sizes[item['id']])
        else:
            remaining = sum(m['id'] not in required_set for m in chosen_messages[index:])
            # Keep the original divisor exactly when no required inputs were reserved.
            divisor = remaining if required_opt_in and not required_unavailable else len(chosen_messages) - index
            text, limits = excerpt(item['text'], min(6000, budget // max(1, divisor)))
            budget -= limits['utf8_end']
        reference = {'kind': 'message', 'room_id': room['id'], 'message_id': item['id'],
                     'actor_id': item['actor_id'], 'seq': item['seq'], **limits,
                     'sha256': _sha256(text)}
        reference['source_id'] = source_id(reference)
        references.append(reference)
        inputs.append({key: item[key] for key in ('id', 'actor_id', 'actor_kind', 'display_name', 'seq', 'kind')}
                      | {'text': text, **limits, 'source_id': reference['source_id'], 'untrusted_data': True})
    inputs.sort(key=lambda item: item['seq'])
    selected_messages = {item['id'] for item in chosen_messages}
    selected_versions = {version['id'] for _, _, version in chosen_versions}
    manifest = {'snapshot_seq': room['last_seq'],
                'event_range': [min((r['seq'] for r in references), default=room['last_seq']), room['last_seq']],
                'sources': references, 'summary': {'status': 'not_generated', 'covered_event_range': None},
                'omissions': {'messages': _omissions(messages, selected_messages, lambda m: m['id']),
                              'artifact_versions': _omissions([v for _, _, v in versions], selected_versions,
                                                              lambda v: v['id'])},
                'evidence': 'prepared_worker_input_not_model_read_receipt'}
    pinned = room.get('pinned_message_id')
    result = {'inputs': inputs, 'artifact_proposals': proposals, 'input_manifest': manifest,
              'pinned_message_id': pinned if pinned in selected_messages else None}
    if required_opt_in:
        result['_required_context'] = {'message_ids': required_ids, 'unavailable': required_unavailable}
    return result


def required_predecessors_available(required_ids, context, room):
    """Verify the final prompt carries each required message and its complete source."""
    if (not isinstance(required_ids, list)
            or any(not isinstance(mid, str) or not mid for mid in required_ids)
            or len(set(required_ids)) != len(required_ids)):
        return False
    inputs = context.get('inputs')
    manifest = context.get('input_manifest')
    sources = manifest.get('sources') if isinstance(manifest, dict) else None
    messages = room.get('messages')
    if not all(isinstance(value, list) for value in (inputs, sources, messages)):
        return False
    for mid in required_ids:
        originals = [m for m in messages if isinstance(m, dict) and m.get('id') == mid]
        entries = [m for m in inputs if isinstance(m, dict) and m.get('id') == mid]
        refs = [s for s in sources if isinstance(s, dict) and s.get('kind') == 'message'
                and s.get('message_id') == mid]
        if len(originals) != 1 or len(entries) != 1 or len(refs) != 1:
            return False
        original, entry, ref = originals[0], entries[0], refs[0]
        text = original.get('text')
        if not isinstance(text, str) or entry.get('text') != text:
            return False
        try:
            size = len(text.encode('utf-8'))
        except UnicodeError:
            return False
        limits = {'utf8_start': 0, 'utf8_end': size, 'total_utf8_bytes': size, 'truncated': False}
        if any(type(obj.get(k)) is not type(v) or obj.get(k) != v
               for obj in (entry, ref) for k, v in limits.items()):
            return False
        if (ref.get('room_id') != room.get('id') or ref.get('actor_id') != original.get('actor_id')
                or ref.get('seq') != original.get('seq') or not isinstance(entry.get('source_id'), str)
                or entry['source_id'] != ref.get('source_id')):
            return False
        try:
            if ref['source_id'] != source_id(ref):
                return False
        except (KeyError, TypeError):
            return False
    return True


def add_file_sources(context, excerpts, *, available_count=None):
    """Record the attachment excerpts a worker fetched, including what it skipped."""
    context['input_manifest']['omissions']['files'] = {
        'count': max(0, (len(excerpts) if available_count is None else available_count) - len(excerpts))}
    for item in excerpts:
        source = {'kind': 'file_excerpt', 'room_id': context['room_id'], 'ref': copy.deepcopy(item['ref']),
                  'start': item['start'], 'end': item['end'], 'truncated': item['truncated']}
        source['source_id'] = source_id(source)
        context['input_manifest']['sources'].append(source)
    return context


def _serialized_size(context, *, work_workspace=None):
    try:
        if work_workspace is None:
            prompt = prompt_text(context)
        else:
            prompt = work_prompt_text(work_workspace) + prompt_text(context, work_profile=True)
        return len(prompt.encode('utf-8'))
    except (TypeError, ValueError, RecursionError, UnicodeError):
        raise ContextBudgetError('invalid_runtime_context') from None


def fit_context(context, *, work_workspace=None):
    """Shrink text until the actual serialized prompt fits, never dropping metadata.

    The largest ordinary entry is halved first; the pinned message and the exact review
    target keep a 64-byte floor longer than everything else. For an explicitly selected
    work workspace, count the same work-mode header and workspace prefix as the runtime.
    """
    manifest = context['input_manifest']
    required = context.pop('_required_context', None)
    if required is not None and required['unavailable']:
        context['_required_context'] = required
        raise ContextBudgetError('required_input_unavailable')
    required_ids = set(required['message_ids']) if required is not None else set()
    manifest['budget'] = {'max_serialized_utf8_bytes': MAX_INPUT}
    target = context.get('review_target') or {}
    while True:
        if _serialized_size(context, work_workspace=work_workspace) <= MAX_INPUT:
            return context
        candidates = []
        for group in ('inputs', 'artifact_proposals', 'file_excerpts'):
            for item in context.get(group, []):
                if group == 'inputs' and item.get('id') in required_ids:
                    continue
                length = len(item['text'].encode('utf-8'))
                protected = ((group == 'inputs' and item.get('id') == context.get('pinned_message_id')) or
                             (group == 'artifact_proposals' and item.get('version_id') == target.get('version_id')))
                floor = 64 if protected else 0
                if length > floor:
                    candidates.append((not protected, length, group, item, floor))
        if not candidates:
            if required is not None:
                context['_required_context'] = required
            raise ContextBudgetError('required_input_unavailable' if required is not None and required_ids
                                     else 'invalid_runtime_budget')
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
            ref.update(utf8_end=item['utf8_end'], truncated=item['truncated'],
                       sha256=_sha256(item['text']))


def validate_manifest(manifest, room, task):
    """Validate worker-reported immutable references against current authorized data.

    This proves reference existence, scope, byte range and SHA-256 byte identity for
    messages and artifact versions. It does not prove delivery, comprehension or
    anything about the provider. Legacy clients may omit the manifest.
    """
    from .domain import require
    if manifest is None:
        return {'evidence': 'legacy_input_unverified', 'sources': []}
    require(isinstance(manifest, dict) and type(manifest.get('snapshot_seq')) is int
            and 0 <= manifest['snapshot_seq'] <= room['last_seq'], 'invalid_input_manifest', 422)
    sources = manifest.get('sources')
    require(isinstance(sources, list) and len(sources) <= MAX_MESSAGES + MAX_VERSIONS + MAX_FILE_SOURCES,
            'invalid_input_manifest', 422)
    known_messages = {m['id']: m for m in room['messages']}
    known_artifacts = {a['id']: a for a in room['artifacts']}
    verified, seen = [], set()
    for source in sources:
        require(isinstance(source, dict) and source.get('room_id') == room['id'], 'invalid_input_source', 422)
        kind = source.get('kind')
        identifiers = {'message': ('message_id',), 'artifact_version': ('artifact_id', 'version_id'),
                       'file_excerpt': ()}
        require(isinstance(kind, str) and kind in identifiers, 'invalid_input_source', 422)
        require(all(isinstance(source.get(key), str) and 1 <= len(source[key]) <= 128 for key in identifiers[kind]),
                'invalid_input_source', 422)
        if kind == 'message':
            original = known_messages.get(source.get('message_id'))
            require(original and type(source.get('seq')) is int and original['seq'] <= manifest['snapshot_seq']
                    and source.get('actor_id') == original['actor_id'] and source.get('seq') == original['seq'],
                    'invalid_input_source', 422)
        elif kind == 'artifact_version':
            artifact = known_artifacts.get(source.get('artifact_id'))
            original = next((v for v in artifact['versions'] if v['id'] == source.get('version_id')), None) if artifact else None
            revision = next((_revision(v, index) for index, v in enumerate(artifact['versions'])
                             if v['id'] == source.get('version_id')), None) if artifact else None
            require(original and type(source.get('revision')) is int and source['revision'] == revision,
                    'invalid_input_source', 422)
            require(type(source.get('seq')) is int and original['seq'] <= manifest['snapshot_seq']
                    and source.get('actor_id') == original['actor_id'] and source.get('seq') == original['seq'],
                    'invalid_input_source', 422)
        else:
            ref = source.get('ref')
            scoped_task = next((t for t in room['tasks'] if t['id'] == task['id']), {})
            require(isinstance(ref, dict) and ref in scoped_task.get('attachment_refs', []),
                    'invalid_input_source', 422)
            read = task.get('_file_reads', {}).get(f"file-read:{task['id']}:{task['attempt']}:{ref['file_id']}")
            require(type(source.get('start')) is int and type(source.get('end')) is int and read
                    and read['range'][0] == source['start'] <= source['end'] <= read['range'][1],
                    'invalid_input_source', 422)
            normalized = {key: copy.deepcopy(source[key]) for key in ('kind', 'room_id', 'ref', 'start', 'end')}
            original = None
        if kind != 'file_excerpt':
            total = len(original['text'].encode('utf-8'))
            end = source.get('utf8_end')
            require(type(source.get('utf8_start')) is int and source['utf8_start'] == 0 and type(end) is int
                    and 0 <= end <= total
                    and type(source.get('total_utf8_bytes')) is int and source['total_utf8_bytes'] == total
                    and source.get('truncated') is (end < total), 'invalid_input_source', 422)
            try:
                raw = original['text'].encode('utf-8')
                raw[:end].decode('utf-8')
            except UnicodeError:
                require(False, 'invalid_input_source', 422)
            # Byte binding: the reported range must hash to the stored bytes. This is
            # not delivery evidence; it only rejects a range/content mismatch.
            require(isinstance(source.get('sha256'), str)
                    and source['sha256'] == hashlib.sha256(raw[:end]).hexdigest(),
                    'invalid_input_source', 422)
            keys = ('kind', 'room_id', 'actor_id', 'seq', 'utf8_start', 'utf8_end',
                    'total_utf8_bytes', 'truncated', 'sha256')
            normalized = {key: source[key] for key in keys}
            normalized.update({key: source[key] for key in
                               (('message_id',) if kind == 'message' else ('artifact_id', 'version_id', 'revision'))})
        sid = source_id(normalized)
        require(source.get('source_id') == sid and sid not in seen, 'invalid_input_source', 422)
        seen.add(sid)
        verified.append({**normalized, 'source_id': sid})
    target = task.get('review_target')
    if target:
        require(any(s.get('artifact_id') == target['artifact_id'] and s.get('version_id') == target['version_id']
                    for s in verified), 'review_target_missing_from_input', 422)
    return {'snapshot_seq': manifest['snapshot_seq'], 'sources': verified,
            'evidence': 'worker_reported_server_validated_refs_not_model_read_receipt'}
