import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { evolverApi, type EvolverHealthCheck } from '../../services/api';

function ScoreGauge({ score, size = 'lg' }: { score: number; size?: 'sm' | 'lg' }) {
    const color = score >= 80 ? '#4ade80' : score >= 60 ? '#fbbf24' : '#f87171';
    const isLg = size === 'lg';
    return (
        <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            width: isLg ? '64px' : '40px', height: isLg ? '64px' : '40px',
            borderRadius: '12px', border: `1.5px solid ${color}40`,
            background: `${color}10`,
        }}>
            <span style={{ fontWeight: 700, color, fontSize: isLg ? '20px' : '14px' }}>{score}</span>
        </div>
    );
}

function DimensionBar({ name, score, feedback }: { name: string; score: number; feedback: string }) {
    const color = score >= 80 ? '#4ade80' : score >= 60 ? '#fbbf24' : '#f87171';
    return (
        <div style={{ marginBottom: '10px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', marginBottom: '4px' }}>
                <span style={{ color: 'var(--text-secondary)' }}>{name}</span>
                <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{score}</span>
            </div>
            <div style={{ height: '6px', borderRadius: '3px', background: 'var(--bg-tertiary)', overflow: 'hidden' }}>
                <div style={{ height: '100%', borderRadius: '3px', background: color, width: `${score}%`, transition: 'width 0.5s ease' }} />
            </div>
            <p style={{ margin: '4px 0 0', fontSize: '11px', color: 'var(--text-tertiary)', lineHeight: 1.4 }}>{feedback}</p>
        </div>
    );
}

export default function HealthTab({ agentId }: { agentId: string }) {
    const { t } = useTranslation();
    const qc = useQueryClient();
    const [expandedId, setExpandedId] = useState<string | null>(null);

    const { data: checks = [], isLoading } = useQuery({
        queryKey: ['evolver-health', agentId],
        queryFn: () => evolverApi.listHealthChecks(agentId),
    });

    const triggerMutation = useMutation({
        mutationFn: () => evolverApi.triggerHealthCheck(agentId),
        onSuccess: () => qc.invalidateQueries({ queryKey: ['evolver-health', agentId] }),
    });

    const latest = checks[0];
    const previous = checks[1];
    const trend = latest && previous ? latest.overall_score - previous.overall_score : null;

    return (
        <div style={{ padding: '20px', maxWidth: '800px' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px' }}>
                <div>
                    <h3 style={{ margin: 0, fontSize: '16px', fontWeight: 600 }}>
                        {t('evolver.healthChecks', 'Health Checks')}
                    </h3>
                    <p style={{ margin: '4px 0 0', fontSize: '12px', color: 'var(--text-tertiary)' }}>
                        {t('evolver.healthDesc', 'AI-powered quality analysis of your agent script')}
                    </p>
                </div>
                <button
                    className="btn btn-primary"
                    style={{ fontSize: '13px', padding: '6px 14px' }}
                    disabled={triggerMutation.isPending}
                    onClick={() => triggerMutation.mutate()}
                >
                    {triggerMutation.isPending ? t('evolver.analyzing', 'Analyzing...') : t('evolver.runCheck', 'Run Health Check')}
                </button>
            </div>

            {latest && (
                <div style={{
                    display: 'flex', alignItems: 'center', gap: '16px',
                    padding: '16px', borderRadius: '12px', marginBottom: '20px',
                    border: '1px solid var(--border-default)', background: 'var(--bg-elevated)',
                }}>
                    <ScoreGauge score={latest.overall_score} />
                    <div>
                        <div style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>
                            {t('evolver.latestScore', 'Latest Score')}
                            {trend !== null && (
                                <span style={{
                                    marginLeft: '8px', fontSize: '12px', fontWeight: 500,
                                    color: trend > 0 ? '#4ade80' : trend < 0 ? '#f87171' : 'var(--text-tertiary)',
                                }}>
                                    {trend > 0 ? `+${trend}` : trend}
                                </span>
                            )}
                        </div>
                        <p style={{ margin: '2px 0 0', fontSize: '12px', color: 'var(--text-tertiary)' }}>
                            {latest.script_version} · {new Date(latest.created_at).toLocaleString()}
                        </p>
                    </div>
                </div>
            )}

            {isLoading ? (
                <p style={{ color: 'var(--text-tertiary)', fontSize: '13px' }}>{t('common.loading', 'Loading...')}</p>
            ) : checks.length === 0 ? (
                <p style={{ color: 'var(--text-tertiary)', fontSize: '13px', textAlign: 'center', padding: '40px 0' }}>
                    {t('evolver.noHealthChecks', 'No health checks yet. Run one to analyze your agent script.')}
                </p>
            ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    {checks.map(check => {
                        const isExpanded = expandedId === check.id;
                        const dims = (check.dimensions || []) as { name: string; score: number; feedback: string }[];
                        const strengths = (check.strengths || []) as string[];
                        const suggestions = (check.suggestions || []) as string[];

                        return (
                            <div key={check.id} style={{
                                border: '1px solid var(--border-default)', borderRadius: '10px',
                                background: 'var(--bg-elevated)', overflow: 'hidden',
                            }}>
                                <div
                                    onClick={() => setExpandedId(isExpanded ? null : check.id)}
                                    style={{
                                        display: 'flex', alignItems: 'center', gap: '12px',
                                        padding: '12px 16px', cursor: 'pointer',
                                    }}
                                >
                                    <span style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>{isExpanded ? '▼' : '▸'}</span>
                                    <ScoreGauge score={check.overall_score} size="sm" />
                                    <div style={{ flex: 1 }}>
                                        <p style={{ margin: 0, fontSize: '13px', fontWeight: 500, color: 'var(--text-primary)' }}>
                                            {check.script_version || 'Health Check'}
                                        </p>
                                        <p style={{ margin: '2px 0 0', fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                            {new Date(check.created_at).toLocaleString()}
                                        </p>
                                    </div>
                                </div>

                                {isExpanded && (
                                    <div style={{ padding: '0 16px 16px', borderTop: '1px solid var(--border-subtle)' }}>
                                        <div style={{ paddingTop: '12px' }}>
                                            {dims.map(dim => (
                                                <DimensionBar key={dim.name} name={dim.name} score={dim.score} feedback={dim.feedback} />
                                            ))}
                                        </div>

                                        {strengths.length > 0 && (
                                            <div style={{ marginTop: '12px' }}>
                                                <p style={{ fontSize: '12px', fontWeight: 600, color: '#4ade80', marginBottom: '6px' }}>Strengths</p>
                                                {strengths.map((s, i) => (
                                                    <p key={i} style={{ fontSize: '11px', color: 'var(--text-secondary)', margin: '2px 0', paddingLeft: '12px' }}>• {s}</p>
                                                ))}
                                            </div>
                                        )}

                                        {suggestions.length > 0 && (
                                            <div style={{ marginTop: '12px' }}>
                                                <p style={{ fontSize: '12px', fontWeight: 600, color: '#fbbf24', marginBottom: '6px' }}>Suggestions</p>
                                                {suggestions.map((s, i) => (
                                                    <p key={i} style={{ fontSize: '11px', color: 'var(--text-secondary)', margin: '2px 0', paddingLeft: '12px' }}>• {s}</p>
                                                ))}
                                            </div>
                                        )}
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
}
