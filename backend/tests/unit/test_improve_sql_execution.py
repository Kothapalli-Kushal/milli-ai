"""
Checkpoint-6 verification (unit), chunk 3 — the `sql_execution` rung.

Covers 6.38 (execute candidate + reference, multiset rows, `order_sensitive:
"auto"` derived from the reference's top-level ORDER BY), 6.39 (correct-but-
differently-shaped queries pass under execution where AST comparison fails
them), 6.40 (`execution_env` resolves an EXISTING Synapse SQL connection via
`tools/sql_agent.py::get_db_engine` — no new connection manager), 6.41
(read-only enforcement on BOTH sides before anything executes; adversarial),
6.42 (save-time double execution rejects a non-deterministic reference;
LIMIT-without-ORDER-BY and NOW()/RANDOM() warn), 6.43 (snapshot_id / "unpinned"
semantics), and 6.44 (`execution_timeout` and `row_cap_exceeded` are distinct
statuses — neither a silent pass nor an extraction failure).

The tests run the REAL `SqlExecutor.execute` against an in-memory SQLite
database by monkeypatching `tools.sql_agent.get_db_engine` — the exact seam
the production code resolves connections through.
"""
import pytest

pytest.importorskip("sqlglot")
sqlalchemy = pytest.importorskip("sqlalchemy")

from core.improve import sql_compare
from core.improve.grading import (
    FAIL_STATUSES,
    GradeContext,
    GradingConfigError,
    run_check,
)
from core.improve.sql_compare import ExecResult, SqlExecutor

USER = "default"
ENV = {"connection_id": "sales_readonly", "timeout_s": 5, "max_rows": 5000}


# ── fixtures: an in-memory DB behind the real connection-resolution seam ─────

SEED_SQL = [
    "CREATE TABLE sales (region TEXT, quarter TEXT, revenue REAL)",
    "INSERT INTO sales VALUES ('APAC', 'Q3', 2200000)",
    "INSERT INTO sales VALUES ('APAC', 'Q3', 2000000)",
    "INSERT INTO sales VALUES ('EMEA', 'Q3', 3100000)",
    "INSERT INTO sales VALUES ('AMER', 'Q3', 2800000)",
    "INSERT INTO sales VALUES ('APAC', 'Q2', 900000)",
    "CREATE TABLE users (id INTEGER, name TEXT)",
    "INSERT INTO users VALUES (1, 'ada'), (2, 'bob'), (3, 'cyd')",
    "CREATE TABLE orders (id INTEGER, user_id INTEGER)",
    "INSERT INTO orders VALUES (10, 1), (11, 3)",
]


@pytest.fixture
def engine():
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    eng = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    with eng.connect() as conn:
        for stmt in SEED_SQL:
            conn.exec_driver_sql(stmt)
        conn.commit()
    yield eng
    eng.dispose()


class TrackingExecutor(SqlExecutor):
    """The real executor, plus a record of every statement it was given.

    6.41 needs to prove a refused write was NEVER handed to the database —
    absence from `self.executed` is that proof.
    """

    def __init__(self):
        self.executed: list[str] = []

    def execute(self, sql: str, env: dict) -> ExecResult:
        self.executed.append(sql)
        return super().execute(sql, env)


@pytest.fixture
def executor(engine, monkeypatch):
    import tools.sql_agent as sql_agent

    calls: list[str | None] = []

    def fake_get_db_engine(db_id=None):
        calls.append(db_id)
        return engine, "testdb"

    monkeypatch.setattr(sql_agent, "get_db_engine", fake_get_db_engine)
    tracker = TrackingExecutor()
    tracker.connection_ids_requested = calls
    return tracker


def cmp_exec(actual, reference, spec=None, env=ENV, executor=None):
    return sql_compare.compare_execution(actual, reference, spec or {}, env, executor)


# ── 6.38 — execution comparison semantics ────────────────────────────────────

REF_TOP_REGION = (
    "SELECT region, SUM(revenue) AS r FROM sales WHERE quarter='Q3' "
    "GROUP BY region ORDER BY r DESC LIMIT 1"
)


class TestSqlExecutionComparator:
    def test_identical_results_pass(self, executor):
        out = cmp_exec(
            "select   region , sum(revenue) as total from sales\n"
            "where quarter = 'Q3' group by region order by total desc limit 1",
            REF_TOP_REGION, executor=executor,
        )
        assert out.status == "pass"

    def test_multiset_by_default_when_reference_has_no_order_by(self, executor):
        """Row order is agent-chosen noise unless the reference pinned one."""
        out = cmp_exec(
            "SELECT region FROM sales WHERE quarter='Q3' ORDER BY region DESC",
            "SELECT region FROM sales WHERE quarter='Q3'",
            executor=executor,
        )
        assert out.status == "pass" and "multiset" in out.detail

    def test_ordered_when_reference_has_top_level_order_by(self, executor):
        out = cmp_exec(
            "SELECT DISTINCT region FROM sales WHERE quarter='Q3' ORDER BY region DESC",
            "SELECT DISTINCT region FROM sales WHERE quarter='Q3' ORDER BY region ASC",
            executor=executor,
        )
        assert out.status == "fail"

    def test_auto_rule_reads_only_the_top_level_order_by(self):
        assert sql_compare.reference_has_order_by(REF_TOP_REGION) is True
        assert sql_compare.reference_has_order_by(
            "SELECT region FROM sales WHERE quarter='Q3'") is False
        # An ORDER BY buried in a subquery does not pin the outer row order.
        assert sql_compare.reference_has_order_by(
            "SELECT region FROM (SELECT region FROM sales ORDER BY revenue) t"
        ) is False

    def test_column_aliases_ignored_by_position(self, executor):
        """An agent naming the column `total` instead of `r` is not an error."""
        out = cmp_exec(
            "SELECT region, SUM(revenue) AS total FROM sales WHERE quarter='Q3' "
            "GROUP BY region ORDER BY total DESC LIMIT 1",
            REF_TOP_REGION, executor=executor,
        )
        assert out.status == "pass"

    def test_float_tolerance(self, executor):
        out = cmp_exec("SELECT 4.2001", "SELECT 4.2",
                       spec={"float_tol": 0.01}, executor=executor)
        assert out.status == "pass"
        out = cmp_exec("SELECT 4.3", "SELECT 4.2",
                       spec={"float_tol": 0.01}, executor=executor)
        assert out.status == "fail"

    def test_empty_result_set_is_a_legitimate_expected_value(self, executor):
        out = cmp_exec(
            "SELECT region FROM sales WHERE quarter='Q9'",
            "SELECT region FROM sales WHERE quarter='Q9'",
            executor=executor,
        )
        assert out.status == "pass"
        # ... and empty-vs-nonempty is a real difference, not a vacuous pass.
        out = cmp_exec(
            "SELECT region FROM sales WHERE quarter='Q9'",
            "SELECT region FROM sales WHERE quarter='Q3'",
            executor=executor,
        )
        assert out.status == "fail"

    def test_wrong_rows_fail(self, executor):
        out = cmp_exec(
            "SELECT region, SUM(revenue) AS r FROM sales WHERE quarter='Q2' "
            "GROUP BY region ORDER BY r DESC LIMIT 1",
            REF_TOP_REGION, executor=executor,
        )
        assert out.status == "fail" and "differ" in out.detail

    def test_candidate_execution_error_is_a_check_failure(self, executor):
        out = cmp_exec("SELECT region FROM no_such_table", REF_TOP_REGION,
                       executor=executor)
        assert out.status == "fail" and "errored" in out.detail

    def test_reference_execution_error_is_an_authoring_error(self, executor):
        """A reference that errors must be LOUD (§6.3.5), never scored."""
        with pytest.raises(GradingConfigError, match="reference SQL failed"):
            cmp_exec("SELECT 1", "SELECT region FROM no_such_table",
                     executor=executor)


# ── 6.39 — correct-but-differently-shaped queries pass under execution ───────

class TestEquivalentShapesPassUnderExecution:
    def test_join_vs_subquery(self, executor):
        """The §6.3.1 motivating case: AST comparison fails a genuinely correct
        JOIN where the reference used a subquery; execution passes it."""
        ref = "SELECT name FROM users WHERE id IN (SELECT user_id FROM orders)"
        alt = "SELECT u.name FROM users u JOIN orders o ON o.user_id = u.id"

        assert sql_compare.compare_ast(alt, ref, "sqlite").status == "fail"
        assert cmp_exec(alt, ref, executor=executor).status == "pass"

    def test_window_function_vs_order_by_limit(self, executor):
        alt = (
            "SELECT region, r FROM ("
            "  SELECT region, SUM(revenue) AS r,"
            "         RANK() OVER (ORDER BY SUM(revenue) DESC) AS rk"
            "  FROM sales WHERE quarter='Q3' GROUP BY region"
            ") WHERE rk = 1"
        )
        assert sql_compare.compare_ast(alt, REF_TOP_REGION, "sqlite").status == "fail"
        assert cmp_exec(alt, REF_TOP_REGION, executor=executor).status == "pass"


# ── 6.40 / 6.43 — connection resolution and snapshot semantics ───────────────

class TestConnectionResolution:
    def test_executor_resolves_the_existing_synapse_connection_by_id(self, executor):
        result = executor.execute("SELECT COUNT(*) FROM sales", ENV)
        assert result.rows == [(5,)]
        assert executor.connection_ids_requested == ["sales_readonly"]

    def test_unknown_connection_is_an_error_result_not_a_crash(self, monkeypatch):
        import tools.sql_agent as sql_agent

        def refuse(db_id=None):
            raise ValueError(f"No database config found for db_id='{db_id}'.")

        monkeypatch.setattr(sql_agent, "get_db_engine", refuse)
        result = SqlExecutor().execute("SELECT 1", {"connection_id": "nope"})
        assert result.error and "nope" in result.error and result.rows is None

    def test_snapshot_id_defaults_to_unpinned(self):
        assert SqlExecutor().snapshot_id({}) == "unpinned"
        assert SqlExecutor().snapshot_id(
            {"snapshot_id": "2026-08-01T00:00Z"}) == "2026-08-01T00:00Z"


# ── 6.41 — read-only enforcement, both sides, BEFORE execution ───────────────

class TestReadOnlyEnforcement:
    @pytest.mark.parametrize("attack", [
        "DROP TABLE sales",
        "UPDATE sales SET revenue = 0",
        "DELETE FROM sales",
        "INSERT INTO sales VALUES ('X', 'Q3', 1)",
        "SELECT 1; DROP TABLE sales",
        "WITH x AS (SELECT 1) DELETE FROM sales",
    ])
    def test_candidate_write_is_a_failure_and_is_never_executed(
        self, executor, attack
    ):
        out = cmp_exec(attack, REF_TOP_REGION, executor=executor)
        assert out.status == "fail" and "refused" in out.detail
        assert attack not in executor.executed, "the write reached the executor"
        # The graded database is intact.
        assert executor.execute(
            "SELECT COUNT(*) FROM sales", ENV).rows == [(5,)]

    def test_write_reference_is_an_authoring_error_and_never_executes(
        self, executor
    ):
        with pytest.raises(GradingConfigError, match="read-only"):
            cmp_exec("SELECT 1", "DELETE FROM sales", executor=executor)
        assert executor.executed == []


# ── 6.44 — execution_timeout / row_cap_exceeded are DISTINCT statuses ────────

class ScriptedExecutor(SqlExecutor):
    """Returns canned ExecResults in order (reference first, candidate second)."""

    def __init__(self, results):
        self.results = list(results)

    def execute(self, sql: str, env: dict) -> ExecResult:
        return self.results.pop(0)


def _check_ctx(executor, trace_query="SELECT region FROM sales"):
    import json as _json
    trace = {
        "output": "done",
        "messages": [{
            "role": "assistant",
            "tool_calls": [{
                "id": "c1",
                "function": {"name": "sql_agent",
                             "arguments": _json.dumps({"query": trace_query})},
            }],
        }],
    }
    return GradeContext(user_id=USER, trace=trace, trace_file="t.json",
                        expected={"reference_sql": "SELECT region FROM sales"},
                        execution_env=ENV, sql_executor=executor,
                        input_id="in_001")


class TestDistinctStatuses:
    def test_row_cap_exceeded_is_distinct(self, executor):
        env = dict(ENV, max_rows=2)
        out = cmp_exec(
            "SELECT * FROM sales",                        # 5 rows > cap
            "SELECT * FROM sales WHERE region='EMEA'",    # 1 row, under cap
            env=env, executor=executor,
        )
        assert out.status == "row_cap_exceeded"

    def test_execution_timeout_is_distinct(self):
        scripted = ScriptedExecutor([
            ExecResult(rows=[("APAC",)], columns=["region"]),   # reference
            ExecResult(error="canceling statement due to statement timeout",
                       timed_out=True),                          # candidate
        ])
        out = cmp_exec("SELECT pg_sleep(60)", "SELECT 'APAC'", executor=scripted)
        assert out.status == "execution_timeout"

    def test_statuses_count_as_failures_not_extraction_failures(self):
        """§6.3.5 — a timeout is a failing CHECK: it stays in the denominator,
        can trip a critical veto, and is never confused with a mis-specified
        extractor (which would mark the score unreliable instead)."""
        scripted = ScriptedExecutor([
            ExecResult(rows=[("APAC",)], columns=["region"]),
            ExecResult(error="timeout", timed_out=True),
        ])
        check = {
            "id": "sql_rows", "weight": 1.0, "critical": True,
            "extract": {"from": "tool_call_arg", "tool": "sql_agent",
                        "arg": "query"},
            "compare": {"type": "sql_execution",
                        "reference": "$expected.reference_sql"},
        }
        result = run_check(check, _check_ctx(scripted))
        assert result["status"] == "execution_timeout"
        assert result["status"] in FAIL_STATUSES
        assert result["status"] != "extraction_failed"


# ── 6.42 — save-time reference validation ────────────────────────────────────

class FlakyExecutor(SqlExecutor):
    """Different rows on every call — a non-deterministic reference query."""

    def __init__(self):
        self.n = 0

    def execute(self, sql: str, env: dict) -> ExecResult:
        self.n += 1
        return ExecResult(rows=[(self.n,)], columns=["r"])


class TestSaveTimeReferenceValidation:
    def test_deterministic_reference_passes_and_is_executed_twice(self, executor):
        report = sql_compare.validate_reference_query(
            REF_TOP_REGION, ENV, executor=executor)
        assert report["errors"] == []
        assert executor.executed == [REF_TOP_REGION, REF_TOP_REGION]

    def test_nondeterministic_reference_is_rejected(self):
        report = sql_compare.validate_reference_query(
            "SELECT r FROM t", ENV, executor=FlakyExecutor())
        assert any("non-deterministic" in e for e in report["errors"])

    def test_limit_without_order_by_warns(self, executor):
        report = sql_compare.validate_reference_query(
            "SELECT region FROM sales LIMIT 1", ENV, executor=executor)
        assert any("LIMIT/TOP without ORDER BY" in w for w in report["warnings"])

    @pytest.mark.parametrize("construct,sql", [
        ("now", "SELECT * FROM sales WHERE quarter = NOW()"),
        ("random", "SELECT region FROM sales ORDER BY RANDOM() LIMIT 1"),
        ("current_date", "SELECT CURRENT_DATE"),
    ])
    def test_nondeterministic_constructs_warn(self, executor, construct, sql):
        report = sql_compare.validate_reference_query(sql, ENV, executor=executor)
        assert any(construct in w for w in report["warnings"])

    def test_failing_reference_is_rejected(self, executor):
        report = sql_compare.validate_reference_query(
            "SELECT region FROM no_such_table", ENV, executor=executor)
        assert any("failed to execute" in e for e in report["errors"])

    def test_write_reference_is_rejected_without_executing(self, executor):
        report = sql_compare.validate_reference_query(
            "DELETE FROM sales", ENV, executor=executor)
        assert any("read-only" in e for e in report["errors"])
        assert executor.executed == []

    def test_no_connection_skips_execution_but_keeps_static_checks(self, executor):
        """Without a connection there is nothing to execute against; the static
        construct warnings still apply."""
        report = sql_compare.validate_reference_query(
            "SELECT region FROM sales LIMIT 1", None, executor=executor)
        assert report["errors"] == []
        assert executor.executed == []
        assert any("LIMIT/TOP" in w for w in report["warnings"])

    def test_save_benchmark_surfaces_the_rejection(self):
        """The double-execution guard runs at SAVE time through
        `save_benchmark`, not at run time (checklist 6.42 + 6.8)."""
        from core.improve import benchmark as bm

        suite = {
            "id": "bench_nondet", "name": "n", "target_object_id": "agent_1",
            "schema_version": 2, "grading_mode": "deterministic",
            "execution_env": {"connection_id": "sales_readonly"},
            "scorer": {"metrics": {"success": 1.0},
                       "process_weight": 1.0, "outcome_weight": 1.0},
            "inputs": [{
                "id": "in_001", "prompt": "top region?", "split": "train",
                "expected": {
                    "reference_sql": "SELECT r FROM t",
                    "checks": [{
                        "id": "rows", "weight": 1.0,
                        "extract": {"from": "tool_call_arg",
                                    "tool": "sql_agent", "arg": "query"},
                        "compare": {"type": "sql_execution",
                                    "reference": "$expected.reference_sql"},
                    }],
                },
            }],
        }
        with pytest.raises(GradingConfigError, match="non-deterministic"):
            bm.save_benchmark(USER, suite, sql_executor=FlakyExecutor())
