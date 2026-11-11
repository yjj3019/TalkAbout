"""Human moderation panel catalog: closed actions + read-only bindings.

TalkAbout-owned contract. The browser panel is NOTRUN; this pins the contract the
panel must bind to: unknown/forged actions fail closed and the only polls allowed
are the read-only projections (docs/CLEAN_ROOM_IMPLEMENTATION.md).
"""
import pytest

from conftest import LocalClient
from talkabout.classroom_panel import (
    HUMAN_ONLY_ACTIONS, PANEL_ACTIONS, PANEL_READS, PanelError, panel_action,
    panel_is_human_only, panel_read,
)


def test_panel_catalog_is_closed_and_stable():
    assert len(PANEL_ACTIONS) == 12
    assert 'change_deliverable' in PANEL_ACTIONS and 'adopt_artifact' in PANEL_ACTIONS
    assert len(PANEL_READS) == 5
    assert PANEL_READS <= {'search', 'production', 'diagnostics', 'messages', 'artifacts'}


def test_unknown_actions_and_reads_fail_closed():
    with pytest.raises(PanelError):
        panel_action('rename_owner')
        panel_action('delete_room')
        panel_action(None)
    with pytest.raises(PanelError):
        panel_read('tasks/claim')
        panel_read('memberships/purge')
    assert panel_action('pause') == 'pause'
    assert panel_read('production') == 'production'


def test_human_only_actions_are_never_agent_issuable():
    assert panel_is_human_only('approve_control')
    assert panel_is_human_only('adopt_artifact')
    assert panel_is_human_only('close')
    assert not panel_is_human_only('search')
    assert HUMAN_ONLY_ACTIONS <= PANEL_ACTIONS


def test_panel_catalog_binds_to_real_api_surfaces(fixture):
    store, core, rid = fixture
    client = LocalClient(store, 'fixture-human')
    client.request('GET', f'rooms/{rid}/production')
    client.request('GET', f'rooms/{rid}/search', params={'q': 'x'})
    assert {'production', 'search'} <= PANEL_READS
    assert {'room_production', 'room_search'} <= set(dir(core))
