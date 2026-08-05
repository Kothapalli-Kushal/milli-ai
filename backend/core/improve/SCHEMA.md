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

---

## 8. Outcome grading (Checkpoint 6)

CP1–CP5 measure *how the agent behaved*. CP6 adds a second axis measuring
*what it should have produced*.

```
composite = process_weight × process_score + outcome_weight × outcome_score
```

`process_score` is the CP4 weighted detector composite, computed by the
existing `benchmark.score_traces` with unchanged arithmetic. `outcome_score`
is the weighted mean of per-input outcome scores from `grading.py`. Weights are
normalized by their sum, so they need not total 1. `outcome_weight: 0`
reproduces CP4 exactly; `process_weight: 0` is legal.

If **every** input's outcome is N/A, the outcome axis reports `N/A`, the
composite falls back to the process axis alone, and `outcome_na: true` is
recorded. 0 is never silently substituted — that is indistinguishable from
"the agent got everything wrong" and would trigger a spurious ratchet revert.

### 8.1 Schema dispatch

`schema_version` absent or `1` → CP4 semantics exactly: no grading stage runs,
no CP6 keys are written to the benchmark file, and the result record keeps the
CP4 field set. `2` → the grading stage runs and the record gains the fields in
§8.6.

### 8.2 The per-input contract

Both grading modes emit the same object, so everything downstream is
mode-agnostic:

```jsonc
{
  "input_id": "in_001",
  "score":     0.75,          // [0,1]; null == N/A
  "na_reason": null,          // extraction_failed | judge_unavailable | malformed_verdict
  "vetoed":    false,         // a critical check/criterion failed → score forced to 0.0
  "checks": [
    {
      "check_id": "sql_rows", "status": "pass", "weight": 4.0,
      "critical": true, "detail": "...", "actual": "SELECT ...",
      "trace_file": "traces/agent_1/2026-08/s1.json", "message_idx": 2
    }
  ]
}
```

`CheckResult.status` ∈ `pass | fail | extraction_failed | execution_timeout |
row_cap_exceeded | judge_na`.

`outcome_score(input) = Σ(weights of passing checks) / Σ(all check weights)`,
excluding N/A-status checks from the denominator. Partial credit is
deliberate: with ten inputs, binary pass/fail moves the score in 0.1 steps and
the ratchet cannot see genuine incremental improvement. A failing check with
`critical: true` forces the input to `0.0` and sets `vetoed: true`.

### 8.3 Extractors (v1 set — closed)

| `from` | Behavior |
|---|---|
| `final_output` | Trace `output` field. |
| `last_assistant_message` | Last `role: assistant` message content. |
| `tool_call_arg` | Requires `tool` + `arg`; `occurrence: first \| last \| any` (default `last`). Reads `messages[].tool_calls[].function.arguments`, JSON-parsed. |
| `tool_result` | Requires `tool`; content of the matching `tool_call_id` result message. |

All read the CP1 trace dict as-is. **No new instrumentation, no new trace
fields.** An optional `regex` post-processes the extracted string and takes
capture group 1; a non-match is an **extraction failure**, not a wrong answer.

### 8.4 Comparators (v1 set — closed)

| `type` | Semantics |
|---|---|
| `exact` | String equality after `strip()`; `case_sensitive` defaults `false`. |
| `contains_all` | All listed substrings present. |
| `regex` | Extracted value matches the pattern. |
| `numeric` | Parse to float; pass if `abs(actual − value) <= tol`. |
| `json_equal` | Structural compare; key order irrelevant, array order significant unless `order_sensitive: false`. |
| `sql_equivalent` | AST normalization via `sqlglot` — see below. |
| `sql_execution` | Execute both queries, compare result sets — see below. |
| `semantic_match` | Opt-in single binary LLM verdict. |
| `any_of` | Wrapper; passes if any option passes. |

#### `resultset` is DEFERRED — and is not the same thing as `sql_execution`

`resultset` would compare rows **scraped out of the trace's tool-result
message**, which depends on the tool result being reliably JSON-shaped in the
trace. That is not yet guaranteed, so it stays out of CP6. `sql_execution`
instead executes both the candidate and reference queries **at grade time**
against a declared connection, so it does not depend on trace shape at all.
The two are different mechanisms and must not be conflated.

#### `sql_equivalent` limitations (read before weighting it)

Normalization pipeline, applied to both sides: parse → strip comments →
lowercase unquoted identifiers and keywords → sort operands of commutative
predicates (`AND`, `OR`, `=` between simple operands) → sort `SELECT`
projections when no `ORDER BY` / `GROUP BY` ordinal depends on their position →
compare rendered ASTs.

This is **not** semantic equivalence — that is undecidable. It will fail a
genuinely correct answer written as a `JOIN` where the reference used a
subquery, or as a window function where the reference used `ORDER BY … LIMIT
1`. Treat it as a **low-weight diagnostic** that tells the tuner *how* the
query differed, never as the authoritative correctness check. A parse failure
on the **actual** value is a check failure; a parse failure on the **expected**
value is an authoring error raised at **save time**.

#### `sql_execution` — the authoritative NL2SQL check

Executes candidate and reference against the same connection and compares
returned rows. Still deterministic grading: equivalence is decided by the data,
not by a model's opinion.

```jsonc
"execution_env": {
  "connection_id": "sales_readonly",   // an EXISTING db_configs.json id
  "snapshot_id":   "2026-08-01T00:00Z",
  "timeout_s":     10,
  "max_rows":      5000
}
```

- Sits at the **benchmark** level; per-input override allowed.
- Resolves an **existing** Synapse SQL connection via
  `tools/sql_agent.py::get_db_engine`. CP6 introduces no connection manager, no
  credential storage, and no DB config.
- **Multiset by default.** `order_sensitive: "auto" | true | false`; `auto`
  derives ordering from whether the reference has a top-level `ORDER BY`.
- **Column matching** `by_position` (default) or `by_name`; aliases are ignored
  under `by_position` — an agent naming a column `total` instead of `r` is not
  an error.
- **Float tolerance** via `float_tol`; `Decimal`/`float` normalized, dates and
  `Decimal` canonicalized to strings, before comparison.
- **Empty result set** is a legitimate expected value, distinguishable from an
  execution error.
- **Read-only enforcement on both sides, before execution.** The primary
  authority is the existing `tools/sql_agent.py::_is_write_query` guard;
  `sqlglot` adds a second opinion that also catches writes hidden behind CTEs
  and stacked statements. A candidate `DROP TABLE` is a check **failure**, never
  an executed statement.
- **Timeout / row cap** produce the distinct statuses `execution_timeout` and
  `row_cap_exceeded` — neither a silent pass nor an extraction failure.
- **Save-time reference validation**: the reference is executed twice and
  rejected if the results differ; `LIMIT`/`TOP` without `ORDER BY`, and
  `NOW()`/`CURRENT_DATE`/`RANDOM()`-style constructs, produce warnings.

#### `semantic_match` and `grading_strictness`

`semantic_match` issues one binary LLM verdict per check, reusing the rubric
judge path, cache, and injection hardening. It is **never permitted for SQL
checks** — a `semantic_match` on a `tool_call_arg` extractor whose argument
names a SQL statement is refused at save time.

A benchmark containing at least one `semantic_match` check is labeled
`grading_strictness: "mixed"`; otherwise `"strict"`. The label is **derived,
not authored**, recorded in every result record, and selects the
reproducibility threshold in §8.7.

### 8.5 Rubric mode

Rubrics are standalone, reusable, **immutable-per-version** objects, stored
per-user:

```
improve/<user_id>/rubrics/<rubric_id>/v<N>.json   # immutable versions
improve/<user_id>/rubrics/index.json              # id → latest version, name, content_hash
improve/<user_id>/judge_cache/<hash>.json         # verdict cache entries
```

`rubrics.py` is the **single authoritative Rubric registry for Synapse**. The
Training-tab concept of a flat `data/rubrics.json` is **superseded** — do not
create it; a shared flat file cannot satisfy §0.6.5's per-user auth-scoping
constraint. Public API: `get_rubric`, `list_rubrics`, `save_rubric`,
`resolve_version`.

`content_hash` is `sha256` over `{id, name, criteria}` only. Bumping a version
without changing the criteria therefore does **not** change the hash — a
re-save must not spuriously make old scores incomparable. An edit writes a new
`version`; prior versions remain readable and are never mutated.

Criterion kinds (exactly three):

- **`key_point_coverage`** — atomic binary judge calls, one per key point,
  batched into a single request returning a JSON verdict array. Score =
  `Σ(matched weights) / Σ(weights)`. `forbidden` items are graded the same way,
  inverted; a hit forces the criterion to 0. Atomic binary judgments are
  markedly more stable across runs than holistic scoring.
- **`anchored`** — integer `0..scale` with a written anchor for **every** level
  (a missing anchor is a save-time error). Normalized `score/scale`.
- **`deterministic`** — embeds a §8.3/§8.4 check inside a rubric. Pass → 1.0,
  fail → 0.0.

`outcome_score(input) = Σ(wᵢ × normalizedᵢ) / Σ(wᵢ)`, with the same critical
veto (`normalized < critical_floor` → input scored 0, `vetoed: true`).

**Judge discipline.** Model resolution: `judge_model` (per-run) → settings
`improve_judge_model` → `settings.model`, pinned and recorded per result.
Judge == tuner model is a **soft UI warning**, not a hard block. Verdicts are
cached at `judge_cache/`, keyed on `sha256(rubric_id, rubric_version,
content_hash, criterion_id, judge_model, input_id, normalized_output_text,
expectation_hash)` — this is the primary reproducibility mechanism, not an
optimization. `judge.samples` defaults to 1; >1 aggregates by median.
Prompt-injection hardening is mandatory: agent output is inserted inside a
delimited block explicitly labeled untrusted data to be graded, never as
instructions, and forged markers in the payload are neutralized. A malformed
verdict gets one retry, then that criterion is N/A and drops out of the
denominator. Judge spend joins `usage_tracker` with `source="improve_judge"`
and counts against `improve_budget_usd`.

### 8.6 Result-record additions

All optional; existing readers tolerate their absence. Present only for
`schema_version: 2` runs.

```
{…CP4 fields…,
 schema_version, process_score, outcome_score, composite_score,
 grading_mode, grading_strictness,
 rubric_id, rubric_version, rubric_content_hash,
 execution_connection_id, snapshot_id,
 judge_model, judge_cache_hits, judge_spend_usd,
 split_seed, extraction_failed_count, extraction_failed_rate,
 outcome_na, per_input: [InputOutcome]}
```

For a v2 benchmark `score` carries the **composite**, so the ratchet compares
composites; for a v1 benchmark `score` remains the CP4 process score.

**New inbox kinds:** `grading_mismatch`, `grading_unreliable`.

### 8.7 Reproducibility thresholds, by mode

| Axis / mode | Threshold |
|---|---|
| Process axis (all modes) | ±0.02 (`SCORE_VARIANCE_THRESHOLD`, unchanged from CP4) |
| Outcome, deterministic + `strict` + pinned snapshot | **exact equality** |
| Outcome, deterministic + `strict` + `snapshot_id: "unpinned"` | ±0.02, flagged in the UI |
| Outcome, deterministic + `mixed` | ±0.05 (`OUTCOME_VARIANCE_THRESHOLD_RUBRIC`) |
| Outcome, rubric | ±0.05 (`OUTCOME_VARIANCE_THRESHOLD_RUBRIC`) |

Deterministic + strict + pinned is held to exact equality because no LLM is
involved and the data is fixed: any drift is a bug in an extractor, a
nondeterministic agent, or a nondeterministic query, and must fail loudly. When
a connection cannot report a snapshot identifier, `snapshot_id: "unpinned"` is
recorded and the score is explicitly **not** claimed to be exactly
reproducible — silent DB drift between a baseline run and a new run is
otherwise indistinguishable from an agent regression.

### 8.8 Score comparability

`IMPROVE_RATCHET_DECIDE` **refuses to compare** two scores whose
`rubric_content_hash` or `grading_mode` differ. It emits a `grading_mismatch`
inbox entry and treats the iteration as `revert` (the CP5 safe default for a
missing or incomparable score). Silently comparing across rubric versions is
the subtlest way this subsystem can lie to you.
