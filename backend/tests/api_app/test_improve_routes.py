"""
Checkpoint-3 verification (API): the four REST endpoints (3.12), the 409
concurrency gate (3.13), end-to-end analyze → propose → review → apply →
rollback for one agent (3.19) and one orchestration (3.20), and JSON
persistence across a simulated backend restart (3.21).
"""
import json
import os
import shutil

import pytest

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


def agent_diff(**overrides):
    diff = {
        "target_object_id": "agent_1",
        "target_kind": "agent",
        "field_edits": [
            {"field": "system_prompt",
             "old_value": "You are a helpful test assistant.",
             "new_value": "You are a relentless assistant. Never give up.",
             "rationale": "give_up rate 0.6"}
        ],
        "rationale": "Frequent give-ups observed.",
        "evidence_pointers": [{"trace_file": "agent_1/2026-01/s.json",
                               "message_idx": 2}],
        "expected_metric_deltas": {"give_up": -0.4},
    }
    diff.update(overrides)
    return diff


def orch_diff():
    return {
        "target_object_id": "orch_1",
        "target_kind": "orchestration",
        "field_edits": [
            {"field": "steps[0].prompt_template",
             "old_value": "old {state.input}",
             "new_value": "Be precise: {state.input}",
             "rationale": "errors detector fired on step output"}
        ],
        "rationale": "Step prompt too vague.",
        "evidence_pointers": [{"trace_file": "orch_1/2026-01/r.json",
                               "message_idx": 1}],
        "expected_metric_deltas": {"errors": -0.3},
    }


def seed_agent():
    return seed.seed_agents([seed.make_agent(id="agent_1")])[0]


def seed_orch():
    orch = seed.make_orchestration(
        id="orch_1",
        steps=[{"id": "s1", "name": "Step", "type": "agent",
                "agent_id": "agent_1", "prompt_template": "old {state.input}",
                "next_step_id": None}],
    )
    return seed.seed_orchestrations([orch])[0]


class TestProposeEndpoint:
    async def test_propose_returns_run_and_diff(self, client, fake_llm):
        seed_agent()
        fake_llm.script([json.dumps(agent_diff())])
        resp = await client.post("/api/improve/propose", json={
            "target_object_id": "agent_1", "target_kind": "agent",
            "tuner_model": "gpt-4o"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["run"]["tuner_model"] == "gpt-4o"
        assert body["proposed_diff"]["field_edits"][0]["field"] == "system_prompt"
        # evidence-first: pointers present on the diff (3.17 data contract)
        assert body["proposed_diff"]["evidence_pointers"][0]["message_idx"] == 2

    async def test_propose_404_unknown_target(self, client):
        resp = await client.post("/api/improve/propose", json={
            "target_object_id": "ghost", "target_kind": "agent"})
        assert resp.status_code == 404

    async def test_propose_409_when_run_in_progress(self, client, fake_llm):
        """3.13 — concurrent run on the same target_object_id is rejected."""
        seed_agent()
        fake_llm.script([json.dumps(agent_diff()), json.dumps(agent_diff())])
        first = await client.post("/api/improve/propose", json={
            "target_object_id": "agent_1", "target_kind": "agent"})
        assert first.status_code == 200
        second = await client.post("/api/improve/propose", json={
            "target_object_id": "agent_1", "target_kind": "agent"})
        assert second.status_code == 409

    async def test_propose_422_on_persistently_bad_tuner_output(
            self, client, fake_llm):
        seed_agent()
        fake_llm.script(["nope", "still nope"])
        resp = await client.post("/api/improve/propose", json={
            "target_object_id": "agent_1", "target_kind": "agent"})
        assert resp.status_code == 422

    async def test_propose_401_when_gate_enabled(self, client, monkeypatch):
        import core.routes.improve as improve_routes
        monkeypatch.setattr(improve_routes, "load_settings", lambda: {
            "login_enabled": True, "login_username": "alice",
            "login_password_hash": "x"})
        resp = await client.post("/api/improve/propose", json={
            "target_object_id": "agent_1", "target_kind": "agent"})
        assert resp.status_code == 401


class TestAgentEndToEnd:
    """3.19 — analyze → propose → review → apply → rollback for one agent."""

    async def test_full_loop(self, client, fake_llm):
        seed_agent()

        # analyze (read-only insights endpoint from CP2)
        resp = await client.get("/api/improve/insights",
                                params={"agent_id": "agent_1"})
        assert resp.status_code == 200

        # propose
        fake_llm.script([json.dumps(agent_diff())])
        resp = await client.post("/api/improve/propose", json={
            "target_object_id": "agent_1", "target_kind": "agent"})
        run_id = resp.json()["run"]["run_id"]

        # review + apply (all fields accepted)
        resp = await client.post("/api/improve/apply", json={"run_id": run_id})
        assert resp.status_code == 200
        assert resp.json()["object"]["version_n"] == 2

        # live agent updated
        resp = await client.get("/api/agents")
        live = next(a for a in resp.json() if a["id"] == "agent_1")
        assert "Never give up" in live["system_prompt"]

        # versions endpoint (3.12/3.16 data source)
        resp = await client.get("/api/improve/versions/agent_1")
        body = resp.json()
        assert [v["version_n"] for v in body["versions"]] == [1, 2]
        assert body["runs"][0]["decision"] == "keep"
        assert body["versions"][1]["metric_snapshot"] == {"give_up": -0.4}

        # rollback
        resp = await client.post("/api/improve/rollback/agent_1/1")
        assert resp.status_code == 200
        resp = await client.get("/api/agents")
        live = next(a for a in resp.json() if a["id"] == "agent_1")
        assert live["system_prompt"] == "You are a helpful test assistant."
        assert live["version_n"] == 1

    async def test_per_field_reject_subset(self, client, fake_llm):
        """3.15 backend contract — apply honors per-field accept/reject."""
        seed_agent()
        diff = agent_diff(field_edits=[
            {"field": "system_prompt", "new_value": "NEW"},
            {"field": "max_turns", "new_value": 12},
        ])
        fake_llm.script([json.dumps(diff)])
        resp = await client.post("/api/improve/propose", json={
            "target_object_id": "agent_1", "target_kind": "agent"})
        run_id = resp.json()["run"]["run_id"]
        resp = await client.post("/api/improve/apply", json={
            "run_id": run_id, "accepted_fields": ["max_turns"]})
        assert resp.status_code == 200
        obj = resp.json()["object"]
        assert obj["max_turns"] == 12
        assert obj["system_prompt"] == "You are a helpful test assistant."

    async def test_reject_action_closes_run(self, client, fake_llm):
        seed_agent()
        fake_llm.script([json.dumps(agent_diff()), json.dumps(agent_diff())])
        resp = await client.post("/api/improve/propose", json={
            "target_object_id": "agent_1", "target_kind": "agent"})
        run_id = resp.json()["run"]["run_id"]
        resp = await client.post("/api/improve/apply", json={
            "run_id": run_id, "action": "reject"})
        assert resp.status_code == 200
        # lock released: a new propose succeeds
        resp = await client.post("/api/improve/propose", json={
            "target_object_id": "agent_1", "target_kind": "agent"})
        assert resp.status_code == 200


class TestOrchestrationEndToEnd:
    """3.20 — the same flow works for one orchestration."""

    async def test_full_loop(self, client, fake_llm):
        seed_orch()

        fake_llm.script([json.dumps(orch_diff())])
        resp = await client.post("/api/improve/propose", json={
            "target_object_id": "orch_1", "target_kind": "orchestration"})
        assert resp.status_code == 200
        run_id = resp.json()["run"]["run_id"]

        resp = await client.post("/api/improve/apply", json={"run_id": run_id})
        assert resp.status_code == 200
        assert resp.json()["object"]["steps"][0]["prompt_template"] == \
            "Be precise: {state.input}"

        resp = await client.get("/api/orchestrations/orch_1")
        assert resp.json()["steps"][0]["prompt_template"] == \
            "Be precise: {state.input}"
        assert resp.json()["version_n"] == 2

        resp = await client.post("/api/improve/rollback/orch_1/1")
        assert resp.status_code == 200
        resp = await client.get("/api/orchestrations/orch_1")
        assert resp.json()["steps"][0]["prompt_template"] == "old {state.input}"
        assert resp.json()["version_n"] == 1


class TestRestartPersistence:
    """3.21 — applied diffs survive a backend restart (JSON on disk)."""

    async def test_applied_diff_is_on_disk_not_just_in_cache(
            self, client, fake_llm):
        from core.config import DATA_DIR
        seed_agent()
        fake_llm.script([json.dumps(agent_diff())])
        resp = await client.post("/api/improve/propose", json={
            "target_object_id": "agent_1", "target_kind": "agent"})
        run_id = resp.json()["run"]["run_id"]
        await client.post("/api/improve/apply", json={"run_id": run_id})

        # Read the store file directly — what a fresh process would load.
        with open(os.path.join(DATA_DIR, "user_agents.json"),
                  encoding="utf-8") as f:
            on_disk = json.load(f)
        agent = next(a for a in on_disk if a["id"] == "agent_1")
        assert "Never give up" in agent["system_prompt"]
        assert agent["version_n"] == 2
        assert agent["improvement_run_id"] == run_id

        # Version snapshots + run index are plain JSON on disk too.
        base = os.path.join(DATA_DIR, "improve", "default")
        assert os.path.exists(os.path.join(base, "versions", "agent_1", "v1.json"))
        assert os.path.exists(os.path.join(base, "versions", "agent_1", "v2.json"))
        with open(os.path.join(base, "runs.json"), encoding="utf-8") as f:
            runs = json.load(f)
        assert runs[0]["decision"] == "keep" and runs[0]["new_version_n"] == 2
