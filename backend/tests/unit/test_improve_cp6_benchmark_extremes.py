"""
Checkpoint-6 verification (unit): extreme-case coverage for the benchmarking
surface (scoring, split assignment, fold math, and ratchet comparability).

This file intentionally packs many parameterized boundary scenarios so we get
wide coverage quickly without introducing new runtime dependencies.
"""

import pytest

from core.improve.grading import aggregate_outcomes, composite_score
from core.improve.splits import (
    SplitPolicyError,
    active_fold,
    assign_kfold,
    assign_random,
    effective_split,
    fold_ids,
    inherit_from_parents,
    materialize,
    rotation_mode,
    scores_across_folds,
    scores_by_split,
)
from core.improve.steps import comparability_reason, ratchet_basis, unreliable_reason


def _outcome(input_id, score, statuses, split="train", fold=None):
    return {
        "input_id": input_id,
        "score": score,
        "split": split,
        "fold": fold,
        "checks": [{"status": s} for s in statuses],
    }


COMPOSITE_CASES = [
    (1.0, 1.0, 1.0, 1.0, 1.0),
    (1.0, 0.0, 1.0, 1.0, 0.5),
    (0.0, 1.0, 1.0, 1.0, 0.5),
    (0.73, 0.11, 1.0, 0.0, 0.73),
    (0.73, 0.11, 0.0, 1.0, 0.11),
    (None, 0.66, 1.0, 1.0, 0.66),
    (0.66, None, 1.0, 1.0, 0.66),
    (None, None, 1.0, 1.0, None),
    (0.9, 0.1, 3.0, 1.0, 0.7),
    (0.9, 0.1, 1.0, 3.0, 0.3),
    (0.125, 0.875, 100.0, 100.0, 0.5),
    (0.125, 0.875, 1000.0, 1.0, round((1000.0 * 0.125 + 1.0 * 0.875) / 1001.0, 6)),
    (0.333333, 0.666667, 2.0, 1.0, 0.444444),
    (0.333333, 0.666667, 1.0, 2.0, 0.555556),
    (0.2, 0.8, -1.0, 1.0, 0.8),
    (0.2, 0.8, 1.0, -1.0, 0.2),
    (0.2, 0.8, -1.0, -1.0, None),
    (0.0, 0.0, 1.0, 1.0, 0.0),
    (1.0, 1.0, 0.0, 0.0, None),
    (0.777777, 0.123456, 7.0, 5.0, round((7.0 * 0.777777 + 5.0 * 0.123456) / 12.0, 6)),
]


@pytest.mark.parametrize(
    "process_score,outcome_score,process_weight,outcome_weight,expected",
    COMPOSITE_CASES,
)
def test_composite_score_extremes(
    process_score, outcome_score, process_weight, outcome_weight, expected
):
    assert composite_score(
        process_score, outcome_score, process_weight, outcome_weight
    ) == expected


AGGREGATE_CASES = [
    {
        "id": "empty_outcomes",
        "outcomes": [],
        "weights": {},
        "expected": {
            "outcome_score": None,
            "outcome_na": True,
            "graded_input_count": 0,
            "na_input_count": 0,
            "extraction_failed_count": 0,
            "extraction_failed_rate": 0.0,
        },
    },
    {
        "id": "all_na_extraction_failed",
        "outcomes": [
            _outcome("in_1", None, ["extraction_failed"]),
            _outcome("in_2", None, ["extraction_failed", "extraction_failed"]),
        ],
        "weights": {},
        "expected": {
            "outcome_score": None,
            "outcome_na": True,
            "graded_input_count": 0,
            "na_input_count": 2,
            "extraction_failed_count": 3,
            "extraction_failed_rate": 1.0,
        },
    },
    {
        "id": "all_na_judge",
        "outcomes": [
            _outcome("in_1", None, ["judge_na"]),
            _outcome("in_2", None, ["judge_na", "judge_na"]),
        ],
        "weights": {},
        "expected": {
            "outcome_score": None,
            "outcome_na": True,
            "graded_input_count": 0,
            "na_input_count": 2,
            "extraction_failed_count": 0,
            "extraction_failed_rate": 0.0,
        },
    },
    {
        "id": "uniform_weights_mixed",
        "outcomes": [
            _outcome("in_1", 1.0, ["pass"]),
            _outcome("in_2", 0.0, ["fail"]),
            _outcome("in_3", None, ["extraction_failed"]),
        ],
        "weights": {},
        "expected": {
            "outcome_score": 0.5,
            "outcome_na": False,
            "graded_input_count": 2,
            "na_input_count": 1,
            "extraction_failed_count": 1,
            "extraction_failed_rate": round(1 / 3, 6),
        },
    },
    {
        "id": "custom_weights_skewed",
        "outcomes": [
            _outcome("in_1", 1.0, ["pass"]),
            _outcome("in_2", 0.0, ["fail"]),
            _outcome("in_3", 1.0, ["pass"]),
        ],
        "weights": {"in_1": 4.0, "in_2": 1.0, "in_3": 1.0},
        "expected": {
            "outcome_score": round((4.0 + 0.0 + 1.0) / 6.0, 6),
            "outcome_na": False,
            "graded_input_count": 3,
            "na_input_count": 0,
            "extraction_failed_count": 0,
            "extraction_failed_rate": 0.0,
        },
    },
    {
        "id": "weighted_input_ignored_when_na",
        "outcomes": [
            _outcome("in_1", 0.25, ["pass"]),
            _outcome("in_2", None, ["judge_na"]),
        ],
        "weights": {"in_1": 1.0, "in_2": 999.0},
        "expected": {
            "outcome_score": 0.25,
            "outcome_na": False,
            "graded_input_count": 1,
            "na_input_count": 1,
            "extraction_failed_count": 0,
            "extraction_failed_rate": 0.0,
        },
    },
    {
        "id": "all_weights_zero_but_scores_present",
        "outcomes": [
            _outcome("in_1", 0.9, ["pass"]),
            _outcome("in_2", 0.1, ["pass"]),
        ],
        "weights": {"in_1": 0.0, "in_2": 0.0},
        "expected": {
            "outcome_score": None,
            "outcome_na": True,
            "graded_input_count": 2,
            "na_input_count": 0,
            "extraction_failed_count": 0,
            "extraction_failed_rate": 0.0,
        },
    },
    {
        "id": "default_weight_fallback",
        "outcomes": [
            _outcome("in_1", 0.2, ["pass"]),
            _outcome("in_2", 0.8, ["pass"]),
        ],
        "weights": {"in_1": 3.0},
        "expected": {
            "outcome_score": round((3.0 * 0.2 + 1.0 * 0.8) / 4.0, 6),
            "outcome_na": False,
            "graded_input_count": 2,
            "na_input_count": 0,
            "extraction_failed_count": 0,
            "extraction_failed_rate": 0.0,
        },
    },
    {
        "id": "extraction_failure_rate_counts_all_checks",
        "outcomes": [
            _outcome("in_1", 1.0, ["pass", "extraction_failed", "pass"]),
            _outcome("in_2", 0.0, ["fail", "extraction_failed"]),
        ],
        "weights": {},
        "expected": {
            "outcome_score": 0.5,
            "outcome_na": False,
            "graded_input_count": 2,
            "na_input_count": 0,
            "extraction_failed_count": 2,
            "extraction_failed_rate": 0.4,
        },
    },
    {
        "id": "rounded_score_to_six_decimals",
        "outcomes": [
            _outcome("in_1", 1.0 / 3.0, ["pass"]),
            _outcome("in_2", 2.0 / 3.0, ["pass"]),
        ],
        "weights": {"in_1": 1.0, "in_2": 2.0},
        "expected": {
            "outcome_score": round((1.0 / 3.0 + 4.0 / 3.0) / 3.0, 6),
            "outcome_na": False,
            "graded_input_count": 2,
            "na_input_count": 0,
            "extraction_failed_count": 0,
            "extraction_failed_rate": 0.0,
        },
    },
    {
        "id": "na_and_scored_mix",
        "outcomes": [
            _outcome("in_1", None, ["judge_na"]),
            _outcome("in_2", 0.5, ["pass"]),
            _outcome("in_3", None, ["extraction_failed"]),
        ],
        "weights": {"in_2": 10.0},
        "expected": {
            "outcome_score": 0.5,
            "outcome_na": False,
            "graded_input_count": 1,
            "na_input_count": 2,
            "extraction_failed_count": 1,
            "extraction_failed_rate": round(1 / 3, 6),
        },
    },
    {
        "id": "no_checks_means_zero_failure_rate",
        "outcomes": [
            {"input_id": "in_1", "score": 1.0, "checks": []},
            {"input_id": "in_2", "score": None, "checks": []},
        ],
        "weights": {},
        "expected": {
            "outcome_score": 1.0,
            "outcome_na": False,
            "graded_input_count": 1,
            "na_input_count": 1,
            "extraction_failed_count": 0,
            "extraction_failed_rate": 0.0,
        },
    },
]


@pytest.mark.parametrize("case", AGGREGATE_CASES, ids=[c["id"] for c in AGGREGATE_CASES])
def test_aggregate_outcomes_extremes(case):
    assert aggregate_outcomes(case["outcomes"], case["weights"]) == case["expected"]


def _parent_child_inputs():
    return [
        {"id": "in_001", "split": "train", "fold": 0, "is_augmented": False},
        {"id": "in_002", "split": "holdout", "fold": 1, "is_augmented": False},
        {
            "id": "in_001__aug1",
            "split": "holdout",
            "fold": 9,
            "is_augmented": True,
            "parent_input_id": "in_001",
        },
    ]


def test_inherit_from_parents_corrects_variant_split_and_fold():
    inputs = _parent_child_inputs()
    corrected = inherit_from_parents(inputs)
    assert corrected == ["in_001__aug1"]
    child = next(i for i in inputs if i["id"] == "in_001__aug1")
    assert child["split"] == "train"
    assert child["fold"] == 0


def test_inherit_from_parents_ignores_orphan_variant():
    inputs = [{
        "id": "in_orphan__aug1",
        "split": "holdout",
        "fold": 1,
        "is_augmented": True,
        "parent_input_id": "missing_parent",
    }]
    corrected = inherit_from_parents(inputs)
    assert corrected == []
    assert inputs[0]["split"] == "holdout"
    assert inputs[0]["fold"] == 1


def test_assign_random_is_deterministic_for_same_seed():
    inputs_a = [
        {"id": f"in_{i:03d}", "split": "train", "is_augmented": False}
        for i in range(1, 9)
    ]
    inputs_b = [dict(item) for item in inputs_a]

    assign_random(inputs_a, seed=1337, ratios={"train": 0.6, "holdout": 0.4})
    assign_random(inputs_b, seed=1337, ratios={"train": 0.6, "holdout": 0.4})

    assert [i["split"] for i in inputs_a] == [i["split"] for i in inputs_b]


def test_assign_random_with_different_seed_changes_assignment():
    inputs_a = [
        {"id": f"in_{i:03d}", "split": "train", "is_augmented": False}
        for i in range(1, 9)
    ]
    inputs_b = [dict(item) for item in inputs_a]

    assign_random(inputs_a, seed=1, ratios={"train": 0.5, "holdout": 0.5})
    assign_random(inputs_b, seed=999, ratios={"train": 0.5, "holdout": 0.5})

    assert [i["split"] for i in inputs_a] != [i["split"] for i in inputs_b]


def test_assign_random_never_reassigns_declared_regression_inputs():
    inputs = [
        {"id": "in_train_1", "split": "train", "is_augmented": False},
        {"id": "in_train_2", "split": "train", "is_augmented": False},
        {"id": "in_reg_1", "split": "regression", "is_augmented": False},
    ]
    assign_random(inputs, seed=7, ratios={"train": 0.5, "holdout": 0.5})
    assert next(i for i in inputs if i["id"] == "in_reg_1")["split"] == "regression"


def test_assign_random_forces_train_and_holdout_when_pool_at_least_two():
    inputs = [
        {"id": "in_1", "split": "train", "is_augmented": False},
        {"id": "in_2", "split": "train", "is_augmented": False},
    ]
    assign_random(inputs, seed=7, ratios={"train": 1.0, "holdout": 0.0})
    splits = {i["split"] for i in inputs}
    assert splits == {"train", "holdout"}


@pytest.mark.parametrize(
    "ratios",
    [
        {},
        {"train": 0.0, "holdout": 0.0, "regression": 0.0},
        {"train": -1.0, "holdout": 0.0},
    ],
)
def test_assign_random_invalid_total_ratio_raises(ratios):
    inputs = [{"id": "in_1", "split": "train", "is_augmented": False}]
    with pytest.raises(SplitPolicyError, match="sum to a positive"):
        assign_random(inputs, seed=5, ratios=ratios)


@pytest.mark.parametrize(
    "ratios",
    [
        {"train": 0.0, "holdout": 0.0, "regression": 1.0},
        {"train": 0.0, "holdout": 0.0},
    ],
)
def test_assign_random_requires_train_or_holdout_weight(ratios):
    inputs = [
        {"id": "in_1", "split": "train", "is_augmented": False},
        {"id": "in_2", "split": "train", "is_augmented": False},
    ]
    with pytest.raises(SplitPolicyError, match="positive weight to train or holdout"):
        assign_random(inputs, seed=5, ratios=ratios)


@pytest.mark.parametrize("k", [0, 1, -3])
def test_assign_kfold_rejects_k_below_two(k):
    inputs = [
        {"id": "in_1", "split": "train", "is_augmented": False},
        {"id": "in_2", "split": "holdout", "is_augmented": False},
    ]
    with pytest.raises(SplitPolicyError, match="k >= 2"):
        assign_kfold(inputs, seed=123, k=k)


@pytest.mark.parametrize("k", [3, 4, 8])
def test_assign_kfold_rejects_when_non_regression_pool_too_small(k):
    inputs = [
        {"id": "in_1", "split": "train", "is_augmented": False},
        {"id": "in_2", "split": "regression", "is_augmented": False},
    ]
    with pytest.raises(SplitPolicyError, match="needs at least"):
        assign_kfold(inputs, seed=123, k=k)


def test_assign_kfold_materializes_fold_in_range():
    inputs = [
        {"id": f"in_{i:03d}", "split": "train", "is_augmented": False}
        for i in range(1, 11)
    ]
    assign_kfold(inputs, seed=42, k=4)
    folds = {i.get("fold") for i in inputs}
    assert folds.issubset({0, 1, 2, 3})
    assert len(folds) == 4


def test_materialize_explicit_only_enforces_variant_inheritance():
    suite = {
        "split_policy": {"mode": "explicit", "seed": 17},
        "inputs": _parent_child_inputs(),
    }
    out = materialize(suite)
    assert out["mode"] == "explicit"
    assert out["seed"] == 17
    assert out["corrected_variants"] == ["in_001__aug1"]


@pytest.mark.parametrize(
    "mode",
    ["random", "kfold"],
)
def test_materialize_records_assignments_for_each_mode(mode):
    inputs = [
        {"id": f"in_{i:03d}", "split": "train", "is_augmented": False}
        for i in range(1, 7)
    ]
    policy = {"mode": mode, "seed": 11}
    if mode == "random":
        policy["ratios"] = {"train": 0.5, "holdout": 0.5}
    else:
        policy["kfold"] = {"k": 3, "rotation": "per_iteration"}

    out = materialize({"split_policy": policy, "inputs": inputs})
    assert set(out["assignments"]) == {i["id"] for i in inputs}


def test_materialize_unknown_mode_raises():
    suite = {
        "split_policy": {"mode": "mystery", "seed": 1},
        "inputs": [{"id": "in_1", "split": "train", "is_augmented": False}],
    }
    with pytest.raises(SplitPolicyError, match="unknown split mode"):
        materialize(suite)


@pytest.mark.parametrize(
    "policy,iteration,expected",
    [
        ({"mode": "explicit", "seed": 1}, 3, None),
        ({"mode": "kfold", "seed": 1, "kfold": {"k": 5, "rotation": "all_folds"}}, 3, None),
        ({"mode": "kfold", "seed": 1, "kfold": {"k": 5, "rotation": "per_iteration"}}, 0, 0),
        ({"mode": "kfold", "seed": 1, "kfold": {"k": 5, "rotation": "per_iteration"}}, 6, 1),
    ],
)
def test_active_fold_extremes(policy, iteration, expected):
    assert active_fold({"split_policy": policy}, iteration) == expected


@pytest.mark.parametrize(
    "item,active,expected",
    [
        ({"split": "train", "fold": 2}, 2, "holdout"),
        ({"split": "train", "fold": 1}, 2, "train"),
        ({"split": "holdout", "fold": 1}, 2, "train"),
        ({"split": "regression", "fold": 1}, 1, "regression"),
        ({"split": "train", "fold": None}, 1, "train"),
        ({"split": "holdout", "fold": 2}, None, "holdout"),
    ],
)
def test_effective_split_extremes(item, active, expected):
    assert effective_split(item, active) == expected


@pytest.mark.parametrize(
    "benchmark,expected_ids,expected_rotation",
    [
        ({"split_policy": {"mode": "explicit", "seed": 1}}, [], None),
        ({"split_policy": {"mode": "kfold", "seed": 1, "kfold": {"k": 3}}}, [0, 1, 2], "per_iteration"),
        ({"split_policy": {"mode": "kfold", "seed": 1, "kfold": {"k": 4, "rotation": "all_folds"}}}, [0, 1, 2, 3], "all_folds"),
    ],
)
def test_fold_metadata_helpers(benchmark, expected_ids, expected_rotation):
    assert fold_ids(benchmark) == expected_ids
    assert rotation_mode(benchmark) == expected_rotation


SCORES_BY_SPLIT_CASES = [
    {
        "id": "all_three_splits_present",
        "outcomes": [
            _outcome("in_1", 1.0, ["pass"], split="train"),
            _outcome("in_2", 0.0, ["fail"], split="holdout"),
            _outcome("in_3", 0.5, ["pass"], split="regression"),
        ],
        "weights": {},
        "expected": {"train": 1.0, "holdout": 0.0, "regression": 0.5},
    },
    {
        "id": "na_is_excluded_from_split_denominator",
        "outcomes": [
            _outcome("in_1", None, ["judge_na"], split="train"),
            _outcome("in_2", 0.25, ["pass"], split="train"),
        ],
        "weights": {},
        "expected": {"train": 0.25, "holdout": None, "regression": None},
    },
    {
        "id": "custom_weights_by_split",
        "outcomes": [
            _outcome("in_1", 1.0, ["pass"], split="holdout"),
            _outcome("in_2", 0.0, ["fail"], split="holdout"),
        ],
        "weights": {"in_1": 3.0, "in_2": 1.0},
        "expected": {"train": None, "holdout": 0.75, "regression": None},
    },
    {
        "id": "effective_split_overrides_declared_split",
        "outcomes": [
            {
                "input_id": "in_1",
                "score": 1.0,
                "split": "holdout",
                "effective_split": "train",
                "checks": [{"status": "pass"}],
            }
        ],
        "weights": {},
        "expected": {"train": 1.0, "holdout": None, "regression": None},
    },
    {
        "id": "default_weight_is_one",
        "outcomes": [
            _outcome("in_1", 0.2, ["pass"], split="train"),
            _outcome("in_2", 0.8, ["pass"], split="train"),
        ],
        "weights": {"in_1": 4.0},
        "expected": {
            "train": round((4.0 * 0.2 + 1.0 * 0.8) / 5.0, 6),
            "holdout": None,
            "regression": None,
        },
    },
    {
        "id": "all_na_train_returns_none_for_train",
        "outcomes": [
            _outcome("in_1", None, ["extraction_failed"], split="train"),
            _outcome("in_2", None, ["judge_na"], split="train"),
        ],
        "weights": {},
        "expected": {"train": None, "holdout": None, "regression": None},
    },
    {
        "id": "split_rounding_to_six_decimals",
        "outcomes": [
            _outcome("in_1", 0.3333333, ["pass"], split="holdout"),
            _outcome("in_2", 0.6666667, ["pass"], split="holdout"),
        ],
        "weights": {"in_1": 1.0, "in_2": 2.0},
        "expected": {
            "train": None,
            "holdout": round((0.3333333 + 2 * 0.6666667) / 3.0, 6),
            "regression": None,
        },
    },
    {
        "id": "regression_is_independent",
        "outcomes": [
            _outcome("in_1", 0.2, ["pass"], split="train"),
            _outcome("in_2", 0.9, ["pass"], split="regression"),
        ],
        "weights": {},
        "expected": {"train": 0.2, "holdout": None, "regression": 0.9},
    },
]


@pytest.mark.parametrize(
    "case", SCORES_BY_SPLIT_CASES, ids=[c["id"] for c in SCORES_BY_SPLIT_CASES]
)
def test_scores_by_split_extremes(case):
    assert scores_by_split(case["outcomes"], case["weights"]) == case["expected"]


FOLD_SCORING_CASES = [
    {
        "id": "balanced_two_folds",
        "outcomes": [
            _outcome("in_1", 1.0, ["pass"], fold=0),
            _outcome("in_2", 0.0, ["fail"], fold=1),
        ],
        "weights": {},
        "folds": [0, 1],
        "expected": {
            "scores_by_fold": [1.0, 0.0],
            "fold_mean": 0.5,
            "fold_stddev": 0.5,
        },
    },
    {
        "id": "fold_with_no_gradeable_inputs_is_none",
        "outcomes": [
            _outcome("in_1", None, ["judge_na"], fold=0),
            _outcome("in_2", 0.4, ["pass"], fold=1),
        ],
        "weights": {},
        "folds": [0, 1],
        "expected": {
            "scores_by_fold": [None, 0.4],
            "fold_mean": 0.4,
            "fold_stddev": 0.0,
        },
    },
    {
        "id": "weighted_fold_scores",
        "outcomes": [
            _outcome("in_1", 1.0, ["pass"], fold=0),
            _outcome("in_2", 0.0, ["fail"], fold=0),
            _outcome("in_3", 0.5, ["pass"], fold=1),
        ],
        "weights": {"in_1": 3.0, "in_2": 1.0, "in_3": 2.0},
        "folds": [0, 1],
        "expected": {
            "scores_by_fold": [0.75, 0.5],
            "fold_mean": 0.625,
            "fold_stddev": 0.125,
        },
    },
    {
        "id": "all_folds_na",
        "outcomes": [
            _outcome("in_1", None, ["judge_na"], fold=0),
            _outcome("in_2", None, ["judge_na"], fold=1),
        ],
        "weights": {},
        "folds": [0, 1],
        "expected": {
            "scores_by_fold": [None, None],
            "fold_mean": None,
            "fold_stddev": 0.0,
        },
    },
]


@pytest.mark.parametrize(
    "case", FOLD_SCORING_CASES, ids=[c["id"] for c in FOLD_SCORING_CASES]
)
def test_scores_across_folds_extremes(case):
    assert (
        scores_across_folds(case["outcomes"], case["weights"], case["folds"])
        == case["expected"]
    )


RATCHET_BASIS_CASES = [
    {
        "id": "prefers_holdout_when_both_present",
        "baseline": 0.2,
        "new": 0.9,
        "baseline_detail": {"scores_by_split": {"holdout": 0.4}},
        "new_detail": {"scores_by_split": {"holdout": 0.3}},
        "expected": (0.4, 0.3, "holdout"),
    },
    {
        "id": "falls_back_to_composite_when_baseline_missing_holdout",
        "baseline": 0.2,
        "new": 0.9,
        "baseline_detail": {"scores_by_split": {"train": 1.0}},
        "new_detail": {"scores_by_split": {"holdout": 0.3}},
        "expected": (0.2, 0.9, "composite"),
    },
    {
        "id": "falls_back_when_new_holdout_is_none",
        "baseline": 0.2,
        "new": 0.9,
        "baseline_detail": {"scores_by_split": {"holdout": 0.4}},
        "new_detail": {"scores_by_split": {"holdout": None}},
        "expected": (0.2, 0.9, "composite"),
    },
    {
        "id": "empty_details_fall_back",
        "baseline": 0.2,
        "new": 0.9,
        "baseline_detail": {},
        "new_detail": {},
        "expected": (0.2, 0.9, "composite"),
    },
    {
        "id": "none_details_fall_back",
        "baseline": None,
        "new": None,
        "baseline_detail": None,
        "new_detail": None,
        "expected": (None, None, "composite"),
    },
    {
        "id": "bool_holdout_values_are_numeric_by_current_contract",
        "baseline": 0.2,
        "new": 0.9,
        "baseline_detail": {"scores_by_split": {"holdout": True}},
        "new_detail": {"scores_by_split": {"holdout": False}},
        "expected": (1.0, 0.0, "holdout"),
    },
]


@pytest.mark.parametrize("case", RATCHET_BASIS_CASES, ids=[c["id"] for c in RATCHET_BASIS_CASES])
def test_ratchet_basis_extremes(case):
    assert ratchet_basis(
        case["baseline"], case["new"], case["baseline_detail"], case["new_detail"]
    ) == case["expected"]


COMPARABILITY_CASES = [
    {
        "id": "identical_none_values",
        "baseline": {},
        "new": {},
        "expected": None,
    },
    {
        "id": "identical_mode_and_hash",
        "baseline": {"grading_mode": "rubric", "rubric_content_hash": "sha256:a"},
        "new": {"grading_mode": "rubric", "rubric_content_hash": "sha256:a"},
        "expected": None,
    },
    {
        "id": "mode_changed",
        "baseline": {"grading_mode": "deterministic"},
        "new": {"grading_mode": "rubric"},
        "expected_substring": "grading_mode changed",
    },
    {
        "id": "hash_changed",
        "baseline": {"grading_mode": "rubric", "rubric_content_hash": "sha256:a"},
        "new": {"grading_mode": "rubric", "rubric_content_hash": "sha256:b"},
        "expected_substring": "content_hash changed",
    },
    {
        "id": "mode_none_equals_missing",
        "baseline": {"grading_mode": None},
        "new": {},
        "expected": None,
    },
    {
        "id": "hash_none_equals_missing",
        "baseline": {"rubric_content_hash": None, "grading_mode": "rubric"},
        "new": {"grading_mode": "rubric"},
        "expected": None,
    },
    {
        "id": "extra_keys_ignored",
        "baseline": {"grading_mode": "deterministic", "other": 1},
        "new": {"grading_mode": "deterministic", "other": 99},
        "expected": None,
    },
    {
        "id": "both_changed_reports_mode_first",
        "baseline": {"grading_mode": "deterministic", "rubric_content_hash": "sha256:a"},
        "new": {"grading_mode": "rubric", "rubric_content_hash": "sha256:b"},
        "expected_substring": "grading_mode changed",
    },
    {
        "id": "none_dict_inputs",
        "baseline": None,
        "new": None,
        "expected": None,
    },
    {
        "id": "hash_change_with_none_modes",
        "baseline": {"rubric_content_hash": "sha256:a"},
        "new": {"rubric_content_hash": "sha256:b"},
        "expected_substring": "content_hash changed",
    },
]


@pytest.mark.parametrize("case", COMPARABILITY_CASES, ids=[c["id"] for c in COMPARABILITY_CASES])
def test_comparability_reason_extremes(case):
    reason = comparability_reason(case.get("baseline"), case.get("new"))
    if "expected" in case:
        assert reason == case["expected"]
    else:
        assert reason is not None
        assert case["expected_substring"] in reason


UNRELIABLE_CASES = [
    (
        {"incomparable_reason": "rubric changed"},
        {"incomparable_reason": None},
        "baseline run: rubric changed",
    ),
    (
        {"incomparable_reason": None},
        {"incomparable_reason": "grading mode changed"},
        "new run: grading mode changed",
    ),
    (
        None,
        None,
        None,
    ),
    (
        {},
        {},
        None,
    ),
]


@pytest.mark.parametrize("baseline_detail,new_detail,expected", UNRELIABLE_CASES)
def test_unreliable_reason_extremes(baseline_detail, new_detail, expected):
    assert unreliable_reason(baseline_detail, new_detail) == expected


def _expected_composite(process_score, outcome_score, process_weight, outcome_weight):
    parts = []
    if process_score is not None and process_weight > 0:
        parts.append((float(process_weight), float(process_score)))
    if outcome_score is not None and outcome_weight > 0:
        parts.append((float(outcome_weight), float(outcome_score)))
    if not parts:
        return None
    total = sum(w for w, _ in parts)
    return round(sum(w * s for w, s in parts) / total, 6)


ADDITIONAL_COMPOSITE_CASES = [
    (
        process_score,
        outcome_score,
        process_weight,
        outcome_weight,
        _expected_composite(process_score, outcome_score, process_weight, outcome_weight),
    )
    for process_score in [None, 0.0, 0.125, 0.5, 0.875, 1.0]
    for outcome_score in [None, 0.0, 0.2, 0.8, 1.0]
    for process_weight, outcome_weight in [
        (0.0, 0.0),
        (1.0, 0.0),
        (0.0, 1.0),
        (1.0, 1.0),
        (2.0, 7.0),
        (7.0, 2.0),
        (-1.0, 1.0),
        (1.0, -1.0),
    ]
]


@pytest.mark.parametrize(
    "process_score,outcome_score,process_weight,outcome_weight,expected",
    ADDITIONAL_COMPOSITE_CASES[:60],
)
def test_composite_score_bulk_matrix(
    process_score, outcome_score, process_weight, outcome_weight, expected
):
    assert composite_score(
        process_score, outcome_score, process_weight, outcome_weight
    ) == expected


def _aggregate_bulk_case(i):
    a = (i % 11) / 10.0
    b = ((i * 3) % 11) / 10.0
    c = ((i * 7) % 11) / 10.0

    outcomes = [
        _outcome("in_a", a, ["pass", "extraction_failed"] if i % 2 == 0 else ["pass"]),
        _outcome("in_b", b, ["fail", "extraction_failed"] if i % 3 == 0 else ["fail"]),
        _outcome("in_c", None if i % 4 else c, ["judge_na"] if i % 4 else ["pass"]),
    ]

    weights = {
        "in_a": 1.0 + (i % 3),
        "in_b": 1.0 + (i % 5),
        "in_c": 0.5 + (i % 2),
    }

    numeric = []
    for o in outcomes:
        if o["score"] is not None:
            w = float(weights.get(o["input_id"], 1.0))
            numeric.append((w, float(o["score"])))

    den = sum(w for w, _ in numeric)
    outcome_score = round(sum(w * s for w, s in numeric) / den, 6) if den > 0 else None

    checks = [s for o in outcomes for s in [c["status"] for c in o["checks"]]]
    ef_count = len([s for s in checks if s == "extraction_failed"])

    expected = {
        "outcome_score": outcome_score,
        "outcome_na": den <= 0,
        "graded_input_count": len([o for o in outcomes if o["score"] is not None]),
        "na_input_count": len([o for o in outcomes if o["score"] is None]),
        "extraction_failed_count": ef_count,
        "extraction_failed_rate": round(ef_count / len(checks), 6) if checks else 0.0,
    }
    return outcomes, weights, expected


ADDITIONAL_AGGREGATE_CASES = [_aggregate_bulk_case(i) for i in range(25)]


@pytest.mark.parametrize("outcomes,weights,expected", ADDITIONAL_AGGREGATE_CASES)
def test_aggregate_outcomes_bulk_generated(outcomes, weights, expected):
    assert aggregate_outcomes(outcomes, weights) == expected


def _split_bulk_case(i):
    train_a = ((i % 9) + 1) / 10.0
    train_b = (((i * 2) % 9) + 1) / 10.0
    holdout = (((i * 3) % 9) + 1) / 10.0
    regression = (((i * 4) % 9) + 1) / 10.0

    outcomes = [
        _outcome("in_ta", train_a, ["pass"], split="train"),
        _outcome("in_tb", None if i % 5 == 0 else train_b, ["judge_na"] if i % 5 == 0 else ["pass"], split="train"),
        _outcome("in_h", holdout, ["pass"], split="holdout"),
        _outcome("in_r", regression, ["pass"], split="regression"),
    ]
    weights = {
        "in_ta": 1.0 + (i % 3),
        "in_tb": 1.0 + (i % 4),
        "in_h": 1.0 + (i % 2),
        "in_r": 1.0,
    }

    def _score(split):
        pairs = []
        for outcome in outcomes:
            if outcome.get("effective_split", outcome.get("split")) != split:
                continue
            if outcome["score"] is None:
                continue
            pairs.append((float(weights.get(outcome["input_id"], 1.0)), float(outcome["score"])))
        if not pairs:
            return None
        den = sum(w for w, _ in pairs)
        return round(sum(w * s for w, s in pairs) / den, 6)

    expected = {
        "train": _score("train"),
        "holdout": _score("holdout"),
        "regression": _score("regression"),
    }
    return outcomes, weights, expected


ADDITIONAL_SPLIT_CASES = [_split_bulk_case(i) for i in range(25)]


@pytest.mark.parametrize("outcomes,weights,expected", ADDITIONAL_SPLIT_CASES)
def test_scores_by_split_bulk_generated(outcomes, weights, expected):
    assert scores_by_split(outcomes, weights) == expected


MODE_VALUES = [None, "deterministic", "rubric"]
HASH_VALUES = [None, "sha256:a", "sha256:b"]


def _expected_comparability_reason(baseline, new):
    b_mode = (baseline or {}).get("grading_mode") or None
    n_mode = (new or {}).get("grading_mode") or None
    if b_mode != n_mode:
        return (
            f"grading_mode changed between runs "
            f"({(baseline or {}).get('grading_mode')!r} -> {(new or {}).get('grading_mode')!r})"
        )
    b_hash = (baseline or {}).get("rubric_content_hash") or None
    n_hash = (new or {}).get("rubric_content_hash") or None
    if b_hash != n_hash:
        return (
            "rubric content_hash changed between runs — the rubric was edited "
            "mid-ratchet, so the two scores were measured with different rulers"
        )
    return None


ADDITIONAL_COMPARABILITY_CASES = [
    (
        {"grading_mode": baseline_mode, "rubric_content_hash": baseline_hash},
        {"grading_mode": new_mode, "rubric_content_hash": new_hash},
        _expected_comparability_reason(
            {"grading_mode": baseline_mode, "rubric_content_hash": baseline_hash},
            {"grading_mode": new_mode, "rubric_content_hash": new_hash},
        ),
    )
    for baseline_mode in MODE_VALUES
    for new_mode in MODE_VALUES
    for baseline_hash in HASH_VALUES
    for new_hash in HASH_VALUES
]


@pytest.mark.parametrize(
    "baseline,new,expected",
    ADDITIONAL_COMPARABILITY_CASES[:25],
)
def test_comparability_reason_bulk_matrix(baseline, new, expected):
    assert comparability_reason(baseline, new) == expected

