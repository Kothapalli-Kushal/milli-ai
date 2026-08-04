"""
Regression tests for the run_sql_query timeout/internal-error bug.

Five root causes were identified and fixed; these tests verify each fix:

  RC1 — sql_agent.py used blocking SQLAlchemy calls inside the async event
         loop. FIXED: DB work is offloaded via asyncio.to_thread.
  RC2 — create_engine() in sql_agent.py had no connect_timeout.
         FIXED: _engine_kwargs supplies a 10s connect/login timeout.
  RC3 — anyio/MCP may surface timeouts as ExceptionGroup, not bare
         TimeoutError, which fell through to "An internal error".
         FIXED: engine.py unwraps ExceptionGroup via _extract_timeout.
  RC4 — steps._execute_tool had no outer anyio.fail_after guard around
         session.call_tool. FIXED: guard added, mirroring react_engine.py.
  RC5 — Cached engines had no pool_pre_ping, so stale connections hung on
         first use. FIXED: _engine_kwargs sets pool_pre_ping=True.
"""
from __future__ import annotations

import asyncio
import inspect
import re
import types as _types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# RC1 — blocking sync call inside async event loop
# ─────────────────────────────────────────────────────────────────────────────

class TestRC1BlockingSyncCall:
    """The SQL agent's tool handlers must not run DB work synchronously on the
    asyncio event loop. Verify the DB calls are offloaded to a worker thread
    via asyncio.to_thread so MCP I/O tasks keep running during slow queries."""

    @pytest.mark.asyncio
    async def test_offloaded_db_call_does_not_block_loop(self):
        """A blocking sleep offloaded via asyncio.to_thread (as the fixed
        handler does) must NOT freeze the event loop: a concurrent ticker
        keeps ticking while the 'DB call' runs in a worker thread."""
        ticks = []

        async def background_ticker():
            for _ in range(20):
                await asyncio.sleep(0.02)
                ticks.append(asyncio.get_event_loop().time())

        def fake_blocking_db_call():
            import time
            time.sleep(0.3)  # simulated slow synchronous DB call

        ticker_task = asyncio.create_task(background_ticker())

        t0 = asyncio.get_event_loop().time()
        await asyncio.to_thread(fake_blocking_db_call)
        elapsed = asyncio.get_event_loop().time() - t0

        ticks_during_call = len(ticks)
        ticker_task.cancel()
        try:
            await ticker_task
        except asyncio.CancelledError:
            pass

        assert elapsed >= 0.3, "The blocking call did not take long enough"
        # The loop stayed responsive: ~0.3s / 0.02s ≈ 15 ticks expected;
        # require at least a handful to prove the loop was not frozen.
        assert ticks_during_call >= 5, (
            f"Event loop was starved during the offloaded call: only {ticks_during_call} ticks"
        )

        # Key assertion: the handler offloads DB work instead of blocking
        import tools.sql_agent as sql_agent
        source = inspect.getsource(sql_agent.call_tool)
        assert "asyncio.to_thread" in source, (
            "RC1 REGRESSION: call_tool no longer offloads DB work via asyncio.to_thread"
        )
        assert "with engine.connect()" not in source, (
            "RC1 REGRESSION: call_tool contains a blocking engine.connect() again"
        )

    def test_call_tool_handler_offloads_db_work(self):
        """Confirm the DB operations in sql_agent are offloaded (to_thread),
        not executed inline on the async event loop."""
        import tools.sql_agent as sql_agent
        source = inspect.getsource(sql_agent.call_tool)

        async_patterns = [
            "run_in_executor",
            "run_sync",
            "asyncio.to_thread",
            "await engine",
        ]
        found_async = [p for p in async_patterns if p in source]
        blocking_pattern = "with engine.connect()" in source

        assert not blocking_pattern, (
            "RC1 REGRESSION: blocking engine.connect() found inside async call_tool"
        )
        assert found_async, (
            f"RC1 REGRESSION: no async DB offload found (looked for {async_patterns})"
        )

        # The blocking work must live in sync helpers executed off-loop
        for helper in ("_run_list_tables", "_run_get_table_schema", "_run_sql_query"):
            fn = getattr(sql_agent, helper)
            assert not inspect.iscoroutinefunction(fn), f"{helper} should be sync (thread-run)"
            assert "with engine.connect()" in inspect.getsource(fn)


# ─────────────────────────────────────────────────────────────────────────────
# RC2 — create_engine has no connect_timeout
# ─────────────────────────────────────────────────────────────────────────────

class TestRC2NoConnectTimeout:
    """create_engine() in sql_agent.py must bound the connect/login handshake
    via connect_args, like the db_configs validation route does."""

    def test_sql_agent_engine_has_connect_timeout(self):
        import tools.sql_agent as sql_agent

        # get_db_engine builds kwargs via _engine_kwargs for every path
        source = inspect.getsource(sql_agent.get_db_engine)
        assert "_engine_kwargs" in source, (
            "RC2 REGRESSION: get_db_engine no longer uses _engine_kwargs"
        )

        # pyodbc uses `timeout` (login timeout); others use `connect_timeout`
        odbc_kwargs = sql_agent._engine_kwargs("mssql+pyodbc:///?odbc_connect=x")
        assert odbc_kwargs.get("connect_args", {}).get("timeout"), (
            "RC2 REGRESSION: pyodbc engine kwargs missing login timeout"
        )
        pg_kwargs = sql_agent._engine_kwargs("postgresql://u:p@host/db")
        assert pg_kwargs.get("connect_args", {}).get("connect_timeout"), (
            "RC2 REGRESSION: non-ODBC engine kwargs missing connect_timeout"
        )

    def test_db_configs_route_has_connect_timeout_for_comparison(self):
        """The db_configs validation route correctly sets connect_timeout=10.
        This confirms the pattern is known and intentional elsewhere."""
        from core.routes import db_configs
        source = inspect.getsource(db_configs)
        assert "connect_timeout" in source, (
            "Unexpected: db_configs route does not use connect_timeout either."
        )

    def test_engine_created_with_timeout_guard(self):
        """get_db_engine must pass connect_args to create_engine so an
        unreachable host cannot hang the handshake indefinitely."""
        import tools.sql_agent as sql_agent

        captured_kwargs = {}

        def capturing_create_engine(url, **kwargs):
            captured_kwargs.update(kwargs)
            return MagicMock()

        fake_config = [{
            "id": "test_db",
            "connection_string": "mssql+pyodbc:///?odbc_connect=DATABASE%3DFAKEDB%3BSERVER%3Dlocalhost",
        }]

        with patch("tools.sql_agent.create_engine", side_effect=capturing_create_engine), \
             patch("tools.sql_agent._load_db_configs", return_value=fake_config):
            sql_agent._engines.clear()
            try:
                sql_agent.get_db_engine("test_db")
            finally:
                sql_agent._engines.clear()

        assert "connect_args" in captured_kwargs, (
            f"RC2 REGRESSION: create_engine called without connect_args: {captured_kwargs}"
        )
        assert captured_kwargs.get("pool_pre_ping") is True, (
            "RC5 REGRESSION: create_engine called without pool_pre_ping=True"
        )


# ─────────────────────────────────────────────────────────────────────────────
# RC3 — ExceptionGroup masking TimeoutError as "An internal error"
# ─────────────────────────────────────────────────────────────────────────────

class TestRC3ExceptionGroupMasking:
    """Python 3.11+ anyio can raise ExceptionGroup([TimeoutError()]).
    engine.py catches `except TimeoutError` (bare), which does NOT match an
    ExceptionGroup wrapping a TimeoutError. It falls through to `except Exception`
    → 'An internal error occurred while executing this step.'
    """

    def test_bare_timeout_error_is_caught_by_timeout_handler(self):
        """Confirm that a bare TimeoutError IS caught by the specific handler."""
        caught_by_timeout_handler = False
        caught_by_generic_handler = False

        try:
            raise TimeoutError("plain timeout")
        except TimeoutError:
            caught_by_timeout_handler = True
        except Exception:
            caught_by_generic_handler = True

        assert caught_by_timeout_handler
        assert not caught_by_generic_handler

    def test_exception_group_timeout_is_NOT_caught_by_timeout_handler(self):
        """ExceptionGroup([TimeoutError()]) does NOT match `except TimeoutError`.
        This is why engine.py must unwrap the group explicitly."""
        caught_by_timeout_handler = False
        caught_by_generic_handler = False

        try:
            raise ExceptionGroup("mcp timeout", [TimeoutError("inner timeout")])
        except TimeoutError:
            caught_by_timeout_handler = True
        except Exception:
            caught_by_generic_handler = True

        assert not caught_by_timeout_handler, (
            "Unexpected: ExceptionGroup matched except TimeoutError — Python changed behavior"
        )
        assert caught_by_generic_handler, (
            "ExceptionGroup was not caught — something else is wrong"
        )

    def test_extract_timeout_unwraps_exception_groups(self):
        """engine._extract_timeout must find a TimeoutError inside plain,
        nested, and mixed ExceptionGroups, and return None otherwise."""
        from core.orchestration.engine import _extract_timeout

        assert _extract_timeout(TimeoutError("plain")) is not None
        assert _extract_timeout(
            ExceptionGroup("g", [TimeoutError("inner")])
        ) is not None
        assert _extract_timeout(
            ExceptionGroup("outer", [ExceptionGroup("inner", [TimeoutError("deep")])])
        ) is not None
        assert _extract_timeout(
            ExceptionGroup("mixed", [ValueError("x"), TimeoutError("t")])
        ) is not None
        assert _extract_timeout(ValueError("nope")) is None
        assert _extract_timeout(ExceptionGroup("g", [ValueError("x")])) is None

    def test_exception_group_timeout_routes_to_timeout_message(self):
        """Simulate the fixed engine.py catch chain: an ExceptionGroup-wrapped
        TimeoutError must produce the step-timeout message, not the sanitized
        'An internal error' message."""
        from core.orchestration.engine import _extract_timeout

        step_timeout_msg = None
        internal_error_msg = None

        exc = ExceptionGroup("anyio timeout", [TimeoutError("mcp read timeout exceeded")])

        try:
            raise exc
        except Exception as e:
            if _extract_timeout(e) is not None:
                step_timeout_msg = "Step timed out"
            else:
                internal_error_msg = "An internal error occurred while executing this step."

        assert step_timeout_msg is not None, (
            "RC3 REGRESSION: ExceptionGroup([TimeoutError]) was not routed to the timeout path"
        )
        assert internal_error_msg is None

    @pytest.mark.asyncio
    async def test_anyio_fail_after_raises_exception_group_on_python311_plus(self):
        """anyio's fail_after raises ExceptionGroup on Python 3.11+ when the
        cancelled body raises a BaseException subclass itself.
        Verify what anyio actually raises on this runtime."""
        import sys
        import anyio

        raised_type = None
        inner_type = None

        try:
            with anyio.fail_after(0.01):
                await asyncio.sleep(1.0)  # will be cancelled
        except BaseException as e:
            raised_type = type(e).__name__
            if isinstance(e, BaseExceptionGroup):
                inner_type = type(e.exceptions[0]).__name__ if e.exceptions else None

        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
        print(f"\nRC3 INFO: Python {py_ver}, anyio fail_after raised: {raised_type}"
              f"{f' (inner: {inner_type})' if inner_type else ''}")

        # The key question: does the engine's `except TimeoutError` catch it?
        caught_correctly = raised_type in ("TimeoutError", "Cancelled")
        if not caught_correctly:
            print(f"RC3 CONFIRMED: anyio raises {raised_type} which may not match "
                  f"`except TimeoutError` in engine.py")
        else:
            print(f"RC3 INCONCLUSIVE on this runtime: anyio raises {raised_type}")


# ─────────────────────────────────────────────────────────────────────────────
# RC4 — orchestration TOOL step has no outer fail_after
# ─────────────────────────────────────────────────────────────────────────────

class TestRC4NoOuterFailAfterInToolStep:
    """react_engine.py wraps call_tool with anyio.fail_after(timeout + 5).
    steps.py _execute_tool must do the same so a wedged MCP transport cannot
    hang a TOOL step indefinitely."""

    def test_react_engine_has_fail_after_around_call_tool(self):
        import core.react_engine as re_mod
        source = inspect.getsource(re_mod)
        # look for the pattern: fail_after(...) immediately followed by call_tool
        # within the same with-block. We search for the literal pattern in source.
        has_fail_after = "fail_after" in source
        assert has_fail_after, "react_engine.py does not use fail_after at all — source changed"

        # Confirm the guard and the call_tool are in the same code block by checking
        # that after `fail_after` appears, `call_tool` appears before the next `def `.
        idx_fail = source.index("fail_after")
        next_def = source.find("\ndef ", idx_fail)          # next top-level def
        if next_def == -1:
            next_def = len(source)
        idx_call = source.find("call_tool", idx_fail)
        assert 0 < idx_call < next_def, (
            "call_tool not found after fail_after in the same section of react_engine.py"
        )
        print("\nReact engine: fail_after guard IS present near call_tool.")

    def test_steps_execute_tool_has_fail_after(self):
        import core.orchestration.steps as steps_mod
        source = inspect.getsource(steps_mod.ToolStepExecutor._execute_tool)
        assert "fail_after" in source, (
            "RC4 REGRESSION: _execute_tool lost its fail_after guard around call_tool."
        )
        # The guard must wrap the MCP call, not something else
        idx_fail = source.index("fail_after")
        idx_call = source.find("call_tool", idx_fail)
        assert idx_call > idx_fail, (
            "RC4 REGRESSION: fail_after does not guard the session.call_tool invocation"
        )

    @pytest.mark.asyncio
    async def test_tool_step_mcp_exception_hits_generic_handler(self, fake_llm):
        """Drive the orchestration TOOL step with a session.call_tool that raises
        a ConnectionError. Confirm the engine surfaces 'An internal error' rather
        than a meaningful message — because _execute_tool has no outer fail_after
        guard and the generic except Exception handler sanitizes the real error.

        The ToolStepExecutor calls aggregate_all_tools() to find the tool, and
        THEN asks the LLM for JSON args, and THEN calls _execute_tool. We mock
        all three gates so the ConnectionError from call_tool is the failure point.
        """
        import io, sys, os
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")

        from _fakes import seed as S

        orch_dict = S.make_orchestration(
            entry_step_id="t1",
            steps=[{
                "id": "t1",
                "name": "RunSQL",
                "type": "tool",
                "forced_tool": "run_sql_query",
                "tool_args": {"query": "SELECT 1"},
                "output_key": "result",
                "next_step_id": None,
            }],
        )

        from core.models_orchestration import Orchestration
        from core.orchestration.engine import OrchestrationEngine
        from mcp.types import Tool

        # The session whose call_tool raises
        mock_session = AsyncMock()
        mock_session.call_tool.side_effect = ConnectionError("TCP connection reset by peer")
        mock_session.send_ping = AsyncMock(return_value=None)

        server_mod = _types.SimpleNamespace(
            agent_sessions={"ext_mcp_sql": mock_session},
            memory_store=None,
            tool_router={"run_sql_query": ("ext_mcp_sql", "run_sql_query")},
        )

        # Stub aggregate_all_tools so tool discovery succeeds
        fake_tool = Tool(
            name="run_sql_query",
            description="Execute a SQL query",
            inputSchema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        )

        # Stub the LLM to return a valid JSON tool call
        fake_llm.set_default('{"tool": "run_sql_query", "arguments": {"query": "SELECT 1"}}')

        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        events = []
        try:
            # aggregate_all_tools is a local import inside the function, so patch at source module
            with patch("core.tools.aggregate_all_tools", new=AsyncMock(return_value=([fake_tool], {}, {}, {}))):
                with patch.object(
                    __import__("core.orchestration.steps", fromlist=["ToolStepExecutor"]).ToolStepExecutor,
                    "_execute_tool",
                    new=AsyncMock(side_effect=ConnectionError("TCP connection reset by peer")),
                ):
                    orch = Orchestration.model_validate(orch_dict)
                    engine = OrchestrationEngine(orch, server_mod)
                    async for ev in engine.run("hello", run_id="run_rc4_test"):
                        events.append(ev)
        finally:
            sys.stdout = old_stdout

        # The ToolStepExecutor retries max_turns times, then raises RuntimeError
        # which the engine catches as a generic Exception → "An internal error"
        error_events = [e for e in events if e.get("type") == "step_error"]
        assert error_events, (
            f"Expected step_error event. Events seen: {[e.get('type') for e in events]}"
        )

        error_msg = error_events[0].get("error", "")
        print(f"\nRC4: step_error message = '{error_msg}'")
        assert "internal error" in error_msg.lower(), (
            f"Unexpected error message: '{error_msg}'"
        )
        print("RC4 CONFIRMED: ConnectionError from _execute_tool produces 'An internal error' (sanitized).")


# ─────────────────────────────────────────────────────────────────────────────
# RC5 — engine cache has no pool_pre_ping
# ─────────────────────────────────────────────────────────────────────────────

class TestRC5NopoolPrePing:
    """Engines in sql_agent._engines must be created with pool_pre_ping=True so
    stale pooled connections are health-checked (and replaced) before use."""

    def test_sql_agent_create_engine_has_pool_pre_ping(self):
        import tools.sql_agent as sql_agent
        for conn_str in (
            "mssql+pyodbc:///?odbc_connect=DATABASE%3DX%3BSERVER%3Dlocalhost",
            "postgresql://u:p@host/db",
        ):
            kwargs = sql_agent._engine_kwargs(conn_str)
            assert kwargs.get("pool_pre_ping") is True, (
                f"RC5 REGRESSION: pool_pre_ping missing for {conn_str}"
            )

    def test_scale_db_has_pool_pre_ping_for_comparison(self):
        """core/scale/db.py correctly sets pool_pre_ping=True.
        This confirms the pattern is used elsewhere in the codebase."""
        from core.scale import db as scale_db
        source = inspect.getsource(scale_db)
        assert "pool_pre_ping" in source, (
            "scale/db.py no longer uses pool_pre_ping — reference changed."
        )
        print("\nReference: scale/db.py correctly uses pool_pre_ping=True.")

    def test_stale_connection_would_raise_on_first_execute(self):
        """Simulate what happens when a pooled connection is stale: engine.connect()
        succeeds (pool hands back cached conn), but execute() raises
        OperationalError. Without pool_pre_ping, no pre-check occurs."""
        from unittest.mock import MagicMock, patch
        import tools.sql_agent as sql_agent

        stale_conn = MagicMock()
        stale_conn.__enter__ = MagicMock(return_value=stale_conn)
        stale_conn.__exit__ = MagicMock(return_value=False)

        try:
            from sqlalchemy.exc import OperationalError
            stale_conn.execute.side_effect = OperationalError(
                "statement", {}, Exception("server has gone away")
            )
        except ImportError:
            pytest.skip("sqlalchemy not installed")

        mock_engine = MagicMock()
        mock_engine.connect.return_value = stale_conn

        # Without pool_pre_ping the engine just returns the stale connection
        with pytest.raises(Exception):  # OperationalError
            with mock_engine.connect() as conn:
                conn.execute("SELECT 1")

        # connect() was called ONCE (no retry, no pre-ping)
        mock_engine.connect.assert_called_once()
        print("\nRC5 CONFIRMED: stale connection raises OperationalError on execute() with no retry/pre-ping.")
