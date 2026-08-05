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


# ── Checkpoint 6 — rubrics, augmentation, splits ─────────────────────────────
#
# Seven routes, all on THIS router, all behind `resolve_improve_user`. No new
# router and no new server.py include: the §0.4 hook budget stays consumed.

class ApproveVariantsRequest(BaseModel):
    # input_id -> approve (True) / reject (False). Rejected variants are removed.
    decisions: dict[str, bool]


class ResplitRequest(BaseModel):
    # Materializing splits invalidates score comparability against every
    # previous run, so it cannot happen by accident.
    confirm: bool = False


@router.get("/api/improve/rubrics")
async def list_rubrics_route(user_id: str = Depends(resolve_improve_user)):
    """Every rubric's latest version."""
    from core.improve import rubrics as rubrics_mod
    return rubrics_mod.list_rubrics(user_id)


@router.get("/api/improve/rubric/{rubric_id}")
async def get_rubric_route(
    rubric_id: str,
    version: int | None = None,
    user_id: str = Depends(resolve_improve_user),
):
    """Fetch a rubric; `?version=N` pins a specific immutable version."""
    from core.improve import rubrics as rubrics_mod
    try:
        rubric = rubrics_mod.get_rubric(user_id, rubric_id, version)
    except rubrics_mod.RubricNotFound:
        raise HTTPException(status_code=404, detail="Rubric not found")
    return {
        "rubric": rubric,
        "versions": [
            {"version": v["version"], "content_hash": v["content_hash"],
             "created_at": v.get("created_at"), "name": v.get("name")}
            for v in rubrics_mod.list_versions(user_id, rubric_id)
        ],
    }


@router.put("/api/improve/rubric/{rubric_id}")
async def save_rubric_route(
    rubric_id: str, body: dict, user_id: str = Depends(resolve_improve_user)
):
    """Create a rubric, or write a NEW version of one.

    Never mutates an existing version — an edit mid-ratchet would measure
    baseline and new scores with different rulers.
    """
    from pydantic import ValidationError
    from core.improve import judge as judge_mod
    from core.improve import rubrics as rubrics_mod

    body = dict(body or {})
    body["id"] = rubric_id  # path is authoritative
    try:
        saved = rubrics_mod.save_rubric(user_id, body)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Judge == tuner model is a SOFT warning, surfaced, never a hard block.
    warning = None
    try:
        settings = load_settings()
        judge_model = judge_mod.resolve_judge_model(settings)
        if judge_mod.judge_tuner_collision(settings, judge_model):
            warning = (
                f"The judge and the tuner both resolve to '{judge_model}'. "
                "Optimizing against a judge that is the same model as the "
                "optimizer is a known correlated-error risk."
            )
    except Exception:
        pass
    return {"rubric": saved, "warning": warning}


@router.delete("/api/improve/rubric/{rubric_id}")
async def delete_rubric_route(
    rubric_id: str, user_id: str = Depends(resolve_improve_user)
):
    """Soft-delete. Refused while any benchmark still references the rubric."""
    from core.improve import rubrics as rubrics_mod
    try:
        return rubrics_mod.delete_rubric(user_id, rubric_id)
    except rubrics_mod.RubricNotFound:
        raise HTTPException(status_code=404, detail="Rubric not found")
    except rubrics_mod.RubricInUse as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/api/improve/benchmark/{benchmark_id}/augment")
async def augment_benchmark_route(
    benchmark_id: str, user_id: str = Depends(resolve_improve_user)
):
    """Generate paraphrase variants; they land `approved: false`.

    An explicit authoring action, never an implicit run-time step: an LLM
    paraphrase that quietly changes the question is a corrupted benchmark, and
    a corrupted benchmark silently misdirects the tuner for every subsequent
    iteration.
    """
    from core.improve import augment as augment_mod
    from core.improve import benchmark as bm
    from core.improve import splits as splits_mod

    try:
        suite = bm.load_benchmark(user_id, benchmark_id)
    except bm.BenchmarkNotFound:
        raise HTTPException(status_code=404, detail="Benchmark not found")

    try:
        generated = await augment_mod.generate_variants(user_id, suite)
    except augment_mod.AugmentationError as e:
        raise HTTPException(status_code=422, detail=str(e))

    superseded = augment_mod.supersede_unapproved(suite)
    suite["inputs"] = list(suite.get("inputs") or []) + generated["variants"]
    splits_mod.inherit_from_parents(suite["inputs"])
    bm.save_benchmark(user_id, suite)

    return {
        "benchmark_id": benchmark_id,
        "variants": generated["variants"],
        "rejected": generated["rejected"],
        "superseded_unapproved": superseded,
    }


@router.post("/api/improve/benchmark/{benchmark_id}/augment/approve")
async def approve_variants_route(
    benchmark_id: str,
    req: ApproveVariantsRequest,
    user_id: str = Depends(resolve_improve_user),
):
    """Approve or reject generated variants by id. Rejected ones are removed."""
    from core.improve import augment as augment_mod
    from core.improve import benchmark as bm

    try:
        suite = bm.load_benchmark(user_id, benchmark_id)
    except bm.BenchmarkNotFound:
        raise HTTPException(status_code=404, detail="Benchmark not found")

    outcome = augment_mod.apply_approvals(suite, req.decisions)
    bm.save_benchmark(user_id, suite)
    return {"benchmark_id": benchmark_id, **outcome}


@router.post("/api/improve/benchmark/{benchmark_id}/resplit")
async def resplit_benchmark_route(
    benchmark_id: str,
    req: ResplitRequest,
    user_id: str = Depends(resolve_improve_user),
):
    """Materialize splits/folds from `split_policy` into the benchmark file.

    Requires explicit confirmation: reassigning splits invalidates score
    comparability against every previous run of this benchmark.
    """
    from core.improve import benchmark as bm
    from core.improve import splits as splits_mod

    if not req.confirm:
        raise HTTPException(
            status_code=400,
            detail=("Re-splitting invalidates score comparability with every "
                    "previous run of this benchmark. Re-send with confirm=true."),
        )
    try:
        suite = bm.load_benchmark(user_id, benchmark_id)
    except bm.BenchmarkNotFound:
        raise HTTPException(status_code=404, detail="Benchmark not found")

    try:
        report = splits_mod.materialize(suite)
    except splits_mod.SplitPolicyError as e:
        raise HTTPException(status_code=422, detail=str(e))
    bm.save_benchmark(user_id, suite)
    return {"benchmark_id": benchmark_id, **report}
