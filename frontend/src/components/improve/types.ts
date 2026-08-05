// Shared types for the Self-Improvement panels (Checkpoint 3).

export type TargetKind = 'agent' | 'orchestration';

export interface EvidencePointer {
    trace_file: string;
    message_idx: number | null;
}

export interface FieldEdit {
    field: string;
    old_value: unknown;
    new_value: unknown;
    rationale?: string;
}

export interface ProposedDiff {
    target_object_id: string;
    target_kind: TargetKind;
    field_edits: FieldEdit[];
    rationale: string;
    evidence_pointers: EvidencePointer[];
    expected_metric_deltas: Record<string, number>;
}

export interface ImprovementRun {
    run_id: string;
    target_object_id: string;
    target_kind: TargetKind;
    baseline_version_n: number;
    new_version_n: number | null;
    mode: string;
    tuner_model: string;
    decision: 'keep' | 'revert' | 'pending';
    created_at: string;
    closed_at: string | null;
    proposed_diff?: ProposedDiff | null;
}

export interface VersionSnapshot {
    object_id: string;
    version_n: number;
    parent_version_n: number | null;
    is_active: boolean;
    improvement_run_id: string | null;
    metric_snapshot: Record<string, number> | null;
    config: Record<string, unknown>;
}

export interface Insight {
    id: string;
    kind: string;
    detector: string;
    severity: 'high' | 'medium' | 'low';
    learning: string;
    value: unknown;
    evidence: EvidencePointer[];
}

export interface DetectorSlot {
    numerator: number;
    denominator: number;
    rate: number | 'N/A';
}

// ── Checkpoint 6 — outcome grading ───────────────────────────────────────────

export type GradingMode = 'deterministic' | 'rubric' | null;
export type GradingStrictness = 'strict' | 'mixed';
export type Split = 'train' | 'holdout' | 'regression';
export type RubricCriterionKind = 'key_point_coverage' | 'anchored' | 'deterministic';

export interface RubricCriterion {
    id: string;
    kind: RubricCriterionKind;
    weight: number;
    critical?: boolean;
    critical_floor?: number;
    question?: string;
    scale?: number;
    anchors?: Record<string, string>;
    check?: EmbeddedCheck;
}

export interface ExtractSpec {
    from?: string;
    tool?: string;
    arg?: string;
    occurrence?: 'first' | 'last' | 'any';
    regex?: string;
}

export interface CompareSpec {
    type?: string;
    value?: unknown;
    reference?: string;
    tol?: number;
    float_tol?: number;
    case_sensitive?: boolean;
    dialect?: string;
    order_sensitive?: 'auto' | boolean;
    column_match?: 'by_position' | 'by_name';
    options?: CompareSpec[];
}

// A §6.3 check embedded inside a rubric criterion (`kind: 'deterministic'`).
export interface EmbeddedCheck {
    extract?: ExtractSpec;
    compare?: CompareSpec;
}

export interface Rubric {
    id: string;
    name: string;
    version: number;
    // Two benchmark scores measured under different content hashes are NOT
    // comparable — IMPROVE_RATCHET_DECIDE refuses to compare them.
    content_hash: string;
    created_at?: string;
    criteria: RubricCriterion[];
}

export interface RubricVersionRef {
    version: number;
    content_hash: string;
    created_at?: string;
    name?: string;
}

export interface CheckResult {
    check_id: string;
    // `extraction_failed` is NOT a wrong answer — it means the agent never
    // produced the thing the extractor was looking for.
    status: 'pass' | 'fail' | 'extraction_failed' | 'execution_timeout' | 'row_cap_exceeded' | 'judge_na';
    weight: number;
    critical: boolean;
    detail?: string;
    normalized?: number | null;
    trace_file?: string | null;
    message_idx?: number | null;
}

export interface InputOutcome {
    input_id: string;
    score: number | null;   // null == N/A, never 0
    na_reason: string | null;
    vetoed: boolean;
    checks: CheckResult[];
    split?: Split;
    effective_split?: Split;
    fold?: number | null;
}

export interface OutcomeScores {
    process_score?: number | null;
    outcome_score?: number | null;
    composite_score?: number | null;
    grading_mode?: GradingMode;
    grading_strictness?: GradingStrictness;
    rubric_id?: string | null;
    rubric_version?: number | null;
    rubric_content_hash?: string | null;
    snapshot_id?: string | null;
    scores_by_split?: Partial<Record<Split, number | null>>;
    scores_by_fold?: (number | null)[] | null;
    fold_stddev?: number | null;
    fold_index?: number | null;
    extraction_failed_count?: number;
    extraction_failed_rate?: number;
    outcome_na?: boolean;
    incomparable_reason?: string | null;
    judge_model?: string | null;
    judge_cache_hits?: number;
    judge_spend_usd?: number;
    per_input?: InputOutcome[];
}

export interface AugmentedVariant {
    id: string;
    parent_input_id: string;
    is_augmented: true;
    approved: boolean;
    prompt: string;
    split: Split;
    fold?: number | null;
    weight: number;
}

export function fmtValue(v: unknown): string {
    if (v === null || v === undefined) return '—';
    if (typeof v === 'string') return v;
    return JSON.stringify(v, null, 2);
}
