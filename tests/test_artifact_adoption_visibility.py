"""Artifact adoption identifiers follow the viewer's version history floor."""
import json

from talkabout.response_projection import project_task_response


def _create_versions(core, rid):
    created = core.mutation('fixture-human', rid, 'artifacts', 'create-artifact', {
        'generation': 1, 'membership_revision': 1, 'text': 'private version'})
    old = created['versions'][0]
    adopted = core.mutation('fixture-human', rid, f"artifacts/{created['id']}/adoptions", 'adopt-old', {
        'generation': 1, 'membership_revision': 1, 'expected_revision': 1,
        'version_id': old['id']})
    updated = core.mutation('fixture-human', rid, f"artifacts/{created['id']}/versions", 'append-version', {
        'generation': 1, 'membership_revision': 1, 'expected_revision': adopted['revision'],
        'text': 'visible version'})
    return updated, old, updated['versions'][-1]


def _room_views(core, rid):
    return (core.read('fixture-human', rid),
            core.room_snapshot('fixture-human', rid)['room'],
            next(room for room in core.rooms('fixture-human') if room['id'] == rid))


def test_hidden_adoption_is_redacted_from_room_projections(fixture):
    store, core, rid = fixture
    artifact, old, visible = _create_versions(core, rid)
    with store.transaction(rid, 'fixture-human') as (_, room):
        room['members']['human']['read_start_seq'] = visible['seq']

    for room in _room_views(core, rid):
        projected = next(a for a in room['artifacts'] if a['id'] == artifact['id'])
        assert [v['id'] for v in projected['versions']] == [visible['id']]
        assert projected['adopted'] is None
        assert old['id'] not in json.dumps(room)

    with store.read_transaction(rid, 'fixture-human') as (_, room):
        assert room['artifacts'][artifact['id']]['adopted'] == old['id']


def test_visible_adoption_is_preserved_at_history_floor(fixture):
    store, core, rid = fixture
    artifact, old, visible = _create_versions(core, rid)
    core.mutation('fixture-human', rid, f"artifacts/{artifact['id']}/adoptions", 'adopt-visible', {
        'generation': 1, 'membership_revision': 1, 'expected_revision': artifact['revision'],
        'version_id': visible['id']})
    with store.transaction(rid, 'fixture-human') as (_, room):
        room['members']['human']['read_start_seq'] = visible['seq']

    for room in _room_views(core, rid):
        projected = next(a for a in room['artifacts'] if a['id'] == artifact['id'])
        assert [v['id'] for v in projected['versions']] == [visible['id']]
        assert projected['adopted'] == visible['id']
        assert old['id'] not in json.dumps(room)


def test_task_response_does_not_disclose_hidden_adoption(fixture):
    store, core, rid = fixture
    artifact, old, visible = _create_versions(core, rid)
    with store.transaction(rid, 'fixture-human') as (actor, room):
        room['members']['human']['read_start_seq'] = visible['seq']
        task = next(iter(room['tasks'].values()))
        response = project_task_response(room, actor, {'task': task, 'artifact': artifact})
        assert response['artifact']['adopted'] is None
        assert old['id'] not in json.dumps(response['artifact'])
