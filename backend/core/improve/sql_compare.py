"""
SQL comparison for outcome grading (Checkpoint 6, §6.3.5).

Two rungs of the escalation ladder live here:

  Rung 1  `sql_equivalent`  — AST normalization via sqlglot. Free, exact, and
                              deliberately LOW WEIGHT: it is a diagnostic that
                              tells the tuner *how* a query differed, never the
                              authoritative correctness check. It will fail a
                              genuinely correct answer written as a JOIN where
                              the reference used a subquery. That is a known
                              limitation, not a bug.
  Rung 2  `sql_execution`   — execute candidate and reference against the same
                              connection and compare the returned rows. This is
                              the AUTHORITATIVE NL2SQL check and should carry
                              the majority of the weight. It is still
                              *deterministic* grading: equivalence is decided by
                              the data, not by a model's opinion.

`sql_execution` is NOT the deferred `resultset` comparator. `resultset` would
scrape rows out of the trace's tool-result message, which depends on the tool
result being reliably JSON-shaped in the trace — not yet guaranteed, so it stays
out of CP6. `sql_execution` executes at grade time and does not depend on trace
shape at all.

Read-only enforcement runs on BOTH sides before anything executes: a candidate
`DROP TABLE` is a check *failure*, never an executed statement.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

# Constructs that make a reference query non-reproducible (§6.3.5 authoring
# guards). Checked at benchmark save time, warned about, never silently graded.
NONDETERMINISTIC_CONSTRUCTS = (
    "now(", "current_date", "current_time", "current_timestamp",
    "getdate(", "sysdatetime(", "random(", "rand(", "newid(", "uuid(",
)

DEFAULT_TIMEOUT_S = 10
DEFAULT_MAX_ROWS = 5000


class SqlGuardRefusal(Exception):
    """A statement failed the read-only guard and must never be executed."""


# ── read-only guard — reuses the EXISTING sql_agent guard (§6.3.5) ───────────

def is_read_only(sql: str) -> bool:
    """True when `sql` is a read-only statement.

    The primary authority is `tools/sql_agent.py::_is_write_query`, the guard
    Synapse already ships and already trusts — CP6 does not write a second one.
    sqlglot, when installed, adds a second opinion that also catches writes
    hidden behind CTEs (`WITH x AS (...) DELETE ...`) and stacked statements,
    which a first-keyword check cannot see. Either side saying "write" refuses.
    """
    text = (sql or "").strip()
    if not text:
        return False
    try:
        from tools.sql_agent import _is_write_query
        if _is_write_query(text):
            return False
    except Exception:
        # Fall through to the sqlglot opinion rather than failing open.
        if re.match(
            r"^\s*(insert|update|delete|drop|create|alter|truncate|replace|"
            r"merge|upsert|grant|revoke)\b",
            text, re.IGNORECASE,
        ):
            return False

    parsed = _parse_all(text)
    if parsed is None:  # sqlglot unavailable — the keyword guard already ran
        return True
    if len(parsed) > 1:
        return False  # stacked statements: refuse rather than guess
    import sqlglot.expressions as exp
    root = parsed[0]
    if root is None:
        return False
    write_nodes = (
        exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create,
        exp.Alter, exp.Grant,
    )
    if isinstance(root, write_nodes):
        return False
    return not any(isinstance(node, write_nodes) for node in root.walk())


def _parse_all(sql: str, dialect: str | None = None):
    try:
        import sqlglot
    except ImportError:
        return None
    try:
        return sqlglot.parse(sql, read=dialect)
    except Exception:
        return [None]


# ── rung 1: AST normalization ────────────────────────────────────────────────

def normalize_sql(sql: str, dialect: str | None = None) -> str:
    """Canonical form of a query for AST comparison.

    Pipeline (§6.3.5): parse -> strip comments -> lowercase keywords and
    identifiers -> sort commutative predicate operands -> sort projections when
    no ORDER BY depends on their position -> render.

    Raises ValueError on a parse failure so the caller can distinguish a bad
    *candidate* (check failure) from a bad *expected* value (authoring error).
    """
    try:
        import sqlglot
        import sqlglot.expressions as exp
    except ImportError as e:
        raise RuntimeError(
            "sqlglot is required for the sql_equivalent comparator "
            "(pip install sqlglot)"
        ) from e

    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception as e:
        raise ValueError(f"could not parse SQL: {e}") from e
    if tree is None:
        raise ValueError("could not parse SQL: empty statement")

    tree = tree.transform(_strip_comments)
    tree = tree.transform(lambda n: _lower_identifiers(n, exp))
    tree = tree.transform(lambda n: _sort_commutative(n, exp))
    tree = _sort_projections(tree, exp)
    return tree.sql(dialect=dialect, normalize=True, comments=False).lower()


def _strip_comments(node):
    try:
        node.comments = None
    except Exception:
        pass
    return node


def _lower_identifiers(node, exp):
    if isinstance(node, exp.Identifier) and isinstance(node.this, str):
        if not node.quoted:
            node.set("this", node.this.lower())
    return node


def _sort_commutative(node, exp):
    """Sort operands of commutative connectives so `a AND b` == `b AND a`."""
    if isinstance(node, (exp.And, exp.Or)):
        left, right = node.left, node.right
        if left is not None and right is not None:
            if left.sql().lower() > right.sql().lower():
                node.set("this", right)
                node.set("expression", left)
    elif isinstance(node, exp.EQ):
        left, right = node.left, node.right
        both_simple = all(
            isinstance(side, (exp.Column, exp.Literal)) for side in (left, right)
        )
        if both_simple and left.sql().lower() > right.sql().lower():
            node.set("this", right)
            node.set("expression", left)
    return node


def _sort_projections(tree, exp):
    """Sort SELECT projections when nothing depends on their position.

    An ORDER BY / GROUP BY referencing an ordinal (`ORDER BY 2`) makes column
    position meaningful, so those queries are left alone.
    """
    for select in list(tree.find_all(exp.Select)):
        if _has_ordinal_reference(select, exp):
            continue
        expressions = select.expressions or []
        if len(expressions) < 2:
            continue
        select.set("expressions", sorted(expressions, key=lambda e: e.sql().lower()))
    return tree


def _has_ordinal_reference(select, exp) -> bool:
    for clause in ("order", "group"):
        node = select.args.get(clause)
        if node is None:
            continue
        for literal in node.find_all(exp.Literal):
            if literal.is_int:
                return True
    return False


def compare_ast(actual: str, reference: str, dialect: str | None = None):
    """Rung 1 comparison. Returns a grading.Comparison."""
    from core.improve.grading import Comparison, GradingConfigError

    try:
        expected_norm = normalize_sql(reference, dialect)
    except ValueError as e:
        # A bad EXPECTED value is a benchmark authoring error (checklist 6.8).
        raise GradingConfigError(f"reference SQL is not parseable: {e}")
    try:
        actual_norm = normalize_sql(actual, dialect)
    except ValueError as e:
        return Comparison("fail", f"candidate SQL is not parseable: {e}")

    if actual_norm == expected_norm:
        return Comparison("pass", "AST-normalized forms are identical")
    return Comparison(
        "fail",
        f"AST differs — normalized candidate: {actual_norm[:200]}",
    )


# ── rung 2: execution ────────────────────────────────────────────────────────

@dataclass
class ExecResult:
    rows: list[tuple] | None = None
    columns: list[str] | None = None
    error: str | None = None
    timed_out: bool = False
    row_cap_exceeded: bool = False


class SqlExecutor:
    """Executes read-only SQL against an EXISTING Synapse SQL connection.

    CP6 introduces no connection manager, no credential storage, and no DB
    config of its own (checklist 6.40) — `connection_id` is a `db_configs.json`
    id resolved by `tools/sql_agent.py::get_db_engine`.
    """

    def execute(self, sql: str, env: dict) -> ExecResult:
        env = env or {}
        max_rows = int(env.get("max_rows") or DEFAULT_MAX_ROWS)
        timeout_s = int(env.get("timeout_s") or DEFAULT_TIMEOUT_S)
        try:
            from sqlalchemy import text as sa_text
            from tools.sql_agent import get_db_engine
            engine, _db = get_db_engine(env.get("connection_id"))
        except Exception as e:
            return ExecResult(error=f"connection '{env.get('connection_id')}': {e}")

        try:
            with engine.connect() as conn:
                try:  # best effort — not every driver honors a statement timeout
                    conn.exec_driver_sql(f"SET statement_timeout = {timeout_s * 1000}")
                except Exception:
                    pass
                cursor = conn.execute(sa_text(sql))
                columns = [str(k) for k in cursor.keys()]
                rows = cursor.fetchmany(max_rows + 1)
        except Exception as e:
            message = str(e)
            if "timeout" in message.lower() or "canceling statement" in message.lower():
                return ExecResult(error=message, timed_out=True)
            return ExecResult(error=message)

        if len(rows) > max_rows:
            return ExecResult(columns=columns, row_cap_exceeded=True,
                              error=f"result exceeded max_rows={max_rows}")
        return ExecResult(rows=[tuple(r) for r in rows], columns=columns)

    def snapshot_id(self, env: dict) -> str:
        """Best-effort snapshot/version identifier for the pinned connection.

        Returns "unpinned" when the connection cannot report one — the score is
        then explicitly NOT exactly reproducible rather than falsely claimed to
        be (§6.3.5 determinism guards).
        """
        return str((env or {}).get("snapshot_id") or "unpinned")


_DEFAULT_EXECUTOR = SqlExecutor()


def reference_has_order_by(sql: str, dialect: str | None = None) -> bool:
    """True when the reference has a TOP-LEVEL ORDER BY — the `auto` rule."""
    try:
        import sqlglot
        tree = sqlglot.parse_one(sql, read=dialect)
        return tree is not None and tree.args.get("order") is not None
    except Exception:
        return bool(re.search(r"\border\s+by\b", sql or "", re.IGNORECASE))


def _canon_cell(value: Any, float_tol: float) -> Any:
    """Canonicalize one cell so driver-level type noise is not a difference."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        number = float(value)
        if float_tol > 0:
            # Quantize to the tolerance so equal-within-tolerance values become
            # the same key — required for order-insensitive multiset matching.
            return round(number / float_tol) * float_tol
        return number
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    return str(value)


def _canon_rows(
    rows: list[tuple], columns: list[str], spec: dict, float_tol: float
) -> list[tuple]:
    column_match = str(spec.get("column_match") or "by_position")
    if column_match == "by_name" and columns:
        order = sorted(range(len(columns)), key=lambda i: columns[i].lower())
        rows = [tuple(row[i] for i in order) for row in rows]
    return [tuple(_canon_cell(cell, float_tol) for cell in row) for row in rows]


def compare_execution(
    actual: str,
    reference: str,
    spec: dict,
    env: dict | None,
    executor: SqlExecutor | None = None,
):
    """Rung 2 comparison — the authoritative NL2SQL check.

    Returns a grading.Comparison. Distinct statuses for `execution_timeout` and
    `row_cap_exceeded` (checklist 6.44): neither is a silent pass, and neither
    is an extraction failure.
    """
    from core.improve.grading import Comparison, GradingConfigError

    executor = executor or _DEFAULT_EXECUTOR
    env = env or {}
    spec = spec or {}
    float_tol = float(spec.get("float_tol") or 0.0)

    # Read-only guard, BOTH sides, BEFORE anything executes (checklist 6.41).
    if not is_read_only(reference):
        raise GradingConfigError(
            "reference SQL is not a read-only statement — refused"
        )
    if not is_read_only(actual):
        return Comparison(
            "fail",
            "candidate SQL is not a read-only statement — refused, not executed",
        )

    ref_result = executor.execute(reference, env)
    if ref_result.error and not ref_result.rows:
        # A reference that errors is an authoring error, surfaced loudly.
        raise GradingConfigError(f"reference SQL failed to execute: {ref_result.error}")

    act_result = executor.execute(actual, env)
    if act_result.timed_out:
        return Comparison("execution_timeout", "candidate query timed out")
    if act_result.row_cap_exceeded:
        return Comparison("row_cap_exceeded", act_result.error or "row cap exceeded")
    if act_result.error:
        return Comparison("fail", f"candidate query errored: {act_result.error}")

    order_sensitive = spec.get("order_sensitive", "auto")
    if order_sensitive == "auto":
        order_sensitive = reference_has_order_by(reference, spec.get("dialect"))
    order_sensitive = bool(order_sensitive)

    expected_rows = _canon_rows(
        ref_result.rows or [], ref_result.columns or [], spec, float_tol
    )
    actual_rows = _canon_rows(
        act_result.rows or [], act_result.columns or [], spec, float_tol
    )

    if not order_sensitive:
        expected_rows = sorted(expected_rows, key=lambda r: repr(r))
        actual_rows = sorted(actual_rows, key=lambda r: repr(r))

    if expected_rows == actual_rows:
        # An empty result set is a legitimate expected value and is reported as
        # such, distinguishable from an execution error above.
        return Comparison(
            "pass",
            f"{len(actual_rows)} row(s) match "
            f"({'ordered' if order_sensitive else 'multiset'})",
        )
    return Comparison(
        "fail",
        f"result sets differ: expected {len(expected_rows)} row(s), "
        f"got {len(actual_rows)} row(s)",
    )


# ── save-time reference validation (§6.3.5 determinism guards) ───────────────

def validate_reference_query(
    sql: str, env: dict | None, executor: SqlExecutor | None = None
) -> dict:
    """Authoring-time checks for a reference query.

    Returns {"errors": [...], "warnings": [...]}. Callers treat a non-empty
    `errors` list as a save-time rejection: silent DB drift or a tie under
    `LIMIT` between a baseline run and a new run is otherwise indistinguishable
    from an agent regression, and will make the ratchet revert good edits.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not is_read_only(sql):
        errors.append("reference SQL is not a read-only statement")
        return {"errors": errors, "warnings": warnings}

    lowered = (sql or "").lower()
    for construct in NONDETERMINISTIC_CONSTRUCTS:
        if construct in lowered:
            warnings.append(
                f"reference uses '{construct.rstrip('(')}' — results will drift "
                "between runs"
            )
    if re.search(r"\b(limit|top)\b", lowered) and not re.search(
        r"\border\s+by\b", lowered
    ):
        warnings.append(
            "reference uses LIMIT/TOP without ORDER BY — ties make the correct "
            "answer ambiguous"
        )

    if env and env.get("connection_id"):
        executor = executor or _DEFAULT_EXECUTOR
        first = executor.execute(sql, env)
        if first.error and not first.rows:
            errors.append(f"reference SQL failed to execute: {first.error}")
        else:
            second = executor.execute(sql, env)
            if second.error and not second.rows:
                errors.append(f"reference SQL failed on re-execution: {second.error}")
            elif (first.rows or []) != (second.rows or []):
                errors.append(
                    "reference SQL is non-deterministic — two executions "
                    "returned different results"
                )
    return {"errors": errors, "warnings": warnings}
