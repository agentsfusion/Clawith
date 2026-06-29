import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { skillApi, fileApi } from '../../services/api';

interface MissingSkill {
    action: string;
    folder_name: string;
}

interface MissingTool {
    action: string;
    tool_name: string;
}

interface SkillStatus {
    folder_name: string;
    action: string;
    presetSkillId: string | null;
    status: 'loading' | 'ready' | 'importing' | 'imported' | 'error' | 'not-found';
    error?: string;
}

interface Props {
    agentId: string;
    missingSkills: MissingSkill[];
    missingTools: MissingTool[];
    onClose: () => void;
    onRetry: () => void;
}

export default function ScriptValidationErrorDialog({
    agentId,
    missingSkills,
    missingTools,
    onClose,
    onRetry,
}: Props) {
    const { t } = useTranslation();
    const [skillStatuses, setSkillStatuses] = useState<SkillStatus[]>([]);
    const [loadingPresets, setLoadingPresets] = useState(true);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const allSkills = await skillApi.list();
                if (cancelled) return;

                const statuses: SkillStatus[] = missingSkills.map(ms => {
                    const match = allSkills.find(
                        (s: any) => s.folder_name === ms.folder_name
                    );
                    return {
                        folder_name: ms.folder_name,
                        action: ms.action,
                        presetSkillId: match ? match.id : null,
                        status: match ? 'ready' : 'not-found',
                    };
                });
                setSkillStatuses(statuses);
            } catch {
                if (cancelled) return;
                setSkillStatuses(
                    missingSkills.map(ms => ({
                        folder_name: ms.folder_name,
                        action: ms.action,
                        presetSkillId: null,
                        status: 'not-found' as const,
                    }))
                );
            } finally {
                if (!cancelled) setLoadingPresets(false);
            }
        })();
        return () => { cancelled = true; };
    }, [missingSkills]);

    const handleImport = useCallback(async (index: number) => {
        const skill = skillStatuses[index];
        if (!skill?.presetSkillId) return;

        setSkillStatuses(prev => {
            const next = [...prev];
            next[index] = { ...next[index], status: 'importing' };
            return next;
        });

        try {
            await fileApi.importSkill(agentId, skill.presetSkillId);
            setSkillStatuses(prev => {
                const next = [...prev];
                next[index] = { ...next[index], status: 'imported' };
                return next;
            });
        } catch (err: any) {
            setSkillStatuses(prev => {
                const next = [...prev];
                next[index] = { ...next[index], status: 'error', error: err?.message || 'Import failed' };
                return next;
            });
        }
    }, [agentId, skillStatuses]);

    const allSkillsImported = skillStatuses.length > 0 && skillStatuses.every(s => s.status === 'imported');
    const hasImportableSkills = skillStatuses.some(s => s.presetSkillId !== null);

    return (
        <div style={{
            position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
            background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center',
            zIndex: 10000,
        }} onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
            <div style={{
                background: 'var(--bg-primary)', borderRadius: '14px', padding: '0',
                width: '540px', maxWidth: '90vw', maxHeight: '80vh',
                border: '1px solid var(--border-subtle)',
                boxShadow: '0 20px 60px rgba(0,0,0,0.5)',
                display: 'flex', flexDirection: 'column',
            }}>
                <div style={{
                    padding: '18px 22px', borderBottom: '1px solid var(--border-subtle)',
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                        <span style={{ fontSize: '18px' }}>⚠️</span>
                        <h4 style={{ margin: 0, fontSize: '15px', fontWeight: 600, color: 'var(--text-primary)' }}>
                            {t('evolver.scriptValidation.title', 'Script References Unavailable Resources')}
                        </h4>
                    </div>
                    <button
                        onClick={onClose}
                        style={{
                            background: 'none', border: 'none', color: 'var(--text-tertiary)',
                            cursor: 'pointer', fontSize: '16px', padding: '4px',
                        }}
                    >✕</button>
                </div>

                <div style={{ padding: '18px 22px', overflowY: 'auto', flex: 1 }}>
                    {missingSkills.length > 0 && (
                        <div style={{ marginBottom: '20px' }}>
                            <h5 style={{
                                margin: '0 0 10px', fontSize: '13px', fontWeight: 600,
                                color: '#f59e0b', display: 'flex', alignItems: 'center', gap: '6px',
                            }}>
                                📋 {t('evolver.scriptValidation.missingSkills', 'Missing Skills')} ({missingSkills.length})
                            </h5>
                            {loadingPresets ? (
                                <p style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
                                    {t('common.loading', 'Loading presets...')}
                                </p>
                            ) : (
                                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                                    {skillStatuses.map((skill, i) => (
                                        <div key={skill.folder_name} style={{
                                            display: 'flex', alignItems: 'center', gap: '10px',
                                            padding: '10px 14px', borderRadius: '8px',
                                            background: 'var(--bg-elevated)',
                                            border: '1px solid var(--border-default)',
                                        }}>
                                            <div style={{ flex: 1, minWidth: 0 }}>
                                                <div style={{
                                                    fontSize: '13px', fontWeight: 500,
                                                    color: 'var(--text-primary)',
                                                    display: 'flex', alignItems: 'center', gap: '6px',
                                                }}>
                                                    <code style={{
                                                        fontSize: '12px',
                                                        background: 'rgba(255,255,255,0.06)',
                                                        padding: '1px 6px', borderRadius: '4px',
                                                    }}>skill://{skill.folder_name}</code>
                                                    {skill.status === 'imported' && (
                                                        <span style={{ color: '#4ade80', fontSize: '12px' }}>✓</span>
                                                    )}
                                                </div>
                                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>
                                                    {t('evolver.scriptValidation.usedBy', 'Used by action')}: <strong>{skill.action}</strong>
                                                </div>
                                                {skill.status === 'error' && (
                                                    <div style={{ fontSize: '11px', color: '#ef4444', marginTop: '2px' }}>
                                                        {skill.error}
                                                    </div>
                                                )}
                                            </div>
                                            {skill.status === 'ready' && (
                                                <button
                                                    className="btn btn-primary"
                                                    style={{ fontSize: '11px', padding: '4px 12px', whiteSpace: 'nowrap' }}
                                                    onClick={() => handleImport(i)}
                                                >
                                                    {t('evolver.scriptValidation.importFromPresets', 'Import from Presets')}
                                                </button>
                                            )}
                                            {skill.status === 'importing' && (
                                                <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                                    {t('common.loading', 'Importing...')}
                                                </span>
                                            )}
                                            {skill.status === 'imported' && (
                                                <span style={{
                                                    fontSize: '11px', color: '#4ade80', fontWeight: 500,
                                                }}>
                                                    {t('evolver.scriptValidation.imported', 'Imported')}
                                                </span>
                                            )}
                                            {skill.status === 'not-found' && (
                                                <span style={{
                                                    fontSize: '11px', color: 'var(--text-tertiary)',
                                                    fontStyle: 'italic',
                                                }}>
                                                    {t('evolver.scriptValidation.notInPresets', 'Not found in presets')}
                                                </span>
                                            )}
                                            {skill.status === 'error' && (
                                                <button
                                                    className="btn"
                                                    style={{ fontSize: '11px', padding: '4px 10px', whiteSpace: 'nowrap' }}
                                                    onClick={() => handleImport(i)}
                                                >
                                                    {t('common.retry', 'Retry')}
                                                </button>
                                            )}
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    )}

                    {missingTools.length > 0 && (
                        <div>
                            <h5 style={{
                                margin: '0 0 10px', fontSize: '13px', fontWeight: 600,
                                color: '#ef4444', display: 'flex', alignItems: 'center', gap: '6px',
                            }}>
                                🔧 {t('evolver.scriptValidation.missingTools', 'Missing Tools')} ({missingTools.length})
                            </h5>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                                {missingTools.map(tool => (
                                    <div key={tool.tool_name} style={{
                                        display: 'flex', alignItems: 'center', gap: '10px',
                                        padding: '10px 14px', borderRadius: '8px',
                                        background: 'var(--bg-elevated)',
                                        border: '1px solid var(--border-default)',
                                    }}>
                                        <div style={{ flex: 1 }}>
                                            <div style={{
                                                fontSize: '13px', fontWeight: 500,
                                                color: 'var(--text-primary)',
                                            }}>
                                                <code style={{
                                                    fontSize: '12px',
                                                    background: 'rgba(255,255,255,0.06)',
                                                    padding: '1px 6px', borderRadius: '4px',
                                                }}>tool://{tool.tool_name}</code>
                                            </div>
                                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>
                                                {t('evolver.scriptValidation.usedBy', 'Used by action')}: <strong>{tool.action}</strong>
                                            </div>
                                        </div>
                                        <span style={{
                                            fontSize: '11px', color: '#f59e0b', fontStyle: 'italic',
                                        }}>
                                            {t('evolver.scriptValidation.contactAdmin', 'Contact admin to configure')}
                                        </span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </div>

                <div style={{
                    padding: '14px 22px', borderTop: '1px solid var(--border-subtle)',
                    display: 'flex', justifyContent: 'flex-end', gap: '8px',
                }}>
                    <button className="btn" onClick={onClose} style={{ fontSize: '13px', padding: '6px 16px' }}>
                        {t('common.cancel', 'Cancel')}
                    </button>
                    {(allSkillsImported || !hasImportableSkills) && (
                        <button
                            className="btn btn-primary"
                            onClick={onRetry}
                            style={{ fontSize: '13px', padding: '6px 16px' }}
                        >
                            {t('evolver.scriptValidation.retrySave', 'Retry Save')}
                        </button>
                    )}
                </div>
            </div>
        </div>
    );
}
