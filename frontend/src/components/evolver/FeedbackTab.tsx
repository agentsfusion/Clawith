import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { evolverApi, type EvolverFeedback } from '../../services/api';

const CATEGORIES = [
    { value: 'improvement', label: 'Improvement', color: '#60a5fa' },
    { value: 'bug', label: 'Bug Report', color: '#f87171' },
    { value: 'quality', label: 'Quality', color: '#4ade80' },
    { value: 'direction', label: 'Direction', color: '#a78bfa' },
    { value: 'general', label: 'General', color: '#9ca3af' },
];

const STATUS_CONFIG: Record<string, { label: string; color: string }> = {
    open: { label: 'Open', color: '#fbbf24' },
    addressed: { label: 'Addressed', color: '#4ade80' },
    dismissed: { label: 'Dismissed', color: '#9ca3af' },
};

function CategoryBadge({ category }: { category: string }) {
    const cat = CATEGORIES.find(c => c.value === category) || CATEGORIES[4];
    return (
        <span style={{
            display: 'inline-flex', alignItems: 'center', gap: '4px',
            padding: '2px 8px', borderRadius: '12px', fontSize: '11px', fontWeight: 500,
            color: cat.color, background: `${cat.color}15`, border: `1px solid ${cat.color}30`,
        }}>
            {cat.label}
        </span>
    );
}

function StatusBadge({ status, onClick }: { status: string; onClick?: () => void }) {
    const config = STATUS_CONFIG[status] || STATUS_CONFIG.open;
    return (
        <button
            onClick={onClick}
            style={{
                display: 'inline-flex', alignItems: 'center', gap: '4px',
                padding: '2px 8px', borderRadius: '12px', fontSize: '11px', fontWeight: 500,
                color: config.color, background: `${config.color}15`, border: `1px solid ${config.color}30`,
                cursor: onClick ? 'pointer' : 'default',
            }}
        >
            {config.label}
        </button>
    );
}

export default function FeedbackTab({ agentId }: { agentId: string }) {
    const { t } = useTranslation();
    const qc = useQueryClient();
    const [showForm, setShowForm] = useState(false);
    const [category, setCategory] = useState('improvement');
    const [content, setContent] = useState('');
    const [statusFilter, setStatusFilter] = useState<string | null>(null);

    const { data: feedbacks = [], isLoading } = useQuery({
        queryKey: ['evolver-feedbacks', agentId],
        queryFn: () => evolverApi.listFeedbacks(agentId),
    });

    const createMutation = useMutation({
        mutationFn: () => evolverApi.createFeedback(agentId, category, content),
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['evolver-feedbacks', agentId] });
            setShowForm(false);
            setContent('');
        },
    });

    const updateMutation = useMutation({
        mutationFn: ({ feedbackId, data }: { feedbackId: string; data: { status?: string } }) =>
            evolverApi.updateFeedback(agentId, feedbackId, data),
        onSuccess: () => qc.invalidateQueries({ queryKey: ['evolver-feedbacks', agentId] }),
    });

    const deleteMutation = useMutation({
        mutationFn: (feedbackId: string) => evolverApi.deleteFeedback(agentId, feedbackId),
        onSuccess: () => qc.invalidateQueries({ queryKey: ['evolver-feedbacks', agentId] }),
    });

    const filtered = statusFilter
        ? feedbacks.filter(f => f.status === statusFilter)
        : feedbacks;

    const openCount = feedbacks.filter(f => f.status === 'open').length;

    return (
        <div style={{ padding: '20px', maxWidth: '800px' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px' }}>
                <div>
                    <h3 style={{ margin: 0, fontSize: '16px', fontWeight: 600 }}>
                        {t('evolver.feedback', 'Feedback')}
                    </h3>
                    <p style={{ margin: '4px 0 0', fontSize: '12px', color: 'var(--text-tertiary)' }}>
                        {openCount} {t('evolver.openFeedbacks', 'open feedback(s) will be addressed in next evolution')}
                    </p>
                </div>
                <button
                    className="btn btn-primary"
                    style={{ fontSize: '13px', padding: '6px 14px' }}
                    onClick={() => setShowForm(!showForm)}
                >
                    {showForm ? t('common.cancel', 'Cancel') : t('evolver.addFeedback', '+ Add Feedback')}
                </button>
            </div>

            <div style={{ display: 'flex', gap: '6px', marginBottom: '16px' }}>
                <button
                    onClick={() => setStatusFilter(null)}
                    className={`btn ${!statusFilter ? 'btn-primary' : ''}`}
                    style={{ fontSize: '11px', padding: '3px 10px' }}
                >All ({feedbacks.length})</button>
                {Object.entries(STATUS_CONFIG).map(([key, cfg]) => (
                    <button
                        key={key}
                        onClick={() => setStatusFilter(key)}
                        className={`btn ${statusFilter === key ? 'btn-primary' : ''}`}
                        style={{ fontSize: '11px', padding: '3px 10px', color: statusFilter === key ? undefined : cfg.color }}
                    >{cfg.label} ({feedbacks.filter(f => f.status === key).length})</button>
                ))}
            </div>

            {showForm && (
                <div style={{
                    border: '1px solid var(--border-default)', borderRadius: '10px',
                    padding: '16px', marginBottom: '16px', background: 'var(--bg-elevated)',
                }}>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginBottom: '12px' }}>
                        {CATEGORIES.map(cat => (
                            <button
                                key={cat.value}
                                onClick={() => setCategory(cat.value)}
                                style={{
                                    padding: '4px 12px', borderRadius: '8px', fontSize: '12px', fontWeight: 500,
                                    border: `1px solid ${category === cat.value ? cat.color : 'var(--border-default)'}`,
                                    background: category === cat.value ? `${cat.color}15` : 'transparent',
                                    color: category === cat.value ? cat.color : 'var(--text-secondary)',
                                    cursor: 'pointer',
                                }}
                            >{cat.label}</button>
                        ))}
                    </div>
                    <textarea
                        value={content}
                        onChange={e => setContent(e.target.value)}
                        placeholder={t('evolver.feedbackPlaceholder', 'Describe your feedback, improvement suggestion, or issue...')}
                        rows={3}
                        style={{
                            width: '100%', resize: 'none', fontSize: '13px',
                            padding: '10px', borderRadius: '8px',
                            border: '1px solid var(--border-default)', background: 'var(--bg-primary)',
                            color: 'var(--text-primary)',
                        }}
                    />
                    <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '10px' }}>
                        <button
                            className="btn btn-primary"
                            disabled={!content.trim() || createMutation.isPending}
                            onClick={() => createMutation.mutate()}
                            style={{ fontSize: '13px', padding: '6px 16px' }}
                        >
                            {createMutation.isPending ? t('common.loading', 'Saving...') : t('evolver.submit', 'Submit')}
                        </button>
                    </div>
                </div>
            )}

            {isLoading ? (
                <p style={{ color: 'var(--text-tertiary)', fontSize: '13px' }}>{t('common.loading', 'Loading...')}</p>
            ) : filtered.length === 0 ? (
                <p style={{ color: 'var(--text-tertiary)', fontSize: '13px', textAlign: 'center', padding: '40px 0' }}>
                    {t('evolver.noFeedbacks', 'No feedback yet. Add feedback to guide the agent\'s evolution.')}
                </p>
            ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    {filtered.map(fb => (
                        <div key={fb.id} style={{
                            border: '1px solid var(--border-default)', borderRadius: '10px',
                            padding: '12px 16px', background: 'var(--bg-elevated)',
                        }}>
                            <div style={{ display: 'flex', alignItems: 'flex-start', gap: '8px' }}>
                                <div style={{ flex: 1 }}>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px' }}>
                                        <CategoryBadge category={fb.category} />
                                        <StatusBadge
                                            status={fb.status}
                                            onClick={() => {
                                                const nextStatus = fb.status === 'open' ? 'addressed' : fb.status === 'addressed' ? 'dismissed' : 'open';
                                                updateMutation.mutate({ feedbackId: fb.id, data: { status: nextStatus } });
                                            }}
                                        />
                                    </div>
                                    <p style={{ margin: 0, fontSize: '13px', lineHeight: 1.5, color: 'var(--text-primary)', whiteSpace: 'pre-wrap' }}>
                                        {fb.content}
                                    </p>
                                    <p style={{ margin: '6px 0 0', fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                        {new Date(fb.created_at).toLocaleString()}
                                    </p>
                                </div>
                                <button
                                    onClick={() => deleteMutation.mutate(fb.id)}
                                    style={{
                                        background: 'none', border: 'none', cursor: 'pointer',
                                        color: 'var(--text-tertiary)', fontSize: '16px', padding: '4px',
                                    }}
                                    title="Delete"
                                >×</button>
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
