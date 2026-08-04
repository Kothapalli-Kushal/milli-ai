'use client';
// Self-Improvement — BenchmarkEditor (Checkpoint 4, checklist 4.10).
// Authoring UI for standalone, reusable benchmark suites, plus a run button
// that executes the suite against the current editor target.
import { useCallback, useEffect, useState } from 'react';
import { FlaskConical, Loader2, Play, Plus, Save, Trash } from 'lucide-react';
import type { TargetKind } from './types';

// Metric names the scorer understands: `success` + the detector registry.
const KNOWN_METRICS = [
    'success', 'clean_success', 'recovery', 'loops', 'give_up', 'errors',
    'duration_outlier', 'token_usage', 'sequentialthinking_cap_hit',
    'hallucinated_tool_rate', 'compaction_thrash', 'sticky_arg_conflict',
    'delegate_pingpong', 'mcp_ping_timeout_rate', 'browser_state_stale_rate',
];

interface BenchmarkSuite {
    id: string;
    name: string;
    target_object_id: string;
    inputs: { prompt: string; expected_metric_hints?: Record<string, number> }[];
    scorer: { metrics: Record<string, number> };
}

interface BenchmarkResult {
    run_id: string;
    benchmark_id: string;
    score: number | null;
    target_version_n: number;
    per_metric: Record<string, { rate: number | 'N/A'; weight: number; numerator: number; denominator: number }>;
    trace_count: number;
}

interface BenchmarkEditorProps {
    targetId: string;
    targetKind: TargetKind;
    onRan?: () => void; // lets VersionHistory refresh its score chips
}

function newSuite(targetId: string): BenchmarkSuite {
    return {
        id: `bench_${Date.now()}`,
        name: '',
        target_object_id: targetId,
        inputs: [{ prompt: '' }],
        scorer: { metrics: { success: 1, clean_success: 1 } },
    };
}

export function BenchmarkEditor({ targetId, onRan }: BenchmarkEditorProps) {
    const [suites, setSuites] = useState<BenchmarkSuite[]>([]);
    const [draft, setDraft] = useState<BenchmarkSuite | null>(null);
    const [busy, setBusy] = useState(false);
    const [runningId, setRunningId] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [lastResult, setLastResult] = useState<BenchmarkResult | null>(null);

    const load = useCallback(async () => {
        try {
            const res = await fetch('/api/improve/benchmarks');
            if (res.ok) setSuites(await res.json());
        } catch { /* keep current list */ }
    }, []);

    useEffect(() => { setDraft(null); setLastResult(null); load(); }, [load, targetId]);

    const save = async () => {
        if (!draft) return;
        setBusy(true);
        setError(null);
        try {
            const res = await fetch(`/api/improve/benchmark/${encodeURIComponent(draft.id)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(draft),
            });
            const body = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
            setDraft(null);
            await load();
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Save failed');
        } finally {
            setBusy(false);
        }
    };

    const remove = async (id: string) => {
        setBusy(true);
        try {
            await fetch(`/api/improve/benchmark/${encodeURIComponent(id)}`, { method: 'DELETE' });
            if (draft?.id === id) setDraft(null);
            await load();
        } finally {
            setBusy(false);
        }
    };

    const run = async (id: string) => {
        setRunningId(id);
        setError(null);
        try {
            // Run against the object open in this editor (suites are reusable).
            const res = await fetch(`/api/improve/benchmark/${encodeURIComponent(id)}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ target_object_id: targetId }),
            });
            const body = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
            setLastResult(body);
            onRan?.();
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Run failed');
        } finally {
            setRunningId(null);
        }
    };

    const setMetric = (name: string, weight: number) => {
        if (!draft) return;
        setDraft({ ...draft, scorer: { metrics: { ...draft.scorer.metrics, [name]: weight } } });
    };

    return (
        <div className="space-y-3">
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5 text-xs font-bold text-white">
                    <FlaskConical className="h-3.5 w-3.5" /> Benchmarks
                </div>
                {!draft && (
                    <button
                        onClick={() => setDraft(newSuite(targetId))}
                        className="flex items-center gap-1 px-2 py-1 text-[10px] font-bold border border-zinc-800 text-zinc-400 hover:border-zinc-500 hover:text-white"
                    >
                        <Plus className="h-3 w-3" /> New Suite
                    </button>
                )}
            </div>

            {error && <div className="text-[10px] text-red-400">{error}</div>}

            {/* Suite list */}
            {!draft && (
                <div className="space-y-1.5">
                    {suites.map(s => (
                        <div key={s.id} className="flex items-center gap-2 p-2 border border-zinc-800 bg-zinc-950">
                            <div className="flex-1 min-w-0">
                                <div className="text-[10px] font-bold text-white truncate">{s.name || s.id}</div>
                                <div className="text-[9px] text-zinc-500">
                                    {s.inputs.length} input{s.inputs.length === 1 ? '' : 's'} · default target{' '}
                                    <span className="font-mono">{s.target_object_id}</span>
                                    {s.target_object_id !== targetId && ' (will run against this editor\u2019s target)'}
                                </div>
                            </div>
                            <button
                                onClick={() => run(s.id)}
                                disabled={runningId !== null || busy}
                                className="flex items-center gap-1 px-2 py-1 text-[9px] font-bold bg-white text-black hover:bg-zinc-200 disabled:opacity-50"
                            >
                                {runningId === s.id ? <Loader2 className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />}
                                Run
                            </button>
                            <button
                                onClick={() => setDraft(JSON.parse(JSON.stringify(s)))}
                                disabled={busy}
                                className="px-2 py-1 text-[9px] font-bold border border-zinc-800 text-zinc-400 hover:border-zinc-500"
                            >
                                Edit
                            </button>
                            <button
                                onClick={() => remove(s.id)}
                                disabled={busy}
                                className="p-1 text-zinc-600 hover:text-red-400"
                            >
                                <Trash className="h-3 w-3" />
                            </button>
                        </div>
                    ))}
                    {suites.length === 0 && (
                        <p className="text-[10px] text-zinc-600">No benchmark suites yet — create one to measure improvements objectively.</p>
                    )}
                </div>
            )}

            {/* Last run result */}
            {lastResult && !draft && (
                <div className="p-2 border border-zinc-700 bg-zinc-950 space-y-1">
                    <div className="text-[10px] text-white font-bold">
                        Score: <span className="font-mono">{lastResult.score ?? 'N/A'}</span>
                        <span className="text-zinc-500 font-normal"> · v{lastResult.target_version_n} · {lastResult.trace_count} trace{lastResult.trace_count === 1 ? '' : 's'}</span>
                    </div>
                    <div className="flex flex-wrap gap-1">
                        {Object.entries(lastResult.per_metric).map(([name, m]) => (
                            <span key={name} className="px-1.5 py-0.5 text-[9px] font-mono border border-zinc-800 text-zinc-400">
                                {name} {m.rate === 'N/A' ? 'N/A' : m.rate} (w{m.weight})
                            </span>
                        ))}
                    </div>
                </div>
            )}

            {/* Authoring form */}
            {draft && (
                <div className="p-2 border border-zinc-800 bg-zinc-950 space-y-3">
                    <div className="grid grid-cols-2 gap-2">
                        <input
                            value={draft.name}
                            onChange={e => setDraft({ ...draft, name: e.target.value })}
                            placeholder="Suite name"
                            className="bg-black border border-zinc-800 px-2 py-1.5 text-[10px] text-white focus:border-white focus:outline-none"
                        />
                        <input
                            value={draft.target_object_id}
                            onChange={e => setDraft({ ...draft, target_object_id: e.target.value })}
                            placeholder="Default target object id"
                            className="bg-black border border-zinc-800 px-2 py-1.5 text-[10px] font-mono text-white focus:border-white focus:outline-none"
                        />
                    </div>

                    <div className="space-y-1.5">
                        <div className="text-[9px] font-bold text-zinc-500 uppercase">Inputs</div>
                        {draft.inputs.map((inp, i) => (
                            <div key={i} className="flex gap-1.5">
                                <textarea
                                    value={inp.prompt}
                                    onChange={e => {
                                        const inputs = [...draft.inputs];
                                        inputs[i] = { ...inputs[i], prompt: e.target.value };
                                        setDraft({ ...draft, inputs });
                                    }}
                                    rows={2}
                                    placeholder={`Prompt #${i + 1}`}
                                    className="flex-1 bg-black border border-zinc-800 px-2 py-1.5 text-[10px] text-white focus:border-white focus:outline-none resize-y"
                                />
                                <button
                                    onClick={() => setDraft({ ...draft, inputs: draft.inputs.filter((_, j) => j !== i) })}
                                    disabled={draft.inputs.length <= 1}
                                    className="p-1 text-zinc-600 hover:text-red-400 disabled:opacity-30"
                                >
                                    <Trash className="h-3 w-3" />
                                </button>
                            </div>
                        ))}
                        <button
                            onClick={() => setDraft({ ...draft, inputs: [...draft.inputs, { prompt: '' }] })}
                            className="flex items-center gap-1 px-2 py-1 text-[9px] font-bold border border-zinc-800 text-zinc-400 hover:border-zinc-500"
                        >
                            <Plus className="h-3 w-3" /> Add Input
                        </button>
                    </div>

                    <div className="space-y-1.5">
                        <div className="text-[9px] font-bold text-zinc-500 uppercase">
                            Scorer weights <span className="normal-case font-normal">(0 excludes a detector)</span>
                        </div>
                        <div className="grid grid-cols-3 gap-1.5">
                            {KNOWN_METRICS.map(name => (
                                <label key={name} className="flex items-center gap-1.5 text-[9px] font-mono text-zinc-400">
                                    <input
                                        type="number"
                                        min={0}
                                        step={0.5}
                                        value={draft.scorer.metrics[name] ?? 0}
                                        onChange={e => setMetric(name, parseFloat(e.target.value) || 0)}
                                        className="w-14 bg-black border border-zinc-800 px-1 py-0.5 text-[9px] text-white focus:border-white focus:outline-none"
                                    />
                                    <span className={draft.scorer.metrics[name] ? 'text-zinc-300' : 'text-zinc-600'}>{name}</span>
                                </label>
                            ))}
                        </div>
                    </div>

                    <div className="flex items-center gap-2">
                        <button
                            onClick={save}
                            disabled={busy || !draft.inputs.some(i => i.prompt.trim())}
                            className="flex items-center gap-1.5 px-3 py-1.5 bg-white text-black text-[10px] font-bold hover:bg-zinc-200 disabled:opacity-50"
                        >
                            {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
                            Save Suite
                        </button>
                        <button
                            onClick={() => setDraft(null)}
                            disabled={busy}
                            className="px-3 py-1.5 border border-zinc-800 text-zinc-400 text-[10px] font-bold hover:border-zinc-500"
                        >
                            Cancel
                        </button>
                    </div>
                </div>
            )}
        </div>
    );
}
