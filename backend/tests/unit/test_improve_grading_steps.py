"""
Checkpoint-6 verification (unit), chunk 1 — the pieces that live in the
orchestration/benchmark execution path rather than in `grading.py` itself:

- 6.1/6.3  the two-axis composite as produced by a real `run_benchmark`
- 6.2      the grading-mode toggle end to end
- 6.4      a CP4 benchmark still runs and scores exactly as before
- 6.15     `IMPROVE_RATCHET_DECIDE` refuses to compare across differing
           `rubric_content_hash` or `grading_mode` and emits a
           `grading_mismatch` inbox entry
"""
import json
import os
import shutil
import time
import types

import pytest

from core.improve import benchmark as bm, inbox as inbox_mod, runs as runs_mod
from core.improve.steps import (
    IMPROVE_STEP_EXECUTORS,
    comparability_reason,
    grading_detail,
)
from core.models_orchestration import OrchestrationRun, StepConfig, StepType
from _fakes import seed

USER = "default"


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


# ── helpers (mirrors test_improve_steps.py) ──────────────────────────────────

def _server():
    return types.SimpleNamespace(agent_sessions={}, memory_store=None, tool_router={})


def _engine(orch_id="orch_driver", timeout_minutes=60):
    return types.SimpleNamespace(
        orch=types.SimpleNamespace(id=orch_id, timeout_minutes=timeout_minutes),
        server_module=_server(),
    )


def _run(shared_state=None):
    return OrchestrationRun(
        run_id="run_cp6",
        orchestration_id="orch_driver",
        shared_state=shared_state or {},
        started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )


def _step(step_type, **overrides):
    cfg = {"id": "s1", "name": "CP6 step", "type": step_type}
    cfg.update(overrides)
    return StepConfig.model_validate(cfg)


async def _drain(gen):
    return [e async for e in gen]


def seed_agent(**overrides):
    agent = seed.make_agent(id="agent_1", tools=[], skip_default_tools=True,
                            **overrides)
    seed.seed_agents([agent])
    return agent


async def _decide(step, run):
    events = await _drain(
        IMPROVE_STEP_EXECUTORS["improve_ratchet_decide"].execute(
            step, run, _engine())
    )
    return next(e for e in events if e["type"] == "ratchet_decision")


# ── 6.1 / 6.2 / 6.3 — the outcome axis through a real benchmark run ──────────

V2_SUITE = {
    "id": "bench_v2", "name": "Outcome suite", "target_object_id": "agent_1",
    "schema_version": 2, "grading_mode": "deterministic",
    "scorer": {"metrics": {"success": 1.0},
               "process_weight": 1.0, "outcome_weight": 1.0},
    "inputs": [
        {"id": "in_001", "prompt": "Say the magic word.", "split": "train",
         "weight": 1.0,
         "expected": {"checks": [
             {"id": "answer", "weight": 1.0,
              "extract": {"from": "final_output"},
              "compare": {"type": "contains_all", "value": ["APAC"]}},
         ]}},
    ],
}


class TestOutcomeAxisEndToEnd:
    async def test_v2_record_carries_both_axes_and_the_composite(self, fake_llm):
        seed_agent()
        fake_llm.set_default("APAC led Q3 revenue.")
        bm.save_benchmark(USER, json.loads(json.dumps(V2_SUITE)))

        result = await bm.run_benchmark(USER, "bench_v2", server_module=_server())

        assert result["process_score"] == 1.0
        assert result["outcome_score"] == 1.0
        assert result["composite_score"] == 1.0
        assert result["score"] == result["composite_score"]
        assert result["grading_mode"] == "deterministic"
        assert result["grading_strictness"] == "strict"
        assert result["outcome_na"] is False
        assert result["extraction_failed_rate"] == 0.0
        assert result["per_input"][0]["input_id"] == "in_001"
        assert result["per_input"][0]["checks"][0]["status"] == "pass"

    async def test_wrong_answer_lowers_only_the_outcome_axis(self, fake_llm):
        seed_agent()
        fake_llm.set_default("EMEA led Q3 revenue.")
        bm.save_benchmark(USER, json.loads(json.dumps(V2_SUITE)))

        result = await bm.run_benchmark(USER, "bench_v2", server_module=_server())

        assert result["process_score"] == 1.0     # the agent behaved fine
        assert result["outcome_score"] == 0.0     # it was simply wrong
        assert result["composite_score"] == 0.5   # normalized 1:1 blend

    async def test_evidence_pointers_link_to_the_trace(self, fake_llm):
        """§3.17 evidence-first rule applies unchanged to CP6 check rows."""
        seed_agent()
        fake_llm.set_default("APAC led Q3 revenue.")
        bm.save_benchmark(USER, json.loads(json.dumps(V2_SUITE)))
        result = await bm.run_benchmark(USER, "bench_v2", server_module=_server())
        check = result["per_input"][0]["checks"][0]
        assert check["trace_file"] in result["trace_files"]

    async def test_outcome_weight_zero_reproduces_the_process_score(self, fake_llm):
        seed_agent()
        fake_llm.set_default("EMEA led Q3 revenue.")
        suite = json.loads(json.dumps(V2_SUITE))
        suite["scorer"]["outcome_weight"] = 0.0
        bm.save_benchmark(USER, suite)

        result = await bm.run_benchmark(USER, "bench_v2", server_module=_server())
        assert result["outcome_score"] == 0.0
        assert result["composite_score"] == result["process_score"]

    async def test_no_trace_for_an_input_is_na_not_zero(self, fake_llm):
        """§6.1 — never silently substitute 0; that is indistinguishable from
        'the agent got everything wrong' and triggers a spurious revert."""
        seed_agent()
        fake_llm.set_default("APAC led Q3 revenue.")
        suite = json.loads(json.dumps(V2_SUITE))
        # An input whose extractor can never match: all checks N/A -> input N/A.
        suite["inputs"][0]["expected"]["checks"] = [{
            "id": "sql", "weight": 1.0,
            "extract": {"from": "tool_call_arg", "tool": "sql_agent",
                        "arg": "query"},
            "compare": {"type": "exact", "value": "SELECT 1"},
        }]
        bm.save_benchmark(USER, suite)

        result = await bm.run_benchmark(USER, "bench_v2", server_module=_server())
        assert result["outcome_score"] is None
        assert result["outcome_na"] is True
        assert result["composite_score"] == result["process_score"]
        assert result["extraction_failed_count"] == 1

    async def test_per_input_grading_mode_override_is_honoured(self, fake_llm):
        seed_agent()
        fake_llm.set_default("APAC led Q3 revenue.")
        suite = json.loads(json.dumps(V2_SUITE))
        suite["grading_mode"] = None
        suite["inputs"][0]["grading_mode"] = "deterministic"
        bm.save_benchmark(USER, suite)

        result = await bm.run_benchmark(USER, "bench_v2", server_module=_server())
        assert result["outcome_score"] == 1.0


# ── 6.4 — a CP4 benchmark still runs and scores exactly as before ────────────

CP4_SUITE = {
    "id": "bench_cp4", "name": "Legacy", "target_object_id": "agent_1",
    "inputs": [{"prompt": "Say hi."}],
    "scorer": {"metrics": {"success": 1.0}},
}


class TestCp4RunBackCompat:
    async def test_cp4_run_produces_the_cp4_record_shape_and_score(self, fake_llm):
        seed_agent()
        fake_llm.set_default("All done.")
        bm.save_benchmark(USER, json.loads(json.dumps(CP4_SUITE)))

        result = await bm.run_benchmark(USER, "bench_cp4", server_module=_server())

        assert result["score"] == 1.0
        for key in ("process_score", "outcome_score", "composite_score",
                    "grading_mode", "per_input", "extraction_failed_rate",
                    "schema_version"):
            assert key not in result, f"CP6 key '{key}' leaked into a CP4 record"

    async def test_cp4_score_is_stable_across_two_runs(self, fake_llm):
        seed_agent()
        fake_llm.set_default("All done.")
        bm.save_benchmark(USER, json.loads(json.dumps(CP4_SUITE)))
        first = await bm.run_benchmark(USER, "bench_cp4", server_module=_server())
        second = await bm.run_benchmark(USER, "bench_cp4", server_module=_server())
        assert first["score"] == second["score"]
        assert abs(first["score"] - second["score"]) <= bm.SCORE_VARIANCE_THRESHOLD


# ── 6.15 — the ratchet's comparability gate ─────────────────────────────────

class TestComparabilityHelpers:
    def test_identical_rulers_are_comparable(self):
        detail = {"grading_mode": "rubric", "rubric_content_hash": "sha256:a"}
        assert comparability_reason(detail, dict(detail)) is None

    def test_differing_rubric_content_hash_is_incomparable(self):
        reason = comparability_reason(
            {"grading_mode": "rubric", "rubric_content_hash": "sha256:a"},
            {"grading_mode": "rubric", "rubric_content_hash": "sha256:b"},
        )
        assert reason and "content_hash" in reason

    def test_differing_grading_mode_is_incomparable(self):
        reason = comparability_reason(
            {"grading_mode": "deterministic"}, {"grading_mode": "rubric"})
        assert reason and "grading_mode" in reason

    def test_cp4_records_have_no_grading_metadata_and_stay_comparable(self):
        assert comparability_reason(grading_detail({}), grading_detail({})) is None

    def test_grading_detail_is_all_optional(self):
        assert set(grading_detail({}).values()) == {None}


RULER_KEYS = ("grading_mode", "grading_strictness", "rubric_id",
              "rubric_version", "rubric_content_hash", "outcome_score",
              "composite_score", "outcome_na", "extraction_failed_rate",
              "snapshot_id", "process_score")


class TestRatchetComparabilityGate:
    async def test_rubric_edited_mid_ratchet_forces_revert(self):
        """6.15 — silently comparing across rubric versions is the subtlest way
        this subsystem can lie to you."""
        seed_agent()
        run = _run(shared_state={
            "improve_target_id": "agent_1",
            "baseline_score": 0.5, "new_score": 0.9,   # would otherwise KEEP
            "baseline_score_detail": {"grading_mode": "rubric",
                                      "rubric_content_hash": "sha256:a"},
            "new_score_detail": {"grading_mode": "rubric",
                                 "rubric_content_hash": "sha256:b"},
        })
        ev = await _decide(_step(StepType.IMPROVE_RATCHET_DECIDE), run)

        assert ev["decision"] == "revert"
        assert ev["delta"] is None
        assert "content_hash" in ev["incomparable_reason"]
        assert run.shared_state["ratchet_incomparable_reason"]

    async def test_grading_mode_change_forces_revert(self):
        seed_agent()
        run = _run(shared_state={
            "improve_target_id": "agent_1",
            "baseline_score": 0.1, "new_score": 0.99,
            "baseline_score_detail": {"grading_mode": None},
            "new_score_detail": {"grading_mode": "deterministic"},
        })
        ev = await _decide(_step(StepType.IMPROVE_RATCHET_DECIDE), run)
        assert ev["decision"] == "revert"
        assert "grading_mode" in ev["incomparable_reason"]

    async def test_mismatch_emits_a_grading_mismatch_inbox_entry(self):
        seed_agent()
        run = _run(shared_state={
            "improve_target_id": "agent_1", "improve_mode": "autonomous",
            "baseline_score": 0.5, "new_score": 0.9,
            "baseline_score_detail": {"grading_mode": "rubric",
                                      "rubric_content_hash": "sha256:a"},
            "new_score_detail": {"grading_mode": "rubric",
                                 "rubric_content_hash": "sha256:b"},
        })
        await _decide(_step(StepType.IMPROVE_RATCHET_DECIDE), run)

        entries = inbox_mod.list_entries(USER, kind="grading_mismatch")
        assert len(entries) == 1
        assert entries[0]["object_id"] == "agent_1"
        assert "not comparable" in entries[0]["message"]

    async def test_matching_rulers_still_keep(self):
        seed_agent()
        detail = {"grading_mode": "deterministic",
                  "rubric_content_hash": None}
        run = _run(shared_state={
            "improve_target_id": "agent_1",
            "baseline_score": 0.5, "new_score": 0.9,
            "baseline_score_detail": detail, "new_score_detail": dict(detail),
        })
        ev = await _decide(_step(StepType.IMPROVE_RATCHET_DECIDE), run)
        assert ev["decision"] == "keep"
        assert ev["incomparable_reason"] is None
        assert inbox_mod.list_entries(USER, kind="grading_mismatch") == []

    async def test_cp5_behaviour_unchanged_when_no_detail_is_present(self):
        """The CP5 path must be untouched: no detail keys -> same decision."""
        seed_agent()
        run = _run(shared_state={"improve_target_id": "agent_1",
                                 "baseline_score": 0.5, "new_score": 0.9})
        ev = await _decide(_step(StepType.IMPROVE_RATCHET_DECIDE), run)
        assert ev["decision"] == "keep" and ev["delta"] == 0.4


# ── the BENCHMARK executor's richer shared-state object ─────────────────────

class TestBenchmarkStepDetail:
    async def test_step_writes_the_richer_score_object(self, fake_llm):
        seed_agent()
        fake_llm.set_default("APAC led Q3 revenue.")
        bm.save_benchmark(USER, json.loads(json.dumps(V2_SUITE)))
        step = _step(StepType.BENCHMARK, benchmark_id="bench_v2",
                     benchmark_record_as="baseline")
        run = _run(shared_state={"improve_target_id": "agent_1"})

        events = await _drain(
            IMPROVE_STEP_EXECUTORS["benchmark"].execute(step, run, _engine()))
        ev = next(e for e in events if e["type"] == "benchmark_result")

        detail = run.shared_state["baseline_score_detail"]
        assert detail["grading_mode"] == "deterministic"
        assert detail["outcome_score"] == 1.0
        assert detail["composite_score"] == run.shared_state["baseline_score"]
        assert ev["outcome_score"] == 1.0

    async def test_cp4_step_still_writes_the_bare_float(self, fake_llm):
        seed_agent()
        fake_llm.set_default("All done.")
        bm.save_benchmark(USER, json.loads(json.dumps(CP4_SUITE)))
        step = _step(StepType.BENCHMARK, benchmark_id="bench_cp4",
                     benchmark_record_as="baseline")
        run = _run(shared_state={"improve_target_id": "agent_1"})

        await _drain(IMPROVE_STEP_EXECUTORS["benchmark"].execute(
            step, run, _engine()))

        assert run.shared_state["baseline_score"] == 1.0
        detail = run.shared_state["baseline_score_detail"]
        # Every grading field is absent for a CP4 suite; only the back-pointer
        # to the benchmark result record is populated.
        assert all(detail[k] is None for k in RULER_KEYS)
        assert detail["benchmark_run_id"].startswith("bench_")
