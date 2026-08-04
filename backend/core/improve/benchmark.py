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
"""
import json
import os
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from core.improve import runs as runs_mod
from core.improve.trace_writer import ensure_user_layout

# Two benchmark runs on the same version must agree within this (4.12).
SCORE_VARIANCE_THRESHOLD = 0.02


class BenchmarkNotFound(Exception):
    pass


class BenchmarkTargetNotFound(Exception):
    pass


# ── Schema (checklist 4.1) ────────────────────────────────────────────────────

class BenchmarkInput(BaseModel):
    prompt: str
    expected_metric_hints: dict[str, float] = {}
    images: list[str] | None = None


class BenchmarkScorer(BaseModel):
    metrics: dict[str, float] = {}  # detector name or "success" -> weight; 0/absent = excluded


class Benchmark(BaseModel):
    id: str
    name: str
    target_object_id: str
    inputs: list[BenchmarkInput] = Field(min_length=1)
    scorer: BenchmarkScorer = BenchmarkScorer()


DEFAULT_SCORER_METRICS = {"success": 1.0, "clean_success": 1.0}


# ── Storage (standalone objects — checklist 4.2) ──────────────────────────────

def _benchmarks_dir(user_id: str | None) -> str:
    return os.path.join(ensure_user_layout(user_id), "benchmarks")


def save_benchmark(user_id: str | None, benchmark: dict) -> dict:
    validated = Benchmark.model_validate(benchmark).model_dump()
    path = os.path.join(_benchmarks_dir(user_id), f"{validated['id']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(validated, f, indent=2, ensure_ascii=False)
    return validated


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
) -> dict:
    """Run every input, score the resulting traces, index the result.

    Raises BenchmarkNotFound / BenchmarkTargetNotFound.
    """
    benchmark = Benchmark.model_validate(load_benchmark(user_id, benchmark_id))
    target_id = target_object_id or benchmark.target_object_id
    target_kind, config = _resolve_target(target_id)
    server_module = server_module or _default_server_module()

    result_run_id = f"bench_{uuid.uuid4().hex[:12]}"
    started = _now_iso()
    ids: set[str] = set()
    input_errors: list[str] = []
    for i, item in enumerate(benchmark.inputs):
        exec_id = f"{result_run_id}_{i}"
        ids.add(exec_id)
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
    runs = runs_mod.load_runs(user_id)
    runs.append(result)
    runs_mod.save_runs(runs, user_id)

    if improvement_run_id and record_as in ("baseline", "new"):
        try:
            runs_mod.update_run(
                user_id,
                improvement_run_id,
                benchmark_id=benchmark.id,
                **{f"{record_as}_score": scored["score"]},
            )
        except runs_mod.RunNotFound:
            pass
    return result


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
