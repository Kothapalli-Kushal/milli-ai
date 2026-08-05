"""
Input augmentation (Checkpoint 6, §6.5.3).

Real users do not phrase questions the way the benchmark author did.
Augmentation generates paraphrases of an input's prompt **while holding the
expected output fixed**, exposing brittleness to surface wording.

GENERATE ONCE, FREEZE FOREVER. Augmentation is an explicit authoring action
(`POST /api/improve/benchmark/{id}/augment`), never an implicit run-time step.
Generated variants are written back into the benchmark file as first-class
inputs and must be approved before they count.

The non-negotiables, each enforced in code rather than in documentation:

- Variants inherit the parent's `split` and `fold` (see `splits.py`). A
  paraphrase landing in holdout while its parent is in train is train/test
  leakage and makes the holdout score meaningless.
- `expected` is SHARED with the parent as `{"$ref": <parent_id>}`, never
  copied. A copied expectation drifts the moment the parent is edited.
- Variants land with `approved: false` and are excluded from scoring until a
  human approves them. An LLM paraphrase that quietly changes the question is a
  corrupted benchmark, and a corrupted benchmark silently misdirects the tuner
  for every subsequent iteration.
- Default `weight: 0.5`, so variants inform without dominating the parent.
- The constraint guard is DETERMINISTIC and non-LLM by design. One
  reject-and-retry, then skip that variant — the same pattern as the CP3 tuner
  boundary.
- Answer-leakage rejection: a paraphrase that leaks the expected answer into
  the question scores 1.0 forever and teaches you nothing.
"""
from __future__ import annotations

import json
import re

DEFAULT_VARIANT_WEIGHT = 0.5

AUGMENT_SYSTEM_PROMPT = """You rewrite benchmark questions as natural paraphrases.

Rules:
- Preserve the MEANING exactly. The correct answer must not change.
- Preserve every number, quoted literal, date, and named entity verbatim.
- Do not add constraints, qualifiers, or hints that were not in the original.
- Do not include or hint at the answer.
- Vary only the surface wording and sentence structure.

Respond with a single JSON object and nothing else:
{"variants": ["<paraphrase 1>", "<paraphrase 2>"]}"""

# A "named entity" for the deterministic guard: a capitalized or all-caps token,
# including alphanumeric labels like `Q3`, `FY24`, `H1`, which are exactly the
# kind of specific a paraphrase must not quietly drop. Deliberately
# conservative — the guard's job is to catch dropped specifics, not to do NER.
_ENTITY_RE = re.compile(
    r"\b(?:[A-Z][A-Za-z]*\d+[A-Za-z0-9]*|[A-Z]{2,}|[A-Z][a-z]+(?:[A-Z][a-z]+)*)\b"
)
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
_QUOTED_RE = re.compile(r"['\"`]([^'\"`]+)['\"`]")

# Sentence-initial words that are capitalized purely by grammar.
_STOPWORDS = {
    "The", "A", "An", "In", "On", "At", "For", "Which", "What", "Who", "When",
    "Where", "Why", "How", "Is", "Are", "Was", "Were", "Do", "Does", "Did",
    "Can", "Could", "Should", "Would", "List", "Show", "Give", "Find", "Tell",
    "Name", "Summarize", "Describe", "Explain", "Compare", "Identify", "Of",
    "By", "To", "From", "And", "Or", "With", "During", "Over", "Per",
}


class AugmentationError(Exception):
    pass


# ── deterministic constraint guard (checklist 6.22) ──────────────────────────

def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUMBER_RE.findall(text or "")}


def _quoted(text: str) -> set[str]:
    return {q.strip().lower() for q in _QUOTED_RE.findall(text or "")}


def _entities(text: str) -> set[str]:
    return {
        token for token in _ENTITY_RE.findall(text or "")
        if token not in _STOPWORDS
    }


def check_constraints(parent_prompt: str, variant: str, constraints: dict) -> list[str]:
    """Deterministic violations of a paraphrase. Empty list == acceptable.

    Non-LLM by design: the whole point is to catch a paraphrase that quietly
    changed the question, and asking a model to check a model's work does not
    do that reliably.
    """
    constraints = constraints or {}
    violations: list[str] = []
    variant = (variant or "").strip()

    if not variant:
        return ["variant is empty"]
    if variant.strip().lower() == (parent_prompt or "").strip().lower():
        return ["variant is identical to the parent prompt"]

    if constraints.get("preserve_numbers", True):
        missing = _numbers(parent_prompt) - _numbers(variant)
        if missing:
            violations.append(f"dropped number(s): {sorted(missing)}")

    if constraints.get("preserve_quoted_literals", True):
        missing = _quoted(parent_prompt) - _quoted(variant)
        if missing:
            violations.append(f"dropped quoted literal(s): {sorted(missing)}")

    if constraints.get("preserve_entities", True):
        variant_lower = variant.lower()
        missing = {
            entity for entity in _entities(parent_prompt)
            if entity.lower() not in variant_lower
        }
        if missing:
            violations.append(f"dropped named entity/entities: {sorted(missing)}")

    max_ratio = float(constraints.get("max_length_ratio", 1.5))
    parent_len = max(len(parent_prompt or ""), 1)
    if len(variant) > parent_len * max_ratio:
        violations.append(
            f"variant is {len(variant) / parent_len:.2f}x the parent's length "
            f"(max {max_ratio})"
        )

    if constraints.get("forbid_added_constraints", True):
        # A question mark count increase, or an added "only/must/exactly"
        # qualifier, is the cheap deterministic proxy for an added constraint.
        added = {
            word for word in ("only", "must", "exactly", "at least", "at most")
            if word in variant.lower() and word not in (parent_prompt or "").lower()
        }
        if added:
            violations.append(f"added constraint word(s): {sorted(added)}")

    return violations


def check_answer_leakage(variant: str, expected: dict) -> list[str]:
    """Reject a variant whose prompt contains the answer (§6.5.3).

    A paraphrase that leaks the answer into the question scores 1.0 forever and
    teaches you nothing.
    """
    violations: list[str] = []
    haystack = (variant or "").lower()

    for payload in _expected_payloads(expected or {}):
        text = str(payload).strip()
        if len(text) < 3:
            continue  # too short to be a meaningful leak signal
        if text.lower() in haystack:
            violations.append(f"leaks expected value: {text[:60]!r}")
    return violations


def _expected_payloads(expected: dict) -> list:
    """Every answer-bearing string in an expectation block."""
    out: list = []
    for check in expected.get("checks") or []:
        out.extend(_compare_payloads((check or {}).get("compare") or {}))
    for key_point in expected.get("key_points") or []:
        if key_point.get("text"):
            out.append(key_point["text"])
    out.extend(expected.get("forbidden") or [])
    if expected.get("reference_output"):
        out.append(expected["reference_output"])
    if expected.get("reference_sql"):
        out.append(expected["reference_sql"])
    return out


def _compare_payloads(compare: dict) -> list:
    ctype = str(compare.get("type") or "")
    if ctype == "any_of":
        payloads: list = []
        for option in compare.get("options") or []:
            payloads.extend(_compare_payloads(option or {}))
        return payloads
    value = compare.get("value")
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, dict):
        return [json.dumps(value, sort_keys=True)]
    return [str(value)]


def validate_variant(
    parent: dict, variant_text: str, constraints: dict, expected: dict
) -> list[str]:
    """Both guards, in one call. Empty list == the variant may be offered."""
    return (
        check_constraints(parent.get("prompt", ""), variant_text, constraints)
        + check_answer_leakage(variant_text, expected)
    )


# ── variant construction ─────────────────────────────────────────────────────

def build_variant(parent: dict, prompt: str, index: int) -> dict:
    """One augmented input, sharing (never copying) the parent's expectation."""
    return {
        "id": f"{parent['id']}__aug{index}",
        "parent_input_id": parent["id"],
        "is_augmented": True,
        "approved": False,              # excluded from scoring until reviewed
        "prompt": prompt,
        "expected": {"$ref": parent["id"]},   # SHARED, so it cannot drift
        "split": parent.get("split", "train"),
        "fold": parent.get("fold"),
        "weight": DEFAULT_VARIANT_WEIGHT,
        "grading_mode": parent.get("grading_mode"),
        "rubric_id": parent.get("rubric_id"),
        "expected_metric_hints": {},
        "images": None,
    }


def parse_variants(raw: str) -> list[str]:
    text = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return []
    try:
        parsed = json.loads(text[start:end + 1])
    except ValueError:
        return []
    variants = parsed.get("variants")
    if not isinstance(variants, list):
        return []
    return [str(v).strip() for v in variants if str(v).strip()]


def build_prompt(parent_prompt: str, count: int, constraints: dict) -> str:
    limits = []
    if (constraints or {}).get("preserve_numbers", True):
        limits.append("keep every number exactly as written")
    if (constraints or {}).get("preserve_quoted_literals", True):
        limits.append("keep every quoted literal exactly as written")
    if (constraints or {}).get("preserve_entities", True):
        limits.append("keep every named entity exactly as written")
    ratio = float((constraints or {}).get("max_length_ratio", 1.5))
    limits.append(f"stay under {ratio:.1f}x the original length")
    return (
        f"Produce {count} distinct paraphrase(s) of this benchmark question.\n\n"
        f"ORIGINAL: {parent_prompt}\n\n"
        f"Constraints: {'; '.join(limits)}.\n"
        'Respond with {"variants": [...]} and nothing else.'
    )


# ── the authoring action ─────────────────────────────────────────────────────

async def generate_variants(
    user_id: str | None,
    benchmark: dict,
    *,
    run_id: str | None = None,
    generate=None,
) -> dict:
    """Generate paraphrase variants for a benchmark's eligible inputs.

    Returns {"variants": [...], "rejected": [...], "skipped": [...]}. Variants
    are returned `approved: false`; the caller writes them into the benchmark
    file. Spend joins `usage_tracker` and counts against the run budget.
    """
    from core.improve import benchmark as bm

    policy = benchmark.get("augmentation") or {}
    if not policy.get("enabled"):
        raise AugmentationError(
            "augmentation is not enabled for this benchmark "
            "(set augmentation.enabled = true)"
        )
    per_input = int(policy.get("variants_per_input") or 2)
    constraints = policy.get("constraints") or {}
    apply_to = set(policy.get("apply_to_splits") or ["train", "holdout"])
    call = generate or _default_generate(policy.get("model"), run_id)

    existing_ids = {str(i.get("id")) for i in benchmark.get("inputs") or []}
    variants: list[dict] = []
    rejected: list[dict] = []
    skipped: list[dict] = []

    for parent in list(benchmark.get("inputs") or []):
        if parent.get("is_augmented"):
            continue  # never paraphrase a paraphrase
        if str(parent.get("split") or "train") not in apply_to:
            continue
        expected = bm.resolve_expected(benchmark, parent)
        accepted: list[str] = []

        for index in range(1, per_input + 1):
            text, violations = await _one_variant(
                call, parent, index, constraints, expected, accepted
            )
            if text is None:
                # One reject-and-retry already happened inside _one_variant.
                skipped.append({
                    "parent_input_id": parent["id"], "index": index,
                    "violations": violations,
                })
                rejected.append({
                    "parent_input_id": parent["id"], "index": index,
                    "violations": violations,
                })
                continue
            accepted.append(text)
            variant = build_variant(parent, text, index)
            if variant["id"] in existing_ids:
                variant["id"] = f"{variant['id']}_{len(variants) + 1}"
            existing_ids.add(variant["id"])
            variants.append(variant)

    return {"variants": variants, "rejected": rejected, "skipped": skipped}


async def _one_variant(call, parent, index, constraints, expected, already):
    """One generation attempt plus ONE reject-and-retry, then give up."""
    prompt = build_prompt(parent.get("prompt", ""), 1, constraints)
    violations: list[str] = []
    for attempt in range(2):
        raw = await call(prompt)
        candidates = [
            c for c in parse_variants(raw)
            if c.strip().lower() not in {a.strip().lower() for a in already}
        ]
        for candidate in candidates:
            violations = validate_variant(parent, candidate, constraints, expected)
            if not violations:
                return candidate, []
        if attempt == 0:
            prompt = (
                build_prompt(parent.get("prompt", ""), 1, constraints)
                + "\n\nYOUR PREVIOUS PARAPHRASE WAS REJECTED: "
                + "; ".join(violations or ["no usable variant returned"])
                + "\nProduce a corrected paraphrase."
            )
    return None, violations or ["no usable variant returned"]


def _default_generate(model: str | None, run_id: str | None):
    from core.config import load_settings
    from core.llm_providers import detect_mode_from_model, generate_response

    try:
        settings = load_settings()
    except Exception:
        settings = {}
    resolved = model or settings.get("improve_tuner_model") or settings.get("model")

    async def _call(prompt: str) -> str:
        return await generate_response(
            prompt_msg=prompt,
            sys_prompt=AUGMENT_SYSTEM_PROMPT,
            mode=detect_mode_from_model(resolved),
            current_model=resolved,
            current_settings=settings,
            source="improve_augment",   # joins usage_tracker -> run budget
            run_id=run_id,
        )

    return _call


def apply_approvals(benchmark: dict, decisions: dict[str, bool]) -> dict:
    """Approve or reject generated variants by id.

    Rejected variants are REMOVED rather than left disapproved, so a benchmark
    never accumulates dead inputs that a later bulk-approve could resurrect.
    """
    approved, removed = [], []
    kept = []
    for item in benchmark.get("inputs") or []:
        input_id = str(item.get("id"))
        if input_id in decisions and item.get("is_augmented"):
            if decisions[input_id]:
                item["approved"] = True
                approved.append(input_id)
            else:
                removed.append(input_id)
                continue
        kept.append(item)
    benchmark["inputs"] = kept
    return {"approved": approved, "removed": removed}


def supersede_unapproved(benchmark: dict) -> list[str]:
    """Drop unapproved variants ahead of a regeneration.

    Regeneration supersedes prior UNAPPROVED variants and never silently
    mutates approved ones.
    """
    dropped = [
        str(i.get("id")) for i in benchmark.get("inputs") or []
        if i.get("is_augmented") and not i.get("approved", False)
    ]
    benchmark["inputs"] = [
        i for i in benchmark.get("inputs") or []
        if not (i.get("is_augmented") and not i.get("approved", False))
    ]
    return dropped
