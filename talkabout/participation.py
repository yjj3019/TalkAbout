"""Explicit client sessions and receipts, never native-runtime identity or execution proof."""
import copy
import re
import time

from .domain import authorize, require, token_hash


def context(body, room, member):
    require(type(body.get('generation')) is int and body['generation'] == room['generation']
            and type(body.get('membership_revision')) is int
            and body['membership_revision'] == member['revision'], 'stale_context')
    require(isinstance(body.get('session_id'), str)
            and re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', body['session_id']),
            'invalid_session_id', 422)


def bound_session(token, body, room, member):
    context(body, room, member)
    session = member.get('_participation')
    require(session and session['session_id'] == body['session_id']
            and session['_credential_binding'] == token_hash(token)
            and session['epoch'] == member['epoch']
            and session['generation'] == room['generation']
            and session['membership_revision'] == member['revision'], 'stale_session')
    return session


def public_session(session):
    return {k: copy.deepcopy(v) for k, v in session.items() if not k.startswith('_')}


def bound_task_session(token, body, room, member, task):
    """A session-aware claim cannot later downgrade to the legacy task contract."""
    claimed_session=(task or {}).get('participation_session_id')
    if claimed_session is not None:
        require('session_id' in body,'session_required')
    if 'session_id' in body:
        bound_session(token,body,room,member)
    if claimed_session is not None:
        require(body['session_id']==claimed_session,'stale_task_session')


class Participation:
    def participation(self, token, room_id, action, body):
        require(isinstance(body, dict), 'invalid_fields', 422)
        fields = {'session_id', 'generation', 'membership_revision'}
        allowed = fields | ({'cursor', 'state'} if action == 'ack' else {'previous_session_id'})
        required = fields | ({'cursor', 'state'} if action == 'ack' else set())
        require(action in {'join', 'ack'} and required <= set(body) <= allowed,
                'invalid_fields', 422)
        # The room transaction authenticates again and serializes membership/control changes.
        with self.store.transaction(room_id, token) as (actor, room):
            member = authorize(actor, room, 'room:read')
            context(body, room, member)
            if action == 'join':
                previous = member.get('_participation')
                if previous and previous['session_id'] == body['session_id']:
                    session = bound_session(token, body, room, member)
                else:
                    require(not previous or body.get('previous_session_id') == previous['session_id'], 'session_conflict')
                    retired = member.setdefault('_retired_participation_ids', [])
                    require(body['session_id'] not in retired, 'retired_session')
                    if previous:
                        require(len(retired) < 64, 'session_rotation_capacity', 429)
                        retired.append(previous['session_id'])
                    session = {'session_id': body['session_id'], 'actor_id': actor['id'],
                               '_credential_binding': token_hash(token), 'epoch': member['epoch'],
                               'generation': room['generation'], 'membership_revision': member['revision'],
                               'delivered_seq': 0, 'read_seq': 0}
                    member['_participation'] = session
            else:
                session = bound_session(token, body, room, member)
                require(isinstance(body['state'], str) and body['state'] in {'delivered', 'read'}, 'invalid_receipt', 422)
                require(isinstance(body['cursor'], str) and 1 <= len(body['cursor']) <= 128, 'invalid_cursor', 422)
                cursor = self.store.cursor(body['cursor'])
                require(cursor is not None, 'cursor_gap', 410)
                require(cursor['principal'] == actor['id'] and cursor.get('view') == room_id
                        and cursor.get('participation_session') == session['session_id']
                        and cursor.get('_credential_binding') == token_hash(token)
                        and cursor['scope'] == self.scope(actor, {room_id: room})[1]
                        and cursor.get('epochs', {}).get(room_id) == member['epoch'], 'invalid_cursor', 403)
                seq = cursor['offsets'].get(room_id)
                require(type(seq) is int and member['read_start_seq'] - 1 <= seq <= room['last_seq'], 'invalid_cursor', 422)
                session['delivered_seq'] = max(session['delivered_seq'], seq)
                if body['state'] == 'read':
                    session['read_seq'] = max(session['read_seq'], seq)
            receipt = public_session(session)
        if action == 'join':
            # Snapshot has its own final authorization check. A failed response is safe to repeat.
            return {**self.room_snapshot(token, room_id, session_id=body['session_id']), 'participation': receipt}
        return receipt

    def notifications(self, token, room_id, session_id):
        with self.store.read_transaction(room_id, token) as (actor, room):
            member = authorize(actor, room, 'room:read')
            session = bound_session(token, {'session_id': session_id, 'generation': room['generation'],
                                           'membership_revision': member['revision']}, room, member)
            unread = [m for m in room['messages'].values() if m['seq'] >= member['read_start_seq']
                      and m['seq'] > session['read_seq'] and m['actor_id'] != actor['id']]
            busy = any(t['assignee'] == actor['id'] and t['state'] == 'leased'
                       and t['generation'] == room['generation'] and t['lease_expires_at'] > time.time()
                       for t in room['tasks'].values())
            uncertain = any(t['state'] == 'uncertain' for t in room['tasks'].values())
            return {'count': len(unread), 'oldest_at': min((m['created_at'] for m in unread), default=None),
                    'state': 'held' if (busy or uncertain) and unread else 'available' if unread else 'empty',
                    'read_seq': session['read_seq']}
