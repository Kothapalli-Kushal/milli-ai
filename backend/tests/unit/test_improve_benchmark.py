"""
Checkpoint-4 verification (unit): benchmark schema + standalone storage
(4.1/4.2), execution through run_agent_step (4.3) and OrchestrationEngine.run
(4.4) with traces emitted via the Checkpoint-1 path (4.5), the weighted
composite scorer with zero-weight exclusion (4.6/4.7), results indexed in
runs.json (4.8), score reproducibility within the documented threshold
(4.12), and a known-good diff producing a positive delta (4.13).
"""
import json
import os
import shutil
import types

import pytest
from pydantic import ValidationError

from core.improve import applier, benchmark as bm, runs as runs_mod
from core.improve.benchmark import (
    SCORE_VARIANCE_THRESHOLD,
    Benchmark,
    score_traces,
)
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


def _server():
    return types.SimpleNamespace(agent_sessions={}, memory_store=None, tool_router={})


def make_benchmark(**overrides):
    b = {
        "id": "bench_1",
        "name": "Give-up regression suite",
        "target_object_id": "agent_1",
        "inputs": [
            {"prompt": "Summarize the report.", "expected_metric_hints": {"give_up": 0.0}},
            {"prompt": "List three risks."},
        ],
        "scorer": {"metrics": {"success": 1.0, "give_up": 1.0, "clean_success": 1.0}},
    }
    b.update(overrides)
    return b


def seed_agent(**overrides):
    agent = seed.make_agent(
        id="agent_1", tools=[], skip_default_tools=True, **overrides
    )
    seed.seed_agents([agent])
    return agent


def _trace(success=True, last_message="All done."):
    return {
        "session_id": "s", "timestamp": "2026-01-01T00:00:00Z", "duration_s": 1.0,
        "success": success, "error": None, "output": last_message,
        "agent_id": "agent_1", "orchestration_id": None, "run_id": None,
        "model": "m1", "metadata": {"kind": "agent", "source": "benchmark"},
        "messages": [{"role": "assistant", "content": last_message,
                      "timestamp": "2026-01-01T00:00:00Z"}],
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
                  "estimated_cost_usd": 0.0, "llm_calls": 1},
        "compaction_events": [],
    }


# ── 4.1 / 4.2 — schema + standalone storage ──────────────────────────────────

class TestBenchmarkSchema:
    def test_valid_benchmark_validates(self):
        b = Benchmark.model_validate(make_benchmark())
        assert b.id == "bench_1" and len(b.inputs) == 2
        assert b.scorer.metrics["give_up"] == 1.0
        assert b.inputs[0].expected_metric_hints == {"give_up": 0.0}

    def test_empty_inputs_rejected(self):
        with pytest.raises(ValidationError):
            Benchmark.model_validate(make_benchmark(inputs=[]))

    def test_save_load_list_delete_roundtrip(self):
        bm.save_benchmark("default", make_benchmark())
        loaded = bm.load_benchmark("default", "bench_1")
        assert loaded["name"] == "Give-up regression suite"
        assert [b["id"] for b in bm.list_benchmarks("default")] == ["bench_1"]
        assert bm.delete_benchmark("default", "bench_1")
        with pytest.raises(bm.BenchmarkNotFound):
            bm.load_benchmark("default", "bench_1")

    def test_benchmark_file_is_standalone_object(self):
        """4.2 — stored under benchmarks/<id>.json, independent of any target."""
        from core.config import DATA_DIR
        bm.save_benchmark("default", make_benchmark())
        path = os.path.join(DATA_DIR, "improve", "default", "benchmarks",
                            "bench_1.json")
        assert os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            assert json.load(f)["target_object_id"] == "agent_1"


# ── 4.6 / 4.7 — weighted composite scorer ────────────────────────────────────

class TestScorer:
    def test_weighted_composite(self):
        traces = [_trace(success=True), _trace(success=False, last_message="I cannot help with that.")]
        # success rate = 0.5; give_up good rate = 0.5 (one give-up)
        result = score_traces(traces, {"success": 3.0, "give_up": 1.0})
        assert result["per_metric"]["success"]["rate"] == 0.5
        assert result["per_metric"]["give_up"]["rate"] == 0.5
        assert result["score"] == 0.5  # (3*0.5 + 1*0.5) / 4

    def test_zero_weight_excludes_detector(self):
        """4.7 — an unreliable detector is excluded via zero weight."""
        traces = [_trace(success=True)]
        with_gu = score_traces(traces, {"success": 1.0, "give_up": 1.0})
        without = score_traces(traces, {"success": 1.0, "give_up": 0.0})
        assert "give_up" in with_gu["per_metric"]
        assert "give_up" not in without["per_metric"]

    def test_positive_detector_counts_hit_as_good(self):
        traces = [_trace(success=True)]  # clean run → clean_success hit
        result = score_traces(traces, {"clean_success": 1.0})
        assert result["per_metric"]["clean_success"]["rate"] == 1.0
        assert result["score"] == 1.0

    def test_unknown_metric_reported_na_and_ignored(self):
        result = score_traces([_trace()], {"success": 1.0, "nonsense": 5.0})
        assert result["per_metric"]["nonsense"]["rate"] == "N/A"
        assert result["score"] == 1.0  # composite over `success` only

    def test_empty_metrics_uses_default_scorer(self):
        result = score_traces([_trace()], {})
        assert set(result["per_metric"]) == {"success", "clean_success"}

    def test_no_traces_scores_none_not_zero(self):
        assert score_traces([], {"success": 1.0})["score"] is None


# ── 4.3 / 4.5 / 4.8 — agent execution through run_agent_step ────────────────

class TestAgentBenchmarkRun:
    async def test_run_scores_and_emits_cp1_traces(self, fake_llm):
        from core.config import DATA_DIR
        seed_agent()
        bm.save_benchmark("default", make_benchmark())
        fake_llm.script(["Summary: fine.", "Risks: a, b, c."])

        result = await bm.run_benchmark("default", "bench_1",
                                        server_module=_server())
        assert result["trace_count"] == 2
        assert result["score"] == 1.0
        assert result["target_kind"] == "agent"
        assert result["target_version_n"] == 1

        # 4.5 — traces landed via the CP1 writer under traces/<agent>/<month>/
        traces_root = os.path.join(DATA_DIR, "improve", "default", "traces",
                                   "agent_1")
        found = []
        for month in os.listdir(traces_root):
            for name in os.listdir(os.path.join(traces_root, month)):
                with open(os.path.join(traces_root, month, name),
                          encoding="utf-8") as f:
                    found.append(json.load(f))
        bench_traces = [t for t in found
                        if t["metadata"]["source"] == "benchmark"]
        assert len(bench_traces) == 2
        assert all(t["metadata"]["kind"] == "agent" for t in bench_traces)

        # 4.8 — result indexed in runs.json and retrievable
        results = bm.list_results("default", benchmark_id="bench_1")
        assert len(results) == 1 and results[0]["run_id"] == result["run_id"]
        assert results[0]["trace_files"]  # evidence pointers to trace files

    async def test_benchmark_reusable_across_targets(self, fake_llm):
        """4.2 — same suite runs against a different target via override."""
        agent_b = seed.make_agent(id="agent_2", tools=[],
                                  skip_default_tools=True)
        seed.seed_agents([seed.make_agent(id="agent_1", tools=[],
                                          skip_default_tools=True), agent_b])
        bm.save_benchmark("default", make_benchmark())
        fake_llm.set_default("Done.")
        result = await bm.run_benchmark("default", "bench_1",
                                        target_object_id="agent_2",
                                        server_module=_server())
        assert result["target_object_id"] == "agent_2"
        assert result["trace_count"] == 2

    async def test_unknown_benchmark_and_target_raise(self, fake_llm):
        with pytest.raises(bm.BenchmarkNotFound):
            await bm.run_benchmark("default", "ghost", server_module=_server())
        bm.save_benchmark("default", make_benchmark(target_object_id="ghost"))
        with pytest.raises(bm.BenchmarkTargetNotFound):
            await bm.run_benchmark("default", "bench_1", server_module=_server())


# ── 4.4 — orchestration execution through OrchestrationEngine.run ────────────

class TestOrchestrationBenchmarkRun:
    async def test_run_through_engine_emits_orch_trace(self, fake_llm):
        orch = seed.make_orchestration(id="orch_1")  # single print step, no LLM
        seed.seed_orchestrations([orch])
        bm.save_benchmark("default", make_benchmark(
            id="bench_orch", target_object_id="orch_1",
            inputs=[{"prompt": "run it"}],
            scorer={"metrics": {"success": 1.0}}))

        result = await bm.run_benchmark("default", "bench_orch",
                                        server_module=_server())
        assert result["target_kind"] == "orchestration"
        assert result["trace_count"] == 1
        assert result["score"] == 1.0
        # CP1 orchestration trace, keyed by our per-input run id
        results = bm.list_results("default", target_object_id="orch_1")
        assert results[0]["trace_files"][0].startswith("orch_1/")


# ── 4.12 — reproducibility within the documented threshold ───────────────────

class TestReproducibility:
    async def test_same_version_scores_within_threshold(self, fake_llm):
        """Threshold: ±0.02 (SCORE_VARIANCE_THRESHOLD, per Appendix C)."""
        seed_agent()
        bm.save_benchmark("default", make_benchmark())
        fake_llm.script(["Summary: fine.", "Risks: a, b, c."])
        first = await bm.run_benchmark("default", "bench_1",
                                       server_module=_server())
        fake_llm.script(["Summary: fine.", "Risks: a, b, c."])
        second = await bm.run_benchmark("default", "bench_1",
                                        server_module=_server())
        assert first["score"] is not None and second["score"] is not None
        assert abs(first["score"] - second["score"]) <= SCORE_VARIANCE_THRESHOLD
        # With deterministic detectors + identical model behavior: exact match.
        assert first["score"] == second["score"]


# ── 4.13 — a known-good diff produces a positive delta ───────────────────────

class TestImprovementDelta:
    async def test_applied_diff_improves_seeded_benchmark(self, fake_llm):
        seed_agent()
        bm.save_benchmark("default", make_benchmark())

        # Baseline: the agent gives up on both inputs → floor score.
        run = runs_mod.create_run("default", "agent_1", "agent",
                                  baseline_version_n=1, tuner_model="m")
        runs_mod.write_proposal("default", run["run_id"], {
            "insights": {},
            "proposed_diff": {
                "target_object_id": "agent_1", "target_kind": "agent",
                "field_edits": [{"field": "system_prompt",
                                 "new_value": "You are relentless. Never give up."}],
                "rationale": "give_up rate 1.0",
                "evidence_pointers": [],
                "expected_metric_deltas": {"give_up": -1.0},
            },
        })
        fake_llm.script(["I cannot help with that.",
                         "I am unable to complete this."])
        baseline = await bm.run_benchmark(
            "default", "bench_1", server_module=_server(),
            improvement_run_id=run["run_id"], record_as="baseline")
        assert baseline["score"] == 0.0

        # Apply the known-good diff (v2 becomes active).
        applier.apply_run("default", run["run_id"])

        # New version: clean completions on both inputs → ceiling score.
        fake_llm.script(["Summary: fine.", "Risks: a, b, c."])
        new = await bm.run_benchmark(
            "default", "bench_1", server_module=_server(),
            improvement_run_id=run["run_id"], record_as="new")
        assert new["target_version_n"] == 2
        assert new["score"] == 1.0
        assert new["score"] > baseline["score"]  # the positive delta

        # Scores stamped onto the ImprovementRun in runs.json.
        stamped = runs_mod.get_run("default", run["run_id"])
        assert stamped["baseline_score"] == 0.0
        assert stamped["new_score"] == 1.0
        assert stamped["benchmark_id"] == "bench_1"
