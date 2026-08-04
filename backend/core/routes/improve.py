"""
Self-Improvement REST routes.

Checkpoint 2 (read-only): GET /api/improve/insights — latest detector report
+ atomic learnings for the calling user's traces.

Checkpoint 3 (human-gated write path): POST /api/improve/propose,
POST /api/improve/apply, POST /api/improve/rollback/{object_id}/{version_n},
GET /api/improve/versions/{object_id}.

Checkpoint 4 (benchmark suite): GET /api/improve/benchmarks,
PUT/DELETE /api/improve/benchmark/{benchmark_id} (authoring),
POST /api/improve/benchmark/{benchmark_id} (run),
GET /api/improve/benchmark/results.

Auth follows the ACL map in core/improve/SCHEMA.md §3: session JWT (login
gate) or API key; every request is scoped to the caller's own
/data/improve/<user_id>/ namespace.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from core.config import load_settings
from core.improve.trace_writer import resolve_user_id
from core.user_auth import verify_session_token

router = APIRouter()


async def resolve_improve_user(request: Request) -> str:
    """Authenticated user id for improvement storage (ACL map, SCHEMA.md §3).

    - Valid session JWT → its subject (the login username).
    - Valid API key → the workspace's configured user namespace.
    - Login gate disabled → the default single-user namespace.
    - Login gate enabled and no valid credential → 401.
    """
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if token:
        try:
            subject = verify_session_token(token)
        except Exception:
            subject = None
        if subject:
            return subject
        try:
            from core.api_keys import validate_api_key
            if validate_api_key(token):
                return resolve_user_id()
        except Exception:
            pass

    try:
        s = load_settings()
        gate_enabled = bool(
            s.get("login_enabled") and s.get("login_username") and s.get("login_password_hash")
        )
    except Exception:
        gate_enabled = False
    if gate_enabled:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return resolve_user_id()


@router.get("/api/improve/insights")
async def get_insights(
    agent_id: str | None = None,
    orchestration_id: str | None = None,
    user_id: str = Depends(resolve_improve_user),
):
    """Latest insights report for the calling user's traces. Read-only."""
    from core.improve.insights import extract_insights
    from core.improve.runner import build_report

    report = build_report(
        user_id=user_id, agent_id=agent_id, orchestration_id=orchestration_id
    )
    return {"report": report, "insights": extract_insights(report)}


# ── Checkpoint 3 — propose / apply / rollback / versions ─────────────────────

class ProposeRequest(BaseModel):
    target_object_id: str
    target_kind: Literal["agent", "orchestration"]
    tuner_model: str | None = None  # per-run override (§0.6.3); default from settings


class ApplyRequest(BaseModel):
    run_id: str
    action: Literal["apply", "reject"] = "apply"
    accepted_fields: list[str] | None = None  # None = apply every proposed edit


@router.post("/api/improve/propose")
async def propose_improvement(
    req: ProposeRequest, user_id: str = Depends(resolve_improve_user)
):
    """Run the tuner against the target's insights; returns a ProposedDiff."""
    from core.improve import runs as runs_mod
    from core.improve import tuner

    try:
        return await tuner.propose(
            user_id,
            req.target_object_id,
            req.target_kind,
            tuner_model=req.tuner_model,
        )
    except runs_mod.RunConflict:
        raise HTTPException(
            status_code=409,
            detail=f"An improvement run is already in progress for "
                   f"'{req.target_object_id}'",
        )
    except tuner.TargetNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except tuner.TunerOutputError as e:
        raise HTTPException(
            status_code=422, detail=f"Tuner produced invalid output: {e}"
        )


@router.post("/api/improve/apply")
async def apply_improvement(
    req: ApplyRequest, user_id: str = Depends(resolve_improve_user)
):
    """Apply (or reject) a pending ProposedDiff, per-field selectable."""
    from core.improve import applier

    try:
        if req.action == "reject":
            return {"run": applier.reject_run(user_id, req.run_id)}
        return applier.apply_run(user_id, req.run_id, req.accepted_fields)
    except applier.ApplyError as e:
        raise HTTPException(status_code=e.status, detail=str(e))


@router.post("/api/improve/rollback/{object_id}/{version_n}")
async def rollback_version(
    object_id: str, version_n: int, user_id: str = Depends(resolve_improve_user)
):
    """Restore a snapshot into the live store (JSON rollback, not git)."""
    from core.improve import applier

    try:
        return applier.rollback(user_id, object_id, version_n)
    except applier.ApplyError as e:
        raise HTTPException(status_code=e.status, detail=str(e))


@router.get("/api/improve/versions/{object_id}")
async def get_versions(
    object_id: str, user_id: str = Depends(resolve_improve_user)
):
    """All version snapshots for an object, plus its improvement runs."""
    from core.improve import runs as runs_mod
    from core.improve import versioning

    versions = versioning.list_versions(user_id, object_id)
    runs = []
    for r in runs_mod.load_runs(user_id):
        if r.get("target_object_id") != object_id:
            continue
        r = dict(r)
        if r.get("proposed_diff_ref"):  # let the UI re-open a pending review
            try:
                r["proposed_diff"] = runs_mod.load_proposal(
                    user_id, r["run_id"]
                ).get("proposed_diff")
            except Exception:
                r["proposed_diff"] = None
        runs.append(r)
    return {"object_id": object_id, "versions": versions, "runs": runs}


# ── Checkpoint 4 — benchmarks ─────────────────────────────────────────────────

class RunBenchmarkRequest(BaseModel):
    target_object_id: str | None = None       # override — benchmarks are reusable
    improvement_run_id: str | None = None     # stamp baseline/new score onto a run
    record_as: Literal["baseline", "new"] | None = None


@router.get("/api/improve/benchmarks")
async def list_benchmarks_route(user_id: str = Depends(resolve_improve_user)):
    from core.improve import benchmark as bm
    return bm.list_benchmarks(user_id)


@router.put("/api/improve/benchmark/{benchmark_id}")
async def save_benchmark_route(
    benchmark_id: str, body: dict, user_id: str = Depends(resolve_improve_user)
):
    """Create or update a standalone benchmark suite."""
    from pydantic import ValidationError
    from core.improve import benchmark as bm
    body["id"] = benchmark_id  # path is authoritative
    try:
        return bm.save_benchmark(user_id, body)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.delete("/api/improve/benchmark/{benchmark_id}")
async def delete_benchmark_route(
    benchmark_id: str, user_id: str = Depends(resolve_improve_user)
):
    from core.improve import benchmark as bm
    if not bm.delete_benchmark(user_id, benchmark_id):
        raise HTTPException(status_code=404, detail="Benchmark not found")
    return {"deleted": benchmark_id}


@router.post("/api/improve/benchmark/{benchmark_id}")
async def run_benchmark_route(
    benchmark_id: str,
    request: Request,
    body: RunBenchmarkRequest | None = None,
    user_id: str = Depends(resolve_improve_user),
):
    """Run a benchmark suite against its target (or an override target)."""
    from core.improve import benchmark as bm
    body = body or RunBenchmarkRequest()
    server_module = getattr(request.app.state, "server_module", None)
    try:
        return await bm.run_benchmark(
            user_id,
            benchmark_id,
            target_object_id=body.target_object_id,
            server_module=server_module,
            improvement_run_id=body.improvement_run_id,
            record_as=body.record_as,
        )
    except bm.BenchmarkNotFound:
        raise HTTPException(status_code=404, detail="Benchmark not found")
    except bm.BenchmarkTargetNotFound as e:
        raise HTTPException(status_code=404, detail=f"Target not found: {e}")


@router.get("/api/improve/benchmark/results")
async def benchmark_results_route(
    benchmark_id: str | None = None,
    target_object_id: str | None = None,
    user_id: str = Depends(resolve_improve_user),
):
    from core.improve import benchmark as bm
    return bm.list_results(
        user_id, benchmark_id=benchmark_id, target_object_id=target_object_id
    )


# ── Checkpoint 5 — Self-Improvement Inbox + autonomous bulk revert ────────────

class RevertAutonomousRequest(BaseModel):
    since: str                      # ISO-8601 UTC timestamp
    object_id: str | None = None    # optional single-object scope


@router.get("/api/improve/inbox")
async def get_inbox(
    object_id: str | None = None,
    kind: str | None = None,
    limit: int = 100,
    user_id: str = Depends(resolve_improve_user),
):
    """Self-Improvement Inbox — notification + audit log, newest first."""
    from core.improve import inbox as inbox_mod
    return inbox_mod.list_entries(
        user_id, object_id=object_id, kind=kind, limit=limit
    )


@router.post("/api/improve/revert-autonomous")
async def revert_autonomous(
    req: RevertAutonomousRequest, user_id: str = Depends(resolve_improve_user)
):
    """Revert all autonomous edits since T (checklist 5.19). Audited."""
    from core.improve import applier
    return applier.revert_autonomous_since(user_id, req.since, req.object_id)
