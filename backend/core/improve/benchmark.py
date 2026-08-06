"""
Benchmark suite for the Synapse Self-Improvement subsystem (Checkpoint 4).

Benchmarks are STANDALONE objects (§0.6.1) stored at
`benchmarks/<benchmark_id>.json`, referenced by id, and reusable across
targets (the run entry point accepts a target override). Schema (Appendix A):

    {id, name, target_object_id,
     inputs: [{prompt, expected_metric_hints, images?}],
     scorer: {metrics: {name: weight}}}

Execution goes through Synapse's authoritative surfaces only: each input runs
through `run_agent_step` (agents) or `OrchestrationEngine.run`
(orchestrations), so traces are emitted by the Checkpoint-1 hook path — there
is no separate benchmark trace mechanism. Detectors then score the collected
traces.

Scoring: weighted composite in [0, 1]. For each metric with weight > 0:
- `success`      — fraction of traces with success=True.
- detector name  — "good rate" over applicable traces: positive detectors
  (recovery, clean_success) count a hit as good; problem detectors count a
  NON-hit as good.
Composite = Σ(weight × rate) / Σ(weight), over metrics whose denominator > 0.
A metric with weight 0 (or omitted) is excluded — the escape hatch for any
detector judged unreliable (checklist 4.7).

Reproducibility (checklist 4.12): detectors and the scorer are deterministic,
so two runs of the same benchmark on the same version must score within
±0.02 (2%, matching Appendix C); the only variance source is the model.

Results are appended to `runs.json` as `{"type": "benchmark", ...}` records
(checklist 4.8) and can optionally stamp `baseline_score` / `new_score` onto
an ImprovementRun for before/after comparisons.

--------------------------------------------------------------------------
Checkpoint 6 — OUTCOME GRADING (additive; the CP4 path is untouched)

CP4 measures *how the agent behaved*. CP6 adds a second axis measuring *what
it should have produced*:

    composite = process_weight x process_score + outcome_weight x outcome_score

`process_score` is the CP4 weighted detector composite above, computed by the
existing `score_traces` with byte-identical arithmetic. `outcome_score` is the
weighted mean of per-input outcome scores from `grading.py`.

Dispatch is by `schema_version`: absent or `1` -> CP4 semantics exactly (no
grading stage, no new result fields, same `score`). `2` -> the grading stage
runs and the record gains the Appendix A6 fields. Back-compatibility is a hard
exit criterion, checked by test (6.4), not by inspection.
--------------------------------------------------------------------------
"""
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from core.improve import runs as runs_mod
from core.improve.trace_writer import ensure_user_layout

# Two benchmark runs on the same version must agree within this (4.12).
SCORE_VARIANCE_THRESHOLD = 0.02

# CP6 §6.8 — reproducibility thresholds by mode. Deterministic + `strict` on a
# pinned snapshot is held to EXACT equality: no LLM is involved and the data is
# fixed, so any drift is a bug in an extractor, a nondeterministic agent, or a
# nondeterministic query, and must fail loudly.
OUTCOME_VARIANCE_THRESHOLD_STRICT_EXACT = 0.0
OUTCOME_VARIANCE_THRESHOLD_UNPINNED = 0.02
OUTCOME_VARIANCE_THRESHOLD_RUBRIC = 0.05

# Above this share of failed extractions the score describes the benchmark's
# extractors, not the agent, and must not be compared (§6.6).
EXTRACTION_FAILURE_LIMIT = 0.5

# The CP4 field set. A schema_version 1 benchmark is persisted with EXACTLY
# these keys so its file — and therefore its score — is unchanged by CP6.
CP4_BENCHMARK_KEYS = ("id", "name", "target_object_id", "inputs", "scorer")
CP4_INPUT_KEYS = ("prompt", "expected_metric_hints", "images")
CP4_SCORER_KEYS = ("metrics",)


class BenchmarkNotFound(Exception):
    pass


class BenchmarkTargetNotFound(Exception):
    pass


# ── Schema (checklist 4.1; extended per Appendix A6) ──────────────────────────

class ExecutionEnv(BaseModel):
    """References an EXISTING Synapse SQL connection by id (checklist 6.40).
    CP6 introduces no connection manager, credential store, or DB config."""
    connection_id: str
    snapshot_id: str | None = None
    timeout_s: int = 10
    max_rows: int = 5000


class KFoldPolicy(BaseModel):
    k: int = 5
    rotation: Literal["per_iteration", "all_folds"] = "per_iteration"


class SplitPolicy(BaseModel):
    mode: Literal["explicit", "random", "kfold"] = "explicit"
    seed: int = 1337
    ratios: dict[str, float] | None = None
    kfold: KFoldPolicy | None = None


class AugmentationConstraints(BaseModel):
    preserve_entities: bool = True
    preserve_numbers: bool = True
    preserve_quoted_literals: bool = True
    max_length_ratio: float = 1.5
    forbid_added_constraints: bool = True
    forbid_answer_leakage: bool = True


class AugmentationPolicy(BaseModel):
    enabled: bool = False
    variants_per_input: int = 2
    model: str | None = None
    seed: int = 1337
    apply_to_splits: list[str] = ["train", "holdout"]
    constraints: AugmentationConstraints = AugmentationConstraints()


class JudgeConfig(BaseModel):
    model: str | None = None
    samples: int = 1
    temperature: float = 0.0
    max_concurrency: int = 4


class BenchmarkInput(BaseModel):
    # CP4 fields
    prompt: str
    expected_metric_hints: dict[str, float] = {}
    images: list[str] | None = None
    # CP6 fields — all optional so a CP4 input validates unchanged
    id: str | None = None
    weight: float = 1.0
    split: Literal["train", "holdout", "regression"] = "train"
    fold: int | None = None
    grading_mode: Literal["deterministic", "rubric"] | None = None
    rubric_id: str | None = None
    execution_env: ExecutionEnv | None = None
    expected: dict[str, Any] | None = None
    parent_input_id: str | None = None
    is_augmented: bool = False
    approved: bool = True


class BenchmarkScorer(BaseModel):
    metrics: dict[str, float] = {}  # detector name or "success" -> weight; 0/absent = excluded
    # CP6: axis weights. `outcome_weight: 0` reproduces CP4 exactly.
    process_weight: float = 1.0
    outcome_weight: float = 0.0
    judge: JudgeConfig | None = None


class Benchmark(BaseModel):
    id: str
    name: str
    target_object_id: str
    inputs: list[BenchmarkInput] = Field(min_length=1)
    scorer: BenchmarkScorer = BenchmarkScorer()
    # CP6 — absent/1 means CP4 semantics
    schema_version: int = 1
    grading_mode: Literal["deterministic", "rubric"] | None = None
    grading_strictness: Literal["strict", "mixed"] | None = None  # derived, not authored
    rubric_id: str | None = None
    rubric_version: int | None = None
    execution_env: ExecutionEnv | None = None
    split_policy: SplitPolicy = SplitPolicy()
    augmentation: AugmentationPolicy | None = None


DEFAULT_SCORER_METRICS = {"success": 1.0, "clean_success": 1.0}


# ── Storage (standalone objects — checklist 4.2) ──────────────────────────────

def _benchmarks_dir(user_id: str | None) -> str:
    return os.path.join(ensure_user_layout(user_id), "benchmarks")


def _strip_to_cp4(validated: dict) -> dict:
    """Persist a schema_version 1 benchmark with EXACTLY its CP4 key set.

    CP6 must not rewrite CP4 files. Adding defaulted CP6 keys would change the
    stored bytes of every legacy benchmark on the next save for no behavioral
    gain, so v1 suites are written back in their original shape (checklist 6.4).
    """
    out = {k: validated[k] for k in CP4_BENCHMARK_KEYS if k in validated}
    out["inputs"] = [
        {k: item[k] for k in CP4_INPUT_KEYS if k in item}
        for item in validated.get("inputs") or []
    ]
    scorer = validated.get("scorer") or {}
    out["scorer"] = {k: scorer[k] for k in CP4_SCORER_KEYS if k in scorer}
    return out


def save_benchmark(
    user_id: str | None, benchmark: dict, *, sql_executor=None
) -> dict:
    """Validate and persist. CP6 adds save-time expected-answer validation.

    An unparseable expected value, an unknown extractor/comparator, or a
    `semantic_match` on a SQL argument is a benchmark AUTHORING error and is
    raised here (checklists 6.8 / 6.45) — never deferred to run time, where it
    would silently score the agent 0 and drive the ratchet to revert good edits.
    """
    validated = Benchmark.model_validate(benchmark).model_dump()

    if int(validated.get("schema_version") or 1) < 2:
        payload = _strip_to_cp4(validated)
    else:
        validate_expected(validated, sql_executor=sql_executor)
        validated["grading_strictness"] = derive_strictness(validated)
        _assign_input_ids(validated)
        # Variants must never drift off their parent's split/fold, whatever the
        # caller sent. Enforced in code, not in documentation (§6.5.3).
        from core.improve.splits import inherit_from_parents
        inherit_from_parents(validated["inputs"])
        payload = validated

    path = os.path.join(_benchmarks_dir(user_id), f"{payload['id']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return payload


def _assign_input_ids(validated: dict) -> None:
    """Every v2 input needs a stable id — outcomes are keyed on it."""
    for i, item in enumerate(validated.get("inputs") or []):
        if not item.get("id"):
            item["id"] = f"in_{i + 1:03d}"


def derive_strictness(benchmark: dict) -> str:
    """`mixed` when ANY check uses `semantic_match`, else `strict` (§6.3.6).

    Derived, never authored. A single convenience check must not silently
    downgrade a benchmark's reproducibility guarantees without saying so — the
    label is recorded per result and drives threshold selection in §6.8.
    """
    for item in benchmark.get("inputs") or []:
        for check in ((item.get("expected") or {}).get("checks") or []):
            if _uses_semantic_match(check.get("compare") or {}):
                return "mixed"
    return "strict"


def _uses_semantic_match(compare: dict) -> bool:
    if str(compare.get("type")) == "semantic_match":
        return True
    if str(compare.get("type")) == "any_of":
        return any(_uses_semantic_match(o or {}) for o in compare.get("options") or [])
    return False


_SQL_ARG_NAMES = {"query", "sql", "statement", "sql_query"}


def validate_expected(benchmark: dict, *, sql_executor=None) -> dict:
    """Save-time validation of every expected-answer spec in a v2 benchmark.

    Returns {"warnings": [...]}. Raises GradingConfigError on anything that
    would otherwise become a run-time surprise.
    """
    from core.improve import grading, sql_compare

    warnings: list[str] = []
    suite_mode = benchmark.get("grading_mode")

    for item in benchmark.get("inputs") or []:
        input_id = item.get("id") or item.get("prompt", "")[:40]
        mode = item.get("grading_mode") or suite_mode
        expected = item.get("expected") or {}
        if isinstance(expected.get("$ref"), str):
            continue  # augmented variant — validated through its parent
        if not mode:
            continue

        if mode == "rubric":
            if not (item.get("rubric_id") or benchmark.get("rubric_id")):
                raise grading.GradingConfigError(
                    f"input '{input_id}' is rubric-graded but no rubric_id is set"
                )

        env = item.get("execution_env") or benchmark.get("execution_env")
        for check in expected.get("checks") or []:
            _validate_check(check, expected, input_id, env, warnings,
                            sql_executor=sql_executor)

    return {"warnings": warnings}


def _validate_check(
    check: dict, expected: dict, input_id: str, env: dict | None,
    warnings: list[str], *, sql_executor=None,
) -> None:
    from core.improve import grading, sql_compare

    extract_spec = check.get("extract") or {}
    compare_spec = check.get("compare") or {}
    check_id = check.get("id") or "?"
    where = f"input '{input_id}' check '{check_id}'"

    source = str(extract_spec.get("from") or "")
    if source not in grading.VALID_EXTRACT_FROM:
        raise grading.GradingConfigError(
            f"{where}: unknown extractor '{source}' "
            f"(allowed: {sorted(grading.VALID_EXTRACT_FROM)})"
        )
    if source == "tool_call_arg" and not (
        extract_spec.get("tool") and extract_spec.get("arg")
    ):
        raise grading.GradingConfigError(
            f"{where}: extractor 'tool_call_arg' requires 'tool' and 'arg'"
        )
    if source == "tool_result" and not extract_spec.get("tool"):
        raise grading.GradingConfigError(
            f"{where}: extractor 'tool_result' requires 'tool'"
        )
    if extract_spec.get("regex"):
        try:
            import re as _re
            _re.compile(str(extract_spec["regex"]))
        except Exception as e:
            raise grading.GradingConfigError(
                f"{where}: invalid extractor regex: {e}"
            )

    _validate_compare(compare_spec, extract_spec, expected, where, env,
                      warnings, sql_executor=sql_executor)


def _validate_compare(
    compare_spec: dict, extract_spec: dict, expected: dict, where: str,
    env: dict | None, warnings: list[str], *, sql_executor=None,
) -> None:
    from core.improve import grading, sql_compare

    ctype = str(compare_spec.get("type") or "")
    if ctype not in grading.VALID_COMPARATORS:
        raise grading.GradingConfigError(
            f"{where}: unknown comparator '{ctype}' "
            f"(allowed: {sorted(grading.VALID_COMPARATORS)})"
        )

    if ctype == "any_of":
        options = compare_spec.get("options")
        if not isinstance(options, list) or not options:
            raise grading.GradingConfigError(
                f"{where}: comparator 'any_of' requires a non-empty 'options' list"
            )
        for option in options:
            _validate_compare(option or {}, extract_spec, expected, where, env,
                              warnings, sql_executor=sql_executor)
        return

    # §6.3.6 / checklist 6.45 — semantic_match is NEVER permitted for SQL.
    # SQL correctness is decided by sql_execution, not by a model's opinion.
    if ctype == "semantic_match":
        if str(extract_spec.get("from")) == "tool_call_arg" and str(
            extract_spec.get("arg") or ""
        ).lower() in _SQL_ARG_NAMES:
            raise grading.GradingConfigError(
                f"{where}: 'semantic_match' is not permitted on a SQL argument "
                f"('{extract_spec.get('arg')}') — use 'sql_execution', which "
                "decides equivalence by data rather than by a model's opinion"
            )
        if not compare_spec.get("value"):
            raise grading.GradingConfigError(
                f"{where}: comparator 'semantic_match' requires 'value'"
            )
        return

    if ctype in grading.SQL_COMPARATORS:
        reference = compare_spec.get("reference", compare_spec.get("value"))
        if reference is None:
            raise grading.GradingConfigError(
                f"{where}: comparator '{ctype}' requires 'reference' or 'value'"
            )
        reference = str(reference)
        if reference.startswith("$expected."):
            key = reference[len("$expected."):]
            if not expected.get(key):
                raise grading.GradingConfigError(
                    f"{where}: reference '{reference}' does not resolve — "
                    f"expected.{key} is not set"
                )
            reference = str(expected[key])

        # A parse failure on the EXPECTED value is an authoring error (§6.3.5).
        try:
            sql_compare.normalize_sql(reference, compare_spec.get("dialect"))
        except ValueError as e:
            raise grading.GradingConfigError(
                f"{where}: reference SQL is not parseable: {e}"
            )
        except RuntimeError as e:  # sqlglot missing — surface, do not pretend
            raise grading.GradingConfigError(f"{where}: {e}")

        if ctype == "sql_execution":
            report = sql_compare.validate_reference_query(
                reference, env, executor=sql_executor
            )
            if report["errors"]:
                raise grading.GradingConfigError(
                    f"{where}: " + "; ".join(report["errors"])
                )
            warnings.extend(f"{where}: {w}" for w in report["warnings"])
        return

    # Remaining comparators all need a `value`; `numeric` needs a numeric one.
    if "value" not in compare_spec:
        raise grading.GradingConfigError(
            f"{where}: comparator '{ctype}' requires 'value'"
        )
    if ctype == "numeric":
        try:
            float(compare_spec["value"])
        except (TypeError, ValueError):
            raise grading.GradingConfigError(
                f"{where}: comparator 'numeric' value "
                f"{compare_spec['value']!r} is not a number"
            )
    if ctype == "contains_all" and not isinstance(compare_spec["value"], list):
        raise grading.GradingConfigError(
            f"{where}: comparator 'contains_all' requires a list 'value'"
        )
    if ctype == "regex":
        try:
            import re as _re
            _re.compile(str(compare_spec["value"]))
        except Exception as e:
            raise grading.GradingConfigError(f"{where}: invalid comparator regex: {e}")
    if ctype == "json_equal" and isinstance(compare_spec["value"], str):
        try:
            json.loads(compare_spec["value"])
        except ValueError as e:
            raise grading.GradingConfigError(
                f"{where}: comparator 'json_equal' value is not JSON: {e}"
            )


def load_benchmark(user_id: str | None, benchmark_id: str) -> dict:
    path = os.path.join(_benchmarks_dir(user_id), f"{benchmark_id}.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        raise BenchmarkNotFound(benchmark_id)


def list_benchmarks(user_id: str | None) -> list[dict]:
    bdir = _benchmarks_dir(user_id)
    out = []
    for name in sorted(os.listdir(bdir)):
        if name.endswith(".json"):
            try:
                with open(os.path.join(bdir, name), encoding="utf-8") as f:
                    out.append(json.load(f))
            except Exception:
                continue
    return out


def delete_benchmark(user_id: str | None, benchmark_id: str) -> bool:
    path = os.path.join(_benchmarks_dir(user_id), f"{benchmark_id}.json")
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


# ── Scorer (checklists 4.6 / 4.7) ─────────────────────────────────────────────

def score_traces(traces: list[dict], metrics: dict[str, float] | None) -> dict:
    """Weighted composite over detector results + the `success` pseudo-metric.

    Returns {"score": float|None, "per_metric": {name: {rate, weight,
    numerator, denominator}}}. `score` is None when no weighted metric has an
    applicable denominator (never a silent zero).
    """
    from core.improve.detectors import DETECTORS
    from core.improve.insights import _POSITIVE_DETECTORS
    from core.improve.runner import corpus_duration_stats, run_detectors_on_trace

    weights = {k: float(v) for k, v in (metrics or DEFAULT_SCORER_METRICS).items()
               if float(v) > 0}
    stats = corpus_duration_stats([("", t) for t in traces])
    all_results = [run_detectors_on_trace(t, stats) for t in traces]

    per_metric: dict[str, dict] = {}
    weighted_sum = 0.0
    weight_total = 0.0
    for name, weight in sorted(weights.items()):
        if name == "success":
            den = len(traces)
            num = sum(1 for t in traces if t.get("success"))
        elif name in DETECTORS:
            den = num = 0
            for res in all_results:
                r = res[name]
                if not r["applicable"]:
                    continue
                den += 1
                good = r["hit"] if name in _POSITIVE_DETECTORS else not r["hit"]
                num += 1 if good else 0
        else:
            per_metric[name] = {"rate": "N/A", "weight": weight,
                                "numerator": 0, "denominator": 0,
                                "error": "unknown metric"}
            continue
        rate = round(num / den, 4) if den > 0 else "N/A"
        per_metric[name] = {"rate": rate, "weight": weight,
                            "numerator": num, "denominator": den}
        if den > 0:
            weighted_sum += weight * (num / den)
            weight_total += weight

    score = round(weighted_sum / weight_total, 4) if weight_total > 0 else None
    return {"score": score, "per_metric": per_metric}


# ── Outcome axis (Checkpoint 6) ───────────────────────────────────────────────

def resolve_expected(benchmark: dict, item: dict) -> dict:
    """Resolve an input's `expected`, following `{"$ref": <input_id>}`.

    Augmented variants SHARE their parent's expectation rather than copying it
    (§6.5.3): a copied expectation drifts the moment the parent is edited.
    """
    expected = item.get("expected") or {}
    ref = expected.get("$ref") if isinstance(expected, dict) else None
    if not ref:
        return expected
    for candidate in benchmark.get("inputs") or []:
        if candidate.get("id") == ref:
            return candidate.get("expected") or {}
    from core.improve.grading import GradingConfigError
    raise GradingConfigError(
        f"input '{item.get('id')}' references expected of unknown input '{ref}'"
    )


def input_grading_mode(benchmark: dict, item: dict) -> str | None:
    """Per-input override wins over the benchmark-level toggle (§6.2)."""
    return item.get("grading_mode") or benchmark.get("grading_mode")


def _needs_judge(benchmark: dict) -> bool:
    """True when any input can reach the judge (rubric mode or semantic_match)."""
    if derive_strictness(benchmark) == "mixed":
        return True
    for item in benchmark.get("inputs") or []:
        if input_grading_mode(benchmark, item) == "rubric":
            return True
    return False


def _build_judge(benchmark: dict, user_id: str | None, run_id: str | None,
                 rubric: dict | None, judge_model: str | None):
    from core.config import load_settings
    from core.improve import judge as judge_mod

    try:
        settings = load_settings()
    except Exception:
        settings = {}
    cfg = (benchmark.get("scorer") or {}).get("judge") or {}
    model = judge_mod.resolve_judge_model(settings, judge_model or cfg.get("model"))
    return judge_mod.JudgeSession(
        user_id,
        model=model,
        settings=settings,
        run_id=run_id,
        samples=int(cfg.get("samples") or 1),
        temperature=float(cfg.get("temperature") or 0.0),
        max_concurrency=int(cfg.get("max_concurrency") or 4),
        rubric_id=(rubric or {}).get("id"),
        rubric_version=(rubric or {}).get("version"),
        rubric_content_hash=(rubric or {}).get("content_hash"),
    ), model, settings


class BudgetExceeded(Exception):
    """Projected spend for this evaluation exceeds `improve_budget_usd`."""


def project_all_folds_spend(
    user_id: str | None, benchmark: dict, k: int
) -> float | None:
    """Projected spend for an `all_folds` evaluation, or None with no history.

    Estimated from the most recent recorded run of this benchmark. With no
    prior run there is nothing to project from, and refusing to start on a
    guess would block a first run forever — so the guard reports None and the
    caller proceeds.
    """
    for record in reversed(list_results(user_id, benchmark_id=benchmark.get("id"))):
        spend = record.get("judge_spend_usd")
        if spend is not None:
            return round(float(spend) * max(int(k), 1), 6)
    return None


def preflight_all_folds_budget(
    user_id: str | None, benchmark: dict, budget_usd: float | None
) -> None:
    """Refuse to start an `all_folds` run whose projected spend blows the budget.

    Checked BEFORE any input executes — aborting halfway leaves a partially
    graded benchmark whose score means nothing.
    """
    from core.improve import splits as splits_mod

    if budget_usd is None or splits_mod.rotation_mode(benchmark) != "all_folds":
        return
    k = len(splits_mod.fold_ids(benchmark)) or 1
    projected = project_all_folds_spend(user_id, benchmark, k)
    if projected is not None and projected > float(budget_usd):
        raise BudgetExceeded(
            f"k-fold rotation 'all_folds' with k={k} projects ${projected:.4f} "
            f"of judge spend, over the ${float(budget_usd):.4f} run budget"
        )


def grade_outcomes(
    user_id: str | None,
    benchmark: dict,
    traces_by_input: dict[str, tuple[str, dict]],
    *,
    run_id: str | None = None,
    judge_model: str | None = None,
    sql_executor=None,
    judge_session=None,
    iteration: int = 0,
) -> dict:
    """Run the outcome axis over one benchmark execution.

    `traces_by_input` maps input id -> (trace_file, trace dict). An input with
    no trace at all is N/A with `extraction_failed` — the agent produced nothing
    to grade, which is categorically different from producing a wrong answer.
    """
    from core.improve import feedback as feedback_mod, grading
    from core.improve import rubrics as rubrics_mod
    from core.improve import splits as splits_mod

    active = splits_mod.active_fold(benchmark, iteration)
    suite_rubric_id = benchmark.get("rubric_id")
    rubric_cache: dict[tuple[str, int | None], dict] = {}

    def _rubric(rubric_id: str | None, version: int | None) -> dict | None:
        if not rubric_id:
            return None
        key = (rubric_id, version)
        if key not in rubric_cache:
            rubric_cache[key] = rubrics_mod.get_rubric(user_id, rubric_id, version)
        return rubric_cache[key]

    primary_rubric = None
    if suite_rubric_id:
        primary_rubric = _rubric(suite_rubric_id, benchmark.get("rubric_version"))

    judge = judge_session
    judge_model_used = None
    if judge is None and _needs_judge(benchmark):
        judge, judge_model_used, _settings = _build_judge(
            benchmark, user_id, run_id, primary_rubric, judge_model
        )
    elif judge is not None:
        judge_model_used = getattr(judge, "model", None)

    suite_env = benchmark.get("execution_env")
    outcomes: list[dict] = []
    weights: dict[str, float] = {}

    for item in benchmark.get("inputs") or []:
        input_id = str(item.get("id") or "")
        # Unapproved augmented variants are excluded from scoring (§6.5.3).
        if item.get("is_augmented") and not item.get("approved", True):
            continue
        mode = input_grading_mode(benchmark, item)
        if not mode:
            continue
        weights[input_id] = float(item.get("weight", 1.0))

        trace_file, trace = traces_by_input.get(input_id, (None, None))
        if trace is None:
            outcomes.append({
                "input_id": input_id, "score": None,
                "na_reason": "extraction_failed", "vetoed": False,
                "checks": [], "split": item.get("split", "train"),
                "effective_split": splits_mod.effective_split(item, active),
                "fold": item.get("fold"),
                "detail": "no trace was produced for this input",
            })
            continue

        ctx = grading.GradeContext(
            user_id=user_id,
            trace=trace,
            trace_file=trace_file,
            expected=resolve_expected(benchmark, item),
            execution_env=item.get("execution_env") or suite_env,
            judge=judge,
            sql_executor=sql_executor,
            input_id=input_id,
            run_id=run_id,
        )
        rubric = (
            _rubric(item.get("rubric_id") or suite_rubric_id,
                    benchmark.get("rubric_version"))
            if mode == "rubric" else None
        )
        outcome = grading.grade_input(ctx, mode, rubric)
        outcome["split"] = item.get("split", "train")
        outcome["effective_split"] = splits_mod.effective_split(item, active)
        outcome["fold"] = item.get("fold")
        # Extractor/comparator NAMES for the §6.7 tuner feedback serializer.
        feedback_mod.annotate_checks(outcome, ctx.expected, rubric)
        outcomes.append(outcome)

    aggregate = grading.aggregate_outcomes(outcomes, weights)
    by_split = splits_mod.scores_by_split(outcomes, weights)

    fold_report: dict = {}
    if splits_mod.rotation_mode(benchmark) == "all_folds":
        fold_report = splits_mod.scores_across_folds(
            outcomes, weights, splits_mod.fold_ids(benchmark)
        )
        # The reported outcome score is the mean ACROSS folds (§6.5.2).
        if fold_report.get("fold_mean") is not None:
            aggregate = {**aggregate, "outcome_score": fold_report["fold_mean"]}

    return {
        **aggregate,
        "scores_by_split": by_split,
        "scores_by_fold": fold_report.get("scores_by_fold"),
        "fold_stddev": fold_report.get("fold_stddev"),
        "fold_index": active,
        "per_input": outcomes,
        "judge_model": judge_model_used,
        "judge_cache_hits": getattr(judge, "cache_hits", 0) if judge else 0,
        "judge_spend_usd": judge.spend_usd() if judge else 0.0,
        "rubric_id": (primary_rubric or {}).get("id"),
        "rubric_version": (primary_rubric or {}).get("version"),
        "rubric_content_hash": (primary_rubric or {}).get("content_hash"),
    }


# ── Target resolution ─────────────────────────────────────────────────────────

def _resolve_target(target_object_id: str) -> tuple[str, dict]:
    """(target_kind, config) from the live stores. Agents first."""
    from core.routes.agents import load_user_agents
    for a in load_user_agents():
        if a.get("id") == target_object_id:
            return "agent", a
    from core.routes.orchestrations import load_orchestrations
    for o in load_orchestrations():
        if o.get("id") == target_object_id:
            return "orchestration", o
    raise BenchmarkTargetNotFound(target_object_id)


def _default_server_module():
    import core.server as server_module
    return server_module


# ── Execution (checklists 4.3 / 4.4 / 4.5) ────────────────────────────────────

async def _execute_agent_input(
    agent_config: dict, prompt: str, session_id: str, images, server_module
) -> None:
    """One benchmark input through run_agent_step — the CP1-hooked path."""
    from core.react_engine import run_agent_step
    async for _event in run_agent_step(
        message=prompt,
        agent_id=agent_config.get("id"),
        session_id=session_id,
        server_module=server_module,
        source="benchmark",
        images=images,
    ):
        pass


async def _execute_orchestration_input(
    orch_config: dict, prompt: str, run_id: str, server_module
) -> None:
    """One benchmark input through OrchestrationEngine.run — CP1-hooked."""
    from core.models_orchestration import Orchestration
    from core.orchestration.engine import OrchestrationEngine
    orch = Orchestration.model_validate(orch_config)
    engine = OrchestrationEngine(orch, server_module)
    async for _event in engine.run(prompt, run_id=run_id):
        pass


def _collect_traces(
    user_id: str | None, target_kind: str, target_object_id: str, ids: set[str]
) -> list[tuple[str, dict]]:
    """This run's traces, straight from the CP1 storage layout."""
    from core.improve.runner import join_compaction_events, load_traces
    traces = load_traces(
        user_id,
        agent_id=target_object_id if target_kind == "agent" else None,
        orchestration_id=target_object_id if target_kind == "orchestration" else None,
    )
    key = "session_id" if target_kind == "agent" else "run_id"
    picked = [(rel, t) for rel, t in traces if str(t.get(key)) in ids]
    join_compaction_events(picked)
    return picked


def _trace_exec_id(trace: dict, target_kind: str) -> str:
    return str(trace.get("session_id" if target_kind == "agent" else "run_id") or "")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


async def run_benchmark(
    user_id: str | None,
    benchmark_id: str,
    *,
    target_object_id: str | None = None,
    server_module=None,
    improvement_run_id: str | None = None,
    record_as: str | None = None,  # "baseline" | "new" — stamps the ImprovementRun
    judge_model: str | None = None,
    sql_executor=None,
    judge_session=None,
    iteration: int = 0,
    budget_usd: float | None = None,
) -> dict:
    """Run every input, score the resulting traces, index the result.

    Raises BenchmarkNotFound / BenchmarkTargetNotFound.

    CP6: after trace collection, a `schema_version: 2` benchmark also runs the
    outcome-grading stage and composes the two-axis composite. Execution itself
    is unchanged — inputs still run through `run_agent_step` /
    `OrchestrationEngine.run`, and traces still flow through the CP1 hooks with
    `source="benchmark"`.
    """
    raw = load_benchmark(user_id, benchmark_id)
    benchmark = Benchmark.model_validate(raw)
    schema_version = int(raw.get("schema_version") or 1)
    target_id = target_object_id or benchmark.target_object_id
    target_kind, config = _resolve_target(target_id)
    server_module = server_module or _default_server_module()

    # BEFORE any input executes: an `all_folds` run that blows the budget
    # halfway leaves a partially graded benchmark whose score means nothing.
    preflight_all_folds_budget(user_id, raw, budget_usd)

    result_run_id = f"bench_{uuid.uuid4().hex[:12]}"
    started = _now_iso()
    ids: set[str] = set()
    exec_id_to_input: dict[str, str] = {}
    input_errors: list[str] = []
    # HARD INVARIANT (SQL memory spec §7): schema memory is FROZEN for every
    # run with source="benchmark". If memory accumulated across benchmark
    # inputs, input 5 in run 1 would see different memory than input 5 in
    # run 2 — score drift indistinguishable from an agent regression, which
    # breaks the 6.27 exact-reproducibility guarantee. Not a config flag.
    from tools.sql_memory import freeze_writes as _sql_memory_freeze
    with _sql_memory_freeze(result_run_id):
        for i, item in enumerate(benchmark.inputs):
            exec_id = f"{result_run_id}_{i}"
            ids.add(exec_id)
            exec_id_to_input[exec_id] = item.id or f"in_{i + 1:03d}"
            try:
                if target_kind == "agent":
                    await _execute_agent_input(
                        config, item.prompt, exec_id, item.images, server_module
                    )
                else:
                    await _execute_orchestration_input(
                        config, item.prompt, exec_id, server_module
                    )
            except Exception as e:  # input failure is data, not a crash
                input_errors.append(f"input[{i}]: {type(e).__name__}: {e}")

    collected = _collect_traces(user_id, target_kind, target_id, ids)
    scored = score_traces([t for _, t in collected], benchmark.scorer.metrics)

    result = {
        "run_id": result_run_id,
        "type": "benchmark",
        "benchmark_id": benchmark.id,
        "target_object_id": target_id,
        "target_kind": target_kind,
        "target_version_n": int(config.get("version_n") or 1),
        "score": scored["score"],
        "per_metric": scored["per_metric"],
        "trace_count": len(collected),
        "trace_files": [rel for rel, _ in collected],
        "input_errors": input_errors,
        "improvement_run_id": improvement_run_id,
        "created_at": started,
        "closed_at": _now_iso(),
    }

    # ── CP6 outcome axis. A v1 benchmark never enters this block, so its
    # record keeps exactly the CP4 field set and exactly the CP4 score. ──
    if schema_version >= 2 and (
        raw.get("grading_mode")
        or any(i.get("grading_mode") for i in raw.get("inputs") or [])
    ):
        traces_by_input: dict[str, tuple[str, dict]] = {}
        for rel, trace in collected:
            input_id = exec_id_to_input.get(_trace_exec_id(trace, target_kind))
            if input_id:
                traces_by_input[input_id] = (rel, trace)

        graded = grade_outcomes(
            user_id, raw, traces_by_input,
            run_id=improvement_run_id or result_run_id,
            judge_model=judge_model,
            sql_executor=sql_executor,
            judge_session=judge_session,
            iteration=iteration,
        )
        env = raw.get("execution_env") or {}
        scorer = raw.get("scorer") or {}
        composite = grading_composite(
            scored["score"], graded["outcome_score"],
            float(scorer.get("process_weight", 1.0)),
            float(scorer.get("outcome_weight", 0.0)),
        )
        # SQL memory generation (spec §7 corollary): memory content is part of
        # the agent's effective configuration. A baseline recorded against
        # memory state A is not comparable with a new score against state B.
        from tools.sql_memory import generation as _sql_memory_generation
        try:
            memory_generation = _sql_memory_generation(env.get("connection_id"))
        except Exception:
            memory_generation = None
        result.update({
            "schema_version": schema_version,
            "process_score": scored["score"],
            "outcome_score": graded["outcome_score"],
            "composite_score": composite,
            "score": composite,   # the ratchet compares composites for v2
            "sql_memory_generation": memory_generation,
            "grading_mode": raw.get("grading_mode"),
            "grading_strictness": raw.get("grading_strictness")
                                  or derive_strictness(raw),
            "rubric_id": graded["rubric_id"],
            "rubric_version": graded["rubric_version"],
            "rubric_content_hash": graded["rubric_content_hash"],
            "execution_connection_id": env.get("connection_id"),
            "snapshot_id": env.get("snapshot_id") or (
                "unpinned" if env.get("connection_id") else None
            ),
            "judge_model": graded["judge_model"],
            "judge_cache_hits": graded["judge_cache_hits"],
            "judge_spend_usd": graded["judge_spend_usd"],
            "extraction_failed_count": graded["extraction_failed_count"],
            "extraction_failed_rate": graded["extraction_failed_rate"],
            "outcome_na": graded["outcome_na"],
            "split_seed": (raw.get("split_policy") or {}).get("seed"),
            "augmentation_seed": (raw.get("augmentation") or {}).get("seed"),
            "scores_by_split": graded["scores_by_split"],
            "scores_by_fold": graded["scores_by_fold"],
            "fold_stddev": graded["fold_stddev"],
            "fold_index": graded["fold_index"],
            "iteration": iteration,
            "per_input": graded["per_input"],
        })
        # A mis-specified extractor that silently scores 0 will drive the
        # ratchet to revert good edits indefinitely, so an unreliable score is
        # marked incomparable rather than trusted (§6.6).
        if graded["extraction_failed_rate"] > EXTRACTION_FAILURE_LIMIT:
            result["incomparable_reason"] = (
                f"extraction_failed_rate {graded['extraction_failed_rate']:.2f} "
                f"> {EXTRACTION_FAILURE_LIMIT} — the benchmark's extractors are "
                "very likely misconfigured, not the agent"
            )

    runs = runs_mod.load_runs(user_id)
    runs.append(result)
    runs_mod.save_runs(runs, user_id)

    if improvement_run_id and record_as in ("baseline", "new"):
        try:
            runs_mod.update_run(
                user_id,
                improvement_run_id,
                benchmark_id=benchmark.id,
                **{f"{record_as}_score": result["score"]},
            )
        except runs_mod.RunNotFound:
            pass
    return result


def grading_composite(
    process_score: float | None,
    outcome_score: float | None,
    process_weight: float = 1.0,
    outcome_weight: float = 0.0,
) -> float | None:
    """Two-axis composite (§6.1). Thin re-export so callers need only this
    module; the arithmetic lives in `grading.composite_score`."""
    from core.improve.grading import composite_score
    return composite_score(
        process_score, outcome_score, process_weight, outcome_weight
    )


def outcome_variance_threshold(result: dict) -> float:
    """The §6.8 reproducibility threshold that applies to a result record.

    Deterministic + strict + pinned snapshot is held to EXACT equality; an
    unpinned snapshot is downgraded to +/-0.02 and flagged, because the DB may
    have moved underneath the run and claiming exactness the setup cannot
    deliver is worse than reporting the flag.
    """
    if result.get("grading_mode") == "rubric":
        return OUTCOME_VARIANCE_THRESHOLD_RUBRIC
    if result.get("grading_strictness") == "mixed":
        return OUTCOME_VARIANCE_THRESHOLD_RUBRIC
    if result.get("snapshot_id") in (None, "unpinned"):
        if result.get("execution_connection_id"):
            return OUTCOME_VARIANCE_THRESHOLD_UNPINNED
    return OUTCOME_VARIANCE_THRESHOLD_STRICT_EXACT


def list_results(
    user_id: str | None,
    benchmark_id: str | None = None,
    target_object_id: str | None = None,
) -> list[dict]:
    """Benchmark result records from runs.json, newest last (4.8)."""
    return [
        r for r in runs_mod.load_runs(user_id)
        if r.get("type") == "benchmark"
        and (benchmark_id is None or r.get("benchmark_id") == benchmark_id)
        and (target_object_id is None or r.get("target_object_id") == target_object_id)
    ]
