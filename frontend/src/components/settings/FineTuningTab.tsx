'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, Bot, CheckCircle2, FlaskConical, Loader2, Play, Square, Workflow } from 'lucide-react';

import { BenchmarkEditor } from '@/components/improve/BenchmarkEditor';
import { InboxPanel } from '@/components/improve/InboxPanel';
import { RubricEditor } from '@/components/improve/RubricEditor';
import { VersionHistory } from '@/components/improve/VersionHistory';

type TargetKind = 'agent' | 'orchestration';

const BUILTIN_FINE_TUNE_ORCH_ID = 'orch_fine_tune_builtin';

interface TargetOption {
    id: string;
    name: string;
}

interface BenchmarkSuite {
    id: string;
    name?: string;
    target_object_id?: string;
}

interface BenchmarkResult {
    run_id: string;
    benchmark_id: string;
    score: number | null;
    created_at?: string;
    target_version_n?: number;
}

interface HumanField {
    name: string;
    type?: string;
    label?: string;
    options?: string[];
}

interface SseEvent {
    type?: string;
    run_id?: string;
    status?: string;
    step_name?: string;
    orch_step_id?: string;
    step_type?: string;
    prompt?: string;
    fields?: HumanField[];
    recorded_as?: string;
    benchmark_id?: string;
    score?: number | null;
    decision?: string;
    delta?: number | null;
    stop?: boolean;
    stop_reason?: string;
    error?: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === 'object' && value !== null;
}

function toTargetOptions(value: unknown): TargetOption[] {
    if (!Array.isArray(value)) return [];
    return value
        .filter(isRecord)
        .map((item) => ({
            id: String(item.id || ''),
            name: String(item.name || item.id || ''),
        }))
        .filter((item) => item.id.length > 0);
}

function toBenchmarkSuites(value: unknown): BenchmarkSuite[] {
    if (!Array.isArray(value)) return [];
    return value
        .filter(isRecord)
        .map((item) => ({
            id: String(item.id || ''),
            name: item.name ? String(item.name) : undefined,
            target_object_id: item.target_object_id ? String(item.target_object_id) : undefined,
        }))
        .filter((item) => item.id.length > 0);
}

function toBenchmarkResults(value: unknown): BenchmarkResult[] {
    if (!Array.isArray(value)) return [];
    return value
        .filter(isRecord)
        .map((item) => ({
            run_id: String(item.run_id || ''),
            benchmark_id: String(item.benchmark_id || ''),
            score: typeof item.score === 'number' ? item.score : null,
            created_at: item.created_at ? String(item.created_at) : undefined,
            target_version_n: typeof item.target_version_n === 'number' ? item.target_version_n : undefined,
        }))
        .filter((item) => item.run_id.length > 0 && item.benchmark_id.length > 0);
}

export function FineTuningTab() {
    const abortRef = useRef<AbortController | null>(null);

    const [targetKind, setTargetKind] = useState<TargetKind>('agent');
    const [targetId, setTargetId] = useState('');
    const [benchmarkId, setBenchmarkId] = useState('');
    const [improveMode, setImproveMode] = useState<'human' | 'autonomous'>('human');
    const [budgetUsd, setBudgetUsd] = useState('');
    const [ratchetThreshold, setRatchetThreshold] = useState('0');
    const [maxIterations, setMaxIterations] = useState('5');
    const [plateauPatience, setPlateauPatience] = useState('2');

    const [agents, setAgents] = useState<TargetOption[]>([]);
    const [orchestrations, setOrchestrations] = useState<TargetOption[]>([]);
    const [benchmarks, setBenchmarks] = useState<BenchmarkSuite[]>([]);
    const [latestResults, setLatestResults] = useState<BenchmarkResult[]>([]);

    const [loadingData, setLoadingData] = useState(false);
    const [loadError, setLoadError] = useState<string | null>(null);

    const [runId, setRunId] = useState<string | null>(null);
    const [runStatus, setRunStatus] = useState<'idle' | 'running' | 'paused' | 'completed' | 'failed' | 'cancelled'>('idle');
    const [runEvents, setRunEvents] = useState<string[]>([]);
    const [lastRatchetDecision, setLastRatchetDecision] = useState<string | null>(null);
    const [launchError, setLaunchError] = useState<string | null>(null);

    const [humanPrompt, setHumanPrompt] = useState<string | null>(null);
    const [humanFields, setHumanFields] = useState<HumanField[] | null>(null);
    const [humanValues, setHumanValues] = useState<Record<string, string>>({});

    const [refreshKey, setRefreshKey] = useState(0);

    const targetOptions = useMemo(() => {
        return targetKind === 'agent' ? agents : orchestrations;
    }, [targetKind, agents, orchestrations]);

    const builtinExists = useMemo(
        () => orchestrations.some((o) => o.id === BUILTIN_FINE_TUNE_ORCH_ID),
        [orchestrations]
    );

    const pushEvent = useCallback((line: string) => {
        setRunEvents((prev) => [...prev.slice(-79), line]);
    }, []);

    const loadLatestResults = useCallback(async (currentTargetId: string) => {
        if (!currentTargetId) {
            setLatestResults([]);
            return;
        }
        try {
            const res = await fetch(`/api/improve/benchmark/results?target_object_id=${encodeURIComponent(currentTargetId)}`);
            if (!res.ok) return;
            const rows = await res.json();
            const sorted = toBenchmarkResults(rows)
                .slice()
                .sort((a, b) => {
                    const at = String(a.created_at || '');
                    const bt = String(b.created_at || '');
                    return bt.localeCompare(at);
                })
                .slice(0, 8);
            setLatestResults(sorted);
        } catch {
            // optional panel, keep quiet
        }
    }, []);

    const loadData = useCallback(async () => {
        setLoadingData(true);
        setLoadError(null);
        try {
            const [agentsRes, orchsRes, benchesRes] = await Promise.all([
                fetch('/api/agents'),
                fetch('/api/orchestrations'),
                fetch('/api/improve/benchmarks'),
            ]);

            const agentsData = agentsRes.ok ? await agentsRes.json() : [];
            const orchData = orchsRes.ok ? await orchsRes.json() : [];
            const benchData = benchesRes.ok ? await benchesRes.json() : [];

            const nextAgents = toTargetOptions(agentsData);
            const nextOrchs = toTargetOptions(orchData);

            setAgents(nextAgents);
            setOrchestrations(nextOrchs);
            const parsedBenches = toBenchmarkSuites(benchData);
            setBenchmarks(parsedBenches);

            if (!targetId) {
                const seed = targetKind === 'agent' ? nextAgents[0]?.id : nextOrchs.find((o: TargetOption) => o.id !== BUILTIN_FINE_TUNE_ORCH_ID)?.id;
                if (seed) setTargetId(seed);
            }

            if (!benchmarkId) {
                const firstBench = parsedBenches[0]?.id;
                if (firstBench) setBenchmarkId(String(firstBench));
            }
        } catch (e) {
            setLoadError(e instanceof Error ? e.message : 'Failed to load fine-tuning data');
        } finally {
            setLoadingData(false);
        }
    }, [benchmarkId, targetId, targetKind]);

    useEffect(() => {
        loadData();
    }, [loadData]);

    useEffect(() => {
        if (targetId) {
            loadLatestResults(targetId);
        }
    }, [targetId, loadLatestResults, refreshKey]);

    const resetHumanPrompt = () => {
        setHumanPrompt(null);
        setHumanFields(null);
        setHumanValues({});
    };

    const handleSSEEvent = useCallback((data: SseEvent) => {
        switch (data.type) {
            case 'orchestration_start':
                setRunId(data.run_id || null);
                setRunStatus('running');
                pushEvent(`Started run ${data.run_id}`);
                break;
            case 'step_start':
                pushEvent(`Step: ${data.step_name || data.orch_step_id} (${data.step_type || 'unknown'})`);
                break;
            case 'human_input_required': {
                setRunStatus('paused');
                setHumanPrompt(data.prompt || 'Approval required');
                const fields = Array.isArray(data.fields) ? data.fields : [];
                setHumanFields(fields);
                const initValues: Record<string, string> = {};
                fields.forEach((f: HumanField) => {
                    if (f.name) initValues[f.name] = '';
                });
                setHumanValues(initValues);
                pushEvent('Paused for human review');
                break;
            }
            case 'benchmark_result':
                pushEvent(`Benchmark ${data.recorded_as || ''}: ${data.benchmark_id} => ${data.score ?? 'N/A'}`);
                break;
            case 'ratchet_decision':
                setLastRatchetDecision(data.decision || null);
                pushEvent(`Ratchet: ${data.decision} (delta ${data.delta ?? 'N/A'})`);
                if (data.stop) {
                    pushEvent(`Stop reason: ${data.stop_reason || 'none'}`);
                }
                break;
            case 'orchestration_complete':
                setRunStatus(data.status === 'completed' ? 'completed' : 'failed');
                pushEvent(`Run finished with status: ${data.status}`);
                abortRef.current?.abort();
                abortRef.current = null;
                setRefreshKey((k) => k + 1);
                if (targetId) {
                    loadLatestResults(targetId);
                }
                resetHumanPrompt();
                break;
            case 'orchestration_error':
                setRunStatus('failed');
                pushEvent(`Run error: ${data.error || 'Unknown error'}`);
                abortRef.current?.abort();
                abortRef.current = null;
                resetHumanPrompt();
                break;
            default:
                break;
        }
    }, [loadLatestResults, pushEvent, targetId]);

    const streamSSE = useCallback(async (url: string, body: Record<string, unknown>) => {
        const controller = new AbortController();
        abortRef.current = controller;

        try {
            const res = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
                signal: controller.signal,
            });
            if (!res.ok || !res.body) {
                setRunStatus('failed');
                pushEvent(`HTTP ${res.status} while streaming run`);
                return;
            }

            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop() || '';
                for (const line of lines) {
                    if (!line.startsWith('data: ')) continue;
                    try {
                        const event = JSON.parse(line.slice(6)) as SseEvent;
                        handleSSEEvent(event);
                    } catch {
                        // ignore malformed frames
                    }
                }
            }
        } catch (e: unknown) {
            if (!(isRecord(e) && e.name === 'AbortError')) {
                setRunStatus('failed');
                const message = isRecord(e) && typeof e.message === 'string' ? e.message : 'stream failed';
                pushEvent(`Connection error: ${message}`);
            }
        } finally {
            abortRef.current = null;
        }
    }, [handleSSEEvent, pushEvent]);

    const startRun = async () => {
        setLaunchError(null);

        if (!builtinExists) {
            setLaunchError('Built-in fine-tuning orchestration is missing: orch_fine_tune_builtin');
            return;
        }
        if (!targetId) {
            setLaunchError('Pick a target object before starting.');
            return;
        }
        if (!benchmarkId) {
            setLaunchError('Pick a benchmark before starting.');
            return;
        }

        const budget = budgetUsd.trim() === '' ? null : Number(budgetUsd);
        const threshold = Number(ratchetThreshold);
        const maxIters = Number(maxIterations);
        const plateau = Number(plateauPatience);

        if (budget !== null && (Number.isNaN(budget) || budget <= 0)) {
            setLaunchError('Budget must be a positive number when provided.');
            return;
        }
        if (Number.isNaN(threshold)) {
            setLaunchError('Ratchet threshold must be numeric.');
            return;
        }
        if (!Number.isInteger(maxIters) || maxIters < 1) {
            setLaunchError('Max iterations must be an integer >= 1.');
            return;
        }
        if (!Number.isInteger(plateau) || plateau < 1) {
            setLaunchError('Plateau patience must be an integer >= 1.');
            return;
        }

        setRunStatus('running');
        setRunId(null);
        setLastRatchetDecision(null);
        setRunEvents([]);
        resetHumanPrompt();

        await streamSSE(`/api/orchestrations/${BUILTIN_FINE_TUNE_ORCH_ID}/run`, {
            message: `Fine-tune ${targetKind}:${targetId}`,
            initial_state: {
                improve_target_kind: targetKind,
                improve_target_id: targetId,
                improve_benchmark_id: benchmarkId,
                improve_mode: improveMode,
                improve_budget_usd: budget,
                improve_ratchet_threshold: threshold,
                improve_ratchet_max_iterations: maxIters,
                improve_ratchet_plateau_patience: plateau,
            },
        });
    };

    const cancelRun = async () => {
        abortRef.current?.abort();
        abortRef.current = null;
        if (runId) {
            try {
                await fetch(`/api/orchestrations/runs/${runId}/cancel`, { method: 'POST' });
            } catch {
                // no-op
            }
        }
        setRunStatus('cancelled');
        pushEvent('Run cancelled by user');
        resetHumanPrompt();
    };

    const submitHumanInput = async () => {
        if (!runId || !humanFields || humanFields.length === 0) return;

        const response: Record<string, string> = {};
        humanFields.forEach((f) => {
            const value = humanValues[f.name] || '';
            if (f.name) response[f.name] = value;
            if (f.label && f.label !== f.name) response[f.label] = value;
        });

        setRunStatus('running');
        pushEvent('Human input submitted, resuming run');
        resetHumanPrompt();

        await streamSSE(`/api/orchestrations/runs/${runId}/human-input`, { response });
    };

    return (
        <div className="space-y-8">
            {loadError && (
                <div className="flex items-center gap-2 border border-red-900 bg-red-950/40 p-3 text-xs text-red-300">
                    <AlertTriangle className="h-4 w-4" />
                    {loadError}
                </div>
            )}

            {!builtinExists && !loadingData && (
                <div className="flex items-center gap-2 border border-amber-900 bg-amber-950/40 p-3 text-xs text-amber-300">
                    <AlertTriangle className="h-4 w-4" />
                    Built-in fine-tuning template not found. Expected orchestration id: {BUILTIN_FINE_TUNE_ORCH_ID}
                </div>
            )}

            <section className="space-y-3 border border-zinc-800 bg-zinc-950 p-4">
                <h2 className="text-sm font-bold text-zinc-100">Target</h2>
                <div className="grid gap-3 md:grid-cols-2">
                    <label className="space-y-1 text-xs text-zinc-400">
                        Target kind
                        <select
                            value={targetKind}
                            onChange={(e) => {
                                const next = e.target.value as TargetKind;
                                setTargetKind(next);
                                setTargetId('');
                            }}
                            className="w-full bg-black border border-zinc-800 px-2 py-2 text-xs text-zinc-200 outline-none focus:border-zinc-500"
                        >
                            <option value="agent">Agent</option>
                            <option value="orchestration">Orchestration</option>
                        </select>
                    </label>
                    <label className="space-y-1 text-xs text-zinc-400">
                        Target object
                        <select
                            value={targetId}
                            onChange={(e) => setTargetId(e.target.value)}
                            className="w-full bg-black border border-zinc-800 px-2 py-2 text-xs text-zinc-200 outline-none focus:border-zinc-500"
                        >
                            <option value="">Select target...</option>
                            {targetOptions
                                .filter((t) => t.id !== BUILTIN_FINE_TUNE_ORCH_ID)
                                .map((t) => (
                                    <option key={t.id} value={t.id}>{t.name} ({t.id})</option>
                                ))}
                        </select>
                    </label>
                </div>
            </section>

            <section className="space-y-3 border border-zinc-800 bg-zinc-950 p-4">
                <h2 className="flex items-center gap-2 text-sm font-bold text-zinc-100">
                    <FlaskConical className="h-4 w-4" /> Benchmark
                </h2>
                <label className="space-y-1 text-xs text-zinc-400 block">
                    Benchmark suite
                    <select
                        value={benchmarkId}
                        onChange={(e) => setBenchmarkId(e.target.value)}
                        className="w-full bg-black border border-zinc-800 px-2 py-2 text-xs text-zinc-200 outline-none focus:border-zinc-500"
                    >
                        <option value="">Select benchmark...</option>
                        {benchmarks.map((b) => (
                            <option key={b.id} value={b.id}>{b.name || b.id} ({b.id})</option>
                        ))}
                    </select>
                </label>
                {targetId && (
                    <BenchmarkEditor
                        targetId={targetId}
                        targetKind={targetKind}
                        onRan={() => setRefreshKey((k) => k + 1)}
                    />
                )}
                <RubricEditor />
            </section>

            <section className="space-y-3 border border-zinc-800 bg-zinc-950 p-4">
                <h2 className="text-sm font-bold text-zinc-100">Controls</h2>
                <div className="grid gap-3 md:grid-cols-2">
                    <label className="space-y-1 text-xs text-zinc-400">
                        Improve mode
                        <select
                            value={improveMode}
                            onChange={(e) => setImproveMode(e.target.value as 'human' | 'autonomous')}
                            className="w-full bg-black border border-zinc-800 px-2 py-2 text-xs text-zinc-200 outline-none focus:border-zinc-500"
                        >
                            <option value="human">human</option>
                            <option value="autonomous">autonomous</option>
                        </select>
                    </label>
                    <label className="space-y-1 text-xs text-zinc-400">
                        Budget (USD, optional)
                        <input
                            value={budgetUsd}
                            onChange={(e) => setBudgetUsd(e.target.value)}
                            placeholder="e.g. 2.50"
                            className="w-full bg-black border border-zinc-800 px-2 py-2 text-xs text-zinc-200 outline-none focus:border-zinc-500"
                        />
                    </label>
                    <label className="space-y-1 text-xs text-zinc-400">
                        Ratchet threshold
                        <input
                            value={ratchetThreshold}
                            onChange={(e) => setRatchetThreshold(e.target.value)}
                            className="w-full bg-black border border-zinc-800 px-2 py-2 text-xs text-zinc-200 outline-none focus:border-zinc-500"
                        />
                    </label>
                    <label className="space-y-1 text-xs text-zinc-400">
                        Max iterations
                        <input
                            value={maxIterations}
                            onChange={(e) => setMaxIterations(e.target.value)}
                            className="w-full bg-black border border-zinc-800 px-2 py-2 text-xs text-zinc-200 outline-none focus:border-zinc-500"
                        />
                    </label>
                    <label className="space-y-1 text-xs text-zinc-400 md:col-span-2">
                        Plateau patience
                        <input
                            value={plateauPatience}
                            onChange={(e) => setPlateauPatience(e.target.value)}
                            className="w-full bg-black border border-zinc-800 px-2 py-2 text-xs text-zinc-200 outline-none focus:border-zinc-500"
                        />
                    </label>
                </div>
            </section>

            <section className="space-y-3 border border-zinc-800 bg-zinc-950 p-4">
                <h2 className="text-sm font-bold text-zinc-100">Run</h2>
                {launchError && <p className="text-xs text-red-400">{launchError}</p>}
                <div className="flex flex-wrap items-center gap-2">
                    <button
                        onClick={startRun}
                        disabled={loadingData || runStatus === 'running' || !builtinExists}
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold border border-zinc-700 text-zinc-100 hover:border-zinc-500 disabled:opacity-50"
                    >
                        {runStatus === 'running' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
                        Start
                    </button>
                    <button
                        onClick={cancelRun}
                        disabled={runStatus !== 'running' && runStatus !== 'paused'}
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold border border-zinc-700 text-zinc-300 hover:border-zinc-500 disabled:opacity-40"
                    >
                        <Square className="h-3.5 w-3.5" /> Cancel
                    </button>
                    <span className="text-xs text-zinc-400">Status: {runStatus}</span>
                    {runId && <span className="text-[11px] text-zinc-500 font-mono">run {runId}</span>}
                    {lastRatchetDecision && (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[10px] border border-zinc-700 text-zinc-300">
                            <CheckCircle2 className="h-3 w-3" /> Last decision: {lastRatchetDecision}
                        </span>
                    )}
                </div>

                {humanPrompt && (
                    <div className="space-y-2 border border-zinc-800 bg-black p-3">
                        <p className="text-xs text-zinc-200">{humanPrompt}</p>
                        {(humanFields || []).map((f) => (
                            <label key={f.name} className="space-y-1 text-xs text-zinc-400 block">
                                {f.label || f.name}
                                {Array.isArray(f.options) && f.options.length > 0 ? (
                                    <select
                                        value={humanValues[f.name] || ''}
                                        onChange={(e) => setHumanValues((prev) => ({ ...prev, [f.name]: e.target.value }))}
                                        className="w-full bg-zinc-950 border border-zinc-800 px-2 py-2 text-xs text-zinc-200 outline-none focus:border-zinc-500"
                                    >
                                        <option value="">Select...</option>
                                        {f.options.map((opt) => <option key={opt} value={opt}>{opt}</option>)}
                                    </select>
                                ) : (
                                    <input
                                        value={humanValues[f.name] || ''}
                                        onChange={(e) => setHumanValues((prev) => ({ ...prev, [f.name]: e.target.value }))}
                                        className="w-full bg-zinc-950 border border-zinc-800 px-2 py-2 text-xs text-zinc-200 outline-none focus:border-zinc-500"
                                    />
                                )}
                            </label>
                        ))}
                        <button
                            onClick={submitHumanInput}
                            className="px-3 py-1.5 text-xs font-bold border border-zinc-700 text-zinc-100 hover:border-zinc-500"
                        >
                            Submit human input
                        </button>
                    </div>
                )}

                <div className="space-y-1 max-h-64 overflow-y-auto border border-zinc-800 bg-black p-3">
                    {runEvents.length === 0 ? (
                        <p className="text-xs text-zinc-600">No run events yet.</p>
                    ) : runEvents.map((line, i) => (
                        <p key={`${line}-${i}`} className="text-xs text-zinc-300 font-mono">{line}</p>
                    ))}
                </div>
            </section>

            <section className="space-y-3 border border-zinc-800 bg-zinc-950 p-4">
                <h2 className="text-sm font-bold text-zinc-100">Results</h2>
                {latestResults.length === 0 ? (
                    <p className="text-xs text-zinc-600">No benchmark results for this target yet.</p>
                ) : (
                    <div className="space-y-1.5">
                        {latestResults.map((r) => (
                            <div key={r.run_id} className="flex flex-wrap items-center gap-2 border border-zinc-800 bg-black px-2 py-1.5 text-[11px] text-zinc-300">
                                <span className="font-mono text-zinc-500">{r.run_id}</span>
                                <span className="inline-flex items-center gap-1"><FlaskConical className="h-3 w-3" /> {r.benchmark_id}</span>
                                <span>score {r.score ?? 'N/A'}</span>
                                {r.target_version_n !== undefined && <span>v{r.target_version_n}</span>}
                            </div>
                        ))}
                    </div>
                )}

                {targetId && (
                    <>
                        <VersionHistory
                            targetId={targetId}
                            targetKind={targetKind}
                            refreshKey={refreshKey}
                            onRolledBack={() => setRefreshKey((k) => k + 1)}
                        />
                        <InboxPanel objectId={targetId} />
                    </>
                )}
            </section>

            {(loadingData || runStatus === 'running') && (
                <div className="fixed bottom-4 right-4 flex items-center gap-2 rounded border border-zinc-800 bg-zinc-950 px-3 py-2 text-xs text-zinc-300">
                    {loadingData ? <Bot className="h-3.5 w-3.5" /> : <Workflow className="h-3.5 w-3.5" />}
                    {loadingData ? 'Loading fine-tuning data...' : 'Fine-tuning run in progress...'}
                </div>
            )}
        </div>
    );
}
