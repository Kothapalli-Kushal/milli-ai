"""
Checkpoint-2 verification: detector unit corpus (positive + negative per
detector, pure dicts only), runner aggregation, insight evidence pointers,
report determinism, read-only guarantee, and the auth-scoped REST endpoint.
"""
import json
import os
import shutil

import pytest

from core.improve.detectors import (
    DETECTORS,
    TOKEN_USAGE_FLAG_THRESHOLD,
    browser_state_stale_rate,
    clean_success,
    compaction_thrash,
    delegate_pingpong,
    duration_outlier,
    errors,
    give_up,
    hallucinated_tool_rate,
    loops,
    mcp_ping_timeout_rate,
    recovery,
    sequentialthinking_cap_hit,
    sticky_arg_conflict,
    token_usage,
)
from core.improve.insights import extract_insights
from core.improve.runner import build_report, corpus_duration_stats, load_traces


@pytest.fixture(autouse=True)
def _clean_improve_dir():
    """Trace files from other test modules must not leak into this corpus."""
    from core.config import DATA_DIR
    shutil.rmtree(os.path.join(DATA_DIR, "improve"), ignore_errors=True)
    yield


# ── trace-dict builders (no I/O — checklist 2.3/2.4) ─────────────────────────

def _call(name, **args):
    return {"role": "assistant", "content": "", "timestamp": "2026-01-01T00:00:00Z",
            "tool_calls": [{"id": "c", "function": {
                "name": name, "arguments": json.dumps(args, sort_keys=True)}}]}


def _tool(content):
    return {"role": "tool", "content": content, "timestamp": "2026-01-01T00:00:00Z",
            "tool_call_id": "c"}


def _assist(content):
    return {"role": "assistant", "content": content, "timestamp": "2026-01-01T00:00:00Z"}


def make_trace(messages=None, *, success=True, error=None, duration=1.0,
               usage_tokens=100, agent_id="a1", orchestration_id=None,
               model="m1", session_id="s", compactions=0):
    return {
        "session_id": session_id, "timestamp": "2026-01-01T00:00:00Z",
        "duration_s": duration, "success": success, "error": error,
        "output": "done", "agent_id": agent_id,
        "orchestration_id": orchestration_id, "run_id": None, "model": model,
        "metadata": {"kind": "agent", "source": "chat"},
        "messages": messages if messages is not None else [_assist("All done.")],
        "usage": {"input_tokens": usage_tokens, "output_tokens": 0,
                  "total_tokens": usage_tokens, "estimated_cost_usd": 0.0,
                  "llm_calls": 1},
        "compaction_events": [{"stage": "trim"}] * compactions,
    }


class TestRIDetectors:
    def test_loops_positive_and_negative(self):
        looping = make_trace([_call("t", x=1), _call("t", x=1), _call("t", x=1)])
        res = loops(looping)
        assert res["hit"] and res["value"] == 3 and len(res["evidence"]) == 3
        varied = make_trace([_call("t", x=1), _call("t", x=2), _call("t", x=3)])
        assert not loops(varied)["hit"]
        assert not loops(make_trace([_assist("hi")]))["applicable"]

    def test_give_up_positive_and_negative(self):
        assert give_up(make_trace([_assist("I can't do that.")]))["hit"]
        assert not give_up(make_trace([_assist("Deployed successfully.")]))["hit"]
        assert not give_up(make_trace([_call("t", x=1)]))["applicable"]

    def test_errors_positive_and_negative(self):
        res = errors(make_trace([_tool("Error: boom"), _assist("ok")]))
        assert res["hit"] and res["evidence"] == [0]
        assert errors(make_trace(error="run failed"))["hit"]
        assert not errors(make_trace([_assist("fine")]))["hit"]

    def test_recovery_positive_and_negative(self):
        recovered = make_trace([_tool("Error: transient"), _assist("fixed it")], success=True)
        assert recovery(recovered)["hit"]
        died = make_trace([_tool("Error: fatal")], success=False)
        assert recovery(died)["applicable"] and not recovery(died)["hit"]
        assert not recovery(make_trace([_assist("no errors here")]))["applicable"]

    def test_clean_success_positive_and_negative(self):
        assert clean_success(make_trace([_assist("Task complete.")]))["hit"]
        assert not clean_success(make_trace([_tool("Error: x"), _assist("done")]))["hit"]
        assert not clean_success(make_trace([_assist("I give up.")]))["hit"]

    def test_duration_outlier_positive_and_negative(self):
        stats = {"mean": 1.0, "std": 0.5, "n": 10}
        assert duration_outlier(make_trace(duration=5.0), stats)["hit"]
        assert not duration_outlier(make_trace(duration=1.2), stats)["hit"]
        # No corpus stats → explicitly not applicable (never a silent zero)
        assert not duration_outlier(make_trace(duration=5.0), None)["applicable"]

    def test_token_usage_positive_and_negative(self):
        heavy = make_trace(usage_tokens=TOKEN_USAGE_FLAG_THRESHOLD + 1)
        assert token_usage(heavy)["hit"]
        assert not token_usage(make_trace(usage_tokens=10))["hit"]
        no_usage = make_trace()
        no_usage["usage"] = {}
        assert not token_usage(no_usage)["applicable"]


class TestSynapseDetectors:
    def test_sequentialthinking_cap_positive_and_negative(self):
        capped = make_trace([
            _call("sequentialthinking", thoughtNumber=6),
            _tool("Blocked: sequentialthinking already used 5 times (call a real tool now)"),
        ])
        assert sequentialthinking_cap_hit(capped)["hit"]
        under_cap = make_trace([_call("sequentialthinking", thoughtNumber=1), _tool("ok")])
        assert not sequentialthinking_cap_hit(under_cap)["hit"]
        assert not sequentialthinking_cap_hit(make_trace([_call("other")]))["applicable"]

    def test_hallucinated_tool_positive_and_negative(self):
        bad = make_trace([_call("ghost_tool"), _tool("Blocked: Tool not available for this agent"),
                          _call("gone_tool"), _tool("Error: tool not found")])
        res = hallucinated_tool_rate(bad)
        assert res["hit"] and res["value"] == 1.0 and res["evidence"] == [1, 3]
        good = make_trace([_call("real_tool"), _tool("result data")])
        assert not hallucinated_tool_rate(good)["hit"]
        assert not hallucinated_tool_rate(make_trace([_assist("no calls")]))["applicable"]

    def test_compaction_thrash_positive_and_negative(self):
        assert compaction_thrash(make_trace(compactions=2))["hit"]
        assert not compaction_thrash(make_trace(compactions=1))["hit"]
        assert compaction_thrash(make_trace(compactions=0))["applicable"]

    def test_sticky_arg_conflict_positive_and_negative(self):
        flip = make_trace([_call("q", db="prod"), _call("q", db="staging"), _call("q", db="prod")])
        res = sticky_arg_conflict(flip)
        assert res["hit"] and len(res["evidence"]) == 3
        stable = make_trace([_call("q", db="prod"), _call("q", db="prod"), _call("q", db="prod")])
        assert sticky_arg_conflict(stable)["applicable"] and not sticky_arg_conflict(stable)["hit"]
        assert not sticky_arg_conflict(make_trace([_call("q", db="prod")]))["applicable"]

    def test_delegate_pingpong_positive_and_negative(self):
        pingpong = make_trace([_call("delegate_to_agent", agent_id="w", task="1"),
                               _call("delegate_to_agent", agent_id="w", task="2"),
                               _call("delegate_to_agent", agent_id="w", task="3")])
        assert delegate_pingpong(pingpong)["hit"]
        once = make_trace([_call("delegate_to_agent", agent_id="w", task="1")])
        assert delegate_pingpong(once)["applicable"] and not delegate_pingpong(once)["hit"]
        assert not delegate_pingpong(make_trace([_call("other")]))["applicable"]

    def test_mcp_ping_timeout_positive_and_negative(self):
        timed_out = make_trace([_call("mcp_tool"), _tool("Error: session unresponsive")])
        assert mcp_ping_timeout_rate(timed_out)["hit"]
        healthy = make_trace([_call("mcp_tool"), _tool("fine")])
        assert not mcp_ping_timeout_rate(healthy)["hit"]
        assert not mcp_ping_timeout_rate(make_trace([_assist("x")]))["applicable"]

    def test_browser_stale_positive_and_negative(self):
        stale = make_trace([_call("browser_click", selector="#btn"),
                            _tool("Error: element not found on page")])
        assert browser_state_stale_rate(stale)["hit"]
        fresh = make_trace([_call("browser_click", selector="#btn"), _tool("clicked ok")])
        assert not browser_state_stale_rate(fresh)["hit"]
        # Non-browser errors don't count against browser staleness
        other = make_trace([_call("browser_open", url="u"), _tool("page loaded"),
                            _call("sql_query", q="x"), _tool("Error: not found")])
        assert not browser_state_stale_rate(other)["hit"]
        assert not browser_state_stale_rate(make_trace([_call("sql_query")]))["applicable"]


class TestPurity:
    """2.3 — detectors never mutate their input and are deterministic."""

    def test_detectors_do_not_mutate_trace(self):
        trace = make_trace([_call("t", x=1), _tool("Error: boom"), _assist("I give up.")],
                           compactions=2)
        snapshot = json.dumps(trace, sort_keys=True)
        stats = {"mean": 1.0, "std": 0.5, "n": 5}
        for name, fn in DETECTORS.items():
            fn(trace, stats) if name == "duration_outlier" else fn(trace)
        assert json.dumps(trace, sort_keys=True) == snapshot

    def test_detectors_deterministic(self):
        trace = make_trace([_call("t", x=1), _call("t", x=1), _call("t", x=1),
                            _tool("Error: x"), _assist("I can't proceed.")])
        stats = {"mean": 1.0, "std": 0.5, "n": 5}
        for name, fn in DETECTORS.items():
            args = (trace, stats) if name == "duration_outlier" else (trace,)
            assert fn(*args) == fn(*args)


# ── seeded corpus on disk for runner / insights / API tests ─────────────────

def _seed_traces(user_id="default"):
    """Write a small mixed corpus into the (sandboxed) improve storage."""
    from core.config import DATA_DIR
    corpus = [
        ("agent_x", make_trace([_call("t", x=1), _call("t", x=1), _call("t", x=1),
                                _assist("done")],
                               agent_id="agent_x", model="model_a", session_id="t1")),
        ("agent_x", make_trace([_assist("I can't complete this.")],
                               success=False, agent_id="agent_x", model="model_a",
                               session_id="t2")),
        ("agent_y", make_trace([_call("sequentialthinking", n=1),
                                _tool("Blocked: sequentialthinking already used 5 times"),
                                _call("ghost"), _tool("Error: tool not found"),
                                _tool("Error: session unresponsive"),
                                _call("browser_click", s="#a"), _tool("Error: element not found"),
                                _call("delegate_to_agent", agent_id="w", task="a"),
                                _call("delegate_to_agent", agent_id="w", task="b"),
                                _call("delegate_to_agent", agent_id="w", task="c"),
                                _call("q", db="prod"), _call("q", db="dev"), _call("q", db="prod"),
                                _tool("Error: transient"), _assist("recovered and finished")],
                               agent_id="agent_y", model="model_b", session_id="t3",
                               duration=50.0, usage_tokens=TOKEN_USAGE_FLAG_THRESHOLD + 5,
                               compactions=3)),
        ("orch_z", {**make_trace([_assist("workflow done")], agent_id=None,
                                 orchestration_id="orch_z", model="model_a",
                                 session_id="t4"), "metadata": {"kind": "orchestration",
                                                                "source": "orchestration"}}),
        ("agent_x", make_trace([_assist("all good")], agent_id="agent_x",
                               model="model_b", session_id="t5")),
    ]
    for object_id, trace in corpus:
        d = os.path.join(DATA_DIR, "improve", user_id, "traces", object_id, "2026-08")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{trace['session_id']}.json"), "w", encoding="utf-8") as f:
            json.dump(trace, f, indent=2)


class TestRunner:
    def test_aggregates_per_agent_orch_model(self):
        _seed_traces()
        report = build_report(user_id="default")
        assert report["trace_count"] == 5
        per_agent = report["aggregates"]["per_agent"]
        assert set(per_agent) == {"agent_x", "agent_y"}
        assert per_agent["agent_x"]["loops"] == {"numerator": 1, "denominator": 1, "rate": 1.0}
        assert per_agent["agent_x"]["give_up"]["numerator"] == 1
        per_orch = report["aggregates"]["per_orchestration"]
        assert set(per_orch) == {"orch_z"}
        assert per_orch["orch_z"]["clean_success"]["rate"] == 1.0
        per_model = report["aggregates"]["per_model"]
        assert set(per_model) == {"model_a", "model_b"}

    def test_no_silent_zero_denominators(self):
        """2.10 — every detector has denominator > 0 or rate == 'N/A'."""
        _seed_traces()
        report = build_report(user_id="default")
        for name, slot in report["detectors"].items():
            assert slot["denominator"] > 0 or slot["rate"] == "N/A", name
        # The seeded corpus makes every single detector applicable somewhere.
        assert all(slot["denominator"] > 0 for slot in report["detectors"].values())

    def test_agent_filter(self):
        _seed_traces()
        report = build_report(user_id="default", agent_id="agent_x")
        assert report["trace_count"] == 3
        assert set(report["aggregates"]["per_agent"]) == {"agent_x"}

    def test_corpus_duration_stats_empty(self):
        assert corpus_duration_stats([]) == {"mean": 0.0, "std": 0.0, "n": 0}


class TestInsights:
    def test_atomic_learnings_carry_evidence_pointers(self):
        """2.8 — every insight has (trace_file, message_idx) evidence."""
        _seed_traces()
        report = build_report(user_id="default")
        result = extract_insights(report)
        assert result["insight_count"] > 0
        for insight in result["insights"]:
            assert insight["id"] and insight["learning"]
            assert insight["evidence"], insight["detector"]
            for ev in insight["evidence"]:
                assert "trace_file" in ev and "message_idx" in ev
        # The looping trace's finding points at the exact repeated-call messages
        loop_findings = [i for i in result["insights"]
                         if i["detector"] == "loops" and i["kind"] == "trace_finding"]
        assert loop_findings
        assert {e["message_idx"] for e in loop_findings[0]["evidence"]} == {0, 1, 2}
        assert loop_findings[0]["target"] == {"agent_id": "agent_x"}

    def test_positive_detectors_not_reported_as_problems(self):
        _seed_traces()
        result = extract_insights(build_report(user_id="default"))
        assert all(i["detector"] not in ("recovery", "clean_success")
                   for i in result["insights"])


class TestDeterminismAndReadOnly:
    def test_report_and_insights_byte_stable(self):
        """2.11 — two runs on identical input are byte-identical."""
        _seed_traces()
        a_report = build_report(user_id="default")
        b_report = build_report(user_id="default")
        assert json.dumps(a_report, sort_keys=True) == json.dumps(b_report, sort_keys=True)
        a_ins = extract_insights(a_report)
        b_ins = extract_insights(b_report)
        assert json.dumps(a_ins, sort_keys=True) == json.dumps(b_ins, sort_keys=True)

    def test_pipeline_is_read_only(self):
        """2.12 — no file under the data dir is created/modified by analysis."""
        from core.config import DATA_DIR
        _seed_traces()

        def snapshot():
            state = {}
            for root, _, files in os.walk(DATA_DIR):
                for name in files:
                    p = os.path.join(root, name)
                    state[p] = (os.path.getsize(p), os.path.getmtime(p))
            return state

        before = snapshot()
        report = build_report(user_id="default")
        extract_insights(report)
        load_traces("default")
        assert snapshot() == before


class TestInsightsEndpoint:
    """2.9 — GET /api/improve/insights, auth-scoped to the calling user."""

    async def test_endpoint_returns_report_and_insights(self, client):
        _seed_traces()
        resp = await client.get("/api/improve/insights")
        assert resp.status_code == 200
        body = resp.json()
        assert body["report"]["trace_count"] == 5
        assert body["insights"]["insight_count"] > 0

    async def test_endpoint_filters_by_agent(self, client):
        _seed_traces()
        resp = await client.get("/api/improve/insights", params={"agent_id": "agent_x"})
        assert resp.status_code == 200
        assert resp.json()["report"]["trace_count"] == 3

    async def test_endpoint_401_when_gate_enabled_and_no_token(self, client, monkeypatch):
        import core.routes.improve as improve_routes
        monkeypatch.setattr(improve_routes, "load_settings", lambda: {
            "login_enabled": True, "login_username": "alice",
            "login_password_hash": "x"})
        resp = await client.get("/api/improve/insights")
        assert resp.status_code == 401

    async def test_endpoint_scopes_to_jwt_user(self, client, monkeypatch):
        """A valid JWT reads its own namespace — not another user's traces."""
        monkeypatch.setenv("SYNAPSE_JWT_SECRET", "test-secret")
        from core.user_auth import create_session_token
        _seed_traces(user_id="alice")
        _seed_traces(user_id="default")
        token = create_session_token("bob")  # bob has no traces
        resp = await client.get("/api/improve/insights",
                                headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["report"]["trace_count"] == 0
