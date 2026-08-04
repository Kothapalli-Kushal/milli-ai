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

export function fmtValue(v: unknown): string {
    if (v === null || v === undefined) return '—';
    if (typeof v === 'string') return v;
    return JSON.stringify(v, null, 2);
}
