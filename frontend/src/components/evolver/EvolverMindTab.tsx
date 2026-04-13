import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { evolverApi, fileApi, type EvolverScriptVersion } from '../../services/api';

export default function EvolverMindTab({ agentId }: { agentId: string }) {
    const { t } = useTranslation();
    const qc = useQueryClient();
    const [isEditing, setIsEditing] = useState(false);
    const [editContent, setEditContent] = useState('');

    const { data: versions = [], isLoading } = useQuery({
        queryKey: ['evolver-scripts', agentId, null],
        queryFn: () => evolverApi.listScriptVersions(agentId),
    });

    const latestScript = versions.find(v => v.folder === 'evolved') || versions.find(v => v.folder === 'initial');

    const saveMutation = useMutation({
        mutationFn: async () => {
            await evolverApi.createScriptVersion(agentId, latestScript?.folder || 'initial', editContent, 'manual-edit');
            await fileApi.write(agentId, 'soul.md', editContent);
        },
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['evolver-scripts', agentId] });
            qc.invalidateQueries({ queryKey: ['file', agentId, 'soul.md'] });
            setIsEditing(false);
        },
    });

    const handleEdit = () => {
        if (latestScript) {
            setEditContent(latestScript.content);
        }
        setIsEditing(true);
    };

    const handleCancel = () => {
        setIsEditing(false);
        setEditContent('');
    };

    if (isLoading) {
        return (
            <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: '13px' }}>
                {t('common.loading', 'Loading...')}
            </div>
        );
    }

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
            <div>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '4px' }}>
                    <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
                        📜 {t('agent.mind.agentScript', 'Agent Script')}
                    </h3>
                    <div style={{ display: 'flex', gap: '6px' }}>
                        {isEditing ? (
                            <>
                                <button
                                    className="btn"
                                    style={{ fontSize: '12px', padding: '4px 12px' }}
                                    onClick={handleCancel}
                                    disabled={saveMutation.isPending}
                                >
                                    {t('common.cancel', 'Cancel')}
                                </button>
                                <button
                                    className="btn btn-primary"
                                    style={{ fontSize: '12px', padding: '4px 12px' }}
                                    onClick={() => saveMutation.mutate()}
                                    disabled={!editContent.trim() || saveMutation.isPending}
                                >
                                    {saveMutation.isPending ? t('common.loading', 'Saving...') : t('common.save', 'Save')}
                                </button>
                            </>
                        ) : (
                            <>
                                {latestScript && (
                                    <button
                                        className="btn"
                                        style={{ fontSize: '12px', padding: '4px 12px' }}
                                        onClick={() => navigator.clipboard.writeText(latestScript.content)}
                                    >
                                        {t('common.copy', 'Copy')}
                                    </button>
                                )}
                                <button
                                    className="btn"
                                    style={{ fontSize: '12px', padding: '4px 12px' }}
                                    onClick={handleEdit}
                                >
                                    {t('common.edit', 'Edit')}
                                </button>
                            </>
                        )}
                    </div>
                </div>
                <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                    {t('agent.mind.agentScriptDesc', 'The Agent Script defines this evolver agent\'s behavior, replacing the traditional Soul identity.')}
                </p>

                {latestScript && !isEditing && (
                    <div style={{ display: 'flex', gap: '8px', marginBottom: '12px', alignItems: 'center' }}>
                        <span style={{
                            fontSize: '10px', padding: '2px 8px', borderRadius: '6px', fontWeight: 600,
                            color: latestScript.folder === 'evolved' ? '#4ade80' : '#60a5fa',
                            background: latestScript.folder === 'evolved' ? '#4ade8015' : '#60a5fa15',
                            border: `1px solid ${latestScript.folder === 'evolved' ? '#4ade8030' : '#60a5fa30'}`,
                        }}>
                            {latestScript.folder === 'evolved' ? 'Evolved' : 'Initial'}
                        </span>
                        <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-primary)' }}>
                            v{latestScript.version}
                        </span>
                        <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                            {new Date(latestScript.created_at).toLocaleString()}
                        </span>
                    </div>
                )}

                {isEditing ? (
                    <textarea
                        value={editContent}
                        onChange={e => setEditContent(e.target.value)}
                        rows={20}
                        style={{
                            width: '100%', resize: 'vertical', fontSize: '12px',
                            fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
                            padding: '14px 16px', borderRadius: '10px',
                            border: '1px solid var(--border-default)', background: '#1a1b26',
                            color: '#c0caf5', lineHeight: 1.65,
                        }}
                    />
                ) : latestScript ? (
                    <div style={{ borderRadius: '10px', overflow: 'hidden', border: '1px solid var(--border-default)' }}>
                        <pre style={{
                            margin: 0, padding: '14px 16px', background: '#1a1b26',
                            color: '#c0caf5', fontSize: '12px', lineHeight: 1.65,
                            fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
                            overflow: 'auto', maxHeight: '500px', whiteSpace: 'pre',
                        }}>
                            {latestScript.content}
                        </pre>
                    </div>
                ) : (
                    <div style={{
                        padding: '40px', textAlign: 'center', borderRadius: '10px',
                        border: '1px dashed var(--border-default)', color: 'var(--text-tertiary)', fontSize: '13px',
                    }}>
                        {t('agent.mind.noScript', 'No Agent Script defined yet. Click Edit to create one.')}
                    </div>
                )}
            </div>
        </div>
    );
}
