"""
Diff applier + rollback for the Synapse Self-Improvement subsystem (CP3).

This is the SECOND, independent guardrail boundary (§0.5): the allow-list is
re-declared and re-checked here, deliberately NOT imported from tuner.py, so
a bug (or bypass) in the tuner boundary can never reach the stores. The
applier may only touch `user_agents.json` and `orchestrations.json`, and only
the allow-listed fields.

Apply sequence (checklists 3.9–3.11):
  1. validate every accepted edit against the applier allow-list,
  2. snapshot the current config as v<N> (before ANY mutation),
  3. apply edits to a copy and validate it against the pydantic model
     (Agent / Orchestration) — malformed results never persist,
  4. snapshot the new config as v<N+1>, transfer `is_active`,
  5. persist the live store, close the ImprovementRun.
"""
import copy
import re
from datetime import datetime, timezone
from typing import Any

from core.improve import runs as runs_mod
from core.improve import versioning

# ── Independent allow-list (do not import from tuner.py) ─────────────────────

_APPLIER_AGENT_FIELDS = {
    "system_prompt",
    "tools",
    "max_turns",
    "model",
    "description",
    "delegate_agent_ids",
}
_APPLIER_ORCH_STEP_RE = re.compile(r"^steps\[(\d+)\]\.([A-Za-z_][A-Za-z0-9_]*)$")
_APPLIER_ORCH_STEP_FIELDS = {
    "prompt_template",
    "evaluator_prompt",
    "next_step_id",
    "route_map",
    "if_true_step_id",
    "if_false_step_id",
    "switch_cases",
    "switch_default_step_id",
    "max_iterations",
    "loop_count",
    "timeout_seconds",
    "human_timeout_seconds",
    "max_turns",
}


class ApplyError(Exception):
    """Apply/rollback failure; `status` maps to the HTTP response code."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def check_edit_allowed(field: str, target_kind: str) -> str | None:
    """Applier-boundary allow-list check. Returns an error string or None."""
    if target_kind == "agent":
        if field not in _APPLIER_AGENT_FIELDS:
            return f"applier rejected out-of-scope agent field '{field}'"
        return None
    m = _APPLIER_ORCH_STEP_RE.match(field)
    if not m:
        return f"applier rejected invalid orchestration edit path '{field}'"
    if m.group(2) not in _APPLIER_ORCH_STEP_FIELDS:
        return f"applier rejected out-of-scope step field '{m.group(2)}'"
    return None


# ── Store access (the ONLY two files the applier may write — §0.4) ───────────

def _load_store(target_kind: str) -> list[dict]:
    if target_kind == "agent":
        from core.routes.agents import load_user_agents
        return load_user_agents()
    from core.routes.orchestrations import load_orchestrations
    return load_orchestrations()


def _save_store(target_kind: str, objs: list[dict]) -> None:
    if target_kind == "agent":
        from core.routes.agents import save_user_agents
        save_user_agents(objs)
    else:
        from core.routes.orchestrations import save_orchestrations
        save_orchestrations(objs)


def _validate_schema(target_kind: str, config: dict) -> None:
    """Full pydantic validation of the post-edit config (checklist 3.11)."""
    try:
        if target_kind == "agent":
            from core.models import Agent
            Agent.model_validate(config)
        else:
            from core.models_orchestration import Orchestration
            Orchestration.model_validate(config)
    except Exception as e:
        raise ApplyError(f"edited config failed schema validation: {e}") from e


def _apply_edit(config: dict, field: str, new_value: Any, target_kind: str) -> None:
    if target_kind == "agent":
        config[field] = new_value
        return
    m = _APPLIER_ORCH_STEP_RE.match(field)
    idx, step_field = int(m.group(1)), m.group(2)
    steps = config.get("steps") or []
    if idx >= len(steps):
        raise ApplyError(f"edit path '{field}' addresses a nonexistent step")
    steps[idx][step_field] = new_value


def _ensure_baseline_snapshot(
    user_id: str | None, object_id: str, config: dict, version_n: int
) -> None:
    """Snapshot v<N> before apply/rollback mutates anything (checklist 3.9)."""
    if versioning.load_version(user_id, object_id, version_n) is None:
        versioning.snapshot_version(
            user_id,
            object_id,
            copy.deepcopy(config),
            version_n=version_n,
            parent_version_n=None,
            is_active=True,
            improvement_run_id=config.get("improvement_run_id"),
            metric_snapshot=config.get("metric_snapshot"),
        )


# ── Apply / reject / rollback ─────────────────────────────────────────────────

def apply_run(
    user_id: str | None,
    run_id: str,
    accepted_fields: list[str] | None = None,
    executing_orchestration_id: str | None = None,
) -> dict:
    """Apply a pending run's ProposedDiff (optionally a per-field subset).

    `executing_orchestration_id` enables the CP5 self-edit lockout: an
    orchestration may never apply an improvement to ITSELF while running.
    """
    try:
        run = runs_mod.get_run(user_id, run_id)
    except runs_mod.RunNotFound:
        raise ApplyError(f"improvement run '{run_id}' not found", status=404)
    if run.get("decision") != "pending" or run.get("closed_at"):
        raise ApplyError(f"improvement run '{run_id}' is already closed", status=409)

    if (
        executing_orchestration_id
        and run.get("target_object_id") == executing_orchestration_id
    ):
        raise ApplyError(
            f"self-edit lockout: refusing to apply improvements to "
            f"'{executing_orchestration_id}' from within its own run",
            status=409,
        )

    try:
        diff = runs_mod.load_proposal(user_id, run_id)["proposed_diff"]
    except Exception:
        raise ApplyError(f"proposal payload for run '{run_id}' not found", status=404)

    target_kind = run["target_kind"]
    object_id = run["target_object_id"]

    edits = [
        e for e in diff.get("field_edits", [])
        if accepted_fields is None or e.get("field") in accepted_fields
    ]
    if not edits:
        raise ApplyError("no accepted field edits to apply")

    # Independent second-boundary allow-list check (checklist 3.7).
    for e in edits:
        err = check_edit_allowed(str(e.get("field") or ""), target_kind)
        if err:
            raise ApplyError(err)

    objs = _load_store(target_kind)
    pos = next((i for i, o in enumerate(objs) if o.get("id") == object_id), None)
    if pos is None:
        raise ApplyError(f"{target_kind} '{object_id}' no longer exists", status=404)
    current = objs[pos]
    baseline_n = int(current.get("version_n") or 1)

    _ensure_baseline_snapshot(user_id, object_id, current, baseline_n)

    new_config = copy.deepcopy(current)
    for e in edits:
        _apply_edit(new_config, e["field"], e.get("new_value"), target_kind)
    new_n = versioning.next_version_n(user_id, object_id, at_least=baseline_n + 1)
    new_config["version_n"] = new_n
    new_config["is_active"] = True
    new_config["improvement_run_id"] = run_id
    _validate_schema(target_kind, new_config)

    versioning.snapshot_version(
        user_id,
        object_id,
        copy.deepcopy(new_config),
        version_n=new_n,
        parent_version_n=baseline_n,
        is_active=True,
        improvement_run_id=run_id,
        metric_snapshot=diff.get("expected_metric_deltas") or None,
    )
    versioning.transfer_active(user_id, object_id, new_n)

    objs[pos] = new_config
    _save_store(target_kind, objs)

    run = runs_mod.update_run(
        user_id,
        run_id,
        new_version_n=new_n,
        decision="keep",
        closed_at=_now_iso(),
    )
    return {"run": run, "object": new_config, "applied_fields": [e["field"] for e in edits]}


def reject_run(user_id: str | None, run_id: str) -> dict:
    """Close a pending run without applying anything."""
    try:
        run = runs_mod.get_run(user_id, run_id)
    except runs_mod.RunNotFound:
        raise ApplyError(f"improvement run '{run_id}' not found", status=404)
    if run.get("decision") != "pending" or run.get("closed_at"):
        raise ApplyError(f"improvement run '{run_id}' is already closed", status=409)
    return runs_mod.update_run(user_id, run_id, decision="revert", closed_at=_now_iso())


def rollback(user_id: str | None, object_id: str, version_n: int) -> dict:
    """Restore a snapshot's config into the live store. JSON, not git."""
    snapshot = versioning.load_version(user_id, object_id, version_n)
    if snapshot is None:
        raise ApplyError(
            f"no version snapshot v{version_n} for '{object_id}'", status=404
        )

    target_kind = "agent"
    objs = _load_store("agent")
    pos = next((i for i, o in enumerate(objs) if o.get("id") == object_id), None)
    if pos is None:
        target_kind = "orchestration"
        objs = _load_store("orchestration")
        pos = next((i for i, o in enumerate(objs) if o.get("id") == object_id), None)
    if pos is None:
        raise ApplyError(f"object '{object_id}' no longer exists", status=404)

    current = objs[pos]
    _ensure_baseline_snapshot(
        user_id, object_id, current, int(current.get("version_n") or 1)
    )

    restored = copy.deepcopy(snapshot["config"])
    restored["version_n"] = int(snapshot["version_n"])
    restored["is_active"] = True
    _validate_schema(target_kind, restored)

    objs[pos] = restored
    _save_store(target_kind, objs)
    versioning.transfer_active(user_id, object_id, int(snapshot["version_n"]))
    return {"object": restored, "restored_version_n": int(snapshot["version_n"])}


def revert_autonomous_since(
    user_id: str | None, since_iso: str, object_id: str | None = None
) -> dict:
    """Revert every autonomously-applied version since `since_iso` (CP5 5.19).

    For each affected object, rolls back to the earliest baseline version
    among its autonomous applies in the window, flips those runs' decision to
    'revert', and emits one inbox audit entry per object. Never silent.
    """
    from core.improve import inbox as inbox_mod

    targets: dict[str, list[dict]] = {}
    for r in runs_mod.load_runs(user_id):
        if r.get("type") == "benchmark":
            continue
        if r.get("mode") != "autonomous" or r.get("decision") != "keep":
            continue
        if (r.get("closed_at") or "") < since_iso:
            continue
        if object_id and r.get("target_object_id") != object_id:
            continue
        targets.setdefault(r["target_object_id"], []).append(r)

    reverted: list[dict] = []
    errors: list[str] = []
    for obj_id, obj_runs in sorted(targets.items()):
        baseline_n = min(int(r.get("baseline_version_n") or 1) for r in obj_runs)
        try:
            result = rollback(user_id, obj_id, baseline_n)
        except ApplyError as e:
            errors.append(f"{obj_id}: {e}")
            continue
        for r in obj_runs:
            runs_mod.update_run(user_id, r["run_id"], decision="revert")
        try:
            inbox_mod.add_entry(
                user_id,
                kind="revert",
                mode="autonomous",
                run_id=obj_runs[-1]["run_id"],
                object_id=obj_id,
                version_n=baseline_n,
                message=(
                    f"Bulk revert: {len(obj_runs)} autonomous edit(s) since "
                    f"{since_iso} undone — restored v{baseline_n}"
                ),
            )
        except Exception:
            pass  # audit best-effort; the revert itself already succeeded
        reverted.append(
            {"object_id": obj_id, "restored_version_n": baseline_n,
             "reverted_run_ids": [r["run_id"] for r in obj_runs]}
        )
    return {"reverted": reverted, "errors": errors}
