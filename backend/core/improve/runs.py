"""
ImprovementRun index for the Milli Self-Improvement subsystem (Checkpoint 3).

`runs.json` (per-user, see SCHEMA.md §2) holds one record per improvement run:

    {run_id, target_object_id, target_kind: agent|orchestration,
     baseline_version_n, new_version_n?, mode: human|autonomous,
     tuner_model, insights_ref, proposed_diff_ref, benchmark_id?,
     baseline_score?, new_score?, decision: keep|revert|pending,
     iteration?, budget_spent?, created_at, closed_at?}

The proposal payload (insights + proposed diff) is written to
`proposals/<run_id>.json` and referenced from the run record, keeping
`runs.json` a small index.

Concurrency rule (§0.6 / risk register): at most one open run — decision
"pending" and no `closed_at` — may exist per `target_object_id`. Attempting
to open a second raises `RunConflict`, which the route layer maps to 409.
"""
import json
import os
import uuid
from datetime import datetime, timezone

from core.improve.trace_writer import ensure_user_layout, user_improve_dir


class RunConflict(Exception):
    """An open ImprovementRun already exists for the target object."""


class RunNotFound(Exception):
    """No ImprovementRun with the given run_id."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _runs_path(user_id: str | None) -> str:
    return os.path.join(ensure_user_layout(user_id), "runs.json")


def load_runs(user_id: str | None = None) -> list[dict]:
    try:
        with open(_runs_path(user_id), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_runs(runs: list[dict], user_id: str | None = None) -> None:
    path = _runs_path(user_id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(runs, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def find_open_run(user_id: str | None, target_object_id: str) -> dict | None:
    """The in-progress run for an object, if any (decision pending, not closed)."""
    for r in load_runs(user_id):
        if (
            r.get("target_object_id") == target_object_id
            and r.get("decision") == "pending"
            and not r.get("closed_at")
        ):
            return r
    return None


def get_run(user_id: str | None, run_id: str) -> dict:
    for r in load_runs(user_id):
        if r.get("run_id") == run_id:
            return r
    raise RunNotFound(run_id)


def update_run(user_id: str | None, run_id: str, **fields) -> dict:
    runs = load_runs(user_id)
    for r in runs:
        if r.get("run_id") == run_id:
            r.update(fields)
            save_runs(runs, user_id)
            return r
    raise RunNotFound(run_id)


def create_run(
    user_id: str | None,
    target_object_id: str,
    target_kind: str,
    baseline_version_n: int,
    tuner_model: str,
    mode: str = "human",
) -> dict:
    """Open a new pending run. Raises RunConflict if one is already open."""
    if find_open_run(user_id, target_object_id):
        raise RunConflict(target_object_id)
    run = {
        "run_id": f"imp_{uuid.uuid4().hex[:12]}",
        "target_object_id": target_object_id,
        "target_kind": target_kind,
        "baseline_version_n": baseline_version_n,
        "new_version_n": None,
        "mode": mode,
        "tuner_model": tuner_model,
        "insights_ref": None,
        "proposed_diff_ref": None,
        "benchmark_id": None,
        "baseline_score": None,
        "new_score": None,
        "decision": "pending",
        "created_at": _now_iso(),
        "closed_at": None,
    }
    runs = load_runs(user_id)
    runs.append(run)
    save_runs(runs, user_id)
    return run


# ── proposal payload (insights + proposed diff) ──────────────────────────────

def write_proposal(user_id: str | None, run_id: str, payload: dict) -> str:
    """Persist the proposal payload; returns its path relative to the user dir."""
    base = user_improve_dir(user_id)
    proposals_dir = os.path.join(base, "proposals")
    os.makedirs(proposals_dir, exist_ok=True)
    rel = f"proposals/{run_id}.json"
    with open(os.path.join(base, rel), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return rel


def load_proposal(user_id: str | None, run_id: str) -> dict:
    path = os.path.join(user_improve_dir(user_id), "proposals", f"{run_id}.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)
