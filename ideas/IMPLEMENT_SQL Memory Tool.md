# IMPLEMENT.md — Synapse SQL Schema Memory

**Persistent, keyed, outcome-gated schema knowledge for the SQL MCP server**

This document specifies the SQL Schema Memory subsystem: a durable store of database knowledge that the NL2SQL agent reads before writing a query and writes to after a run is confirmed correct. It is scoped as a **standalone additive subsystem** on `backend/core/mcp_servers/sql_agent.py` (or wherever that module currently lives), with a new module `sql_memory.py`.

**Sequencing:** this subsystem must land **after CP6 closes.** It adds a tool to the SQL agent's tool list, which changes agent behavior; landing it mid-CP6 puts the byte-identical back-compat test (CP6 checklist 6.4) and the exact-reproducibility test (6.27) at risk. Write the spec now, merge it after CP6 is approved.

---

## 0. Scope discipline — read before writing code

**This is additive. It is not a refactor of the SQL agent.**

**Explicitly forbidden:**

- Changing the behavior, signature, or output format of `list_tables`, `get_table_schema`, or `run_sql_query`. An agent with memory disabled must behave byte-identically to today.
- Changing `get_db_engine`, the `_engines` cache, `_load_db_configs`, or `_extract_database_name`.
- Writing anything to a linked database. The memory subsystem reads target databases only, and only for schema fingerprinting. It never issues DDL or DML against `db_configs.json` targets.
- Introducing a second read-only guard. If a candidate query needs guarding, it goes through the existing guard path.
- Introducing a new connection manager, credential store, or DB config file.
- Adding a new MCP server. Memory tools live on the **existing** `sql-mcp-server`.

**Permitted modifications to existing files:**

| File | Permitted change |
|---|---|
| `sql_agent.py` | Import `sql_memory`; append two `types.Tool` entries to `list_tools()`; append two `elif` branches to `call_tool()`. Optionally append memory to `get_table_schema` output (§6.3). Nothing else. |
| `db_configs.json` | Untouched. Memory is keyed on the existing `id` field. |
| `SCHEMA.md` | Append a memory section. Do not rewrite existing sections. |
Anything else you want. But do not change current stuff.

---

## 1. Purpose and the problem being solved

`get_table_schema` returns column names, types, nullability, PKs, and declared FKs. That is the *structure* of the database. It says nothing about:

- What a table's grain actually is (one row per order, or per line item?)
- What a column means (`RevAmt INT` — dollars or cents?)
- What sentinel values exist (`-1` means refunded, not negative revenue)
- How tables join when no FK is declared
- Which filters are always required (soft deletes, tenant scoping)
- Which query shapes have previously produced correct answers

Every run, the agent rediscovers these by trial and error, or gets them wrong in a way that produces a *plausible wrong number* rather than an error. Schema memory makes that knowledge durable across runs.

**Non-goal:** this is not session memory, not conversational history, and not a general key-value store. It stores facts about databases, addressed by database object.

---

## 2. Storage

**SQLite, one file: `{DATA_DIR}/sql_memory.sqlite3`.**

Rationale for SQLite over the alternatives:

| Option | Verdict |
|---|---|
| **SQLite** | ✅ Retrieval is exact keyed lookup (`WHERE subject IN (...)`), not semantic search. Real indexes, transactions, transparent partial-index constraints, zero ops burden. |
| `json_store` | ❌ Read-whole-file-per-lookup degrades with size; no partial-key scan; no atomic upsert. |
| ChromaDB | ❌ Wrong access pattern. By retrieval time the agent already knows which tables it needs — it wants exact lookup, not approximate nearest-neighbour. Reserve for `query_exemplar` semantic matching in v2 (§10). |
| Target database | ❌ Requires write access to customer databases; couples memory lifetime to a DB the agent may only read. |
| Postgres | Deferred. Correct answer only if the backend is ever multi-replica against shared storage (§9.4). |

**Operational properties:**

- WAL mode; expect `.sqlite3-wal` and `.sqlite3-shm` sidecars in the same directory. Gitignore must cover all three.
- Per-call connections, no shared handle. Required: all DB work in `sql_agent.py` is dispatched via `asyncio.to_thread`, so the store is entered from arbitrary worker threads.
- `busy_timeout=10000` covers brief write contention.
- Durability follows `DATA_DIR`. If `DATA_DIR` is not a mounted volume, memory silently resets on container rebuild with no error — verify the mount.
- Backup: `sqlite3 sql_memory.sqlite3 ".backup out.db"` while running. Never `cp` a live WAL database.
- Plaintext on disk, unencrypted. No credentials, connection strings, or PII may be stored (§4.2).

---

## 3. Data model

```sql
CREATE TABLE memory (
  id             TEXT PRIMARY KEY,      -- uuid4 hex
  db_id          TEXT NOT NULL,         -- from db_configs.json; "__global__" for the fallback
  kind           TEXT NOT NULL,         -- one of the six in §4
  subject        TEXT NOT NULL,         -- canonical address, see §3.2
  content        TEXT NOT NULL,         -- the fact, <= 1200 chars
  payload        TEXT,                  -- JSON, kind-specific structured detail
  schema_fp      TEXT,                  -- DDL fingerprint of subject tables at write time
  verified       INTEGER DEFAULT 0,     -- 0 until promoted by a confirmed-good run
  uses           INTEGER DEFAULT 0,     -- incremented on retrieval
  successes      INTEGER DEFAULT 0,     -- incremented on promotion
  source_run_id  TEXT,
  created_at     REAL NOT NULL,
  updated_at     REAL NOT NULL,
  superseded_by  TEXT                   -- id of the row that replaced this one
);

CREATE UNIQUE INDEX ux_memory_live
  ON memory(db_id, kind, subject) WHERE superseded_by IS NULL;

CREATE INDEX ix_memory_subject
  ON memory(db_id, subject) WHERE superseded_by IS NULL;
```

### 3.1 Why `superseded_by` instead of `DELETE`

A bad write must be auditable and reversible. Rows are never deleted; an update marks the prior row superseded and inserts a new one, preserving `created_at`. The partial unique index enforces exactly one live row per address while permitting unlimited history behind it.

### 3.2 Canonical addressing — the central design decision

**The single most important rule in this document: entries are addressed canonically, not by free text.**

A free-text `set_memory(key, value)` produces `sales_info`, `about_sales`, `sales_tbl`, and `dbo.Sales notes` as four rows holding overlapping and eventually contradictory claims, with no mechanism to reconcile them. Canonical addressing makes a write an **upsert on a computed address**, so a correction replaces the thing it corrects.

| Kind | Address form | Canonicalization |
|---|---|---|
| `table_note` | `dbo.sales` | lowercase; `dbo`-qualify if unqualified; keep last two segments of `db.schema.table` |
| `column_note` | `dbo.sales.rev_amt` | table part canonicalized as above; column lowercased |
| `join_path` | `dbo.customers~dbo.sales` | both sides canonicalized, then **sorted**, so either argument order collides |
| `convention` | `soft_deletes` | lowercased slug; empty → `__db__` |
| `pitfall` | `dbo.sales` | as `table_note` |
| `query_exemplar` | `a3f9...` (16 hex) | `sha256(question.lower())[:16]` |

Malformed addresses are **rejected at write time with an explanatory error**, not silently coerced. A `join_path` naming one table, or a `column_note` with no dot, returns an error string the agent can act on.

### 3.3 Schema fingerprinting

`schema_fp` is `sha256` over the subject table(s)' column list — `name:type:is_nullable` per column, ordered by `column_id` — computed with the same `[{db}].sys.columns` join already used by `_run_get_table_schema`, truncated to 32 hex chars. For multi-table subjects (`join_path`), fingerprint each side and hash the concatenation.

On retrieval, the current fingerprint is recomputed (cached per table per call) and compared. A mismatch marks the entry **`STALE`** in the returned text: *"schema changed since this was written — verify before relying on it."*

This is the highest-severity failure mode the subsystem has. A stored note that was true before a column rename is now a confident lie, and unlike a wrong query it produces no error — just a plausible wrong number. Fingerprinting converts a silent lie into a visible warning.

Entries whose fingerprint cannot be computed (table dropped, permission denied) store `NULL` and are served without a staleness claim rather than being asserted as fresh.

---

## 4. What may be stored — the six kinds

**Governing rule: store what a schema dump cannot tell you.** Column names and types are already free from `get_table_schema`. Duplicating them wastes the retrieval budget and is the content most likely to go stale.

### 4.1 The kinds

**`table_note`** — what the table holds, at what grain, with what coverage caveats.
> *"One row per line item, not per order — `COUNT(*)` overcounts orders roughly 3x. Rows before 2019-01 were migrated from the legacy system and have NULL `channel`."*

**`column_note`** — meaning, units, encoding, sentinel values. **Highest-value kind.**
> *"Stored in cents as an INT, not dollars. `-1` is the sentinel for 'refunded', not negative revenue — exclude it or SUM is wrong."*

**`join_path`** — keys, cardinality, undeclared constraints, fan-out risk.
> *"Join `s.CustomerKey = c.CustomerKey`, many-to-one. No FK is declared. `CustomerKey` is reused across tenants — also match `s.TenantID = c.TenantID` or you get cross-tenant rows."*
> `payload: {"on": "s.CustomerKey = c.CustomerKey AND s.TenantID = c.TenantID", "cardinality": "many-to-one"}`
>
> The undeclared-FK case is precisely what `_run_get_table_schema` cannot surface, since it reads `sys.foreign_keys`.

**`convention`** — database-wide rules. Returned on **every** retrieval regardless of which tables were named.
> *"Every table has `IsDeleted BIT`. Always filter `IsDeleted = 0` unless the question explicitly concerns deleted records."*
>
> Keep this set small — five or six. Conventions consume budget on every call, so a large set starves the table-specific entries that were actually requested.

**`pitfall`** — a specific mistake made before and its correction.
> *"Grouping by `OrderDate` directly yields one row per timestamp — cast to DATE first. Previously returned 40k rows when the user wanted daily totals."*

**`query_exemplar`** — a question and SQL that verifiably answered it.
> content: *"Which region had the highest Q3 revenue?"*
> `payload: {"sql": "SELECT region, SUM(rev_amt)/100.0 AS r FROM dbo.sales WHERE quarter='Q3' AND rev_amt <> -1 GROUP BY region ORDER BY r DESC"}`
>
> Use sparingly. Exemplars are the kind most likely to be stale-but-plausible after a schema change, and their natural retrieval is semantic, not keyed — v1 matches only on exact question hash, which is a real limitation (§10).

### 4.2 What must never be stored

| Excluded | Why |
|---|---|
| Column lists, types, nullability | Free from `get_table_schema`; duplicates the thing fingerprinting exists to detect drift in |
| Row values, aggregates, query results | True for one snapshot, false next quarter; entries have no expiry, so this becomes a confident lie |
| The user's question or conversational context | Session state — belongs in ChromaDB with the rest of session memory |
| Unconfirmed assumptions | The agent persists its own hallucination and reads it back as fact forever (§5) |
| Credentials, connection strings, PII | Plaintext unencrypted file |
| Anything about a different `db_id` | Column meanings and join paths are not portable across databases |

Enforce the first two by tool description, the third by kind restriction, the fourth structurally by §5, and the last two by the `db_id` key.

---

## 5. Write discipline — the promotion path

**Entries land `verified: 0` and are served labelled `unverified`.**

The failure this prevents: the agent writes memory mid-run, before it knows whether it was right; the assumption was wrong; every subsequent run reads it back as established fact. Without gating, the subsystem's error rate compounds monotonically.

**Promotion is out-of-band.** `mark_verified(db_id, kind, subject, success=True)` sets `verified: 1` and increments `successes`. It is called from the **post-run path, not by the agent** — an agent that can promote its own writes has no gate at all.

Promotion signals, in order of preference:

1. **CP6 outcome grading.** A benchmark run with a known `outcome_score` gives an unambiguous verdict: promote entries written during high-scoring runs, discard entries from failed ones. This is the cleanest integration available and the reason this subsystem sequences after CP6.
2. **Explicit user confirmation** in the chat UI.
3. **Reuse without contradiction** — an entry retrieved in N subsequent runs that were not corrected. Weakest signal; do not implement in v1.

**Open decision — settle before writing code:** is `set_table_info` agent-callable at all, or is memory written only by a post-run distillation pass?

- *Agent-callable* (as specified here): cheaper, no extra LLM call, but entries are written mid-trajectory with partial information.
- *Distillation pass*: markedly higher entry quality, because it sees the whole trajectory and the outcome and can write one well-formed fact instead of five fragments. Costs one LLM call per run.

The `verified: 0` + promotion design gets most of the safety of the latter at the cost of the former. If quality proves insufficient in practice, switching is a matter of dropping the tool declaration and calling `set_entry` from the distillation path — the module API does not change.

---

## 6. Tool surface

Two tools on the existing `sql-mcp-server`, both dispatched via `asyncio.to_thread`, both accepting the existing `db_id` property.

### 6.1 `get_table_info`

```
db_id?       string    — as elsewhere; required when multiple DBs linked
table_names  string[]  — tables about to be queried
kinds?       string[]  — optional filter over the six kinds
```

Returns ranked, budget-capped text. Retrieval matches, for each named table: the exact subject, `subject LIKE 'tbl.%'` (column notes), `subject LIKE 'tbl~%'` and `LIKE '%~tbl'` (join paths), plus **all `convention` entries unconditionally**.

**Ranking:** `verified DESC, successes DESC, updated_at DESC`.
**Budget:** 4000 characters default. Entries beyond it are dropped and the count reported in the header.

The budget is not optional. "Infinite storage" is true of the file and false of the model's context; without a cap, the memory block grows until it crowds out the task, and the degradation is gradual enough that nobody notices.

`uses` is incremented for every entry actually returned.

### 6.2 `set_table_info`

```
db_id?    string  — as elsewhere
kind      string  — enum over the six kinds
subject   string  — canonicalized per §3.2
content   string  — the fact, <= 1200 chars
payload?  object  — kind-specific structured detail
```

Returns `"Stored <kind> for '<address>'."` or `"Updated ..."`, or an explanatory error.

**Tool description must instruct:** call only after a query has returned confirmed-correct results; one fact per call; store business meaning, not the column list; never store the user's question or a one-off result.

### 6.3 Optional: auto-attach to `get_table_schema`

Appending memory directly to `get_table_schema` output guarantees delivery and saves a round-trip, at the cost of doubling `sys.*` queries per schema lookup (fingerprint recomputation) and removing the agent's ability to skip it. **Default off.** If enabled, it is a strictly additive suffix — the existing output must remain a prefix of the new output, byte for byte.

---

## 7. Benchmark and reproducibility interaction

**`SYNAPSE_SQL_MEMORY_MODE=frozen` makes `set_table_info` a no-op.** Reads still work.

This must be set on every run with `source="benchmark"`, as a **hard invariant in code, not a config flag**. The reasoning:

- CP6 checklist 6.27 requires a `strict` deterministic benchmark to produce **exactly equal** outcome scores across two runs on a pinned snapshot.
- If memory accumulates across benchmark inputs, input 5 in run 1 sees different memory than input 5 in run 2. The runs are no longer the same experiment.
- The resulting score drift is indistinguishable from an agent regression, so `IMPROVE_RATCHET_DECIDE` starts reverting good edits on noise it cannot see.

This mirrors CP6's `snapshot_id` reasoning exactly: silent state drift beneath a scored run is the failure mode, and pinning is the mitigation.

**Corollary:** memory content is part of the agent's effective configuration. If a benchmark baseline was recorded with memory in state A, and memory advances to state B, baseline and new scores are measured with different rulers. Record a memory generation counter (max `updated_at` for the `db_id`) in benchmark result records, and treat a change as grounds for the same `grading_mismatch` handling CP6 §6.4 applies to rubric hash changes.

---

## 8. Risk register

| Risk | Mitigation |
|---|---|
| Agent persists its own hallucination as permanent fact | `verified: 0` on write; promotion only from a confirmed-good outcome; promotion not agent-reachable (M14) |
| Stored note survives a schema change and becomes a confident lie | `schema_fp` at write, recomputed at read, `STALE` label (M11–M12) |
| Free-text keys produce contradictory duplicate entries | Canonical addressing + partial unique index; write is an upsert (M6–M8) |
| Memory block crowds out the task in context | Hard 4000-char retrieval budget, ranked; 1200-char per-entry cap (M17–M18) |
| Memory drift breaks benchmark reproducibility | Frozen mode on benchmark runs, enforced in code; generation counter in result records (M23–M26) |
| Cross-database contamination | `db_id` in the primary key and every query predicate (M20) |
| Memory silently resets on redeploy | `DATA_DIR` volume mount verified; documented in SCHEMA.md |
| Concurrent writes from `to_thread` workers corrupt state | WAL + `busy_timeout` + per-call connections; load test (M3) |
| Subsystem writes to a customer database | Read-only account in the test suite (M22) |
| Conventions grow unbounded and starve table-specific entries | Documented cap of ~6; surfaced in the editor if one is built |
| Exemplar retrieval misses on rephrased questions | Acknowledged v1 limitation; semantic retrieval deferred (§10) |

---

## 9. Explicitly deferred

- **Semantic exemplar retrieval via ChromaDB.** The one genuinely semantic case. Only earns its keep past a few hundred verified exemplars; exact-hash matching covers the rest until then.
- **Per-user scoping.** CP6 §0.6.5 requires per-user auth scoping under `improve/<user_id>/`. This MCP server is a stdio process with no user context and no `resolve_improve_user` equivalent, so the store is flat. Threading a user id through is a design change, not a rename. Record as a known deviation.
- **Postgres backend.** Required only if the backend becomes multi-replica against shared storage — WAL over a network filesystem is unreliable. Only `_connect` changes.
- **A memory editor UI.** Inspection is `sqlite3` for v1.
- **Automatic decay / expiry.** Ranking by `verified` and `successes` approximates it; TTLs on schema facts are more likely to discard good knowledge than bad.
- **Cross-table inference** ("these three tables always join this way"). Speculative until there is usage data.

---

## 10. Exit criteria

Memory is stored under six canonical kinds in a local SQLite file keyed by `db_id`; a write upserts on a canonical address and supersedes rather than deletes; every entry records a schema fingerprint and is served `STALE` when the underlying DDL has moved; entries are unverified until promoted by a confirmed-good run through a path the agent cannot reach; retrieval is ranked and hard-budgeted; the store is frozen during benchmark runs so CP6's exact-reproducibility guarantee survives; nothing is ever written to a target database; and with memory disabled the three existing SQL tools are byte-identical to today. Wrtie now it should be empty SQL.

---

## Appendix A — Deviations from the earlier design sketch

1. **SQLite, not ChromaDB, as the primary store.** Retrieval is keyed, not semantic. Chroma is deferred to exemplar matching only.
2. **Typed kinds with canonical addresses, not free-text `get`/`set` keys.** This is the difference between a store that self-corrects and one that accumulates contradictions.
3. **Writes are gated, not immediate.** The originally requested `set_` tool exists, but its output is unverified until promoted.
4. **Scoping is `db_id`, not a connection string**, matching the existing `db_configs.json` identity.
5. **The subsystem is CP7-scoped**, after CP6, because it changes agent behavior under test.

---

## WHAT WAS DONE — implementation report (2026-08-05)

Implemented in full, after CP6 closed, as specified. Full suite: **1210 passed** (1156 pre-existing + 54 new), zero regressions. (One pre-existing, unrelated failure in `test_improve_cp6_benchmark_extremes.py::test_assign_random_invalid_total_ratio_raises[ratios0]` — a `splits.py` edge case that fails identically without this change.)

### Files created

| File | What |
|---|---|
| `backend/tools/sql_memory.py` | The entire subsystem (~500 lines). Storage, addressing, fingerprinting, gating, freezing, generation counter. |
| `backend/tests/unit/test_sql_memory.py` | 54 tests covering every exit criterion (§10) — see breakdown below. |

### Files modified (all additive)

| File | Change |
|---|---|
| `backend/tools/sql_agent.py` | Exactly what §0 permits: import `sql_memory` (with a `tools.`/script-style import fallback for both the test and MCP-subprocess import paths); two `types.Tool` entries appended to `list_tools()` (`get_table_info`, `set_table_info`, both dispatched via `asyncio.to_thread`, both accepting the existing `db_id` property); two `elif` branches appended to `call_tool()`. Nothing else touched — `list_tables` / `get_table_schema` / `run_sql_query` / `get_db_engine` / `_engines` / `_load_db_configs` / `_extract_database_name` are byte-identical. |
| `backend/core/improve/benchmark.py` | §7 hard invariant: `run_benchmark` wraps the input-execution loop in `sql_memory.freeze_writes(run_id)` — in code, not a config flag. §7 corollary: v2 result records carry `sql_memory_generation` (max `updated_at` for the benchmark's `execution_env.connection_id`, global max when unset, `null` when the store is empty). v1 records unchanged, preserving the exact CP4 field set. |
| `backend/core/improve/steps.py` | `grading_detail()` carries `sql_memory_generation`; `comparability_reason()` refuses to compare scores whose generation differs — same `grading_mismatch` inbox + revert handling CP6 §6.4 applies to rubric hash changes. |
| `backend/core/improve/SCHEMA.md` | New §9 "SQL Schema Memory" appended (storage, tools/gating, benchmark interaction, known deviations). Existing sections untouched. |
| `.gitignore` | `sql_memory.sqlite3` + `-wal`/`-shm` sidecars + `sql_memory.freeze` marker. |
| `db_configs.json` | Untouched, as required. |

### How each section of the spec landed

- **§2 Storage** — `{DATA_DIR}/sql_memory.sqlite3`, WAL mode, `busy_timeout=10000`, per-call connections (safe from `asyncio.to_thread` workers; verified by an 8-thread × 5-write contention test). Plaintext; the tool description forbids credentials/PII.
- **§3 Data model** — schema exactly as specified, including the partial unique index `ux_memory_live ON memory(db_id, kind, subject) WHERE superseded_by IS NULL` and `ix_memory_subject`. Updates supersede (preserving `created_at`) and never delete; history is auditable.
- **§3.2 Canonical addressing** — all six forms implemented: lowercase + `dbo`-qualify + last-two-segments for tables; table-canonical + lowercased column for `column_note`; both-sides-canonicalized-then-**sorted** for `join_path` (either argument order collides); lowercased slug (empty → `__db__`) for `convention`; `sha256(question.lower())[:16]` for `query_exemplar` (16-hex passthrough). Malformed addresses raise `AddressError` with an agent-actionable message (surfaced as `Error: ...` text through the existing `call_tool` except path) — never coerced.
- **§3.3 Fingerprinting** — sha256 over `name:type:is_nullable` ordered by `column_id`, same `[{db}].sys.columns` join as `_run_get_table_schema`, truncated to 32 hex; join paths hash the concatenation of both sides. Recomputed on retrieval (cached per table per call); mismatch is labelled `STALE: schema changed since this was written — verify before relying on it`. Uncomputable fingerprints store `NULL` and are served without a staleness claim.
- **§4 Six kinds** — enforced by enum in the tool schema and in code. Exclusions (§4.2) enforced by tool description (column lists, one-off results, questions), structurally (§5 gating), and by the `db_id` key (cross-DB contamination).
- **§5 Write discipline** — entries land `verified: 0`, served labelled `unverified`. Promotion is `mark_verified(db_id, kind, subject, success)` + `mark_run_outcome(source_run_id, success)` (the CP6 integration point: promote entries from confirmed-good runs, discard from failed ones — discard supersedes with a `__discarded__` tombstone, preserving history). **Neither is exposed as an MCP tool** — the agent cannot promote its own writes (verified by test). The §5 open decision was settled as *agent-callable* `set_table_info` with the `verified: 0` + promotion gate, exactly as the spec's own analysis recommends; switching to a distillation pass later requires only dropping the tool declaration.
- **§6 Tool surface** — `get_table_info(db_id?, table_names, kinds?)`: matches exact subject, `LIKE 'tbl.%'`, `LIKE 'tbl~%'`/`'%~tbl'`, plus all conventions unconditionally; ranked `verified DESC, successes DESC, updated_at DESC`; hard 4000-char budget with dropped-count reporting in the header; `uses` incremented only for entries actually returned. `set_table_info(db_id?, kind, subject, content, payload?)`: returns `Stored/Updated <kind> for '<address>'.`; 1200-char content cap. §6.3 auto-attach: **not implemented** (default off, as specified) — `get_table_schema` output has no memory suffix, byte for byte.
- **§7 Benchmark/reproducibility** — frozen mode makes `set_table_info` a no-op while reads work. Two mechanisms, because the SQL MCP server is a separate long-lived process: the `SYNAPSE_SQL_MEMORY_MODE=frozen` env var (in-process and newly spawned children) **and** a `{DATA_DIR}/sql_memory.freeze` marker file (crosses the process boundary to an already-running MCP subprocess). `run_benchmark` engages both around every input via `freeze_writes()` try/finally. Generation counter recorded in v2 results; ratchet treats a generation change as incomparable (revert + `grading_mismatch` inbox entry).
- **Scoping** — `resolve_db_id()` mirrors `get_db_engine`'s auto-select-when-single-config rule without touching it; falls back to `__global__`.

### Test coverage (54 tests, `tests/unit/test_sql_memory.py`)

Canonical addressing (12) · writes/upsert/supersede/caps/concurrency (7) · retrieval matching/ranking/budget/uses/isolation (11) · staleness (5) · promotion incl. not-an-MCP-tool (5) · frozen mode + generation (4) · tool surface through the real `call_tool` (4) · back-compat byte-identity + never-writes-to-target (3, source-inspection style matching `test_sql_agent_debug.py`) · benchmark freeze integration through the real `run_benchmark` (1) · ratchet generation comparability (2).

### Deviations, all recorded in SCHEMA.md §9.4

- Flat store, not per-user (stdio process has no user context) — acknowledged deviation per §9 of the spec.
- Exemplar retrieval is exact-hash only; ChromaDB semantic retrieval deferred (§9).
- `sql_memory_generation` is recorded on **v2** benchmark records only, because the CP6 code and its comment guarantee v1 records keep exactly the CP4 field set (§0's "do not change current stuff" outranks the corollary's "all result records").
- No memory editor UI; inspection is `sqlite3` (§9).

### Exit criteria (§10) — all met

Six canonical kinds in a local SQLite file keyed by `db_id` ✅ · writes upsert on a canonical address and supersede rather than delete ✅ · every entry records a schema fingerprint and is served `STALE` when the DDL moved ✅ · entries unverified until promoted through a path the agent cannot reach ✅ · retrieval ranked and hard-budgeted ✅ · store frozen during benchmark runs, in code ✅ · nothing ever written to a target database (fingerprint path is a single `SELECT`; verified by test) ✅ · with memory unused, the three existing SQL tools are byte-identical ✅ · store starts empty (no seed rows; file not even created until first use) ✅
