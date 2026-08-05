"""
Checkpoint-6 verification (API), chunk 2 — checklist 6.29, plus the
end-to-end pieces that only show up over HTTP or across two full runs:

- 6.16  splits honored and train/holdout/regression reported separately
- 6.25  extraction failure distinct from check failure end to end; the rate
        computed and present in the result record
- 6.27  a `strict` deterministic outcome score is EXACTLY reproducible across
        two runs on the same version
- 6.28  rubric outcome variance measured across two runs and reported
- 6.29  all seven CP6 routes implemented and auth-scoped via
        `resolve_improve_user`; no new router, no new server.py include
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


def seed_agent():
    return seed.seed_agents(
        [seed.make_agent(id="agent_1", tools=[], skip_default_tools=True)]
    )[0]


RUBRIC_BODY = {
    "name": "Research synthesis quality",
    "criteria": [
        {"id": "coverage", "kind": "key_point_coverage", "weight": 3.0,
         "critical": True, "critical_floor": 0.5},
        {"id": "cited_sources", "kind": "deterministic", "weight": 1.0,
         "check": {"extract": {"from": "final_output"},
                   "compare": {"type": "regex", "value": r"(https?://[^\s]+.*){2,}"}}},
    ],
}


def v2_suite(**overrides):
    suite = {
        "name": "NL2SQL outcome suite",
        "target_object_id": "agent_1",
        "schema_version": 2,
        "grading_mode": "deterministic",
        "scorer": {"metrics": {"success": 1.0},
                   "process_weight": 1.0, "outcome_weight": 1.0},
        "split_policy": {"mode": "explicit", "seed": 1337},
        "inputs": [
            {"id": "in_001", "prompt": "Q1?", "split": "train", "weight": 1.0,
             "expected": {"checks": [
                 {"id": "answer", "weight": 1.0,
                  "extract": {"from": "final_output"},
                  "compare": {"type": "contains_all", "value": ["APAC"]}}]}},
            {"id": "in_002", "prompt": "Q2?", "split": "holdout", "weight": 1.0,
             "expected": {"checks": [
                 {"id": "answer", "weight": 1.0,
                  "extract": {"from": "final_output"},
                  "compare": {"type": "contains_all", "value": ["APAC"]}}]}},
            {"id": "in_003", "prompt": "Q3?", "split": "regression", "weight": 1.0,
             "expected": {"checks": [
                 {"id": "answer", "weight": 1.0,
                  "extract": {"from": "final_output"},
                  "compare": {"type": "contains_all", "value": ["APAC"]}}]}},
        ],
    }
    suite.update(overrides)
    return suite


# ── 6.29 — the seven routes ──────────────────────────────────────────────────

class TestRubricRoutes:
    async def test_put_creates_then_versions(self, client):
        first = await client.put("/api/improve/rubric/rubric_a", json=RUBRIC_BODY)
        assert first.status_code == 200
        assert first.json()["rubric"]["version"] == 1

        edited = json.loads(json.dumps(RUBRIC_BODY))
        edited["criteria"][0]["weight"] = 5.0
        second = await client.put("/api/improve/rubric/rubric_a", json=edited)
        assert second.json()["rubric"]["version"] == 2
        assert second.json()["rubric"]["content_hash"] != \
            first.json()["rubric"]["content_hash"]

    async def test_path_id_is_authoritative(self, client):
        body = {**RUBRIC_BODY, "id": "spoofed"}
        res = await client.put("/api/improve/rubric/rubric_real", json=body)
        assert res.json()["rubric"]["id"] == "rubric_real"

    async def test_get_returns_latest_plus_version_history(self, client):
        await client.put("/api/improve/rubric/rubric_a", json=RUBRIC_BODY)
        edited = json.loads(json.dumps(RUBRIC_BODY))
        edited["name"] = "Renamed"
        await client.put("/api/improve/rubric/rubric_a", json=edited)

        res = await client.get("/api/improve/rubric/rubric_a")
        body = res.json()
        assert body["rubric"]["version"] == 2
        assert [v["version"] for v in body["versions"]] == [1, 2]
        assert all(v["content_hash"].startswith("sha256:") for v in body["versions"])

    async def test_get_pins_a_version(self, client):
        await client.put("/api/improve/rubric/rubric_a", json=RUBRIC_BODY)
        edited = json.loads(json.dumps(RUBRIC_BODY))
        edited["name"] = "Renamed"
        await client.put("/api/improve/rubric/rubric_a", json=edited)

        res = await client.get("/api/improve/rubric/rubric_a?version=1")
        assert res.json()["rubric"]["name"] == "Research synthesis quality"

    async def test_get_missing_is_404(self, client):
        assert (await client.get("/api/improve/rubric/nope")).status_code == 404

    async def test_list_returns_latest_versions(self, client):
        await client.put("/api/improve/rubric/rubric_a", json=RUBRIC_BODY)
        await client.put("/api/improve/rubric/rubric_b",
                         json={**RUBRIC_BODY, "name": "B"})
        listed = (await client.get("/api/improve/rubrics")).json()
        assert {r["id"] for r in listed} == {"rubric_a", "rubric_b"}

    async def test_invalid_criterion_kind_is_422(self, client):
        res = await client.put("/api/improve/rubric/rubric_bad", json={
            "name": "bad", "criteria": [{"id": "x", "kind": "vibes"}]})
        assert res.status_code == 422

    async def test_delete_soft_deletes(self, client):
        await client.put("/api/improve/rubric/rubric_a", json=RUBRIC_BODY)
        assert (await client.delete("/api/improve/rubric/rubric_a")).status_code == 200
        assert (await client.get("/api/improve/rubrics")).json() == []

    async def test_delete_refused_while_referenced(self, client):
        seed_agent()
        await client.put("/api/improve/rubric/rubric_a", json=RUBRIC_BODY)
        await client.put("/api/improve/benchmark/bench_r", json={
            "name": "r", "target_object_id": "agent_1", "schema_version": 2,
            "grading_mode": "rubric", "rubric_id": "rubric_a",
            "inputs": [{"id": "in_001", "prompt": "p",
                        "expected": {"key_points": [{"id": "kp1", "text": "t"}]}}],
        })
        res = await client.delete("/api/improve/rubric/rubric_a")
        assert res.status_code == 409
        assert "bench_r" in res.json()["detail"]

    async def test_delete_missing_is_404(self, client):
        assert (await client.delete("/api/improve/rubric/nope")).status_code == 404


class TestResplitRoute:
    async def test_requires_explicit_confirmation(self, client):
        """Re-splitting invalidates comparability with every previous run, so
        it must not happen by accident."""
        seed_agent()
        await client.put("/api/improve/benchmark/bench_s",
                         json=v2_suite(split_policy={"mode": "random", "seed": 5}))
        res = await client.post("/api/improve/benchmark/bench_s/resplit", json={})
        assert res.status_code == 400
        assert "comparability" in res.json()["detail"]

    async def test_materializes_and_persists_the_assignment(self, client):
        seed_agent()
        suite = v2_suite(split_policy={"mode": "random", "seed": 5,
                                       "ratios": {"train": 0.5, "holdout": 0.5}})
        await client.put("/api/improve/benchmark/bench_s", json=suite)
        res = await client.post("/api/improve/benchmark/bench_s/resplit",
                                json={"confirm": True})
        assert res.status_code == 200
        assignments = res.json()["assignments"]

        stored = (await client.get("/api/improve/benchmarks")).json()
        stored_suite = next(s for s in stored if s["id"] == "bench_s")
        for item in stored_suite["inputs"]:
            assert item["split"] == assignments[item["id"]]["split"]
        # The declared regression input is never reassigned.
        assert assignments["in_003"]["split"] == "regression"

    async def test_kfold_materializes_fold_numbers(self, client):
        seed_agent()
        suite = v2_suite(split_policy={"mode": "kfold", "seed": 9,
                                       "kfold": {"k": 2, "rotation": "per_iteration"}})
        await client.put("/api/improve/benchmark/bench_k", json=suite)
        res = await client.post("/api/improve/benchmark/bench_k/resplit",
                                json={"confirm": True})
        folds = {a["fold"] for a in res.json()["assignments"].values()}
        assert {0, 1}.issubset(folds)

    async def test_impossible_policy_is_422(self, client):
        seed_agent()
        suite = v2_suite(split_policy={"mode": "kfold", "seed": 1,
                                       "kfold": {"k": 9}})
        await client.put("/api/improve/benchmark/bench_k2", json=suite)
        res = await client.post("/api/improve/benchmark/bench_k2/resplit",
                                json={"confirm": True})
        assert res.status_code == 422

    async def test_missing_benchmark_is_404(self, client):
        res = await client.post("/api/improve/benchmark/nope/resplit",
                                json={"confirm": True})
        assert res.status_code == 404


class TestAugmentRoutes:
    async def test_augment_refused_when_disabled(self, client):
        seed_agent()
        await client.put("/api/improve/benchmark/bench_a", json=v2_suite())
        res = await client.post("/api/improve/benchmark/bench_a/augment")
        assert res.status_code == 422
        assert "not enabled" in res.json()["detail"]

    async def test_augment_missing_benchmark_is_404(self, client):
        assert (await client.post(
            "/api/improve/benchmark/nope/augment")).status_code == 404

    async def test_approve_keeps_and_reject_removes(self, client):
        seed_agent()
        suite = v2_suite()
        suite["inputs"].append({
            "id": "in_001__aug1", "prompt": "paraphrase one",
            "parent_input_id": "in_001", "is_augmented": True, "approved": False,
            "split": "train", "weight": 0.5, "expected": {"$ref": "in_001"},
        })
        suite["inputs"].append({
            "id": "in_001__aug2", "prompt": "paraphrase two",
            "parent_input_id": "in_001", "is_augmented": True, "approved": False,
            "split": "train", "weight": 0.5, "expected": {"$ref": "in_001"},
        })
        await client.put("/api/improve/benchmark/bench_a", json=suite)

        res = await client.post(
            "/api/improve/benchmark/bench_a/augment/approve",
            json={"decisions": {"in_001__aug1": True, "in_001__aug2": False}},
        )
        assert res.json()["approved"] == ["in_001__aug1"]
        assert res.json()["removed"] == ["in_001__aug2"]

        stored = next(
            s for s in (await client.get("/api/improve/benchmarks")).json()
            if s["id"] == "bench_a"
        )
        ids = [i["id"] for i in stored["inputs"]]
        assert "in_001__aug1" in ids and "in_001__aug2" not in ids
        variant = next(i for i in stored["inputs"] if i["id"] == "in_001__aug1")
        assert variant["approved"] is True
        assert variant["expected"] == {"$ref": "in_001"}   # shared, not copied

    async def test_approve_missing_benchmark_is_404(self, client):
        res = await client.post("/api/improve/benchmark/nope/augment/approve",
                                json={"decisions": {}})
        assert res.status_code == 404


class TestRoutesAreAuthScoped:
    """6.29 — every CP6 route resolves its user through `resolve_improve_user`,
    and none of them adds a router or a server.py include."""

    CP6_ROUTES = [
        ("GET", "/api/improve/rubrics"),
        ("GET", "/api/improve/rubric/{rubric_id}"),
        ("PUT", "/api/improve/rubric/{rubric_id}"),
        ("DELETE", "/api/improve/rubric/{rubric_id}"),
        ("POST", "/api/improve/benchmark/{benchmark_id}/augment"),
        ("POST", "/api/improve/benchmark/{benchmark_id}/augment/approve"),
        ("POST", "/api/improve/benchmark/{benchmark_id}/resplit"),
    ]

    def test_all_seven_are_registered_on_the_existing_router(self):
        from core.routes.improve import router
        registered = {
            (method, route.path)
            for route in router.routes
            for method in getattr(route, "methods", set())
        }
        for method, path in self.CP6_ROUTES:
            assert (method, path) in registered, f"{method} {path} is missing"

    def test_every_cp6_route_depends_on_resolve_improve_user(self):
        from core.routes.improve import resolve_improve_user, router
        paths = {p for _m, p in self.CP6_ROUTES}
        checked = 0
        for route in router.routes:
            if route.path not in paths:
                continue
            calls = [d.call for d in route.dependant.dependencies]
            assert resolve_improve_user in calls, f"{route.path} is not auth-scoped"
            checked += 1
        # Three of the seven share the /rubric/{id} path under different verbs.
        assert checked == len(self.CP6_ROUTES)

    def test_no_new_router_and_no_new_server_include(self):
        """The §0.4 hook budget stays consumed.

        Evidence, not inspection: `core/routes/improve.py` declares exactly one
        APIRouter, and `server.py` includes it exactly once. CP6 added seven
        routes to that existing router and nothing else.
        """
        import inspect
        import core.routes.improve as improve_routes
        import core.server as server

        route_src = inspect.getsource(improve_routes)
        assert route_src.count("APIRouter(") == 1

        server_src = inspect.getsource(server)
        assert server_src.count("include_router(improve_router") == 1
        assert server_src.count("improve_router") == 2  # the import + the include

    def test_every_cp6_path_is_served_by_the_existing_improve_router(self):
        from core.routes.improve import router
        served = {r.path for r in router.routes}
        assert {p for _m, p in self.CP6_ROUTES}.issubset(served)

    async def test_routes_401_when_the_login_gate_is_on(self, client, monkeypatch):
        import core.routes.improve as improve_routes
        monkeypatch.setattr(
            improve_routes, "load_settings",
            lambda: {"login_enabled": True, "login_username": "u",
                     "login_password_hash": "h"},
        )
        assert (await client.get("/api/improve/rubrics")).status_code == 401
        assert (await client.put("/api/improve/rubric/x",
                                 json=RUBRIC_BODY)).status_code == 401
        assert (await client.post(
            "/api/improve/benchmark/x/resplit", json={"confirm": True})
        ).status_code == 401


# ── 6.16 / 6.25 / 6.27 — a full graded run over HTTP ─────────────────────────

class TestGradedRunOverHttp:
    async def test_splits_are_reported_separately(self, client, fake_llm):
        seed_agent()
        fake_llm.set_default("APAC led Q3 revenue.")
        await client.put("/api/improve/benchmark/bench_v2", json=v2_suite())

        result = (await client.post("/api/improve/benchmark/bench_v2")).json()
        assert result["scores_by_split"] == {
            "train": 1.0, "holdout": 1.0, "regression": 1.0}
        assert result["outcome_score"] == 1.0

    async def test_results_endpoint_carries_the_new_fields(self, client, fake_llm):
        seed_agent()
        fake_llm.set_default("APAC led Q3 revenue.")
        await client.put("/api/improve/benchmark/bench_v2", json=v2_suite())
        await client.post("/api/improve/benchmark/bench_v2")

        results = (await client.get(
            "/api/improve/benchmark/results?benchmark_id=bench_v2")).json()
        assert len(results) == 1
        for key in ("process_score", "outcome_score", "composite_score",
                    "scores_by_split", "extraction_failed_rate",
                    "grading_mode", "grading_strictness", "per_input"):
            assert key in results[0]

    async def test_extraction_failure_is_distinct_and_surfaced(self, client, fake_llm):
        """6.25 — 'the agent never called the tool' must not be reported as
        'your SQL is wrong'."""
        seed_agent()
        fake_llm.set_default("APAC led Q3 revenue.")
        suite = v2_suite()
        # in_001 asks for a tool call the agent never makes; the others don't.
        suite["inputs"][0]["expected"]["checks"] = [{
            "id": "sql", "weight": 1.0,
            "extract": {"from": "tool_call_arg", "tool": "sql_agent",
                        "arg": "query"},
            "compare": {"type": "exact", "value": "SELECT 1"},
        }]
        await client.put("/api/improve/benchmark/bench_v2", json=suite)

        result = (await client.post("/api/improve/benchmark/bench_v2")).json()

        failed = next(o for o in result["per_input"] if o["input_id"] == "in_001")
        assert failed["checks"][0]["status"] == "extraction_failed"
        assert failed["score"] is None and failed["na_reason"] == "extraction_failed"
        assert result["extraction_failed_count"] == 1
        assert result["extraction_failed_rate"] == pytest.approx(1 / 3)
        # The failed input drops out of the denominator; it is not scored 0.
        assert result["outcome_score"] == 1.0
        assert result["scores_by_split"]["train"] is None

    async def test_strict_deterministic_score_is_exactly_reproducible(
        self, client, fake_llm
    ):
        """6.27 — no LLM is in the loop and the data is fixed, so anything but
        exact equality is a bug in an extractor or a nondeterministic agent."""
        seed_agent()
        fake_llm.set_default("APAC led Q3 revenue.")
        await client.put("/api/improve/benchmark/bench_v2", json=v2_suite())

        first = (await client.post("/api/improve/benchmark/bench_v2")).json()
        second = (await client.post("/api/improve/benchmark/bench_v2")).json()

        assert first["grading_strictness"] == "strict"
        assert first["outcome_score"] == second["outcome_score"]
        assert first["scores_by_split"] == second["scores_by_split"]
        assert abs(first["outcome_score"] - second["outcome_score"]) <= \
            __import__("core.improve.benchmark", fromlist=["x"]) \
            .OUTCOME_VARIANCE_THRESHOLD_STRICT_EXACT

    async def test_per_input_outcomes_are_byte_identical_across_runs(
        self, client, fake_llm
    ):
        seed_agent()
        fake_llm.set_default("APAC led Q3 revenue.")
        await client.put("/api/improve/benchmark/bench_v2", json=v2_suite())

        def strip_volatile(per_input):
            return [
                {**o, "checks": [
                    {k: v for k, v in c.items() if k != "trace_file"}
                    for c in o["checks"]
                ]}
                for o in per_input
            ]

        first = (await client.post("/api/improve/benchmark/bench_v2")).json()
        second = (await client.post("/api/improve/benchmark/bench_v2")).json()
        assert strip_volatile(first["per_input"]) == strip_volatile(second["per_input"])


class TestRubricVarianceMeasurement:
    """6.28 — measure the rubric outcome variance across two runs and report it.

    The suite's fake LLM is deterministic and the verdict cache makes a second
    scoring of unchanged output free, so the figure observed HERE is the
    cache-and-fake floor, not a measurement of a live judge. It is recorded so
    the harness exists and the number is reported rather than asserted.
    """

    async def test_measured_rubric_variance_across_two_runs(self, client, fake_llm):
        from core.improve.benchmark import OUTCOME_VARIANCE_THRESHOLD_RUBRIC

        seed_agent()
        await client.put("/api/improve/rubric/rubric_res", json={
            "name": "Research quality",
            "criteria": [{
                "id": "cited_sources", "kind": "deterministic", "weight": 1.0,
                "check": {"extract": {"from": "final_output"},
                          "compare": {"type": "regex",
                                      "value": r"(https?://[^\s]+.*){2,}"}},
            }],
        })
        fake_llm.set_default("See https://a.com and https://b.com for detail.")
        await client.put("/api/improve/benchmark/bench_rub", json={
            "name": "Rubric suite", "target_object_id": "agent_1",
            "schema_version": 2, "grading_mode": "rubric",
            "rubric_id": "rubric_res",
            "scorer": {"metrics": {"success": 1.0}, "outcome_weight": 1.0},
            "inputs": [{"id": "in_001", "prompt": "Summarize.", "split": "holdout",
                        "expected": {"key_points": []}}],
        })

        first = (await client.post("/api/improve/benchmark/bench_rub")).json()
        second = (await client.post("/api/improve/benchmark/bench_rub")).json()

        observed = abs(first["outcome_score"] - second["outcome_score"])
        print(f"\n[6.28] observed rubric outcome variance across two runs: "
              f"{observed:.6f} (documented threshold "
              f"{OUTCOME_VARIANCE_THRESHOLD_RUBRIC})")

        assert observed <= OUTCOME_VARIANCE_THRESHOLD_RUBRIC
        assert first["rubric_content_hash"] == second["rubric_content_hash"]
        assert first["judge_model"] is not None
