'use client';
// Self-Improvement — RubricEditor (Checkpoint 6, checklist 6.30).
//
// Rubrics are standalone, reusable, IMMUTABLE-PER-VERSION objects. Saving an
// edit writes a NEW version with a recomputed content_hash; prior versions
// stay readable. The version history below shows the hash for exactly this
// reason: two benchmark scores measured under different content hashes are not
// comparable, and IMPROVE_RATCHET_DECIDE refuses to compare them.
//
// Lives inside the EXISTING Self-Improve sub-tab — no new top-level nav.
import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, ClipboardCheck, Loader2, Plus, Save, Trash } from 'lucide-react';
import type { Rubric, RubricCriterion, RubricCriterionKind, RubricVersionRef } from './types';

const KINDS: { value: RubricCriterionKind; label: string; hint: string }[] = [
    {
        value: 'key_point_coverage',
        label: 'Key point coverage',
        hint: 'Atomic yes/no judge calls, one per key point on the input. The most stable kind across runs — prefer it.',
    },
    {
        value: 'anchored',
        label: 'Anchored scale',
        hint: 'Integer 0..scale with a written anchor for every level. Reserve for genuinely qualitative dimensions.',
    },
    {
        value: 'deterministic',
        label: 'Deterministic check',
        hint: 'Embeds an extractor + comparator. No judge, no cost, exactly reproducible.',
    },
];

function newRubric(): Rubric {
    return {
        id: `rubric_${Date.now()}`,
        name: '',
        version: 1,
        content_hash: '',
        criteria: [newCriterion('key_point_coverage', 0)],
    };
}

function newCriterion(kind: RubricCriterionKind, index: number): RubricCriterion {
    const base: RubricCriterion = {
        id: `criterion_${index + 1}`,
        kind,
        weight: 1,
        critical: false,
        critical_floor: 1,
    };
    if (kind === 'anchored') {
        return { ...base, question: '', scale: 2, anchors: { '0': '', '1': '', '2': '' } };
    }
    if (kind === 'deterministic') {
        return {
            ...base,
            check: {
                extract: { from: 'final_output' },
                compare: { type: 'contains_all', value: [] },
            },
        };
    }
    return base;
}

function resizeAnchors(criterion: RubricCriterion, scale: number): Record<string, string> {
    const next: Record<string, string> = {};
    for (let level = 0; level <= scale; level += 1) {
        next[String(level)] = criterion.anchors?.[String(level)] ?? '';
    }
    return next;
}

export function RubricEditor() {
    const [rubrics, setRubrics] = useState<Rubric[]>([]);
    const [draft, setDraft] = useState<Rubric | null>(null);
    const [versions, setVersions] = useState<RubricVersionRef[]>([]);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [warning, setWarning] = useState<string | null>(null);

    const load = useCallback(async () => {
        try {
            const res = await fetch('/api/improve/rubrics');
            if (res.ok) setRubrics(await res.json());
        } catch { /* keep current list */ }
    }, []);

    useEffect(() => { load(); }, [load]);

    const openForEdit = async (rubric: Rubric) => {
        setDraft(JSON.parse(JSON.stringify(rubric)));
        setVersions([]);
        try {
            const res = await fetch(`/api/improve/rubric/${encodeURIComponent(rubric.id)}`);
            if (res.ok) {
                const body = await res.json();
                setVersions(body.versions ?? []);
            }
        } catch { /* history is informational */ }
    };

    const save = async () => {
        if (!draft) return;
        setBusy(true);
        setError(null);
        try {
            const res = await fetch(`/api/improve/rubric/${encodeURIComponent(draft.id)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(draft),
            });
            const body = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
            setWarning(body.warning ?? null);
            setDraft(null);
            setVersions([]);
            await load();
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Save failed');
        } finally {
            setBusy(false);
        }
    };

    const remove = async (id: string) => {
        setBusy(true);
        setError(null);
        try {
            const res = await fetch(`/api/improve/rubric/${encodeURIComponent(id)}`, { method: 'DELETE' });
            if (!res.ok) {
                const body = await res.json().catch(() => ({}));
                throw new Error(body.detail || `HTTP ${res.status}`);
            }
            await load();
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Delete failed');
        } finally {
            setBusy(false);
        }
    };

    const patchCriterion = (index: number, patch: Partial<RubricCriterion>) => {
        if (!draft) return;
        const criteria = [...draft.criteria];
        criteria[index] = { ...criteria[index], ...patch };
        setDraft({ ...draft, criteria });
    };

    const changeKind = (index: number, kind: RubricCriterionKind) => {
        if (!draft) return;
        const criteria = [...draft.criteria];
        criteria[index] = { ...newCriterion(kind, index), id: criteria[index].id, weight: criteria[index].weight };
        setDraft({ ...draft, criteria });
    };

    return (
        <div className="space-y-2">
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5 text-[10px] font-bold text-zinc-300 uppercase">
                    <ClipboardCheck className="h-3 w-3" /> Rubrics
                </div>
                {!draft && (
                    <button
                        onClick={() => { setDraft(newRubric()); setVersions([]); }}
                        className="flex items-center gap-1 px-2 py-1 text-[9px] font-bold border border-zinc-800 text-zinc-400 hover:border-zinc-500"
                    >
                        <Plus className="h-3 w-3" /> New Rubric
                    </button>
                )}
            </div>

            {error && <p className="text-[10px] text-red-400">{error}</p>}
            {warning && (
                <p className="flex items-start gap-1.5 p-2 text-[10px] text-amber-400 border border-amber-900/60 bg-amber-950/20">
                    <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0" />
                    <span>{warning}</span>
                </p>
            )}

            {/* Rubric list */}
            {!draft && (
                <div className="space-y-1">
                    {rubrics.map(r => (
                        <div key={r.id} className="flex items-center gap-2 p-2 border border-zinc-800 bg-zinc-950">
                            <div className="flex-1 min-w-0">
                                <div className="text-[10px] text-white font-bold truncate">{r.name || r.id}</div>
                                <div className="text-[9px] text-zinc-500">
                                    v{r.version} · {r.criteria.length} criteri{r.criteria.length === 1 ? 'on' : 'a'} ·{' '}
                                    <span className="font-mono" title="Scores measured under different content hashes are not comparable">
                                        {r.content_hash.replace('sha256:', '').slice(0, 12)}
                                    </span>
                                </div>
                            </div>
                            <button
                                onClick={() => openForEdit(r)}
                                disabled={busy}
                                className="px-2 py-1 text-[9px] font-bold border border-zinc-800 text-zinc-400 hover:border-zinc-500"
                            >
                                Edit
                            </button>
                            <button onClick={() => remove(r.id)} disabled={busy} className="p-1 text-zinc-600 hover:text-red-400">
                                <Trash className="h-3 w-3" />
                            </button>
                        </div>
                    ))}
                    {rubrics.length === 0 && (
                        <p className="text-[10px] text-zinc-600">
                            No rubrics yet — create one to grade open-ended work (research, synthesis, writing) that has no single right answer.
                        </p>
                    )}
                </div>
            )}

            {/* Authoring form */}
            {draft && (
                <div className="p-2 border border-zinc-800 bg-zinc-950 space-y-3">
                    <div className="grid grid-cols-2 gap-2">
                        <input
                            value={draft.name}
                            onChange={e => setDraft({ ...draft, name: e.target.value })}
                            placeholder="Rubric name"
                            className="bg-black border border-zinc-800 px-2 py-1.5 text-[10px] text-white focus:border-white focus:outline-none"
                        />
                        <input
                            value={draft.id}
                            onChange={e => setDraft({ ...draft, id: e.target.value })}
                            placeholder="Rubric id"
                            className="bg-black border border-zinc-800 px-2 py-1.5 text-[10px] font-mono text-white focus:border-white focus:outline-none"
                        />
                    </div>

                    <p className="text-[9px] text-zinc-500">
                        A rubric is generic (&ldquo;does it avoid fabrication&rdquo;); the expectation is per-input
                        (&ldquo;APAC led at $4.2M&rdquo;). Saving writes a new immutable version — prior versions stay readable.
                    </p>

                    {/* Criteria */}
                    <div className="space-y-2">
                        <div className="text-[9px] font-bold text-zinc-500 uppercase">Criteria</div>
                        {draft.criteria.map((criterion, i) => (
                            <div key={i} className="p-2 border border-zinc-800 bg-black space-y-2">
                                <div className="flex gap-1.5">
                                    <input
                                        value={criterion.id}
                                        onChange={e => patchCriterion(i, { id: e.target.value })}
                                        placeholder="criterion id"
                                        className="flex-1 bg-black border border-zinc-800 px-2 py-1 text-[10px] font-mono text-white focus:border-white focus:outline-none"
                                    />
                                    <select
                                        value={criterion.kind}
                                        onChange={e => changeKind(i, e.target.value as RubricCriterionKind)}
                                        className="bg-black border border-zinc-800 px-2 py-1 text-[10px] text-white focus:border-white focus:outline-none"
                                    >
                                        {KINDS.map(k => <option key={k.value} value={k.value}>{k.label}</option>)}
                                    </select>
                                    <label className="flex items-center gap-1 text-[9px] font-mono text-zinc-400">
                                        w
                                        <input
                                            type="number"
                                            min={0}
                                            step={0.5}
                                            value={criterion.weight}
                                            onChange={e => patchCriterion(i, { weight: parseFloat(e.target.value) || 0 })}
                                            className="w-12 bg-black border border-zinc-800 px-1 py-0.5 text-[9px] text-white focus:border-white focus:outline-none"
                                        />
                                    </label>
                                    <button
                                        onClick={() => setDraft({ ...draft, criteria: draft.criteria.filter((_, j) => j !== i) })}
                                        disabled={draft.criteria.length <= 1}
                                        className="p-1 text-zinc-600 hover:text-red-400 disabled:opacity-30"
                                    >
                                        <Trash className="h-3 w-3" />
                                    </button>
                                </div>

                                <p className="text-[9px] text-zinc-600">{KINDS.find(k => k.value === criterion.kind)?.hint}</p>

                                {/* Anchored authoring — every level needs a written anchor */}
                                {criterion.kind === 'anchored' && (
                                    <div className="space-y-1.5">
                                        <div className="flex gap-1.5">
                                            <input
                                                value={criterion.question ?? ''}
                                                onChange={e => patchCriterion(i, { question: e.target.value })}
                                                placeholder="Question the judge answers"
                                                className="flex-1 bg-black border border-zinc-800 px-2 py-1 text-[10px] text-white focus:border-white focus:outline-none"
                                            />
                                            <label className="flex items-center gap-1 text-[9px] font-mono text-zinc-400">
                                                scale
                                                <input
                                                    type="number"
                                                    min={1}
                                                    max={10}
                                                    value={criterion.scale ?? 2}
                                                    onChange={e => {
                                                        const scale = Math.max(1, parseInt(e.target.value, 10) || 1);
                                                        patchCriterion(i, { scale, anchors: resizeAnchors(criterion, scale) });
                                                    }}
                                                    className="w-12 bg-black border border-zinc-800 px-1 py-0.5 text-[9px] text-white focus:border-white focus:outline-none"
                                                />
                                            </label>
                                        </div>
                                        {Object.keys(criterion.anchors ?? {}).sort().map(level => (
                                            <div key={level} className="flex items-center gap-1.5">
                                                <span className="w-4 text-[9px] font-mono text-zinc-500">{level}</span>
                                                <input
                                                    value={criterion.anchors?.[level] ?? ''}
                                                    onChange={e => patchCriterion(i, {
                                                        anchors: { ...(criterion.anchors ?? {}), [level]: e.target.value },
                                                    })}
                                                    placeholder={`What level ${level} looks like`}
                                                    className="flex-1 bg-black border border-zinc-800 px-2 py-1 text-[10px] text-white focus:border-white focus:outline-none"
                                                />
                                            </div>
                                        ))}
                                        {Object.values(criterion.anchors ?? {}).some(a => !a.trim()) && (
                                            <p className="text-[9px] text-amber-500">
                                                Every level needs a written anchor — an unanchored level is rejected at save time.
                                            </p>
                                        )}
                                    </div>
                                )}

                                {/* Deterministic criterion — extractor + comparator */}
                                {criterion.kind === 'deterministic' && (
                                    <div className="grid grid-cols-2 gap-1.5">
                                        <select
                                            value={criterion.check?.extract?.from ?? 'final_output'}
                                            onChange={e => patchCriterion(i, {
                                                check: {
                                                    ...(criterion.check ?? {}),
                                                    extract: { ...(criterion.check?.extract ?? {}), from: e.target.value },
                                                    compare: criterion.check?.compare ?? { type: 'contains_all', value: [] },
                                                },
                                            })}
                                            className="bg-black border border-zinc-800 px-2 py-1 text-[10px] text-white focus:border-white focus:outline-none"
                                        >
                                            <option value="final_output">final_output</option>
                                            <option value="last_assistant_message">last_assistant_message</option>
                                            <option value="tool_call_arg">tool_call_arg</option>
                                            <option value="tool_result">tool_result</option>
                                        </select>
                                        <select
                                            value={criterion.check?.compare?.type ?? 'contains_all'}
                                            onChange={e => patchCriterion(i, {
                                                check: {
                                                    ...(criterion.check ?? {}),
                                                    extract: criterion.check?.extract ?? { from: 'final_output' },
                                                    compare: { ...(criterion.check?.compare ?? {}), type: e.target.value },
                                                },
                                            })}
                                            className="bg-black border border-zinc-800 px-2 py-1 text-[10px] text-white focus:border-white focus:outline-none"
                                        >
                                            {['exact', 'contains_all', 'regex', 'numeric', 'json_equal'].map(t => (
                                                <option key={t} value={t}>{t}</option>
                                            ))}
                                        </select>
                                    </div>
                                )}

                                {/* Critical veto */}
                                <label className="flex items-center gap-1.5 text-[9px] text-zinc-400">
                                    <input
                                        type="checkbox"
                                        checked={criterion.critical ?? false}
                                        onChange={e => patchCriterion(i, { critical: e.target.checked })}
                                        className="accent-white"
                                    />
                                    Critical — score the whole input 0 when this falls below
                                    <input
                                        type="number"
                                        min={0}
                                        max={1}
                                        step={0.1}
                                        value={criterion.critical_floor ?? 1}
                                        onChange={e => patchCriterion(i, { critical_floor: parseFloat(e.target.value) || 0 })}
                                        disabled={!criterion.critical}
                                        className="w-12 bg-black border border-zinc-800 px-1 py-0.5 text-[9px] text-white focus:border-white focus:outline-none disabled:opacity-40"
                                    />
                                </label>
                            </div>
                        ))}
                        <button
                            onClick={() => setDraft({ ...draft, criteria: [...draft.criteria, newCriterion('key_point_coverage', draft.criteria.length)] })}
                            className="flex items-center gap-1 px-2 py-1 text-[9px] font-bold border border-zinc-800 text-zinc-400 hover:border-zinc-500"
                        >
                            <Plus className="h-3 w-3" /> Add Criterion
                        </button>
                    </div>

                    {/* Version history — the content hash is the comparability key */}
                    {versions.length > 0 && (
                        <div className="space-y-1">
                            <div className="text-[9px] font-bold text-zinc-500 uppercase">Version history</div>
                            {versions.slice().reverse().map(v => (
                                <div key={v.version} className="flex items-center gap-2 text-[9px] font-mono text-zinc-500">
                                    <span className="text-zinc-300">v{v.version}</span>
                                    <span title="Benchmark scores under different hashes are never compared">
                                        {v.content_hash.replace('sha256:', '').slice(0, 16)}
                                    </span>
                                    <span className="text-zinc-600">{v.created_at?.slice(0, 19).replace('T', ' ')}</span>
                                </div>
                            ))}
                        </div>
                    )}

                    <div className="flex items-center gap-2">
                        <button
                            onClick={save}
                            disabled={busy || !draft.name.trim() || draft.criteria.some(c => !c.id.trim())}
                            className="flex items-center gap-1.5 px-3 py-1.5 bg-white text-black text-[10px] font-bold hover:bg-zinc-200 disabled:opacity-50"
                        >
                            {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
                            Save as New Version
                        </button>
                        <button
                            onClick={() => { setDraft(null); setVersions([]); }}
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
