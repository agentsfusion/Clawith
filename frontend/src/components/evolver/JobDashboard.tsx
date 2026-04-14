import { useState, useEffect, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { evolverApi, type EvolutionJob } from '../../services/api';

interface Props {
    agentId: string;
}

interface NewJobForm {
    direction: string;
    cronSchedule: string;
}

function CronDisplay({ cron }: { cron: string }) {
    const parts = cron.split(' ');
    if (parts.length !== 5) return <span>{cron}</span>;
    const [min, hour, dom, mon, dow] = parts;
    if (dom === '*' && mon === '*' && dow === '*') {
        return <span>Daily at {hour.padStart(2, '0')}:{min.padStart(2, '0')} UTC</span>;
    }
    if (dom === '*' && mon === '*' && dow !== '*') {
        const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
        return <span>{days[parseInt(dow)] || dow} at {hour.padStart(2, '0')}:{min.padStart(2, '0')} UTC</span>;
    }
    return <span>{cron}</span>;
}

function StatusBadge({ status }: { status: string | undefined | null }) {
    if (!status || status === '') {
        return (
            <span style={{
                display: 'inline-flex', alignItems: 'center', gap: '4px',
                padding: '2px 8px', borderRadius: '9999px', fontSize: '12px',
                background: 'rgba(128,128,128,0.1)', color: 'rgba(128,128,128,0.8)',
            }}>⏳ Pending</span>
        );
    }
    if (status === 'running') {
        return (
            <span style={{
                display: 'inline-flex', alignItems: 'center', gap: '4px',
                padding: '2px 8px', borderRadius: '9999px', fontSize: '12px',
                background: 'rgba(59,130,246,0.1)', color: '#60a5fa',
            }}>⟳ Running</span>
        );
    }
    if (status === 'success') {
        return (
            <span style={{
                display: 'inline-flex', alignItems: 'center', gap: '4px',
                padding: '2px 8px', borderRadius: '9999px', fontSize: '12px',
                background: 'rgba(74,222,128,0.1)', color: '#4ade80',
            }}>✓ Success</span>
        );
    }
    return (
        <span style={{
            display: 'inline-flex', alignItems: 'center', gap: '4px',
            padding: '2px 8px', borderRadius: '9999px', fontSize: '12px',
            background: 'rgba(248,113,113,0.1)', color: '#f87171',
        }}>✗ Error</span>
    );
}

function timeAgo(dateStr: string): string {
    const seconds = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000);
    if (seconds < 60) return `${seconds}s ago`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    return `${days}d ago`;
}

function formatDate(dateStr: string): string {
    const d = new Date(dateStr);
    const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    return `${months[d.getUTCMonth()]} ${d.getUTCDate()}, ${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')} UTC`;
}

export default function JobDashboard({ agentId }: Props) {
    const qc = useQueryClient();

    const { data: jobs = [], isLoading } = useQuery({
        queryKey: ['evolution-jobs', agentId],
        queryFn: () => evolverApi.listJobs(agentId),
        refetchInterval: 15000,
    });

    const [showForm, setShowForm] = useState(false);
    const [form, setForm] = useState<NewJobForm>({ direction: '', cronSchedule: '0 0 * * *' });
    const [expandedErrors, setExpandedErrors] = useState<Set<string>>(new Set());
    const [editingJob, setEditingJob] = useState<string | null>(null);
    const [editDirection, setEditDirection] = useState('');
    const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

    const invalidate = useCallback(() => {
        qc.invalidateQueries({ queryKey: ['evolution-jobs', agentId] });
    }, [qc, agentId]);

    const createMut = useMutation({
        mutationFn: () => evolverApi.createJob(agentId, form.direction.trim(), form.cronSchedule),
        onSuccess: () => { invalidate(); setShowForm(false); setForm({ direction: '', cronSchedule: '0 0 * * *' }); },
    });

    const updateMut = useMutation({
        mutationFn: (args: { jobId: string; updates: { direction?: string; cron_schedule?: string; active?: boolean } }) =>
            evolverApi.updateJob(agentId, args.jobId, args.updates),
        onSuccess: () => { invalidate(); setEditingJob(null); },
    });

    const deleteMut = useMutation({
        mutationFn: (jobId: string) => evolverApi.deleteJob(agentId, jobId),
        onSuccess: () => { invalidate(); setConfirmDelete(null); },
    });

    const triggerMut = useMutation({
        mutationFn: (jobId: string) => evolverApi.triggerJob(agentId, jobId),
        onSuccess: invalidate,
    });

    const toggleError = (id: string) => {
        setExpandedErrors(prev => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id); else next.add(id);
            return next;
        });
    };

    const saveEditDirection = (jobId: string) => {
        if (editDirection.trim()) {
            updateMut.mutate({ jobId, updates: { direction: editDirection.trim() } });
        }
        setEditingJob(null);
    };

    const activeCount = jobs.filter(j => j.active).length;
    const runningCount = jobs.filter(j => j.last_run_status === 'running').length;

    const s: Record<string, React.CSSProperties> = {
        root: { display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' },
        header: {
            height: '48px', borderBottom: '1px solid rgba(255,255,255,0.06)', padding: '0 16px',
            display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0,
        },
        headerIcon: { fontSize: '16px', color: '#fbbf24' },
        headerTitle: { fontSize: '14px', fontWeight: 500, color: 'rgba(255,255,255,0.8)' },
        headerBadge: {
            marginLeft: 'auto', fontSize: '11px', color: 'rgba(255,255,255,0.4)',
            display: 'flex', gap: '8px',
        },
        body: { flex: 1, overflowY: 'auto' as const, padding: '16px', display: 'flex', flexDirection: 'column' as const, gap: '12px' },
        addBtn: {
            width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px',
            padding: '12px 16px', fontSize: '13px', fontWeight: 500, borderRadius: '12px',
            background: 'rgba(251,191,36,0.08)', color: '#fbbf24', border: '1px solid rgba(251,191,36,0.2)',
            cursor: 'pointer', transition: 'background 0.15s',
        },
        formCard: {
            borderRadius: '12px', border: '1px solid rgba(255,255,255,0.08)',
            background: 'rgba(255,255,255,0.03)', padding: '16px',
            display: 'flex', flexDirection: 'column' as const, gap: '12px',
        },
        label: { fontSize: '12px', color: 'rgba(255,255,255,0.5)', marginBottom: '4px' },
        textarea: {
            width: '100%', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.1)',
            background: 'rgba(255,255,255,0.03)', padding: '8px 12px', fontSize: '13px',
            color: '#fff', outline: 'none', resize: 'none' as const, fontFamily: 'inherit',
        },
        input: {
            width: '100%', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.1)',
            background: 'rgba(255,255,255,0.03)', padding: '8px 12px', fontSize: '13px',
            color: '#fff', outline: 'none', fontFamily: 'monospace',
        },
        presetBtn: (active: boolean) => ({
            padding: '4px 8px', fontSize: '11px', borderRadius: '8px', cursor: 'pointer',
            border: `1px solid ${active ? 'rgba(59,130,246,0.3)' : 'rgba(255,255,255,0.1)'}`,
            background: active ? 'rgba(59,130,246,0.1)' : 'transparent',
            color: active ? '#60a5fa' : 'rgba(255,255,255,0.5)',
            transition: 'all 0.15s',
        }),
        primaryBtn: {
            flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px',
            padding: '8px 12px', fontSize: '13px', fontWeight: 500, borderRadius: '8px',
            background: 'rgba(59,130,246,0.9)', color: '#fff', border: 'none', cursor: 'pointer',
        },
        cancelBtn: {
            padding: '8px 12px', fontSize: '13px', borderRadius: '8px',
            border: '1px solid rgba(255,255,255,0.1)', background: 'transparent',
            color: 'rgba(255,255,255,0.5)', cursor: 'pointer',
        },
        card: (active: boolean) => ({
            borderRadius: '12px', border: '1px solid rgba(255,255,255,0.08)',
            background: 'rgba(255,255,255,0.03)', padding: '14px',
            display: 'flex', flexDirection: 'column' as const, gap: '10px',
            opacity: active ? 1 : 0.5, transition: 'opacity 0.2s',
        }),
        iconBtn: (hoverColor: string) => ({
            padding: '4px', borderRadius: '6px', background: 'transparent', border: 'none',
            color: 'rgba(255,255,255,0.35)', cursor: 'pointer', fontSize: '14px',
            transition: 'color 0.15s',
        }),
        emptyState: {
            display: 'flex', flexDirection: 'column' as const, alignItems: 'center',
            justifyContent: 'center', padding: '64px 0', textAlign: 'center' as const,
        },
        emptyIcon: {
            width: '64px', height: '64px', marginBottom: '16px', borderRadius: '16px',
            background: 'rgba(251,191,36,0.08)', border: '1px solid rgba(251,191,36,0.2)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '28px',
        },
    };

    return (
        <div style={s.root}>
            <div style={s.header}>
                <span style={s.headerIcon}>⚡</span>
                <span style={s.headerTitle}>Evolution Jobs</span>
                <div style={s.headerBadge}>
                    <span>{jobs.length} job{jobs.length !== 1 ? 's' : ''}</span>
                    {activeCount > 0 && <span style={{ color: '#4ade80' }}>{activeCount} active</span>}
                    {runningCount > 0 && <span style={{ color: '#60a5fa' }}>{runningCount} running</span>}
                </div>
            </div>

            <div style={s.body}>
                {!showForm ? (
                    <button style={s.addBtn} onClick={() => setShowForm(true)}
                        onMouseEnter={e => (e.currentTarget.style.background = 'rgba(251,191,36,0.15)')}
                        onMouseLeave={e => (e.currentTarget.style.background = 'rgba(251,191,36,0.08)')}>
                        + Schedule New Evolution Job
                    </button>
                ) : (
                    <div style={s.formCard}>
                        <div style={{ fontSize: '13px', fontWeight: 500, color: 'rgba(255,255,255,0.85)' }}>New Evolution Job</div>

                        <div>
                            <div style={s.label}>Evolution Direction</div>
                            <textarea style={s.textarea} rows={2} value={form.direction}
                                onChange={e => setForm(f => ({ ...f, direction: e.target.value }))}
                                placeholder="e.g., Improve error handling and edge case coverage" />
                        </div>

                        <div>
                            <div style={s.label}>Schedule (Cron)</div>
                            <div style={{ display: 'flex', gap: '6px', marginBottom: '8px' }}>
                                {[
                                    { label: 'Daily midnight', value: '0 0 * * *' },
                                    { label: 'Every 6h', value: '0 */6 * * *' },
                                    { label: 'Every 12h', value: '0 */12 * * *' },
                                ].map(p => (
                                    <button key={p.value} style={s.presetBtn(form.cronSchedule === p.value)}
                                        onClick={() => setForm(f => ({ ...f, cronSchedule: p.value }))}>
                                        {p.label}
                                    </button>
                                ))}
                            </div>
                            <input style={s.input} value={form.cronSchedule}
                                onChange={e => setForm(f => ({ ...f, cronSchedule: e.target.value }))}
                                placeholder="0 0 * * *" />
                        </div>

                        <div style={{ display: 'flex', gap: '8px', paddingTop: '4px' }}>
                            <button style={{ ...s.primaryBtn, opacity: (!form.direction.trim() || createMut.isPending) ? 0.4 : 1 }}
                                disabled={!form.direction.trim() || createMut.isPending}
                                onClick={() => createMut.mutate()}>
                                {createMut.isPending ? '⟳' : '+'} Create Job
                            </button>
                            <button style={s.cancelBtn} onClick={() => setShowForm(false)}>Cancel</button>
                        </div>
                    </div>
                )}

                {isLoading && (
                    <div style={{ display: 'flex', justifyContent: 'center', padding: '48px 0' }}>
                        <span style={{ fontSize: '24px', color: 'rgba(255,255,255,0.3)', animation: 'spin 1s linear infinite' }}>⟳</span>
                    </div>
                )}

                {!isLoading && jobs.length === 0 && !showForm && (
                    <div style={s.emptyState}>
                        <div style={s.emptyIcon}>⚡</div>
                        <div style={{ fontSize: '16px', fontWeight: 500, color: 'rgba(255,255,255,0.7)', marginBottom: '8px' }}>
                            No Evolution Jobs
                        </div>
                        <div style={{ fontSize: '13px', color: 'rgba(255,255,255,0.4)', maxWidth: '320px' }}>
                            Schedule autonomous evolution jobs to continuously improve your agent scripts.
                            Jobs run on a cron schedule and use feedback + health data to evolve.
                        </div>
                    </div>
                )}

                {jobs.map(job => (
                    <div key={job.id} style={s.card(job.active)}>
                        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '8px' }}>
                            <div style={{ flex: 1, minWidth: 0 }}>
                                {editingJob === job.id ? (
                                    <div style={{ display: 'flex', gap: '6px' }}>
                                        <input style={{ ...s.input, flex: 1, fontFamily: 'inherit' }}
                                            value={editDirection}
                                            onChange={e => setEditDirection(e.target.value)}
                                            onKeyDown={e => {
                                                if (e.key === 'Enter') saveEditDirection(job.id);
                                                if (e.key === 'Escape') setEditingJob(null);
                                            }}
                                            autoFocus />
                                        <button style={{ padding: '4px 10px', fontSize: '12px', borderRadius: '6px',
                                            background: 'rgba(59,130,246,0.9)', color: '#fff', border: 'none', cursor: 'pointer' }}
                                            onClick={() => saveEditDirection(job.id)}>Save</button>
                                    </div>
                                ) : (
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                        <span style={{ fontSize: '13px', fontWeight: 500, color: 'rgba(255,255,255,0.9)' }}>
                                            {job.direction}
                                        </span>
                                        <button style={{ ...s.iconBtn('#fff'), fontSize: '11px', opacity: 0.3 }}
                                            onClick={() => { setEditingJob(job.id); setEditDirection(job.direction); }}
                                            title="Edit direction">✎</button>
                                    </div>
                                )}
                                <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginTop: '4px', fontSize: '12px', color: 'rgba(255,255,255,0.4)' }}>
                                    <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                                        🕐 <CronDisplay cron={job.cron_schedule} />
                                    </span>
                                    <StatusBadge status={job.last_run_status} />
                                </div>
                            </div>

                            <div style={{ display: 'flex', alignItems: 'center', gap: '2px', flexShrink: 0 }}>
                                <button style={s.iconBtn('#4ade80')} title="Run now"
                                    onClick={() => triggerMut.mutate(job.id)}
                                    onMouseEnter={e => (e.currentTarget.style.color = '#4ade80')}
                                    onMouseLeave={e => (e.currentTarget.style.color = 'rgba(255,255,255,0.35)')}>▶</button>
                                <button style={s.iconBtn('#fbbf24')} title={job.active ? 'Pause' : 'Resume'}
                                    onClick={() => updateMut.mutate({ jobId: job.id, updates: { active: !job.active } })}
                                    onMouseEnter={e => (e.currentTarget.style.color = '#fbbf24')}
                                    onMouseLeave={e => (e.currentTarget.style.color = 'rgba(255,255,255,0.35)')}>
                                    {job.active ? '⏸' : '↻'}
                                </button>
                                {confirmDelete === job.id ? (
                                    <div style={{ display: 'flex', gap: '4px', marginLeft: '4px' }}>
                                        <button style={{ padding: '2px 8px', fontSize: '11px', borderRadius: '6px',
                                            background: 'rgba(248,113,113,0.15)', color: '#f87171', border: '1px solid rgba(248,113,113,0.3)',
                                            cursor: 'pointer' }}
                                            onClick={() => deleteMut.mutate(job.id)}>Delete</button>
                                        <button style={{ padding: '2px 8px', fontSize: '11px', borderRadius: '6px',
                                            background: 'transparent', color: 'rgba(255,255,255,0.5)', border: '1px solid rgba(255,255,255,0.1)',
                                            cursor: 'pointer' }}
                                            onClick={() => setConfirmDelete(null)}>Cancel</button>
                                    </div>
                                ) : (
                                    <button style={s.iconBtn('#f87171')} title="Delete"
                                        onClick={() => setConfirmDelete(job.id)}
                                        onMouseEnter={e => (e.currentTarget.style.color = '#f87171')}
                                        onMouseLeave={e => (e.currentTarget.style.color = 'rgba(255,255,255,0.35)')}>🗑</button>
                                )}
                            </div>
                        </div>

                        <div style={{ display: 'flex', alignItems: 'center', gap: '16px', fontSize: '11px', color: 'rgba(255,255,255,0.35)' }}>
                            {job.last_run_at && <span>Last run: {timeAgo(job.last_run_at)}</span>}
                            {job.next_run_at && <span>Next: {formatDate(job.next_run_at)}</span>}
                        </div>

                        {job.last_run_status === 'error' && job.last_run_error && (
                            <div>
                                <button style={{ background: 'none', border: 'none', cursor: 'pointer',
                                    fontSize: '11px', color: '#f87171', display: 'flex', alignItems: 'center', gap: '4px' }}
                                    onClick={() => toggleError(job.id)}>
                                    {expandedErrors.has(job.id) ? '▲ Hide error' : '▼ Show error'}
                                </button>
                                {expandedErrors.has(job.id) && (
                                    <pre style={{
                                        marginTop: '4px', padding: '8px', borderRadius: '8px',
                                        background: 'rgba(248,113,113,0.05)', border: '1px solid rgba(248,113,113,0.15)',
                                        fontSize: '11px', color: '#f87171', overflow: 'auto', whiteSpace: 'pre-wrap',
                                    }}>{job.last_run_error}</pre>
                                )}
                            </div>
                        )}
                    </div>
                ))}
            </div>
        </div>
    );
}
