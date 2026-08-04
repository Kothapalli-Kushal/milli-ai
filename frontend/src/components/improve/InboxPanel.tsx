'use client';
// Self-Improvement Inbox (Checkpoint 5, checklist 5.9) — notification + audit
// log for every autonomous apply, revert, and safeguard stop. Never silent.
import { useCallback, useEffect, useState } from 'react';
import { Inbox, Loader2, RefreshCw } from 'lucide-react';

interface InboxEntry {
    event_id: string;
    timestamp: string;
    run_id: string | null;
    object_id: string | null;
    version_n: number | null;
    kind: string;
    mode: string;
    score_delta: number | null;
    message: string;
}

const KIND_STYLE: Record<string, string> = {
    apply: 'text-green-400 border-green-900',
    revert: 'text-yellow-400 border-yellow-900',
    budget_abort: 'text-red-400 border-red-900',
    plateau_stop: 'text-orange-400 border-orange-900',
    timeout_stop: 'text-red-400 border-red-900',
    max_iterations_stop: 'text-orange-400 border-orange-900',
};

interface InboxPanelProps {
    objectId?: string; // scope to one agent/orchestration; omit for everything
}

export function InboxPanel({ objectId }: InboxPanelProps) {
    const [entries, setEntries] = useState<InboxEntry[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const qs = objectId ? `?object_id=${encodeURIComponent(objectId)}` : '';
            const res = await fetch(`/api/improve/inbox${qs}`);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            setEntries(await res.json());
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Failed to load inbox');
        } finally {
            setLoading(false);
        }
    }, [objectId]);

    useEffect(() => { load(); }, [load]);

    return (
        <div className="space-y-2">
            <div className="flex items-center gap-1.5 text-xs font-bold text-white">
                <Inbox className="h-3.5 w-3.5" /> Self-Improvement Inbox
                {loading && <Loader2 className="h-3 w-3 animate-spin text-zinc-500" />}
                <button onClick={load} className="ml-auto text-zinc-500 hover:text-white" title="Refresh">
                    <RefreshCw className="h-3 w-3" />
                </button>
            </div>
            {error && <div className="text-[10px] text-red-400">{error}</div>}

            {entries.length === 0 && !loading ? (
                <p className="text-[10px] text-zinc-600">
                    No notifications — autonomous applies, reverts, and safeguard stops land here.
                </p>
            ) : (
                <div className="space-y-1.5 max-h-64 overflow-y-auto">
                    {entries.map(e => (
                        <div key={e.event_id} className="flex items-start gap-2 p-2 border border-zinc-800 bg-zinc-950">
                            <span className={`px-1 py-0.5 text-[9px] font-bold font-mono border shrink-0 ${KIND_STYLE[e.kind] ?? 'text-zinc-400 border-zinc-700'}`}>
                                {e.kind.toUpperCase()}
                            </span>
                            <div className="min-w-0 flex-1 space-y-0.5">
                                <p className="text-[10px] text-zinc-300 leading-snug">{e.message}</p>
                                <p className="text-[9px] text-zinc-600 font-mono truncate">
                                    {e.timestamp}
                                    {e.object_id && ` · ${e.object_id}`}
                                    {e.version_n !== null && ` · v${e.version_n}`}
                                    {e.score_delta !== null && ` · Δ${e.score_delta}`}
                                    {` · ${e.mode}`}
                                </p>
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
