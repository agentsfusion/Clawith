import { useState, useEffect, useRef } from 'react';
import { useMutation } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { agentApi } from '../services/api';

interface CloneAgentModalProps {
    agentId: string;
    agentName: string;
    open: boolean;
    onClose: () => void;
    onSuccess: (newAgent: any) => void;
}

export default function CloneAgentModal({ agentId, agentName, open, onClose, onSuccess }: CloneAgentModalProps) {
    const { t } = useTranslation();
    const inputRef = useRef<HTMLInputElement>(null);
    const [name, setName] = useState('');
    const [validationError, setValidationError] = useState('');

    useEffect(() => {
        if (open) {
            setName(agentName + t('agent.clone.nameSuffix', ' (Copy)'));
            setValidationError('');
            setTimeout(() => inputRef.current?.focus(), 100);
        }
    }, [open, agentName, t]);

    const validate = (value: string): string => {
        if (!value.trim()) return t('agent.clone.validation.nameRequired');
        if (value.trim().length < 2) return t('agent.clone.validation.nameTooShort');
        if (value.trim().length > 100) return t('agent.clone.validation.nameTooLong');
        return '';
    };

    const cloneMutation = useMutation({
        mutationFn: () => agentApi.clone(agentId, { name: name.trim() }),
        onSuccess: (data) => {
            onSuccess(data);
        },
    });

    const handleSubmit = () => {
        const error = validate(name);
        if (error) {
            setValidationError(error);
            return;
        }
        setValidationError('');
        cloneMutation.mutate();
    };

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Escape') {
            onClose();
        }
        if (e.key === 'Enter' && !cloneMutation.isPending) {
            handleSubmit();
        }
    };

    if (!open) return null;

    return (
        <div
            style={{
                position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
                background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center',
                zIndex: 10000,
            }}
            onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
            onKeyDown={handleKeyDown}
        >
            <div style={{
                background: 'var(--bg-primary)', borderRadius: '12px', padding: '24px',
                width: '400px', maxWidth: '90vw', border: '1px solid var(--border-subtle)',
                boxShadow: '0 20px 60px rgba(0,0,0,0.4)',
            }}
                onClick={e => e.stopPropagation()}
            >
                <h4 style={{ marginBottom: '6px', fontSize: '15px', color: 'var(--text-primary)' }}>
                    {t('agent.clone.title')}
                </h4>
                <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '20px', lineHeight: 1.5 }}>
                    {t('agent.clone.description')}
                </p>

                <div style={{ marginBottom: '16px' }}>
                    <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '6px' }}>
                        {t('agent.clone.nameLabel')}
                    </label>
                    <input
                        ref={inputRef}
                        className="input"
                        style={{ width: '100%', boxSizing: 'border-box' }}
                        value={name}
                        onChange={(e) => {
                            setName(e.target.value);
                            if (validationError) setValidationError('');
                        }}
                        placeholder={t('agent.clone.namePlaceholder')}
                        onKeyDown={handleKeyDown}
                    />
                    {validationError && (
                        <div style={{ fontSize: '12px', color: 'var(--error)', marginTop: '4px' }}>
                            {validationError}
                        </div>
                    )}
                    {cloneMutation.error && (
                        <div style={{ fontSize: '12px', color: 'var(--error)', marginTop: '4px' }}>
                            {t('agent.clone.errorPrefix')}{(cloneMutation.error as any)?.message || 'Unknown error'}
                        </div>
                    )}
                </div>

                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
                    <button className="btn btn-secondary" onClick={onClose} disabled={cloneMutation.isPending}>
                        {t('common.cancel', 'Cancel')}
                    </button>
                    <button
                        className="btn btn-primary"
                        onClick={handleSubmit}
                        disabled={cloneMutation.isPending}
                    >
                        {cloneMutation.isPending ? t('agent.clone.cloning') : t('agent.clone.button')}
                    </button>
                </div>
            </div>
        </div>
    );
}
