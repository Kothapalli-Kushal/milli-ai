"""
Orchestration engine — real execution of representative step types.

Unlike the route tests (which patch the engine), these drive the REAL
OrchestrationEngine so the step executors, shared-state plumbing, and — for the
LLM step — the fake-LLM interception are all exercised end to end. Checkpoints
and logs land in the sandbox DATA_DIR.
"""
import types

import pytest

from _fakes import seed as S
from core.orchestration.steps import _merge_table_schema_context


def _server_module():
    """A minimal stand-in for core.server that step executors can read."""
    return types.SimpleNamespace(agent_sessions={}, memory_store=None, tool_router={})


def test_later_table_schema_expands_schema_context():
    original = """RELEVANT TABLES:
- dbo.tblFundLiabilityValue: original fact table
NOTES:
- dbo.tblFundFCLValue may be relevant but has not been inspected.
"""
    tool_output = """**Table: dbo.tblFundFCLValue**
Columns:
- FundFCLValueID (int) NOT NULL (PK)
- FundID (int) NOT NULL

**Table: dbo.tblFundLiabilityValue**
Columns:
- FundLiabilityValueID (int) NOT NULL (PK)"""

    merged = _merge_table_schema_context(original, tool_output)

    assert "**Table: dbo.tblFundFCLValue**" in merged
    assert "FundFCLValueID (int)" in merged
    assert "**Table: dbo.tblFundLiabilityValue**" not in merged
    assert _merge_table_schema_context(merged, tool_output) == merged


async def _run(orch_dict, initial_input="hello", initial_state=None):
    from core.models_orchestration import Orchestration
    from core.orchestration.engine import OrchestrationEngine
    orch = Orchestration.model_validate(orch_dict)
    engine = OrchestrationEngine(orch, _server_module())
    events = []
    async for ev in engine.run(
        initial_input,
        run_id=f"run_test_{orch.id}",
        initial_state=initial_state,
    ):
        events.append(ev)
    return events


async def test_write_sql_schema_lookup_updates_shared_context(monkeypatch):
    import core.react_engine as react_engine

    agent = S.make_agent(type="code", tools=["get_table_schema"])
    S.seed_agents([agent])
    tool_output = """**Table: dbo.tblFundFCLValue**
Columns:
- FundFCLValueID (int) NOT NULL (PK)"""

    async def fake_run_agent_step(**kwargs):
        async for event in kwargs["post_tool_hook"]("get_table_schema", tool_output):
            yield event
        yield {"type": "final", "response": "ACTION: EXECUTE"}

    monkeypatch.setattr(react_engine, "run_agent_step", fake_run_agent_step)
    orch = S.make_orchestration(
        entry_step_id="write_sql",
        steps=[{
            "id": "write_sql",
            "name": "Write SQL",
            "type": "agent",
            "agent_id": agent["id"],
            "prompt_template": "Inspect another table if needed.",
            "output_key": "draft_sql",
            "next_step_id": None,
        }],
    )

    events = await _run(
        orch,
        initial_state={"schema_context": "RELEVANT TABLES:\n- dbo.aFund: fund lookup"},
    )

    update = next(event for event in events if event.get("type") == "schema_context_updated")
    assert update["orch_step_id"] == "write_sql"
    completed = next(event for event in events if event.get("type") == "orchestration_complete")
    assert "**Table: dbo.tblFundFCLValue**" in completed["final_state"]["schema_context"]


class TestPrintStep:
    async def test_print_step_runs_to_completion(self):
        # make_orchestration defaults to a single PRINT step (no LLM).
        orch = S.make_orchestration()
        events = await _run(orch)
        types_seen = [e.get("type") for e in events]
        assert "orchestration_start" in types_seen
        assert "orchestration_complete" in types_seen


class TestLLMStep:
    async def test_llm_step_uses_fake_llm(self, fake_llm):
        fake_llm.set_default("SUMMARY: all good")
        orch = S.make_orchestration(
            entry_step_id="llm1",
            steps=[{
                "id": "llm1",
                "name": "Summarize",
                "type": "llm",
                "prompt_template": "Summarize: {state.user_input}",
                "output_key": "summary",
                "model": "claude-test",
                "next_step_id": None,
            }],
        )
        events = await _run(orch, initial_input="a long document")
        types_seen = [e.get("type") for e in events]
        assert "orchestration_complete" in types_seen
        # The fake LLM was actually invoked for the LLM step.
        assert fake_llm.call_count >= 1


class TestTwoStepChain:
    async def test_llm_then_print_chain(self, fake_llm):
        fake_llm.set_default("draft text")
        orch = S.make_orchestration(
            entry_step_id="s_llm",
            steps=[
                {
                    "id": "s_llm", "name": "Draft", "type": "llm",
                    "prompt_template": "Write about {state.user_input}",
                    "output_key": "draft", "model": "claude-test",
                    "next_step_id": "s_print",
                },
                {
                    "id": "s_print", "name": "Show", "type": "print",
                    "print_content": "Result: {state.draft}",
                    "output_key": "shown", "next_step_id": None,
                },
            ],
        )
        events = await _run(orch, initial_input="cats")
        types_seen = [e.get("type") for e in events]
        assert types_seen.count("step_start") >= 2  # both steps ran
        assert "orchestration_complete" in types_seen
