import pytest


@pytest.mark.parametrize("scenario", ["room_commit_order", "pause_complete_lock", "revocation_waiting_poll"])
def test_postgres_integration_gate(scenario):
    pytest.skip("NOTRUN: no approved PostgreSQL test service is available; MemoryStore results do not verify database locks or commit ordering")
