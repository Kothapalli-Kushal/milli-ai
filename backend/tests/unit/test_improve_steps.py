"""
Checkpoint-5 verification (unit): the six self-improvement step executors
(5.1–5.7), inbox audit entries for autonomous applies/reverts (5.9–5.11),
the autonomous safeguards — iteration cap, plateau patience, wallclock,
budget, self-edit lockout (5.12–5.17) — bulk revert of autonomous edits
(5.18/5.19), and human-gated pause/resume plus clean autonomous
termination through the REAL OrchestrationEngine (5.20/5.22).
"""
import json
import os
import shutil
import time
import types
from datetime import datetime, timedelta, timezone

import pytest

from core.improve import applier, benchmark as bm, inbox as inbox_mod, runs as runs_mod
from core.improve.steps import IMPROVE_STEP_EXECUTORS
from core.models_orchestration import OrchestrationRun, StepConfig, StepType
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


# ── helpers ──────────────────────────────────────────────────────────────────

def _server():
    return types.SimpleNamespace(agent_sessions={}, memory_store=None, tool_router={})


def _engine(orch_id="orch_driver", timeout_minutes=60):
    """Minimal engine stand-in for direct executor calls."""
    return types.SimpleNamespace(
        orch=types.SimpleNamespace(id=orch_id, timeout_minutes=timeout_minutes),
        server_module=_server(),
    )


def _run(shared_state=None, orchestration_id="orch_driver", started_at=None):
    return OrchestrationRun(
        run_id="run_cp5",
        orchestration_id=orchestration_id,
        shared_state=shared_state or {},
        started_at=started_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )


def _step(step_type, **overrides):
    cfg = {"id": "s1", "name": "CP5 step", "type": step_type}
    cfg.update(overrides)
    return StepConfig.model_validate(cfg)


async def _drain(gen):
    return [e async for e in gen]


def make_diff(**overrides):
    diff = {
        "target_object_id": "agent_1",
        "target_kind": "agent",
        "field_edits": [
            {
                "field": "system_prompt",
                "old_value": "You are a helpful test assistant.",
                "new_value": "You are a persistent assistant. Never give up.",
                "rationale": "give_up detector fired",
            }
        ],
        "rationale": "Traces show premature give-ups.",
        "evidence_pointers": [{"trace_file": "agent_1/2026-01/s.json", "message_idx": 2}],
        "expected_metric_deltas": {"give_up": -0.5},
    }
    diff.update(overrides)
    return diff


def seed_agent(**overrides):
    agent = seed.make_agent(id="agent_1", tools=[], skip_default_tools=True, **overrides)
    seed.seed_agents([agent])
    return agent


def open_run_with_proposal(diff=None, target_kind="agent", object_id="agent_1",
                           mode="human"):
    run = runs_mod.create_run(
        "default", object_id, target_kind, baseline_version_n=1,
        tuner_model="m", mode=mode,
    )
    runs_mod.write_proposal(
        "default", run["run_id"], {"insights": {}, "proposed_diff": diff or make_diff()}
    )
    return run


# ── 5.1 / 5.2 — enum + executor registration ─────────────────────────────────

class TestRegistration:
    def test_all_six_step_types_exist(self):
        for name in ("IMPROVE_ANALYZE", "IMPROVE_PROPOSE", "IMPROVE_REVIEW",
                     "IMPROVE_APPLY", "BENCHMARK", "IMPROVE_RATCHET_DECIDE"):
            assert hasattr(StepType, name)

    def test_all_six_executors_registered(self):
        from core.orchestration.steps import STEP_EXECUTORS
        for st in (StepType.IMPROVE_ANALYZE, StepType.IMPROVE_PROPOSE,
                   StepType.IMPROVE_REVIEW, StepType.IMPROVE_APPLY,
                   StepType.BENCHMARK, StepType.IMPROVE_RATCHET_DECIDE):
            assert st in STEP_EXECUTORS, f"missing executor for {st}"

    def test_registry_module_exports_six(self):
        assert len(IMPROVE_STEP_EXECUTORS) == 6


# ── 5.3 — IMPROVE_ANALYZE ────────────────────────────────────────────────────

class TestAnalyzeStep:
    async def test_analyze_writes_insights_and_propagates_target(self):
        seed_agent()
        step = _step(StepType.IMPROVE_ANALYZE, improve_target_id="agent_1",
                     improve_target_kind="agent", output_key="insights")
        run = _run()
        events = await _drain(
            IMPROVE_STEP_EXECUTORS["improve_analyze"].execute(step, run, _engine())
        )
        assert any(e["type"] == "improve_analyze_result" for e in events)
        assert "insights" in run.shared_state
        assert run.shared_state["improve_target_id"] == "agent_1"
        assert run.shared_state["improve_target_kind"] == "agent"

    async def test_analyze_without_target_raises(self):
        step = _step(StepType.IMPROVE_ANALYZE)
        with pytest.raises(RuntimeError, match="improve_target_id"):
            await _drain(
                IMPROVE_STEP_EXECUTORS["improve_analyze"].execute(step, _run(), _engine())
            )


# ── 5.3 / 5.16 — IMPROVE_PROPOSE ─────────────────────────────────────────────

class TestProposeStep:
    async def test_propose_writes_run_id_and_diff(self, fake_llm):
        seed_agent()
        fake_llm.script([json.dumps(make_diff())])
        step = _step(StepType.IMPROVE_PROPOSE)
        run = _run(shared_state={"improve_target_id": "agent_1",
                                 "improve_target_kind": "agent"})
        events = await _drain(
            IMPROVE_STEP_EXECUTORS["improve_propose"].execute(step, run, _engine())
        )
        assert any(e["type"] == "improve_propose_result" for e in events)
        assert run.shared_state["improve_run_id"].startswith("imp_")
        assert run.shared_state["proposed_diff"]["field_edits"][0]["field"] == "system_prompt"

    async def test_budget_exceeded_aborts_before_llm_call(self, fake_llm):
        """5.16 — abort on exceed, with a budget_abort inbox entry."""
        seed_agent()
        step = _step(StepType.IMPROVE_PROPOSE, improve_budget_usd=0.0)
        run = _run(shared_state={"improve_target_id": "agent_1"})
        events = await _drain(
            IMPROVE_STEP_EXECUTORS["improve_propose"].execute(step, run, _engine())
        )
        assert run.status == "failed"
        assert any(e["type"] == "step_error" and "budget" in e["error"].lower()
                   for e in events)
        assert fake_llm.calls == []  # aborted BEFORE spending more
        kinds = [e["kind"] for e in inbox_mod.list_entries("default")]
        assert "budget_abort" in kinds


# ── 5.5 / 5.6 — IMPROVE_REVIEW ───────────────────────────────────────────────

class TestReviewStep:
    async def test_human_mode_blocks_via_existing_approval_flow(self):
        """5.5 — emits the same human_input_required event the engine pauses on."""
        step = _step(StepType.IMPROVE_REVIEW, output_key="improve_review")
        run = _run(shared_state={
            "improve_mode": "human",
            "proposed_diff": make_diff(),
        })
        events = await _drain(
            IMPROVE_STEP_EXECUTORS["improve_review"].execute(step, run, _engine())
        )
        assert len(events) == 1
        ev = events[0]
        assert ev["type"] == "human_input_required"
        assert "system_prompt" in ev["prompt"]  # summarizes the proposed edits
        assert any(f["name"] == "action" for f in ev["fields"])

    async def test_autonomous_mode_skips_review(self):
        """5.6 — skipped, with an auto-approval written for the apply step."""
        step = _step(StepType.IMPROVE_REVIEW, output_key="improve_review")
        run = _run(shared_state={"improve_mode": "autonomous"})
        events = await _drain(
            IMPROVE_STEP_EXECUTORS["improve_review"].execute(step, run, _engine())
        )
        assert [e["type"] for e in events] == ["improve_review_skipped"]
        assert run.shared_state["improve_review"] == {"action": "apply", "auto": True}


# ── 5.3 / 5.10 / 5.15 — IMPROVE_APPLY ────────────────────────────────────────

class TestApplyStep:
    async def test_apply_writes_new_version(self):
        seed_agent()
        imp = open_run_with_proposal()
        step = _step(StepType.IMPROVE_APPLY)
        run = _run(shared_state={
            "improve_run_id": imp["run_id"],
            "improve_review": {"action": "apply"},
        })
        events = await _drain(
            IMPROVE_STEP_EXECUTORS["improve_apply"].execute(step, run, _engine())
        )
        assert any(e["type"] == "improve_apply_result" and e["applied"] for e in events)
        assert run.shared_state["new_version_n"] == 2
        from core.routes.agents import load_user_agents
        agent = next(a for a in load_user_agents() if a["id"] == "agent_1")
        assert agent["system_prompt"].startswith("You are a persistent")

    async def test_autonomous_apply_emits_inbox_entry(self):
        """5.10 — never silent."""
        seed_agent()
        imp = open_run_with_proposal(mode="autonomous")
        step = _step(StepType.IMPROVE_APPLY)
        run = _run(shared_state={"improve_mode": "autonomous",
                                 "improve_run_id": imp["run_id"]})
        await _drain(
            IMPROVE_STEP_EXECUTORS["improve_apply"].execute(step, run, _engine())
        )
        entries = inbox_mod.list_entries("default", kind="apply")
        assert len(entries) == 1
        assert entries[0]["object_id"] == "agent_1"
        assert entries[0]["mode"] == "autonomous"

    async def test_reject_action_closes_run_without_version(self):
        seed_agent()
        imp = open_run_with_proposal()
        step = _step(StepType.IMPROVE_APPLY)
        run = _run(shared_state={
            "improve_run_id": imp["run_id"],
            "improve_review": {"action": "reject"},
        })
        events = await _drain(
            IMPROVE_STEP_EXECUTORS["improve_apply"].execute(step, run, _engine())
        )
        assert any(e["type"] == "improve_apply_result" and not e["applied"]
                   for e in events)
        closed = runs_mod.get_run("default", imp["run_id"])
        assert closed["decision"] == "revert" and closed["closed_at"]

    async def test_self_edit_lockout_in_executor(self):
        """5.15 — an orchestration may not improve ITSELF while running."""
        orch = seed.make_orchestration(id="orch_self")
        seed.seed_orchestrations([orch])
        diff = make_diff(
            target_object_id="orch_self", target_kind="orchestration",
            field_edits=[{"field": "steps[0].prompt_template", "new_value": "x"}],
        )
        imp = open_run_with_proposal(diff=diff, target_kind="orchestration",
                                     object_id="orch_self")
        step = _step(StepType.IMPROVE_APPLY)
        run = _run(shared_state={"improve_run_id": imp["run_id"]},
                   orchestration_id="orch_self")
        events = await _drain(
            IMPROVE_STEP_EXECUTORS["improve_apply"].execute(
                step, run, _engine(orch_id="orch_self"))
        )
        assert run.status == "failed"
        assert any("self-edit lockout" in e.get("error", "") for e in events)

    def test_self_edit_lockout_at_applier_boundary(self):
        """5.15 — the applier itself refuses, independent of the executor."""
        orch = seed.make_orchestration(id="orch_self")
        seed.seed_orchestrations([orch])
        diff = make_diff(
            target_object_id="orch_self", target_kind="orchestration",
            field_edits=[{"field": "steps[0].prompt_template", "new_value": "x"}],
        )
        imp = open_run_with_proposal(diff=diff, target_kind="orchestration",
                                     object_id="orch_self")
        with pytest.raises(applier.ApplyError, match="self-edit lockout"):
            applier.apply_run("default", imp["run_id"],
                              executing_orchestration_id="orch_self")


# ── 5.3 — BENCHMARK step ─────────────────────────────────────────────────────

class TestBenchmarkStep:
    async def test_benchmark_step_scores_and_writes_state(self, fake_llm):
        seed_agent()
        fake_llm.set_default("All done.")
        bm.save_benchmark("default", {
            "id": "bench_cp5", "name": "CP5 suite", "target_object_id": "agent_1",
            "inputs": [{"prompt": "Say hi."}],
            "scorer": {"metrics": {"success": 1.0}},
        })
        step = _step(StepType.BENCHMARK, benchmark_id="bench_cp5",
                     benchmark_record_as="baseline", output_key="baseline_score")
        run = _run(shared_state={"improve_target_id": "agent_1"})
        events = await _drain(
            IMPROVE_STEP_EXECUTORS["benchmark"].execute(step, run, _engine())
        )
        ev = next(e for e in events if e["type"] == "benchmark_result")
        assert ev["benchmark_id"] == "bench_cp5"
        assert run.shared_state["baseline_score"] == ev["score"]
        assert ev["score"] is not None

    async def test_benchmark_step_without_id_raises(self):
        step = _step(StepType.BENCHMARK)
        with pytest.raises(RuntimeError, match="benchmark_id"):
            await _drain(
                IMPROVE_STEP_EXECUTORS["benchmark"].execute(step, _run(), _engine())
            )


# ── 5.11–5.17 — IMPROVE_RATCHET_DECIDE ───────────────────────────────────────

def _ratchet(**overrides):
    return _step(StepType.IMPROVE_RATCHET_DECIDE, **overrides)


async def _decide(step, run, engine=None):
    events = await _drain(
        IMPROVE_STEP_EXECUTORS["improve_ratchet_decide"].execute(
            step, run, engine or _engine())
    )
    return next(e for e in events if e["type"] == "ratchet_decision")


class TestRatchetDecide:
    async def test_keep_when_delta_meets_threshold(self):
        seed_agent()
        imp = open_run_with_proposal()
        run = _run(shared_state={"improve_run_id": imp["run_id"],
                                 "baseline_score": 0.5, "new_score": 0.8})
        ev = await _decide(_ratchet(), run)
        assert ev["decision"] == "keep" and not ev["stop"]
        assert run.shared_state["ratchet_decision"] == "keep"
        stamped = runs_mod.get_run("default", imp["run_id"])
        assert stamped["baseline_score"] == 0.5 and stamped["new_score"] == 0.8

    async def test_revert_rolls_back_and_audits(self):
        """5.11 / 5.17 — auto-revert below threshold, inbox entry, restored config."""
        agent = seed_agent()
        original_prompt = agent["system_prompt"]
        imp = open_run_with_proposal(mode="autonomous")
        applied = applier.apply_run("default", imp["run_id"])
        assert applied["run"]["new_version_n"] == 2
        run = _run(shared_state={"improve_mode": "autonomous",
                                 "improve_run_id": imp["run_id"],
                                 "baseline_score": 0.8, "new_score": 0.5})
        ev = await _decide(_ratchet(ratchet_threshold=0.0), run)
        assert ev["decision"] == "revert"
        from core.routes.agents import load_user_agents
        restored = next(a for a in load_user_agents() if a["id"] == "agent_1")
        assert restored["system_prompt"] == original_prompt  # rolled back
        assert runs_mod.get_run("default", imp["run_id"])["decision"] == "revert"
        entries = inbox_mod.list_entries("default", kind="revert")
        assert len(entries) == 1 and entries[0]["score_delta"] == -0.3

    async def test_missing_scores_defaults_to_revert(self):
        run = _run(shared_state={})
        ev = await _decide(_ratchet(), run)
        assert ev["decision"] == "revert" and ev["delta"] is None

    async def test_iteration_cap_stops_cleanly(self):
        """5.12 — hard cap on iterations (from StepConfig)."""
        run = _run(shared_state={"_ratchet_iteration": 4,
                                 "baseline_score": 0.5, "new_score": 0.9})
        ev = await _decide(_ratchet(ratchet_max_iterations=5), run)
        assert ev["stop"] and ev["stop_reason"] == "max_iterations"
        assert run.shared_state["ratchet_stop"] is True
        kinds = [e["kind"] for e in inbox_mod.list_entries("default")]
        assert "max_iterations_stop" in kinds

    async def test_plateau_patience_stops_after_consecutive_reverts(self):
        """5.14 — N consecutive reverts terminates the loop."""
        run = _run(shared_state={"_ratchet_consecutive_reverts": 1})
        ev = await _decide(_ratchet(ratchet_plateau_patience=2), run)
        assert ev["decision"] == "revert"
        assert ev["stop"] and ev["stop_reason"] == "plateau"
        kinds = [e["kind"] for e in inbox_mod.list_entries("default")]
        assert "plateau_stop" in kinds

    async def test_keep_resets_consecutive_reverts(self):
        run = _run(shared_state={"_ratchet_consecutive_reverts": 1,
                                 "baseline_score": 0.5, "new_score": 0.9})
        ev = await _decide(_ratchet(ratchet_plateau_patience=2), run)
        assert ev["decision"] == "keep" and not ev["stop"]
        assert run.shared_state["_ratchet_consecutive_reverts"] == 0

    async def test_wallclock_cap_stops_proactively(self):
        """5.13 — stops before starting an iteration that would blow the
        global timeout (engine's hard timeout remains the backstop)."""
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        run = _run(shared_state={"baseline_score": 0.5, "new_score": 0.9},
                   started_at=old)
        ev = await _decide(_ratchet(), run, _engine(timeout_minutes=60))
        assert ev["stop"] and ev["stop_reason"] == "timeout"
        kinds = [e["kind"] for e in inbox_mod.list_entries("default")]
        assert "timeout_stop" in kinds

    async def test_budget_cap_stops_with_inbox_entry(self):
        """5.16 — per-run LLM budget enforced at the ratchet too."""
        run = _run(shared_state={"baseline_score": 0.5, "new_score": 0.9})
        ev = await _decide(_ratchet(improve_budget_usd=0.0), run)
        assert ev["stop"] and ev["stop_reason"] == "budget"
        kinds = [e["kind"] for e in inbox_mod.list_entries("default")]
        assert "budget_abort" in kinds


# ── 5.18 / 5.19 — bulk revert of autonomous edits ────────────────────────────

class TestRevertAutonomousSince:
    async def test_revert_all_autonomous_edits_since_t(self):
        agent = seed_agent()
        original_prompt = agent["system_prompt"]
        since = "2000-01-01T00:00:00Z"

        imp = open_run_with_proposal(mode="autonomous")
        applier.apply_run("default", imp["run_id"])
        from core.routes.agents import load_user_agents
        assert next(a for a in load_user_agents()
                    if a["id"] == "agent_1")["version_n"] == 2

        result = applier.revert_autonomous_since("default", since)
        assert result["errors"] == []
        assert result["reverted"][0]["object_id"] == "agent_1"
        assert result["reverted"][0]["restored_version_n"] == 1
        restored = next(a for a in load_user_agents() if a["id"] == "agent_1")
        assert restored["system_prompt"] == original_prompt
        # 5.18 — the autonomous run is tagged and flipped to revert
        assert runs_mod.get_run("default", imp["run_id"])["decision"] == "revert"
        assert inbox_mod.list_entries("default", kind="revert")

    async def test_human_mode_applies_are_not_reverted(self):
        seed_agent()
        imp = open_run_with_proposal(mode="human")
        applier.apply_run("default", imp["run_id"])
        result = applier.revert_autonomous_since("default", "2000-01-01T00:00:00Z")
        assert result["reverted"] == []
        from core.routes.agents import load_user_agents
        assert next(a for a in load_user_agents()
                    if a["id"] == "agent_1")["version_n"] == 2  # untouched


# ── 5.7 / 5.20 / 5.22 — through the REAL engine ──────────────────────────────

async def _run_engine(orch_dict, initial_state=None, initial_input="go"):
    from core.models_orchestration import Orchestration
    from core.orchestration.engine import OrchestrationEngine
    orch = Orchestration.model_validate(orch_dict)
    engine = OrchestrationEngine(orch, _server())
    events = []
    async for ev in engine.run(initial_input, run_id=f"run_{orch.id}",
                               initial_state=initial_state):
        events.append(ev)
    return events


class TestEngineIntegration:
    async def test_autonomous_loop_terminates_cleanly_on_plateau(self, fake_llm):
        """5.6 / 5.7 / 5.22 — full autonomous pass through the real engine:
        analyze → propose → review(skipped) → apply → ratchet(revert+plateau)
        → gate → end. Same executors as the human-gated path."""
        agent = seed_agent()
        original_prompt = agent["system_prompt"]
        fake_llm.set_default(json.dumps(make_diff()))
        orch = seed.make_orchestration(
            id="orch_ratchet",
            entry_step_id="s_analyze",
            steps=[
                {"id": "s_analyze", "name": "Analyze", "type": "improve_analyze",
                 "improve_target_id": "agent_1", "improve_target_kind": "agent",
                 "output_key": "insights", "next_step_id": "s_propose"},
                {"id": "s_propose", "name": "Propose", "type": "improve_propose",
                 "output_key": "proposed_diff", "next_step_id": "s_review"},
                {"id": "s_review", "name": "Review", "type": "improve_review",
                 "output_key": "improve_review", "next_step_id": "s_apply"},
                {"id": "s_apply", "name": "Apply", "type": "improve_apply",
                 "output_key": "apply_result", "next_step_id": "s_ratchet"},
                # No benchmark steps: missing scores → revert → plateau stop
                {"id": "s_ratchet", "name": "Ratchet", "type": "improve_ratchet_decide",
                 "ratchet_plateau_patience": 1, "output_key": "ratchet",
                 "next_step_id": "s_gate"},
                {"id": "s_gate", "name": "Gate", "type": "if_else",
                 "if_condition": "state.ratchet_stop == True",
                 "if_true_step_id": "s_end", "if_false_step_id": "s_analyze"},
                {"id": "s_end", "name": "End", "type": "end"},
            ],
        )
        events = await _run_engine(
            orch, initial_state={"improve_mode": "autonomous"})
        types_seen = [e.get("type") for e in events]
        assert "improve_review_skipped" in types_seen        # 5.6
        assert "orchestration_complete" in types_seen        # clean termination
        complete = next(e for e in events if e["type"] == "orchestration_complete")
        assert complete["status"] == "completed"
        assert complete["final_state"]["ratchet_stop_reason"] == "plateau"
        # Applied then auto-reverted — live config restored (5.17)
        from core.routes.agents import load_user_agents
        assert next(a for a in load_user_agents()
                    if a["id"] == "agent_1")["system_prompt"] == original_prompt
        # Never silent: apply + revert + plateau_stop all audited (5.10/5.11)
        kinds = {e["kind"] for e in inbox_mod.list_entries("default")}
        assert {"apply", "revert", "plateau_stop"} <= kinds

    async def test_human_gated_blocks_and_resumes(self, fake_llm):
        """5.5 / 5.20 — review pauses via the existing approval flow, and the
        run resumes correctly with the human's decision."""
        from core.orchestration.engine import OrchestrationEngine
        seed_agent()
        imp = open_run_with_proposal()
        orch = seed.make_orchestration(
            id="orch_gated",
            entry_step_id="s_review",
            steps=[
                {"id": "s_review", "name": "Review", "type": "improve_review",
                 "output_key": "improve_review", "next_step_id": "s_apply"},
                {"id": "s_apply", "name": "Apply", "type": "improve_apply",
                 "output_key": "apply_result", "next_step_id": None},
            ],
        )
        seed.seed_orchestrations([orch])  # resume() reloads from disk

        events = await _run_engine(
            orch,
            initial_state={"improve_mode": "human",
                           "improve_run_id": imp["run_id"],
                           "proposed_diff": make_diff()},
        )
        assert events[-1]["type"] == "human_input_required"  # blocked (5.5)

        resumed = []
        async for ev in OrchestrationEngine.resume(
            f"run_{orch['id']}", {"action": "apply"}, _server()
        ):
            resumed.append(ev)
        types_seen = [e.get("type") for e in resumed]
        assert "improve_apply_result" in types_seen
        assert "orchestration_complete" in types_seen
        from core.routes.agents import load_user_agents
        agent = next(a for a in load_user_agents() if a["id"] == "agent_1")
        assert agent["version_n"] == 2  # the approved diff landed (5.20)
