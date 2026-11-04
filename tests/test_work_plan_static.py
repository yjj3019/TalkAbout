"""Synthetic static-asset contract for the private work-plan module.

The browser imports work-plan.mjs as part of app.mjs, so it must remain reachable
while setup is locked and through the /web/{name} route. No other new module may
become publicly readable.
"""

from fastapi.testclient import TestClient

from talkabout.api import create_app
from talkabout.setup import complete_setup, issue_code
from talkabout.store import MemoryStore

WORK_PLAN = "/web/work-plan.mjs"


def _body(code):
    return {
        "code": code,
        "workspace_id": "synthetic-install",
        "display_name": "Fixture Owner",
        "username": "owner",
        "password": "synthetic-password-only",
        "confirmation": "synthetic-password-only",
    }


def _locked_client():
    store = MemoryStore()
    client = TestClient(create_app(store, start_scheduler=False, allowed_hosts=["testserver"]))
    return store, client


def test_work_plan_module_is_served_while_setup_is_locked_and_has_javascript_mime():
    store, client = _locked_client()
    response = client.get(WORK_PLAN)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript"), response.headers["content-type"]
    assert "createWorkPlanView" in response.text
    assert response.headers["cache-control"] == "public, max-age=0, must-revalidate"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert client.get("/").status_code == 200
    assert client.get("/web/app.mjs").status_code == 200
    # The setup gate must still reject a module that is not on the allowlist.
    assert client.get("/web/not-allowed.mjs").status_code == 503
    assert client.get("/web/not-allowed.mjs").json() == {"code": "setup_required"}
    complete_setup(store, _body(issue_code(store)))


def test_work_plan_module_remains_the_only_new_public_module_after_setup():
    store, client = _locked_client()
    complete_setup(store, _body(issue_code(store)))

    response = client.get(WORK_PLAN)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert "createWorkPlanView" in response.text
    # Existing public assets keep working.
    for path in ("/web/app.mjs", "/web/state.mjs", "/web/style.css"):
        assert client.get(path).status_code == 200, path
    # A sibling or renamed module must not be exposed by the allowlist.
    for path in (
        "/web/not-allowed.mjs",
        "/web/work-plan.js",
        "/web/work-plan.mjs.bak",
        "/web/../work-plan.mjs",
    ):
        assert client.get(path).status_code == 404, path
