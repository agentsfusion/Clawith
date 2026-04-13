import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { agentApi } from '../services/api';

const FactoryIcons = {
    factory: (
        <svg width="32" height="32" viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 28V16l6-4v4l6-4v4l6-4v12" />
            <rect x="4" y="28" width="24" height="0" />
            <path d="M28 28V8l-4-4h-2l-4 4" />
            <line x1="4" y1="28" x2="28" y2="28" />
            <circle cx="11" cy="22" r="1.5" fill="currentColor" stroke="none" />
            <circle cx="17" cy="22" r="1.5" fill="currentColor" stroke="none" />
        </svg>
    ),
    script: (
        <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
            <path d="M6 3h10a2 2 0 012 2v10a2 2 0 01-2 2H8a2 2 0 01-2-2V3z" />
            <path d="M6 3a2 2 0 00-2 2v9a3 3 0 003 3" />
            <line x1="9" y1="8" x2="15" y2="8" />
            <line x1="9" y1="11" x2="13" y2="11" />
        </svg>
    ),
    chat: (
        <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 4h14a1 1 0 011 1v8a1 1 0 01-1 1h-4l-3 3v-3H3a1 1 0 01-1-1V5a1 1 0 011-1z" />
            <line x1="6" y1="8" x2="14" y2="8" />
            <line x1="6" y1="11" x2="11" y2="11" />
        </svg>
    ),
    evolve: (
        <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
            <path d="M10 2v3M10 15v3" />
            <circle cx="10" cy="10" r="4" />
            <path d="M3.5 5.5l2 2M14.5 14.5l2 2" />
            <path d="M2 10h3M15 10h3" />
            <path d="M3.5 14.5l2-2M14.5 5.5l2-2" />
        </svg>
    ),
    tools: (
        <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12.5 3.5a3.5 3.5 0 00-4.8 4.8L3 13l1 1 1 1 4.7-4.7a3.5 3.5 0 004.8-4.8l-2.5 2.5-2-2 2.5-2.5z" />
        </svg>
    ),
    arrow: (
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 8h10M9 4l4 4-4 4" />
        </svg>
    ),
    sparkle: (
        <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
            <path d="M10 2l1.5 4.5L16 8l-4.5 1.5L10 14l-1.5-4.5L4 8l4.5-1.5L10 2z" />
            <path d="M15 13l.75 2.25L18 16l-2.25.75L15 19l-.75-2.25L12 16l2.25-.75L15 13z" />
        </svg>
    ),
};

const FEATURES = [
    { icon: FactoryIcons.chat, key: 'conversational' },
    { icon: FactoryIcons.script, key: 'agentScript' },
    { icon: FactoryIcons.tools, key: 'autoTools' },
    { icon: FactoryIcons.evolve, key: 'selfEvolve' },
];

const STEPS = [
    { num: '1', key: 'describe' },
    { num: '2', key: 'design' },
    { num: '3', key: 'review' },
    { num: '4', key: 'deploy' },
];

export default function AgentFactory() {
    const { t, i18n } = useTranslation();
    const navigate = useNavigate();
    const isChinese = i18n.language?.startsWith('zh');
    const currentTenant = localStorage.getItem('current_tenant_id') || '';
    const [hoveredFeature, setHoveredFeature] = useState<number | null>(null);

    const { data: agents = [] } = useQuery({
        queryKey: ['agents', currentTenant],
        queryFn: () => agentApi.list(currentTenant || undefined),
    });

    const ascriptAgents = agents.filter((a: any) => a.agent_type === 'ascript');

    const handleStartFactory = () => {
        const factoryNames = ['Agent Factory', 'ClawEvolver Factory', 'ClawEvolver Agent Factory'];
        const factoryAgent = agents.find((a: any) =>
            factoryNames.some(n => (a.name || '').toLowerCase() === n.toLowerCase())
            || a.role_description?.toLowerCase().includes('agent factory')
        );
        if (factoryAgent) {
            navigate(`/agents/${factoryAgent.id}/chat`);
        } else {
            navigate('/agents/new');
        }
    };

    return (
        <div style={{ maxWidth: '960px', margin: '0 auto' }}>
            <div style={{
                display: 'flex', flexDirection: 'column', alignItems: 'center',
                textAlign: 'center', marginBottom: '48px', paddingTop: '12px',
            }}>
                <div style={{
                    width: '64px', height: '64px', borderRadius: '16px',
                    background: 'linear-gradient(135deg, var(--primary), var(--accent-primary))',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    color: '#fff', marginBottom: '20px',
                    boxShadow: '0 4px 24px rgba(var(--primary-rgb, 99,102,241), 0.25)',
                }}>
                    {FactoryIcons.factory}
                </div>
                <h1 style={{
                    fontSize: '28px', fontWeight: 700, margin: 0, marginBottom: '8px',
                    letterSpacing: '-0.03em', color: 'var(--text-primary)',
                }}>
                    {isChinese ? 'Agent Factory' : 'Agent Factory'}
                </h1>
                <p style={{
                    fontSize: '15px', color: 'var(--text-secondary)', margin: 0,
                    maxWidth: '520px', lineHeight: '1.6',
                }}>
                    {isChinese
                        ? '通过自然语言对话，创建基于 Agent Script 的智能体。它们拥有结构化的行为逻辑、可自主进化，并能使用你已安装的所有工具和技能。'
                        : 'Create Agent Script-powered digital employees through natural conversation. They have structured behavior logic, self-evolution capabilities, and access to all your installed tools and skills.'}
                </p>
            </div>

            <div style={{
                display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '12px',
                marginBottom: '40px',
            }}>
                {FEATURES.map((f, i) => (
                    <div
                        key={f.key}
                        onMouseEnter={() => setHoveredFeature(i)}
                        onMouseLeave={() => setHoveredFeature(null)}
                        style={{
                            padding: '20px',
                            background: hoveredFeature === i ? 'var(--bg-hover)' : 'var(--bg-secondary)',
                            border: '1px solid var(--border-subtle)',
                            borderRadius: 'var(--radius-lg)',
                            transition: 'all 150ms ease',
                            cursor: 'default',
                        }}
                    >
                        <div style={{
                            display: 'flex', alignItems: 'center', gap: '10px',
                            marginBottom: '8px', color: 'var(--primary)',
                        }}>
                            {f.icon}
                            <span style={{
                                fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)',
                            }}>
                                {isChinese ? ({
                                    conversational: '对话式创建',
                                    agentScript: 'Agent Script 驱动',
                                    autoTools: '自动工具绑定',
                                    selfEvolve: '自主进化',
                                } as Record<string, string>)[f.key] : ({
                                    conversational: 'Conversational Creation',
                                    agentScript: 'Agent Script Powered',
                                    autoTools: 'Auto Tool Binding',
                                    selfEvolve: 'Self-Evolution',
                                } as Record<string, string>)[f.key]}
                            </span>
                        </div>
                        <p style={{
                            fontSize: '13px', color: 'var(--text-tertiary)',
                            margin: 0, lineHeight: '1.5',
                        }}>
                            {isChinese ? ({
                                conversational: '无需编码或配置，用自然语言描述你想要的智能体，Factory 会完成所有设计。',
                                agentScript: '生成结构化 Agent Script，定义话题路由、推理逻辑和行为约束，比自由提示更精确。',
                                autoTools: '根据 Agent Script 中的行为需求，自动绑定你已安装的搜索、文档、通讯等工具。',
                                selfEvolve: '内置进化引擎，Agent 根据用户反馈和质量评估自动优化自身行为脚本。',
                            } as Record<string, string>)[f.key] : ({
                                conversational: 'No coding or configuration needed. Describe the agent you want in natural language and Factory handles the rest.',
                                agentScript: 'Generates structured Agent Scripts with topic routing, reasoning logic, and behavior constraints — more precise than free-form prompts.',
                                autoTools: 'Automatically binds your installed tools (search, docs, messaging) based on the behavior needs defined in the Agent Script.',
                                selfEvolve: 'Built-in evolution engine lets agents automatically improve their behavior scripts based on user feedback and quality assessments.',
                            } as Record<string, string>)[f.key]}
                        </p>
                    </div>
                ))}
            </div>

            <div style={{
                padding: '28px 32px',
                background: 'var(--bg-secondary)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius-lg)',
                marginBottom: '40px',
            }}>
                <h3 style={{
                    fontSize: '15px', fontWeight: 600, margin: 0, marginBottom: '20px',
                    color: 'var(--text-primary)',
                }}>
                    {isChinese ? '创建流程' : 'How It Works'}
                </h3>
                <div style={{
                    display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '16px',
                }}>
                    {STEPS.map((s) => (
                        <div key={s.key} style={{ textAlign: 'center' }}>
                            <div style={{
                                width: '36px', height: '36px', borderRadius: '50%',
                                background: 'var(--bg-tertiary)', border: '1px solid var(--border-subtle)',
                                display: 'flex', alignItems: 'center', justifyContent: 'center',
                                margin: '0 auto 10px', fontSize: '14px', fontWeight: 700,
                                color: 'var(--primary)',
                            }}>
                                {s.num}
                            </div>
                            <div style={{
                                fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)',
                                marginBottom: '4px',
                            }}>
                                {isChinese ? ({
                                    describe: '描述需求',
                                    design: 'AI 设计脚本',
                                    review: '确认方案',
                                    deploy: '一键部署',
                                } as Record<string, string>)[s.key] : ({
                                    describe: 'Describe Needs',
                                    design: 'AI Designs Script',
                                    review: 'Review & Confirm',
                                    deploy: 'Deploy Agent',
                                } as Record<string, string>)[s.key]}
                            </div>
                            <div style={{
                                fontSize: '12px', color: 'var(--text-tertiary)', lineHeight: '1.4',
                            }}>
                                {isChinese ? ({
                                    describe: '用自然语言告诉 Factory 你需要什么样的智能体',
                                    design: 'Factory 自动查询可用工具，设计 Agent Script',
                                    review: '预览脚本结构和行为逻辑，按需调整',
                                    deploy: '确认后自动创建 Agent 并绑定所有工具',
                                } as Record<string, string>)[s.key] : ({
                                    describe: 'Tell Factory what kind of agent you need',
                                    design: 'Factory queries available tools and designs the script',
                                    review: 'Preview script structure and behavior, adjust as needed',
                                    deploy: 'Confirm to auto-create the agent with all tools bound',
                                } as Record<string, string>)[s.key]}
                            </div>
                        </div>
                    ))}
                </div>
            </div>

            <div style={{
                display: 'flex', justifyContent: 'center', gap: '12px',
                marginBottom: '48px',
            }}>
                <button
                    className="btn btn-primary"
                    onClick={handleStartFactory}
                    style={{
                        display: 'flex', alignItems: 'center', gap: '8px',
                        padding: '12px 28px', fontSize: '15px', fontWeight: 600,
                    }}
                >
                    {FactoryIcons.sparkle}
                    {isChinese ? '开始创建' : 'Start Creating'}
                    {FactoryIcons.arrow}
                </button>
            </div>

            {ascriptAgents.length > 0 && (
                <div style={{
                    padding: '24px',
                    background: 'var(--bg-secondary)',
                    border: '1px solid var(--border-subtle)',
                    borderRadius: 'var(--radius-lg)',
                }}>
                    <h3 style={{
                        fontSize: '14px', fontWeight: 600, margin: 0, marginBottom: '16px',
                        color: 'var(--text-primary)',
                        display: 'flex', alignItems: 'center', gap: '8px',
                    }}>
                        {FactoryIcons.script}
                        {isChinese ? 'Agent Script 智能体' : 'Agent Script Agents'}
                        <span style={{
                            fontSize: '12px', fontWeight: 500, color: 'var(--text-tertiary)',
                            background: 'var(--bg-tertiary)', padding: '2px 8px',
                            borderRadius: '10px',
                        }}>
                            {ascriptAgents.length}
                        </span>
                    </h3>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                        {ascriptAgents.map((agent: any) => (
                            <div
                                key={agent.id}
                                onClick={() => navigate(`/agents/${agent.id}`)}
                                style={{
                                    display: 'flex', alignItems: 'center', gap: '12px',
                                    padding: '10px 12px', borderRadius: 'var(--radius-md)',
                                    cursor: 'pointer', transition: 'background 120ms ease',
                                }}
                                onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = 'var(--bg-hover)'; }}
                                onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = 'transparent'; }}
                            >
                                <div style={{
                                    width: '32px', height: '32px', borderRadius: 'var(--radius-md)',
                                    background: 'linear-gradient(135deg, var(--primary), var(--accent-primary))',
                                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                                    color: '#fff', fontSize: '14px', fontWeight: 600, flexShrink: 0,
                                }}>
                                    {(Array.from(agent.name || '?')[0] as string).toUpperCase()}
                                </div>
                                <div style={{ flex: 1, minWidth: 0 }}>
                                    <div style={{
                                        fontSize: '13px', fontWeight: 500, color: 'var(--text-primary)',
                                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                                    }}>
                                        {agent.name}
                                    </div>
                                    <div style={{
                                        fontSize: '12px', color: 'var(--text-tertiary)',
                                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                                    }}>
                                        {agent.role_description || (isChinese ? 'Agent Script 驱动' : 'Agent Script Powered')}
                                    </div>
                                </div>
                                <div style={{
                                    fontSize: '11px', padding: '2px 8px', borderRadius: '10px',
                                    background: agent.status === 'running' ? 'rgba(34,197,94,0.1)' : 'var(--bg-tertiary)',
                                    color: agent.status === 'running' ? 'var(--status-running)' : 'var(--text-tertiary)',
                                    fontWeight: 500,
                                }}>
                                    {agent.status}
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
}
