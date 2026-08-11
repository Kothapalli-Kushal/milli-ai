"""
Trace writer for the Synapse Self-Improvement subsystem (Checkpoint 1).

Consumes the existing `run_agent_step` / `OrchestrationEngine.run` event
streams and writes one schema-valid trace JSON per run on close. See
SCHEMA.md (same directory) for the schema, storage layout, ACL map,
retention, and success-derivation contract.

Design constraints (CLAUDE.md §0.3 / §0.4):
- Additive and failure-isolated: nothing here may ever raise into the agent
  or orchestration path, mutate events, or change logger output.
- Tokens/cost are JOINED from usage_tracker records — never re-counted.
- No monkey-patching: hooks are an explicit decorator on run_agent_step and
  an explicit try/finally in OrchestrationEngine.run.
"""
import contextvars
import functools
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone

from core.config import DATA_DIR, load_settings

# ──────────────────────────────────────────────────────────────────────────────
# Constants & storage layout
# ──────────────────────────────────────────────────────────────────────────────

IMPROVE_DIR = os.path.join(DATA_DIR, "improve")
DEFAULT_TRACE_RETENTION_DAYS = 30

# Explicit give-up phrasing in the last assistant message fails the run.
# Shared with the Checkpoint-2 `give_up` detector.
GIVE_UP_RE = re.compile(
    r"(?i)\b("
    r"i\s+(?:can(?:no|')t|cannot|am\s+unable|'m\s+unable)"
    r"|unable\s+to\s+(?:help|complete|comply|proceed|assist|do\s+th)"
    r"|i\s+give\s+up"
    r"|no\s+tools?\s+(?:are\s+)?available"
    r"|as\s+an\s+ai(?:\s+language)?\s+model"
    r")\b"
)

# Task-local trace context. Nested run_agent_step calls (delegate_to_agent,
# spawn_subtask) see their parent's writer here; agent runs inside an
# orchestration step see the orchestration writer.
_current_agent_trace: contextvars.ContextVar = contextvars.ContextVar(
    "synapse_improve_agent_trace", default=None
)
_current_orch_trace: contextvars.ContextVar = contextvars.ContextVar(
    "synapse_improve_orch_trace", default=None
)


def resolve_user_id() -> str:
    """Improvement storage namespace (§0.6.5): login username or 'default'."""
    try:
        username = (load_settings().get("login_username") or "").strip()
        return username or "default"
    except Exception:
        return "default"


def user_improve_dir(user_id: str | None = None) -> str:
    return os.path.join(IMPROVE_DIR, user_id or resolve_user_id())


def ensure_user_layout(user_id: str | None = None) -> str:
    """Create the per-user storage layout documented in SCHEMA.md §2."""
    base = user_improve_dir(user_id)
    for sub in ("traces", "benchmarks", "versions"):
        os.makedirs(os.path.join(base, sub), exist_ok=True)
    for index_file, empty in (("runs.json", []), ("inbox.json", [])):
        path = os.path.join(base, index_file)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                json.dump(empty, f)
    return base


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ──────────────────────────────────────────────────────────────────────────────
# Success derivation & usage join (SCHEMA.md §4 / §5)
# ──────────────────────────────────────────────────────────────────────────────

def derive_success(
    final_fired: bool,
    error: str | None,
    had_error_event: bool,
    messages: list[dict],
) -> bool:
    """success = final fired ∧ no error event ∧ last assistant msg not give-up."""
    if not final_fired or error or had_error_event:
        return False
    last_assistant = next(
        (m for m in reversed(messages)
         if m.get("role") == "assistant" and m.get("content")),
        None,
    )
    if last_assistant and GIVE_UP_RE.search(last_assistant["content"]):
        return False
    return True


def _join_usage(
    run_id: str | None,
    session_id: str | None,
    agent_id: str | None,
    since_iso: str,
) -> tuple[dict, str | None]:
    """Aggregate matching usage_tracker records. Read-only; never re-counts.

    Returns ({input_tokens, output_tokens, total_tokens, estimated_cost_usd,
    llm_calls}, resolved_model).
    """
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "estimated_cost_usd": 0.0,
        "llm_calls": 0,
    }
    model: str | None = None
    try:
        from core.usage_tracker import get_usage_logs
        if run_id:
            records = get_usage_logs(limit=1_000_000, run_id=run_id)
            if agent_id:  # per-step agent trace inside an orchestration run
                records = [r for r in records if r.get("agent_id") == agent_id]
        elif session_id:
            records = get_usage_logs(limit=1_000_000, session_id=session_id)
            if agent_id:
                records = [r for r in records if r.get("agent_id") == agent_id]
            # 2s clock-skew allowance; ISO-8601 UTC strings compare lexically
            cutoff = since_iso[:19]
            records = [r for r in records if (r.get("timestamp") or "")[:19] >= cutoff]
        else:
            records = []

        model_counts: dict[str, int] = {}
        for r in records:
            if r.get("event_type") == "compaction":
                continue  # observability record, zero tokens/cost
            usage["input_tokens"] += int(r.get("input_tokens") or 0)
            usage["output_tokens"] += int(r.get("output_tokens") or 0)
            usage["total_tokens"] += int(r.get("total_tokens") or 0)
            usage["estimated_cost_usd"] += float(r.get("estimated_cost") or 0.0)
            usage["llm_calls"] += 1
            m = r.get("model")
            if m:
                model_counts[m] = model_counts.get(m, 0) + 1
        usage["estimated_cost_usd"] = round(usage["estimated_cost_usd"], 8)
        if model_counts:
            model = max(model_counts, key=model_counts.get)
    except Exception:
        pass
    return usage, model


# ──────────────────────────────────────────────────────────────────────────────
# Retention (SCHEMA.md §6)
# ──────────────────────────────────────────────────────────────────────────────

def _agent_retention_days(agent_id: str | None) -> int:
    """Per-agent trace_retention_days override, else the 30-day default."""
    if agent_id:
        try:
            from core.routes.agents import load_user_agents
            for a in load_user_agents():
                if a.get("id") == agent_id:
                    override = a.get("trace_retention_days")
                    if override:
                        return int(override)
                    break
        except Exception:
            pass
    return DEFAULT_TRACE_RETENTION_DAYS


def purge_expired_traces(object_trace_dir: str, retention_days: int) -> int:
    """Best-effort purge of trace files older than the retention window.

    Deletes expired files under the object's `<YYYY-MM>/` dirs, removes empty
    month dirs. Returns the number of files removed. Never raises.
    """
    removed = 0
    try:
        cutoff = time.time() - retention_days * 86400
        if not os.path.isdir(object_trace_dir):
            return 0
        for month in os.listdir(object_trace_dir):
            month_dir = os.path.join(object_trace_dir, month)
            if not os.path.isdir(month_dir):
                continue
            for name in os.listdir(month_dir):
                path = os.path.join(month_dir, name)
                try:
                    if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                        os.remove(path)
                        removed += 1
                except OSError:
                    pass
            try:
                if not os.listdir(month_dir):
                    os.rmdir(month_dir)
            except OSError:
                pass
    except Exception:
        pass
    return removed


# ──────────────────────────────────────────────────────────────────────────────
# TraceWriter
# ──────────────────────────────────────────────────────────────────────────────

class TraceWriter:
    """Builds one trace dict from a run's event stream; writes JSON on close.

    Usable as an async context manager (``async with TraceWriter(...)``) or
    imperatively via ``record_event()`` + ``close()``. All entry points are
    failure-isolated: recording and writing never raise.
    """

    def __init__(
        self,
        *,
        kind: str,                       # "agent" | "orchestration"
        object_id: str,
        session_id: str | None = None,
        run_id: str | None = None,
        source: str = "chat",
        user_message: str = "",
        orchestration_id: str | None = None,
        step_id: str | None = None,
        delegated_from: str | None = None,
        parent_session_id: str | None = None,
        retention_days: int | None = None,
        user_id: str | None = None,
    ):
        self.kind = kind
        self.object_id = object_id or "unknown"
        self.session_id = session_id or run_id or f"trace-{uuid.uuid4().hex[:12]}"
        self.run_id = run_id
        self.source = source
        self.orchestration_id = orchestration_id
        self.step_id = step_id
        self.delegated_from = delegated_from
        self.parent_session_id = parent_session_id
        self.retention_days = retention_days or DEFAULT_TRACE_RETENTION_DAYS
        self.user_id = user_id or resolve_user_id()

        self.messages: list[dict] = []
        if user_message:
            self.messages.append(
                {"role": "user", "content": str(user_message), "timestamp": _now_iso()}
            )
        self.error: str | None = None
        self.output: str = ""
        self.final_fired = False
        self._had_error_event = False
        self._orch_status: str | None = None
        self.current_step_id: str | None = step_id
        self._call_seq = 0
        self._last_call_id_by_name: dict[str, str] = {}
        self._t0 = time.monotonic()
        self._started_iso = _now_iso()
        self._closed = False
        self._ctx_token = None  # contextvar reset token, set by hook helpers

    # ── async context manager ────────────────────────────────────────────────

    async def __aenter__(self) -> "TraceWriter":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if exc is not None and self.error is None and exc_type is not GeneratorExit:
            self.error = f"{exc_type.__name__}: {exc}"
        self.close()
        return False

    # ── event recording ──────────────────────────────────────────────────────

    def record_event(self, event) -> None:
        """Record one run event. Never raises, never mutates the event."""
        try:
            if isinstance(event, dict):
                self._record(event)
        except Exception:
            pass

    def _record(self, event: dict) -> None:
        etype = event.get("type")
        ts = _now_iso()

        if etype == "llm_thought":
            self.messages.append(
                {"role": "assistant", "content": str(event.get("thought", "")), "timestamp": ts}
            )
        elif etype == "tool_execution":
            self._call_seq += 1
            call_id = f"call_{self._call_seq}"
            name = str(event.get("tool_name", ""))
            self._last_call_id_by_name[name] = call_id
            try:
                arguments = json.dumps(event.get("args") or {}, ensure_ascii=False, default=str)
            except Exception:
                arguments = "{}"
            self.messages.append(
                {
                    "role": "assistant",
                    "content": "",
                    "timestamp": ts,
                    "tool_calls": [
                        {"id": call_id, "function": {"name": name, "arguments": arguments}}
                    ],
                }
            )
        elif etype in ("tool_result", "tool_cache_hit"):
            name = str(event.get("tool_name", ""))
            self.messages.append(
                {
                    "role": "tool",
                    "content": str(event.get("preview", "")),
                    "timestamp": ts,
                    "tool_call_id": self._last_call_id_by_name.get(name, ""),
                }
            )
        elif etype == "final":
            self.final_fired = True
            self.output = str(event.get("response", ""))
            self.messages.append(
                {"role": "assistant", "content": self.output, "timestamp": ts}
            )
        elif etype == "error":
            self._had_error_event = True
            if self.error is None:
                self.error = str(event.get("message", "error"))
        elif etype == "orchestration_error":
            self._had_error_event = True
            if self.error is None:
                self.error = str(event.get("error", "orchestration error"))
        elif etype == "step_error":
            self._had_error_event = True
            self.messages.append(
                {
                    "role": "system",
                    "content": f"step_error {event.get('orch_step_id', '')}: {event.get('error', '')}",
                    "timestamp": ts,
                }
            )
        elif etype == "step_start":
            self.current_step_id = event.get("orch_step_id") or event.get("step_id")
            self.messages.append(
                {
                    "role": "system",
                    "content": f"step_start {self.current_step_id} {event.get('step_name', '')}".rstrip(),
                    "timestamp": ts,
                }
            )
        elif etype == "step_complete":
            self.messages.append(
                {
                    "role": "system",
                    "content": (
                        f"step_complete {event.get('orch_step_id', '')} "
                        f"({event.get('duration_seconds', '?')}s)"
                    ),
                    "timestamp": ts,
                }
            )
        elif etype == "orchestration_complete":
            self._orch_status = event.get("status")
        # thinking / status / heartbeat / everything else: no transcript value

    def record_exception(self, exc: BaseException) -> None:
        if self.error is None and not isinstance(exc, GeneratorExit):
            self.error = f"{type(exc).__name__}: {exc}"
            self._had_error_event = True

    # ── finalize & write ─────────────────────────────────────────────────────

    def build_trace(self) -> dict:
        usage, model = _join_usage(
            self.run_id,
            self.session_id,
            self.object_id if self.kind == "agent" else None,
            self._started_iso,
        )
        error = self.error
        if self.kind == "orchestration" and error is None:
            if self._orch_status not in (None, "completed"):
                error = f"orchestration status: {self._orch_status}"
        success = derive_success(self.final_fired, error, self._had_error_event, self.messages)
        if self.kind == "orchestration" and self._orch_status not in (None, "completed"):
            success = False

        metadata = {"kind": self.kind, "source": self.source}
        if self.delegated_from:
            metadata["delegated_from"] = self.delegated_from
        if self.parent_session_id:
            metadata["parent_session_id"] = self.parent_session_id
        if self.step_id:
            metadata["step_id"] = self.step_id

        return {
            "session_id": self.session_id,
            "timestamp": self._started_iso,
            "duration_s": round(time.monotonic() - self._t0, 3),
            "success": success,
            "error": error,
            "output": self.output,
            "agent_id": self.object_id if self.kind == "agent" else None,
            "orchestration_id": (
                self.object_id if self.kind == "orchestration" else self.orchestration_id
            ),
            "run_id": self.run_id,
            "model": model,
            "git_branch": None,
            "git_commit": None,
            "metadata": metadata,
            "messages": self.messages,
            "usage": usage,
        }

    def _trace_path(self) -> str:
        base = ensure_user_layout(self.user_id)
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        safe_object = re.sub(r"[^A-Za-z0-9._-]", "_", self.object_id)[:120] or "unknown"
        safe_session = re.sub(r"[^A-Za-z0-9._-]", "_", self.session_id)[:120] or "session"
        month_dir = os.path.join(base, "traces", safe_object, month)
        os.makedirs(month_dir, exist_ok=True)
        path = os.path.join(month_dir, f"{safe_session}.json")
        n = 2  # one file per run; suffix on chat sessions with multiple runs
        while os.path.exists(path):
            path = os.path.join(month_dir, f"{safe_session}__{n}.json")
            n += 1
        return path

    def close(self) -> None:
        """Write the trace file. Idempotent, best-effort, never raises."""
        if self._closed:
            return
        self._closed = True
        try:
            trace = self.build_trace()
            path = self._trace_path()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(trace, f, indent=2, ensure_ascii=False)
            purge_expired_traces(os.path.dirname(os.path.dirname(path)), self.retention_days)
        except Exception as e:
            print(f"DEBUG improve.trace_writer: trace write failed (non-fatal): {e}", flush=True)


class _NullTrace:
    """No-op stand-in so hook sites never need None checks."""

    current_step_id = None
    object_id = None
    session_id = None
    _ctx_token = None

    def record_event(self, event) -> None:
        pass

    def record_exception(self, exc) -> None:
        pass

    def close(self) -> None:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Hook: run_agent_step (decorator — checklist 1.8)
# ──────────────────────────────────────────────────────────────────────────────

_AGENT_STEP_POSITIONAL = ("message", "agent_id", "session_id", "server_module")


def trace_agent_run(fn):
    """Checkpoint-1 hook for `run_agent_step`.

    Tees the event stream into a TraceWriter; yields every event unmodified.
    Nested calls (delegate_to_agent / spawn_subtask) inherit the parent trace
    via a contextvar and record `delegated_from` / `parent_session_id`; runs
    inside an orchestration step inherit `orchestration_id` / `step_id`.
    """

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        writer = None
        token = None
        try:
            params = dict(zip(_AGENT_STEP_POSITIONAL, args))
            params.update(kwargs)
            parent = _current_agent_trace.get()
            orch = _current_orch_trace.get()
            agent_override = params.get("agent_override") or {}
            object_id = agent_override.get("id") or params.get("agent_id") or "unknown"
            retention = (
                int(agent_override["trace_retention_days"])
                if agent_override.get("trace_retention_days")
                else _agent_retention_days(params.get("agent_id"))
            )
            writer = TraceWriter(
                kind="agent",
                object_id=object_id,
                session_id=params.get("session_id"),
                run_id=params.get("run_id"),
                source=params.get("source", "chat"),
                user_message=str(params.get("message", "")),
                orchestration_id=getattr(orch, "object_id", None),
                step_id=getattr(orch, "current_step_id", None),
                delegated_from=getattr(parent, "object_id", None),
                parent_session_id=getattr(parent, "session_id", None),
                retention_days=retention,
            )
            token = _current_agent_trace.set(writer)
        except Exception:
            writer = None

        try:
            async for event in fn(*args, **kwargs):
                if writer is not None:
                    writer.record_event(event)
                yield event
        except BaseException as exc:
            if writer is not None:
                writer.record_exception(exc)
            raise
        finally:
            if token is not None:
                try:
                    _current_agent_trace.reset(token)
                except Exception:
                    pass
            if writer is not None:
                writer.close()

    return wrapper


# ──────────────────────────────────────────────────────────────────────────────
# Hook: OrchestrationEngine.run (try/finally helpers — checklist 1.9)
# ──────────────────────────────────────────────────────────────────────────────

def begin_orchestration_trace(
    *,
    orchestration_id: str,
    run_id: str,
    session_id: str | None,
    user_input: str,
):
    """Create the orchestration TraceWriter and publish it to the task context.

    Returns a _NullTrace on any failure so the engine hook stays a plain
    try/finally with no conditionals.
    """
    try:
        writer = TraceWriter(
            kind="orchestration",
            object_id=orchestration_id,
            session_id=session_id,
            run_id=run_id,
            source="orchestration",
            user_message=user_input,
        )
        writer._ctx_token = _current_orch_trace.set(writer)
        return writer
    except Exception:
        return _NullTrace()


def end_orchestration_trace(writer) -> None:
    """Finalize the orchestration trace. Never raises."""
    try:
        if getattr(writer, "_ctx_token", None) is not None:
            try:
                _current_orch_trace.reset(writer._ctx_token)
            except Exception:
                pass
        writer.close()
    except Exception:
        pass
