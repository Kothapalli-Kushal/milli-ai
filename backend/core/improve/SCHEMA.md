# Synapse Self-Improvement — Trace Schema & Storage Layout (Checkpoint 1)

Status: Checkpoint 1. This document is the authoritative reference for the
trace JSON schema, the per-user storage layout, the ACL map, retention, and
the success-derivation rule.

---

## 1. Trace schema

One JSON file per agent run / orchestration run ("session"). The schema is a
Synapse-native superset of the recursive-improve (RI) trace schema — every RI
field is present with the same name and meaning; Synapse adds `kind`, the
`usage` aggregate, and extra `metadata` keys.

```jsonc
{
  // ── identity ────────────────────────────────────────────────────────────
  "session_id":       "str  — chat session id, or run_id for orchestrations",
  "timestamp":        "str  — ISO-8601 UTC, run start",
  "duration_s":       "float — wallclock seconds",
  "run_id":           "str|null — orchestration/builder run id (null for plain chat)",
  "agent_id":         "str|null — target agent (null for orchestration traces)",
  "orchestration_id": "str|null — set for orchestration traces AND for agent traces executed inside an orchestration step",
  "model":            "str|null — resolved model, joined from usage_tracker records",
  "git_branch":       "str|null — optional, not populated in Checkpoint 1",
  "git_commit":       "str|null — optional, not populated in Checkpoint 1",

  // ── outcome ─────────────────────────────────────────────────────────────
  "success":          "bool — see §4 Success derivation",
  "error":            "str|null — first error/orchestration_error event message, or exception text",
  "output":           "str — final response text",

  // ── context ─────────────────────────────────────────────────────────────
  "metadata": {
    "kind":              "agent | orchestration",
    "source":            "chat | orchestration | schedule | api | benchmark | ...",
    "delegated_from":    "str? — parent agent id when this run was a delegate_to_agent / spawn_subtask sub-run",
    "parent_session_id": "str? — parent trace's session_id for delegate sub-runs",
    "step_id":           "str? — orchestration step id when run inside a step"
  },

  // ── transcript ──────────────────────────────────────────────────────────
  "messages": [
    {
      "role":         "user | assistant | tool | system",
      "content":      "str",
      "timestamp":    "str — ISO-8601 UTC",
      "tool_calls":   [ { "id": "call_N", "function": { "name": "str", "arguments": "str (JSON)" } } ],  // assistant only, optional
      "tool_call_id": "str?  — tool role only, links back to the assistant tool_call",
      "model":        "str?",
      "usage":        "obj?",
      "reasoning":    "str?"
    }
  ],

  // ── cost (Synapse extension; joined, never re-counted) ──────────────────
  "usage": {
    "input_tokens":       0,
    "output_tokens":      0,
    "total_tokens":       0,
    "estimated_cost_usd": 0.0,
    "llm_calls":          0
  }
}
```

### Event → message mapping (agent traces)

| `run_agent_step` event | Trace effect |
|---|---|
| initial `message` arg | `{role: user, content: message}` (first message) |
| `llm_thought` | `{role: assistant, content: thought}` |
| `tool_execution` | `{role: assistant, tool_calls: [{id, function: {name, arguments}}]}` |
| `tool_result` / `tool_cache_hit` | `{role: tool, content: preview, tool_call_id}` |
| `final` | `{role: assistant, content: response}`; sets `output`, marks final fired |
| `error` | sets `error`; fails the run |
| `thinking`, `status` | ignored (UI chrome, no transcript value) |

### Event → message mapping (orchestration traces)

| Engine event | Trace effect |
|---|---|
| `step_start` | `{role: system, content: "step_start <id> <name>"}`; tracks current step id |
| `step_complete` | `{role: system, content: "step_complete <id> (<duration>s)"}` |
| `step_error` | `{role: system, ...}`; marks error event seen (fails success) |
| `orchestration_error` | sets `error`; fails the run |
| `orchestration_complete` | records final `status`; any status ≠ `completed` fails the run |
| `final` | `{role: assistant, content: response}`; sets `output`, marks final fired |
| everything else | ignored |

### File naming

`<session_id>.json`. A chat session spans multiple runs, so on collision the
writer suffixes `<session_id>__2.json`, `__3.json`, … — one file **per run**,
grouped by the session id. `session_id` inside the file stays the original.

### In-memory enrichment (Checkpoint 2)

Before detectors run, the runner joins usage_tracker compaction records onto
each trace dict as `compaction_events` (keyed by `run_id`, falling back to
`session_id`). This happens **in memory only** — trace files are never
rewritten — and keeps the `compaction_thrash` detector a pure function.

---

## 2. Per-user storage layout

All improvement storage is namespaced by user id (§0.6.5). Synapse's login
gate is single-username; `user_id` resolves to `settings.login_username`, or
`"default"` when the gate is disabled.

```
backend/data/improve/<user_id>/
  traces/<agent_or_orch_id>/<YYYY-MM>/<session_id>.json   # rotation by month
  runs.json                                               # ImprovementRun index      (Checkpoint 3+)
  proposals/<run_id>.json                                 # tuner proposal payloads: {insights, proposed_diff} (Checkpoint 3)
  benchmarks/<benchmark_id>.json                          # standalone benchmarks     (Checkpoint 4)
  versions/<object_id>/v<N>.json                          # immutable version snapshots (Checkpoint 3)
  inbox.json                                              # Self-Improvement Inbox audit log (Checkpoint 5)
```

Version snapshot files are written once and never overwritten; their `config`
payload is immutable. The only permitted rewrite is the envelope's `is_active`
bookkeeping bit, flipped by `versioning.transfer_active()` when the active
version changes. Files are chmod'd read-only (best effort) between writes.

The layout (dirs + empty `runs.json` / `inbox.json`) is created lazily by
`ensure_user_layout()` the first time a trace is written for a user.

---

## 3. ACL map

All `/api/improve/*` routes reuse the existing auth surfaces: the login-gate
JWT (`core/user_auth.py`, validated the same way as every other session
route) and/or programmatic API keys (`require_api_key`). Every route is
scoped to the **calling** user's namespace — no route may read or write
another user's `backend/data/improve/<user_id>/` tree.

| Route (planned checkpoint) | Method | Auth level | Scope |
|---|---|---|---|
| `/api/improve/insights` (CP2) | GET | session JWT or API key | caller's traces only |
| `/api/improve/propose` (CP3) | POST | session JWT or API key | caller's objects only |
| `/api/improve/apply` (CP3) | POST | session JWT or API key | caller's objects only |
| `/api/improve/rollback/{object_id}/{version_n}` (CP3) | POST | session JWT or API key | caller's versions only |
| `/api/improve/versions/{object_id}` (CP3) | GET | session JWT or API key | caller's versions only |
| `/api/improve/benchmark/{benchmark_id}` (CP4) | POST | session JWT or API key | caller's benchmarks only |
| `/api/improve/benchmark/{benchmark_id}` (CP4, authoring) | PUT / DELETE | session JWT or API key | caller's benchmarks only |
| `/api/improve/benchmarks` (CP4, authoring) | GET | session JWT or API key | caller's benchmarks only |
| `/api/improve/benchmark/results` (CP4) | GET | session JWT or API key | caller's results only |
| `/api/improve/inbox` (CP5) | GET | session JWT or API key | caller's inbox only |
| `/api/improve/traces/purge` (risk register) | POST | session JWT or API key | caller's traces only |

No improvement route is ever exposed unauthenticated. Checkpoint 1 ships no
routes — this table is the contract later checkpoints implement against.

---

## 4. Success derivation

```
success = final_event_fired
        ∧ no error / orchestration_error / step_error event
        ∧ (orchestration only) final status == "completed"
        ∧ last assistant message with content does NOT match GIVE_UP_RE
```

`GIVE_UP_RE` (case-insensitive) matches explicit give-up phrasing: "I can't /
cannot / am unable to …", "I give up", "no tool(s) available", "as an AI
(language) model I can't", etc. It is defined once in `trace_writer.py` and
reused by the Checkpoint 2 `give_up` detector.

---

## 5. Token / cost join

Tokens and cost are **joined** from existing `usage_tracker` records — never
re-counted, never double-logged:

- `run_id` present → join on `run_id` (plus `agent_id` for agent traces inside
  an orchestration, so per-step traces don't absorb the whole run's spend).
- `run_id` absent (plain chat) → join on `session_id` + `agent_id`, bounded to
  records timestamped at/after trace start (2 s clock-skew allowance).

The join is read-only against `usage_logs.json` via `usage_tracker.get_usage_logs`.

---

## 6. Retention & rotation

- Trace files rotate into `<YYYY-MM>/` month directories (writer path logic).
- Default retention: **30 days** (`DEFAULT_TRACE_RETENTION_DAYS`).
- Per-agent override: `trace_retention_days` field on the Agent model
  (`None` = use default).
- Purge is best-effort and opportunistic: after each trace write, files older
  than the retention window under that object's trace dir are deleted and
  empty month dirs removed. A dedicated purge endpoint arrives with the
  improvement router (risk register: "Trace files grow unbounded").

---

## 7. Version model fields (Checkpoint 1.2 / 1.3)

Added to both `Agent` (`core/models.py`) and `Orchestration`
(`core/models_orchestration.py`), all defaulted so existing JSON loads
unchanged (back-compat, checklist 1.4):

| Field | Default | Meaning |
|---|---|---|
| `parent_id` | `None` | id of the version this one was derived from |
| `version_n` | `1` | monotonically increasing version number |
| `is_active` | `True` | exactly one active version per object lineage |
| `improvement_run_id` | `None` | ImprovementRun that produced this version |
| `metric_snapshot` | `None` | benchmark/detector metrics captured at version time |

`Agent` additionally gains `trace_retention_days` (see §6).
