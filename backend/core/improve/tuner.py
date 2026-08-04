"""
Tuner for the Synapse Self-Improvement subsystem (Checkpoint 3).

Turns an insights report + the target object's current JSON config into a
`ProposedDiff` — a reviewable, evidence-linked set of field edits — using
Synapse's own LLM dispatch (`core.llm_providers.generate_response`). No
direct provider SDK calls, ever.

Guardrails (CLAUDE.md §0.5, enforced here as the FIRST of two boundaries;
`applier.py` independently re-checks at apply time):
- Pydantic schema validation on tuner output; malformed JSON → one retry
  with the parse error fed back.
- Field allow-list check; out-of-scope edits → one retry with the violation
  fed back. Still bad after retry → TunerOutputError.
- Prompt-length cap with a compacted-insights fallback.
- Tuner model is user-configurable per run (§0.6.3), pinned, and recorded in
  the ImprovementRun record.

--------------------------------------------------------------------------
Attribution: the tuner system prompt below is derived from the SKILL.md of
the `recursive-improve` project, licensed under the Apache License,
Version 2.0 (http://www.apache.org/licenses/LICENSE-2.0). Modifications:
adapted to Synapse's agent/orchestration config schema, field allow-list,
and ProposedDiff output contract. Provided on an "AS IS" BASIS, WITHOUT
WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
--------------------------------------------------------------------------
"""
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

# ── Field allow-list (§0.5) — tuner-side boundary ────────────────────────────

AGENT_TUNABLE_FIELDS = {
    "system_prompt",
    "tools",
    "max_turns",
    "model",
    "description",
    "delegate_agent_ids",
}

# Orchestration edits address a step field as `steps[<idx>].<field>`.
ORCH_STEP_FIELD_RE = re.compile(r"^steps\[(\d+)\]\.([A-Za-z_][A-Za-z0-9_]*)$")
ORCH_STEP_TUNABLE_FIELDS = {
    # prompts
    "prompt_template",
    "evaluator_prompt",
    # transitions
    "next_step_id",
    "route_map",
    "if_true_step_id",
    "if_false_step_id",
    "switch_cases",
    "switch_default_step_id",
    # retry / loop bounds
    "max_iterations",
    "loop_count",
    # timeouts / turn bounds
    "timeout_seconds",
    "human_timeout_seconds",
    "max_turns",
}

MAX_TUNER_PROMPT_CHARS = 24_000
MAX_COMPACT_INSIGHTS = 20


class TunerOutputError(Exception):
    """Tuner output failed schema/allow-list validation after retry."""


class TargetNotFound(Exception):
    """The target agent/orchestration does not exist."""


# ── ProposedDiff schema (checklist 3.4 — all five components) ────────────────

class FieldEdit(BaseModel):
    field: str
    old_value: Any = None
    new_value: Any = None
    rationale: str = ""


class EvidencePointer(BaseModel):
    trace_file: str
    message_idx: int | None = None


class ProposedDiff(BaseModel):
    target_object_id: str
    target_kind: Literal["agent", "orchestration"]
    field_edits: list[FieldEdit] = Field(min_length=1)
    rationale: str
    evidence_pointers: list[EvidencePointer] = []
    expected_metric_deltas: dict[str, float] = {}


def validate_field_edits(
    field_edits: list[dict], target_kind: str, allowed_models: set[str] | None = None
) -> list[str]:
    """Allow-list violations for a set of edits. Empty list == all in scope."""
    violations: list[str] = []
    for edit in field_edits:
        field = str(edit.get("field") or "")
        if target_kind == "agent":
            if field not in AGENT_TUNABLE_FIELDS:
                violations.append(
                    f"'{field}' is not a tunable agent field "
                    f"(allowed: {sorted(AGENT_TUNABLE_FIELDS)})"
                )
            elif field == "model" and allowed_models is not None:
                new_model = edit.get("new_value")
                if new_model not in allowed_models:
                    violations.append(
                        f"model '{new_model}' is not in the allowed model list "
                        f"{sorted(allowed_models)}"
                    )
        else:
            m = ORCH_STEP_FIELD_RE.match(field)
            if not m:
                violations.append(
                    f"'{field}' is not a valid orchestration edit path "
                    "(expected steps[<idx>].<field>)"
                )
            elif m.group(2) not in ORCH_STEP_TUNABLE_FIELDS:
                violations.append(
                    f"step field '{m.group(2)}' is not tunable "
                    f"(allowed: {sorted(ORCH_STEP_TUNABLE_FIELDS)})"
                )
    return violations


# ── Tuner system prompt (Apache-2.0-derived — see module header) ─────────────

TUNER_SYSTEM_PROMPT = """You are a configuration tuner for AI agents and orchestrations. \
You analyze behavioral insights extracted from execution traces and propose minimal, \
targeted edits to the target's JSON configuration that address the observed problems.

Principles (from recursive-improve, adapted to Synapse):
- Ground every proposed edit in specific evidence from the insights. Never invent problems.
- Prefer the smallest change that plausibly fixes the highest-severity finding.
- Improve instructions (system prompts / prompt templates) before changing structure.
- Remove or bound what is misused (hallucinated tools, runaway loops) rather than adding complexity.
- Preserve the target's intent and voice; edit surgically, do not rewrite wholesale.
- State the metric you expect each change to move, and in which direction.

You may ONLY edit fields in the allow-list given in the user message. Never propose edits \
to code files, credentials, MCP or database configuration, or any field outside the allow-list.

Respond with a single JSON object and nothing else (no prose, no code fences), exactly this shape:
{
  "target_object_id": "<id>",
  "target_kind": "agent" | "orchestration",
  "field_edits": [
    {"field": "<allow-listed field path>", "old_value": <current value>, "new_value": <proposed value>, "rationale": "<why>"}
  ],
  "rationale": "<overall reasoning>",
  "evidence_pointers": [{"trace_file": "<relative trace path>", "message_idx": <int or null>}],
  "expected_metric_deltas": {"<detector or metric name>": <signed float>}
}"""


# ── Prompt assembly & compaction (checklist 3.8) ─────────────────────────────

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def compact_insights(insights: dict) -> dict:
    """Lossy fallback when the full insights JSON blows the prompt cap.

    Keeps the top findings by severity, truncates learnings, and trims each
    finding's evidence to its first two pointers.
    """
    ranked = sorted(
        insights.get("insights", []),
        key=lambda i: (_SEVERITY_ORDER.get(i.get("severity"), 3), i.get("id", "")),
    )
    compacted = []
    for item in ranked[:MAX_COMPACT_INSIGHTS]:
        compacted.append(
            {
                "detector": item.get("detector"),
                "severity": item.get("severity"),
                "learning": str(item.get("learning") or "")[:200],
                "evidence": (item.get("evidence") or [])[:2],
            }
        )
    return {
        "trace_count": insights.get("trace_count", 0),
        "insight_count": insights.get("insight_count", 0),
        "note": "compacted to fit prompt cap",
        "insights": compacted,
    }


def _allow_list_text(target_kind: str) -> str:
    if target_kind == "agent":
        return "Editable agent fields: " + ", ".join(sorted(AGENT_TUNABLE_FIELDS))
    return (
        "Editable orchestration paths: steps[<idx>].<field> where <field> is one of: "
        + ", ".join(sorted(ORCH_STEP_TUNABLE_FIELDS))
    )


def build_tuner_prompt(
    target_object_id: str,
    target_kind: str,
    config: dict,
    insights: dict,
    allowed_models: set[str],
) -> str:
    """User prompt under MAX_TUNER_PROMPT_CHARS, compacting insights if needed."""
    config_json = json.dumps(config, indent=2, ensure_ascii=False)

    def render(ins: dict) -> str:
        return (
            f"TARGET: {target_kind} '{target_object_id}'\n"
            f"{_allow_list_text(target_kind)}\n"
            + (
                f"Allowed values for 'model': {sorted(allowed_models)}\n"
                if target_kind == "agent"
                else ""
            )
            + f"\nCURRENT CONFIGURATION:\n{config_json}\n"
            f"\nINSIGHTS (from execution-trace detectors):\n"
            f"{json.dumps(ins, indent=2, ensure_ascii=False)}\n"
            "\nPropose the minimal set of allow-listed field edits that addresses the "
            "highest-severity findings. Respond with the JSON object only."
        )

    prompt = render(insights)
    if len(prompt) > MAX_TUNER_PROMPT_CHARS:
        prompt = render(compact_insights(insights))
    if len(prompt) > MAX_TUNER_PROMPT_CHARS:  # config itself is huge — hard truncate
        prompt = prompt[:MAX_TUNER_PROMPT_CHARS]
    return prompt


# ── Output parsing ────────────────────────────────────────────────────────────

def parse_tuner_output(raw: str) -> dict:
    """Extract and load the first JSON object from the model's response."""
    text = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in tuner output")
    return json.loads(text[start : end + 1])


# ── Target loading ────────────────────────────────────────────────────────────

def load_target_config(target_object_id: str, target_kind: str) -> dict:
    if target_kind == "agent":
        from core.routes.agents import load_user_agents
        objs = load_user_agents()
    else:
        from core.routes.orchestrations import load_orchestrations
        objs = load_orchestrations()
    for o in objs:
        if o.get("id") == target_object_id:
            return o
    raise TargetNotFound(f"{target_kind} '{target_object_id}' not found")


def allowed_models_for(config: dict) -> set[str]:
    """Bounded model allow-list for `model` edits (§0.5): the target's current
    model plus the workspace default. Never an open-ended choice."""
    from core.config import load_settings
    allowed: set[str] = set()
    if config.get("model"):
        allowed.add(config["model"])
    try:
        default = load_settings().get("model")
        if default:
            allowed.add(default)
    except Exception:
        pass
    return allowed


# ── Main entry point ──────────────────────────────────────────────────────────

async def propose(
    user_id: str | None,
    target_object_id: str,
    target_kind: str,
    tuner_model: str | None = None,
    mode: str = "human",
) -> dict:
    """Full propose flow: insights → tuner LLM → validated ProposedDiff.

    Opens an ImprovementRun (409-guarded by runs.create_run), pins the tuner
    model, writes the proposal payload, and returns {"run": ..., "proposed_diff": ...}.
    Raises TargetNotFound / runs.RunConflict / TunerOutputError.
    """
    from core.config import load_settings
    from core.improve import runs as runs_mod
    from core.improve.insights import extract_insights
    from core.improve.runner import build_report

    config = load_target_config(target_object_id, target_kind)
    settings = load_settings()
    resolved_model = (
        tuner_model
        or settings.get("improve_tuner_model")
        or settings.get("model")
        or "ollama.mistral"
    )

    run = runs_mod.create_run(
        user_id,
        target_object_id,
        target_kind,
        baseline_version_n=int(config.get("version_n") or 1),
        tuner_model=resolved_model,
        mode=mode,
    )

    try:
        report = build_report(
            user_id=user_id,
            agent_id=target_object_id if target_kind == "agent" else None,
            orchestration_id=target_object_id if target_kind == "orchestration" else None,
        )
        insights = extract_insights(report)
        allowed_models = allowed_models_for(config)
        prompt = build_tuner_prompt(
            target_object_id, target_kind, config, insights, allowed_models
        )
        diff = await _call_tuner_with_retry(
            prompt, resolved_model, settings, run["run_id"],
            target_object_id, target_kind, allowed_models,
        )

        proposal_ref = runs_mod.write_proposal(
            user_id,
            run["run_id"],
            {"insights": insights, "proposed_diff": diff},
        )
        run = runs_mod.update_run(
            user_id,
            run["run_id"],
            insights_ref=proposal_ref,
            proposed_diff_ref=proposal_ref,
        )
        return {"run": run, "proposed_diff": diff, "insights": insights}
    except Exception:
        # Never leave a dead run holding the 409 lock.
        try:
            runs_mod.update_run(
                user_id, run["run_id"], decision="revert",
                closed_at=runs_mod._now_iso(),
            )
        except Exception:
            pass
        raise


async def _call_tuner_with_retry(
    prompt: str,
    model: str,
    settings: dict,
    run_id: str,
    target_object_id: str,
    target_kind: str,
    allowed_models: set[str],
) -> dict:
    """One call + one reject-and-retry pass over schema and allow-list checks."""
    from core.llm_providers import detect_mode_from_model, generate_response

    llm_mode = detect_mode_from_model(model)
    attempt_prompt = prompt
    last_error = ""
    for attempt in range(2):
        raw = await generate_response(
            prompt_msg=attempt_prompt,
            sys_prompt=TUNER_SYSTEM_PROMPT,
            mode=llm_mode,
            current_model=model,
            current_settings=settings,
            source="improve_tuner",
            run_id=run_id,
        )
        try:
            data = parse_tuner_output(raw)
            # Never trust the model with its own routing.
            data["target_object_id"] = target_object_id
            data["target_kind"] = target_kind
            validated = ProposedDiff.model_validate(data)
        except (ValueError, ValidationError) as e:
            last_error = f"output failed schema validation: {e}"
        else:
            violations = validate_field_edits(
                [fe.model_dump() for fe in validated.field_edits],
                target_kind,
                allowed_models,
            )
            if not violations:
                return validated.model_dump()
            last_error = "out-of-scope field edits: " + "; ".join(violations)
        if attempt == 0:
            attempt_prompt = (
                prompt
                + "\n\nYOUR PREVIOUS RESPONSE WAS REJECTED: "
                + last_error
                + "\nRespond again with a corrected JSON object only."
            )
    raise TunerOutputError(last_error)
