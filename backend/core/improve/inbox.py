"""
Self-Improvement Inbox (Checkpoint 5) — the notification + audit log backing
store at `backend/data/improve/<user_id>/inbox.json` (Appendix A):

    {event_id, timestamp, run_id, object_id, version_n,
     kind: apply|revert|budget_abort|plateau_stop|timeout_stop|max_iterations_stop,
     mode: autonomous|human, score_delta?, message}

Design decision §0.6.2: autonomous applies/reverts are never silent — every
one lands here. Entries are append-only; the UI reads newest-first.
"""
import json
import os
import uuid
from datetime import datetime, timezone

from core.improve.trace_writer import ensure_user_layout

INBOX_KINDS = {
    "apply",
    "revert",
    "budget_abort",
    "plateau_stop",
    "timeout_stop",
    "max_iterations_stop",
    # Checkpoint 6 (Appendix A6) — outcome-grading integrity events.
    "grading_mismatch",    # scores measured with different rulers; not compared
    "grading_unreliable",  # extraction_failed_rate > 0.5; score not trusted
}


def _inbox_path(user_id: str | None) -> str:
    return os.path.join(ensure_user_layout(user_id), "inbox.json")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def load_inbox(user_id: str | None = None) -> list[dict]:
    try:
        with open(_inbox_path(user_id), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(entries: list[dict], user_id: str | None) -> None:
    path = _inbox_path(user_id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def add_entry(
    user_id: str | None,
    *,
    kind: str,
    mode: str,
    message: str,
    run_id: str | None = None,
    object_id: str | None = None,
    version_n: int | None = None,
    score_delta: float | None = None,
) -> dict:
    """Append one audit/notification entry. Never raises into the caller's
    control flow beyond validation — an unwritable inbox must not kill a run,
    so callers wrap this in _notify_inbox-style best-effort helpers."""
    if kind not in INBOX_KINDS:
        raise ValueError(f"unknown inbox kind '{kind}'")
    entry = {
        "event_id": f"inb_{uuid.uuid4().hex[:12]}",
        "timestamp": _now_iso(),
        "run_id": run_id,
        "object_id": object_id,
        "version_n": version_n,
        "kind": kind,
        "mode": mode,
        "score_delta": score_delta,
        "message": message,
    }
    entries = load_inbox(user_id)
    entries.append(entry)
    _save(entries, user_id)
    return entry


def list_entries(
    user_id: str | None = None,
    *,
    object_id: str | None = None,
    kind: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Newest-first entries, optionally filtered."""
    entries = load_inbox(user_id)
    if object_id:
        entries = [e for e in entries if e.get("object_id") == object_id]
    if kind:
        entries = [e for e in entries if e.get("kind") == kind]
    return list(reversed(entries))[:limit]
