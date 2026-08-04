"""
Verify db_id propagation from the ORCHESTRATION entry point.

Drives the REAL OrchestrationEngine -> AgentStepExecutor -> run_agent_step
chain (no engine patching) and checks that:

  1. _inject_db_context is actually invoked for an agent step, and
  2. the resulting system prompt sent to the LLM contains the
     "### LINKED DATABASES ###" block with the DB ID the model must pass
     as `db_id` in SQL tool calls.

If injection is skipped anywhere along this path, the LLM never learns any
db_id and run_sql_query later fails with "No db_id provided".
"""
import types

import pytest

from _fakes import seed as S


DB_ID = "db_test_1"


def _server_module():
    return types.SimpleNamespace(agent_sessions={}, memory_store=None, tool_router={})


def _seed_db_config():
    from core.routes.db_configs import save_db_configs
    save_db_configs([{
        "id": DB_ID,
        "name": "Test DB",
        "db_type": "mssql",
        "connection_string": "mssql+pyodbc:///?odbc_connect=DATABASE%3DTESTDB%3B",
        "description": "A test database.",
        "schema_info": "Tables:\n  dbo.users(id (int), name (varchar))",
    }])


def _seed_code_agent_linked_to_db():
    agent = S.make_agent(
        type="code",
        tools=[],
        db_configs=[DB_ID],
        system_prompt="You are a code agent.",
    )
    S.seed_agents([agent])
    return agent


def _orchestration_with_agent_step(agent_id: str) -> dict:
    return S.make_orchestration(
        entry_step_id="s_agent",
        steps=[{
            "id": "s_agent",
            "name": "Query DB",
            "type": "agent",
            "agent_id": agent_id,
            "prompt_template": "How many users are there?",
            "output_key": "answer",
            "next_step_id": None,
        }],
    )


async def _run(orch_dict, initial_input="go"):
    from core.models_orchestration import Orchestration
    from core.orchestration.engine import OrchestrationEngine
    orch = Orchestration.model_validate(orch_dict)
    engine = OrchestrationEngine(orch, _server_module())
    return [ev async for ev in engine.run(initial_input, run_id=f"run_test_{orch.id}")]


class TestOrchestrationInjectsDbContext:
    async def test_inject_db_context_called_from_agent_step(self, fake_llm, monkeypatch):
        """The orchestration agent-step path must call _inject_db_context with
        the resolved agent (carrying its db_configs)."""
        import core.react_engine as re

        _seed_db_config()
        agent = _seed_code_agent_linked_to_db()

        seen = []
        real_inject = re._inject_db_context

        def spy(agent_data, system_template):
            out = real_inject(agent_data, system_template)
            seen.append({"agent_id": agent_data.get("id"),
                         "db_configs": agent_data.get("db_configs"),
                         "injected": out != system_template})
            return out

        monkeypatch.setattr(re, "_inject_db_context", spy)

        fake_llm.set_default("There are 42 users.")
        events = await _run(_orchestration_with_agent_step(agent["id"]))

        assert any(e.get("type") == "orchestration_complete" for e in events), \
            f"orchestration did not complete: {[e.get('type') for e in events]}"
        assert seen, "_inject_db_context was never called from the orchestration path"
        call = seen[0]
        assert call["agent_id"] == agent["id"]
        assert call["db_configs"] == [DB_ID]
        assert call["injected"], (
            "_inject_db_context ran but returned the template unchanged — "
            "DB context was NOT injected for a code agent with linked db_configs"
        )

    async def test_system_prompt_sent_to_llm_contains_db_id(self, fake_llm):
        """End-to-end: the sys_prompt the LLM actually receives during an
        orchestration agent step must contain the LINKED DATABASES block and
        the exact db_id the model is told to pass to SQL tools."""
        _seed_db_config()
        agent = _seed_code_agent_linked_to_db()

        fake_llm.set_default("There are 42 users.")
        events = await _run(_orchestration_with_agent_step(agent["id"]))

        assert any(e.get("type") == "orchestration_complete" for e in events)
        assert fake_llm.call_count >= 1, "fake LLM was never invoked"

        sys_prompts = [c.get("sys_prompt", "") for c in fake_llm.calls]
        matching = [p for p in sys_prompts if "### LINKED DATABASES ###" in p]
        assert matching, (
            "No LLM call received the LINKED DATABASES block — db context was "
            f"dropped on the orchestration path. sys_prompt heads: "
            f"{[p[:120] for p in sys_prompts]}"
        )
        assert any(DB_ID in p for p in matching), (
            f"LINKED DATABASES block present but db_id '{DB_ID}' missing from it"
        )
        assert any("pass this as db_id" in p for p in matching), (
            "LINKED DATABASES block does not instruct the model to pass db_id"
        )

    async def test_no_injection_when_agent_has_no_db_configs(self, fake_llm):
        """Control: an agent with empty db_configs gets no LINKED DATABASES
        block, proving the positive tests assert real injection (not noise)."""
        _seed_db_config()
        agent = S.make_agent(type="code", tools=[], db_configs=[])
        S.seed_agents([agent])

        fake_llm.set_default("done")
        events = await _run(_orchestration_with_agent_step(agent["id"]))

        assert any(e.get("type") == "orchestration_complete" for e in events)
        assert all(
            "### LINKED DATABASES ###" not in c.get("sys_prompt", "")
            for c in fake_llm.calls
        )
