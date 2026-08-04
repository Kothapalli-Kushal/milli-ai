"""
Checkpoint-5 verification (API): the Self-Improvement Inbox endpoint (5.9)
and the "revert all autonomous edits since T" endpoint (5.19), both
auth-scoped through resolve_improve_user like every other improve route.
"""
import os
import shutil

import pytest

from core.improve import applier, inbox as inbox_mod, runs as runs_mod
from _fakes import seed


@pytest.fixture(autouse=True)
def _clean_improve_dir():
    from core.config import DATA_DIR
    improve_dir = os.path.join(DATA_DIR, "improve")
    for root, _dirs, files in os.walk(improve_dir):
        for name in files:
            try:
                os.chmod(os.path.join(root, name), 0o600)
            except OSError:
                pass
    shutil.rmtree(improve_dir, ignore_errors=True)
    yield


def _diff():
    return {
        "target_object_id": "agent_1",
        "target_kind": "agent",
        "field_edits": [
            {"field": "system_prompt", "old_value": "a", "new_value": "b",
             "rationale": "r"}
        ],
        "rationale": "r",
        "evidence_pointers": [],
        "expected_metric_deltas": {},
    }


def _seed_autonomous_apply():
    seed.seed_agents([seed.make_agent(id="agent_1")])
    run = runs_mod.create_run(
        "default", "agent_1", "agent", baseline_version_n=1,
        tuner_model="m", mode="autonomous",
    )
    runs_mod.write_proposal("default", run["run_id"],
                            {"insights": {}, "proposed_diff": _diff()})
    applier.apply_run("default", run["run_id"])
    return run


class TestInboxEndpoint:
    async def test_inbox_lists_entries_newest_first(self, client):
        inbox_mod.add_entry("default", kind="apply", mode="autonomous",
                            object_id="agent_1", version_n=2, message="first")
        inbox_mod.add_entry("default", kind="revert", mode="autonomous",
                            object_id="agent_1", version_n=1, message="second")
        resp = await client.get("/api/improve/inbox")
        assert resp.status_code == 200
        body = resp.json()
        assert [e["message"] for e in body] == ["second", "first"]

    async def test_inbox_filters_by_object_and_kind(self, client):
        inbox_mod.add_entry("default", kind="apply", mode="autonomous",
                            object_id="agent_1", message="a1")
        inbox_mod.add_entry("default", kind="plateau_stop", mode="autonomous",
                            object_id="agent_2", message="a2")
        resp = await client.get("/api/improve/inbox?object_id=agent_2")
        assert [e["object_id"] for e in resp.json()] == ["agent_2"]
        resp = await client.get("/api/improve/inbox?kind=apply")
        assert [e["kind"] for e in resp.json()] == ["apply"]


class TestRevertAutonomousEndpoint:
    async def test_revert_autonomous_since(self, client):
        run = _seed_autonomous_apply()
        resp = await client.post("/api/improve/revert-autonomous",
                                 json={"since": "2000-01-01T00:00:00Z"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["reverted"][0]["object_id"] == "agent_1"
        assert body["reverted"][0]["restored_version_n"] == 1
        assert runs_mod.get_run("default", run["run_id"])["decision"] == "revert"
        # The bulk revert itself is audited in the inbox (never silent)
        inbox = (await client.get("/api/improve/inbox?kind=revert")).json()
        assert inbox

    async def test_revert_scoped_to_object(self, client):
        _seed_autonomous_apply()
        resp = await client.post(
            "/api/improve/revert-autonomous",
            json={"since": "2000-01-01T00:00:00Z", "object_id": "other_agent"},
        )
        assert resp.status_code == 200
        assert resp.json()["reverted"] == []

    async def test_future_since_reverts_nothing(self, client):
        _seed_autonomous_apply()
        resp = await client.post("/api/improve/revert-autonomous",
                                 json={"since": "2999-01-01T00:00:00Z"})
        assert resp.json()["reverted"] == []
