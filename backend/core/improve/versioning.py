"""
Version snapshots for the Milli Self-Improvement subsystem (Checkpoint 3).

Rollback is JSON, not git (CLAUDE.md §0.3.4). Every apply snapshots the
target's full config to `versions/<object_id>/v<N>.json` FIRST; the new
version is snapshotted after. Snapshot files follow Appendix A:

    {object_id, version_n, parent_version_n, is_active,
     improvement_run_id?, metric_snapshot?, config: <full JSON>}

Immutability contract: a snapshot's `config` is never modified after being
written, and an existing `v<N>.json` is never overwritten (`snapshot_version`
raises). The single exception is the envelope's `is_active` bookkeeping bit,
which `transfer_active` flips when the active version changes — the config
payload is untouched. Files are chmod'd read-only (best effort) between
writes.
"""
import json
import os
import stat
from typing import Any

from core.improve.trace_writer import ensure_user_layout


def versions_dir(user_id: str | None, object_id: str) -> str:
    return os.path.join(ensure_user_layout(user_id), "versions", object_id)


def _version_path(user_id: str | None, object_id: str, version_n: int) -> str:
    return os.path.join(versions_dir(user_id, object_id), f"v{int(version_n)}.json")


def _read(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write(path: str, record: dict) -> None:
    if os.path.exists(path):
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)  # lift read-only for rewrite
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
    try:
        os.chmod(path, stat.S_IREAD)  # best-effort read-only
    except Exception:
        pass


def list_versions(user_id: str | None, object_id: str) -> list[dict]:
    """All snapshot envelopes for an object, sorted by version_n."""
    vdir = versions_dir(user_id, object_id)
    out: list[dict] = []
    if not os.path.isdir(vdir):
        return out
    for name in os.listdir(vdir):
        if name.startswith("v") and name.endswith(".json"):
            rec = _read(os.path.join(vdir, name))
            if rec is not None:
                out.append(rec)
    out.sort(key=lambda r: int(r.get("version_n") or 0))
    return out


def load_version(user_id: str | None, object_id: str, version_n: int) -> dict | None:
    return _read(_version_path(user_id, object_id, version_n))


def next_version_n(user_id: str | None, object_id: str, at_least: int = 1) -> int:
    existing = [int(r.get("version_n") or 0) for r in list_versions(user_id, object_id)]
    return max(existing + [at_least - 1]) + 1


def snapshot_version(
    user_id: str | None,
    object_id: str,
    config: dict,
    *,
    version_n: int,
    parent_version_n: int | None = None,
    is_active: bool = False,
    improvement_run_id: str | None = None,
    metric_snapshot: dict[str, Any] | None = None,
) -> dict:
    """Write `v<N>.json`. Refuses to overwrite an existing snapshot."""
    path = _version_path(user_id, object_id, version_n)
    if os.path.exists(path):
        raise FileExistsError(f"version snapshot already exists: {path}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    record = {
        "object_id": object_id,
        "version_n": int(version_n),
        "parent_version_n": parent_version_n,
        "is_active": bool(is_active),
        "improvement_run_id": improvement_run_id,
        "metric_snapshot": metric_snapshot,
        "config": config,
    }
    _write(path, record)
    return record


def transfer_active(user_id: str | None, object_id: str, active_version_n: int) -> None:
    """Flip the `is_active` envelope bit so exactly one snapshot carries it.

    Config payloads are never touched — only the bookkeeping bit.
    """
    for rec in list_versions(user_id, object_id):
        n = int(rec.get("version_n") or 0)
        should_be = n == int(active_version_n)
        if bool(rec.get("is_active")) != should_be:
            rec["is_active"] = should_be
            _write(_version_path(user_id, object_id, n), rec)
