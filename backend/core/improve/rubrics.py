"""
Rubric registry for the Milli Self-Improvement subsystem (Checkpoint 6, §6.4).

Rubrics are STANDALONE, REUSABLE, IMMUTABLE-PER-VERSION objects, consistent
with §0.6.1's decision for benchmarks. A rubric is generic ("does it avoid
fabrication"); the *expectation* is per-input ("APAC led at $4.2M"). Conflating
them means re-authoring a rubric for every prompt.

Immutability is not a nicety. If a rubric is edited mid-ratchet, baseline and
new scores are measured with different rulers, and silently comparing across
rubric versions is the subtlest way this subsystem can lie to you. So:

- An edit writes a NEW `version` with a recomputed `content_hash`; prior
  versions remain readable. Same pattern as `versions/<object_id>/v<N>.json`.
- Every benchmark result records `rubric_id`, `rubric_version`,
  `rubric_content_hash`.
- `IMPROVE_RATCHET_DECIDE` refuses to compare two scores whose
  `rubric_content_hash` differs.

Storage (§0.6.5 — per-user auth-scoped):

    improve/<user_id>/rubrics/<rubric_id>/v<N>.json   # immutable versions
    improve/<user_id>/rubrics/index.json              # id -> latest version

REGISTRY UNIFICATION: this module is the single authoritative Rubric registry
for Milli. The Training-tab concept of a flat `data/rubrics.json` is
SUPERSEDED — do not create it. A shared flat file cannot satisfy §0.6.5's
per-user auth-scoping constraint. The public API below (`get_rubric`,
`list_rubrics`, `save_rubric`, `resolve_version`) is what the Training tab
consumes later.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator

from core.improve.trace_writer import ensure_user_layout

CRITERION_KINDS = {"key_point_coverage", "anchored", "deterministic"}


class RubricNotFound(Exception):
    pass


class RubricInUse(Exception):
    """Refused deletion — a benchmark still references this rubric."""


# ── schema (Appendix A6) ─────────────────────────────────────────────────────

class RubricCriterion(BaseModel):
    id: str
    kind: str
    weight: float = 1.0
    critical: bool = False
    critical_floor: float = 1.0
    question: str | None = None
    scale: int | None = None
    anchors: dict[str, str] | None = None
    check: dict | None = None

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, v: str) -> str:
        if v not in CRITERION_KINDS:
            raise ValueError(
                f"unknown criterion kind '{v}' (allowed: {sorted(CRITERION_KINDS)})"
            )
        return v


class Rubric(BaseModel):
    id: str
    name: str
    version: int = 1
    content_hash: str = ""
    created_at: str | None = None
    criteria: list[RubricCriterion] = Field(min_length=1)
    deleted: bool = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ── content hash ─────────────────────────────────────────────────────────────

def compute_content_hash(rubric: dict) -> str:
    """sha256 over the grading-relevant payload only.

    `version`, `created_at`, and the hash itself are excluded: bumping a version
    without changing the criteria must NOT change the hash, or every re-save
    would spuriously make old scores incomparable.
    """
    payload = {
        "id": rubric.get("id"),
        "name": rubric.get("name"),
        "criteria": rubric.get("criteria") or [],
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── storage ──────────────────────────────────────────────────────────────────

def _rubrics_dir(user_id: str | None) -> str:
    path = os.path.join(ensure_user_layout(user_id), "rubrics")
    os.makedirs(path, exist_ok=True)
    return path


def _rubric_dir(user_id: str | None, rubric_id: str) -> str:
    return os.path.join(_rubrics_dir(user_id), rubric_id)


def _version_path(user_id: str | None, rubric_id: str, version: int) -> str:
    return os.path.join(_rubric_dir(user_id, rubric_id), f"v{int(version)}.json")


def _index_path(user_id: str | None) -> str:
    return os.path.join(_rubrics_dir(user_id), "index.json")


def _read_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_immutable(path: str, record: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
    try:
        os.chmod(path, stat.S_IREAD)  # best-effort read-only, as versioning.py does
    except Exception:
        pass


def load_index(user_id: str | None) -> dict:
    return _read_json(_index_path(user_id)) or {}


def _save_index(user_id: str | None, index: dict) -> None:
    path = _index_path(user_id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def latest_version(user_id: str | None, rubric_id: str) -> int:
    versions = [
        int(name[1:-5])
        for name in os.listdir(_rubric_dir(user_id, rubric_id))
        if name.startswith("v") and name.endswith(".json") and name[1:-5].isdigit()
    ] if os.path.isdir(_rubric_dir(user_id, rubric_id)) else []
    return max(versions) if versions else 0


# ── public API (consumed by the Training tab later) ──────────────────────────

def save_rubric(user_id: str | None, rubric: dict) -> dict:
    """Create or write a NEW version. Never mutates an existing version.

    The caller's `version` field is ignored — the registry assigns the next
    number so a stale client cannot overwrite history.
    """
    candidate = dict(rubric or {})
    candidate.pop("content_hash", None)
    validated = Rubric.model_validate({**candidate, "version": 1}).model_dump()

    next_n = latest_version(user_id, validated["id"]) + 1
    validated["version"] = next_n
    validated["content_hash"] = compute_content_hash(validated)
    validated["created_at"] = _now_iso()

    path = _version_path(user_id, validated["id"], next_n)
    if os.path.exists(path):  # belt and braces — versions are immutable
        raise FileExistsError(f"rubric version already exists: {path}")
    _write_immutable(path, validated)

    index = load_index(user_id)
    index[validated["id"]] = {
        "id": validated["id"],
        "name": validated["name"],
        "version": next_n,
        "content_hash": validated["content_hash"],
        "created_at": validated["created_at"],
        "deleted": bool(validated.get("deleted")),
    }
    _save_index(user_id, index)
    return validated


def get_rubric(
    user_id: str | None, rubric_id: str, version: int | None = None
) -> dict:
    """Fetch a rubric; `version=None` resolves to the latest."""
    resolved = resolve_version(user_id, rubric_id, version)
    record = _read_json(_version_path(user_id, rubric_id, resolved))
    if record is None:
        raise RubricNotFound(f"{rubric_id} v{resolved}")
    return record


def resolve_version(
    user_id: str | None, rubric_id: str, version: int | None = None
) -> int:
    if version is not None:
        return int(version)
    latest = latest_version(user_id, rubric_id)
    if latest < 1:
        raise RubricNotFound(rubric_id)
    return latest


def list_rubrics(user_id: str | None, include_deleted: bool = False) -> list[dict]:
    """Latest version of every rubric, newest-first by id for stable output."""
    index = load_index(user_id)
    out = []
    for rubric_id, entry in sorted(index.items()):
        if entry.get("deleted") and not include_deleted:
            continue
        try:
            out.append(get_rubric(user_id, rubric_id))
        except RubricNotFound:
            continue
    return out


def list_versions(user_id: str | None, rubric_id: str) -> list[dict]:
    """Every stored version of a rubric, oldest first."""
    latest = latest_version(user_id, rubric_id)
    out = []
    for n in range(1, latest + 1):
        record = _read_json(_version_path(user_id, rubric_id, n))
        if record is not None:
            out.append(record)
    return out


def referencing_benchmarks(user_id: str | None, rubric_id: str) -> list[str]:
    """Benchmark ids that reference this rubric, at suite or input level."""
    from core.improve import benchmark as bm
    hits = []
    for suite in bm.list_benchmarks(user_id):
        if suite.get("rubric_id") == rubric_id:
            hits.append(suite.get("id"))
            continue
        if any(
            (item or {}).get("rubric_id") == rubric_id
            for item in suite.get("inputs") or []
        ):
            hits.append(suite.get("id"))
    return [h for h in hits if h]


def delete_rubric(user_id: str | None, rubric_id: str) -> dict:
    """Soft-delete. Refuses while any benchmark still references the rubric —
    a dangling reference would make that benchmark unscoreable mid-ratchet."""
    index = load_index(user_id)
    if rubric_id not in index:
        raise RubricNotFound(rubric_id)
    refs = referencing_benchmarks(user_id, rubric_id)
    if refs:
        raise RubricInUse(
            f"rubric '{rubric_id}' is referenced by benchmark(s): {sorted(refs)}"
        )
    index[rubric_id]["deleted"] = True
    _save_index(user_id, index)
    return index[rubric_id]
