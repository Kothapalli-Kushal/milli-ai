"""
Insight extraction for the Synapse Self-Improvement subsystem (Checkpoint 2).

Converts a runner report into ATOMIC learnings — one finding per (trace,
detector hit) plus aggregate-rate learnings — each carrying evidence pointers
`(trace_file, message_idx)` so the UI (and the Checkpoint-3 tuner) can link
every claim back to the exact transcript locations that motivated it.

Pure transformation: report dict in, insights dict out. No I/O, no clocks —
byte-stable across runs on identical input.
"""
import hashlib
import json

# Detectors whose hits indicate a problem (drive severity), vs. informational.
_PROBLEM_SEVERITY = {
    "loops": "high",
    "give_up": "high",
    "errors": "medium",
    "duration_outlier": "low",
    "token_usage": "low",
    "sequentialthinking_cap_hit": "medium",
    "hallucinated_tool_rate": "high",
    "compaction_thrash": "medium",
    "sticky_arg_conflict": "medium",
    "delegate_pingpong": "medium",
    "mcp_ping_timeout_rate": "medium",
    "browser_state_stale_rate": "medium",
}
_POSITIVE_DETECTORS = {"recovery", "clean_success"}

# Aggregate-rate learnings fire when a problem detector's corpus rate ≥ this.
AGGREGATE_RATE_THRESHOLD = 0.5


def _insight_id(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def _target(entry: dict) -> dict:
    target = {}
    if entry.get("agent_id"):
        target["agent_id"] = entry["agent_id"]
    if entry.get("orchestration_id"):
        target["orchestration_id"] = entry["orchestration_id"]
    return target


def extract_insights(report: dict) -> dict:
    """Atomic learnings from a runner report (see runner.build_report)."""
    insights = []

    # ── per-trace findings ───────────────────────────────────────────────────
    for entry in report.get("traces", []):
        trace_file = entry["trace_file"]
        for name, res in entry.get("results", {}).items():
            if not res.get("hit") or name in _POSITIVE_DETECTORS:
                continue
            detail = res.get("detail") or f"{name} fired"
            learning = {
                "kind": "trace_finding",
                "detector": name,
                "severity": _PROBLEM_SEVERITY.get(name, "medium"),
                "learning": detail,
                "target": _target(entry),
                "value": res.get("value"),
                "evidence": [
                    {"trace_file": trace_file, "message_idx": idx}
                    for idx in res.get("evidence") or []
                ] or [{"trace_file": trace_file, "message_idx": None}],
            }
            learning["id"] = _insight_id(learning)
            insights.append(learning)

    # ── aggregate-rate findings ──────────────────────────────────────────────
    for scope_name, scoped in (
        ("agent", report.get("aggregates", {}).get("per_agent", {})),
        ("orchestration", report.get("aggregates", {}).get("per_orchestration", {})),
        ("model", report.get("aggregates", {}).get("per_model", {})),
    ):
        for object_id, detectors in scoped.items():
            for name, slot in detectors.items():
                rate = slot.get("rate")
                if name in _POSITIVE_DETECTORS or rate == "N/A":
                    continue
                if isinstance(rate, (int, float)) and rate >= AGGREGATE_RATE_THRESHOLD:
                    supporting = [
                        {"trace_file": e["trace_file"], "message_idx": idx}
                        for e in report.get("traces", [])
                        if e.get("results", {}).get(name, {}).get("hit")
                        and (
                            (scope_name == "agent" and e.get("agent_id") == object_id)
                            or (scope_name == "orchestration" and e.get("orchestration_id") == object_id)
                            or (scope_name == "model" and e.get("model") == object_id)
                        )
                        for idx in (e["results"][name].get("evidence") or [None])
                    ]
                    learning = {
                        "kind": "aggregate_finding",
                        "detector": name,
                        "severity": _PROBLEM_SEVERITY.get(name, "medium"),
                        "learning": (
                            f"{scope_name} '{object_id}': {name} fired in "
                            f"{slot['numerator']}/{slot['denominator']} applicable traces "
                            f"(rate {rate})"
                        ),
                        "target": {f"{scope_name}_id" if scope_name != "model" else "model": object_id},
                        "value": rate,
                        "evidence": supporting,
                    }
                    learning["id"] = _insight_id(learning)
                    insights.append(learning)

    insights.sort(key=lambda x: (x["kind"], x["detector"], x["id"]))
    return {
        "filters": report.get("filters", {}),
        "trace_count": report.get("trace_count", 0),
        "insight_count": len(insights),
        "insights": insights,
    }
