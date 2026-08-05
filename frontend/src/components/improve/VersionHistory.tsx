'use client';
// Self-Improvement — VersionHistory (Checkpoint 3, checklist 3.16).
// Version snapshots for one agent/orchestration with metric snapshots,
// benchmark scores per version (Checkpoint 4, checklist 4.11; extended for
// Checkpoint 6, checklists 6.32/6.33/6.43: process/outcome/composite chips,
// train/holdout/regression breakdown, extraction-failure rate, unpinned flag,
// and failing-check rows linked to their trace evidence), one-click JSON
// rollback, and bulk revert of autonomous edits (Checkpoint 5, 5.19).
import { useCallback, useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, FlaskConical, History, Loader2, RotateCcw, Undo2 } from 'lucide-react';
import { EvidenceChips } from './InsightsPanel';
import type { InputOutcome, OutcomeScores, Split, TargetKind, VersionSnapshot } from './types';

interface VersionHistoryProps {
    targetId: string;
    targetKind: TargetKind;
    refreshKey?: number; // bump to force reload after an apply
    onRolledBack?: () => void;
}

interface BenchResult extends OutcomeScores {
    run_id?: string;
    benchmark_id: string;
    score: number | null;
}

const SPLIT_ORDER: Split[] = ['train', 'holdout', 'regression'];
const fmt = (v?: number | null) => (v === null || v === undefined ? 'N/A' : Number(v).toFixed(3));

// Non-pass statuses worth a row. `extraction_failed` is rendered distinctly:
// it means the extractor found nothing, NOT that the agent was wrong (§6.6).
const ROW_STATUSES = new Set(['fail', 'execution_timeout', 'row_cap_exceeded', 'extraction_failed', 'judge_na']);

function FailingCheckRows({ perInput }: { perInput: InputOutcome[] }) {
    const [open, setOpen] = useState(false);
    const rows = perInput.flatMap(inp =>
        (inp.checks ?? [])
            .filter(c => ROW_STATUSES.has(c.status))
            .map(c => ({ inp, c }))
    );
    if (rows.length === 0) return null;
    return (
        <div>
            <button
                onClick={() => setOpen(!open)}
                className="flex items-center gap-1 text-[9px] font-mono text-zinc-500 hover:text-zinc-300"
            >
                {open ? <ChevronDown className="h-2.5 w-2.5" /> : <ChevronRight className="h-2.5 w-2.5" />}
                {rows.length} failing check{rows.length === 1 ? '' : 's'}
            </button>
            {open && (
                <div className="mt-1 space-y-1 pl-3 border-l border-zinc-800">
                    {rows.map(({ inp, c }, i) => (
                        <div key={i} className="text-[9px] font-mono">
                            <span className="text-zinc-400">{inp.input_id}</span>
                            <span className="text-zinc-600"> / </span>
                            <span className="text-zinc-300">{c.check_id}</span>
                            <span className={c.status === 'extraction_failed' ? 'text-amber-400' : 'text-red-400'}> {c.status}</span>
                            {inp.vetoed && c.critical && <span className="text-red-400 font-bold"> · VETO</span>}
                            {c.detail && <span className="text-zinc-600"> — {c.detail}</span>}
                            {/* Evidence-first (3.17): every failing check links to its trace */}
                            {c.trace_file && (
                                <EvidenceChips evidence={[{ trace_file: c.trace_file, message_idx: c.message_idx ?? null }]} />
                            )}
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}

export function VersionHistory({ targetId, refreshKey, onRolledBack }: VersionHistoryProps) {
    const [versions, setVersions] = useState<VersionSnapshot[]>([]);
    const [benchScores, setBenchScores] = useState<Record<number, BenchResult[]>>({});
    const [loading, setLoading] = useState(false);
    const [busyVersion, setBusyVersion] = useState<number | null>(null);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const res = await fetch(`/api/improve/versions/${encodeURIComponent(targetId)}`);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const body = await res.json();
            setVersions((body.versions ?? []).slice().reverse()); // newest first
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Failed to load versions');
        } finally {
            setLoading(false);
        }
        // Benchmark results widget (4.11) — latest scores grouped by version,
        // carrying the full CP6 record when present (6.32).
        try {
            const res = await fetch(`/api/improve/benchmark/results?target_object_id=${encodeURIComponent(targetId)}`);
            if (res.ok) {
                const results = await res.json();
                const byVersion: Record<number, BenchResult[]> = {};
                for (const r of results) {
                    (byVersion[r.target_version_n] ??= []).push(r);
                }
                setBenchScores(byVersion);
            }
        } catch { /* scores are optional decoration */ }
    }, [targetId]);

    useEffect(() => { load(); }, [load, refreshKey]);

    const rollback = async (versionN: number) => {
        setBusyVersion(versionN);
        setError(null);
        try {
            const res = await fetch(`/api/improve/rollback/${encodeURIComponent(targetId)}/${versionN}`, { method: 'POST' });
            const body = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
            await load();
            onRolledBack?.();
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Rollback failed');
        } finally {
            setBusyVersion(null);
        }
    };

    // "Revert all autonomous edits since T" (Checkpoint 5, checklist 5.19)
    const [revertSince, setRevertSince] = useState('');
    const [revertBusy, setRevertBusy] = useState(false);
    const revertAutonomous = async () => {
        if (!revertSince) return;
        setRevertBusy(true);
        setError(null);
        try {
            const since = new Date(revertSince).toISOString();
            const res = await fetch('/api/improve/revert-autonomous', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ since, object_id: targetId }),
            });
            const body = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
            await load();
            onRolledBack?.();
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Bulk revert failed');
        } finally {
            setRevertBusy(false);
        }
    };

    return (
        <div className="space-y-2">
            <div className="flex items-center gap-1.5 text-xs font-bold text-white">
                <History className="h-3.5 w-3.5" /> Version History
                {loading && <Loader2 className="h-3 w-3 animate-spin text-zinc-500" />}
            </div>
            {error && <div className="text-[10px] text-red-400">{error}</div>}

            {versions.length === 0 && !loading ? (
                <p className="text-[10px] text-zinc-600">
                    No versions yet — versions are snapshotted when an improvement is applied.
                </p>
            ) : (
                <div className="space-y-1.5">
                    {versions.map(v => (
                        <div
                            key={v.version_n}
                            className={`flex items-center gap-3 p-2 border bg-zinc-950 ${v.is_active ? 'border-white' : 'border-zinc-800'}`}
                        >
                            <span className="text-[10px] font-mono font-bold text-white w-8">v{v.version_n}</span>
                            {v.is_active && (
                                <span className="px-1.5 py-0.5 text-[9px] font-bold bg-white text-black">ACTIVE</span>
                            )}
                            <div className="flex-1 min-w-0 space-y-0.5">
                                {v.improvement_run_id && (
                                    <div className="text-[9px] text-zinc-500 font-mono truncate">
                                        run {v.improvement_run_id}
                                        {v.parent_version_n !== null && ` · from v${v.parent_version_n}`}
                                    </div>
                                )}
                                {v.metric_snapshot && Object.keys(v.metric_snapshot).length > 0 && (
                                    <div className="flex flex-wrap gap-1">
                                        {Object.entries(v.metric_snapshot).map(([k, d]) => (
                                            <span key={k} className={`px-1 py-0.5 text-[9px] font-mono border ${d < 0 ? 'text-green-400 border-green-900' : 'text-yellow-400 border-yellow-900'}`}>
                                                {k} {d > 0 ? '+' : ''}{d}
                                            </span>
                                        ))}
                                    </div>
                                )}
                                {(benchScores[v.version_n] ?? []).map((b, i) => (
                                    <div key={i} className="space-y-0.5">
                                        <div className="flex flex-wrap items-center gap-1">
                                            <span className="inline-flex items-center gap-1 px-1 py-0.5 text-[9px] font-mono border border-blue-900 text-blue-400">
                                                <FlaskConical className="h-2.5 w-2.5" />
                                                {b.benchmark_id}: {b.score ?? 'N/A'}
                                            </span>
                                            {b.outcome_score !== undefined && (
                                                <>
                                                    <span className="px-1 py-0.5 text-[9px] font-mono border border-zinc-800 text-zinc-400">
                                                        proc {fmt(b.process_score)}
                                                    </span>
                                                    <span className="px-1 py-0.5 text-[9px] font-mono border border-zinc-800 text-zinc-400">
                                                        out {b.outcome_na ? 'N/A' : fmt(b.outcome_score)}
                                                    </span>
                                                    <span className="px-1 py-0.5 text-[9px] font-mono border border-zinc-800 text-zinc-300">
                                                        comp {fmt(b.composite_score)}
                                                    </span>
                                                </>
                                            )}
                                            {SPLIT_ORDER.filter(s => b.scores_by_split?.[s] !== undefined).map(s => (
                                                <span key={s} className={`px-1 py-0.5 text-[9px] font-mono border ${s === 'holdout' ? 'border-purple-900 text-purple-400' : 'border-zinc-800 text-zinc-400'}`}
                                                    title={s === 'holdout' ? 'The ratchet decides on holdout' : undefined}>
                                                    {s} {fmt(b.scores_by_split![s])}
                                                </span>
                                            ))}
                                            {b.grading_strictness === 'mixed' && (
                                                <span className="px-1 py-0.5 text-[9px] font-mono border border-amber-900 text-amber-400"
                                                    title="Contains semantic_match — the rubric variance threshold applies">
                                                    mixed
                                                </span>
                                            )}
                                            {b.snapshot_id === 'unpinned' && (
                                                <span className="px-1 py-0.5 text-[9px] font-mono border border-amber-900 text-amber-400"
                                                    title="No pinned DB snapshot — outcome scores are not exactly reproducible">
                                                    unpinned
                                                </span>
                                            )}
                                            {(b.extraction_failed_rate ?? 0) > 0 && (
                                                <span className="px-1 py-0.5 text-[9px] font-mono border border-red-900 text-red-400"
                                                    title="A high rate almost always means misconfigured extractors, not a bad agent">
                                                    extraction failed {((b.extraction_failed_rate ?? 0) * 100).toFixed(0)}%
                                                </span>
                                            )}
                                            {b.incomparable_reason && (
                                                <span className="px-1 py-0.5 text-[9px] font-mono border border-red-900 text-red-400" title={b.incomparable_reason}>
                                                    incomparable
                                                </span>
                                            )}
                                        </div>
                                        {b.per_input && <FailingCheckRows perInput={b.per_input} />}
                                    </div>
                                ))}
                            </div>
                            {!v.is_active && (
                                <button
                                    onClick={() => rollback(v.version_n)}
                                    disabled={busyVersion !== null}
                                    className="flex items-center gap-1 px-2 py-1 text-[9px] font-bold border border-zinc-800 text-zinc-400 hover:border-zinc-500 hover:text-white disabled:opacity-50"
                                >
                                    {busyVersion === v.version_n
                                        ? <Loader2 className="h-3 w-3 animate-spin" />
                                        : <RotateCcw className="h-3 w-3" />}
                                    Rollback
                                </button>
                            )}
                        </div>
                    ))}
                </div>
            )}

            {versions.length > 0 && (
                <div className="flex items-center gap-2 pt-1">
                    <input
                        type="datetime-local"
                        value={revertSince}
                        onChange={e => setRevertSince(e.target.value)}
                        className="bg-zinc-950 border border-zinc-800 px-1.5 py-1 text-[10px] text-zinc-300 outline-none focus:border-zinc-500"
                    />
                    <button
                        onClick={revertAutonomous}
                        disabled={!revertSince || revertBusy}
                        className="flex items-center gap-1 px-2 py-1 text-[9px] font-bold border border-yellow-900 text-yellow-400 hover:border-yellow-500 disabled:opacity-50"
                        title="Undo every autonomously-applied version since this time (audited in the inbox)"
                    >
                        {revertBusy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Undo2 className="h-3 w-3" />}
                        Revert autonomous edits since
                    </button>
                </div>
            )}
        </div>
    );
}
