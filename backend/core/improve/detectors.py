"""
Trace detectors for the Synapse Self-Improvement subsystem (Checkpoint 2).

Every detector is a PURE function: a trace dict in (see SCHEMA.md §1),
a DetectorResult dict out. No I/O, no globals, no clocks, no randomness —
identical input always produces identical output (determinism requirement).

DetectorResult contract:
    {
      "detector":   str,          # detector name
      "applicable": bool,         # counts toward the denominator
      "hit":        bool,         # counts toward the numerator (when applicable)
      "value":      number|None,  # detector-specific scalar (count, rate, seconds, tokens)
      "evidence":   [int, ...],   # message indices into trace["messages"]
      "detail":     str,          # one-line human-readable finding
    }

`duration_outlier` additionally accepts an optional `corpus_stats` dict
({"mean": float, "std": float, "n": int}) computed by the runner — still a
pure function of its inputs.

Ported RI detectors: loops, give_up, errors, recovery, clean_success,
duration_outlier, token_usage.
Synapse-native detectors (grounded in observable react_engine.py behavior):
sequentialthinking_cap_hit, hallucinated_tool_rate, compaction_thrash,
sticky_arg_conflict, delegate_pingpong, mcp_ping_timeout_rate,
browser_state_stale_rate.
"""
from core.improve.trace_writer import GIVE_UP_RE

# High-usage flag threshold for token_usage (total tokens in one run).
TOKEN_USAGE_FLAG_THRESHOLD = 50_000

# Exact observable strings emitted by react_engine.py tool_result previews.
_SEQ_CAP_MARKER = "Blocked: sequentialthinking already used"
_HALLUCINATED_MARKERS = (
    "Blocked: Tool not available for this agent",
    "Error: tool not found",
    "Tool removed",
)
_MCP_UNRESPONSIVE_MARKER = "session unresponsive"
_BROWSER_TOOL_PREFIX = "browser_"
# Symptoms of acting on a stale DOM snapshot (element gone after navigation).
_BROWSER_STALE_MARKERS = (
    "not found", "no such element", "stale", "not visible", "detached",
    "no longer", "timeout", "timed out",
)


def _result(name, applicable, hit=False, value=None, evidence=None, detail=""):
    return {
        "detector": name,
        "applicable": bool(applicable),
        "hit": bool(applicable and hit),
        "value": value,
        "evidence": sorted(evidence or []),
        "detail": detail,
    }


def _messages(trace):
    msgs = trace.get("messages")
    return msgs if isinstance(msgs, list) else []


def _tool_call_entries(trace):
    """[(message_idx, name, arguments_str)] for every tool call in the trace."""
    out = []
    for i, m in enumerate(_messages(trace)):
        if m.get("role") != "assistant":
            continue
        for call in m.get("tool_calls") or []:
            fn = call.get("function") or {}
            out.append((i, str(fn.get("name", "")), str(fn.get("arguments", ""))))
    return out


def _tool_result_entries(trace):
    """[(message_idx, content)] for every tool-role message."""
    return [
        (i, str(m.get("content", "")))
        for i, m in enumerate(_messages(trace))
        if m.get("role") == "tool"
    ]


def _last_tool_name_before(trace, idx):
    """Tool name of the nearest preceding assistant tool_call (links results to calls)."""
    msgs = _messages(trace)
    for i in range(idx - 1, -1, -1):
        for call in msgs[i].get("tool_calls") or []:
            return str((call.get("function") or {}).get("name", ""))
    return ""


def _error_signal_indices(trace):
    """Message indices carrying an error signal (tool errors / system step errors)."""
    hits = []
    for i, m in enumerate(_messages(trace)):
        content = str(m.get("content", ""))
        if m.get("role") == "tool" and content.startswith("Error"):
            hits.append(i)
        elif m.get("role") == "system" and content.startswith("step_error"):
            hits.append(i)
    return hits


# ──────────────────────────────────────────────────────────────────────────────
# RI-ported detectors
# ──────────────────────────────────────────────────────────────────────────────

def loops(trace):
    """Identical (tool, arguments) called ≥3 times in one run."""
    calls = _tool_call_entries(trace)
    if not calls:
        return _result("loops", applicable=False)
    counts: dict[tuple, list[int]] = {}
    for idx, name, args in calls:
        counts.setdefault((name, args), []).append(idx)
    worst_key, worst = None, []
    for key, indices in counts.items():
        if len(indices) > len(worst):
            worst_key, worst = key, indices
    hit = len(worst) >= 3
    return _result(
        "loops", applicable=True, hit=hit, value=len(worst),
        evidence=worst if hit else [],
        detail=(f"tool '{worst_key[0]}' called {len(worst)}x with identical arguments"
                if hit else ""),
    )


def give_up(trace):
    """Last assistant message with content matches the give-up regex."""
    assistants = [
        (i, str(m.get("content", "")))
        for i, m in enumerate(_messages(trace))
        if m.get("role") == "assistant" and m.get("content")
    ]
    if not assistants:
        return _result("give_up", applicable=False)
    idx, content = assistants[-1]
    hit = bool(GIVE_UP_RE.search(content))
    return _result(
        "give_up", applicable=True, hit=hit, evidence=[idx] if hit else [],
        detail=f"final assistant message gives up: {content[:120]!r}" if hit else "",
    )


def errors(trace):
    """Run-level error, or error-signal messages in the transcript."""
    evidence = _error_signal_indices(trace)
    hit = bool(trace.get("error")) or bool(evidence)
    return _result(
        "errors", applicable=True, hit=hit, value=len(evidence), evidence=evidence,
        detail=(str(trace.get("error") or f"{len(evidence)} error message(s) in transcript")
                if hit else ""),
    )


def recovery(trace):
    """Errors occurred mid-run but the run still succeeded."""
    evidence = _error_signal_indices(trace)
    if not evidence:
        return _result("recovery", applicable=False)
    hit = bool(trace.get("success"))
    return _result(
        "recovery", applicable=True, hit=hit, value=len(evidence),
        evidence=evidence if hit else [],
        detail=f"recovered from {len(evidence)} error(s) and finished successfully" if hit else "",
    )


def clean_success(trace):
    """Success with zero error signals and no give-up phrasing."""
    error_free = not trace.get("error") and not _error_signal_indices(trace)
    gave_up = give_up(trace)["hit"]
    hit = bool(trace.get("success")) and error_free and not gave_up
    return _result(
        "clean_success", applicable=True, hit=hit,
        detail="clean success" if hit else "",
    )


def duration_outlier(trace, corpus_stats=None):
    """Duration > mean + 2σ of the corpus (stats supplied by the runner)."""
    duration = trace.get("duration_s")
    stats = corpus_stats or {}
    applicable = (
        isinstance(duration, (int, float))
        and stats.get("n", 0) >= 3
        and (stats.get("std") or 0) > 0
    )
    if not applicable:
        return _result("duration_outlier", applicable=False,
                       value=duration if isinstance(duration, (int, float)) else None)
    threshold = stats["mean"] + 2 * stats["std"]
    hit = duration > threshold
    return _result(
        "duration_outlier", applicable=True, hit=hit, value=duration,
        detail=(f"duration {duration}s exceeds corpus mean+2σ ({round(threshold, 2)}s)"
                if hit else ""),
    )


def token_usage(trace):
    """Total token consumption; flags runs above TOKEN_USAGE_FLAG_THRESHOLD."""
    usage = trace.get("usage") or {}
    total = usage.get("total_tokens")
    if not isinstance(total, (int, float)) or not usage.get("llm_calls"):
        return _result("token_usage", applicable=False)
    hit = total > TOKEN_USAGE_FLAG_THRESHOLD
    return _result(
        "token_usage", applicable=True, hit=hit, value=total,
        detail=(f"{total} tokens exceeds the {TOKEN_USAGE_FLAG_THRESHOLD} high-usage flag"
                if hit else ""),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Synapse-native detectors
# ──────────────────────────────────────────────────────────────────────────────

def sequentialthinking_cap_hit(trace):
    """react_engine blocked sequentialthinking after its 5-call cap."""
    seq_calls = [i for i, name, _ in _tool_call_entries(trace) if name == "sequentialthinking"]
    if not seq_calls:
        return _result("sequentialthinking_cap_hit", applicable=False)
    evidence = [i for i, content in _tool_result_entries(trace) if _SEQ_CAP_MARKER in content]
    hit = bool(evidence)
    return _result(
        "sequentialthinking_cap_hit", applicable=True, hit=hit,
        value=len(seq_calls), evidence=evidence,
        detail="sequentialthinking 5-call cap was hit" if hit else "",
    )


def hallucinated_tool_rate(trace):
    """Calls to tools that don't exist / aren't available to the agent."""
    calls = _tool_call_entries(trace)
    if not calls:
        return _result("hallucinated_tool_rate", applicable=False)
    evidence = [
        i for i, content in _tool_result_entries(trace)
        if any(marker in content for marker in _HALLUCINATED_MARKERS)
    ]
    rate = round(len(evidence) / len(calls), 4)
    return _result(
        "hallucinated_tool_rate", applicable=True, hit=bool(evidence),
        value=rate, evidence=evidence,
        detail=(f"{len(evidence)}/{len(calls)} tool calls targeted unavailable/nonexistent tools"
                if evidence else ""),
    )


def compaction_thrash(trace):
    """≥2 context compactions in one run (engine intends at most one).

    `compaction_events` is joined onto the trace dict by the runner from
    usage_tracker records; absent/empty means no compactions observed.
    """
    events = trace.get("compaction_events") or []
    count = len(events)
    hit = count >= 2
    return _result(
        "compaction_thrash", applicable=True, hit=hit, value=count,
        detail=f"context compacted {count}x in a single run" if hit else "",
    )


def sticky_arg_conflict(trace):
    """An argument value flips A→B→A across calls of the same tool —
    the symptom of sticky-arg injection fighting the model's intent."""
    import json as _json
    per_tool: dict[str, list[tuple[int, dict]]] = {}
    for idx, name, args_str in _tool_call_entries(trace):
        try:
            args = _json.loads(args_str)
        except Exception:
            continue
        if isinstance(args, dict):
            per_tool.setdefault(name, []).append((idx, args))

    applicable = any(len(v) >= 3 for v in per_tool.values())
    if not applicable:
        return _result("sticky_arg_conflict", applicable=False)

    for name, entries in sorted(per_tool.items()):
        if len(entries) < 3:
            continue
        keys = sorted({k for _, args in entries for k in args})
        for key in keys:
            seq = [(idx, _json.dumps(args.get(key), sort_keys=True, default=str))
                   for idx, args in entries if key in args]
            for a in range(len(seq) - 2):
                if seq[a][1] == seq[a + 2][1] and seq[a][1] != seq[a + 1][1]:
                    evidence = [seq[a][0], seq[a + 1][0], seq[a + 2][0]]
                    return _result(
                        "sticky_arg_conflict", applicable=True, hit=True,
                        evidence=evidence,
                        detail=f"tool '{name}' arg '{key}' flipped A→B→A across calls",
                    )
    return _result("sticky_arg_conflict", applicable=True, hit=False)


def delegate_pingpong(trace):
    """The same agent delegated to ≥3 times in one run."""
    import json as _json
    targets: dict[str, list[int]] = {}
    for idx, name, args_str in _tool_call_entries(trace):
        if name != "delegate_to_agent":
            continue
        try:
            target = str((_json.loads(args_str) or {}).get("agent_id", ""))
        except Exception:
            target = ""
        targets.setdefault(target, []).append(idx)
    if not targets:
        return _result("delegate_pingpong", applicable=False)
    worst_target = max(sorted(targets), key=lambda t: len(targets[t]))
    worst = targets[worst_target]
    hit = len(worst) >= 3
    return _result(
        "delegate_pingpong", applicable=True, hit=hit, value=len(worst),
        evidence=worst if hit else [],
        detail=f"delegated to agent '{worst_target}' {len(worst)}x in one run" if hit else "",
    )


def mcp_ping_timeout_rate(trace):
    """MCP sessions found unresponsive at the pre-call ping health check."""
    calls = _tool_call_entries(trace)
    if not calls:
        return _result("mcp_ping_timeout_rate", applicable=False)
    evidence = [i for i, content in _tool_result_entries(trace)
                if _MCP_UNRESPONSIVE_MARKER in content]
    rate = round(len(evidence) / len(calls), 4)
    return _result(
        "mcp_ping_timeout_rate", applicable=True, hit=bool(evidence),
        value=rate, evidence=evidence,
        detail=f"{len(evidence)} MCP call(s) hit an unresponsive session" if evidence else "",
    )


def browser_state_stale_rate(trace):
    """Browser tool results showing stale-DOM symptoms (element gone/moved)."""
    browser_calls = [i for i, name, _ in _tool_call_entries(trace)
                     if name.startswith(_BROWSER_TOOL_PREFIX)]
    if not browser_calls:
        return _result("browser_state_stale_rate", applicable=False)
    evidence = []
    for i, content in _tool_result_entries(trace):
        if not _last_tool_name_before(trace, i).startswith(_BROWSER_TOOL_PREFIX):
            continue
        lowered = content.lower()
        if any(marker in lowered for marker in _BROWSER_STALE_MARKERS):
            evidence.append(i)
    rate = round(len(evidence) / len(browser_calls), 4)
    return _result(
        "browser_state_stale_rate", applicable=True, hit=bool(evidence),
        value=rate, evidence=evidence,
        detail=(f"{len(evidence)}/{len(browser_calls)} browser actions hit stale-state symptoms"
                if evidence else ""),
    )


# Registry — stable iteration order (dicts preserve insertion order).
DETECTORS = {
    "loops": loops,
    "give_up": give_up,
    "errors": errors,
    "recovery": recovery,
    "clean_success": clean_success,
    "duration_outlier": duration_outlier,
    "token_usage": token_usage,
    "sequentialthinking_cap_hit": sequentialthinking_cap_hit,
    "hallucinated_tool_rate": hallucinated_tool_rate,
    "compaction_thrash": compaction_thrash,
    "sticky_arg_conflict": sticky_arg_conflict,
    "delegate_pingpong": delegate_pingpong,
    "mcp_ping_timeout_rate": mcp_ping_timeout_rate,
    "browser_state_stale_rate": browser_state_stale_rate,
}
