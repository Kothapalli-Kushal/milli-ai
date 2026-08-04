"""
Checkpoint-4 verification (API): benchmark authoring CRUD, the run endpoint
(POST /api/improve/benchmark/{id}) and results endpoint
(GET /api/improve/benchmark/results), auth scoping (4.9), and the
before/after delta flow driven entirely over HTTP.
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


def bench_body(**overrides):
    b = {
        "name": "API suite",
        "target_object_id": "agent_1",
        "inputs": [{"prompt": "Do the thing."}],
        "scorer": {"metrics": {"success": 1.0, "give_up": 1.0}},
    }
    b.update(overrides)
    return b


def seed_agent():
    return seed.seed_agents(
        [seed.make_agent(id="agent_1", tools=[], skip_default_tools=True)]
    )[0]


class TestBenchmarkCrud:
    async def test_put_get_delete_roundtrip(self, client):
        resp = await client.put("/api/improve/benchmark/bench_api", json=bench_body())
        assert resp.status_code == 200
        assert resp.json()["id"] == "bench_api"  # path id is authoritative

        resp = await client.get("/api/improve/benchmarks")
        assert [b["id"] for b in resp.json()] == ["bench_api"]

        resp = await client.delete("/api/improve/benchmark/bench_api")
        assert resp.status_code == 200
        resp = await client.get("/api/improve/benchmarks")
        assert resp.json() == []

    async def test_put_invalid_suite_422(self, client):
        resp = await client.put("/api/improve/benchmark/bench_bad",
                                json=bench_body(inputs=[]))
        assert resp.status_code == 422

    async def test_delete_unknown_404(self, client):
        resp = await client.delete("/api/improve/benchmark/ghost")
        assert resp.status_code == 404

    async def test_auth_401_when_gate_enabled(self, client, monkeypatch):
        import core.routes.improve as improve_routes
        monkeypatch.setattr(improve_routes, "load_settings", lambda: {
            "login_enabled": True, "login_username": "alice",
            "login_password_hash": "x"})
        for method, url in (
            ("get", "/api/improve/benchmarks"),
            ("get", "/api/improve/benchmark/results"),
        ):
            resp = await getattr(client, method)(url)
            assert resp.status_code == 401
        resp = await client.post("/api/improve/benchmark/x", json={})
        assert resp.status_code == 401


class TestBenchmarkRun:
    async def test_run_and_results(self, client, fake_llm):
        seed_agent()
        await client.put("/api/improve/benchmark/bench_api", json=bench_body())
        fake_llm.script(["Did the thing."])

        resp = await client.post("/api/improve/benchmark/bench_api", json={})
        assert resp.status_code == 200
        body = resp.json()
        assert body["score"] == 1.0
        assert body["trace_count"] == 1
        assert body["per_metric"]["success"]["rate"] == 1.0

        resp = await client.get("/api/improve/benchmark/results",
                                params={"benchmark_id": "bench_api"})
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) == 1 and results[0]["run_id"] == body["run_id"]

        # filter by target too
        resp = await client.get("/api/improve/benchmark/results",
                                params={"target_object_id": "agent_1"})
        assert len(resp.json()) == 1

    async def test_run_unknown_benchmark_404(self, client):
        resp = await client.post("/api/improve/benchmark/ghost", json={})
        assert resp.status_code == 404

    async def test_run_unknown_target_404(self, client):
        await client.put("/api/improve/benchmark/bench_api",
                         json=bench_body(target_object_id="ghost"))
        resp = await client.post("/api/improve/benchmark/bench_api", json={})
        assert resp.status_code == 404

    async def test_before_after_delta_over_http(self, client, fake_llm):
        """Propose → baseline benchmark → apply → new benchmark, all via REST."""
        seed_agent()
        await client.put("/api/improve/benchmark/bench_api", json=bench_body())

        diff = {
            "target_object_id": "agent_1", "target_kind": "agent",
            "field_edits": [{"field": "system_prompt",
                             "new_value": "Never give up."}],
            "rationale": "give_up findings", "evidence_pointers": [],
            "expected_metric_deltas": {"give_up": -1.0},
        }
        fake_llm.script([json.dumps(diff)])
        resp = await client.post("/api/improve/propose", json={
            "target_object_id": "agent_1", "target_kind": "agent"})
        run_id = resp.json()["run"]["run_id"]

        fake_llm.script(["I cannot help with that."])
        resp = await client.post("/api/improve/benchmark/bench_api", json={
            "improvement_run_id": run_id, "record_as": "baseline"})
        assert resp.json()["score"] == 0.0

        await client.post("/api/improve/apply", json={"run_id": run_id})

        fake_llm.script(["Did the thing."])
        resp = await client.post("/api/improve/benchmark/bench_api", json={
            "improvement_run_id": run_id, "record_as": "new"})
        assert resp.json()["score"] == 1.0
        assert resp.json()["target_version_n"] == 2

        # scores landed on the ImprovementRun, visible via the versions route
        resp = await client.get("/api/improve/versions/agent_1")
        run = next(r for r in resp.json()["runs"] if r["run_id"] == run_id)
        assert run["baseline_score"] == 0.0 and run["new_score"] == 1.0
        assert run["benchmark_id"] == "bench_api"
