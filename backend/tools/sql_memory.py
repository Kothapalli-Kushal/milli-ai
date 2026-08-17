"""
SQL Schema Memory — persistent, keyed, outcome-gated schema knowledge for the
SQL MCP server (spec: ideas/IMPLEMENT_SQL Memory Tool.md).

Design invariants, in order of importance:

- Entries are addressed CANONICALLY, not by free text (§3.2). A write is an
  upsert on a computed address, so a correction replaces the thing it corrects.
  Malformed addresses are rejected with an explanatory error, never coerced.
- Rows are never deleted. An update marks the prior row superseded and inserts
  a new one; a partial unique index enforces exactly one live row per address
  while permitting unlimited history behind it (§3.1).
- Entries land `verified: 0` and are served labelled `unverified` (§5).
  Promotion (`mark_verified` / `mark_run_outcome`) happens out-of-band from the
  post-run path — it is deliberately NOT exposed as an MCP tool, so the agent
  cannot promote its own writes.
- Every entry records a schema fingerprint at write time; retrieval recomputes
  it and labels a mismatch STALE (§3.3). This converts the subsystem's worst
  failure mode — a confident lie after a schema change — into a visible warning.
- Retrieval is ranked (verified DESC, successes DESC, updated_at DESC) and
  hard-budgeted to 4000 chars (§6.1).
- `SYNAPSE_SQL_MEMORY_MODE=frozen` (or the freeze marker file, which crosses
  the process boundary to the long-lived MCP subprocess) makes writes a no-op
  while reads still work (§7). Benchmark runs freeze memory as a hard
  invariant in code so CP6's exact-reproducibility guarantee survives.
- This module NEVER writes to a target database. Target engines are used
  read-only, and only for schema fingerprinting via sys.columns.

Storage is a single SQLite file `{DATA_DIR}/sql_memory_db/sql_memory.sqlite3` in WAL mode
with per-call connections — all callers dispatch via asyncio.to_thread, so the
store is entered from arbitrary worker threads (§2).
"""
import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager

from core.config import DATA_DIR

# ── constants ─────────────────────────────────────────────────────────────────

KINDS = (
    "table_note", "column_note", "join_path",
    "convention", "pitfall", "query_exemplar",
)

MAX_CONTENT_CHARS = 1200        # per-entry cap (§4 / risk M17-M18)
DEFAULT_BUDGET_CHARS = 4000     # retrieval budget (§6.1)
GLOBAL_DB_ID = "__global__"     # fallback scope when no db_id resolves
DISCARDED = "__discarded__"     # superseded_by marker for discarded entries

_FREEZE_ENV = "SYNAPSE_SQL_MEMORY_MODE"
_HEX16 = re.compile(r"^[0-9a-f]{16}$")


class AddressError(ValueError):
    """A subject that cannot be canonicalized. The message is agent-actionable."""


# ── storage ───────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS memory (
  id             TEXT PRIMARY KEY,
  db_id          TEXT NOT NULL,
  kind           TEXT NOT NULL,
  subject        TEXT NOT NULL,
  content        TEXT NOT NULL,
  payload        TEXT,
  schema_fp      TEXT,
  verified       INTEGER DEFAULT 0,
  uses           INTEGER DEFAULT 0,
  successes      INTEGER DEFAULT 0,
  source_run_id  TEXT,
  created_at     REAL NOT NULL,
  updated_at     REAL NOT NULL,
  superseded_by  TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_memory_live
  ON memory(db_id, kind, subject) WHERE superseded_by IS NULL;
CREATE INDEX IF NOT EXISTS ix_memory_subject
  ON memory(db_id, subject) WHERE superseded_by IS NULL;
"""


def db_path() -> str:
    return os.path.join(DATA_DIR, "sql_memory_db", "sql_memory.sqlite3")


def _connect() -> sqlite3.Connection:
    """Per-call connection — no shared handle across to_thread workers (§2)."""
    os.makedirs(os.path.dirname(db_path()), exist_ok=True)
    conn = sqlite3.connect(db_path(), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.executescript(_SCHEMA_SQL)
    return conn


# ── frozen mode (§7) ──────────────────────────────────────────────────────────

def _freeze_marker_path() -> str:
    return os.path.join(DATA_DIR, "sql_memory.freeze")


def frozen() -> bool:
    """True when writes must be a no-op. Reads always work.

    Two mechanisms because the SQL MCP server is a separate long-lived process:
    the env var covers processes spawned inside a frozen scope; the marker file
    in the shared DATA_DIR covers subprocesses that were already running.
    """
    if os.environ.get(_FREEZE_ENV, "").strip().lower() == "frozen":
        return True
    return os.path.exists(_freeze_marker_path())


@contextmanager
def freeze_writes(reason: str = ""):
    """Freeze memory writes for the duration of the block.

    This is the hard invariant behind CP6 checklist 6.27: memory accumulating
    across benchmark inputs makes input 5 in run 1 a different experiment from
    input 5 in run 2, and the resulting score drift is indistinguishable from
    an agent regression.
    """
    prior = os.environ.get(_FREEZE_ENV)
    os.environ[_FREEZE_ENV] = "frozen"
    marker = _freeze_marker_path()
    try:
        with open(marker, "w", encoding="utf-8") as f:
            f.write(reason or "frozen")
    except OSError:
        pass  # env var still enforces in-process
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop(_FREEZE_ENV, None)
        else:
            os.environ[_FREEZE_ENV] = prior
        try:
            os.remove(marker)
        except OSError:
            pass


# ── canonical addressing (§3.2) ──────────────────────────────────────────────

def _canon_table(name: str) -> str:
    """Canonicalize a table reference: lowercase, dbo-qualify, keep the last
    two segments of `db.schema.table`. Strips [] quoting."""
    raw = (name or "").strip()
    parts = [p.strip().strip("[]").strip().lower()
             for p in raw.split(".") if p.strip().strip("[]").strip()]
    if not parts:
        raise AddressError("empty table name")
    for p in parts:
        if "~" in p:
            raise AddressError(
                f"invalid character '~' in table name {name!r} — "
                "'~' is reserved as the join_path separator"
            )
    if len(parts) == 1:
        parts = ["dbo"] + parts
    return ".".join(parts[-2:])


def canonicalize(kind: str, subject: str) -> str:
    """Compute the canonical address for (kind, subject), or raise AddressError
    with a message the agent can act on. Never silently coerces garbage."""
    subject = (subject or "").strip()
    if not subject and kind != "convention":
        raise AddressError(f"subject is required for kind '{kind}'")

    if kind in ("table_note", "pitfall"):
        return _canon_table(subject)

    if kind == "column_note":
        if "." not in subject:
            raise AddressError(
                f"column_note subject must be 'table.column' or "
                f"'schema.table.column', got {subject!r} — which table does "
                "this column belong to?"
            )
        table_part, _, column = subject.rpartition(".")
        column = column.strip().strip("[]").lower()
        if not column:
            raise AddressError(f"column_note subject {subject!r} has an empty column part")
        return f"{_canon_table(table_part)}.{column}"

    if kind == "join_path":
        sides = [s for s in (p.strip() for p in subject.split("~")) if s]
        if len(sides) != 2:
            raise AddressError(
                f"join_path subject must name exactly two tables separated by "
                f"'~' (e.g. 'dbo.customers~dbo.sales'), got {subject!r}"
            )
        a, b = sorted(_canon_table(s) for s in sides)
        if a == b:
            raise AddressError(
                f"join_path subject names the same table twice: {a!r}"
            )
        return f"{a}~{b}"

    if kind == "convention":
        slug = re.sub(r"[^a-z0-9_]+", "_", subject.lower()).strip("_")
        return slug or "__db__"

    if kind == "query_exemplar":
        if _HEX16.match(subject):
            return subject
        return hashlib.sha256(subject.lower().encode("utf-8")).hexdigest()[:16]

    raise AddressError(f"unknown kind {kind!r} — expected one of {', '.join(KINDS)}")


def _subject_tables(kind: str, address: str) -> list[str]:
    """Tables to fingerprint for a canonical address. Empty for kinds with no
    table anchor (convention, query_exemplar)."""
    if kind in ("table_note", "pitfall"):
        return [address]
    if kind == "column_note":
        return [address.rpartition(".")[0]]
    if kind == "join_path":
        return address.split("~")
    return []


# ── schema fingerprinting (§3.3) ─────────────────────────────────────────────

def _table_fp(conn, db: str, schema_name: str, table_name: str) -> str | None:
    """sha256 over `name:type:is_nullable` per column ordered by column_id,
    using the same [{db}].sys.columns join as _run_get_table_schema. Returns
    None when the table is missing or unreadable. Read-only by construction."""
    from sqlalchemy import text
    rows = conn.execute(text(f"""
        SELECT c.name, tp.name AS type_name, c.is_nullable
        FROM [{db}].sys.columns c
        JOIN [{db}].sys.objects o ON o.object_id = c.object_id
        JOIN [{db}].sys.schemas s ON s.schema_id = o.schema_id
        JOIN [{db}].sys.types tp ON tp.user_type_id = c.user_type_id
        WHERE s.name = :schema AND o.name = :table
        ORDER BY c.column_id
    """), {"schema": schema_name, "table": table_name}).fetchall()
    if not rows:
        return None
    blob = "|".join(f"{r[0]}:{r[1]}:{int(bool(r[2]))}" for r in rows)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def compute_schema_fp(engine, db: str, tables: list[str],
                      _cache: dict | None = None) -> str | None:
    """Fingerprint of the subject table(s), or None when it cannot be computed
    (no engine, table dropped, permission denied). A None fingerprint is served
    without a staleness claim rather than asserted as fresh (§3.3)."""
    if engine is None or not db or not tables:
        return None
    fps = []
    try:
        with engine.connect() as conn:
            for t in tables:
                if _cache is not None and t in _cache:
                    fp = _cache[t]
                else:
                    schema_name, _, table_name = t.partition(".")
                    fp = _table_fp(conn, db, schema_name, table_name)
                    if _cache is not None:
                        _cache[t] = fp
                if fp is None:
                    return None
                fps.append(fp)
    except Exception:
        return None
    if len(fps) == 1:
        return fps[0]
    return hashlib.sha256("".join(fps).encode("utf-8")).hexdigest()[:32]


# ── scoping ───────────────────────────────────────────────────────────────────

def resolve_db_id(explicit: str | None, configs: list[dict]) -> str:
    """Memory scope key. Mirrors get_db_engine's auto-select-when-single rule
    so memory follows the database actually being hit; never touches engines."""
    if explicit:
        return explicit
    if len(configs) == 1 and configs[0].get("id"):
        return str(configs[0]["id"])
    return GLOBAL_DB_ID


# ── writes (§6.2) ─────────────────────────────────────────────────────────────

def set_entry(db_id: str, kind: str, subject: str, content: str,
              payload: dict | None = None, engine=None, db: str | None = None,
              source_run_id: str | None = None) -> str:
    """Upsert one fact at its canonical address. Lands verified=0 (§5).

    Returns a human-readable confirmation, or raises AddressError / ValueError
    with an explanatory message.
    """
    if frozen():
        return ("Memory is frozen (benchmark mode) — nothing was stored. "
                "Reads still work.")
    if kind not in KINDS:
        raise AddressError(
            f"unknown kind {kind!r} — expected one of {', '.join(KINDS)}")
    content = (content or "").strip()
    if not content:
        raise ValueError("content is required — one concise fact per call")
    if len(content) > MAX_CONTENT_CHARS:
        raise ValueError(
            f"content is {len(content)} chars; the per-entry cap is "
            f"{MAX_CONTENT_CHARS}. Store one fact per call, not a dump."
        )
    address = canonicalize(kind, subject)
    fp = compute_schema_fp(engine, db, _subject_tables(kind, address))
    payload_json = json.dumps(payload, default=str) if payload else None
    now = time.time()
    new_id = uuid.uuid4().hex

    conn = _connect()
    try:
        with conn:  # one transaction: supersede + insert are atomic
            row = conn.execute(
                "SELECT id, created_at FROM memory WHERE db_id=? AND kind=? "
                "AND subject=? AND superseded_by IS NULL",
                (db_id, kind, address),
            ).fetchone()
            created_at = row[1] if row else now
            if row:
                conn.execute(
                    "UPDATE memory SET superseded_by=? WHERE id=?",
                    (new_id, row[0]),
                )
            conn.execute(
                "INSERT INTO memory (id, db_id, kind, subject, content, payload,"
                " schema_fp, verified, uses, successes, source_run_id,"
                " created_at, updated_at, superseded_by)"
                " VALUES (?,?,?,?,?,?,?,0,0,0,?,?,?,NULL)",
                (new_id, db_id, kind, address, content, payload_json, fp,
                 source_run_id, created_at, now),
            )
    finally:
        conn.close()
    verb = "Updated" if row else "Stored"
    return f"{verb} {kind} for '{address}'."


# ── retrieval (§6.1) ─────────────────────────────────────────────────────────

def _match_clause(tables: list[str]) -> tuple[str, list[str]]:
    """WHERE fragment matching, per named table: the exact subject, column
    notes (LIKE 'tbl.%'), and join paths (LIKE 'tbl~%' / '%~tbl')."""
    ors, params = [], []
    for t in tables:
        ors.append("subject = ?")
        params.append(t)
        ors.append("subject LIKE ?")
        params.append(f"{t}.%")
        ors.append("subject LIKE ?")
        params.append(f"{t}~%")
        ors.append("subject LIKE ?")
        params.append(f"%~{t}")
    return "(" + " OR ".join(ors) + ")", params


def get_entries(db_id: str, table_names: list[str], kinds: list[str] | None = None,
                engine=None, db: str | None = None,
                budget: int = DEFAULT_BUDGET_CHARS) -> str:
    """Ranked, budget-capped memory text for the named tables.

    Matches exact subjects, column notes, and join paths for each table, plus
    ALL convention entries unconditionally (subject to the kinds filter).
    Ranking: verified DESC, successes DESC, updated_at DESC. Entries beyond
    the budget are dropped and the count reported in the header. `uses` is
    incremented for every entry actually returned.
    """
    tables = []
    for t in table_names or []:
        try:
            tables.append(_canon_table(t))
        except AddressError:
            continue

    if kinds:
        bad = [k for k in kinds if k not in KINDS]
        if bad:
            raise AddressError(
                f"unknown kind(s) {', '.join(map(repr, bad))} — expected a "
                f"subset of {', '.join(KINDS)}")

    where = ["db_id = ?", "superseded_by IS NULL"]
    params: list = [db_id]
    match_parts = []
    if tables:
        clause, cl_params = _match_clause(tables)
        match_parts.append(clause)
        params_match = cl_params
    else:
        params_match = []
    # conventions are returned regardless of which tables were named
    match_parts.append("kind = 'convention'")
    where.append("(" + " OR ".join(match_parts) + ")")
    params.extend(params_match)
    if kinds:
        where.append(f"kind IN ({','.join('?' * len(kinds))})")
        params.extend(kinds)

    if not os.path.exists(db_path()):
        return _empty_result(tables)

    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, kind, subject, content, payload, schema_fp, verified,"
            " successes FROM memory WHERE " + " AND ".join(where) +
            " ORDER BY verified DESC, successes DESC, updated_at DESC",
            params,
        ).fetchall()

        if not rows:
            return _empty_result(tables)

        fp_cache: dict = {}
        lines, used_ids, dropped, total = [], [], 0, 0
        for (row_id, kind, subject, content, payload_json, stored_fp,
             verified, _successes) in rows:
            label = "verified" if verified else "unverified"
            if stored_fp:
                current = compute_schema_fp(
                    engine, db, _subject_tables(kind, subject), _cache=fp_cache)
                if current is not None and current != stored_fp:
                    label += ", STALE: schema changed since this was written" \
                             " — verify before relying on it"
            line = f"- [{kind}] {subject} ({label}): {content}"
            if payload_json:
                line += f"\n  payload: {payload_json}"
            if total + len(line) + 1 > budget:
                dropped += 1
                continue
            total += len(line) + 1
            lines.append(line)
            used_ids.append(row_id)

        if used_ids:
            conn.execute(
                f"UPDATE memory SET uses = uses + 1 WHERE id IN "
                f"({','.join('?' * len(used_ids))})",
                used_ids,
            )
            conn.commit()
    finally:
        conn.close()

    header = (f"SQL schema memory for {', '.join(tables) if tables else db_id}"
              f" — {len(lines)} entr{'y' if len(lines) == 1 else 'ies'}")
    if dropped:
        header += f" ({dropped} more dropped: over the {budget}-char budget)"
    header += (". Unverified entries are unconfirmed hypotheses; STALE entries "
               "predate a schema change.")
    return header + "\n" + "\n".join(lines)


def _empty_result(tables: list[str]) -> str:
    scope = ", ".join(tables) if tables else "this database"
    return (f"No stored memory for {scope}. After a query returns "
            "confirmed-correct results, store what the schema dump cannot "
            "tell you with set_table_info.")


# ── promotion path (§5) — post-run only, never exposed as an MCP tool ─────────

def mark_verified(db_id: str, kind: str, subject: str, success: bool = True) -> bool:
    """Promote (or discard) the live entry at a canonical address.

    Called from the post-run path — CP6 outcome grading or explicit user
    confirmation — never by the agent. success=False supersedes the entry
    with a tombstone, preserving history.
    """
    address = canonicalize(kind, subject)
    conn = _connect()
    try:
        with conn:
            if success:
                cur = conn.execute(
                    "UPDATE memory SET verified=1, successes=successes+1,"
                    " updated_at=? WHERE db_id=? AND kind=? AND subject=?"
                    " AND superseded_by IS NULL",
                    (time.time(), db_id, kind, address),
                )
            else:
                cur = conn.execute(
                    "UPDATE memory SET superseded_by=?, updated_at=?"
                    " WHERE db_id=? AND kind=? AND subject=?"
                    " AND superseded_by IS NULL",
                    (DISCARDED, time.time(), db_id, kind, address),
                )
            return cur.rowcount > 0
    finally:
        conn.close()


def mark_run_outcome(source_run_id: str, success: bool) -> int:
    """Promote or discard every unpromoted entry written during a run.

    The CP6 integration point: a run with a known outcome_score gives an
    unambiguous verdict — promote entries from high-scoring runs, discard
    entries from failed ones. Returns the number of rows affected.
    """
    if not source_run_id:
        return 0
    conn = _connect()
    try:
        with conn:
            if success:
                cur = conn.execute(
                    "UPDATE memory SET verified=1, successes=successes+1,"
                    " updated_at=? WHERE source_run_id=? AND verified=0"
                    " AND superseded_by IS NULL",
                    (time.time(), source_run_id),
                )
            else:
                cur = conn.execute(
                    "UPDATE memory SET superseded_by=?, updated_at=?"
                    " WHERE source_run_id=? AND verified=0"
                    " AND superseded_by IS NULL",
                    (DISCARDED, time.time(), source_run_id),
                )
            return cur.rowcount
    finally:
        conn.close()


# ── generation counter (§7 corollary) ────────────────────────────────────────

def generation(db_id: str | None = None) -> float | None:
    """Max updated_at for the scope (None when the store is empty/absent).

    Recorded in benchmark result records: memory content is part of the
    agent's effective configuration, and a generation change between baseline
    and new runs means the two scores were measured with different rulers.
    """
    if not os.path.exists(db_path()):
        return None
    conn = _connect()
    try:
        if db_id:
            row = conn.execute(
                "SELECT MAX(updated_at) FROM memory WHERE db_id=?", (db_id,)
            ).fetchone()
        else:
            row = conn.execute("SELECT MAX(updated_at) FROM memory").fetchone()
        return float(row[0]) if row and row[0] is not None else None
    finally:
        conn.close()
