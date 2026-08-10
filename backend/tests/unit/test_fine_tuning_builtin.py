"""
Fine-tuning tab regression coverage.

These tests lock the backend contract used by the dedicated Fine-Tuning tab:

- the built-in orchestration template exists and has the expected loop/state
  shape
- launch-time runtime state overrides the step defaults for benchmark and
  ratchet controls
- benchmark selection injected through shared state is honored by the
  BENCHMARK step
"""

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from core.config import DATA_DIR
from core.improve import benchmark as bm
from core.improve.steps import IMPROVE_STEP_EXECUTORS
from core.models_orchestration import OrchestrationRun, StepConfig, StepType


@pytest.fixture(autouse=True)
def _clean_improve_dir():
    improve_dir = os.path.join(DATA_DIR, "improve")
    for root, _dirs, files in os.walk(improve_dir):
        for name in files:
            try:
                os.chmod(os.path.join(root, name), 0o600)
            except OSError:
                pass
    shutil.rmtree(improve_dir, ignore_errors=True)
    yield


def _engine(orch_id: str = "orch_fine_tune_builtin"):
    return type("Engine", (), {
        "orch": type("Orch", (), {"id": orch_id, "timeout_minutes": 60})(),
        "server_module": object(),
    })()


def _run(shared_state=None):
    return OrchestrationRun(
        run_id="run_fine_tune",
        orchestration_id="orch_fine_tune_builtin",
        shared_state=shared_state or {},
        started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )


def _step(step_type, **overrides):
    cfg = {"id": "s1", "name": "Fine-tune step", "type": step_type}
    cfg.update(overrides)
    return StepConfig.model_validate(cfg)


async def _drain(gen):
    return [event async for event in gen]


def _sse_json(text: str):
    events = []
    for chunk in text.split("\n\n"):
        for line in chunk.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def _load_builtin_template():
    path = Path(__file__).resolve().parents[2] / "data" / "orchestrations.json"
    with open(path, encoding="utf-8") as handle:
        records = json.load(handle)
    return next(record for record in records if record["id"] == "orch_fine_tune_builtin")


class TestFineTuningBuiltinTemplate:
    @pytest.mark.parametrize(
        "getter,expected",
        [
            (lambda builtin: builtin["entry_step_id"], "s_bench_base"),
            (lambda builtin: builtin["name"], "Fine-Tuning Built-In (System)"),
            (lambda builtin: builtin["steps"][0]["id"], "s_bench_base"),
            (lambda builtin: builtin["steps"][1]["id"], "s_analyze"),
            (lambda builtin: builtin["steps"][2]["id"], "s_propose"),
            (lambda builtin: builtin["steps"][3]["id"], "s_review"),
            (lambda builtin: builtin["steps"][4]["id"], "s_apply"),
            (lambda builtin: builtin["steps"][8]["id"], "s_end"),
        ],
    )
    def test_builtin_template_core_fields(self, getter, expected):
        builtin = _load_builtin_template()

        assert getter(builtin) == expected

    @pytest.mark.parametrize(
        "index,expected_type",
        [
            (0, "benchmark"),
            (1, "improve_analyze"),
            (2, "improve_propose"),
            (3, "improve_review"),
            (4, "improve_apply"),
            (5, "benchmark"),
            (6, "improve_ratchet_decide"),
            (8, "end"),
        ],
    )
    def test_builtin_template_step_types(self, index, expected_type):
        builtin = _load_builtin_template()
        assert builtin["steps"][index]["type"] == expected_type

    @pytest.mark.parametrize(
        "step_id,field,expected",
        [
            ("s_bench_base", "benchmark_id", None),
            ("s_bench_new", "benchmark_id", None),
            ("s_analyze", "output_key", "insights"),
            ("s_propose", "output_key", "proposed_diff"),
            ("s_review", "output_key", "improve_review"),
            ("s_apply", "output_key", "apply_result"),
            ("s_ratchet", "output_key", "ratchet"),
            ("s_gate", "if_condition", "state.ratchet_stop == True"),
        ],
    )
    def test_builtin_template_step_fields(self, step_id, field, expected):
        builtin = _load_builtin_template()
        step = next(item for item in builtin["steps"] if item["id"] == step_id)
        assert step[field] == expected

    @pytest.mark.parametrize(
        "schema_key,expected",
        [
            ("improve_target_id", None),
            ("improve_target_kind", None),
            ("improve_benchmark_id", None),
            ("improve_mode", "human"),
            ("improve_budget_usd", None),
            ("improve_ratchet_threshold", 0.0),
        ],
    )
    def test_builtin_template_state_defaults(self, schema_key, expected):
        builtin = _load_builtin_template()
        assert builtin["state_schema"][schema_key]["default"] == expected

    @pytest.mark.parametrize(
        "schema_key,expected_type",
        [
            ("improve_target_id", "string"),
            ("improve_target_kind", "string"),
            ("improve_benchmark_id", "string"),
            ("improve_mode", "string"),
            ("improve_budget_usd", "number"),
            ("improve_ratchet_threshold", "number"),
        ],
    )
    def test_builtin_template_state_types(self, schema_key, expected_type):
        builtin = _load_builtin_template()
        assert builtin["state_schema"][schema_key]["type"] == expected_type

    def test_builtin_template_contract(self):
        builtin = _load_builtin_template()
        assert [step["id"] for step in builtin["steps"]] == [
            "s_bench_base",
            "s_analyze",
            "s_propose",
            "s_review",
            "s_apply",
            "s_bench_new",
            "s_ratchet",
            "s_gate",
            "s_end",
        ]
        assert [step["id"] for step in builtin["steps"]] == [
            "s_bench_base",
            "s_analyze",
            "s_propose",
            "s_review",
            "s_apply",
            "s_bench_new",
            "s_ratchet",
            "s_gate",
            "s_end",
        ]
        assert [step["type"] for step in builtin["steps"]] == [
            "benchmark",
            "improve_analyze",
            "improve_propose",
            "improve_review",
            "improve_apply",
            "benchmark",
            "improve_ratchet_decide",
            "if_else",
            "end",
        ]

        steps = {step["id"]: step for step in builtin["steps"]}
        assert steps["s_gate"]["if_false_step_id"] == "s_bench_base"
        assert steps["s_review"]["human_fields"][0]["name"] == "action"
        assert steps["s_review"]["human_fields"][0]["options"] == ["apply", "reject"]

    def test_builtin_template_is_domain_neutral(self):
        builtin = _load_builtin_template()
        assert builtin["description"].startswith("Domain-neutral")
        assert all(step.get("agent_id") is None for step in builtin["steps"])
        assert builtin["steps"][0]["benchmark_record_as"] == "baseline"
        assert builtin["steps"][5]["benchmark_record_as"] == "new"

class TestFineTuningRuntimeOverrides:
    @pytest.mark.parametrize("shared_benchmark_id,step_benchmark_id,expected", [
        ("launch_bench", "step_default_bench", "launch_bench"),
        ("launch_bench_two", None, "launch_bench_two"),
        (None, "step_default_bench", "step_default_bench"),
    ])
    async def test_benchmark_step_uses_shared_state_benchmark_id(self, shared_benchmark_id, step_benchmark_id, expected, monkeypatch):
        captured = {}

        async def fake_run_benchmark(user_id, benchmark_id, **kwargs):
            captured["user_id"] = user_id
            captured["benchmark_id"] = benchmark_id
            captured["kwargs"] = kwargs
            return {
                "score": 0.75,
                "per_metric": {"success": {"rate": 1.0}},
                "trace_count": 1,
                "grading_mode": None,
                "grading_strictness": "strict",
            }

        monkeypatch.setattr(bm, "run_benchmark", fake_run_benchmark)

        step_overrides = {"benchmark_record_as": "baseline", "output_key": "baseline_score"}
        if step_benchmark_id is not None:
            step_overrides["benchmark_id"] = step_benchmark_id
        step = _step(StepType.BENCHMARK, **step_overrides)
        shared_state = {"improve_target_id": "agent_1", "improve_target_kind": "agent", "_ratchet_iteration": 7}
        if shared_benchmark_id is not None:
            shared_state["improve_benchmark_id"] = shared_benchmark_id
        run = _run(shared_state=shared_state)

        events = await _drain(
            IMPROVE_STEP_EXECUTORS["benchmark"].execute(step, run, _engine())
        )

        assert captured["benchmark_id"] == expected
        assert captured["kwargs"]["iteration"] == 7
        assert run.shared_state["baseline_score"] == 0.75
        assert run.shared_state["baseline_score_detail"]["grading_strictness"] == "strict"
        assert any(event["type"] == "benchmark_result" and event["benchmark_id"] == expected for event in events)

    @pytest.mark.parametrize("runtime_threshold,step_threshold,baseline,new,expected", [
        (0.1, 0.9, 0.2, 0.35, "keep"),
        (0.5, 0.9, 0.2, 0.35, "revert"),
        (None, 0.0, 0.2, 0.35, "keep"),
    ])
    async def test_ratchet_uses_runtime_threshold_override(self, runtime_threshold, step_threshold, baseline, new, expected):
        step = _step(
            StepType.IMPROVE_RATCHET_DECIDE,
            ratchet_threshold=step_threshold,
            ratchet_max_iterations=99,
            ratchet_plateau_patience=99,
        )
        shared_state = {"baseline_score": baseline, "new_score": new}
        if runtime_threshold is not None:
            shared_state["improve_ratchet_threshold"] = runtime_threshold
        run = _run(shared_state=shared_state)

        events = await _drain(
            IMPROVE_STEP_EXECUTORS["improve_ratchet_decide"].execute(step, run, _engine())
        )
        event = next(event for event in events if event["type"] == "ratchet_decision"
                     )

        expected_threshold = runtime_threshold if runtime_threshold is not None else step_threshold
        assert event["threshold"] == expected_threshold
        assert event["decision"] == expected
        assert run.shared_state["ratchet_decision"] == expected

    @pytest.mark.parametrize("runtime_iteration,step_iteration,expected_iteration,expected_stop", [
        (3, 99, 3, "max_iterations"),
    ])
    async def test_ratchet_uses_runtime_iteration_override(self, runtime_iteration, step_iteration, expected_iteration, expected_stop):
        step = _step(
            StepType.IMPROVE_RATCHET_DECIDE,
            ratchet_threshold=0.0,
            ratchet_max_iterations=step_iteration,
            ratchet_plateau_patience=99,
        )

        max_iter_run = _run(
            shared_state={
                "improve_ratchet_max_iterations": runtime_iteration,
                "_ratchet_iteration": runtime_iteration - 1,
                "baseline_score": 0.9,
                "new_score": 0.8,
            }
        )
        max_iter_event = next(
            event for event in await _drain(
                IMPROVE_STEP_EXECUTORS["improve_ratchet_decide"].execute(
                    step, max_iter_run, _engine()
                )
            )
            if event["type"] == "ratchet_decision"
        )
        assert max_iter_event["iteration"] == expected_iteration
        assert max_iter_event["stop"] is True
        assert max_iter_event["stop_reason"] == expected_stop

    @pytest.mark.parametrize("runtime_plateau,step_plateau,consecutive,expected_stop", [
        (2, 99, 1, "plateau"),
        (3, 99, 2, "plateau"),
    ])
    async def test_ratchet_uses_runtime_plateau_override(self, runtime_plateau, step_plateau, consecutive, expected_stop):
        step = _step(
            StepType.IMPROVE_RATCHET_DECIDE,
            ratchet_threshold=0.0,
            ratchet_max_iterations=99,
            ratchet_plateau_patience=step_plateau,
        )
        run = _run(
            shared_state={
                "improve_ratchet_plateau_patience": runtime_plateau,
                "_ratchet_consecutive_reverts": consecutive,
                "baseline_score": 0.9,
                "new_score": 0.1,
            }
        )
        event = next(
            event for event in await _drain(
                IMPROVE_STEP_EXECUTORS["improve_ratchet_decide"].execute(step, run, _engine())
            )
            if event["type"] == "ratchet_decision"
        )
        assert event["stop"] is True
        assert event["stop_reason"] == expected_stop


class TestFineTuningLaunchInjection:
    @pytest.mark.parametrize(
        "message,initial_state,expected_target_kind,expected_target_id,expected_benchmark",
        [
            ("fine tune", {"improve_target_kind": "agent", "improve_target_id": "agent_1", "improve_benchmark_id": "bench_a"}, "agent", "agent_1", "bench_a"),
            ("run", {"improve_target_kind": "orchestration", "improve_target_id": "orch_1", "improve_benchmark_id": "bench_b"}, "orchestration", "orch_1", "bench_b"),
            ("go", {"improve_target_kind": "agent", "improve_target_id": "agent_2", "improve_benchmark_id": "bench_c"}, "agent", "agent_2", "bench_c"),
        ],
    )
    async def test_initial_state_is_forwarded_through_orchestration_run(self, client, seed_orchestration, monkeypatch, message, initial_state, expected_target_kind, expected_target_id, expected_benchmark):
        orch = seed_orchestration()
        captured: dict = {}

        from _fakes import engine_events as E

        class _CaptureEngine:
            def __init__(self, orch=None, server_module=None):
                self.orch = orch

            async def run(self, user_input, run_id, **kwargs):
                captured["user_input"] = user_input
                captured["run_id"] = run_id
                captured["initial_state"] = kwargs.get("initial_state")
                yield E.orch_start(orch_id=orch["id"])
                yield E.orch_complete(status_str="completed")

        import core.orchestration.engine as engine_mod
        monkeypatch.setattr(engine_mod, "OrchestrationEngine", _CaptureEngine)

        resp = await client.post(
            f"/api/orchestrations/{orch['id']}/run",
            json={"message": message, "initial_state": initial_state},
        )
        assert resp.status_code == 200
        assert captured["user_input"] == message
        assert captured["initial_state"]["improve_target_kind"] == expected_target_kind
        assert captured["initial_state"]["improve_target_id"] == expected_target_id
        assert captured["initial_state"]["improve_benchmark_id"] == expected_benchmark


class TestFineTuningTemplateEdgeMatrix:
    @pytest.mark.parametrize(
        "step_id,field,expected",
        [
            ("s_bench_base", "next_step_id", "s_analyze"),
            ("s_analyze", "next_step_id", "s_propose"),
            ("s_propose", "next_step_id", "s_review"),
            ("s_review", "next_step_id", "s_apply"),
            ("s_apply", "next_step_id", "s_bench_new"),
            ("s_bench_new", "next_step_id", "s_ratchet"),
            ("s_ratchet", "next_step_id", "s_gate"),
            ("s_gate", "if_true_step_id", "s_end"),
            ("s_gate", "if_false_step_id", "s_bench_base"),
            ("s_review", "human_prompt", "Review the proposed improvement and choose apply or reject."),
        ],
    )
    def test_template_edges_and_prompts(self, step_id, field, expected):
        builtin = _load_builtin_template()
        step = next(item for item in builtin["steps"] if item["id"] == step_id)
        assert step[field] == expected


class TestFineTuningLaunchStateMatrix:
    @pytest.mark.parametrize(
        "message,initial_state,expected_state,extra_events",
        [
            (
                "fine tune",
                {"improve_target_kind": "agent", "improve_target_id": "agent_1", "improve_benchmark_id": "bench_a"},
                {"improve_target_kind": "agent", "improve_target_id": "agent_1", "improve_benchmark_id": "bench_a"},
                ["step_start", "benchmark_result"],
            ),
            (
                "run",
                {"improve_target_kind": "orchestration", "improve_target_id": "orch_1", "improve_benchmark_id": "bench_b", "improve_mode": "autonomous"},
                {"improve_target_kind": "orchestration", "improve_target_id": "orch_1", "improve_benchmark_id": "bench_b", "improve_mode": "autonomous"},
                [],
            ),
            (
                "go",
                {"improve_target_kind": "agent", "improve_target_id": "agent_2", "improve_benchmark_id": "bench_c", "improve_budget_usd": 2.5},
                {"improve_target_kind": "agent", "improve_target_id": "agent_2", "improve_benchmark_id": "bench_c", "improve_budget_usd": 2.5},
                [],
            ),
            (
                "start",
                {"improve_target_kind": "orchestration", "improve_target_id": "orch_2", "improve_benchmark_id": "bench_d", "improve_ratchet_threshold": 0.25},
                {"improve_target_kind": "orchestration", "improve_target_id": "orch_2", "improve_benchmark_id": "bench_d", "improve_ratchet_threshold": 0.25},
                [],
            ),
            (
                "launch",
                {"improve_target_kind": "agent", "improve_target_id": "agent_3", "improve_benchmark_id": "bench_e", "improve_ratchet_max_iterations": 9},
                {"improve_target_kind": "agent", "improve_target_id": "agent_3", "improve_benchmark_id": "bench_e", "improve_ratchet_max_iterations": 9},
                [],
            ),
            (
                "begin",
                {"improve_target_kind": "orchestration", "improve_target_id": "orch_3", "improve_benchmark_id": "bench_f", "improve_ratchet_plateau_patience": 7},
                {"improve_target_kind": "orchestration", "improve_target_id": "orch_3", "improve_benchmark_id": "bench_f", "improve_ratchet_plateau_patience": 7},
                [],
            ),
            (
                "execute",
                {"improve_target_kind": "agent", "improve_target_id": "agent_4", "improve_benchmark_id": "bench_g", "improve_mode": "human", "improve_budget_usd": None},
                {"improve_target_kind": "agent", "improve_target_id": "agent_4", "improve_benchmark_id": "bench_g", "improve_mode": "human", "improve_budget_usd": None},
                [],
            ),
            (
                "submit",
                {"improve_target_kind": "orchestration", "improve_target_id": "orch_4", "improve_benchmark_id": "bench_h", "improve_mode": "autonomous", "improve_budget_usd": 0.0},
                {"improve_target_kind": "orchestration", "improve_target_id": "orch_4", "improve_benchmark_id": "bench_h", "improve_mode": "autonomous", "improve_budget_usd": 0.0},
                [],
            ),
            (
                "process",
                {"improve_target_kind": "agent", "improve_target_id": "agent_5", "improve_benchmark_id": "bench_i", "improve_ratchet_threshold": 0.5, "improve_ratchet_max_iterations": 4},
                {"improve_target_kind": "agent", "improve_target_id": "agent_5", "improve_benchmark_id": "bench_i", "improve_ratchet_threshold": 0.5, "improve_ratchet_max_iterations": 4},
                [],
            ),
            (
                "sample-pipeline",
                {"improve_target_kind": "orchestration", "improve_target_id": "orch_5", "improve_benchmark_id": "bench_j", "improve_ratchet_threshold": 0.1, "improve_ratchet_max_iterations": 3, "improve_ratchet_plateau_patience": 2},
                {"improve_target_kind": "orchestration", "improve_target_id": "orch_5", "improve_benchmark_id": "bench_j", "improve_ratchet_threshold": 0.1, "improve_ratchet_max_iterations": 3, "improve_ratchet_plateau_patience": 2},
                ["step_start", "benchmark_result"],
            ),
        ],
    )
    async def test_initial_state_is_forwarded_through_orchestration_run(
        self,
        client,
        seed_orchestration,
        monkeypatch,
        message,
        initial_state,
        expected_state,
        extra_events,
    ):
        orch = seed_orchestration()
        captured: dict = {}

        from _fakes import engine_events as E

        class _CaptureEngine:
            def __init__(self, orch=None, server_module=None):
                self.orch = orch

            async def run(self, user_input, run_id, **kwargs):
                captured["user_input"] = user_input
                captured["run_id"] = run_id
                captured["initial_state"] = kwargs.get("initial_state")
                yield E.orch_start(orch_id=orch["id"])
                if "step_start" in extra_events:
                    yield E.step_start("s_bench_base", "Benchmark Baseline")
                if "benchmark_result" in extra_events:
                    yield {
                        "type": "benchmark_result",
                        "benchmark_id": initial_state["improve_benchmark_id"],
                        "score": 1.0,
                        "recorded_as": "baseline",
                    }
                yield E.orch_complete(status_str="completed")

        import core.orchestration.engine as engine_mod
        monkeypatch.setattr(engine_mod, "OrchestrationEngine", _CaptureEngine)

        resp = await client.post(
            f"/api/orchestrations/{orch['id']}/run",
            json={"message": message, "initial_state": initial_state},
        )
        assert resp.status_code == 200
        assert captured["user_input"] == message
        assert captured["initial_state"] == expected_state
        types = [event["type"] for event in _sse_json(resp.text)]
        assert types[-1] == "done"


class TestFineTuningBenchmarkDataFlowMatrix:
    @pytest.mark.parametrize(
        "shared_target,step_target,shared_kind,step_kind,shared_benchmark,step_benchmark,expected_target,expected_benchmark",
        [
            ("agent_1", None, "agent", None, "bench_a", None, "agent_1", "bench_a"),
            ("orch_1", None, "orchestration", None, "bench_b", None, "orch_1", "bench_b"),
            ("agent_2", "agent_step", "agent", "orchestration", "bench_c", None, "agent_step", "bench_c"),
            ("orch_2", "orch_step", "orchestration", "agent", "bench_d", None, "orch_step", "bench_d"),
            (None, "agent_step", None, "agent", "bench_e", None, "agent_step", "bench_e"),
            (None, "orch_step", None, "orchestration", "bench_f", None, "orch_step", "bench_f"),
            ("agent_3", None, "agent", None, None, "step_bench", "agent_3", "step_bench"),
            ("orch_3", None, "orchestration", None, None, "step_bench", "orch_3", "step_bench"),
            ("agent_4", "agent_step", "agent", "agent", "bench_g", "step_bench", "agent_step", "bench_g"),
            (None, None, None, None, "bench_h", None, None, "bench_h"),
        ],
    )
    async def test_benchmark_step_target_and_suite_resolution(
        self,
        monkeypatch,
        shared_target,
        step_target,
        shared_kind,
        step_kind,
        shared_benchmark,
        step_benchmark,
        expected_target,
        expected_benchmark,
    ):
        captured = {}

        async def fake_run_benchmark(user_id, benchmark_id, **kwargs):
            captured["user_id"] = user_id
            captured["benchmark_id"] = benchmark_id
            captured["kwargs"] = kwargs
            return {
                "score": 0.5,
                "per_metric": {"success": {"rate": 1.0}},
                "trace_count": 1,
                "grading_mode": None,
                "grading_strictness": "strict",
            }

        monkeypatch.setattr(bm, "run_benchmark", fake_run_benchmark)

        step_overrides = {"benchmark_record_as": "baseline", "output_key": "baseline_score"}
        if step_target is not None:
            step_overrides["improve_target_id"] = step_target
        if step_kind is not None:
            step_overrides["improve_target_kind"] = step_kind
        if step_benchmark is not None:
            step_overrides["benchmark_id"] = step_benchmark
        step = _step(StepType.BENCHMARK, **step_overrides)

        shared_state = {}
        if shared_target is not None:
            shared_state["improve_target_id"] = shared_target
        if shared_kind is not None:
            shared_state["improve_target_kind"] = shared_kind
        if shared_benchmark is not None:
            shared_state["improve_benchmark_id"] = shared_benchmark
        run = _run(shared_state=shared_state)

        events = await _drain(
            IMPROVE_STEP_EXECUTORS["benchmark"].execute(step, run, _engine())
        )

        prompt = next(event["prompt"] for event in events if event["type"] == "_log_prompt")
        assert expected_target in prompt if expected_target is not None else "(suite default)" in prompt
        assert expected_benchmark == captured["benchmark_id"]
        assert captured["kwargs"]["target_object_id"] == expected_target
        assert run.shared_state["baseline_score"] == 0.5
        assert run.shared_state["baseline_score_detail"]["grading_mode"] is None
        assert captured["kwargs"]["budget_usd"] is None
        if expected_target is None:
            assert "(suite default)" in prompt


class TestFineTuningRatchetDataFlowMatrix:
    @pytest.mark.parametrize(
        "shared_state,step_overrides,expected_decision,expected_stop",
        [
            ({"baseline_score": 0.2, "new_score": 0.4}, {"ratchet_threshold": 0.1}, "keep", None),
            ({"baseline_score": 0.2, "new_score": 0.2}, {"ratchet_threshold": 0.0}, "keep", None),
            ({"baseline_score": 0.5, "new_score": 0.2}, {"ratchet_threshold": 0.1}, "revert", None),
            ({}, {"ratchet_threshold": 0.1}, "revert", None),
            ({"baseline_score_detail": {"grading_mode": "deterministic"}, "new_score_detail": {"grading_mode": "rubric"}}, {"ratchet_threshold": 0.1}, "revert", None),
            ({"baseline_score_detail": {"rubric_content_hash": "a"}, "new_score_detail": {"rubric_content_hash": "b"}}, {"ratchet_threshold": 0.1}, "revert", None),
            ({"baseline_score_detail": {"extraction_failed_rate": 0.9}, "new_score_detail": {"extraction_failed_rate": 0.0}}, {"ratchet_threshold": 0.1}, "revert", None),
            ({"baseline_score": 0.1, "new_score": 0.5, "_ratchet_iteration": 4}, {"ratchet_max_iterations": 5}, "keep", "max_iterations"),
            ({"baseline_score": 0.1, "new_score": 0.5, "_ratchet_consecutive_reverts": 1}, {"ratchet_plateau_patience": 2}, "keep", None),
            ({"baseline_score": 0.1, "new_score": 0.5}, {"improve_budget_usd": 0.0}, "keep", "budget"),
        ],
    )
    async def test_ratchet_decision_and_stop_matrices(
        self,
        shared_state,
        step_overrides,
        expected_decision,
        expected_stop,
    ):
        step = _step(
            StepType.IMPROVE_RATCHET_DECIDE,
            ratchet_threshold=step_overrides.get("ratchet_threshold", 0.0),
            ratchet_max_iterations=step_overrides.get("ratchet_max_iterations", 99),
            ratchet_plateau_patience=step_overrides.get("ratchet_plateau_patience", 99),
            improve_budget_usd=step_overrides.get("improve_budget_usd"),
        )
        run = _run(shared_state=dict(shared_state))

        event = next(
            event for event in await _drain(
                IMPROVE_STEP_EXECUTORS["improve_ratchet_decide"].execute(step, run, _engine())
            )
            if event["type"] == "ratchet_decision"
        )
        assert event["decision"] == expected_decision
        assert event["stop_reason"] == expected_stop
        assert run.shared_state["ratchet_decision"] == expected_decision


class TestFineTuningStateNormalizationMatrix:
    @pytest.mark.parametrize(
        "initial_state,expected_state,expect_sample_events",
        [
            (None, None, False),
            ({}, None, False),
            ({"improve_target_kind": "agent", "improve_target_id": "agent_1"}, {"improve_target_kind": "agent", "improve_target_id": "agent_1"}, False),
            ({"improve_target_kind": "orchestration", "improve_target_id": "orch_1"}, {"improve_target_kind": "orchestration", "improve_target_id": "orch_1"}, False),
            ({"improve_benchmark_id": "bench_a"}, {"improve_benchmark_id": "bench_a"}, False),
            ({"improve_mode": "autonomous"}, {"improve_mode": "autonomous"}, False),
            ({"improve_budget_usd": 2.5}, {"improve_budget_usd": 2.5}, False),
            ({"improve_ratchet_threshold": 0.1}, {"improve_ratchet_threshold": 0.1}, False),
            ({"improve_ratchet_max_iterations": 3, "improve_ratchet_plateau_patience": 2}, {"improve_ratchet_max_iterations": 3, "improve_ratchet_plateau_patience": 2}, False),
            ({"improve_target_kind": "orchestration", "improve_target_id": "orch_smoke", "improve_benchmark_id": "bench_smoke", "improve_mode": "autonomous", "improve_budget_usd": 1.5, "improve_ratchet_threshold": 0.2}, {"improve_target_kind": "orchestration", "improve_target_id": "orch_smoke", "improve_benchmark_id": "bench_smoke", "improve_mode": "autonomous", "improve_budget_usd": 1.5, "improve_ratchet_threshold": 0.2}, True),
        ],
    )
    async def test_initial_state_normalization_and_smoke_run(
        self,
        client,
        seed_orchestration,
        monkeypatch,
        initial_state,
        expected_state,
        expect_sample_events,
    ):
        orch = seed_orchestration()
        captured: dict = {}

        from _fakes import engine_events as E

        class _SmokeEngine:
            def __init__(self, orch=None, server_module=None):
                self.orch = orch

            async def run(self, user_input, run_id, **kwargs):
                captured["user_input"] = user_input
                captured["initial_state"] = kwargs.get("initial_state")
                yield E.orch_start(orch_id=orch["id"])
                if expect_sample_events:
                    yield E.step_start("s_bench_base", "Benchmark Baseline")
                    yield {
                        "type": "benchmark_result",
                        "benchmark_id": initial_state["improve_benchmark_id"],
                        "score": 1.0,
                        "recorded_as": "baseline",
                    }
                yield E.orch_complete(status_str="completed")

        import core.orchestration.engine as engine_mod
        monkeypatch.setattr(engine_mod, "OrchestrationEngine", _SmokeEngine)

        body = {"message": "fine tune smoke"}
        if initial_state is not None:
            body["initial_state"] = initial_state

        resp = await client.post(f"/api/orchestrations/{orch['id']}/run", json=body)
        assert resp.status_code == 200
        assert captured["user_input"] == "fine tune smoke"
        assert captured["initial_state"] == expected_state
        types = [event["type"] for event in _sse_json(resp.text)]
        assert types[-1] == "done"

