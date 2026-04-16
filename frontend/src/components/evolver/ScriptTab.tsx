import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { evolverApi, type EvolverScriptVersion } from '../../services/api';

const FOLDER_LABELS: Record<string, { label: string; color: string }> = {
    initial: { label: 'Initial', color: '#60a5fa' },
    evolved: { label: 'Evolved', color: '#4ade80' },
    evolution_knowledge: { label: 'Knowledge', color: '#a78bfa' },
};

export default function ScriptTab({ agentId }: { agentId: string }) {
    const { t } = useTranslation();
    const qc = useQueryClient();
    const [selectedVersion, setSelectedVersion] = useState<EvolverScriptVersion | null>(null);
    const [folderFilter, setFolderFilter] = useState<string | null>(null);
    const [direction, setDirection] = useState('');
    const [showUpload, setShowUpload] = useState(false);
    const [uploadContent, setUploadContent] = useState('');

    const { data: versions = [], isLoading } = useQuery({
        queryKey: ['evolver-scripts', agentId, folderFilter],
        queryFn: () => evolverApi.listScriptVersions(agentId, folderFilter || undefined),
    });

    const evolveMutation = useMutation({
        mutationFn: () => evolverApi.triggerEvolution(agentId, direction || undefined),
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['evolver-scripts', agentId] });
            qc.invalidateQueries({ queryKey: ['evolver-feedbacks', agentId] });
            setDirection('');
        },
    });

    const uploadMutation = useMutation({
        mutationFn: () => evolverApi.createScriptVersion(agentId, 'initial', uploadContent, 'manual-upload'),
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['evolver-scripts', agentId] });
            setShowUpload(false);
            setUploadContent('');
        },
    });

    const latestScript = versions.find(v => v.folder === 'evolved') || versions.find(v => v.folder === 'initial');

    return (
        <div style={{ padding: '20px', maxWidth: '900px' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px' }}>
                <div>
                    <h3 style={{ margin: 0, fontSize: '16px', fontWeight: 600 }}>
                        {t('evolver.scriptVersions', 'Script & Evolution')}
                    </h3>
                    <p style={{ margin: '4px 0 0', fontSize: '12px', color: 'var(--text-tertiary)' }}>
                        {t('evolver.scriptDesc', 'Agent Script versions and evolution history')}
                    </p>
                </div>
                <div style={{ display: 'flex', gap: '8px' }}>
                    <button
                        className="btn"
                        style={{ fontSize: '13px', padding: '6px 14px' }}
                        onClick={() => setShowUpload(!showUpload)}
                    >
                        {showUpload ? t('common.cancel', 'Cancel') : t('evolver.uploadScript', 'Upload Script')}
                    </button>
                    <button
                        className="btn btn-primary"
                        style={{ fontSize: '13px', padding: '6px 14px' }}
                        disabled={evolveMutation.isPending || !latestScript}
                        onClick={() => evolveMutation.mutate()}
                    >
                        {evolveMutation.isPending ? t('evolver.evolving', 'Evolving...') : t('evolver.triggerEvolution', 'Trigger Evolution')}
                    </button>
                </div>
            </div>

            {showUpload && (
                <div style={{
                    border: '1px solid var(--border-default)', borderRadius: '10px',
                    padding: '16px', marginBottom: '16px', background: 'var(--bg-elevated)',
                }}>
                    <p style={{ margin: '0 0 8px', fontSize: '13px', fontWeight: 500, color: 'var(--text-primary)' }}>
                        {t('evolver.pasteScript', 'Paste your Agent Script below:')}
                    </p>
                    <textarea
                        value={uploadContent}
                        onChange={e => setUploadContent(e.target.value)}
                        placeholder="config:\n  agent_name: &quot;MyAgent&quot;\n  ..."
                        rows={10}
                        style={{
                            width: '100%', resize: 'vertical', fontSize: '12px',
                            fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
                            padding: '10px', borderRadius: '8px',
                            border: '1px solid var(--border-default)', background: '#1a1b26',
                            color: '#c0caf5', lineHeight: 1.6,
                        }}
                    />
                    <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '10px' }}>
                        <button
                            className="btn btn-primary"
                            disabled={!uploadContent.trim() || uploadMutation.isPending}
                            onClick={() => uploadMutation.mutate()}
                            style={{ fontSize: '13px', padding: '6px 16px' }}
                        >
                            {uploadMutation.isPending ? t('common.loading', 'Saving...') : t('evolver.saveAsInitial', 'Save as Initial')}
                        </button>
                    </div>
                </div>
            )}

            <div style={{ marginBottom: '16px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
                    <input
                        value={direction}
                        onChange={e => setDirection(e.target.value)}
                        placeholder={t('evolver.directionPlaceholder', 'Evolution direction (optional)...')}
                        style={{
                            flex: 1, fontSize: '13px', padding: '8px 12px', borderRadius: '8px',
                            border: '1px solid var(--border-default)', background: 'var(--bg-primary)',
                            color: 'var(--text-primary)',
                        }}
                    />
                </div>
            </div>

            {evolveMutation.isSuccess && evolveMutation.data && (
                <div style={{
                    padding: '10px 14px', borderRadius: '8px', marginBottom: '16px',
                    background: '#4ade8015', border: '1px solid #4ade8030', fontSize: '13px', color: '#4ade80',
                }}>
                    Evolution complete — v{evolveMutation.data.version}
                    {evolveMutation.data.feedbacks_addressed ? ` · ${evolveMutation.data.feedbacks_addressed} feedback(s) addressed` : ''}
                </div>
            )}

            <div style={{ display: 'flex', gap: '6px', marginBottom: '16px' }}>
                <button
                    onClick={() => setFolderFilter(null)}
                    className={`btn ${!folderFilter ? 'btn-primary' : ''}`}
                    style={{ fontSize: '11px', padding: '3px 10px' }}
                >All</button>
                {Object.entries(FOLDER_LABELS).map(([key, cfg]) => (
                    <button
                        key={key}
                        onClick={() => setFolderFilter(key)}
                        className={`btn ${folderFilter === key ? 'btn-primary' : ''}`}
                        style={{ fontSize: '11px', padding: '3px 10px', color: folderFilter === key ? undefined : cfg.color }}
                    >{cfg.label}</button>
                ))}
            </div>

            {isLoading ? (
                <p style={{ color: 'var(--text-tertiary)', fontSize: '13px' }}>{t('common.loading', 'Loading...')}</p>
            ) : versions.length === 0 ? (
                <p style={{ color: 'var(--text-tertiary)', fontSize: '13px', textAlign: 'center', padding: '40px 0' }}>
                    {t('evolver.noScripts', 'No scripts yet. Upload an initial script or trigger evolution.')}
                </p>
            ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                    {versions.map(v => {
                        const folderCfg = FOLDER_LABELS[v.folder] || { label: v.folder, color: '#9ca3af' };
                        const isSelected = selectedVersion?.id === v.id;

                        return (
                            <div key={v.id}>
                                <div
                                    onClick={() => setSelectedVersion(isSelected ? null : v)}
                                    style={{
                                        display: 'flex', alignItems: 'center', gap: '10px',
                                        padding: '10px 14px', borderRadius: '8px', cursor: 'pointer',
                                        border: `1px solid ${isSelected ? 'var(--accent-primary)' : 'var(--border-default)'}`,
                                        background: isSelected ? 'var(--accent-subtle)' : 'var(--bg-elevated)',
                                    }}
                                >
                                    <span style={{
                                        fontSize: '10px', padding: '2px 8px', borderRadius: '6px', fontWeight: 600,
                                        color: folderCfg.color, background: `${folderCfg.color}15`, border: `1px solid ${folderCfg.color}30`,
                                    }}>{folderCfg.label}</span>
                                    <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)' }}>
                                        v{v.version}
                                    </span>
                                    <span style={{ flex: 1, fontSize: '11px', color: 'var(--text-tertiary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                        {v.source || ''}
                                    </span>
                                    <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', flexShrink: 0 }}>
                                        {new Date(v.created_at).toLocaleString()}
                                    </span>
                                </div>

                                {isSelected && (
                                    <div style={{
                                        marginTop: '4px', borderRadius: '8px', overflow: 'hidden',
                                        border: '1px solid var(--border-default)',
                                    }}>
                                        <div style={{ display: 'flex', justifyContent: 'flex-end', padding: '6px 10px', background: '#1e1f2e' }}>
                                            <button
                                                onClick={() => navigator.clipboard.writeText(v.content)}
                                                style={{
                                                    background: 'none', border: '1px solid rgba(255,255,255,0.12)',
                                                    borderRadius: '4px', color: '#7aa2f7', cursor: 'pointer',
                                                    padding: '3px 8px', fontSize: '11px',
                                                }}
                                            >Copy</button>
                                        </div>
                                        <pre style={{
                                            margin: 0, padding: '14px 16px', background: '#1a1b26',
                                            color: '#c0caf5', fontSize: '12px', lineHeight: 1.65,
                                            fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
                                            overflow: 'auto', maxHeight: '400px', whiteSpace: 'pre',
                                        }}>
                                            {v.content}
                                        </pre>
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
