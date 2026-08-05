"""
Checkpoint-6 verification (unit), chunk 3 — the two end-to-end exit criteria.

6.34  deterministic: an NL2SQL-style agent is graded on 5 input/expected pairs,
      a known-good diff raises the outcome score by >= 0.15, and the ratchet
      KEEPS it (Appendix C6 "Deterministic correctness").
6.35  rubric: an open-ended agent is graded against a rubric; a known-bad diff
      RAISES the train score but LOWERS the holdout score; the ratchet decides
      on holdout, auto-reverts, and emits an inbox entry (Appendix C6 "Rubric
      sensitivity" + "Generalization").

Both flows go through the real pipeline: `save_benchmark` -> `run_benchmark`
(real agent execution via the CP1 hooks) -> outcome grading -> the real
`IMPROVE_RATCHET_DECIDE` executor with the real applier rollback.
"""
import json
import os
import shutil
import time
import types

import pytest

from core.improve import applier
from core.improve import benchmark as bm
from core.improve import inbox as inbox_mod
from core.improve import judge as judge_mod
from core.improve import runs as runs_mod
from core.improve.steps import IMPROVE_STEP_EXECUTORS, grading_detail
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


# ── helpers (mirrors test_improve_grading_steps.py) ──────────────────────────

def _server():
    return types.SimpleNamespace(agent_sessions={}, memory_store=None, tool_router={})


def _engine():
    return types.SimpleNamespace(
        orch=types.SimpleNamespace(id="orch_driver", timeout_minutes=60),
        server_module=_server(),
    )


def _run(shared_state):
    return OrchestrationRun(
        run_id="run_e2e", orchestration_id="orch_driver",
        shared_state=shared_state,
        started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )


def _step(step_type, **overrides):
    cfg = {"id": "s1", "name": "E2E step", "type": step_type}
    cfg.update(overrides)
    return StepConfig.model_validate(cfg)


async def _drain(gen):
    return [e async for e in gen]


async def _decide(run, threshold=0.05):
    events = await _drain(
        IMPROVE_STEP_EXECUTORS["improve_ratchet_decide"].execute(
            _step(StepType.IMPROVE_RATCHET_DECIDE, ratchet_threshold=threshold),
            run, _engine())
    )
    return next(e for e in events if e["type"] == "ratchet_decision")


def seed_agent():
    agent = seed.make_agent(id="agent_1", tools=[], skip_default_tools=True)
    seed.seed_agents([agent])
    return agent


def _active_agent_version():
    import core.routes.agents as agents_mod
    agent = next(a for a in agents_mod.load_user_agents()
                 if a.get("id") == "agent_1")
    return int(agent.get("version_n") or 1)


def open_run_and_apply(field="system_prompt", new_value="v2 prompt",
                       mode="human"):
    """Open an improvement run, propose one allow-listed edit, apply it."""
    run = runs_mod.create_run(
        USER, "agent_1", "agent", baseline_version_n=1,
        tuner_model="m", mode=mode,
    )
    runs_mod.write_proposal(USER, run["run_id"], {"insights": {}, "proposed_diff": {
        "target_object_id": "agent_1", "target_kind": "agent",
        "field_edits": [{"field": field, "old_value": "old",
                         "new_value": new_value, "rationale": "e2e"}],
        "rationale": "e2e", "evidence_pointers": [],
        "expected_metric_deltas": {},
    }})
    applier.apply_run(USER, run["run_id"])
    return run


# ── 6.34 — deterministic end to end ──────────────────────────────────────────

# Five NL2SQL-style questions, each with its own expected answer token. The
# same agent answer is graded five different ways, so correctness per input is
# controlled by WHAT the fake agent says, not by scripting call order.
REGION_TOKENS = ["APAC", "EMEA", "AMER", "LATAM", "ANZ"]

NL2SQL_SUITE = {
    "id": "bench_e2e_det", "name": "NL2SQL outcomes",
    "target_object_id": "agent_1",
    "schema_version": 2, "grading_mode": "deterministic",
    "scorer": {"metrics": {"success": 1.0},
               "process_weight": 1.0, "outcome_weight": 1.0},
    "inputs": [
        {
            "id": f"in_{i + 1:03d}",
            "prompt": f"Which region ranks #{i + 1} by Q3 revenue?",
            "split": "train" if i < 3 else "holdout",
            "weight": 1.0,
            "expected": {"checks": [{
                "id": "answer", "weight": 1.0, "critical": True,
                "extract": {"from": "final_output"},
                "compare": {"type": "contains_all", "value": [token],
                            "case_sensitive": False},
            }]},
        }
        for i, token in enumerate(REGION_TOKENS)
    ],
}

# Names two of the three train regions, none of the holdout ones.
BASELINE_ANSWER = "Top Q3 regions: APAC at $4.2M, then EMEA at $3.1M."
# Names all five — the behavior a genuinely good diff would produce.
IMPROVED_ANSWER = ("Q3 revenue ranking: APAC $4.2M, EMEA $3.1M, AMER $2.8M, "
                   "LATAM $1.9M, ANZ $0.9M.")


class TestDeterministicEndToEnd:
    async def test_known_good_diff_raises_outcome_and_ratchet_keeps(self, fake_llm):
        seed_agent()
        bm.save_benchmark(USER, json.loads(json.dumps(NL2SQL_SUITE)))
        run = _run({"improve_target_id": "agent_1", "improve_mode": "human"})
        shared = run.shared_state  # pydantic copies the ctor dict
        
        # 1. Baseline benchmark through the real BENCHMARK step executor.
        fake_llm.set_default(BASELINE_ANSWER)
        await _drain(IMPROVE_STEP_EXECUTORS["benchmark"].execute(
            _step(StepType.BENCHMARK, benchmark_id="bench_e2e_det",
                  benchmark_record_as="baseline"),
            run, _engine()))

        baseline_detail = shared["baseline_score_detail"]
        assert baseline_detail["scores_by_split"]["train"] == pytest.approx(2 / 3)
        assert baseline_detail["scores_by_split"]["holdout"] == 0.0

        # 2. Apply a known-good diff (a real allow-listed edit -> version 2).
        imp = open_run_and_apply(new_value="Always report every region's figure.")
        shared["improve_run_id"] = imp["run_id"]
        assert _active_agent_version() == 2

        # 3. New benchmark run under the improved behavior.
        fake_llm.set_default(IMPROVED_ANSWER)
        await _drain(IMPROVE_STEP_EXECUTORS["benchmark"].execute(
            _step(StepType.BENCHMARK, benchmark_id="bench_e2e_det",
                  benchmark_record_as="new"),
            run, _engine()))

        new_detail = shared["new_score_detail"]
        assert new_detail["scores_by_split"] == {
            "train": 1.0, "holdout": 1.0, "regression": None}

        # Appendix C6: the known-good diff raises outcome_score by >= 0.15.
        assert (new_detail["outcome_score"]
                - baseline_detail["outcome_score"]) >= 0.15

        # 4. The ratchet decides on holdout and KEEPS the edit.
        ev = await _decide(run, threshold=0.15)
        assert ev["decision"] == "keep"
        assert ev["decision_basis"] == "holdout"
        assert ev["baseline_holdout"] == 0.0 and ev["new_holdout"] == 1.0
        assert _active_agent_version() == 2, "keep must not roll back"
        assert runs_mod.get_run(USER, imp["run_id"])["decision"] == "keep"

    async def test_scores_are_recorded_per_split_in_the_result_records(self, fake_llm):
        """6.16 through the same E2E path: both records report train and
        holdout separately, and the composite is not what decided."""
        seed_agent()
        bm.save_benchmark(USER, json.loads(json.dumps(NL2SQL_SUITE)))
        fake_llm.set_default(BASELINE_ANSWER)
        result = await bm.run_benchmark(USER, "bench_e2e_det",
                                        server_module=_server())
        assert result["grading_strictness"] == "strict"
        assert result["scores_by_split"]["train"] == pytest.approx(2 / 3)
        assert result["scores_by_split"]["holdout"] == 0.0
        # 2 of 5 inputs pass -> weighted outcome 0.4; the composite blends in
        # the process axis. Neither hides the holdout zero.
        assert result["outcome_score"] == pytest.approx(0.4)


# ── 6.35 — rubric end to end ─────────────────────────────────────────────────

class ScriptedJudge:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def __call__(self, prompt, system_prompt):
        self.prompts.append((prompt, system_prompt))
        return self.responses.pop(0) if self.responses else ""


def judge_session(responses):
    return judge_mod.JudgeSession(
        USER, model="test.judge", generate=ScriptedJudge(responses))


def verdict(kp1: bool, kp2: bool) -> str:
    return json.dumps({"verdicts": [
        {"id": "kp1", "present": kp1}, {"id": "kp2", "present": kp2}]})


RUBRIC = {
    "id": "rubric_e2e", "name": "Research synthesis quality",
    "criteria": [{
        "id": "coverage", "kind": "key_point_coverage", "weight": 1.0,
    }],
}

RUBRIC_EXPECTED = {
    "key_points": [
        {"id": "kp1", "text": "Notes no mass-market deployment yet", "weight": 2.0},
        {"id": "kp2", "text": "Names at least one manufacturer program", "weight": 1.0},
    ],
}

RUBRIC_SUITE = {
    "id": "bench_e2e_rub", "name": "Open-ended outcomes",
    "target_object_id": "agent_1",
    "schema_version": 2, "grading_mode": "rubric", "rubric_id": "rubric_e2e",
    "scorer": {"metrics": {"success": 1.0},
               "process_weight": 1.0, "outcome_weight": 1.0},
    "inputs": [
        {"id": "in_t", "prompt": "Summarize battery commercialization.",
         "split": "train", "weight": 1.0,
         "expected": json.loads(json.dumps(RUBRIC_EXPECTED))},
        {"id": "in_h", "prompt": "Summarize grid storage trends.",
         "split": "holdout", "weight": 1.0,
         "expected": json.loads(json.dumps(RUBRIC_EXPECTED))},
    ],
}


class TestRubricEndToEnd:
    async def test_known_bad_diff_lowers_holdout_ratchet_reverts_and_notifies(
        self, fake_llm
    ):
        """The signature failure mode splits exist to catch: train IMPROVES
        while holdout REGRESSES. The ratchet must decide on holdout, restore
        the baseline version, and never be silent about it (5.11)."""
        from core.improve import rubrics as rubrics_mod

        seed_agent()
        rubrics_mod.save_rubric(USER, json.loads(json.dumps(RUBRIC)))
        bm.save_benchmark(USER, json.loads(json.dumps(RUBRIC_SUITE)))

        imp = runs_mod.create_run(USER, "agent_1", "agent",
                                  baseline_version_n=1, tuner_model="m",
                                  mode="autonomous")

        # 1. Baseline: train weak (kp1 missed), holdout solid.
        #    Inputs grade in suite order, one batched verdict call per input.
        fake_llm.set_default("Baseline synthesis, careful on grid storage.")
        baseline = await bm.run_benchmark(
            USER, "bench_e2e_rub", server_module=_server(),
            judge_session=judge_session([
                verdict(kp1=False, kp2=True),   # train  -> (0*2 + 1*1)/3
                verdict(kp1=True, kp2=True),    # holdout -> 1.0
            ]),
            improvement_run_id=imp["run_id"], record_as="baseline",
        )
        assert baseline["scores_by_split"]["train"] == pytest.approx(1 / 3)
        assert baseline["scores_by_split"]["holdout"] == 1.0

        # 2. Apply a known-bad diff (real edit, version 2 becomes active).
        runs_mod.write_proposal(USER, imp["run_id"], {
            "insights": {},
            "proposed_diff": {
                "target_object_id": "agent_1", "target_kind": "agent",
                "field_edits": [{"field": "system_prompt", "old_value": "old",
                                 "new_value": "Be extremely brief.",
                                 "rationale": "overfit to train phrasing"}],
                "rationale": "e2e", "evidence_pointers": [],
                "expected_metric_deltas": {},
            },
        })
        applier.apply_run(USER, imp["run_id"])
        assert _active_agent_version() == 2

        # 3. New run: train improves, holdout collapses (memorized phrasing).
        fake_llm.set_default("Terse v2 answer memorizing train phrasing.")
        new = await bm.run_benchmark(
            USER, "bench_e2e_rub", server_module=_server(),
            judge_session=judge_session([
                verdict(kp1=True, kp2=True),    # train  -> 1.0 (improved!)
                verdict(kp1=False, kp2=False),  # holdout -> 0.0 (regressed)
            ]),
            improvement_run_id=imp["run_id"], record_as="new",
        )
        assert new["scores_by_split"]["train"] == 1.0
        assert new["scores_by_split"]["holdout"] == 0.0
        assert new["rubric_content_hash"] == baseline["rubric_content_hash"]

        # 4. The real ratchet step: decides on holdout, reverts, notifies.
        run = _run({
            "improve_target_id": "agent_1", "improve_mode": "autonomous",
            "improve_run_id": imp["run_id"],
            "baseline_score": baseline["score"],
            "baseline_score_detail": grading_detail(baseline),
            "new_score": new["score"],
            "new_score_detail": grading_detail(new),
        })
        ev = await _decide(run)

        assert ev["decision"] == "revert"
        assert ev["decision_basis"] == "holdout"
        assert ev["delta"] == pytest.approx(-1.0)
        assert _active_agent_version() == 1, "revert must restore the baseline"
        assert runs_mod.get_run(USER, imp["run_id"])["decision"] == "revert"

        reverts = inbox_mod.list_entries(USER, kind="revert")
        assert len(reverts) == 1
        assert reverts[0]["object_id"] == "agent_1"
        assert "revert" in reverts[0]["message"].lower()

    async def test_verdict_cache_makes_regrading_unchanged_output_free(self, fake_llm):
        """6.13 through the E2E path: a second run over identical agent output
        re-uses cached verdicts — the scripted judge is never called again."""
        from core.improve import rubrics as rubrics_mod

        seed_agent()
        rubrics_mod.save_rubric(USER, json.loads(json.dumps(RUBRIC)))
        bm.save_benchmark(USER, json.loads(json.dumps(RUBRIC_SUITE)))
        fake_llm.set_default("Stable output.")

        first = await bm.run_benchmark(
            USER, "bench_e2e_rub", server_module=_server(),
            judge_session=judge_session([
                verdict(True, True), verdict(True, True)]),
        )
        starved = judge_session([])  # would return "" -> N/A on a cache miss
        second = await bm.run_benchmark(
            USER, "bench_e2e_rub", server_module=_server(),
            judge_session=starved,
        )
        assert first["outcome_score"] == second["outcome_score"] == 1.0
        assert second["judge_cache_hits"] >= 2
