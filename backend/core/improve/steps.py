"""
Self-improvement orchestration step executors (Checkpoint 5).

The improvement loop is not a parallel subsystem — it is a set of composable
orchestration steps (§0.3.3). Both the human-gated and autonomous variants run
through THESE SAME executors (checklist 5.7); the only behavioral switch is
the shared-state key `improve_mode` ("human" | "autonomous", default human),
which makes IMPROVE_REVIEW block on the existing approval flow or skip.

Executors (registered into STEP_EXECUTORS by the single CP5 hook in
core/orchestration/steps.py):

  IMPROVE_ANALYZE        detectors over the target's trace window → state[insights]
  IMPROVE_PROPOSE        tuner → state[proposed_diff, improve_run_id]
  IMPROVE_REVIEW         human_input_required via the EXISTING approval flow;
                         skipped in autonomous mode
  IMPROVE_APPLY          applier.apply_run (self-edit lockout enforced)
  BENCHMARK              benchmark.run_benchmark → state[<record_as>_score]
  IMPROVE_RATCHET_DECIDE keep/revert vs threshold + every autonomous safeguard
                         (iteration cap, plateau patience, wallclock, budget)

Autonomous applies/reverts/aborts are NEVER silent — each emits a
Self-Improvement Inbox entry (§0.6.2).

Shared-state contract:
  improve_mode              "human" | "autonomous"          (input, default human)
  improve_target_id/_kind   propagated by IMPROVE_ANALYZE for downstream steps
  insights                  IMPROVE_ANALYZE output (or its output_key)
  improve_run_id            open ImprovementRun id (IMPROVE_PROPOSE)
  proposed_diff             the ProposedDiff (IMPROVE_PROPOSE)
  improve_review            review decision dict (IMPROVE_REVIEW / resume path)
  new_version_n             version written by IMPROVE_APPLY
  baseline_score/new_score  BENCHMARK outputs (per benchmark_record_as)
  ratchet_decision/_stop/_stop_reason   IMPROVE_RATCHET_DECIDE outputs for
                            IF_ELSE / SWITCH routing on the canvas
"""
import time
from datetime import datetime, timezone
from typing import AsyncGenerator, TYPE_CHECKING

from core.models_orchestration import StepConfig, OrchestrationRun

if TYPE_CHECKING:
    from core.orchestration.engine import OrchestrationEngine

# Ratchet stops proactively when this fraction of the global wallclock budget
# (orch.timeout_minutes, backed by ORCH_GLOBAL_TIMEOUT_MIN) is consumed, so a
# fresh iteration is never started only to be killed mid-flight.
RATCHET_TIMEOUT_FRACTION = 0.9


def _user_id() -> str:
    from core.improve.trace_writer import resolve_user_id
    return resolve_user_id()


def _mode(run: OrchestrationRun) -> str:
    mode = str(run.shared_state.get("improve_mode") or "human").lower()
    return "autonomous" if mode == "autonomous" else "human"


def _target(step: StepConfig, run: OrchestrationRun) -> tuple[str | None, str]:
    """(target_id, target_kind) from step config, else shared state."""
    target_id = step.improve_target_id or run.shared_state.get("improve_target_id")
    kind = (
        (step.improve_target_id and step.improve_target_kind)
        or run.shared_state.get("improve_target_kind")
        or step.improve_target_kind
        or "agent"
    )
    return target_id, ("orchestration" if kind == "orchestration" else "agent")


def _spent_usd(run_id: str) -> float:
    """LLM spend attributed to this orchestration run (usage_tracker join)."""
    try:
        from core.usage_tracker import get_usage_logs
        records = get_usage_logs(limit=1_000_000, run_id=run_id)
    except Exception:
        return 0.0
    return round(sum(float(r.get("estimated_cost") or 0.0) for r in records), 6)


def _budget(step: StepConfig, run: OrchestrationRun) -> float | None:
    if step.improve_budget_usd is not None:
        return float(step.improve_budget_usd)
    b = run.shared_state.get("improve_budget_usd")
    return float(b) if b is not None else None


def _benchmark_id(step: StepConfig, run: OrchestrationRun) -> str | None:
    """Benchmark id from shared state override, else the step default."""
    return run.shared_state.get("improve_benchmark_id") or step.benchmark_id


def _ratchet_threshold(step: StepConfig, run: OrchestrationRun) -> float:
    """Ratchet threshold from runtime state, else step config default."""
    if run.shared_state.get("improve_ratchet_threshold") is not None:
        return float(run.shared_state.get("improve_ratchet_threshold"))
    if run.shared_state.get("ratchet_threshold") is not None:
        return float(run.shared_state.get("ratchet_threshold"))
    return float(step.ratchet_threshold)


def _ratchet_max_iterations(step: StepConfig, run: OrchestrationRun) -> int:
    """Ratchet max-iteration cap from runtime state, else step config default."""
    if run.shared_state.get("improve_ratchet_max_iterations") is not None:
        return int(run.shared_state.get("improve_ratchet_max_iterations"))
    if run.shared_state.get("ratchet_max_iterations") is not None:
        return int(run.shared_state.get("ratchet_max_iterations"))
    return int(step.ratchet_max_iterations)


def _ratchet_plateau_patience(step: StepConfig, run: OrchestrationRun) -> int:
    """Ratchet plateau patience from runtime state, else step config default."""
    if run.shared_state.get("improve_ratchet_plateau_patience") is not None:
        return int(run.shared_state.get("improve_ratchet_plateau_patience"))
    if run.shared_state.get("ratchet_plateau_patience") is not None:
        return int(run.shared_state.get("ratchet_plateau_patience"))
    return int(step.ratchet_plateau_patience)


def _notify_inbox(user_id: str, **kwargs) -> None:
    """Best-effort inbox write — an unwritable inbox must not kill the run."""
    try:
        from core.improve import inbox as inbox_mod
        inbox_mod.add_entry(user_id, **kwargs)
    except Exception:
        pass


def grading_detail(result: dict) -> dict:
    """The CP6 grading metadata a ratchet needs to decide COMPARABILITY.

    Two scores are only comparable when they were produced by the same ruler.
    Everything here is optional and absent for a CP4 benchmark, which is what
    keeps the v1 path behaviourally identical.
    """
    return {
        "grading_mode": result.get("grading_mode"),
        "grading_strictness": result.get("grading_strictness"),
        "rubric_id": result.get("rubric_id"),
        "rubric_version": result.get("rubric_version"),
        "rubric_content_hash": result.get("rubric_content_hash"),
        "process_score": result.get("process_score"),
        "outcome_score": result.get("outcome_score"),
        "composite_score": result.get("composite_score"),
        "outcome_na": result.get("outcome_na"),
        "extraction_failed_rate": result.get("extraction_failed_rate"),
        "snapshot_id": result.get("snapshot_id"),
        "sql_memory_generation": result.get("sql_memory_generation"),
        "benchmark_run_id": result.get("run_id"),
        "scores_by_split": result.get("scores_by_split"),
        "scores_by_fold": result.get("scores_by_fold"),
        "fold_stddev": result.get("fold_stddev"),
        "fold_index": result.get("fold_index"),
        "incomparable_reason": result.get("incomparable_reason"),
    }


def ratchet_basis(
    baseline: float | None, new: float | None,
    baseline_detail: dict | None, new_detail: dict | None,
) -> tuple[float | None, float | None, str]:
    """The pair of scores the ratchet actually decides on, and their basis.

    THE RATCHET DECIDES ON HOLDOUT (§6.5.1). Train is what the tuner optimized
    against; deciding on it rewards memorizing the train split. When both runs
    report a holdout score, that is the comparison — even when the composite
    (or train) moved the other way. Falls back to the composite for CP4-era
    benchmarks and for suites with no holdout input.
    """
    baseline_splits = (baseline_detail or {}).get("scores_by_split") or {}
    new_splits = (new_detail or {}).get("scores_by_split") or {}
    baseline_holdout = baseline_splits.get("holdout")
    new_holdout = new_splits.get("holdout")
    if isinstance(baseline_holdout, (int, float)) and \
            isinstance(new_holdout, (int, float)):
        return float(baseline_holdout), float(new_holdout), "holdout"
    return baseline, new, "composite"


def unreliable_reason(baseline_detail: dict | None, new_detail: dict | None) -> str | None:
    """Why a score must not be trusted, or None. Checklist 6.26.

    A benchmark that silently scores 0 because of a mis-specified extractor
    will drive the ratchet to revert good edits indefinitely.
    """
    for label, detail in (("baseline", baseline_detail), ("new", new_detail)):
        reason = (detail or {}).get("incomparable_reason")
        if reason:
            return f"{label} run: {reason}"
    return None


def comparability_reason(baseline: dict | None, new: dict | None) -> str | None:
    """Why two benchmark scores must NOT be compared, or None if they may be.

    Silently comparing across rubric versions or grading modes is the subtlest
    way this subsystem can lie to you (§6.4): baseline and new would be measured
    with different rulers, and the ratchet would keep or revert on noise.
    """
    baseline = baseline or {}
    new = new or {}
    if (baseline.get("grading_mode") or None) != (new.get("grading_mode") or None):
        return (
            f"grading_mode changed between runs "
            f"({baseline.get('grading_mode')!r} -> {new.get('grading_mode')!r})"
        )
    if (baseline.get("rubric_content_hash") or None) != (
        new.get("rubric_content_hash") or None
    ):
        return (
            "rubric content_hash changed between runs — the rubric was edited "
            "mid-ratchet, so the two scores were measured with different rulers"
        )
    if (baseline.get("sql_memory_generation") or None) != (
        new.get("sql_memory_generation") or None
    ):
        return (
            "SQL schema memory advanced between runs — memory content is part "
            "of the agent's effective configuration, so the two scores were "
            "measured with different rulers"
        )
    return None


def _elapsed_seconds(run: OrchestrationRun) -> float:
    try:
        started = datetime.strptime(
            run.started_at, "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - started).total_seconds()
    except Exception:
        return 0.0


class ImproveAnalyzeStepExecutor:
    """Run detectors over the target's traces; write insights to shared state."""

    async def execute(
        self, step: StepConfig, run: OrchestrationRun, engine: "OrchestrationEngine"
    ) -> AsyncGenerator[dict, None]:
        from core.improve.insights import extract_insights
        from core.improve.runner import build_report

        target_id, target_kind = _target(step, run)
        if not target_id:
            raise RuntimeError(
                f"Improve-Analyze step '{step.name}' has no improve_target_id configured"
            )

        yield {"type": "_log_prompt", "orch_step_id": step.id,
               "prompt": f"[Improve-Analyze] target={target_kind}:{target_id}"}

        report = build_report(
            user_id=_user_id(),
            agent_id=target_id if target_kind == "agent" else None,
            orchestration_id=target_id if target_kind == "orchestration" else None,
        )
        insights = extract_insights(report)

        # Propagate the target so downstream improve steps need no re-config.
        run.shared_state["improve_target_id"] = target_id
        run.shared_state["improve_target_kind"] = target_kind
        run.shared_state[step.output_key or "insights"] = insights

        yield {
            "type": "improve_analyze_result",
            "orch_step_id": step.id,
            "step_name": step.name,
            "target_object_id": target_id,
            "trace_count": insights.get("trace_count", 0),
            "insight_count": insights.get("insight_count", 0),
        }


class ImproveProposeStepExecutor:
    """Call the tuner; write proposed_diff + improve_run_id to shared state."""

    async def execute(
        self, step: StepConfig, run: OrchestrationRun, engine: "OrchestrationEngine"
    ) -> AsyncGenerator[dict, None]:
        from core.improve import runs as runs_mod
        from core.improve import tuner

        user_id = _user_id()
        target_id, target_kind = _target(step, run)
        if not target_id:
            raise RuntimeError(
                f"Improve-Propose step '{step.name}' has no improve_target_id configured"
            )

        # Per-run LLM budget (5.16): abort BEFORE spending more.
        budget = _budget(step, run)
        if budget is not None:
            spent = _spent_usd(run.run_id)
            if spent >= budget:
                _notify_inbox(
                    user_id, kind="budget_abort", mode=_mode(run),
                    run_id=run.shared_state.get("improve_run_id"),
                    object_id=target_id,
                    message=(f"Improvement run aborted: LLM budget "
                             f"${budget:.4f} exceeded (spent ${spent:.4f})"),
                )
                run.status = "failed"
                yield {"type": "step_error", "orch_step_id": step.id,
                       "error": f"LLM budget ${budget:.4f} exceeded (spent ${spent:.4f})"}
                return

        yield {"type": "_log_prompt", "orch_step_id": step.id,
               "prompt": f"[Improve-Propose] target={target_kind}:{target_id}"}

        try:
            result = await tuner.propose(
                user_id, target_id, target_kind,
                tuner_model=step.model,   # per-step override; None → settings default
                mode=_mode(run),
            )
        except runs_mod.RunConflict:
            run.status = "failed"
            yield {"type": "step_error", "orch_step_id": step.id,
                   "error": f"An improvement run is already open for '{target_id}' (409)"}
            return
        except tuner.TunerOutputError as e:
            run.status = "failed"
            yield {"type": "step_error", "orch_step_id": step.id,
                   "error": f"Tuner produced invalid output: {e}"}
            return

        run.shared_state["improve_run_id"] = result["run"]["run_id"]
        run.shared_state[step.output_key or "proposed_diff"] = result["proposed_diff"]

        yield {
            "type": "improve_propose_result",
            "orch_step_id": step.id,
            "step_name": step.name,
            "improve_run_id": result["run"]["run_id"],
            "field_edits": [e["field"] for e in result["proposed_diff"]["field_edits"]],
            "tuner_model": result["run"]["tuner_model"],
        }


class ImproveReviewStepExecutor:
    """Block on the EXISTING approval flow (human_input_required); skipped in
    autonomous mode (checklists 5.5 / 5.6)."""

    async def execute(
        self, step: StepConfig, run: OrchestrationRun, engine: "OrchestrationEngine"
    ) -> AsyncGenerator[dict, None]:
        review_key = step.output_key or "improve_review"

        if _mode(run) == "autonomous":
            run.shared_state[review_key] = {"action": "apply", "auto": True}
            yield {
                "type": "improve_review_skipped",
                "orch_step_id": step.id,
                "step_name": step.name,
                "reason": "autonomous mode — review step skipped",
            }
            return

        diff = run.shared_state.get("proposed_diff") or {}
        edits = diff.get("field_edits") or []
        summary = "\n".join(
            f"- {e.get('field')}: {str(e.get('rationale') or '')[:160]}" for e in edits
        ) or "(no proposed edits found in shared state)"
        prompt = (
            step.human_prompt
            or f"Review the proposed improvement "
               f"({len(edits)} field edit(s)):\n{summary}\n\nApply or reject?"
        )
        fields = step.human_fields or [
            {"name": "action", "type": "select", "label": "Decision",
             "options": ["apply", "reject"]},
        ]
        run.human_prompt = prompt
        run.human_fields = fields

        # Same pause contract as HumanStepExecutor — the engine checkpoints and
        # the existing resume() path writes the response to our output_key.
        yield {
            "type": "human_input_required",
            "orch_step_id": step.id,
            "prompt": prompt,
            "fields": fields,
            "proposed_diff": diff,
            "improve_run_id": run.shared_state.get("improve_run_id"),
            "run_id": run.run_id,
            "agent_context": None,
            "channel_id": None,
        }


class ImproveApplyStepExecutor:
    """Apply the pending ProposedDiff as a new version (or close as rejected)."""

    async def execute(
        self, step: StepConfig, run: OrchestrationRun, engine: "OrchestrationEngine"
    ) -> AsyncGenerator[dict, None]:
        from core.improve import applier

        user_id = _user_id()
        mode = _mode(run)
        improve_run_id = run.shared_state.get("improve_run_id")
        if not improve_run_id:
            raise RuntimeError(
                f"Improve-Apply step '{step.name}' found no improve_run_id in "
                "shared state — run an Improve-Propose step first"
            )

        review = (
            run.shared_state.get("improve_review")
            or run.shared_state.get("human_response")
            or {}
        )
        if not isinstance(review, dict):
            review = {"action": str(review)}
        action = str(review.get("action") or "apply").strip().lower()
        accepted_fields = review.get("accepted_fields")
        if not isinstance(accepted_fields, list) or not accepted_fields:
            accepted_fields = None

        yield {"type": "_log_prompt", "orch_step_id": step.id,
               "prompt": f"[Improve-Apply] run={improve_run_id} action={action}"}

        try:
            if action == "reject":
                result_run = applier.reject_run(user_id, improve_run_id)
                run.shared_state["improve_applied"] = False
                yield {
                    "type": "improve_apply_result",
                    "orch_step_id": step.id,
                    "step_name": step.name,
                    "applied": False,
                    "improve_run_id": improve_run_id,
                    "decision": result_run.get("decision"),
                }
                return

            # Self-edit lockout (5.15): the applier refuses when the target is
            # the orchestration currently executing this step.
            result = applier.apply_run(
                user_id,
                improve_run_id,
                accepted_fields,
                executing_orchestration_id=engine.orch.id,
            )
        except applier.ApplyError as e:
            run.status = "failed"
            yield {"type": "step_error", "orch_step_id": step.id, "error": str(e)}
            return

        new_n = result["run"].get("new_version_n")
        target_id = result["run"].get("target_object_id")
        run.shared_state["improve_applied"] = True
        run.shared_state["new_version_n"] = new_n
        if step.output_key:
            run.shared_state[step.output_key] = {
                "applied_fields": result.get("applied_fields", []),
                "new_version_n": new_n,
            }

        if mode == "autonomous":  # never silent (5.10)
            _notify_inbox(
                user_id, kind="apply", mode="autonomous",
                run_id=improve_run_id, object_id=target_id, version_n=new_n,
                message=(f"Autonomous apply: v{new_n} written for '{target_id}' "
                         f"({len(result.get('applied_fields', []))} field edit(s))"),
            )

        yield {
            "type": "improve_apply_result",
            "orch_step_id": step.id,
            "step_name": step.name,
            "applied": True,
            "improve_run_id": improve_run_id,
            "new_version_n": new_n,
            "applied_fields": result.get("applied_fields", []),
        }


class BenchmarkStepExecutor:
    """Run a standalone benchmark suite; write its score to shared state."""

    async def execute(
        self, step: StepConfig, run: OrchestrationRun, engine: "OrchestrationEngine"
    ) -> AsyncGenerator[dict, None]:
        from core.improve import benchmark as bm

        benchmark_id = _benchmark_id(step, run)
        if not benchmark_id:
            raise RuntimeError(
                f"Benchmark step '{step.name}' has no benchmark_id configured"
            )
        user_id = _user_id()
        target_id, _kind = _target(step, run)
        record_as = (
            step.benchmark_record_as
            if step.benchmark_record_as in ("baseline", "new") else None
        )

        yield {"type": "_log_prompt", "orch_step_id": step.id,
               "prompt": f"[Benchmark] suite={benchmark_id} "
                         f"target={target_id or '(suite default)'} record_as={record_as}"}

        try:
            result = await bm.run_benchmark(
                user_id,
                benchmark_id,
                target_object_id=target_id,
                server_module=engine.server_module,
                improvement_run_id=run.shared_state.get("improve_run_id"),
                record_as=record_as,
                # K-fold `per_iteration` rotation advances with the ratchet's
                # iteration counter, so each iteration is validated against a
                # different unseen slice (§6.5.2).
                iteration=int(run.shared_state.get("_ratchet_iteration") or 0),
                budget_usd=_budget(step, run),
            )
        except bm.BenchmarkNotFound:
            raise RuntimeError(f"Benchmark '{benchmark_id}' not found")
        except bm.BenchmarkTargetNotFound as e:
            raise RuntimeError(f"Benchmark target not found: {e}")
        except bm.BudgetExceeded as e:
            _notify_inbox(
                user_id, kind="budget_abort", mode=_mode(run),
                run_id=run.shared_state.get("improve_run_id"),
                object_id=target_id, message=str(e),
            )
            run.status = "failed"
            yield {"type": "step_error", "orch_step_id": step.id, "error": str(e)}
            return

        score = result.get("score")
        score_key = step.output_key or (
            f"{record_as}_score" if record_as else "benchmark_score"
        )
        run.shared_state[score_key] = score

        # CP6: the ratchet must be able to tell whether two scores were measured
        # with the same ruler, so the richer score object rides alongside the
        # bare float. Existing readers keep reading the float.
        detail = grading_detail(result)
        run.shared_state[f"{score_key}_detail"] = detail

        yield {
            "type": "benchmark_result",
            "orch_step_id": step.id,
            "step_name": step.name,
            "benchmark_id": benchmark_id,
            "score": score,
            "per_metric": result.get("per_metric", {}),
            "trace_count": result.get("trace_count", 0),
            "recorded_as": record_as,
            **{k: v for k, v in detail.items() if v is not None},
        }


class ImproveRatchetDecideStepExecutor:
    """Compare new_score vs baseline; emit keep/revert; enforce every
    autonomous safeguard (iteration cap, plateau, wallclock, budget).

    Writes flat keys for canvas routing (IF_ELSE / SWITCH):
      ratchet_decision     "keep" | "revert"
      ratchet_stop         bool — loop must terminate
      ratchet_stop_reason  "max_iterations" | "plateau" | "timeout" | "budget" | None
    """

    async def execute(
        self, step: StepConfig, run: OrchestrationRun, engine: "OrchestrationEngine"
    ) -> AsyncGenerator[dict, None]:
        from core.improve import applier
        from core.improve import runs as runs_mod

        user_id = _user_id()
        mode = _mode(run)
        state = run.shared_state
        improve_run_id = state.get("improve_run_id")
        threshold = _ratchet_threshold(step, run)
        max_iterations = _ratchet_max_iterations(step, run)
        plateau_patience = _ratchet_plateau_patience(step, run)

        iteration = int(state.get("_ratchet_iteration") or 0) + 1
        state["_ratchet_iteration"] = iteration

        baseline_key = (step.input_keys or ["baseline_score"])[0]
        new_key = (step.input_keys or [None, "new_score"])[1] \
            if len(step.input_keys or []) > 1 else "new_score"
        baseline = state.get(baseline_key)
        new = state.get(new_key)

        # ── CP6 comparability gate (checklists 6.15 / 6.26) ──────────────────
        # Run BEFORE any arithmetic: a score measured with a different ruler,
        # or one produced by a misconfigured extractor, is not a smaller
        # number — it is a meaningless one.
        baseline_detail = state.get(f"{baseline_key}_detail")
        new_detail = state.get(f"{new_key}_detail")
        incomparable_reason = comparability_reason(baseline_detail, new_detail)
        unreliable = unreliable_reason(baseline_detail, new_detail)

        # ── keep / revert, decided on HOLDOUT where available (§6.5.1) ───────
        baseline_basis, new_basis, decision_basis = ratchet_basis(
            baseline, new, baseline_detail, new_detail
        )
        delta = None
        if isinstance(baseline_basis, (int, float)) and \
                isinstance(new_basis, (int, float)):
            delta = round(float(new_basis) - float(baseline_basis), 4)
        if incomparable_reason:
            # Treat exactly like a missing score: revert, the CP5 safe default.
            delta = None
            _notify_inbox(
                user_id, kind="grading_mismatch", mode=mode,
                run_id=improve_run_id,
                object_id=state.get("improve_target_id"),
                message=(f"Scores are not comparable, treating the iteration as "
                         f"a revert: {incomparable_reason}"),
            )
        elif unreliable:
            delta = None
            _notify_inbox(
                user_id, kind="grading_unreliable", mode=mode,
                run_id=improve_run_id,
                object_id=state.get("improve_target_id"),
                message=(f"Score is not reliable, treating the iteration as a "
                         f"revert: {unreliable}"),
            )
        # Missing scores are treated as a failed improvement — revert (safe default).
        decision = "keep" if (delta is not None and delta >= threshold) else "revert"

        target_id = state.get("improve_target_id")
        reverted_to = None
        if decision == "revert" and improve_run_id:
            try:
                imp_run = runs_mod.get_run(user_id, improve_run_id)
                target_id = imp_run.get("target_object_id") or target_id
                baseline_n = int(imp_run.get("baseline_version_n") or 1)
                if imp_run.get("new_version_n"):  # only roll back if an apply happened
                    applier.rollback(user_id, target_id, baseline_n)
                    reverted_to = baseline_n
                runs_mod.update_run(user_id, improve_run_id, decision="revert")
                if mode == "autonomous":  # never silent (5.11)
                    _notify_inbox(
                        user_id, kind="revert", mode="autonomous",
                        run_id=improve_run_id, object_id=target_id,
                        version_n=baseline_n, score_delta=delta,
                        message=(f"Autonomous revert: delta "
                                 f"{delta if delta is not None else 'N/A'} < threshold "
                                 f"{threshold} — restored v{baseline_n}"),
                    )
            except (applier.ApplyError, runs_mod.RunNotFound) as e:
                yield {"type": "step_warning", "orch_step_id": step.id,
                       "message": f"Ratchet revert failed: {e}"}
        elif decision == "keep" and improve_run_id:
            try:
                runs_mod.update_run(
                    user_id, improve_run_id,
                    baseline_score=baseline, new_score=new,
                )
            except runs_mod.RunNotFound:
                pass

        consecutive = int(state.get("_ratchet_consecutive_reverts") or 0)
        consecutive = consecutive + 1 if decision == "revert" else 0
        state["_ratchet_consecutive_reverts"] = consecutive

        # ── termination safeguards (5.12–5.16 / 5.22) ────────────────────────
        stop_reason = None
        if iteration >= max_iterations:
            stop_reason = "max_iterations"
            _notify_inbox(
                user_id, kind="max_iterations_stop", mode=mode,
                run_id=improve_run_id, object_id=target_id,
                message=f"Ratchet stopped: iteration cap "
                        f"({max_iterations}) reached",
            )
        elif consecutive >= plateau_patience:
            stop_reason = "plateau"
            _notify_inbox(
                user_id, kind="plateau_stop", mode=mode,
                run_id=improve_run_id, object_id=target_id, score_delta=delta,
                message=f"Ratchet stopped: plateau — {consecutive} consecutive revert(s)",
            )
        else:
            timeout_seconds = (engine.orch.timeout_minutes or 60) * 60
            budget = _budget(step, run)
            if _elapsed_seconds(run) > RATCHET_TIMEOUT_FRACTION * timeout_seconds:
                stop_reason = "timeout"
                _notify_inbox(
                    user_id, kind="timeout_stop", mode=mode,
                    run_id=improve_run_id, object_id=target_id,
                    message=(f"Ratchet stopped: >{int(RATCHET_TIMEOUT_FRACTION * 100)}% "
                             f"of the {engine.orch.timeout_minutes}-min global "
                             "timeout consumed"),
                )
            elif budget is not None and _spent_usd(run.run_id) >= budget:
                stop_reason = "budget"
                _notify_inbox(
                    user_id, kind="budget_abort", mode=mode,
                    run_id=improve_run_id, object_id=target_id,
                    message=(f"Ratchet stopped: LLM budget ${budget:.4f} exceeded "
                             f"(spent ${_spent_usd(run.run_id):.4f})"),
                )

        state["ratchet_decision"] = decision
        state["ratchet_stop"] = stop_reason is not None
        state["ratchet_stop_reason"] = stop_reason
        state["ratchet_incomparable_reason"] = incomparable_reason or unreliable
        state["ratchet_decision_basis"] = decision_basis
        if step.output_key:
            state[step.output_key] = {
                "decision": decision, "delta": delta, "iteration": iteration,
                "stop": stop_reason is not None, "stop_reason": stop_reason,
                "reverted_to_version_n": reverted_to,
                "incomparable_reason": incomparable_reason or unreliable,
                "decision_basis": decision_basis,
            }

        yield {
            "type": "ratchet_decision",
            "orch_step_id": step.id,
            "step_name": step.name,
            "decision": decision,
            "baseline_score": baseline,
            "new_score": new,
            "delta": delta,
            "threshold": threshold,
            "iteration": iteration,
            "consecutive_reverts": consecutive,
            "stop": stop_reason is not None,
            "stop_reason": stop_reason,
            "incomparable_reason": incomparable_reason or unreliable,
            "decision_basis": decision_basis,
            "baseline_holdout": baseline_basis if decision_basis == "holdout" else None,
            "new_holdout": new_basis if decision_basis == "holdout" else None,
        }


# Registered into STEP_EXECUTORS by the single permitted CP5 hook (§0.4).
IMPROVE_STEP_EXECUTORS = {
    "improve_analyze": ImproveAnalyzeStepExecutor(),
    "improve_propose": ImproveProposeStepExecutor(),
    "improve_review": ImproveReviewStepExecutor(),
    "improve_apply": ImproveApplyStepExecutor(),
    "benchmark": BenchmarkStepExecutor(),
    "improve_ratchet_decide": ImproveRatchetDecideStepExecutor(),
}
