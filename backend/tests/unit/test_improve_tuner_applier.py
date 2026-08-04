"""
Checkpoint-3 verification (unit): tuner output schema + guardrails
(3.1–3.8, 3.22 tuner boundary), versioning snapshots + immutability
(3.9/3.10), and the applier's independent second boundary + apply/rollback
mechanics (3.7, 3.11, 3.22 applier boundary).
"""
import json
import os
import shutil

import pytest
from pydantic import ValidationError

from core.improve import applier, runs as runs_mod, tuner, versioning
from core.improve.tuner import (
    MAX_TUNER_PROMPT_CHARS,
    ProposedDiff,
    build_tuner_prompt,
    compact_insights,
    parse_tuner_output,
    validate_field_edits,
)
from _fakes import seed


@pytest.fixture(autouse=True)
def _clean_improve_dir():
    """Improvement state from other test modules must not leak in."""
    from core.config import DATA_DIR
    improve_dir = os.path.join(DATA_DIR, "improve")
    # Version snapshots are chmod'd read-only; lift that so rmtree works.
    for root, _dirs, files in os.walk(improve_dir):
        for name in files:
            try:
                os.chmod(os.path.join(root, name), 0o600)
            except OSError:
                pass
    shutil.rmtree(improve_dir, ignore_errors=True)
    yield


# ── helpers ──────────────────────────────────────────────────────────────────

def make_diff(**overrides):
    diff = {
        "target_object_id": "agent_1",
        "target_kind": "agent",
        "field_edits": [
            {
                "field": "system_prompt",
                "old_value": "You are a helpful test assistant.",
                "new_value": "You are a helpful, persistent assistant. Never give up.",
                "rationale": "give_up detector fired",
            }
        ],
        "rationale": "Traces show frequent premature give-ups.",
        "evidence_pointers": [{"trace_file": "agent_1/2026-01/s.json", "message_idx": 3}],
        "expected_metric_deltas": {"give_up": -0.5},
    }
    diff.update(overrides)
    return diff


def seed_agent(**overrides):
    agent = seed.make_agent(id="agent_1", **overrides)
    seed.seed_agents([agent])
    return agent


def open_run_with_proposal(diff=None, target_kind="agent", object_id="agent_1"):
    run = runs_mod.create_run(
        "default", object_id, target_kind, baseline_version_n=1, tuner_model="m"
    )
    runs_mod.write_proposal(
        "default", run["run_id"], {"insights": {}, "proposed_diff": diff or make_diff()}
    )
    return run


# ── 3.4 / 3.5 — ProposedDiff schema ──────────────────────────────────────────

class TestProposedDiffSchema:
    def test_valid_diff_has_all_five_components(self):
        d = ProposedDiff.model_validate(make_diff())
        assert d.target_object_id and d.field_edits and d.rationale
        assert d.evidence_pointers[0].message_idx == 3
        assert d.expected_metric_deltas == {"give_up": -0.5}

    def test_missing_field_edits_rejected(self):
        bad = make_diff()
        del bad["field_edits"]
        with pytest.raises(ValidationError):
            ProposedDiff.model_validate(bad)

    def test_empty_field_edits_rejected(self):
        with pytest.raises(ValidationError):
            ProposedDiff.model_validate(make_diff(field_edits=[]))

    def test_bad_target_kind_rejected(self):
        with pytest.raises(ValidationError):
            ProposedDiff.model_validate(make_diff(target_kind="database"))

    def test_parse_strips_code_fences_and_prose(self):
        payload = json.dumps(make_diff())
        assert parse_tuner_output(f"```json\n{payload}\n```") == json.loads(payload)
        assert parse_tuner_output(f"Here you go:\n{payload}\nDone.") == json.loads(payload)

    def test_parse_garbage_raises(self):
        with pytest.raises(ValueError):
            parse_tuner_output("I refuse to produce JSON.")


# ── 3.6 / 3.22 — tuner-boundary allow-list ───────────────────────────────────

class TestTunerAllowList:
    def test_agent_tunable_fields_pass(self):
        edits = [{"field": f, "new_value": "x"} for f in
                 ("system_prompt", "tools", "max_turns", "description",
                  "delegate_agent_ids")]
        assert validate_field_edits(edits, "agent") == []

    def test_out_of_scope_agent_fields_flagged(self):
        for field in ("id", "repos", "db_configs", "core/react_engine.py",
                      "credentials", "mcp_servers"):
            assert validate_field_edits([{"field": field}], "agent")

    def test_model_edit_bounded_to_allow_list(self):
        allowed = {"gpt-4o", "ollama.mistral"}
        ok = [{"field": "model", "new_value": "gpt-4o"}]
        bad = [{"field": "model", "new_value": "gpt-99-ultra"}]
        assert validate_field_edits(ok, "agent", allowed) == []
        assert validate_field_edits(bad, "agent", allowed)

    def test_orchestration_step_paths(self):
        assert validate_field_edits(
            [{"field": "steps[0].prompt_template", "new_value": "x"}],
            "orchestration") == []
        assert validate_field_edits(
            [{"field": "steps[2].timeout_seconds", "new_value": 60}],
            "orchestration") == []
        # non-tunable step field, non-step path, and a .py path all rejected
        for field in ("steps[0].agent_id", "entry_step_id",
                      "backend/core/tools.py", "steps[x].prompt_template"):
            assert validate_field_edits([{"field": field}], "orchestration")


# ── 3.8 — prompt cap + compacted-insights fallback ───────────────────────────

class TestPromptCap:
    def _huge_insights(self):
        return {
            "trace_count": 500, "insight_count": 500,
            "insights": [
                {"kind": "trace_finding", "detector": "loops", "severity": "high",
                 "learning": "L" * 500, "id": f"i{i}",
                 "evidence": [{"trace_file": f"a/2026-01/s{i}.json",
                               "message_idx": j} for j in range(10)]}
                for i in range(500)
            ],
        }

    def test_small_insights_untouched(self):
        insights = {"trace_count": 1, "insight_count": 0, "insights": []}
        prompt = build_tuner_prompt("agent_1", "agent", {"id": "agent_1"},
                                    insights, set())
        assert "compacted" not in prompt

    def test_oversized_insights_compact_under_cap(self):
        prompt = build_tuner_prompt("agent_1", "agent", {"id": "agent_1"},
                                    self._huge_insights(), set())
        assert len(prompt) <= MAX_TUNER_PROMPT_CHARS
        assert "compacted to fit prompt cap" in prompt

    def test_compact_keeps_highest_severity_and_trims_evidence(self):
        insights = {
            "trace_count": 2, "insight_count": 2,
            "insights": [
                {"detector": "token_usage", "severity": "low", "learning": "l",
                 "id": "b", "evidence": []},
                {"detector": "give_up", "severity": "high", "learning": "g",
                 "id": "a", "evidence": [{"trace_file": "t", "message_idx": i}
                                          for i in range(5)]},
            ],
        }
        compacted = compact_insights(insights)
        assert compacted["insights"][0]["detector"] == "give_up"
        assert len(compacted["insights"][0]["evidence"]) == 2


# ── 3.1 / 3.3 / 3.6 — propose() through the fake LLM ─────────────────────────

class TestProposeFlow:
    async def test_propose_returns_pinned_run_and_diff(self, fake_llm):
        seed_agent()
        fake_llm.script([json.dumps(make_diff())])
        result = await tuner.propose("default", "agent_1", "agent",
                                     tuner_model="gpt-4o")
        assert result["run"]["tuner_model"] == "gpt-4o"  # pinned per run
        assert result["run"]["decision"] == "pending"
        assert result["proposed_diff"]["field_edits"][0]["field"] == "system_prompt"
        # proposal payload persisted and referenced
        saved = runs_mod.load_proposal("default", result["run"]["run_id"])
        assert saved["proposed_diff"] == result["proposed_diff"]
        assert result["run"]["proposed_diff_ref"].endswith(".json")

    async def test_propose_uses_generate_response_not_sdk(self, fake_llm):
        """3.1 — the tuner call lands on the patched generate_response."""
        seed_agent()
        fake_llm.script([json.dumps(make_diff())])
        await tuner.propose("default", "agent_1", "agent")
        assert fake_llm.calls, "tuner must dispatch through generate_response"
        assert fake_llm.calls[-1]["source"] == "improve_tuner"

    async def test_malformed_output_retried_then_accepted(self, fake_llm):
        seed_agent()
        fake_llm.script(["not json at all", json.dumps(make_diff())])
        result = await tuner.propose("default", "agent_1", "agent")
        assert len(fake_llm.calls) == 2
        assert "REJECTED" in fake_llm.calls[-1]["prompt_msg"]
        assert result["proposed_diff"]["target_object_id"] == "agent_1"

    async def test_out_of_scope_output_retried_then_accepted(self, fake_llm):
        seed_agent()
        bad = make_diff(field_edits=[{"field": "repos", "new_value": ["r1"]}])
        fake_llm.script([json.dumps(bad), json.dumps(make_diff())])
        result = await tuner.propose("default", "agent_1", "agent")
        assert len(fake_llm.calls) == 2
        assert result["proposed_diff"]["field_edits"][0]["field"] == "system_prompt"

    async def test_persistent_bad_output_raises_and_releases_lock(self, fake_llm):
        seed_agent()
        fake_llm.script(["garbage", "still garbage"])
        with pytest.raises(tuner.TunerOutputError):
            await tuner.propose("default", "agent_1", "agent")
        assert runs_mod.find_open_run("default", "agent_1") is None

    async def test_adversarial_py_edit_rejected_at_tuner_boundary(self, fake_llm):
        """3.22 — a .py / credential edit never survives the tuner boundary."""
        seed_agent()
        evil = make_diff(field_edits=[
            {"field": "backend/core/react_engine.py", "new_value": "import os"},
            {"field": "credentials", "new_value": {"api_key": "steal"}},
        ])
        fake_llm.script([json.dumps(evil), json.dumps(evil)])
        with pytest.raises(tuner.TunerOutputError):
            await tuner.propose("default", "agent_1", "agent")

    async def test_unknown_target_raises(self, fake_llm):
        with pytest.raises(tuner.TargetNotFound):
            await tuner.propose("default", "agent_missing", "agent")

    async def test_concurrent_run_conflict(self, fake_llm):
        seed_agent()
        fake_llm.script([json.dumps(make_diff())])
        await tuner.propose("default", "agent_1", "agent")
        with pytest.raises(runs_mod.RunConflict):
            await tuner.propose("default", "agent_1", "agent")


# ── 3.9 / 3.10 — versioning snapshots ────────────────────────────────────────

class TestVersioning:
    def test_snapshot_and_list(self):
        cfg = {"id": "agent_1", "system_prompt": "v1"}
        rec = versioning.snapshot_version("default", "agent_1", cfg,
                                          version_n=1, is_active=True)
        assert rec["version_n"] == 1 and rec["config"]["system_prompt"] == "v1"
        assert versioning.list_versions("default", "agent_1")[0]["is_active"]

    def test_snapshot_refuses_overwrite(self):
        versioning.snapshot_version("default", "agent_1", {"id": "agent_1"},
                                    version_n=1)
        with pytest.raises(FileExistsError):
            versioning.snapshot_version("default", "agent_1", {"id": "hacked"},
                                        version_n=1)

    def test_transfer_active_flips_bit_but_never_config(self):
        versioning.snapshot_version("default", "agent_1",
                                    {"id": "agent_1", "system_prompt": "v1"},
                                    version_n=1, is_active=True)
        versioning.snapshot_version("default", "agent_1",
                                    {"id": "agent_1", "system_prompt": "v2"},
                                    version_n=2)
        versioning.transfer_active("default", "agent_1", 2)
        v1 = versioning.load_version("default", "agent_1", 1)
        v2 = versioning.load_version("default", "agent_1", 2)
        assert not v1["is_active"] and v2["is_active"]
        assert v1["config"]["system_prompt"] == "v1"  # config untouched

    def test_next_version_n(self):
        assert versioning.next_version_n("default", "agent_1") == 1
        versioning.snapshot_version("default", "agent_1", {}, version_n=3)
        assert versioning.next_version_n("default", "agent_1") == 4


# ── 3.7 / 3.11 / 3.22 — applier boundary & mechanics ─────────────────────────

class TestApplier:
    def test_independent_allow_list_rejects_py_and_credentials(self):
        """3.22 — the applier check is its own boundary, not tuner's."""
        for field in ("backend/core/react_engine.py", "credentials",
                      "repos", "id"):
            assert applier.check_edit_allowed(field, "agent")
        assert applier.check_edit_allowed("steps[0].agent_id", "orchestration")
        assert applier.check_edit_allowed("system_prompt", "agent") is None

    def test_apply_snapshots_baseline_before_mutating(self):
        seed_agent()
        run = open_run_with_proposal()
        result = applier.apply_run("default", run["run_id"])
        v1 = versioning.load_version("default", "agent_1", 1)
        assert v1["config"]["system_prompt"] == "You are a helpful test assistant."
        assert result["object"]["version_n"] == 2
        assert result["object"]["improvement_run_id"] == run["run_id"]

    def test_apply_updates_live_store_and_closes_run(self):
        seed_agent()
        run = open_run_with_proposal()
        applier.apply_run("default", run["run_id"])
        from core.routes.agents import load_user_agents
        live = load_user_agents()[0]
        assert "Never give up" in live["system_prompt"]
        closed = runs_mod.get_run("default", run["run_id"])
        assert closed["decision"] == "keep" and closed["closed_at"]
        assert closed["new_version_n"] == 2

    def test_apply_transfers_is_active(self):
        seed_agent()
        run = open_run_with_proposal()
        applier.apply_run("default", run["run_id"])
        actives = [v["version_n"] for v in
                   versioning.list_versions("default", "agent_1")
                   if v["is_active"]]
        assert actives == [2]

    def test_apply_accepted_fields_subset(self):
        seed_agent()
        diff = make_diff(field_edits=[
            {"field": "system_prompt", "new_value": "NEW PROMPT"},
            {"field": "max_turns", "new_value": 10},
        ])
        run = open_run_with_proposal(diff)
        result = applier.apply_run("default", run["run_id"],
                                   accepted_fields=["max_turns"])
        assert result["applied_fields"] == ["max_turns"]
        live = result["object"]
        assert live["max_turns"] == 10
        assert live["system_prompt"] == "You are a helpful test assistant."

    def test_apply_rejects_out_of_scope_at_applier_boundary(self):
        """3.7 — even a proposal that slipped past the tuner is stopped here."""
        seed_agent()
        diff = make_diff(field_edits=[{"field": "repos", "new_value": ["r"]}])
        run = open_run_with_proposal(diff)
        with pytest.raises(applier.ApplyError):
            applier.apply_run("default", run["run_id"])

    def test_apply_rejects_schema_breaking_edit(self):
        """3.11 — pydantic validation gates every applied config."""
        seed_agent()
        diff = make_diff(field_edits=[
            {"field": "max_turns", "new_value": "not-a-number"}])
        run = open_run_with_proposal(diff)
        with pytest.raises(applier.ApplyError):
            applier.apply_run("default", run["run_id"])
        from core.routes.agents import load_user_agents
        assert load_user_agents()[0]["system_prompt"] == \
            "You are a helpful test assistant."

    def test_apply_closed_run_conflicts(self):
        seed_agent()
        run = open_run_with_proposal()
        applier.apply_run("default", run["run_id"])
        with pytest.raises(applier.ApplyError) as exc:
            applier.apply_run("default", run["run_id"])
        assert exc.value.status == 409

    def test_reject_closes_without_applying(self):
        seed_agent()
        run = open_run_with_proposal()
        applier.reject_run("default", run["run_id"])
        from core.routes.agents import load_user_agents
        assert load_user_agents()[0]["system_prompt"] == \
            "You are a helpful test assistant."
        assert runs_mod.find_open_run("default", "agent_1") is None

    def test_rollback_restores_previous_config(self):
        seed_agent()
        run = open_run_with_proposal()
        applier.apply_run("default", run["run_id"])
        result = applier.rollback("default", "agent_1", 1)
        assert result["restored_version_n"] == 1
        from core.routes.agents import load_user_agents
        live = load_user_agents()[0]
        assert live["system_prompt"] == "You are a helpful test assistant."
        assert live["version_n"] == 1
        actives = [v["version_n"] for v in
                   versioning.list_versions("default", "agent_1")
                   if v["is_active"]]
        assert actives == [1]

    def test_rollback_unknown_version_404(self):
        seed_agent()
        with pytest.raises(applier.ApplyError) as exc:
            applier.rollback("default", "agent_1", 99)
        assert exc.value.status == 404

    def test_apply_then_rollback_then_apply_never_collides(self):
        """Post-rollback applies allocate fresh version numbers."""
        seed_agent()
        run = open_run_with_proposal()
        applier.apply_run("default", run["run_id"])          # v2
        applier.rollback("default", "agent_1", 1)            # live back to v1
        run2 = open_run_with_proposal(
            make_diff(field_edits=[{"field": "max_turns", "new_value": 5}]))
        result = applier.apply_run("default", run2["run_id"])
        assert result["object"]["version_n"] == 3            # not a v2 clash

    def test_orchestration_step_edit_applies(self):
        orch = seed.make_orchestration(
            id="orch_1",
            steps=[{"id": "s1", "name": "Step", "type": "agent",
                    "agent_id": "agent_1", "prompt_template": "old {state.x}",
                    "next_step_id": None}],
        )
        seed.seed_orchestrations([orch])
        diff = make_diff(
            target_object_id="orch_1", target_kind="orchestration",
            field_edits=[{"field": "steps[0].prompt_template",
                          "new_value": "new and improved {state.x}"}])
        run = open_run_with_proposal(diff, target_kind="orchestration",
                                     object_id="orch_1")
        result = applier.apply_run("default", run["run_id"])
        assert result["object"]["steps"][0]["prompt_template"] == \
            "new and improved {state.x}"
        assert result["object"]["version_n"] == 2
