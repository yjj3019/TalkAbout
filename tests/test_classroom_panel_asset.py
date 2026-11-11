"""조율 패널 웹 자산: 서빙·캐탈로그 결속(브라우저 자동화는 NOTRUN).

The panel module is served by the web shell and must only reference catalog actions
and read-only projections; unknown actions are rejected client-side (fail closed).
Browser acceptance itself stays NOTRUN.
"""
import re
from pathlib import Path

import pytest

from talkabout.classroom_panel import PANEL_ACTIONS, PANEL_READS

PANEL = Path(__file__).resolve().parents[1] / "talkabout" / "web" / "classroom-panel.mjs"
APP = Path(__file__).resolve().parents[1] / "talkabout" / "web" / "app.mjs"


def test_panel_asset_is_served_by_the_web_shell(fixture):
    from conftest import LocalClient
    store, core, rid = fixture
    client = LocalClient(store, "fixture-human")
    body = client.http.get("/web/classroom-panel.mjs")
    body.raise_for_status()
    assert "maybeMountClassroomPanel" in body.text and "production" in body.text


def test_panel_only_uses_catalog_actions_and_reads():
    source = PANEL.read_text(encoding="utf-8")
    reads = {name for name in re.findall(r'rooms/\$\{room(?:Id|\.id)\}/([a-zA-Z-]+)', source)}
    assert {'production', 'search'} <= reads <= (PANEL_READS | {'controls', 'artifacts'})


def test_panel_is_wired_into_the_app_shell():
    source = APP.read_text(encoding="utf-8")
    assert "classroom-panel.mjs" in source and "maybeMountClassroomPanel" in source


def test_panel_never_claims_execution_or_application():
    source = PANEL.read_text(encoding="utf-8")
    for token in ("applyPatch", "DELETE", "exec("):
        assert token not in source
