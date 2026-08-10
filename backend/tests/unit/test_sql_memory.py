"""
SQL Schema Memory verification (spec: ideas/IMPLEMENT_SQL Memory Tool.md).

Covers the exit criteria (§10): six canonical kinds in a local SQLite file
keyed by db_id; writes upsert on a canonical address and supersede rather than
delete; schema fingerprints label STALE entries; entries are unverified until
promoted through a path the agent cannot reach; retrieval is ranked and
hard-budgeted; the store is frozen during benchmark runs; nothing is ever
written to a target database; and with memory unused the three existing SQL
tools are byte-identical to before.
"""
import inspect
import os
import sqlite3
import threading
import types as pytypes

import pytest

from tools import sql_memory as sm


# ── isolation: fresh store, unfrozen env, per test ────────────────────────────

@pytest.fixture(autouse=True)
def _clean_store(monkeypatch):
    monkeypatch.delenv("SYNAPSE_SQL_MEMORY_MODE", raising=False)
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(sm.db_path() + suffix)
        except OSError:
            pass
    try:
        os.remove(sm._freeze_marker_path())
    except OSError:
        pass
    yield


def _live_rows(db_id=None):
    conn = sqlite3.connect(sm.db_path())
    try:
        q = "SELECT * FROM memory WHERE superseded_by IS NULL"
        if db_id:
            q += f" AND db_id='{db_id}'"
        return conn.execute(q).fetchall()
    finally:
        conn.close()


def _all_rows():
    conn = sqlite3.connect(sm.db_path())
    try:
        return conn.execute("SELECT * FROM memory").fetchall()
    finally:
        conn.close()


# ── §3.2 canonical addressing ─────────────────────────────────────────────────

class TestCanonicalAddressing:
    def test_table_note_lowercased_and_dbo_qualified(self):
        assert sm.canonicalize("table_note", "Sales") == "dbo.sales"
        assert sm.canonicalize("table_note", "[DBO].[Sales]") == "dbo.sales"

    def test_table_note_keeps_last_two_segments(self):
        assert sm.canonicalize("table_note", "MyDb.dbo.Sales") == "dbo.sales"

    def test_pitfall_addresses_like_table_note(self):
        assert sm.canonicalize("pitfall", "Sales") == "dbo.sales"

    def test_column_note(self):
        assert sm.canonicalize("column_note", "dbo.Sales.RevAmt") == "dbo.sales.revamt"
        assert sm.canonicalize("column_note", "Sales.RevAmt") == "dbo.sales.revamt"

    def test_column_note_without_dot_is_rejected_with_explanation(self):
        with pytest.raises(sm.AddressError, match="table.column"):
            sm.canonicalize("column_note", "RevAmt")

    def test_join_path_sorted_so_either_argument_order_collides(self):
        a = sm.canonicalize("join_path", "dbo.Sales~dbo.Customers")
        b = sm.canonicalize("join_path", "Customers ~ Sales")
        assert a == b == "dbo.customers~dbo.sales"

    def test_join_path_with_one_table_is_rejected(self):
        with pytest.raises(sm.AddressError, match="exactly two tables"):
            sm.canonicalize("join_path", "dbo.Sales")

    def test_join_path_same_table_twice_is_rejected(self):
        with pytest.raises(sm.AddressError, match="same table twice"):
            sm.canonicalize("join_path", "Sales~dbo.sales")

    def test_convention_slug(self):
        assert sm.canonicalize("convention", "Soft Deletes!") == "soft_deletes"
        assert sm.canonicalize("convention", "") == "__db__"

    def test_query_exemplar_hashes_the_question(self):
        addr = sm.canonicalize("query_exemplar", "Which region had the highest Q3 revenue?")
        assert len(addr) == 16 and int(addr, 16) >= 0
        # case-insensitive: same question, different case, same address
        assert addr == sm.canonicalize("query_exemplar", "WHICH region had the highest q3 revenue?")
        # an already-canonical 16-hex address passes through
        assert sm.canonicalize("query_exemplar", addr) == addr

    def test_unknown_kind_rejected(self):
        with pytest.raises(sm.AddressError, match="unknown kind"):
            sm.canonicalize("free_text", "whatever")

    def test_empty_subject_rejected(self):
        with pytest.raises(sm.AddressError):
            sm.canonicalize("table_note", "   ")


# ── §3.1 / §6.2 writes: upsert + supersede, never delete ─────────────────────

class TestWrites:
    def test_first_write_stores_unverified(self):
        msg = sm.set_entry("db1", "table_note", "Sales", "One row per line item.")
        assert msg == "Stored table_note for 'dbo.sales'."
        rows = _live_rows()
        assert len(rows) == 1
        conn = sqlite3.connect(sm.db_path())
        cols = [d[0] for d in conn.execute("SELECT * FROM memory LIMIT 0").description]
        conn.close()
        row = dict(zip(cols, rows[0]))
        assert row["verified"] == 0 and row["subject"] == "dbo.sales"

    def test_update_supersedes_and_preserves_history(self):
        sm.set_entry("db1", "table_note", "Sales", "v1 fact")
        msg = sm.set_entry("db1", "table_note", "SALES", "v2 corrected fact")
        assert msg == "Updated table_note for 'dbo.sales'."
        assert len(_all_rows()) == 2          # never deleted
        live = _live_rows()
        assert len(live) == 1                  # exactly one live row per address

    def test_update_preserves_created_at(self):
        sm.set_entry("db1", "table_note", "Sales", "v1")
        conn = sqlite3.connect(sm.db_path())
        first_created = conn.execute("SELECT created_at FROM memory").fetchone()[0]
        conn.close()
        sm.set_entry("db1", "table_note", "Sales", "v2")
        conn = sqlite3.connect(sm.db_path())
        live_created = conn.execute(
            "SELECT created_at FROM memory WHERE superseded_by IS NULL"
        ).fetchone()[0]
        conn.close()
        assert live_created == first_created

    def test_content_cap_rejected_with_explanation(self):
        with pytest.raises(ValueError, match="1200"):
            sm.set_entry("db1", "table_note", "Sales", "x" * 1201)

    def test_empty_content_rejected(self):
        with pytest.raises(ValueError, match="content is required"):
            sm.set_entry("db1", "table_note", "Sales", "   ")

    def test_malformed_address_rejected_not_coerced(self):
        with pytest.raises(sm.AddressError):
            sm.set_entry("db1", "column_note", "RevAmt", "cents not dollars")
        assert not os.path.exists(sm.db_path()) or _all_rows() == []

    def test_concurrent_writes_from_worker_threads(self):
        """WAL + busy_timeout + per-call connections under contention (M3)."""
        errors = []

        def worker(n):
            try:
                for i in range(5):
                    sm.set_entry("db1", "table_note", f"t{n}_{i}", f"fact {n}/{i}")
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert len(_live_rows()) == 40


# ── §6.1 retrieval: matching, ranking, budget ─────────────────────────────────

class TestRetrieval:
    def _seed(self):
        sm.set_entry("db1", "table_note", "Sales", "One row per line item.")
        sm.set_entry("db1", "column_note", "Sales.RevAmt", "Cents, -1 = refunded.")
        sm.set_entry("db1", "join_path", "Customers~Sales", "Join on CustomerKey + TenantID.")
        sm.set_entry("db1", "convention", "soft_deletes", "Always filter IsDeleted = 0.")
        sm.set_entry("db1", "pitfall", "Orders", "Cast OrderDate to DATE before grouping.")

    def test_matches_exact_column_and_join_subjects_for_a_table(self):
        self._seed()
        out = sm.get_entries("db1", ["Sales"])
        assert "dbo.sales" in out
        assert "dbo.sales.revamt" in out
        assert "dbo.customers~dbo.sales" in out
        assert "dbo.orders" not in out         # unrelated table not returned

    def test_conventions_returned_unconditionally(self):
        self._seed()
        out = sm.get_entries("db1", ["Orders"])
        assert "soft_deletes" in out and "IsDeleted" in out

    def test_kinds_filter(self):
        self._seed()
        out = sm.get_entries("db1", ["Sales"], kinds=["column_note"])
        assert "revamt" in out and "customers~" not in out

    def test_unknown_kind_filter_rejected(self):
        self._seed()
        with pytest.raises(sm.AddressError, match="unknown kind"):
            sm.get_entries("db1", ["Sales"], kinds=["notes"])

    def test_entries_served_labelled_unverified_until_promoted(self):
        self._seed()
        out = sm.get_entries("db1", ["Sales"])
        assert "unverified" in out and "(verified" not in out

    def test_ranking_verified_first(self):
        sm.set_entry("db1", "table_note", "A", "unverified note")
        sm.set_entry("db1", "table_note", "B", "verified note")
        sm.mark_verified("db1", "table_note", "B")
        body = sm.get_entries("db1", ["A", "B"]).split("\n", 1)[1]
        assert body.index("dbo.b") < body.index("dbo.a")

    def test_budget_drops_and_reports(self):
        for i in range(10):
            sm.set_entry("db1", "table_note", f"t{i}", "y" * 400)
        out = sm.get_entries("db1", [f"t{i}" for i in range(10)], budget=1500)
        assert len(out) < 10 * 400
        assert "dropped" in out and "budget" in out

    def test_uses_incremented_only_for_returned_entries(self):
        self._seed()
        sm.get_entries("db1", ["Sales"])
        conn = sqlite3.connect(sm.db_path())
        uses = dict(conn.execute(
            "SELECT subject, uses FROM memory WHERE superseded_by IS NULL"
        ).fetchall())
        conn.close()
        assert uses["dbo.sales"] == 1
        assert uses["dbo.orders"] == 0         # not returned, not counted

    def test_db_id_isolation(self):
        sm.set_entry("db_a", "table_note", "Sales", "fact about A")
        out = sm.get_entries("db_b", ["Sales"])
        assert "fact about A" not in out
        assert "No stored memory" in out

    def test_empty_store_prompts_toward_set_table_info(self):
        out = sm.get_entries("db1", ["Sales"])
        assert "No stored memory" in out and "set_table_info" in out

    def test_payload_is_returned(self):
        sm.set_entry("db1", "join_path", "a~b", "join note",
                     payload={"on": "a.k = b.k", "cardinality": "many-to-one"})
        out = sm.get_entries("db1", ["a"])
        assert "many-to-one" in out


# ── §3.3 schema fingerprinting / staleness ────────────────────────────────────

class TestStaleness:
    class _FakeEngine:
        """connect() context manager; _table_fp is monkeypatched so no SQL runs."""
        def connect(self):
            class _Ctx:
                def __enter__(self):  # noqa: D105
                    return self
                def __exit__(self, *a):  # noqa: D105
                    return False
            return _Ctx()

    def test_fp_mismatch_is_labelled_stale(self, monkeypatch):
        eng = self._FakeEngine()
        monkeypatch.setattr(sm, "_table_fp", lambda *a: "fp_at_write")
        sm.set_entry("db1", "table_note", "Sales", "grain note", engine=eng, db="D")
        monkeypatch.setattr(sm, "_table_fp", lambda *a: "fp_after_rename")
        out = sm.get_entries("db1", ["Sales"], engine=eng, db="D")
        assert "STALE: schema changed" in out

    def test_fp_match_is_not_stale(self, monkeypatch):
        eng = self._FakeEngine()
        monkeypatch.setattr(sm, "_table_fp", lambda *a: "same_fp")
        sm.set_entry("db1", "table_note", "Sales", "grain note", engine=eng, db="D")
        out = sm.get_entries("db1", ["Sales"], engine=eng, db="D")
        assert "STALE:" not in out

    def test_null_fp_served_without_staleness_claim(self, monkeypatch):
        # fp not computable at write (no engine): no claim either way at read
        sm.set_entry("db1", "table_note", "Sales", "grain note")
        eng = self._FakeEngine()
        monkeypatch.setattr(sm, "_table_fp", lambda *a: "anything")
        out = sm.get_entries("db1", ["Sales"], engine=eng, db="D")
        assert "STALE:" not in out

    def test_unreadable_table_fp_is_none(self, monkeypatch):
        eng = self._FakeEngine()
        monkeypatch.setattr(sm, "_table_fp", lambda *a: None)  # dropped/denied
        assert sm.compute_schema_fp(eng, "D", ["dbo.gone"]) is None

    def test_join_path_fp_covers_both_sides(self, monkeypatch):
        eng = self._FakeEngine()
        fps = {"dbo.a": "fp_a", "dbo.b": "fp_b"}
        monkeypatch.setattr(
            sm, "_table_fp",
            lambda conn, db, s, t: fps[f"{s}.{t}"])
        both = sm.compute_schema_fp(eng, "D", ["dbo.a", "dbo.b"])
        assert both is not None and both not in ("fp_a", "fp_b")


# ── §5 promotion — out-of-band, not agent-reachable ──────────────────────────

class TestPromotion:
    def test_mark_verified_promotes(self):
        sm.set_entry("db1", "column_note", "Sales.RevAmt", "cents")
        assert sm.mark_verified("db1", "column_note", "Sales.RevAmt") is True
        out = sm.get_entries("db1", ["Sales"])
        assert "(verified)" in out

    def test_mark_verified_failure_discards(self):
        sm.set_entry("db1", "table_note", "Sales", "wrong assumption")
        assert sm.mark_verified("db1", "table_note", "Sales", success=False) is True
        out = sm.get_entries("db1", ["Sales"])
        assert "wrong assumption" not in out
        assert len(_all_rows()) == 1           # history preserved, not deleted

    def test_mark_verified_missing_entry_returns_false(self):
        assert sm.mark_verified("db1", "table_note", "Ghost") is False

    def test_mark_run_outcome_promotes_by_source_run(self):
        sm.set_entry("db1", "table_note", "A", "from good run", source_run_id="run_good")
        sm.set_entry("db1", "table_note", "B", "from bad run", source_run_id="run_bad")
        assert sm.mark_run_outcome("run_good", success=True) == 1
        assert sm.mark_run_outcome("run_bad", success=False) == 1
        out = sm.get_entries("db1", ["A", "B"])
        assert "from good run" in out and "(verified)" in out
        assert "from bad run" not in out

    def test_promotion_is_not_an_mcp_tool(self):
        """An agent that can promote its own writes has no gate at all."""
        import asyncio
        import tools.sql_agent as sql_agent
        tools = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            sql_agent.list_tools())
        names = {t.name for t in tools}
        assert "mark_verified" not in names and "mark_run_outcome" not in names


# ── §7 frozen mode + generation counter ───────────────────────────────────────

class TestFrozenMode:
    def test_env_var_freezes_writes_reads_still_work(self, monkeypatch):
        sm.set_entry("db1", "table_note", "Sales", "pre-freeze fact")
        monkeypatch.setenv("SYNAPSE_SQL_MEMORY_MODE", "frozen")
        msg = sm.set_entry("db1", "table_note", "Sales", "must not land")
        assert "frozen" in msg
        out = sm.get_entries("db1", ["Sales"])
        assert "pre-freeze fact" in out and "must not land" not in out

    def test_marker_file_freezes_across_process_boundary(self):
        with open(sm._freeze_marker_path(), "w") as f:
            f.write("bench_x")
        try:
            assert sm.frozen() is True
            msg = sm.set_entry("db1", "table_note", "Sales", "nope")
            assert "frozen" in msg
        finally:
            os.remove(sm._freeze_marker_path())
        assert sm.frozen() is False

    def test_freeze_writes_context_sets_and_restores_both_mechanisms(self):
        assert sm.frozen() is False
        with sm.freeze_writes("bench_123"):
            assert sm.frozen() is True
            assert os.path.exists(sm._freeze_marker_path())
            assert os.environ.get("SYNAPSE_SQL_MEMORY_MODE") == "frozen"
        assert sm.frozen() is False
        assert not os.path.exists(sm._freeze_marker_path())

    def test_generation_none_when_empty_and_advances_on_write(self):
        assert sm.generation() is None
        sm.set_entry("db1", "table_note", "Sales", "fact")
        g1 = sm.generation("db1")
        assert isinstance(g1, float)
        sm.set_entry("db1", "table_note", "Sales", "updated fact")
        assert sm.generation("db1") >= g1
        assert sm.generation("other_db") is None   # scoped


# ── §6 tool surface on the existing sql-mcp-server ───────────────────────────

@pytest.fixture
def fake_sql_engine(monkeypatch):
    import tools.sql_agent as sql_agent

    def fake_get_db_engine(db_id=None):
        return pytypes.SimpleNamespace(), "TESTDB"

    monkeypatch.setattr(sql_agent, "get_db_engine", fake_get_db_engine)
    monkeypatch.setattr(sm, "_table_fp", lambda *a: None)
    return sql_agent


class TestToolSurface:
    @pytest.mark.asyncio
    async def test_set_then_get_through_call_tool(self, fake_sql_engine):
        sa = fake_sql_engine
        res = await sa.call_tool("set_table_info", {
            "db_id": "db1", "kind": "column_note",
            "subject": "dbo.Sales.RevAmt",
            "content": "Stored in cents; -1 means refunded.",
        })
        assert res[0].text == "Stored column_note for 'dbo.sales.revamt'."
        res = await sa.call_tool("get_table_info", {
            "db_id": "db1", "table_names": ["Sales"],
        })
        assert "cents" in res[0].text and "unverified" in res[0].text

    @pytest.mark.asyncio
    async def test_malformed_address_returns_explanatory_error(self, fake_sql_engine):
        res = await fake_sql_engine.call_tool("set_table_info", {
            "db_id": "db1", "kind": "join_path", "subject": "dbo.Sales",
            "content": "join note",
        })
        assert res[0].text.startswith("Error:")
        assert "exactly two tables" in res[0].text

    @pytest.mark.asyncio
    async def test_list_tools_appends_memory_tools_after_existing_three(self):
        import tools.sql_agent as sql_agent
        tools = await sql_agent.list_tools()
        names = [t.name for t in tools]
        assert names[:3] == ["list_tables", "get_table_schema", "run_sql_query"]
        assert names[3:] == ["get_table_info", "set_table_info"]

    def test_resolve_db_id(self):
        assert sm.resolve_db_id("db7", []) == "db7"
        assert sm.resolve_db_id(None, [{"id": "only"}]) == "only"
        assert sm.resolve_db_id(None, [{"id": "a"}, {"id": "b"}]) == sm.GLOBAL_DB_ID
        assert sm.resolve_db_id(None, []) == sm.GLOBAL_DB_ID


# ── §0 back-compat: existing tools byte-identical with memory unused ──────────

class TestBackCompat:
    def test_existing_helpers_never_touch_memory(self):
        """The three pre-existing DB helpers must not reference the memory
        subsystem at all — an agent that never calls the memory tools behaves
        byte-identically to before."""
        import tools.sql_agent as sql_agent
        for helper in ("_run_list_tables", "_run_get_table_schema",
                       "_run_sql_query", "get_db_engine", "_load_db_configs",
                       "_extract_database_name", "_is_write_query"):
            src = inspect.getsource(getattr(sql_agent, helper))
            assert "sql_memory" not in src, f"{helper} must not touch memory"

    def test_get_table_schema_has_no_memory_suffix(self):
        """§6.3 auto-attach is default OFF — not implemented."""
        import tools.sql_agent as sql_agent
        branch = inspect.getsource(sql_agent.call_tool)
        schema_branch = branch.split('elif name == "get_table_schema"')[1] \
                              .split("elif")[0]
        assert "sql_memory" not in schema_branch

    def test_memory_never_writes_to_target_database(self):
        """M22: the module reads targets only for fingerprinting — no DDL/DML,
        no commit, ever."""
        src = inspect.getsource(sm)
        fp_src = inspect.getsource(sm._table_fp)
        assert "SELECT" in fp_src
        for verb in ("INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER"):
            assert verb not in fp_src
        # the only writes in the module go to the sqlite store via _connect()
        assert "engine.execute" not in src
        assert "conn.commit" not in fp_src


# ── §7 benchmark integration: frozen during run_benchmark ────────────────────

class TestBenchmarkFreeze:
    @pytest.mark.asyncio
    async def test_run_benchmark_freezes_memory_for_every_input(self, fake_llm, monkeypatch):
        import shutil
        from core.config import DATA_DIR
        from core.improve import benchmark as bm
        from _fakes import seed

        shutil.rmtree(os.path.join(DATA_DIR, "improve"), ignore_errors=True)
        agent = seed.make_agent(id="agent_1", tools=[], skip_default_tools=True)
        seed.seed_agents([agent])
        bm.save_benchmark("default", {
            "id": "bench_mem", "name": "freeze check",
            "target_object_id": "agent_1",
            "inputs": [{"prompt": "one"}, {"prompt": "two"}],
            "scorer": {"metrics": {"success": 1.0}},
        })

        frozen_during: list[bool] = []
        real_exec = bm._execute_agent_input

        async def spy_exec(*args, **kwargs):
            frozen_during.append(sm.frozen())
            return await real_exec(*args, **kwargs)

        monkeypatch.setattr(bm, "_execute_agent_input", spy_exec)
        server = pytypes.SimpleNamespace(
            agent_sessions={}, memory_store=None, tool_router={})
        await bm.run_benchmark("default", "bench_mem", server_module=server)

        assert frozen_during == [True, True]   # every input ran frozen
        assert sm.frozen() is False            # and the freeze was released
        assert not os.path.exists(sm._freeze_marker_path())


# ── §7 corollary: generation gates ratchet comparability ─────────────────────

class TestGenerationComparability:
    def test_generation_mismatch_is_incomparable(self):
        from core.improve.steps import comparability_reason, grading_detail
        base = grading_detail({"sql_memory_generation": 100.5})
        new = grading_detail({"sql_memory_generation": 200.75})
        reason = comparability_reason(base, new)
        assert reason is not None and "memory" in reason

    def test_equal_or_absent_generation_is_comparable(self):
        from core.improve.steps import comparability_reason, grading_detail
        same = grading_detail({"sql_memory_generation": 100.5})
        assert comparability_reason(same, dict(same)) is None
        assert comparability_reason(grading_detail({}), grading_detail({})) is None
