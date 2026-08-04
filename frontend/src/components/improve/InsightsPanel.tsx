'use client';
// Self-Improvement — InsightsPanel (Checkpoint 3, checklist 3.14).
// Read-only detector results + atomic learnings for one agent/orchestration,
// each learning linked to its evidence (trace file + message index).
import { useCallback, useEffect, useState } from 'react';
import { Loader2, RefreshCw } from 'lucide-react';
import type { DetectorSlot, Insight, TargetKind } from './types';

interface InsightsPanelProps {
    targetId: string;
    targetKind: TargetKind;
}

const SEVERITY_STYLE: Record<string, string> = {
    high: 'text-red-400 border-red-900',
    medium: 'text-yellow-400 border-yellow-900',
    low: 'text-zinc-400 border-zinc-700',
};

export function EvidenceChips({ evidence }: { evidence: { trace_file: string; message_idx: number | null }[] }) {
    if (!evidence?.length) return null;
    return (
        <div className="flex flex-wrap gap-1 mt-1">
            {evidence.slice(0, 6).map((e, i) => (
                <span
                    key={i}
                    title={`Trace ${e.trace_file}${e.message_idx !== null ? ` · message #${e.message_idx}` : ''}`}
                    className="px-1.5 py-0.5 text-[9px] font-mono border border-zinc-800 bg-zinc-900 text-zinc-500"
                >
                    {e.trace_file.split('/').pop()}{e.message_idx !== null ? `#${e.message_idx}` : ''}
                </span>
            ))}
            {evidence.length > 6 && (
                <span className="text-[9px] text-zinc-600">+{evidence.length - 6} more</span>
            )}
        </div>
    );
}

export function InsightsPanel({ targetId, targetKind }: InsightsPanelProps) {
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [traceCount, setTraceCount] = useState(0);
    const [detectors, setDetectors] = useState<Record<string, DetectorSlot>>({});
    const [insights, setInsights] = useState<Insight[]>([]);

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const param = targetKind === 'agent' ? 'agent_id' : 'orchestration_id';
            const res = await fetch(`/api/improve/insights?${param}=${encodeURIComponent(targetId)}`);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const body = await res.json();
            setTraceCount(body.report?.trace_count ?? 0);
            setDetectors(body.report?.detectors ?? {});
            setInsights(body.insights?.insights ?? []);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Failed to load insights');
        } finally {
            setLoading(false);
        }
    }, [targetId, targetKind]);

    useEffect(() => { load(); }, [load]);

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between">
                <div className="text-xs font-bold text-white">
                    Insights <span className="text-zinc-500 font-normal">· {traceCount} trace{traceCount === 1 ? '' : 's'}</span>
                </div>
                <button
                    onClick={load}
                    disabled={loading}
                    className="flex items-center gap-1 px-2 py-1 text-[10px] text-zinc-400 border border-zinc-800 hover:border-zinc-500 disabled:opacity-50"
                >
                    {loading ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
                    Refresh
                </button>
            </div>

            {error && <div className="text-[10px] text-red-400">{error}</div>}

            {/* Detector results table (3.14) */}
            <div className="border border-zinc-800">
                <table className="w-full text-[10px]">
                    <thead>
                        <tr className="text-left text-zinc-500 border-b border-zinc-800 bg-zinc-950">
                            <th className="px-2 py-1.5 font-bold">Detector</th>
                            <th className="px-2 py-1.5 font-bold text-right">Hits</th>
                            <th className="px-2 py-1.5 font-bold text-right">Applicable</th>
                            <th className="px-2 py-1.5 font-bold text-right">Rate</th>
                        </tr>
                    </thead>
                    <tbody>
                        {Object.entries(detectors).map(([name, slot]) => (
                            <tr key={name} className="border-b border-zinc-900 text-zinc-300">
                                <td className="px-2 py-1 font-mono">{name}</td>
                                <td className="px-2 py-1 text-right">{slot.numerator}</td>
                                <td className="px-2 py-1 text-right">{slot.denominator}</td>
                                <td className={`px-2 py-1 text-right font-mono ${slot.rate === 'N/A' ? 'text-zinc-600' : typeof slot.rate === 'number' && slot.rate >= 0.5 ? 'text-red-400' : 'text-zinc-300'}`}>
                                    {slot.rate === 'N/A' ? 'N/A' : slot.rate}
                                </td>
                            </tr>
                        ))}
                        {Object.keys(detectors).length === 0 && !loading && (
                            <tr><td colSpan={4} className="px-2 py-4 text-center text-zinc-600">No detector results yet — run this {targetKind} to generate traces.</td></tr>
                        )}
                    </tbody>
                </table>
            </div>

            {/* Atomic learnings with evidence (3.17) */}
            <div className="space-y-2">
                {insights.map((ins) => (
                    <div key={ins.id} className={`p-2 border bg-zinc-950 ${SEVERITY_STYLE[ins.severity] ?? SEVERITY_STYLE.low}`}>
                        <div className="flex items-center gap-2">
                            <span className="text-[9px] font-bold uppercase">{ins.severity}</span>
                            <span className="text-[10px] font-mono text-zinc-400">{ins.detector}</span>
                            <span className="text-[9px] text-zinc-600">{ins.kind}</span>
                        </div>
                        <p className="text-[10px] text-zinc-300 mt-1">{ins.learning}</p>
                        <EvidenceChips evidence={ins.evidence} />
                    </div>
                ))}
                {insights.length === 0 && !loading && (
                    <p className="text-[10px] text-zinc-600">No findings.</p>
                )}
            </div>
        </div>
    );
}
