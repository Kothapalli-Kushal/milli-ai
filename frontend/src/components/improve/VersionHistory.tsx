'use client';
// Self-Improvement — VersionHistory (Checkpoint 3, checklist 3.16).
// Version snapshots for one agent/orchestration with metric snapshots,
// benchmark scores per version (Checkpoint 4, checklist 4.11), one-click
// JSON rollback, and bulk revert of autonomous edits (Checkpoint 5, 5.19).
import { useCallback, useEffect, useState } from 'react';
import { FlaskConical, History, Loader2, RotateCcw, Undo2 } from 'lucide-react';
import type { TargetKind, VersionSnapshot } from './types';

interface VersionHistoryProps {
    targetId: string;
    targetKind: TargetKind;
    refreshKey?: number; // bump to force reload after an apply
    onRolledBack?: () => void;
}

export function VersionHistory({ targetId, refreshKey, onRolledBack }: VersionHistoryProps) {
    const [versions, setVersions] = useState<VersionSnapshot[]>([]);
    const [benchScores, setBenchScores] = useState<Record<number, { score: number | null; benchmark_id: string }[]>>({});
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
        // Benchmark results widget (4.11) — latest scores grouped by version.
        try {
            const res = await fetch(`/api/improve/benchmark/results?target_object_id=${encodeURIComponent(targetId)}`);
            if (res.ok) {
                const results = await res.json();
                const byVersion: Record<number, { score: number | null; benchmark_id: string }[]> = {};
                for (const r of results) {
                    (byVersion[r.target_version_n] ??= []).push({ score: r.score, benchmark_id: r.benchmark_id });
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
                                    <span key={i} className="inline-flex items-center gap-1 px-1 py-0.5 text-[9px] font-mono border border-blue-900 text-blue-400 mr-1">
                                        <FlaskConical className="h-2.5 w-2.5" />
                                        {b.benchmark_id}: {b.score ?? 'N/A'}
                                    </span>
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
