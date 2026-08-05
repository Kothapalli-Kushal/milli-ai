'use client';
// Self-Improvement — BenchmarkEditor (Checkpoint 4, checklist 4.10; extended
// for Checkpoint 6, checklist 6.31: grading-mode toggle, per-input expected-
// answer authoring, split/fold controls, augmentation approve/reject).
import { useCallback, useEffect, useState } from 'react';
import { Check as CheckIcon, FlaskConical, Loader2, Play, Plus, Save, Shuffle, Sparkles, Trash, X } from 'lucide-react';
import type { CompareSpec, ExtractSpec, GradingMode, Split, TargetKind } from './types';

// Metric names the scorer understands: `success` + the detector registry.
const KNOWN_METRICS = [
    'success', 'clean_success', 'recovery', 'loops', 'give_up', 'errors',
    'duration_outlier', 'token_usage', 'sequentialthinking_cap_hit',
    'hallucinated_tool_rate', 'compaction_thrash', 'sticky_arg_conflict',
    'delegate_pingpong', 'mcp_ping_timeout_rate', 'browser_state_stale_rate',
];

// The v1 extractor and comparator sets (§6.3.3 / §6.3.4) — do not add more.
const EXTRACT_SOURCES = ['final_output', 'last_assistant_message', 'tool_call_arg', 'tool_result'];
const COMPARATORS = ['exact', 'contains_all', 'regex', 'numeric', 'json_equal', 'sql_equivalent', 'sql_execution', 'semantic_match', 'any_of'];
const SPLITS: Split[] = ['train', 'holdout', 'regression'];

const FIELD = 'bg-black border border-zinc-800 px-1.5 py-1 text-[9px] text-white focus:border-white focus:outline-none';

interface CheckSpec {
    id: string;
    weight: number;
    critical?: boolean;
    extract: ExtractSpec;
    compare: CompareSpec;
}

interface KeyPoint { id: string; text: string; weight: number }

interface ExpectedSpec {
    reference_sql?: string;
    checks?: CheckSpec[];
    key_points?: KeyPoint[];
    forbidden?: string[];
    reference_output?: string;
}

interface InputDraft {
    id?: string;
    prompt: string;
    expected_metric_hints?: Record<string, number>;
    weight?: number;
    split?: Split;
    fold?: number | null;
    grading_mode?: GradingMode;
    rubric_id?: string | null;
    expected?: ExpectedSpec | { $ref: string } | null;
    parent_input_id?: string | null;
    is_augmented?: boolean;
    approved?: boolean;
}

interface BenchmarkSuite {
    id: string;
    name: string;
    target_object_id: string;
    schema_version?: number;
    grading_mode?: GradingMode;
    grading_strictness?: 'strict' | 'mixed' | null; // derived at save, never authored
    rubric_id?: string | null;
    execution_env?: { connection_id: string; snapshot_id?: string | null; timeout_s?: number; max_rows?: number } | null;
    split_policy?: {
        mode: 'explicit' | 'random' | 'kfold';
        seed: number;
        ratios?: Record<string, number> | null;
        kfold?: { k: number; rotation: 'per_iteration' | 'all_folds' } | null;
    };
    augmentation?: {
        enabled: boolean;
        variants_per_input: number;
        seed: number;
        apply_to_splits: string[];
        model?: string | null;
    } | null;
    inputs: InputDraft[];
    scorer: { metrics: Record<string, number>; process_weight?: number; outcome_weight?: number };
}

interface BenchmarkResult {
    run_id: string;
    benchmark_id: string;
    score: number | null;
    target_version_n: number;
    per_metric: Record<string, { rate: number | 'N/A'; weight: number; numerator: number; denominator: number }>;
    trace_count: number;
    // CP6 — present on schema_version 2 runs
    process_score?: number | null;
    outcome_score?: number | null;
    composite_score?: number | null;
    outcome_na?: boolean;
    grading_strictness?: string | null;
    snapshot_id?: string | null;
    extraction_failed_rate?: number;
}

interface BenchmarkEditorProps {
    targetId: string;
    targetKind: TargetKind;
    onRan?: () => void; // lets VersionHistory refresh its score chips
}

function newSuite(targetId: string): BenchmarkSuite {
    // CP4 shape by default — a suite that never touches CP6 features persists
    // with schema_version 1 and scores byte-identically to pre-CP6.
    return {
        id: `bench_${Date.now()}`,
        name: '',
        target_object_id: targetId,
        inputs: [{ prompt: '' }],
        scorer: { metrics: { success: 1, clean_success: 1 } },
    };
}

const fmtScore = (v?: number | null) => (v === null || v === undefined ? 'N/A' : v.toFixed(3));

function valueToRaw(v: unknown): string {
    if (v === null || v === undefined) return '';
    if (typeof v === 'string') return v;
    if (Array.isArray(v) && v.every(x => typeof x === 'string')) return v.join('\n');
    return JSON.stringify(v);
}

function rawToValue(type: string | undefined, raw: string): unknown {
    if (type === 'numeric') {
        // Only commit to a number once the text is a complete numeric literal,
        // so typing "4." does not snap back to "4".
        const n = Number(raw);
        return raw.trim() !== '' && !Number.isNaN(n) && String(n) === raw.trim() ? n : raw;
    }
    if (type === 'contains_all') return raw.split('\n');
    if (type === 'json_equal' || type === 'any_of') {
        try { return JSON.parse(raw); } catch { return raw; }
    }
    return raw;
}

const looksLikeSqlArg = (ex: ExtractSpec) =>
    ex.from === 'tool_call_arg' && /sql|query/i.test(ex.arg ?? '');

// Empty lines accumulate while typing list values; strip them before the
// payload leaves the editor.
function sanitizeSuite(suite: BenchmarkSuite): BenchmarkSuite {
    const clean = JSON.parse(JSON.stringify(suite)) as BenchmarkSuite;
    for (const inp of clean.inputs) {
        const exp = inp.expected;
        if (!exp || '$ref' in exp) continue;
        if (exp.forbidden) exp.forbidden = exp.forbidden.map(s => s.trim()).filter(Boolean);
        for (const c of exp.checks ?? []) {
            if (Array.isArray(c.compare?.value)) {
                c.compare.value = (c.compare.value as unknown[]).filter(v => typeof v !== 'string' || v.trim() !== '');
            }
        }
    }
    return clean;
}

// ── Per-check editor: extractor + comparator pickers (§6.3.3 / §6.3.4) ───────

function CheckRow({ check, onChange, onRemove }: {
    check: CheckSpec;
    onChange: (next: CheckSpec) => void;
    onRemove: () => void;
}) {
    const ex = check.extract ?? {};
    const cmp = check.compare ?? {};
    const patchExtract = (p: Partial<ExtractSpec>) => onChange({ ...check, extract: { ...ex, ...p } });
    const patchCompare = (p: Partial<CompareSpec>) => onChange({ ...check, compare: { ...cmp, ...p } });
    const needsTool = ex.from === 'tool_call_arg' || ex.from === 'tool_result';
    const isAnyOf = cmp.type === 'any_of';
    const rawValue = isAnyOf ? valueToRaw(cmp.options ?? cmp.value) : valueToRaw(cmp.value);
    return (
        <div className="p-1.5 border border-zinc-800/70 bg-black/40 space-y-1.5">
            <div className="flex items-center gap-1.5">
                <input value={check.id} onChange={e => onChange({ ...check, id: e.target.value })}
                    placeholder="check id" className={`w-24 font-mono ${FIELD}`} />
                <label className="flex items-center gap-1 text-[9px] text-zinc-400">w
                    <input type="number" min={0} step={0.5} value={check.weight}
                        onChange={e => onChange({ ...check, weight: parseFloat(e.target.value) || 0 })}
                        className={`w-12 ${FIELD}`} />
                </label>
                <label className="flex items-center gap-1 text-[9px] text-zinc-400"
                    title="A failing critical check forces the whole input to 0 — no partial credit">
                    <input type="checkbox" checked={!!check.critical}
                        onChange={e => onChange({ ...check, critical: e.target.checked })} />
                    critical
                </label>
                <div className="flex-1" />
                <button onClick={onRemove} className="p-0.5 text-zinc-600 hover:text-red-400"><Trash className="h-3 w-3" /></button>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-[8px] uppercase text-zinc-600 font-bold">extract</span>
                <select value={ex.from ?? 'final_output'} onChange={e => patchExtract({ from: e.target.value })} className={FIELD}>
                    {EXTRACT_SOURCES.map(s => <option key={s} value={s}>{s}</option>)}
                </select>
                {needsTool && (
                    <input value={ex.tool ?? ''} onChange={e => patchExtract({ tool: e.target.value })} placeholder="tool" className={`w-24 font-mono ${FIELD}`} />
                )}
                {ex.from === 'tool_call_arg' && (
                    <>
                        <input value={ex.arg ?? ''} onChange={e => patchExtract({ arg: e.target.value })} placeholder="arg" className={`w-16 font-mono ${FIELD}`} />
                        <select value={ex.occurrence ?? 'last'} onChange={e => patchExtract({ occurrence: e.target.value as ExtractSpec['occurrence'] })} className={FIELD}>
                            <option value="first">first</option>
                            <option value="last">last</option>
                            <option value="any">any</option>
                        </select>
                    </>
                )}
                <input value={ex.regex ?? ''} onChange={e => patchExtract({ regex: e.target.value || undefined })}
                    placeholder="regex (capture group 1)" className={`w-36 font-mono ${FIELD}`} />
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-[8px] uppercase text-zinc-600 font-bold">compare</span>
                <select value={cmp.type ?? 'exact'} onChange={e => patchCompare({ type: e.target.value })} className={FIELD}>
                    {COMPARATORS.map(c => <option key={c} value={c}>{c}</option>)}
                </select>
                {cmp.type === 'numeric' && (
                    <label className="flex items-center gap-1 text-[9px] text-zinc-400">tol
                        <input type="number" step={0.01} value={cmp.tol ?? 0} onChange={e => patchCompare({ tol: parseFloat(e.target.value) || 0 })} className={`w-14 ${FIELD}`} />
                    </label>
                )}
                {(cmp.type === 'exact' || cmp.type === 'contains_all') && (
                    <label className="flex items-center gap-1 text-[9px] text-zinc-400">
                        <input type="checkbox" checked={!!cmp.case_sensitive} onChange={e => patchCompare({ case_sensitive: e.target.checked })} />
                        case
                    </label>
                )}
                {cmp.type === 'sql_equivalent' && (
                    <input value={cmp.dialect ?? ''} onChange={e => patchCompare({ dialect: e.target.value || undefined })} placeholder="dialect" className={`w-20 font-mono ${FIELD}`} />
                )}
                {(cmp.type === 'sql_equivalent' || cmp.type === 'sql_execution') && (
                    <input value={cmp.reference ?? ''} onChange={e => patchCompare({ reference: e.target.value || undefined })}
                        placeholder="$expected.reference_sql" className={`w-44 font-mono ${FIELD}`} />
                )}
                {cmp.type === 'sql_execution' && (
                    <>
                        <select value={String(cmp.order_sensitive ?? 'auto')}
                            onChange={e => patchCompare({ order_sensitive: e.target.value === 'auto' ? 'auto' : e.target.value === 'true' })}
                            className={FIELD}
                            title="auto derives ordering from the top-level ORDER BY of the reference query">
                            <option value="auto">order: auto</option>
                            <option value="true">order: on</option>
                            <option value="false">order: off</option>
                        </select>
                        <select value={cmp.column_match ?? 'by_position'} onChange={e => patchCompare({ column_match: e.target.value as CompareSpec['column_match'] })} className={FIELD}>
                            <option value="by_position">cols: by_position</option>
                            <option value="by_name">cols: by_name</option>
                        </select>
                        <label className="flex items-center gap-1 text-[9px] text-zinc-400">float_tol
                            <input type="number" step={0.001} value={cmp.float_tol ?? 0.01} onChange={e => patchCompare({ float_tol: parseFloat(e.target.value) || 0 })} className={`w-16 ${FIELD}`} />
                        </label>
                    </>
                )}
            </div>
            {cmp.type !== 'sql_execution' && (
                <textarea
                    value={rawValue}
                    onChange={e => {
                        if (isAnyOf) {
                            const parsed = rawToValue('any_of', e.target.value);
                            patchCompare(Array.isArray(parsed)
                                ? { options: parsed as CompareSpec[], value: undefined }
                                : { value: e.target.value, options: undefined });
                        } else {
                            patchCompare({ value: rawToValue(cmp.type, e.target.value) });
                        }
                    }}
                    rows={1}
                    placeholder={isAnyOf ? 'options: JSON array of comparators' : 'expected value (contains_all: one per line)'}
                    className={`w-full resize-y font-mono ${FIELD}`}
                />
            )}
            {cmp.type === 'semantic_match' && looksLikeSqlArg(ex) && (
                <div className="text-[9px] text-amber-400">
                    semantic_match is never permitted for SQL checks — use sql_execution instead (rejected at save time).
                </div>
            )}
        </div>
    );
}

// ── Expected-answer editors, one per grading mode ─────────────────────────────

function DeterministicExpectedEditor({ expected, onChange }: {
    expected: ExpectedSpec;
    onChange: (e: ExpectedSpec) => void;
}) {
    const checks = expected.checks ?? [];
    return (
        <div className="space-y-1.5">
            <input
                value={expected.reference_sql ?? ''}
                onChange={e => onChange({ ...expected, reference_sql: e.target.value || undefined })}
                placeholder="reference SQL (validated by double execution at save time)"
                className={`w-full font-mono ${FIELD}`}
            />
            {checks.map((c, i) => (
                <CheckRow key={i} check={c}
                    onChange={next => onChange({ ...expected, checks: checks.map((x, j) => (j === i ? next : x)) })}
                    onRemove={() => onChange({ ...expected, checks: checks.filter((_, j) => j !== i) })}
                />
            ))}
            <button
                onClick={() => onChange({
                    ...expected,
                    checks: [...checks, { id: `check_${checks.length + 1}`, weight: 1, extract: { from: 'final_output' }, compare: { type: 'contains_all', value: [] } }],
                })}
                className="flex items-center gap-1 px-1.5 py-0.5 text-[9px] font-bold border border-zinc-800 text-zinc-400 hover:border-zinc-500"
            >
                <Plus className="h-2.5 w-2.5" /> Check
            </button>
        </div>
    );
}

function RubricExpectedEditor({ expected, onChange }: {
    expected: ExpectedSpec;
    onChange: (e: ExpectedSpec) => void;
}) {
    const kps = expected.key_points ?? [];
    const patchKp = (i: number, p: Partial<KeyPoint>) =>
        onChange({ ...expected, key_points: kps.map((x, j) => (j === i ? { ...x, ...p } : x)) });
    return (
        <div className="space-y-1.5">
            <div className="text-[8px] uppercase text-zinc-600 font-bold">Key points (judge makes one binary call each)</div>
            {kps.map((kp, i) => (
                <div key={i} className="flex items-center gap-1.5">
                    <input value={kp.id} onChange={e => patchKp(i, { id: e.target.value })} placeholder="id" className={`w-14 font-mono ${FIELD}`} />
                    <input value={kp.text} onChange={e => patchKp(i, { text: e.target.value })} placeholder="What the answer must contain" className={`flex-1 ${FIELD}`} />
                    <input type="number" min={0} step={0.5} value={kp.weight} onChange={e => patchKp(i, { weight: parseFloat(e.target.value) || 0 })} className={`w-12 ${FIELD}`} />
                    <button onClick={() => onChange({ ...expected, key_points: kps.filter((_, j) => j !== i) })} className="p-0.5 text-zinc-600 hover:text-red-400"><Trash className="h-3 w-3" /></button>
                </div>
            ))}
            <button
                onClick={() => onChange({ ...expected, key_points: [...kps, { id: `kp${kps.length + 1}`, text: '', weight: 1 }] })}
                className="flex items-center gap-1 px-1.5 py-0.5 text-[9px] font-bold border border-zinc-800 text-zinc-400 hover:border-zinc-500"
            >
                <Plus className="h-2.5 w-2.5" /> Key Point
            </button>
            <textarea
                value={(expected.forbidden ?? []).join('\n')}
                onChange={e => onChange({ ...expected, forbidden: e.target.value.split('\n') })}
                rows={1} placeholder="Forbidden claims, one per line (a hit forces the criterion to 0)"
                className={`w-full resize-y ${FIELD}`}
            />
            <textarea
                value={expected.reference_output ?? ''}
                onChange={e => onChange({ ...expected, reference_output: e.target.value || undefined })}
                rows={1} placeholder="Optional ideal answer (judge context only — never shown to the tuner)"
                className={`w-full resize-y ${FIELD}`}
            />
        </div>
    );
}

export function BenchmarkEditor({ targetId, onRan }: BenchmarkEditorProps) {
    const [suites, setSuites] = useState<BenchmarkSuite[]>([]);
    const [draft, setDraft] = useState<BenchmarkSuite | null>(null);
    const [rubrics, setRubrics] = useState<{ id: string; name?: string; version?: number }[]>([]);
    const [busy, setBusy] = useState(false);
    const [runningId, setRunningId] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [lastResult, setLastResult] = useState<BenchmarkResult | null>(null);
    const [augmentNote, setAugmentNote] = useState<string | null>(null);

    const load = useCallback(async () => {
        try {
            const res = await fetch('/api/improve/benchmarks');
            if (res.ok) setSuites(await res.json());
        } catch { /* keep current list */ }
    }, []);

    useEffect(() => { setDraft(null); setLastResult(null); load(); }, [load, targetId]);

    useEffect(() => {
        fetch('/api/improve/rubrics')
            .then(r => (r.ok ? r.json() : []))
            .then(list => setRubrics(Array.isArray(list) ? list : []))
            .catch(() => { /* rubric picker degrades to empty */ });
    }, []);

    // Persist without closing the form — augment/resplit act on the saved file.
    const persist = async (suite: BenchmarkSuite): Promise<BenchmarkSuite> => {
        const res = await fetch(`/api/improve/benchmark/${encodeURIComponent(suite.id)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(sanitizeSuite(suite)),
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
        return body;
    };

    const save = async () => {
        if (!draft) return;
        setBusy(true);
        setError(null);
        try {
            await persist(draft);
            setDraft(null);
            await load();
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Save failed');
        } finally {
            setBusy(false);
        }
    };

    const refreshDraft = async (id: string) => {
        const res = await fetch('/api/improve/benchmarks');
        if (!res.ok) return;
        const list: BenchmarkSuite[] = await res.json();
        setSuites(list);
        const fresh = list.find(s => s.id === id);
        if (fresh) setDraft(JSON.parse(JSON.stringify(fresh)));
    };

    const generateVariants = async () => {
        if (!draft) return;
        setBusy(true);
        setError(null);
        setAugmentNote(null);
        try {
            const saved = await persist(draft);
            const res = await fetch(`/api/improve/benchmark/${encodeURIComponent(saved.id)}/augment`, { method: 'POST' });
            const body = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
            const generated = (body.variants ?? []).length;
            const rejected = (body.rejected ?? []).length;
            setAugmentNote(`${generated} variant${generated === 1 ? '' : 's'} generated (approval required)` +
                (rejected ? `, ${rejected} rejected by the constraint guard` : ''));
            await refreshDraft(saved.id);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Augmentation failed');
        } finally {
            setBusy(false);
        }
    };

    const decideVariant = async (variantId: string, approve: boolean) => {
        if (!draft) return;
        setBusy(true);
        setError(null);
        try {
            // Persist first so prompt edits made during review are what get approved.
            await persist(draft);
            const res = await fetch(`/api/improve/benchmark/${encodeURIComponent(draft.id)}/augment/approve`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ decisions: { [variantId]: approve } }),
            });
            const body = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
            await refreshDraft(draft.id);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Approval failed');
        } finally {
            setBusy(false);
        }
    };

    const resplit = async () => {
        if (!draft) return;
        if (!window.confirm('Re-splitting invalidates score comparability with every previous run of this benchmark. Continue?')) return;
        setBusy(true);
        setError(null);
        try {
            const saved = await persist(draft);
            const res = await fetch(`/api/improve/benchmark/${encodeURIComponent(saved.id)}/resplit`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ confirm: true }),
            });
            const body = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
            await refreshDraft(saved.id);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Re-split failed');
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
        setDraft({ ...draft, scorer: { ...draft.scorer, metrics: { ...draft.scorer.metrics, [name]: weight } } });
    };

    const patchInput = (index: number, patch: Partial<InputDraft>) => {
        if (!draft) return;
        const inputs = [...draft.inputs];
        inputs[index] = { ...inputs[index], ...patch };
        setDraft({ ...draft, inputs });
    };

    // Selecting a mode is the CP6 opt-in: it moves the suite to schema_version 2.
    // A suite left at "off" that never used CP6 features stays schema_version 1.
    const setGradingMode = (mode: GradingMode) => {
        if (!draft) return;
        setDraft({
            ...draft,
            grading_mode: mode,
            schema_version: mode === null ? draft.schema_version : 2,
            scorer: {
                ...draft.scorer,
                process_weight: draft.scorer.process_weight ?? 1,
                outcome_weight: mode === null ? (draft.scorer.outcome_weight ?? 0) : (draft.scorer.outcome_weight || 1),
            },
        });
    };

    const isV2 = (draft?.schema_version ?? 1) >= 2;
    const variants = draft?.inputs.filter(i => i.is_augmented) ?? [];
    const mainInputs = draft?.inputs.map((inp, i) => ({ inp, i })).filter(({ inp }) => !inp.is_augmented) ?? [];

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
                        Score: <span className="font-mono">{lastResult.composite_score ?? lastResult.score ?? 'N/A'}</span>
                        <span className="text-zinc-500 font-normal"> · v{lastResult.target_version_n} · {lastResult.trace_count} trace{lastResult.trace_count === 1 ? '' : 's'}</span>
                    </div>
                    {lastResult.outcome_score !== undefined && (
                        <div className="flex flex-wrap gap-1 text-[9px] font-mono">
                            <span className="px-1.5 py-0.5 border border-zinc-800 text-zinc-400">process {fmtScore(lastResult.process_score)}</span>
                            <span className="px-1.5 py-0.5 border border-zinc-800 text-zinc-400">
                                outcome {lastResult.outcome_na ? 'N/A' : fmtScore(lastResult.outcome_score)}
                            </span>
                            {lastResult.grading_strictness && (
                                <span className={`px-1.5 py-0.5 border border-zinc-800 ${lastResult.grading_strictness === 'mixed' ? 'text-amber-400' : 'text-zinc-400'}`}>
                                    {lastResult.grading_strictness}
                                </span>
                            )}
                            {(lastResult.snapshot_id === 'unpinned' || lastResult.snapshot_id === null) && lastResult.grading_strictness && (
                                <span className="px-1.5 py-0.5 border border-amber-900 text-amber-400" title="No pinned DB snapshot — outcome scores are not exactly reproducible">
                                    unpinned
                                </span>
                            )}
                            {(lastResult.extraction_failed_rate ?? 0) > 0 && (
                                <span className="px-1.5 py-0.5 border border-red-900 text-red-400" title="High rates usually mean misconfigured extractors, not a bad agent">
                                    extraction failed {(lastResult.extraction_failed_rate! * 100).toFixed(0)}%
                                </span>
                            )}
                        </div>
                    )}
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

                    {/* Grading-mode toggle (§6.2) */}
                    <div className="space-y-1.5">
                        <div className="text-[9px] font-bold text-zinc-500 uppercase">
                            Outcome grading
                            {draft.grading_strictness && (
                                <span className={`ml-2 normal-case font-mono ${draft.grading_strictness === 'mixed' ? 'text-amber-400' : 'text-zinc-400'}`}>
                                    {draft.grading_strictness}
                                </span>
                            )}
                        </div>
                        <div className="flex gap-1">
                            {([
                                [null, 'Off (process only)'],
                                ['deterministic', 'Deterministic'],
                                ['rubric', 'Rubric'],
                            ] as [GradingMode, string][]).map(([mode, label]) => (
                                <button
                                    key={String(mode)}
                                    onClick={() => setGradingMode(mode)}
                                    className={`px-2 py-1 text-[9px] font-bold border ${(draft.grading_mode ?? null) === mode
                                        ? 'border-white text-white'
                                        : 'border-zinc-800 text-zinc-500 hover:border-zinc-500'}`}
                                >
                                    {label}
                                </button>
                            ))}
                        </div>
                        {draft.grading_mode === 'rubric' && (
                            <select
                                value={draft.rubric_id ?? ''}
                                onChange={e => setDraft({ ...draft, rubric_id: e.target.value || null })}
                                className={`w-full ${FIELD}`}
                            >
                                <option value="">— select rubric —</option>
                                {rubrics.map(r => <option key={r.id} value={r.id}>{r.name || r.id} (v{r.version ?? '?'})</option>)}
                            </select>
                        )}
                        {draft.grading_mode === 'deterministic' && (
                            <div className="flex flex-wrap items-center gap-1.5">
                                <span className="text-[8px] uppercase text-zinc-600 font-bold">execution env</span>
                                <input
                                    value={draft.execution_env?.connection_id ?? ''}
                                    onChange={e => setDraft({
                                        ...draft,
                                        execution_env: e.target.value
                                            ? { timeout_s: 10, max_rows: 5000, ...draft.execution_env, connection_id: e.target.value }
                                            : null,
                                    })}
                                    placeholder="SQL connection id (existing)"
                                    className={`w-40 font-mono ${FIELD}`}
                                />
                                <input
                                    value={draft.execution_env?.snapshot_id ?? ''}
                                    onChange={e => draft.execution_env && setDraft({
                                        ...draft,
                                        execution_env: { ...draft.execution_env, snapshot_id: e.target.value || null },
                                    })}
                                    placeholder="snapshot id"
                                    disabled={!draft.execution_env}
                                    className={`w-32 font-mono ${FIELD} disabled:opacity-40`}
                                    title="Unpinned snapshots downgrade outcome scores from exact reproducibility"
                                />
                                {draft.execution_env && !draft.execution_env.snapshot_id && (
                                    <span className="text-[9px] text-amber-400">unpinned — scores will not be exactly reproducible</span>
                                )}
                            </div>
                        )}
                        {isV2 && (
                            <div className="flex items-center gap-3">
                                <label className="flex items-center gap-1 text-[9px] font-mono text-zinc-400">process_weight
                                    <input type="number" min={0} step={0.5} value={draft.scorer.process_weight ?? 1}
                                        onChange={e => setDraft({ ...draft, scorer: { ...draft.scorer, process_weight: parseFloat(e.target.value) || 0 } })}
                                        className={`w-14 ${FIELD}`} />
                                </label>
                                <label className="flex items-center gap-1 text-[9px] font-mono text-zinc-400">outcome_weight
                                    <input type="number" min={0} step={0.5} value={draft.scorer.outcome_weight ?? 0}
                                        onChange={e => setDraft({ ...draft, scorer: { ...draft.scorer, outcome_weight: parseFloat(e.target.value) || 0 } })}
                                        className={`w-14 ${FIELD}`} />
                                </label>
                                <span className="text-[9px] text-zinc-600">outcome_weight 0 reproduces CP4 exactly</span>
                            </div>
                        )}
                    </div>

                    {/* Split policy (§6.5) */}
                    {isV2 && (
                        <div className="space-y-1.5">
                            <div className="text-[9px] font-bold text-zinc-500 uppercase">Split policy</div>
                            <div className="flex flex-wrap items-center gap-1.5">
                                <select
                                    value={draft.split_policy?.mode ?? 'explicit'}
                                    onChange={e => {
                                        const mode = e.target.value as 'explicit' | 'random' | 'kfold';
                                        setDraft({
                                            ...draft,
                                            split_policy: {
                                                seed: 1337,
                                                ...draft.split_policy,
                                                mode,
                                                ratios: mode === 'random' ? (draft.split_policy?.ratios ?? { train: 0.6, holdout: 0.3, regression: 0.1 }) : draft.split_policy?.ratios,
                                                kfold: mode === 'kfold' ? (draft.split_policy?.kfold ?? { k: 5, rotation: 'per_iteration' }) : draft.split_policy?.kfold,
                                            },
                                        });
                                    }}
                                    className={FIELD}
                                >
                                    <option value="explicit">explicit (per-input)</option>
                                    <option value="random">random (seeded)</option>
                                    <option value="kfold">k-fold (seeded)</option>
                                </select>
                                <label className="flex items-center gap-1 text-[9px] font-mono text-zinc-400">seed
                                    <input type="number" value={draft.split_policy?.seed ?? 1337}
                                        onChange={e => setDraft({ ...draft, split_policy: { mode: 'explicit', ...draft.split_policy, seed: parseInt(e.target.value) || 0 } })}
                                        className={`w-20 ${FIELD}`} />
                                </label>
                                {draft.split_policy?.mode === 'kfold' && (
                                    <>
                                        <label className="flex items-center gap-1 text-[9px] font-mono text-zinc-400">k
                                            <input type="number" min={2} value={draft.split_policy.kfold?.k ?? 5}
                                                onChange={e => setDraft({
                                                    ...draft,
                                                    split_policy: {
                                                        ...draft.split_policy!,
                                                        kfold: { rotation: 'per_iteration', ...draft.split_policy!.kfold, k: parseInt(e.target.value) || 2 },
                                                    },
                                                })}
                                                className={`w-12 ${FIELD}`} />
                                        </label>
                                        <select
                                            value={draft.split_policy.kfold?.rotation ?? 'per_iteration'}
                                            onChange={e => setDraft({
                                                ...draft,
                                                split_policy: {
                                                    ...draft.split_policy!,
                                                    kfold: { k: 5, ...draft.split_policy!.kfold, rotation: e.target.value as 'per_iteration' | 'all_folds' },
                                                },
                                            })}
                                            className={FIELD}
                                            title="all_folds evaluates every fold each iteration — k times the cost"
                                        >
                                            <option value="per_iteration">rotate per iteration</option>
                                            <option value="all_folds">all folds (k× cost)</option>
                                        </select>
                                    </>
                                )}
                                {(draft.split_policy?.mode ?? 'explicit') !== 'explicit' && (
                                    <button
                                        onClick={resplit}
                                        disabled={busy}
                                        className="flex items-center gap-1 px-2 py-1 text-[9px] font-bold border border-amber-900 text-amber-400 hover:border-amber-500 disabled:opacity-50"
                                        title="Materializes split/fold assignments into the file. Invalidates comparability with previous runs."
                                    >
                                        <Shuffle className="h-3 w-3" /> Materialize Splits
                                    </button>
                                )}
                            </div>
                            <div className="text-[9px] text-zinc-600">
                                The tuner sees train failures only; the ratchet decides on holdout; regression must never degrade.
                            </div>
                        </div>
                    )}

                    <div className="space-y-1.5">
                        <div className="text-[9px] font-bold text-zinc-500 uppercase">Inputs</div>
                        {mainInputs.map(({ inp, i }) => {
                            const effMode = inp.grading_mode ?? draft.grading_mode ?? null;
                            const expected = (inp.expected && !('$ref' in inp.expected) ? inp.expected : {}) as ExpectedSpec;
                            return (
                                <div key={i} className="p-1.5 border border-zinc-800 space-y-1.5">
                                    <div className="flex items-center gap-1.5">
                                        {inp.id && <span className="text-[9px] font-mono text-zinc-600">{inp.id}</span>}
                                        <div className="flex-1" />
                                        {isV2 && (
                                            <>
                                                <label className="flex items-center gap-1 text-[9px] text-zinc-400">w
                                                    <input type="number" min={0} step={0.5} value={inp.weight ?? 1}
                                                        onChange={e => patchInput(i, { weight: parseFloat(e.target.value) || 0 })}
                                                        className={`w-12 ${FIELD}`} />
                                                </label>
                                                <select value={inp.split ?? 'train'} onChange={e => patchInput(i, { split: e.target.value as Split })}
                                                    disabled={(draft.split_policy?.mode ?? 'explicit') !== 'explicit' && inp.split !== 'regression'}
                                                    className={`${FIELD} disabled:opacity-40`}
                                                    title="Non-explicit policies assign splits at materialize time (regression declarations are never reassigned)">
                                                    {SPLITS.map(s => <option key={s} value={s}>{s}</option>)}
                                                </select>
                                                {draft.split_policy?.mode === 'kfold' && (
                                                    <span className="text-[9px] font-mono text-zinc-500" title="Materialized by the split policy">
                                                        fold {inp.fold ?? '—'}
                                                    </span>
                                                )}
                                                <select
                                                    value={inp.grading_mode ?? ''}
                                                    onChange={e => patchInput(i, { grading_mode: (e.target.value || null) as GradingMode })}
                                                    className={FIELD}
                                                    title="Per-input grading-mode override"
                                                >
                                                    <option value="">mode: inherit</option>
                                                    <option value="deterministic">deterministic</option>
                                                    <option value="rubric">rubric</option>
                                                </select>
                                            </>
                                        )}
                                        <button
                                            onClick={() => setDraft({ ...draft, inputs: draft.inputs.filter((_, j) => j !== i) })}
                                            disabled={draft.inputs.length <= 1}
                                            className="p-1 text-zinc-600 hover:text-red-400 disabled:opacity-30"
                                        >
                                            <Trash className="h-3 w-3" />
                                        </button>
                                    </div>
                                    <textarea
                                        value={inp.prompt}
                                        onChange={e => patchInput(i, { prompt: e.target.value })}
                                        rows={2}
                                        placeholder={`Prompt #${i + 1}`}
                                        className="w-full bg-black border border-zinc-800 px-2 py-1.5 text-[10px] text-white focus:border-white focus:outline-none resize-y"
                                    />
                                    {effMode === 'deterministic' && (
                                        <DeterministicExpectedEditor expected={expected} onChange={e => patchInput(i, { expected: e })} />
                                    )}
                                    {effMode === 'rubric' && (
                                        <>
                                            {rubrics.length > 0 && (
                                                <select
                                                    value={inp.rubric_id ?? ''}
                                                    onChange={e => patchInput(i, { rubric_id: e.target.value || null })}
                                                    className={FIELD}
                                                    title="Per-input rubric override"
                                                >
                                                    <option value="">rubric: inherit</option>
                                                    {rubrics.map(r => <option key={r.id} value={r.id}>{r.name || r.id}</option>)}
                                                </select>
                                            )}
                                            <RubricExpectedEditor expected={expected} onChange={e => patchInput(i, { expected: e })} />
                                        </>
                                    )}
                                </div>
                            );
                        })}
                        <button
                            onClick={() => setDraft({ ...draft, inputs: [...draft.inputs, { prompt: '' }] })}
                            className="flex items-center gap-1 px-2 py-1 text-[9px] font-bold border border-zinc-800 text-zinc-400 hover:border-zinc-500"
                        >
                            <Plus className="h-3 w-3" /> Add Input
                        </button>
                    </div>

                    {/* Augmentation (§6.5.3) — generate once, freeze, human approval */}
                    {isV2 && (
                        <div className="space-y-1.5">
                            <div className="text-[9px] font-bold text-zinc-500 uppercase">Augmentation</div>
                            <div className="flex flex-wrap items-center gap-1.5">
                                <label className="flex items-center gap-1 text-[9px] text-zinc-400">
                                    <input type="checkbox" checked={draft.augmentation?.enabled ?? false}
                                        onChange={e => setDraft({
                                            ...draft,
                                            augmentation: {
                                                variants_per_input: 2, seed: 1337, apply_to_splits: ['train', 'holdout'],
                                                ...draft.augmentation, enabled: e.target.checked,
                                            },
                                        })} />
                                    enabled
                                </label>
                                <label className="flex items-center gap-1 text-[9px] font-mono text-zinc-400">variants/input
                                    <input type="number" min={1} value={draft.augmentation?.variants_per_input ?? 2}
                                        disabled={!draft.augmentation?.enabled}
                                        onChange={e => draft.augmentation && setDraft({
                                            ...draft,
                                            augmentation: { ...draft.augmentation, variants_per_input: parseInt(e.target.value) || 1 },
                                        })}
                                        className={`w-12 ${FIELD} disabled:opacity-40`} />
                                </label>
                                <label className="flex items-center gap-1 text-[9px] font-mono text-zinc-400">seed
                                    <input type="number" value={draft.augmentation?.seed ?? 1337}
                                        disabled={!draft.augmentation?.enabled}
                                        onChange={e => draft.augmentation && setDraft({
                                            ...draft,
                                            augmentation: { ...draft.augmentation, seed: parseInt(e.target.value) || 0 },
                                        })}
                                        className={`w-20 ${FIELD} disabled:opacity-40`} />
                                </label>
                                <button
                                    onClick={generateVariants}
                                    disabled={busy || !draft.augmentation?.enabled}
                                    className="flex items-center gap-1 px-2 py-1 text-[9px] font-bold border border-zinc-800 text-zinc-400 hover:border-zinc-500 disabled:opacity-50"
                                    title="Explicit authoring action: saves the suite, generates paraphrase variants, freezes them for review"
                                >
                                    {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Sparkles className="h-3 w-3" />}
                                    Generate Variants
                                </button>
                            </div>
                            {augmentNote && <div className="text-[9px] text-zinc-400">{augmentNote}</div>}
                            {variants.map(v => {
                                const idx = draft.inputs.indexOf(v);
                                const parent = draft.inputs.find(p => p.id === v.parent_input_id);
                                return (
                                    <div key={v.id} className={`p-1.5 border space-y-1 ${v.approved ? 'border-zinc-800' : 'border-amber-900'}`}>
                                        <div className="flex items-center gap-1.5 text-[9px] font-mono text-zinc-500">
                                            <span>{v.id}</span>
                                            <span>· {v.split}{v.fold !== null && v.fold !== undefined ? ` / fold ${v.fold}` : ''} · w{v.weight ?? 0.5}</span>
                                            <span className={v.approved ? 'text-emerald-500' : 'text-amber-400'}>
                                                {v.approved ? 'approved' : 'pending approval — excluded from scoring'}
                                            </span>
                                            <div className="flex-1" />
                                            {!v.approved && (
                                                <>
                                                    <button onClick={() => decideVariant(v.id!, true)} disabled={busy}
                                                        className="flex items-center gap-0.5 px-1.5 py-0.5 text-[9px] font-bold border border-emerald-900 text-emerald-500 hover:border-emerald-500 disabled:opacity-50">
                                                        <CheckIcon className="h-2.5 w-2.5" /> Approve
                                                    </button>
                                                    <button onClick={() => decideVariant(v.id!, false)} disabled={busy}
                                                        className="flex items-center gap-0.5 px-1.5 py-0.5 text-[9px] font-bold border border-red-900 text-red-400 hover:border-red-500 disabled:opacity-50">
                                                        <X className="h-2.5 w-2.5" /> Reject
                                                    </button>
                                                </>
                                            )}
                                        </div>
                                        {/* Diff against the parent prompt — the reviewer must see what changed */}
                                        {parent && (
                                            <div className="text-[9px] text-zinc-600">
                                                <span className="font-bold uppercase text-[8px]">parent</span> {parent.prompt}
                                            </div>
                                        )}
                                        <textarea
                                            value={v.prompt}
                                            onChange={e => patchInput(idx, { prompt: e.target.value })}
                                            rows={2}
                                            className="w-full bg-black border border-zinc-800 px-2 py-1 text-[10px] text-white focus:border-white focus:outline-none resize-y"
                                        />
                                    </div>
                                );
                            })}
                        </div>
                    )}

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
