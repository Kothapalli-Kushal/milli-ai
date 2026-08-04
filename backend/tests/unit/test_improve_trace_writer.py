"""
Checkpoint-1 verification for the Self-Improvement trace subsystem
(core/improve/trace_writer.py): trace emission from real chat / orchestration
/ delegate runs, success derivation, usage join, retention, rotation, and
model back-compat. Driven entirely by the fake LLM — no network, no keys.
"""
import json
import os
import time
import types

import pytest

from _fakes import seed as S
from _fakes.fake_llm import tool_call

from core.config import DATA_DIR
from core.improve.trace_writer import (
    DEFAULT_TRACE_RETENTION_DAYS,
    GIVE_UP_RE,
    TraceWriter,
    derive_success,
    ensure_user_layout,
    purge_expired_traces,
    resolve_user_id,
)


def _server():
    return types.SimpleNamespace(agent_sessions={}, memory_store=None, tool_router={})


def _traces_for(object_id: str) -> list[dict]:
    """All trace files written for an object, sorted by filename."""
    base = os.path.join(DATA_DIR, "improve", resolve_user_id(), "traces", object_id)
    out = []
    if not os.path.isdir(base):
        return out
    for month in sorted(os.listdir(base)):
        month_dir = os.path.join(base, month)
        for name in sorted(os.listdir(month_dir)):
            with open(os.path.join(month_dir, name), encoding="utf-8") as f:
                out.append(json.load(f))
    return out


REQUIRED_TRACE_KEYS = {
    "session_id", "timestamp", "duration_s", "success", "error", "output",
    "agent_id", "orchestration_id", "run_id", "model", "metadata", "messages",
    "usage",
}


class TestChatTrace:
    """1.13 — a plain chat run produces a valid trace."""

    async def test_chat_run_writes_valid_trace(self, fake_llm):
        agent = S.make_agent(id="chat_agent", tools=[], skip_default_tools=True)
        from core.react_engine import run_agent_step
        fake_llm.script(["Here is your answer."])
        events = [ev async for ev in run_agent_step(
            message="hello", agent_id=agent["id"], session_id="sess_chat",
            server_module=_server(), agent_override=agent,
            tools_override=[], max_turns=2)]
        assert any(e.get("type") == "final" for e in events)

        traces = _traces_for("chat_agent")
        assert len(traces) == 1
        t = traces[0]
        assert REQUIRED_TRACE_KEYS <= set(t.keys())
        assert t["session_id"] == "sess_chat"
        assert t["agent_id"] == "chat_agent"
        assert t["success"] is True
        assert t["error"] is None
        assert t["output"] == "Here is your answer."
        assert t["metadata"]["kind"] == "agent"
        assert t["metadata"]["source"] == "chat"
        assert t["messages"][0] == {
            "role": "user", "content": "hello",
            "timestamp": t["messages"][0]["timestamp"],
        }
        assert t["messages"][-1]["role"] == "assistant"

    async def test_tool_calls_recorded_in_transcript(self, fake_llm):
        agent = S.make_agent(id="tool_agent", tools=["all"], skip_default_tools=True)
        schema = {"type": "function", "function": {
            "name": "my_tool", "description": "t",
            "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}}

        async def executor(name, args):
            return "TOOL-OUT"

        from core.react_engine import run_agent_step
        fake_llm.script([tool_call("my_tool", x=1), "final answer"])
        [ev async for ev in run_agent_step(
            message="use the tool", agent_id=agent["id"], session_id="sess_tool",
            server_module=_server(), agent_override=agent,
            tools_override=[schema], tool_executor=executor, max_turns=3)]

        t = _traces_for("tool_agent")[0]
        calls = [m for m in t["messages"] if m.get("tool_calls")]
        assert len(calls) == 1
        fn = calls[0]["tool_calls"][0]["function"]
        assert fn["name"] == "my_tool"
        assert json.loads(fn["arguments"]) == {"x": 1}
        tool_msgs = [m for m in t["messages"] if m["role"] == "tool"]
        assert tool_msgs and tool_msgs[0]["tool_call_id"] == calls[0]["tool_calls"][0]["id"]

    async def test_same_session_twice_keeps_both_traces(self, fake_llm):
        agent = S.make_agent(id="twice_agent", tools=[], skip_default_tools=True)
        from core.react_engine import run_agent_step
        for text in ("first", "second"):
            fake_llm.script([text])
            [ev async for ev in run_agent_step(
                message="go", agent_id=agent["id"], session_id="same_sess",
                server_module=_server(), agent_override=agent,
                tools_override=[], max_turns=2)]
        traces = _traces_for("twice_agent")
        assert len(traces) == 2
        assert {t["output"] for t in traces} == {"first", "second"}
        assert all(t["session_id"] == "same_sess" for t in traces)


class TestOrchestrationTrace:
    """1.14 — an orchestration run produces a valid trace."""

    async def test_orchestration_run_writes_valid_trace(self, fake_llm):
        from core.models_orchestration import Orchestration
        from core.orchestration.engine import OrchestrationEngine
        orch_dict = S.make_orchestration(id="orch_trace_1")
        orch = Orchestration.model_validate(orch_dict)
        events = [ev async for ev in OrchestrationEngine(orch, _server()).run(
            "run the workflow", run_id="run_orch_1")]
        assert any(e.get("type") == "orchestration_complete" for e in events)

        traces = _traces_for("orch_trace_1")
        assert len(traces) == 1
        t = traces[0]
        assert REQUIRED_TRACE_KEYS <= set(t.keys())
        assert t["orchestration_id"] == "orch_trace_1"
        assert t["agent_id"] is None
        assert t["run_id"] == "run_orch_1"
        assert t["success"] is True
        assert t["metadata"]["kind"] == "orchestration"
        step_msgs = [m["content"] for m in t["messages"] if m["role"] == "system"]
        assert any(c.startswith("step_start") for c in step_msgs)
        assert any(c.startswith("step_complete") for c in step_msgs)

    async def test_agent_step_inside_orch_gets_step_context(self, fake_llm):
        from core.models_orchestration import Orchestration
        from core.orchestration.engine import OrchestrationEngine
        agent = S.make_agent(id="orch_step_agent", tools=[], skip_default_tools=True)
        S.seed_agents([agent])
        orch_dict = S.make_orchestration(
            id="orch_with_agent", entry_step_id="a1",
            steps=[{"id": "a1", "name": "Agent step", "type": "agent",
                    "agent_id": "orch_step_agent", "prompt_template": "{state.user_input}",
                    "output_key": "result", "next_step_id": None}],
        )
        fake_llm.set_default("agent step done")
        orch = Orchestration.model_validate(orch_dict)
        [ev async for ev in OrchestrationEngine(orch, _server()).run(
            "do it", run_id="run_orch_agent")]

        agent_traces = _traces_for("orch_step_agent")
        assert len(agent_traces) == 1
        t = agent_traces[0]
        assert t["orchestration_id"] == "orch_with_agent"
        assert t["metadata"]["step_id"] == "a1"
        assert t["run_id"] == "run_orch_agent"


class TestDelegateTrace:
    """1.15 — delegate flow sets delegated_from / parent_session_id."""

    async def test_delegate_subrun_links_to_parent(self, fake_llm):
        target = S.make_agent(id="worker", name="Worker", description="does work",
                              tools=[], skip_default_tools=True)
        lead = S.make_agent(id="lead", name="Lead", type="delegate",
                            delegate_agent_ids=["worker"], tools=["all"])
        S.seed_agents([target, lead])
        from core.react_engine import run_agent_step
        fake_llm.script([
            tool_call("delegate_to_agent", agent_id="worker", task="do the work"),
            "worker finished the work",   # consumed by the nested worker run
            "lead final answer",
        ])
        events = [ev async for ev in run_agent_step(
            message="handle this", agent_id="lead", session_id="sess_lead",
            server_module=_server(), max_turns=3)]
        assert any(e.get("type") == "final" for e in events)

        lead_traces = _traces_for("lead")
        worker_traces = _traces_for("worker")
        assert len(lead_traces) == 1 and len(worker_traces) == 1
        lt, wt = lead_traces[0], worker_traces[0]
        assert "delegated_from" not in lt["metadata"]
        assert wt["metadata"]["delegated_from"] == "lead"
        assert wt["metadata"]["parent_session_id"] == lt["session_id"]


class TestSuccessDerivation:
    """1.10 — success = final ∧ no error event ∧ no give-up in last assistant msg."""

    def test_clean_final_is_success(self):
        msgs = [{"role": "assistant", "content": "All done."}]
        assert derive_success(True, None, False, msgs) is True

    def test_no_final_fails(self):
        assert derive_success(False, None, False, []) is False

    def test_error_event_fails(self):
        msgs = [{"role": "assistant", "content": "All done."}]
        assert derive_success(True, None, True, msgs) is False
        assert derive_success(True, "boom", False, msgs) is False

    @pytest.mark.parametrize("text", [
        "I can't do that with the tools available.",
        "I cannot complete this request.",
        "I am unable to proceed further.",
        "I give up on this task.",
        "As an AI language model I have limits.",
    ])
    def test_give_up_phrases_fail(self, text):
        assert GIVE_UP_RE.search(text)
        assert derive_success(True, None, False, [{"role": "assistant", "content": text}]) is False

    def test_neutral_final_passes_regex(self):
        text = "The deployment completed and all checks passed."
        assert not GIVE_UP_RE.search(text)


class TestUsageJoin:
    """1.11 — tokens/cost joined from usage_tracker by run_id, never re-counted."""

    def test_join_by_run_id(self):
        from core.usage_tracker import log_usage
        log_usage(model="test-model", provider="test", input_tokens=100,
                  output_tokens=50, context_chars=400, session_id="s_join",
                  agent_id="join_agent", source="orchestration", run_id="run_join_1")
        log_usage(model="test-model", provider="test", input_tokens=10,
                  output_tokens=5, context_chars=40, session_id="s_other",
                  agent_id="other_agent", source="chat", run_id="run_OTHER")

        w = TraceWriter(kind="agent", object_id="join_agent",
                        session_id="s_join", run_id="run_join_1")
        w.record_event({"type": "final", "response": "ok"})
        t = w.build_trace()
        assert t["usage"]["input_tokens"] == 100
        assert t["usage"]["output_tokens"] == 50
        assert t["usage"]["llm_calls"] == 1
        assert t["model"] == "test-model"

    def test_chat_fallback_joins_by_session_and_agent(self):
        from core.usage_tracker import log_usage
        w = TraceWriter(kind="agent", object_id="fb_agent", session_id="s_fb")
        log_usage(model="fb-model", provider="test", input_tokens=7,
                  output_tokens=3, context_chars=30, session_id="s_fb",
                  agent_id="fb_agent", source="chat", run_id=None)
        w.record_event({"type": "final", "response": "ok"})
        t = w.build_trace()
        assert t["usage"]["total_tokens"] == 10
        assert t["model"] == "fb-model"


class TestRetentionAndLayout:
    """1.5 / 1.12 — per-user layout, month rotation, retention purge."""

    def test_ensure_user_layout_creates_documented_tree(self):
        base = ensure_user_layout("layout_user")
        for sub in ("traces", "benchmarks", "versions"):
            assert os.path.isdir(os.path.join(base, sub))
        for index_file in ("runs.json", "inbox.json"):
            with open(os.path.join(base, index_file), encoding="utf-8") as f:
                assert json.load(f) == []

    def test_trace_path_rotates_by_month(self):
        w = TraceWriter(kind="agent", object_id="rot_agent", session_id="s_rot")
        w.record_event({"type": "final", "response": "ok"})
        w.close()
        month = time.strftime("%Y-%m", time.gmtime())
        month_dir = os.path.join(DATA_DIR, "improve", resolve_user_id(),
                                 "traces", "rot_agent", month)
        assert os.path.isdir(month_dir) and os.listdir(month_dir)

    def test_purge_removes_files_older_than_retention(self):
        base = os.path.join(DATA_DIR, "improve", "purge_user", "traces", "old_agent")
        old_dir = os.path.join(base, "2020-01")
        os.makedirs(old_dir, exist_ok=True)
        old_file = os.path.join(old_dir, "ancient.json")
        with open(old_file, "w", encoding="utf-8") as f:
            f.write("{}")
        past = time.time() - (DEFAULT_TRACE_RETENTION_DAYS + 5) * 86400
        os.utime(old_file, (past, past))

        removed = purge_expired_traces(base, DEFAULT_TRACE_RETENTION_DAYS)
        assert removed == 1
        assert not os.path.exists(old_file)
        assert not os.path.isdir(old_dir)  # empty month dir cleaned up

    def test_fresh_files_survive_purge(self):
        base = os.path.join(DATA_DIR, "improve", "purge_user2", "traces", "new_agent")
        month_dir = os.path.join(base, time.strftime("%Y-%m", time.gmtime()))
        os.makedirs(month_dir, exist_ok=True)
        fresh = os.path.join(month_dir, "fresh.json")
        with open(fresh, "w", encoding="utf-8") as f:
            f.write("{}")
        assert purge_expired_traces(base, DEFAULT_TRACE_RETENTION_DAYS) == 0
        assert os.path.exists(fresh)


class TestBackCompat:
    """1.4 — pre-existing JSON without the new fields loads unchanged."""

    def test_agent_model_defaults(self):
        from core.models import Agent
        legacy = {"id": "a1", "name": "Legacy", "description": "d",
                  "tools": [], "system_prompt": "p"}
        a = Agent.model_validate(legacy)
        assert a.parent_id is None
        assert a.version_n == 1
        assert a.is_active is True
        assert a.improvement_run_id is None
        assert a.metric_snapshot is None
        assert a.trace_retention_days is None

    def test_orchestration_model_defaults(self):
        from core.models_orchestration import Orchestration
        legacy = {"id": "o1", "name": "Legacy Orch"}
        o = Orchestration.model_validate(legacy)
        assert o.parent_id is None
        assert o.version_n == 1
        assert o.is_active is True
        assert o.improvement_run_id is None
        assert o.metric_snapshot is None


class TestFailureIsolation:
    """1.18 — tracing never alters events or raises into the run."""

    async def test_async_context_manager_writes_on_exit(self):
        async with TraceWriter(kind="agent", object_id="acm_agent",
                               session_id="s_acm") as w:
            w.record_event({"type": "final", "response": "done via acm"})
        traces = _traces_for("acm_agent")
        assert len(traces) == 1 and traces[0]["output"] == "done via acm"

    async def test_async_context_manager_captures_exception(self):
        with pytest.raises(ValueError):
            async with TraceWriter(kind="agent", object_id="acm_err_agent",
                                   session_id="s_acm_err") as w:
                w.record_event({"type": "llm_thought", "thought": "hm"})
                raise ValueError("boom")
        t = _traces_for("acm_err_agent")[0]
        assert t["success"] is False and "boom" in t["error"]

    def test_record_event_swallows_bad_events(self):
        w = TraceWriter(kind="agent", object_id="iso_agent", session_id="s_iso")
        w.record_event(None)
        w.record_event("not a dict")
        w.record_event({"type": "tool_execution"})  # missing fields
        w.close()  # must not raise
        w.close()  # idempotent

    async def test_events_pass_through_unmodified(self, fake_llm):
        agent = S.make_agent(id="passthru_agent", tools=[], skip_default_tools=True)
        from core.react_engine import run_agent_step
        fake_llm.script(["untouched final"])
        events = [ev async for ev in run_agent_step(
            message="go", agent_id=agent["id"], session_id="s_pass",
            server_module=_server(), agent_override=agent,
            tools_override=[], max_turns=2)]
        finals = [e for e in events if e.get("type") == "final"]
        assert finals == [{"type": "final", "response": "untouched final",
                           "intent": finals[0]["intent"], "data": finals[0]["data"],
                           "tool_name": finals[0]["tool_name"]}]
