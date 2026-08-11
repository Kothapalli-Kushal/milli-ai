"""
Detector runner for the Synapse Self-Improvement subsystem (Checkpoint 2).

Loads a user's trace files (the only I/O in this checkpoint — READ-only),
joins compaction events from usage_tracker, runs every detector on every
trace, and aggregates per-agent, per-orchestration, and per-model.

Determinism: traces are processed in sorted-relative-path order, floats are
rounded, and no timestamps or random values enter the report — identical
input always produces byte-identical output.
"""
import json
import os

from core.improve.detectors import DETECTORS
from core.improve.trace_writer import resolve_user_id, user_improve_dir


def load_traces(
    user_id: str | None = None,
    agent_id: str | None = None,
    orchestration_id: str | None = None,
) -> list[tuple[str, dict]]:
    """[(relative_trace_path, trace_dict)] sorted by path. Read-only.

    `agent_id` / `orchestration_id` filter by the trace's own identity fields
    (an agent trace produced inside an orchestration matches both filters).
    """
    traces_root = os.path.join(user_improve_dir(user_id), "traces")
    out: list[tuple[str, dict]] = []
    if not os.path.isdir(traces_root):
        return out
    for object_dir in sorted(os.listdir(traces_root)):
        object_path = os.path.join(traces_root, object_dir)
        if not os.path.isdir(object_path):
            continue
        for month in sorted(os.listdir(object_path)):
            month_dir = os.path.join(object_path, month)
            if not os.path.isdir(month_dir):
                continue
            for name in sorted(os.listdir(month_dir)):
                if not name.endswith(".json"):
                    continue
                path = os.path.join(month_dir, name)
                try:
                    with open(path, encoding="utf-8") as f:
                        trace = json.load(f)
                except Exception:
                    continue  # unreadable/corrupt file — skip, never write
                if agent_id and trace.get("agent_id") != agent_id:
                    continue
                if orchestration_id and trace.get("orchestration_id") != orchestration_id:
                    continue
                rel = "/".join((object_dir, month, name))
                out.append((rel, trace))
    return out


def join_compaction_events(traces: list[tuple[str, dict]]) -> None:
    """Attach usage_tracker compaction records to each trace dict (in memory
    only — trace files are never rewritten). Keys on run_id, falling back to
    session_id, so the pure `compaction_thrash` detector needs no I/O."""
    try:
        from core.usage_tracker import get_usage_logs
        records = [r for r in get_usage_logs(limit=1_000_000, source="compaction")]
    except Exception:
        records = []
    by_run: dict[str, list[dict]] = {}
    by_session: dict[str, list[dict]] = {}
    for r in records:
        if r.get("run_id"):
            by_run.setdefault(str(r["run_id"]), []).append(r)
        elif r.get("session_id"):
            by_session.setdefault(str(r["session_id"]), []).append(r)
    for _, trace in traces:
        events = []
        if trace.get("run_id"):
            events = by_run.get(str(trace["run_id"]), [])
        elif trace.get("session_id"):
            events = by_session.get(str(trace["session_id"]), [])
        trace["compaction_events"] = events


def corpus_duration_stats(traces: list[tuple[str, dict]]) -> dict:
    """{mean, std, n} over duration_s — feeds the duration_outlier detector."""
    durations = [
        float(t.get("duration_s"))
        for _, t in traces
        if isinstance(t.get("duration_s"), (int, float))
    ]
    n = len(durations)
    if n == 0:
        return {"mean": 0.0, "std": 0.0, "n": 0}
    mean = sum(durations) / n
    variance = sum((d - mean) ** 2 for d in durations) / n
    return {"mean": round(mean, 6), "std": round(variance ** 0.5, 6), "n": n}


def run_detectors_on_trace(trace: dict, corpus_stats: dict) -> dict:
    """{detector_name: DetectorResult} for one trace. Pure given its inputs."""
    results = {}
    for name, fn in DETECTORS.items():
        if name == "duration_outlier":
            results[name] = fn(trace, corpus_stats)
        else:
            results[name] = fn(trace)
    return results


def _empty_agg() -> dict:
    return {name: {"numerator": 0, "denominator": 0} for name in DETECTORS}


def _fold(agg: dict, results: dict) -> None:
    for name, res in results.items():
        slot = agg[name]
        if res["applicable"]:
            slot["denominator"] += 1
            if res["hit"]:
                slot["numerator"] += 1


def _finalize_agg(agg: dict) -> dict:
    """Attach rate ('N/A' when the denominator is 0 — never a silent zero)."""
    out = {}
    for name in DETECTORS:  # registry order, deterministic
        slot = agg[name]
        den = slot["denominator"]
        out[name] = {
            "numerator": slot["numerator"],
            "denominator": den,
            "rate": round(slot["numerator"] / den, 4) if den > 0 else "N/A",
        }
    return out


def build_report(
    user_id: str | None = None,
    agent_id: str | None = None,
    orchestration_id: str | None = None,
) -> dict:
    """Full detector report over a user's traces. Read-only, deterministic."""
    user_id = user_id or resolve_user_id()
    traces = load_traces(user_id, agent_id=agent_id, orchestration_id=orchestration_id)
    join_compaction_events(traces)
    stats = corpus_duration_stats(traces)

    overall = _empty_agg()
    per_agent: dict[str, dict] = {}
    per_orch: dict[str, dict] = {}
    per_model: dict[str, dict] = {}
    trace_entries = []

    for rel_path, trace in traces:
        results = run_detectors_on_trace(trace, stats)
        _fold(overall, results)
        for key_value, bucket in (
            (trace.get("agent_id"), per_agent),
            (trace.get("orchestration_id"), per_orch),
            (trace.get("model"), per_model),
        ):
            if key_value:
                _fold(bucket.setdefault(str(key_value), _empty_agg()), results)
        trace_entries.append({
            "trace_file": rel_path,
            "session_id": trace.get("session_id"),
            "agent_id": trace.get("agent_id"),
            "orchestration_id": trace.get("orchestration_id"),
            "model": trace.get("model"),
            "success": bool(trace.get("success")),
            "results": results,
        })

    return {
        "filters": {"agent_id": agent_id, "orchestration_id": orchestration_id},
        "trace_count": len(traces),
        "corpus_duration_stats": stats,
        "detectors": _finalize_agg(overall),
        "aggregates": {
            "per_agent": {k: _finalize_agg(v) for k, v in sorted(per_agent.items())},
            "per_orchestration": {k: _finalize_agg(v) for k, v in sorted(per_orch.items())},
            "per_model": {k: _finalize_agg(v) for k, v in sorted(per_model.items())},
        },
        "traces": trace_entries,
    }
