"""Experimental fixture-only memory repository; not supported for real data.

No public API, runtime or provider is connected. Never use this experimental
foundation with real data: retention, migration, provider-session revocation,
retired member and operator deletion remain unresolved. Blind forget acknowledgments
mean only that a request was recorded, not that deletion succeeded or bytes were
physically removed from the original, WAL, or backups. Blind-forgotten tombstones
continue consuming active quota; safe cleanup remains unresolved. Timing is not concealed.
Approval keys are SHA-256 hashed for new markers, but low-entropy keys can be
brute-forced and old markers/backups may still contain raw keys. Authorization is
rechecked immediately before commit, but cannot guarantee the exact commit time.
A memory is an exact prefix of one visible human message, not a verified fact.

All operations run under the store's room and identity locks. Revocation audit
and receipt live in a reserved per-memory slot, *not* in room events: room SSE
and the existing event history do not report revocations. The slot is a separate,
bounded experimental audit record; it is not an independent compliance ledger.
The fixture-only revoke index checksum detects accidental damage, not malicious
rewrites, simultaneous deletion or database rollback. Legacy rows are not migrated.
Blind-forget success changes the index while a no-op does not; database-internal
observations and timing are not concealed.
"""
import copy
import hashlib
import math
import re
import time
import uuid

from .domain import Problem, authorize, digest, emit, ident, require
from .retention import DEFAULT_RETENTION

MAX_ACTIVE = 50
MAX_TOTAL = 200  # Includes revoked tombstones.
MAX_OPERATIONS = 512  # Includes expired approval/legacy tombstones.
MAX_FORGET_OPERATIONS = 256  # Separate bounded blind-ack tombstones.
MAX_CHARS = 280
MAX_BYTES = 1120
MAX_LIST = 100


def _approval_key_hash(key):
    return hashlib.sha256(key.encode('utf-8')).hexdigest()


def _revoke_key_hash(key):
    return hashlib.sha256(key.encode('utf-8')).hexdigest()


class MemoryRepository:
    """Operate on a transaction-local copy; stores persist it atomically."""

    def __init__(self, actor, room, records, operations, indexes, reauth):
        self._actor = actor
        self._actor_id = actor['id']
        self._reauth = reauth
        self._authentication_failed = False
        self._room = room
        self._records = records
        self._operations = operations
        self._revocation_index = indexes
        self._created_memory_ids = set()
        self._created_index_ids = set()
        self._created_operation_keys = set()
        self._open = True

    def close(self):
        self._open = False
        self._reauth = None

    def _member(self):
        require(self._open, 'memory_transaction_closed', 409)
        require(not self._authentication_failed, 'unauthorized', 401)
        try:
            actor = self._reauth()
            require(actor.get('id') == self._actor_id, 'unauthorized', 401)
        except Problem as error:
            if error.status == 401:
                self._authentication_failed = True
            raise
        self._actor = actor
        require(self._actor.get('owner') == self._room.get('owner'), 'not_found', 404)
        authorize(self._actor, self._room, 'room:read', human=True)
        return authorize(self._actor, self._room, 'room:control', human=True)

    def _source_visible(self, record, member):
        """Revalidate the original even when a revoked record has no text."""
        source = self._room['messages'].get(record.get('source_message_id'))
        if not source or source.get('actor_kind') != 'human' or source.get('attachment_refs'):
            return False
        if (source.get('seq') != record.get('source_seq') or source['seq'] < member['read_start_seq']
                or source.get('actor_id') != record.get('source_actor_id')):
            return False
        text = source.get('text')
        if not isinstance(text, str):
            return False
        try:
            return hashlib.sha256(text.encode('utf-8')).hexdigest() == record['source_sha256']
        except (UnicodeError, KeyError):
            return False

    @staticmethod
    def _slot_valid(record):
        slot = record.get('_revocation_slot')
        if (not isinstance(slot, dict) or set(slot) != {'schema', 'state', 'audit', 'receipt'}
                or type(slot['schema']) is not int or slot['schema'] != 1):
            return False
        if slot['state'] == 'reserved':
            return record.get('state') == 'approved' and slot['audit'] is None and slot['receipt'] is None
        if slot['state'] != 'used' or record.get('state') != 'revoked' or record.get('text') is not None:
            return False
        audit, receipt = slot['audit'], slot['receipt']
        if not isinstance(audit, dict) or set(audit) != {'action', 'actor_id', 'at'}:
            return False
        fields = {'actor_id', 'member_epoch', 'key_sha256', 'request_digest', 'result_revision', 'response_until'}
        if not isinstance(receipt, dict) or set(receipt) not in (fields, fields | {'operation'}, fields | {'key_check_sha256'}):
            return False
        if 'key_check_sha256' in receipt and (not isinstance(receipt['key_check_sha256'], str)
                or len(receipt['key_check_sha256']) != 64
                or any(c not in '0123456789abcdef' for c in receipt['key_check_sha256'])):
            return False
        operation = receipt.get('operation', 'revoke')
        if (operation, audit['action']) not in {('revoke', 'revoked'), ('forget-own', 'forgotten')}:
            return False
        return (isinstance(audit['actor_id'], str)
                and isinstance(audit['at'], (int, float)) and receipt['actor_id'] == audit['actor_id']
                and type(receipt['member_epoch']) is int and type(receipt['result_revision']) is int
                and isinstance(receipt['response_until'], (int, float))
                and all(isinstance(receipt[k], str) and len(receipt[k]) == 64
                        and all(c in '0123456789abcdef' for c in receipt[k])
                        for k in ('key_sha256', 'request_digest')))

    def _index_value(self, memory_id, receipt=None, *, forgotten=False):
        require(not forgotten or receipt is None, 'memory_revoke_index_unavailable', 503)
        state = 'forgotten' if forgotten else ('used' if receipt is not None else 'reserved')
        value = {'version': 1, 'state': state, 'receipt': copy.deepcopy(receipt)}
        value['checksum'] = digest(['fixture-revoke-index-v1', self._room['id'], memory_id, value])
        return value

    def _check_revoke_index(self):
        # Check index rows only, not other records' slots: a corrupt unrelated slot
        # cannot hide its used key or block a different, valid target.
        require(len(self._revocation_index) <= MAX_TOTAL
                and set(self._revocation_index) == set(self._records),
                'memory_revoke_index_unavailable', 503)
        seen = set()
        for mid, value in self._revocation_index.items():
            require(isinstance(mid, str) and isinstance(value, dict)
                    and set(value) == {'version', 'state', 'receipt', 'checksum'}
                    and type(value['version']) is int and value['version'] == 1
                    and value['state'] in {'reserved', 'used', 'forgotten'}, 'memory_revoke_index_unavailable', 503)
            receipt = value['receipt']
            if value['state'] in {'reserved', 'forgotten'}:
                require(receipt is None, 'memory_revoke_index_unavailable', 503)
            else:
                fields = {'actor_id', 'member_epoch', 'key_sha256', 'key_check_sha256', 'request_digest', 'result_revision', 'response_until'}
                require(isinstance(receipt, dict) and set(receipt) == fields
                        and isinstance(receipt['actor_id'], str) and 1 <= len(receipt['actor_id']) <= 128
                        and type(receipt['member_epoch']) is int and receipt['member_epoch'] > 0
                        and type(receipt['result_revision']) is int and receipt['result_revision'] > 0
                        and type(receipt['response_until']) in (int, float) and math.isfinite(receipt['response_until'])
                        and all(isinstance(receipt[k], str) and len(receipt[k]) == 64
                                and all(c in '0123456789abcdef' for c in receipt[k])
                                for k in ('key_sha256', 'key_check_sha256', 'request_digest')),
                        'memory_revoke_index_unavailable', 503)
                identity = (receipt['actor_id'], receipt['key_sha256'])
                require(identity not in seen, 'memory_revoke_index_unavailable', 503)
                seen.add(identity)
            try:
                expected = self._index_value(mid, receipt, forgotten=value['state'] == 'forgotten')
            except (UnicodeError, TypeError, ValueError):
                require(False, 'memory_revoke_index_unavailable', 503)
            require(value == expected, 'memory_revoke_index_unavailable', 503)

    def _occupies_active(self, memory_id, record):
        # Use the authoritative dictionary key, not potentially damaged payload
        # identifiers/state, and conservatively retain forgotten quota.
        index = self._revocation_index.get(memory_id)
        if isinstance(index, dict) and index.get('state') == 'forgotten':
            return True
        if record.get('state') == 'approved':
            return True
        slot = record.get('_revocation_slot')
        audit = slot.get('audit') if isinstance(slot, dict) else None
        return (record.get('state') == 'revoked' and isinstance(audit, dict)
                and audit.get('action') == 'forgotten')

    def _record_bound(self, memory_id, record):
        return (isinstance(record, dict) and record.get('id') == memory_id
                and record.get('room_id') == self._room.get('id'))

    def _visible(self, record, member):
        index = self._revocation_index.get(record.get('id'))
        if (not isinstance(index, dict) or index.get('state') != 'reserved'
                or record.get('state') != 'approved' or not self._slot_valid(record)
                or not self._source_visible(record, member)):
            return False
        source = self._room['messages'][record['source_message_id']]
        return record.get('text') == source['text'][:MAX_CHARS]

    @staticmethod
    def _public(record):
        return {k: copy.deepcopy(v) for k, v in record.items() if not k.startswith('_')}

    def get(self, memory_id):
        member = self._member()
        self._check_revoke_index()
        record = self._records.get(memory_id)
        require(self._record_bound(memory_id, record) and self._visible(record, member), 'not_found', 404)
        return self._public(record)

    def list(self, *, after_seq=0, limit=50):
        member = self._member()
        self._check_revoke_index()
        require(type(after_seq) is int and after_seq >= 0 and type(limit) is int
                and 1 <= limit <= MAX_LIST, 'invalid_memory_page', 422)
        records = ((mid, record) for mid, record in self._records.items()
                   if self._record_bound(mid, record) and type(record.get('approved_seq')) is int)
        records = sorted(records, key=lambda pair: (pair[1]['approved_seq'], pair[0]))
        return [self._public(record) for mid, record in records
                if record['approved_seq'] > after_seq
                and self._visible(record, member)][:limit]

    def _replay(self, member, operation, key, body):
        require(isinstance(key, str) and 1 <= len(key) <= 128, 'idempotency_key_required', 422)
        try:
            fingerprint = digest([self._actor['id'], self._room['id'], operation, body])
            key_hash = _approval_key_hash(key)
            key_check = digest(['approval-key-check-v1', self._actor['id'], self._room['id'], key])
        except (UnicodeError, TypeError, ValueError):
            require(False, 'invalid_memory_request', 422)
        lookup = (self._actor['id'], 'approve:v1', key_hash)
        legacy_lookup = (self._actor['id'], 'approve', key)
        hashed = self._operations.get(lookup)
        legacy = self._operations.get(legacy_lookup)
        # Project *both stored targets* before revealing ambiguity, hash
        # collision, epoch, digest, or expiry. Never use the new request's target.
        for previous in (legacy, hashed):
            if previous is not None:
                record = self._records.get(previous.get('memory_id'))
                require(self._record_bound(previous.get('memory_id'), record)
                        and record.get('revision') == previous.get('result_revision')
                        and self._visible(record, member), 'not_found', 404)
        require(not (legacy is not None and hashed is not None), 'memory_operation_ambiguous')
        previous = legacy if legacy is not None else hashed
        if previous is not None:
            if hashed is not None:
                require(previous.get('key_check_sha256') == key_check, 'idempotency_conflict')
            require(previous['member_epoch'] == member['epoch'], 'idempotency_epoch_conflict')
            require(previous['request_digest'] == fingerprint, 'idempotency_conflict')
            require(previous['response_until'] > time.time(), 'idempotency_retired', 410)
            return lookup, fingerprint, key_check, {'id': previous['memory_id'], 'revision': previous['result_revision']}
        require(sum(op != 'forget-own' for _, op, _ in self._operations) < MAX_OPERATIONS,
                'memory_operation_capacity', 429)
        return lookup, fingerprint, key_check, None

    def _save_operation(self, lookup, fingerprint, key_check, member, record):
        self._operations[lookup] = {
            'actor_id': lookup[0], 'operation': lookup[1], 'key': lookup[2],
            'key_check_sha256': key_check,
            'member_epoch': member['epoch'], 'request_digest': fingerprint,
            'memory_id': record['id'], 'result_revision': record['revision'],
            'response_until': time.time() + DEFAULT_RETENTION.response_seconds,
        }
        self._created_operation_keys.add(lookup)
        return {'id': record['id'], 'revision': record['revision']}

    def approve(self, *, key, source_message_id, source_seq, snapshot_seq,
                expected_generation, expected_membership_revision, excerpt, confirm):
        member = self._member()
        self._check_revoke_index()
        require(isinstance(source_message_id, str) and 1 <= len(source_message_id) <= 128
                and isinstance(excerpt, str) and len(excerpt) <= MAX_CHARS,
                'invalid_memory_request', 422)
        body = {'source_message_id': source_message_id, 'source_seq': source_seq,
                'snapshot_seq': snapshot_seq, 'expected_generation': expected_generation,
                'expected_membership_revision': expected_membership_revision,
                'excerpt': excerpt, 'confirm': confirm}
        lookup, fingerprint, key_check, previous = self._replay(member, 'approve', key, body)
        if previous is not None:
            record = self._records.get(previous['id'])
            require(self._record_bound(previous['id'], record) and record.get('revision') == previous['revision']
                    and self._visible(record, member), 'not_found', 404)
            return previous
        require(confirm is True, 'memory_confirmation_required', 422)
        require(self._room['state'] in {'preparing', 'active', 'paused'}, 'room_closed')
        require(type(expected_generation) is int and expected_generation == self._room['generation']
                and type(expected_membership_revision) is int
                and expected_membership_revision == member['revision'], 'stale_context')
        require(isinstance(source_message_id, str) and 1 <= len(source_message_id) <= 128
                and type(source_seq) is int and type(snapshot_seq) is int
                and source_seq <= snapshot_seq <= self._room['last_seq'], 'invalid_memory_source', 422)
        source = self._room['messages'].get(source_message_id)
        require(source and source.get('seq') == source_seq and source_seq >= member['read_start_seq']
                and source.get('actor_kind') == 'human' and not source.get('attachment_refs'),
                'memory_source_not_found', 404)
        text = source.get('text')
        from .memory_candidates import normalize_text
        require(isinstance(text, str) and bool(normalize_text(text)) and isinstance(excerpt, str)
                and excerpt == text[:MAX_CHARS], 'invalid_memory_excerpt', 422)
        try:
            raw, source_raw = excerpt.encode('utf-8'), text.encode('utf-8')
        except UnicodeError:
            require(False, 'invalid_memory_excerpt', 422)
        require(len(excerpt) <= MAX_CHARS and len(raw) <= MAX_BYTES, 'invalid_memory_excerpt', 422)
        require(len(self._records) < MAX_TOTAL, 'memory_capacity', 429)
        require(sum(self._occupies_active(mid, record) for mid, record in self._records.items()) < MAX_ACTIVE,
                'memory_active_capacity', 429)
        memory_id = ident()
        require(isinstance(memory_id, str) and memory_id not in self._records
                and memory_id not in self._revocation_index, 'memory_id_collision', 409)
        now = time.time()
        record = {'id': memory_id, 'room_id': self._room['id'], 'source_message_id': source_message_id,
                  'source_seq': source_seq, 'source_actor_id': source['actor_id'],
                  'source_sha256': hashlib.sha256(source_raw).hexdigest(), 'text': text[:MAX_CHARS],
                  'state': 'approved', 'revision': 1, 'approved_by': self._actor['id'],
                  'approved_epoch': member['epoch'], 'approved_at': now,
                  'revoked_by': None, 'revoked_at': None, 'revoked_seq': None,
                  '_revocation_slot': {'schema': 1, 'state': 'reserved', 'audit': None, 'receipt': None}}
        record['approved_seq'] = emit(self._room, 'memory.changed', actor_id=self._actor['id'],
                                      action='approved')['seq']
        self._records[record['id']] = record
        self._revocation_index[record['id']] = self._index_value(record['id'])
        self._created_memory_ids.add(record['id'])
        self._created_index_ids.add(record['id'])
        return self._save_operation(lookup, fingerprint, key_check, member, record)

    def revoke(self, *, key, memory_id, expected_revision, confirm):
        member = self._member()
        require(isinstance(memory_id, str) and 1 <= len(memory_id) <= 128,
                'invalid_memory_request', 422)
        require(isinstance(key, str) and 1 <= len(key) <= 128, 'idempotency_key_required', 422)
        body = {'memory_id': memory_id, 'expected_revision': expected_revision, 'confirm': confirm}
        try:
            fingerprint = digest([self._actor['id'], self._room['id'], 'revoke', body])
            key_hash = _revoke_key_hash(key)
            key_check = digest(['revoke-key-check-v1', self._actor['id'], self._room['id'], key])
        except (UnicodeError, TypeError, ValueError):
            require(False, 'invalid_memory_request', 422)
        self._check_revoke_index()
        match = next(((mid, value['receipt']) for mid, value in self._revocation_index.items()
                      if value['state'] == 'used' and value['receipt']['actor_id'] == self._actor['id']
                      and value['receipt']['key_sha256'] == key_hash), None)
        legacy = self._operations.get((self._actor['id'], 'revoke', key))
        # Both stored targets are projected before any conflict or expiry status.
        if match is not None:
            mid, saved = match
            item = self._records.get(mid)
            require(self._record_bound(mid, item) and item.get('state') == 'revoked' and item.get('text') is None
                    and item.get('revision') == saved['result_revision']
                    and self._source_visible(item, member), 'not_found', 404)
        if legacy is not None:
            record = self._records.get(legacy.get('memory_id'))
            require(self._record_bound(legacy.get('memory_id'), record) and record.get('state') == 'revoked' and record.get('text') is None
                    and record.get('revision') == legacy.get('result_revision')
                    and self._source_visible(record, member), 'not_found', 404)
        require(not (match is not None and legacy is not None), 'memory_operation_ambiguous')
        if match is not None:
            require(self._slot_valid(item) and item['_revocation_slot']['receipt'] == saved,
                    'memory_revoke_slot_unavailable', 409)
            require(saved['key_check_sha256'] == key_check, 'idempotency_conflict')
            require(saved['member_epoch'] == member['epoch'], 'idempotency_epoch_conflict')
            require(saved['request_digest'] == fingerprint, 'idempotency_conflict')
            require(saved['response_until'] > time.time(), 'idempotency_retired', 410)
            require(mid == memory_id, 'not_found', 404)
            return {'id': mid, 'revision': saved['result_revision']}
        if legacy is not None:
            require(legacy['member_epoch'] == member['epoch'], 'idempotency_epoch_conflict')
            require(legacy['request_digest'] == fingerprint, 'idempotency_conflict')
            require(legacy['response_until'] > time.time(), 'idempotency_retired', 410)
            require(False, 'memory_revoke_slot_unavailable', 409)
        require(confirm is True, 'memory_confirmation_required', 422)
        record = self._records.get(memory_id)
        require(self._record_bound(memory_id, record) and self._source_visible(record, member), 'not_found', 404)
        require(self._slot_valid(record), 'memory_revoke_slot_unavailable', 409)
        require(self._visible(record, member), 'not_found', 404)
        require(type(expected_revision) is int and expected_revision == record['revision'], 'memory_conflict')
        now = time.time()
        revision = record['revision'] + 1
        slot = {'schema': 1, 'state': 'used',
                'audit': {'action': 'revoked', 'actor_id': self._actor['id'], 'at': now},
                'receipt': {'actor_id': self._actor['id'], 'member_epoch': member['epoch'],
                            'key_sha256': key_hash, 'key_check_sha256': key_check, 'request_digest': fingerprint,
                            'result_revision': revision,
                            'response_until': now + DEFAULT_RETENTION.response_seconds}}
        # Reserved audit + receipt are the separate experimental record. Revocation
        # intentionally does not emit a room event or increment the room cursor.
        index = self._index_value(record['id'], slot['receipt'])
        record.update(state='revoked', revision=revision, text=None, revoked_by=self._actor['id'],
                      revoked_at=now, revoked_seq=None, _revocation_slot=slot)
        self._revocation_index[record['id']] = index
        return {'id': record['id'], 'revision': revision}

    def forget_own(self, *, key, memory_id, confirm):
        """Blindly record a request; accepted never confirms deletion success.

        Target existence and its source visibility are deliberately not returned.
        Same-key no-op replays never execute against a later-created target.
        """
        member = self._member()
        self._check_revoke_index()
        actor_id, room_id = self._actor.get('id'), self._room.get('id')
        require(isinstance(actor_id, str) and 1 <= len(actor_id) <= 128 and actor_id.isprintable()
                and isinstance(room_id, str) and re.fullmatch(r'[A-Za-z0-9-]{1,128}', room_id),
                'invalid_forget_request', 422)
        require(isinstance(key, str) and 1 <= len(key) <= 128 and key.isprintable()
                and isinstance(memory_id, str) and len(memory_id) == 36 and confirm is True,
                'invalid_forget_request', 422)
        try:
            key_bytes = key.encode('utf-8')
            require(len(key_bytes) <= 128 and str(uuid.UUID(memory_id)) == memory_id,
                    'invalid_forget_request', 422)
            key_hash = hashlib.sha256(key_bytes).hexdigest()
            fingerprint = digest([actor_id, room_id, 'forget-own',
                                  {'memory_id': memory_id, 'confirm': True}])
        except (UnicodeError, ValueError, TypeError):
            require(False, 'invalid_forget_request', 422)
        lookup = (actor_id, 'forget-own', key_hash)
        previous = self._operations.get(lookup)
        if previous:
            require(previous['request_digest'] == fingerprint, 'idempotency_conflict')
            require(previous['response_until'] > time.time(), 'idempotency_retired', 410)
            return {'accepted': True}
        # Check the shared quota before even looking up the target. Valid, missing,
        # other-owner, and legacy-slot requests have identical admission behavior.
        require(sum(op == 'forget-own' for _, op, _ in self._operations) < MAX_FORGET_OPERATIONS,
                'memory_forget_capacity', 429)
        now = time.time()
        record = self._records.get(memory_id)
        if (self._record_bound(memory_id, record) and record.get('approved_by') == actor_id
                and record.get('state') == 'approved' and self._slot_valid(record)
                and self._revocation_index[memory_id]['state'] == 'reserved'
                and type(record.get('revision')) is int and record['revision'] >= 1):
            revision = record['revision'] + 1
            slot = {'schema': 1, 'state': 'used',
                    'audit': {'action': 'forgotten', 'actor_id': actor_id, 'at': now},
                    'receipt': {'operation': 'forget-own', 'actor_id': actor_id,
                                'member_epoch': member['epoch'], 'key_sha256': key_hash,
                                'request_digest': fingerprint, 'result_revision': revision,
                                'response_until': now + DEFAULT_RETENTION.response_seconds}}
            index = self._index_value(memory_id, forgotten=True)
            record.update(state='revoked', text=None, revision=revision, revoked_by=actor_id,
                          revoked_at=now, revoked_seq=None, _revocation_slot=slot)
            self._revocation_index[memory_id] = index
        # Identical bounded tombstones for actual changes and every no-op case.
        self._operations[lookup] = {'actor_id': actor_id, 'operation': 'forget-own', 'key': key_hash,
                                    'request_digest': fingerprint,
                                    'response_until': now + DEFAULT_RETENTION.response_seconds}
        self._created_operation_keys.add(lookup)
        return {'accepted': True}

    def validate(self):
        """Catch accidental excessive mutations before either store commits."""
        self._check_revoke_index()
        regular = sum(op != 'forget-own' for _, op, _ in self._operations)
        forgotten = sum(op == 'forget-own' for _, op, _ in self._operations)
        require(len(self._records) <= MAX_TOTAL and regular <= MAX_OPERATIONS
                and forgotten <= MAX_FORGET_OPERATIONS, 'memory_capacity', 429)
        require(sum(self._occupies_active(mid, record) for mid, record in self._records.items()) <= MAX_ACTIVE,
                'memory_active_capacity', 429)
        require(all(r['state'] in {'approved', 'revoked'} and
                    ((r['state'] == 'revoked' and r['text'] is None) or
                     (r['state'] == 'approved' and isinstance(r['text'], str)
                      and len(r['text']) <= MAX_CHARS and len(r['text'].encode('utf-8')) <= MAX_BYTES))
                    for r in self._records.values()), 'invalid_memory_record', 422)
