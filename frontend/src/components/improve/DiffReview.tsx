'use client';
// Self-Improvement — DiffReview (Checkpoint 3, checklist 3.15/3.17).
// Runs the tuner (propose), then renders the ProposedDiff as a side-by-side
// per-field diff with individual approve/reject toggles. Every row links back
// to the evidence (trace file + message index) that motivated it.
import { useCallback, useEffect, useState } from 'react';
import { Check, Loader2, Sparkles, X } from 'lucide-react';
import { EvidenceChips } from './InsightsPanel';
import type { ImprovementRun, ProposedDiff, TargetKind } from './types';
import { fmtValue } from './types';

interface DiffReviewProps {
    targetId: string;
    targetKind: TargetKind;
    onApplied?: () => void;
}

export function DiffReview({ targetId, targetKind, onApplied }: DiffReviewProps) {
    const [tunerModel, setTunerModel] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [run, setRun] = useState<ImprovementRun | null>(null);
    const [diff, setDiff] = useState<ProposedDiff | null>(null);
    const [accepted, setAccepted] = useState<Set<string>>(new Set());

    // Re-open a pending review after reload (runs carry their proposed diff).
    const loadPending = useCallback(async () => {
        try {
            const res = await fetch(`/api/improve/versions/${encodeURIComponent(targetId)}`);
            if (!res.ok) return;
            const body = await res.json();
            const pending = (body.runs ?? []).find(
                (r: ImprovementRun) => r.decision === 'pending' && !r.closed_at && r.proposed_diff,
            );
            if (pending) {
                setRun(pending);
                setDiff(pending.proposed_diff);
                setAccepted(new Set(pending.proposed_diff.field_edits.map((e: { field: string }) => e.field)));
            }
        } catch { /* panel stays in propose state */ }
    }, [targetId]);

    useEffect(() => {
        setRun(null); setDiff(null); setError(null);
        loadPending();
    }, [loadPending]);

    const propose = async () => {
        setBusy(true);
        setError(null);
        try {
            const res = await fetch('/api/improve/propose', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    target_object_id: targetId,
                    target_kind: targetKind,
                    tuner_model: tunerModel.trim() || null,
                }),
            });
            const body = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
            setRun(body.run);
            setDiff(body.proposed_diff);
            setAccepted(new Set(body.proposed_diff.field_edits.map((e: { field: string }) => e.field)));
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Propose failed');
        } finally {
            setBusy(false);
        }
    };

    const decide = async (action: 'apply' | 'reject') => {
        if (!run) return;
        setBusy(true);
        setError(null);
        try {
            const res = await fetch('/api/improve/apply', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    run_id: run.run_id,
                    action,
                    accepted_fields: action === 'apply' ? Array.from(accepted) : null,
                }),
            });
            const body = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
            setRun(null);
            setDiff(null);
            onApplied?.();
        } catch (e) {
            setError(e instanceof Error ? e.message : `${action} failed`);
        } finally {
            setBusy(false);
        }
    };

    const toggle = (field: string) => {
        setAccepted(prev => {
            const next = new Set(prev);
            if (next.has(field)) next.delete(field); else next.add(field);
            return next;
        });
    };

    return (
        <div className="space-y-3">
            <div className="text-xs font-bold text-white">Propose &amp; Review</div>

            {!diff && (
                <div className="flex items-center gap-2">
                    <input
                        value={tunerModel}
                        onChange={e => setTunerModel(e.target.value)}
                        placeholder="Tuner model (blank = workspace default)"
                        className="flex-1 bg-zinc-950 border border-zinc-800 px-2 py-1.5 text-[10px] text-white focus:border-white focus:outline-none"
                    />
                    <button
                        onClick={propose}
                        disabled={busy}
                        className="flex items-center gap-1.5 px-3 py-1.5 bg-white text-black text-[10px] font-bold hover:bg-zinc-200 disabled:opacity-50"
                    >
                        {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Sparkles className="h-3 w-3" />}
                        Propose Improvement
                    </button>
                </div>
            )}

            {error && <div className="text-[10px] text-red-400">{error}</div>}

            {diff && run && (
                <div className="space-y-3">
                    <div className="p-2 border border-zinc-800 bg-zinc-950 space-y-1">
                        <div className="text-[9px] text-zinc-500">
                            run <span className="font-mono">{run.run_id}</span> · tuner model{' '}
                            <span className="font-mono text-zinc-300">{run.tuner_model}</span>
                        </div>
                        <p className="text-[10px] text-zinc-300">{diff.rationale}</p>
                        {Object.keys(diff.expected_metric_deltas ?? {}).length > 0 && (
                            <div className="flex flex-wrap gap-1">
                                {Object.entries(diff.expected_metric_deltas).map(([k, v]) => (
                                    <span key={k} className={`px-1.5 py-0.5 text-[9px] font-mono border ${v < 0 ? 'text-green-400 border-green-900' : 'text-yellow-400 border-yellow-900'}`}>
                                        {k} {v > 0 ? '+' : ''}{v}
                                    </span>
                                ))}
                            </div>
                        )}
                        <EvidenceChips evidence={diff.evidence_pointers ?? []} />
                    </div>

                    {/* Side-by-side per-field diff (3.15) */}
                    {diff.field_edits.map(edit => (
                        <div key={edit.field} className={`border ${accepted.has(edit.field) ? 'border-zinc-700' : 'border-zinc-900 opacity-60'}`}>
                            <div className="flex items-center gap-2 px-2 py-1.5 bg-zinc-950 border-b border-zinc-900">
                                <input
                                    type="checkbox"
                                    checked={accepted.has(edit.field)}
                                    onChange={() => toggle(edit.field)}
                                    className="accent-white"
                                />
                                <div className="flex-1 min-w-0">
                                    <div className="flex items-center gap-2">
                                        <span className="text-[10px] font-mono font-bold text-white">{edit.field}</span>
                                        {edit.rationale && <span className="text-[9px] text-zinc-500 truncate">— {edit.rationale}</span>}
                                    </div>
                                    <EvidenceChips evidence={diff.evidence_pointers ?? []} />
                                </div>
                            </div>
                            <div className="grid grid-cols-2 divide-x divide-zinc-900">
                                <pre className="p-2 text-[9px] text-red-300/80 bg-red-950/10 whitespace-pre-wrap break-words max-h-40 overflow-y-auto">{fmtValue(edit.old_value)}</pre>
                                <pre className="p-2 text-[9px] text-green-300/80 bg-green-950/10 whitespace-pre-wrap break-words max-h-40 overflow-y-auto">{fmtValue(edit.new_value)}</pre>
                            </div>
                        </div>
                    ))}

                    <div className="flex items-center gap-2">
                        <button
                            onClick={() => decide('apply')}
                            disabled={busy || accepted.size === 0}
                            className="flex items-center gap-1.5 px-3 py-1.5 bg-white text-black text-[10px] font-bold hover:bg-zinc-200 disabled:opacity-50"
                        >
                            {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3" />}
                            Apply {accepted.size}/{diff.field_edits.length} edit{accepted.size === 1 ? '' : 's'}
                        </button>
                        <button
                            onClick={() => decide('reject')}
                            disabled={busy}
                            className="flex items-center gap-1.5 px-3 py-1.5 border border-zinc-800 text-zinc-400 text-[10px] font-bold hover:border-zinc-500 disabled:opacity-50"
                        >
                            <X className="h-3 w-3" /> Reject All
                        </button>
                    </div>
                </div>
            )}
        </div>
    );
}
