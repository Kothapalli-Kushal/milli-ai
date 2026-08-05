"""
Splits, k-fold partitioning, and fold rotation (Checkpoint 6, §6.5.1-§6.5.2).

Every benchmark input carries a split:

  train       the ONLY inputs whose failures are visible to the tuner
  holdout     what the ratchet decides on
  regression  never used for optimization; a fixed set that must not degrade,
              reported separately

The single most valuable property this unlocks is auto-revert when the holdout
regresses **even though train improved** — that is the signature of the tuner
memorizing the train split rather than making the agent better, and it must be
enforced in `IMPROVE_RATCHET_DECIDE`, not merely reported.

MATERIALIZATION IS MANDATORY. Split and fold assignment is written into the
benchmark file and never recomputed at run time. A split that moves between
runs makes baseline and new scores incomparable and destroys the ratchet's
meaning, so `random` and `kfold` assign once, at an explicit authoring action,
and freeze. Two runs with the same seed produce identical assignments.
"""
from __future__ import annotations

import random
from statistics import pstdev

SPLITS = ("train", "holdout", "regression")
DEFAULT_RATIOS = {"train": 0.6, "holdout": 0.3, "regression": 0.1}


class SplitPolicyError(Exception):
    """The split policy cannot be applied to this benchmark."""


def _ordered_ids(inputs: list[dict]) -> list[str]:
    """Assignment order is the file order, not a set iteration order.

    Determinism depends on this: a seeded shuffle over an unordered collection
    is not reproducible.
    """
    return [str(item.get("id") or f"in_{i + 1:03d}") for i, item in enumerate(inputs)]


def _parents_only(inputs: list[dict]) -> list[dict]:
    """Augmented variants are never assigned independently — they INHERIT.

    A paraphrase landing in holdout while its parent is in train is train/test
    leakage and makes the holdout score meaningless (§6.5.3).
    """
    return [item for item in inputs if not item.get("is_augmented")]


# ── split assignment ─────────────────────────────────────────────────────────

def assign_random(inputs: list[dict], seed: int, ratios: dict | None) -> None:
    """Seeded shuffle into train/holdout/regression, mutating `inputs`.

    `regression`-declared inputs are NEVER reassigned: a regression set that
    drifts is not a regression set.
    """
    ratios = {**DEFAULT_RATIOS, **(ratios or {})}
    total = sum(float(ratios.get(s, 0.0)) for s in SPLITS)
    if total <= 0:
        raise SplitPolicyError("split ratios must sum to a positive number")

    pool = [
        item for item in _parents_only(inputs)
        if item.get("split") != "regression"
    ]
    order = list(range(len(pool)))
    random.Random(int(seed)).shuffle(order)

    # Only train/holdout are drawn from the pool; declared regression stays put.
    train_share = float(ratios.get("train", 0.0))
    holdout_share = float(ratios.get("holdout", 0.0))
    denominator = train_share + holdout_share
    if denominator <= 0:
        raise SplitPolicyError(
            "split ratios must give positive weight to train or holdout"
        )
    train_count = round(len(pool) * train_share / denominator)
    # A holdout of zero would leave the ratchet with nothing to decide on.
    if len(pool) >= 2:
        train_count = min(max(train_count, 1), len(pool) - 1)

    for rank, index in enumerate(order):
        pool[index]["split"] = "train" if rank < train_count else "holdout"

    inherit_from_parents(inputs)


def assign_kfold(inputs: list[dict], seed: int, k: int) -> None:
    """Partition non-regression inputs into `k` folds by seeded shuffle.

    Fold `i` is the holdout; the remainder is train. The assignment is stored
    as `fold: <int>` on each input and materialized into the file.
    """
    k = int(k)
    if k < 2:
        raise SplitPolicyError(f"kfold requires k >= 2 (got {k})")
    pool = [
        item for item in _parents_only(inputs)
        if item.get("split") != "regression"
    ]
    if len(pool) < k:
        raise SplitPolicyError(
            f"kfold k={k} needs at least {k} non-regression inputs "
            f"(this benchmark has {len(pool)})"
        )
    order = list(range(len(pool)))
    random.Random(int(seed)).shuffle(order)
    for rank, index in enumerate(order):
        pool[index]["fold"] = rank % k

    inherit_from_parents(inputs)


def inherit_from_parents(inputs: list[dict]) -> list[str]:
    """Force every augmented variant onto its parent's split and fold.

    Enforced in code, not in documentation (§6.5.3). Returns the ids of the
    variants that were corrected, so callers can report leakage that was about
    to happen.
    """
    by_id = {str(item.get("id")): item for item in inputs}
    corrected: list[str] = []
    for item in inputs:
        if not item.get("is_augmented"):
            continue
        parent = by_id.get(str(item.get("parent_input_id") or ""))
        if parent is None:
            continue
        if item.get("split") != parent.get("split") or \
                item.get("fold") != parent.get("fold"):
            corrected.append(str(item.get("id")))
        item["split"] = parent.get("split", "train")
        item["fold"] = parent.get("fold")
    return corrected


def materialize(benchmark: dict) -> dict:
    """Apply the benchmark's `split_policy`, mutating its inputs in place.

    Returns {"mode", "seed", "assignments", "corrected_variants"}. `explicit`
    honors each input's declared split and only enforces variant inheritance.
    """
    policy = benchmark.get("split_policy") or {}
    mode = str(policy.get("mode") or "explicit")
    seed = int(policy.get("seed", 1337))
    inputs = benchmark.get("inputs") or []

    if mode == "random":
        assign_random(inputs, seed, policy.get("ratios"))
        corrected = inherit_from_parents(inputs)
    elif mode == "kfold":
        kfold = policy.get("kfold") or {}
        assign_kfold(inputs, seed, int(kfold.get("k", 5)))
        corrected = inherit_from_parents(inputs)
    elif mode == "explicit":
        corrected = inherit_from_parents(inputs)
    else:
        raise SplitPolicyError(f"unknown split mode '{mode}'")

    return {
        "mode": mode,
        "seed": seed,
        "corrected_variants": corrected,
        "assignments": {
            str(item.get("id")): {"split": item.get("split"),
                                  "fold": item.get("fold")}
            for item in inputs
        },
    }


# ── fold rotation (§6.5.2) ───────────────────────────────────────────────────

def active_fold(benchmark: dict, iteration: int) -> int | None:
    """The fold acting as holdout for this iteration, or None when not k-fold.

    `per_iteration` advances by iteration (`fold = iteration % k`): cheap, and
    an agent that overfits to one fold's phrasing is caught within `k`
    iterations.
    """
    policy = benchmark.get("split_policy") or {}
    if str(policy.get("mode")) != "kfold":
        return None
    kfold = policy.get("kfold") or {}
    if str(kfold.get("rotation") or "per_iteration") != "per_iteration":
        return None
    return int(iteration) % int(kfold.get("k", 5))


def effective_split(item: dict, active: int | None) -> str:
    """The split an input occupies for THIS evaluation.

    Under k-fold the declared `split` is overridden by the fold rotation:
    fold == active is the holdout, everything else is train. Regression is
    never touched — it exists precisely so that something stays fixed.
    """
    declared = str(item.get("split") or "train")
    if declared == "regression" or active is None or item.get("fold") is None:
        return declared
    return "holdout" if int(item["fold"]) == int(active) else "train"


def fold_ids(benchmark: dict) -> list[int]:
    policy = benchmark.get("split_policy") or {}
    if str(policy.get("mode")) != "kfold":
        return []
    k = int((policy.get("kfold") or {}).get("k", 5))
    return list(range(k))


def rotation_mode(benchmark: dict) -> str | None:
    policy = benchmark.get("split_policy") or {}
    if str(policy.get("mode")) != "kfold":
        return None
    return str((policy.get("kfold") or {}).get("rotation") or "per_iteration")


# ── per-split / per-fold scoring ─────────────────────────────────────────────

def scores_by_split(outcomes: list[dict], weights: dict[str, float]) -> dict:
    """Weighted outcome score per split. Reported separately, always.

    A split with no gradeable input reports None rather than 0 — the same rule
    as the outcome axis itself.
    """
    out: dict[str, float | None] = {}
    for split in SPLITS:
        num = den = 0.0
        for outcome in outcomes:
            if outcome.get("effective_split", outcome.get("split")) != split:
                continue
            if outcome.get("score") is None:
                continue
            weight = float(weights.get(outcome["input_id"], 1.0))
            num += weight * float(outcome["score"])
            den += weight
        out[split] = round(num / den, 6) if den > 0 else None
    return out


def scores_across_folds(
    outcomes: list[dict], weights: dict[str, float], folds: list[int]
) -> dict:
    """Every fold's holdout score from a SINGLE execution pass.

    Each fold's score is the weighted mean of the outcomes assigned to it; the
    reported outcome score is the mean across folds, with the standard
    deviation of the per-fold scores as the dispersion signal.
    """
    per_fold: list[float | None] = []
    for fold in folds:
        num = den = 0.0
        for outcome in outcomes:
            if outcome.get("fold") is None or int(outcome["fold"]) != int(fold):
                continue
            if outcome.get("score") is None:
                continue
            weight = float(weights.get(outcome["input_id"], 1.0))
            num += weight * float(outcome["score"])
            den += weight
        per_fold.append(round(num / den, 6) if den > 0 else None)

    graded = [s for s in per_fold if s is not None]
    return {
        "scores_by_fold": per_fold,
        "fold_mean": round(sum(graded) / len(graded), 6) if graded else None,
        "fold_stddev": round(pstdev(graded), 6) if len(graded) > 1 else 0.0,
    }
