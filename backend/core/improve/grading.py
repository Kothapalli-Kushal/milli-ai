"""
Outcome grading for the Synapse Self-Improvement subsystem (Checkpoint 6).

CP1-CP5 measure *how the agent behaved* (detector/process metrics). This module
adds the second axis: *what it should have produced*. There is exactly ONE
scoring pipeline here (§6.2) — `grading_mode` selects a grader implementation,
never a parallel code path — and both modes emit the same per-input contract:

    InputOutcome = {
      "input_id": str,
      "score": float | None,        # [0,1]; None == N/A
      "na_reason": str | None,      # extraction_failed | judge_unavailable
                                    # | malformed_verdict
      "vetoed": bool,               # a critical check/criterion failed -> 0.0
      "checks": [CheckResult],      # evidence-first per-check detail
    }

    CheckResult = {
      "check_id": str, "status": str, "weight": float, "critical": bool,
      "detail": str, "actual": str | None,
      "trace_file": str | None, "message_idx": int | None,
    }

`CheckResult.status` is one of:
    pass | fail | extraction_failed | execution_timeout | row_cap_exceeded
    | judge_na

Extraction failure is NOT a wrong answer (§6.6). If an extractor finds no
`sql_agent` call at all, the agent never called the tool — telling the tuner
"your SQL is wrong" would make it rewrite the wrong thing. Failed extractions
are excluded from the denominator and counted separately.

Nothing in this module writes traces or adds instrumentation: every extractor
reads the CP1 trace dict exactly as `trace_writer.py` already produces it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

# Statuses that take a check OUT of the denominator rather than failing it.
NA_STATUSES = {"extraction_failed", "judge_na"}
# Statuses that count as a failure (and can trip a critical veto).
FAIL_STATUSES = {"fail", "execution_timeout", "row_cap_exceeded"}

VALID_EXTRACT_FROM = {
    "final_output",
    "last_assistant_message",
    "tool_call_arg",
    "tool_result",
}

VALID_COMPARATORS = {
    "exact",
    "contains_all",
    "regex",
    "numeric",
    "json_equal",
    "sql_equivalent",
    "sql_execution",
    "semantic_match",
    "any_of",
}

# Comparators that decide SQL correctness by data, never by a model's opinion.
SQL_COMPARATORS = {"sql_equivalent", "sql_execution"}


class GradingConfigError(Exception):
    """A benchmark's expected-answer spec is malformed (a SAVE-time error).

    Per §6.3.5 / checklist 6.8 an unparseable *expected* value is a benchmark
    authoring error and must surface when the benchmark is saved, not as a
    run-time check failure that silently scores the agent 0.
    """


# ── grading context ──────────────────────────────────────────────────────────

@dataclass
class GradeContext:
    """Everything a check needs, assembled once per input."""

    user_id: str | None = None
    trace: dict = field(default_factory=dict)
    trace_file: str | None = None
    expected: dict = field(default_factory=dict)
    execution_env: dict | None = None
    judge: Any = None            # judge.JudgeSession | None
    sql_executor: Any = None     # sql_exec.SqlExecutor | None (injected in tests)
    input_id: str = ""
    run_id: str | None = None


# ── extractors (§6.3.3 — v1 set, do not add more) ────────────────────────────

@dataclass
class Extraction:
    value: str | None
    ok: bool
    reason: str = ""
    message_idx: int | None = None


def _messages(trace: dict) -> list[dict]:
    msgs = trace.get("messages")
    return msgs if isinstance(msgs, list) else []


def _tool_calls(msg: dict) -> list[dict]:
    tc = msg.get("tool_calls")
    return tc if isinstance(tc, list) else []


def _extract_final_output(trace: dict, spec: dict) -> Extraction:
    value = trace.get("output")
    if not value:
        return Extraction(None, False, "trace has no final output")
    return Extraction(str(value), True)


def _extract_last_assistant_message(trace: dict, spec: dict) -> Extraction:
    for idx in range(len(_messages(trace)) - 1, -1, -1):
        msg = _messages(trace)[idx]
        if msg.get("role") == "assistant" and msg.get("content"):
            return Extraction(str(msg["content"]), True, message_idx=idx)
    return Extraction(None, False, "no assistant message with content")


def _extract_tool_call_arg(trace: dict, spec: dict) -> Extraction:
    tool = spec.get("tool")
    arg = spec.get("arg")
    if not tool or not arg:
        raise GradingConfigError(
            "extract.from='tool_call_arg' requires both 'tool' and 'arg'"
        )
    occurrence = str(spec.get("occurrence") or "last").lower()
    hits: list[tuple[int, str]] = []
    for idx, msg in enumerate(_messages(trace)):
        for call in _tool_calls(msg):
            fn = call.get("function") or {}
            if fn.get("name") != tool:
                continue
            raw = fn.get("arguments")
            try:
                args = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except (TypeError, ValueError):
                continue  # unparseable call arguments -> this call yields nothing
            if isinstance(args, dict) and args.get(arg) is not None:
                hits.append((idx, str(args[arg])))
    if not hits:
        return Extraction(
            None, False, f"no '{tool}' tool call carrying argument '{arg}'"
        )
    idx, value = hits[0] if occurrence in ("first", "any") else hits[-1]
    return Extraction(value, True, message_idx=idx)


def _extract_tool_result(trace: dict, spec: dict) -> Extraction:
    tool = spec.get("tool")
    if not tool:
        raise GradingConfigError("extract.from='tool_result' requires 'tool'")
    occurrence = str(spec.get("occurrence") or "last").lower()
    msgs = _messages(trace)

    call_ids: list[str] = []
    for msg in msgs:
        for call in _tool_calls(msg):
            if (call.get("function") or {}).get("name") == tool and call.get("id"):
                call_ids.append(str(call["id"]))
    if not call_ids:
        return Extraction(None, False, f"no '{tool}' tool call in the trace")

    hits: list[tuple[int, str]] = []
    for idx, msg in enumerate(msgs):
        if msg.get("role") == "tool" and str(msg.get("tool_call_id") or "") in call_ids:
            if msg.get("content"):
                hits.append((idx, str(msg["content"])))
    if not hits:
        return Extraction(None, False, f"no tool result message for '{tool}'")
    idx, value = hits[0] if occurrence in ("first", "any") else hits[-1]
    return Extraction(value, True, message_idx=idx)


EXTRACTORS: dict[str, Callable[[dict, dict], Extraction]] = {
    "final_output": _extract_final_output,
    "last_assistant_message": _extract_last_assistant_message,
    "tool_call_arg": _extract_tool_call_arg,
    "tool_result": _extract_tool_result,
}


def extract(trace: dict, spec: dict) -> Extraction:
    """Run one extractor spec against a CP1 trace dict.

    An optional `regex` post-processes the extracted string and takes capture
    group 1. A non-match is an EXTRACTION failure, not a wrong answer (§6.3.3).
    """
    source = str((spec or {}).get("from") or "")
    if source not in EXTRACTORS:
        raise GradingConfigError(
            f"unknown extractor '{source}' (allowed: {sorted(VALID_EXTRACT_FROM)})"
        )
    result = EXTRACTORS[source](trace, spec or {})
    pattern = (spec or {}).get("regex")
    if not result.ok or not pattern:
        return result
    try:
        compiled = re.compile(pattern, re.DOTALL)
    except re.error as e:
        raise GradingConfigError(f"invalid extractor regex {pattern!r}: {e}")
    match = compiled.search(result.value or "")
    if not match:
        return Extraction(
            None, False, f"extractor regex {pattern!r} did not match",
            message_idx=result.message_idx,
        )
    value = match.group(1) if match.groups() else match.group(0)
    return Extraction(str(value), True, message_idx=result.message_idx)


# ── comparators (§6.3.4 — v1 set, do not add more) ───────────────────────────

@dataclass
class Comparison:
    status: str          # pass | fail | execution_timeout | row_cap_exceeded | judge_na
    detail: str = ""


def _norm(value: str, case_sensitive: bool) -> str:
    value = str(value).strip()
    return value if case_sensitive else value.lower()


def _cmp_exact(actual: str, spec: dict, ctx: GradeContext) -> Comparison:
    cs = bool(spec.get("case_sensitive", False))
    expected = spec.get("value")
    if expected is None:
        raise GradingConfigError("comparator 'exact' requires 'value'")
    if _norm(actual, cs) == _norm(str(expected), cs):
        return Comparison("pass", "exact match")
    return Comparison("fail", f"expected exact match, got {actual.strip()[:200]!r}")


def _cmp_contains_all(actual: str, spec: dict, ctx: GradeContext) -> Comparison:
    cs = bool(spec.get("case_sensitive", False))
    needles = spec.get("value")
    if not isinstance(needles, list) or not needles:
        raise GradingConfigError(
            "comparator 'contains_all' requires a non-empty list 'value'"
        )
    hay = _norm(actual, cs)
    missing = [n for n in needles if _norm(str(n), cs) not in hay]
    if missing:
        return Comparison("fail", f"missing substring(s): {missing}")
    return Comparison("pass", f"all {len(needles)} substring(s) present")


def _cmp_regex(actual: str, spec: dict, ctx: GradeContext) -> Comparison:
    pattern = spec.get("value")
    if not pattern:
        raise GradingConfigError("comparator 'regex' requires 'value'")
    flags = 0 if spec.get("case_sensitive", False) else re.IGNORECASE
    try:
        compiled = re.compile(str(pattern), flags | re.DOTALL)
    except re.error as e:
        raise GradingConfigError(f"invalid comparator regex {pattern!r}: {e}")
    if compiled.search(actual):
        return Comparison("pass", f"matched {pattern!r}")
    return Comparison("fail", f"did not match {pattern!r}")


_NUMERIC_RE = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")


def _to_float(text: str) -> float | None:
    cleaned = str(text).strip().replace(",", "").replace("$", "")
    try:
        return float(cleaned)
    except ValueError:
        match = _NUMERIC_RE.search(cleaned)
        return float(match.group(0)) if match else None


def _cmp_numeric(actual: str, spec: dict, ctx: GradeContext) -> Comparison:
    if spec.get("value") is None:
        raise GradingConfigError("comparator 'numeric' requires 'value'")
    try:
        expected = float(spec["value"])
    except (TypeError, ValueError):
        raise GradingConfigError(
            f"comparator 'numeric' value {spec['value']!r} is not a number"
        )
    tol = float(spec.get("tol") or 0.0)
    got = _to_float(actual)
    if got is None:
        return Comparison("fail", f"{actual.strip()[:80]!r} is not numeric")
    if abs(got - expected) <= tol:
        return Comparison("pass", f"{got} within +/-{tol} of {expected}")
    return Comparison("fail", f"{got} outside +/-{tol} of {expected}")


def _canonical_json(value: Any, order_sensitive: bool) -> Any:
    if isinstance(value, dict):
        return {k: _canonical_json(v, order_sensitive) for k, v in sorted(value.items())}
    if isinstance(value, list):
        items = [_canonical_json(v, order_sensitive) for v in value]
        if order_sensitive:
            return items
        return sorted(items, key=lambda v: json.dumps(v, sort_keys=True, default=str))
    return value


def _cmp_json_equal(actual: str, spec: dict, ctx: GradeContext) -> Comparison:
    if "value" not in spec:
        raise GradingConfigError("comparator 'json_equal' requires 'value'")
    order_sensitive = spec.get("order_sensitive", True)
    order_sensitive = True if order_sensitive == "auto" else bool(order_sensitive)
    expected_raw = spec["value"]
    if isinstance(expected_raw, str):
        try:
            expected_obj = json.loads(expected_raw)
        except ValueError as e:
            raise GradingConfigError(f"comparator 'json_equal' value is not JSON: {e}")
    else:
        expected_obj = expected_raw
    try:
        actual_obj = json.loads(actual)
    except ValueError as e:
        return Comparison("fail", f"actual value is not JSON: {e}")
    if _canonical_json(actual_obj, order_sensitive) == _canonical_json(
        expected_obj, order_sensitive
    ):
        return Comparison("pass", "structurally equal")
    return Comparison("fail", "JSON structures differ")


def _resolve_reference(spec: dict, ctx: GradeContext) -> str:
    """Resolve a comparator's SQL reference, incl. the `$expected.<field>` form."""
    ref = spec.get("reference", spec.get("value"))
    if ref is None:
        raise GradingConfigError(
            f"comparator '{spec.get('type')}' requires 'reference' or 'value'"
        )
    ref = str(ref)
    if ref.startswith("$expected."):
        key = ref[len("$expected."):]
        resolved = (ctx.expected or {}).get(key)
        if not resolved:
            raise GradingConfigError(
                f"reference '{ref}' does not resolve — expected.{key} is not set"
            )
        return str(resolved)
    return ref


def _cmp_sql_equivalent(actual: str, spec: dict, ctx: GradeContext) -> Comparison:
    from core.improve import sql_compare
    reference = _resolve_reference(spec, ctx)
    return sql_compare.compare_ast(actual, reference, spec.get("dialect"))


def _cmp_sql_execution(actual: str, spec: dict, ctx: GradeContext) -> Comparison:
    from core.improve import sql_compare
    reference = _resolve_reference(spec, ctx)
    return sql_compare.compare_execution(
        actual, reference, spec, ctx.execution_env, ctx.sql_executor
    )


def _cmp_semantic_match(actual: str, spec: dict, ctx: GradeContext) -> Comparison:
    claim = spec.get("value")
    if not claim:
        raise GradingConfigError("comparator 'semantic_match' requires 'value'")
    if ctx.judge is None:
        return Comparison("judge_na", "no judge session available")
    verdict = ctx.judge.semantic_match(actual, str(claim), input_id=ctx.input_id)
    if verdict is None:
        return Comparison("judge_na", "judge returned no usable verdict")
    return Comparison(
        "pass" if verdict else "fail",
        f"judge verdict on claim: {str(claim)[:120]}",
    )


def _cmp_any_of(actual: str, spec: dict, ctx: GradeContext) -> Comparison:
    options = spec.get("options")
    if not isinstance(options, list) or not options:
        raise GradingConfigError(
            "comparator 'any_of' requires a non-empty 'options' list"
        )
    details: list[str] = []
    statuses: list[str] = []
    for option in options:
        outcome = run_comparator(actual, option, ctx)
        if outcome.status == "pass":
            return Comparison("pass", f"matched option '{option.get('type')}'")
        statuses.append(outcome.status)
        details.append(f"{option.get('type')}: {outcome.detail}")
    # Each option is evaluated exactly once — re-running would double-charge
    # the judge for `semantic_match` options.
    if all(s in NA_STATUSES for s in statuses):
        return Comparison("judge_na", "every any_of option was N/A")
    return Comparison("fail", "no option matched - " + "; ".join(details)[:400])


COMPARATORS: dict[str, Callable[[str, dict, GradeContext], Comparison]] = {
    "exact": _cmp_exact,
    "contains_all": _cmp_contains_all,
    "regex": _cmp_regex,
    "numeric": _cmp_numeric,
    "json_equal": _cmp_json_equal,
    "sql_equivalent": _cmp_sql_equivalent,
    "sql_execution": _cmp_sql_execution,
    "semantic_match": _cmp_semantic_match,
    "any_of": _cmp_any_of,
}


def run_comparator(actual: str, spec: dict, ctx: GradeContext) -> Comparison:
    ctype = str((spec or {}).get("type") or "")
    if ctype not in COMPARATORS:
        raise GradingConfigError(
            f"unknown comparator '{ctype}' (allowed: {sorted(VALID_COMPARATORS)})"
        )
    return COMPARATORS[ctype](actual, spec or {}, ctx)


# ── per-check execution ──────────────────────────────────────────────────────

def run_check(check: dict, ctx: GradeContext) -> dict:
    """One Check -> one CheckResult. Never raises for agent-side problems.

    A malformed *benchmark* (unknown extractor/comparator, missing required
    spec key) still raises GradingConfigError — that is an authoring bug and
    §6.3.5 requires it to be loud, not scored as a failure.
    """
    check_id = str(check.get("id") or "check")
    weight = float(check.get("weight", 1.0))
    critical = bool(check.get("critical", False))
    result = {
        "check_id": check_id,
        "weight": weight,
        "critical": critical,
        "status": "fail",
        "detail": "",
        "actual": None,
        "trace_file": ctx.trace_file,
        "message_idx": None,
    }

    extraction = extract(ctx.trace, check.get("extract") or {})
    result["message_idx"] = extraction.message_idx
    if not extraction.ok:
        result["status"] = "extraction_failed"
        result["detail"] = extraction.reason
        return result

    result["actual"] = extraction.value
    outcome = run_comparator(extraction.value or "", check.get("compare") or {}, ctx)
    result["status"] = outcome.status
    result["detail"] = outcome.detail
    return result


# ── input scoring (§6.3.2) ───────────────────────────────────────────────────

def _na_reason_from(check_results: list[dict]) -> str:
    statuses = {r["status"] for r in check_results}
    if statuses == {"extraction_failed"}:
        return "extraction_failed"
    if "judge_na" in statuses:
        return "judge_unavailable"
    return "extraction_failed"


def _assemble(input_id: str, check_results: list[dict]) -> dict:
    """Weighted partial credit + critical veto -> InputOutcome."""
    scored = [r for r in check_results if r["status"] not in NA_STATUSES]
    if not scored:
        return {
            "input_id": input_id,
            "score": None,
            "na_reason": _na_reason_from(check_results) if check_results else "extraction_failed",
            "vetoed": False,
            "checks": check_results,
        }

    vetoed = any(
        r["critical"] and r["status"] in FAIL_STATUSES for r in scored
    )
    total = sum(r["weight"] for r in scored)
    passed = sum(r["weight"] for r in scored if r["status"] == "pass")
    score = 0.0 if vetoed else (round(passed / total, 6) if total > 0 else 0.0)
    return {
        "input_id": input_id,
        "score": score,
        "na_reason": None,
        "vetoed": vetoed,
        "checks": check_results,
    }


def grade_deterministic(ctx: GradeContext) -> dict:
    """Deterministic mode (§6.3): extractors + comparators, no LLM by default."""
    checks = (ctx.expected or {}).get("checks") or []
    if not checks:
        return {
            "input_id": ctx.input_id,
            "score": None,
            "na_reason": "extraction_failed",
            "vetoed": False,
            "checks": [],
        }
    return _assemble(ctx.input_id, [run_check(c, ctx) for c in checks])


# ── rubric mode (§6.4) ───────────────────────────────────────────────────────

def _criterion_key_point_coverage(criterion: dict, ctx: GradeContext) -> dict:
    """Atomic binary judge calls, one per key point — markedly more stable
    across runs than holistic scoring (§6.4)."""
    key_points = (ctx.expected or {}).get("key_points") or []
    forbidden = (ctx.expected or {}).get("forbidden") or []
    base = {
        "check_id": str(criterion.get("id") or "coverage"),
        "weight": float(criterion.get("weight", 1.0)),
        "critical": bool(criterion.get("critical", False)),
        "critical_floor": float(criterion.get("critical_floor", 1.0)),
        "trace_file": ctx.trace_file,
        "message_idx": None,
        "actual": None,
    }
    if not key_points and not forbidden:
        return {**base, "status": "judge_na", "normalized": None,
                "detail": "no key_points or forbidden items on this input"}
    if ctx.judge is None:
        return {**base, "status": "judge_na", "normalized": None,
                "detail": "no judge session available"}

    output = str(ctx.trace.get("output") or "")
    verdicts = ctx.judge.key_point_coverage(
        output, key_points, forbidden, criterion_id=base["check_id"],
        input_id=ctx.input_id,
    )
    if verdicts is None:
        return {**base, "status": "judge_na", "normalized": None,
                "detail": "judge returned no usable verdict"}

    hit_forbidden = [k for k, v in verdicts.get("forbidden", {}).items() if v]
    if hit_forbidden:
        return {**base, "status": "fail", "normalized": 0.0,
                "detail": f"forbidden claim(s) present: {hit_forbidden}"}

    matched = verdicts.get("key_points", {})
    total = sum(float(kp.get("weight", 1.0)) for kp in key_points) or 1.0
    got = sum(
        float(kp.get("weight", 1.0))
        for kp in key_points
        if matched.get(str(kp.get("id")))
    )
    normalized = round(got / total, 6)
    return {
        **base,
        "status": "pass" if normalized >= 1.0 else "fail",
        "normalized": normalized,
        "detail": f"{len([1 for v in matched.values() if v])}/{len(key_points)} "
                  f"key point(s) covered",
    }


def _criterion_anchored(criterion: dict, ctx: GradeContext) -> dict:
    scale = int(criterion.get("scale") or 0)
    anchors = criterion.get("anchors") or {}
    question = criterion.get("question")
    base = {
        "check_id": str(criterion.get("id") or "anchored"),
        "weight": float(criterion.get("weight", 1.0)),
        "critical": bool(criterion.get("critical", False)),
        "critical_floor": float(criterion.get("critical_floor", 1.0)),
        "trace_file": ctx.trace_file,
        "message_idx": None,
        "actual": None,
    }
    if scale < 1 or not question:
        raise GradingConfigError(
            f"anchored criterion '{base['check_id']}' requires 'question' and scale >= 1"
        )
    missing = [str(i) for i in range(scale + 1) if str(i) not in anchors]
    if missing:
        raise GradingConfigError(
            f"anchored criterion '{base['check_id']}' is missing anchors for "
            f"level(s) {missing} — every level needs a written anchor"
        )
    if ctx.judge is None:
        return {**base, "status": "judge_na", "normalized": None,
                "detail": "no judge session available"}

    output = str(ctx.trace.get("output") or "")
    level = ctx.judge.anchored(
        output, question, anchors, scale,
        criterion_id=base["check_id"], input_id=ctx.input_id,
    )
    if level is None:
        return {**base, "status": "judge_na", "normalized": None,
                "detail": "judge returned no usable verdict"}
    normalized = round(float(level) / float(scale), 6)
    return {
        **base,
        "status": "pass" if normalized >= 1.0 else "fail",
        "normalized": normalized,
        "detail": f"level {level}/{scale}: {anchors.get(str(level), '')}"[:200],
    }


def _criterion_deterministic(criterion: dict, ctx: GradeContext) -> dict:
    """Embeds a §6.3 check inside a rubric — this is why a web agent can have
    'cited >= 2 sources' graded for free and 'synthesis quality' judged."""
    check = criterion.get("check")
    if not isinstance(check, dict):
        raise GradingConfigError(
            f"deterministic criterion '{criterion.get('id')}' requires a 'check' object"
        )
    check = {**check, "id": str(criterion.get("id") or check.get("id") or "check")}
    result = run_check(check, ctx)
    result["weight"] = float(criterion.get("weight", 1.0))
    result["critical"] = bool(criterion.get("critical", False))
    result["critical_floor"] = float(criterion.get("critical_floor", 1.0))
    result["normalized"] = (
        None if result["status"] in NA_STATUSES
        else (1.0 if result["status"] == "pass" else 0.0)
    )
    return result


CRITERION_KINDS: dict[str, Callable[[dict, GradeContext], dict]] = {
    "key_point_coverage": _criterion_key_point_coverage,
    "anchored": _criterion_anchored,
    "deterministic": _criterion_deterministic,
}


def grade_rubric(ctx: GradeContext, rubric: dict) -> dict:
    """Rubric mode (§6.4). Same InputOutcome contract as deterministic mode."""
    criteria = (rubric or {}).get("criteria") or []
    if not criteria:
        raise GradingConfigError(f"rubric '{rubric.get('id')}' has no criteria")

    results = []
    for criterion in criteria:
        kind = str(criterion.get("kind") or "")
        if kind not in CRITERION_KINDS:
            raise GradingConfigError(
                f"unknown criterion kind '{kind}' "
                f"(allowed: {sorted(CRITERION_KINDS)})"
            )
        results.append(CRITERION_KINDS[kind](criterion, ctx))

    scored = [r for r in results if r["status"] not in NA_STATUSES]
    if not scored:
        statuses = {r["status"] for r in results}
        return {
            "input_id": ctx.input_id,
            "score": None,
            "na_reason": "malformed_verdict" if "judge_na" in statuses
                         else "extraction_failed",
            "vetoed": False,
            "checks": results,
        }

    vetoed = any(
        r.get("critical") and float(r.get("normalized") or 0.0)
        < float(r.get("critical_floor", 1.0))
        for r in scored
    )
    total = sum(r["weight"] for r in scored)
    got = sum(r["weight"] * float(r.get("normalized") or 0.0) for r in scored)
    score = 0.0 if vetoed else (round(got / total, 6) if total > 0 else 0.0)
    return {
        "input_id": ctx.input_id,
        "score": score,
        "na_reason": None,
        "vetoed": vetoed,
        "checks": results,
    }


# ── the single scoring pipeline (§6.2) ───────────────────────────────────────

def grade_input(ctx: GradeContext, mode: str, rubric: dict | None = None) -> dict:
    """The ONE entry point. `mode` selects a grader, not a parallel code path."""
    if mode == "deterministic":
        return grade_deterministic(ctx)
    if mode == "rubric":
        if not rubric:
            raise GradingConfigError(
                f"input '{ctx.input_id}' is rubric-graded but no rubric was resolved"
            )
        return grade_rubric(ctx, rubric)
    raise GradingConfigError(f"unknown grading_mode '{mode}'")


# ── outcome axis aggregation + two-axis composite (§6.1) ─────────────────────

def aggregate_outcomes(
    outcomes: list[dict], weights: dict[str, float] | None = None
) -> dict:
    """Weighted mean of per-input outcome scores, N/A inputs excluded.

    Returns {"outcome_score": float|None, "outcome_na": bool,
             "graded_input_count": int, "na_input_count": int,
             "extraction_failed_count": int, "extraction_failed_rate": float}.

    Never substitutes 0 for N/A (§6.1): "the judge was unavailable" and "the
    agent got everything wrong" must not be the same number, or the ratchet
    reverts good edits on a grading outage.
    """
    weights = weights or {}
    num = den = 0.0
    na_count = 0
    extraction_failed = 0
    total_checks = 0
    for outcome in outcomes:
        for check in outcome.get("checks") or []:
            total_checks += 1
            if check.get("status") == "extraction_failed":
                extraction_failed += 1
        if outcome.get("score") is None:
            na_count += 1
            continue
        weight = float(weights.get(outcome["input_id"], 1.0))
        num += weight * float(outcome["score"])
        den += weight

    graded = len(outcomes) - na_count
    return {
        "outcome_score": round(num / den, 6) if den > 0 else None,
        "outcome_na": den <= 0,
        "graded_input_count": graded,
        "na_input_count": na_count,
        "extraction_failed_count": extraction_failed,
        "extraction_failed_rate": (
            round(extraction_failed / total_checks, 6) if total_checks else 0.0
        ),
    }


def composite_score(
    process_score: float | None,
    outcome_score: float | None,
    process_weight: float = 1.0,
    outcome_weight: float = 0.0,
) -> float | None:
    """`process_weight x process + outcome_weight x outcome`, normalized.

    Weights need not sum to 1 — they are divided by their sum. An axis that is
    None (or zero-weighted) drops out entirely rather than contributing 0, so
    `outcome_weight: 0` reproduces CP4 exactly and a fully-N/A outcome axis
    falls back to the process axis alone.
    """
    parts: list[tuple[float, float]] = []
    if process_score is not None and float(process_weight) > 0:
        parts.append((float(process_weight), float(process_score)))
    if outcome_score is not None and float(outcome_weight) > 0:
        parts.append((float(outcome_weight), float(outcome_score)))
    total = sum(w for w, _ in parts)
    if total <= 0:
        return None
    return round(sum(w * s for w, s in parts) / total, 6)
