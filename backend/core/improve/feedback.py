"""
Outcome feedback for the tuner (Checkpoint 6, §6.7).

The tuner may see WHICH checks failed and HOW they failed. It may never see
WHAT the right answer was.

The reason is direct: leak the expected values and the tuner writes "if asked
about regions, answer APAC" into the system prompt. The benchmark score goes
up, the agent gets worse, and every downstream signal in the subsystem is now
lying.

Permitted:
- failing `check_id` / `criterion_id` values, with their weights
- aggregate failure rates per check across the train split
- `status` per check (pass / fail / extraction_failed / ...)
- which extractor failed and what it was looking for (tool name, arg name) —
  WITHOUT the expected value
- judge justification text for rubric criteria (which describes the deficiency,
  not the answer)
- evidence pointers `(trace_file, message_idx)`

Forbidden, enforced BY CONSTRUCTION:
- any `expected.value` payload
- any `key_points[].text`
- any `reference_output`
- any content from `holdout` or `regression` inputs, in any form

This module is an explicit ALLOW-LIST SERIALIZER: it builds the payload field
by field from a whitelist. It never serializes an input object and redacts.
Redaction fails open — one new field in the expectation schema and the secret
ships. Construction fails closed: a field nobody wrote code for simply is not
in the output.
"""
from __future__ import annotations

# The ONLY keys this module will ever emit for a check. Adding a key here is a
# deliberate act that should be reviewed against §6.7.
CHECK_FIELDS = (
    "check_id", "status", "weight", "critical",
    "extractor_from", "extractor_tool", "extractor_arg",
    "comparator_type", "judge_justification",
    "trace_file", "message_idx",
)

# Detail strings can quote the expected value back (e.g. "expected exact match,
# got 'EMEA'"), so `detail` is NOT in the allow-list. Only the judge's
# justification for rubric criteria is admitted, and only for rubric kinds,
# because it describes the deficiency rather than the answer.
JUDGE_JUSTIFICATION_KINDS = {"key_point_coverage", "anchored"}

# Only train-split inputs are ever visible. Holdout is what the ratchet decides
# on; showing it to the optimizer is the definition of train/test leakage.
VISIBLE_SPLITS = {"train"}


def build_outcome_feedback(
    per_input: list[dict],
    *,
    benchmark_id: str | None = None,
    max_inputs: int = 25,
    max_checks_per_input: int = 12,
) -> dict | None:
    """Assemble the tuner-visible outcome feedback block, or None if empty.

    `per_input` is the list of `InputOutcome` dicts from a benchmark result.
    Every field in the output is written explicitly below; nothing is copied
    wholesale from the source objects.
    """
    visible = [
        outcome for outcome in (per_input or [])
        if str(
            outcome.get("effective_split", outcome.get("split")) or "train"
        ) in VISIBLE_SPLITS
    ]
    if not visible:
        return None

    inputs: list[dict] = []
    for outcome in visible[:max_inputs]:
        checks = [
            _serialize_check(check)
            for check in (outcome.get("checks") or [])[:max_checks_per_input]
        ]
        inputs.append({
            "input_id": str(outcome.get("input_id") or ""),
            "score": outcome.get("score"),
            "na_reason": outcome.get("na_reason"),
            "vetoed": bool(outcome.get("vetoed")),
            "checks": [c for c in checks if c is not None],
        })

    return {
        "benchmark_id": benchmark_id,
        "note": (
            "Outcome grading results for the TRAIN split only. Check ids and "
            "failure modes are shown; expected answers deliberately are not."
        ),
        "failure_rates": _failure_rates(visible),
        "inputs": inputs,
    }


def _serialize_check(check: dict) -> dict | None:
    """Field-by-field construction from the whitelist. Never a dict copy."""
    if not isinstance(check, dict):
        return None
    out: dict = {
        "check_id": str(check.get("check_id") or ""),
        "status": str(check.get("status") or ""),
        "weight": float(check.get("weight", 1.0)),
        "critical": bool(check.get("critical", False)),
    }

    # What the extractor was looking for — tool and arg NAMES only, never the
    # value it was compared against.
    extract = check.get("_extract_spec") or {}
    if isinstance(extract, dict):
        if extract.get("from"):
            out["extractor_from"] = str(extract["from"])
        if extract.get("tool"):
            out["extractor_tool"] = str(extract["tool"])
        if extract.get("arg"):
            out["extractor_arg"] = str(extract["arg"])

    comparator = check.get("_comparator_type")
    if comparator:
        out["comparator_type"] = str(comparator)

    if check.get("_criterion_kind") in JUDGE_JUSTIFICATION_KINDS:
        justification = check.get("judge_justification")
        if justification:
            out["judge_justification"] = str(justification)[:400]

    # CP2/CP3 evidence-first rule applies unchanged.
    if check.get("trace_file"):
        out["trace_file"] = str(check["trace_file"])
    if check.get("message_idx") is not None:
        out["message_idx"] = int(check["message_idx"])

    return {k: v for k, v in out.items() if k in CHECK_FIELDS}


def _failure_rates(outcomes: list[dict]) -> list[dict]:
    """Aggregate failure rate per check id across the visible (train) split."""
    tally: dict[str, dict] = {}
    for outcome in outcomes:
        for check in outcome.get("checks") or []:
            check_id = str(check.get("check_id") or "")
            if not check_id:
                continue
            row = tally.setdefault(
                check_id,
                {"check_id": check_id, "total": 0, "failed": 0,
                 "extraction_failed": 0},
            )
            row["total"] += 1
            status = str(check.get("status") or "")
            if status == "extraction_failed":
                row["extraction_failed"] += 1
            elif status != "pass":
                row["failed"] += 1

    rows = []
    for row in sorted(tally.values(), key=lambda r: r["check_id"]):
        total = row["total"] or 1
        rows.append({
            "check_id": row["check_id"],
            "failure_rate": round(row["failed"] / total, 4),
            "extraction_failed_rate": round(row["extraction_failed"] / total, 4),
            "observations": row["total"],
        })
    return rows


# ── annotation helper ────────────────────────────────────────────────────────

def annotate_checks(outcome: dict, expected: dict, rubric: dict | None = None) -> dict:
    """Attach the underscore-prefixed spec hints the serializer reads.

    These live on the in-memory result only. They carry extractor/comparator
    NAMES, never values — so even the annotation step cannot introduce a leak.
    """
    by_id = {
        str(check.get("id")): check for check in (expected or {}).get("checks") or []
    }
    kinds = {
        str(c.get("id")): str(c.get("kind") or "")
        for c in (rubric or {}).get("criteria") or []
    }
    for check in outcome.get("checks") or []:
        spec = by_id.get(str(check.get("check_id"))) or {}
        extract = spec.get("extract") or {}
        check["_extract_spec"] = {
            "from": extract.get("from"),
            "tool": extract.get("tool"),
            "arg": extract.get("arg"),
        }
        check["_comparator_type"] = (spec.get("compare") or {}).get("type")
        check["_criterion_kind"] = kinds.get(str(check.get("check_id")))
    return outcome
