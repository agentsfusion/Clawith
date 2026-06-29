import { useEffect, useRef, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
    IconPlus, IconTrash, IconSend, IconPlayerStop, IconCopy, IconDownload,
    IconChevronsLeft, IconChevronsRight, IconWand, IconRocket, IconTools, IconBolt,
    IconLoader2, IconAlertTriangle, IconCheck, IconX,
} from '@tabler/icons-react';
import { scriptBuilderApi, ScriptConversation, ScriptMessage, ScriptAnalysisResult, ScriptBuilderContext } from '../services/api';
import SyntaxHighlighter from '../components/script-builder/SyntaxHighlighter';
import AnalyzeResult from '../components/script-builder/AnalyzeResult';

const EXAMPLE_PROMPTS = [
    'Build a customer support agent that handles order status and returns',
    'Create an identity verification agent before processing sensitive requests',
    'Design a hotel booking agent with multi-step navigation',
];

// Extract the first ```ascript code block body from a message text.
function extractScript(text: string): string | null {
    if (!text) return null;
    const m = text.match(/```ascript\n([\s\S]*?)```/);
    return m ? m[1] : null;
}

// Render an assistant message: strip the ```ascript block, show remainder.
function AssistantBody({ text }: { text: string }) {
    const script = extractScript(text);
    let body = text;
    if (script !== null) {
        body = text.replace(/```ascript\n[\s\S]*?```/, '').trim();
    }
    return (
        <>
            {body && <div className="sb-prose">{body}</div>}
            {script !== null && <div className="sb-script-badge">Agent Script generated — see code panel →</div>}
        </>
    );
}

export default function ScriptBuilder() {
    const { t } = useTranslation();
    const navigate = useNavigate();

    const [conversations, setConversations] = useState<ScriptConversation[]>([]);
    const [activeId, setActiveId] = useState<number | null>(null);
    const [messages, setMessages] = useState<ScriptMessage[]>([]);
    const [input, setInput] = useState('');
    const [streaming, setStreaming] = useState(false);
    const [streamedContent, setStreamedContent] = useState('');
    const [currentScript, setCurrentScript] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
    const [loadingConvos, setLoadingConvos] = useState(true);
    const [ctx, setCtx] = useState<ScriptBuilderContext | null>(null);
    const [showTools, setShowTools] = useState(true);
    const [showSkills, setShowSkills] = useState(true);

    // Analyze modal
    const [analyzeOpen, setAnalyzeOpen] = useState(false);
    const [analyzing, setAnalyzing] = useState(false);
    const [analyzeResult, setAnalyzeResult] = useState<ScriptAnalysisResult | null>(null);
    const [analyzeError, setAnalyzeError] = useState<string | null>(null);

    // Apply modal
    const [applyOpen, setApplyOpen] = useState(false);
    const [applying, setApplying] = useState(false);
    const [applyResult, setApplyResult] = useState<{ agent_id: string; agent_name: string; installed_tools: string[]; installed_skills: string[] } | null>(null);
    const [applyError, setApplyError] = useState<{ message: string; missing_tools?: string[]; missing_skills?: string[] } | null>(null);
    const [copied, setCopied] = useState(false);

    const abortRef = useRef<AbortController | null>(null);
    const scrollRef = useRef<HTMLDivElement | null>(null);

    const loadConversations = useCallback(async () => {
        setLoadingConvos(true);
        try {
            const list = await scriptBuilderApi.listConversations();
            setConversations(list);
            if (list.length > 0 && activeId === null) {
                setActiveId(list[list.length - 1].id);
            }
        } catch (e: any) {
            setError(e?.message || 'Failed to load conversations');
        } finally {
            setLoadingConvos(false);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const loadMessages = useCallback(async (convId: number) => {
        try {
            const msgs = await scriptBuilderApi.listMessages(convId);
            setMessages(msgs);
            // Seed the code panel from the last assistant message containing a script.
            const lastScript = [...msgs].reverse().find((m) => m.role === 'assistant' && extractScript(m.content));
            setCurrentScript(lastScript ? extractScript(lastScript.content) : null);
        } catch (e: any) {
            setError(e?.message || 'Failed to load messages');
        }
    }, []);

    useEffect(() => {
        loadConversations();
        scriptBuilderApi.getContext().then(setCtx).catch(() => { /* non-fatal */ });
    }, [loadConversations]);

    useEffect(() => {
        if (activeId !== null) {
            setMessages([]);
            setCurrentScript(null);
            setStreamedContent('');
            setError(null);
            loadMessages(activeId);
        }
    }, [activeId, loadMessages]);

    useEffect(() => {
        if (scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
    }, [messages, streamedContent, streaming]);

    const handleCreate = async () => {
        try {
            const c = await scriptBuilderApi.createConversation('New Session');
            setConversations((prev) => [...prev, c]);
            setActiveId(c.id);
        } catch (e: any) {
            setError(e?.message || 'Failed to create session');
        }
    };

    const handleDelete = async (id: number) => {
        try {
            await scriptBuilderApi.deleteConversation(id);
            setConversations((prev) => prev.filter((c) => c.id !== id));
            if (activeId === id) {
                setActiveId(null);
                setMessages([]);
                setCurrentScript(null);
            }
        } catch (e: any) {
            setError(e?.message || 'Failed to delete session');
        }
    };

    const sendMessage = async () => {
        const content = input.trim();
        if (!content || streaming || activeId === null) return;
        setInput('');
        setError(null);
        setStreaming(true);
        setStreamedContent('');

        const userMsg: ScriptMessage = {
            id: Date.now(),
            role: 'user',
            content,
            createdAt: new Date().toISOString(),
        };
        setMessages((prev) => [...prev, userMsg]);

        const controller = new AbortController();
        abortRef.current = controller;
        let full = '';

        try {
            const res = await scriptBuilderApi.streamMessage(activeId, content, controller.signal);
            if (!res.ok || !res.body) {
                const txt = await res.text().catch(() => '');
                throw new Error(txt || `HTTP ${res.status}`);
            }
            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            // eslint-disable-next-line no-constant-condition
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                const parts = buffer.split('\n');
                buffer = parts.pop() || '';
                for (const rawLine of parts) {
                    const line = rawLine.trimStart();
                    if (!line.startsWith('data:')) continue;
                    const payload = line.slice(5).trim();
                    if (!payload) continue;
                    let evt: any;
                    try {
                        evt = JSON.parse(payload);
                    } catch {
                        continue;
                    }
                    if (evt.error) {
                        setError(String(evt.error));
                    } else if (evt.done) {
                        // finish handled below
                    } else if (typeof evt.content === 'string') {
                        full += evt.content;
                        setStreamedContent(full);
                        const script = extractScript(full);
                        if (script !== null) setCurrentScript(script);
                    }
                }
            }
        } catch (e: any) {
            if (e?.name === 'AbortError') {
                // aborted by user — silent
            } else {
                setError(e?.message || 'Streaming failed');
            }
        } finally {
            setStreaming(false);
            setStreamedContent('');
            abortRef.current = null;
            if (activeId !== null) {
                await loadMessages(activeId);
            }
        }
    };

    const handleStop = () => {
        abortRef.current?.abort();
    };

    const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    };

    const runAnalyze = async () => {
        if (!currentScript) return;
        setAnalyzeOpen(true);
        setAnalyzing(true);
        setAnalyzeResult(null);
        setAnalyzeError(null);
        try {
            const r = await scriptBuilderApi.analyze(currentScript);
            setAnalyzeResult(r);
        } catch (e: any) {
            setAnalyzeError(e?.message || 'Failed to analyze script');
        } finally {
            setAnalyzing(false);
        }
    };

    const runApply = async () => {
        if (!currentScript) return;
        setApplyOpen(true);
        setApplying(true);
        setApplyResult(null);
        setApplyError(null);
        try {
            const r = await scriptBuilderApi.applyAsAgent(currentScript);
            setApplyResult(r);
        } catch (e: any) {
            // Backend 400 returns { detail: { message, missing_tools, missing_skills } }
            const detail = e?.detail ?? e?.message ?? 'Failed to apply as agent';
            if (detail && typeof detail === 'object') {
                setApplyError({
                    message: detail.message || 'Cannot create agent — referenced capabilities are missing.',
                    missing_tools: Array.isArray(detail.missing_tools) ? detail.missing_tools : [],
                    missing_skills: Array.isArray(detail.missing_skills) ? detail.missing_skills : [],
                });
            } else {
                setApplyError({ message: String(detail) });
            }
        } finally {
            setApplying(false);
        }
    };

    const handleCopy = async () => {
        if (!currentScript) return;
        try {
            await navigator.clipboard.writeText(currentScript);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
        } catch { /* ignore */ }
    };

    const handleDownload = () => {
        if (!currentScript) return;
        const blob = new Blob([currentScript], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'agent.ascript';
        a.click();
        URL.revokeObjectURL(url);
    };

    const tools = ctx?.tools ?? [];
    const skills = ctx?.skills ?? [];

    return (
        <div className="sb-root">
            {/* ─── Sidebar ─── */}
            <aside className={`sb-sidebar${sidebarCollapsed ? ' collapsed' : ''}`}>
                <div className="sb-sidebar-top">
                    {!sidebarCollapsed && <span className="sb-sidebar-title">{t('scriptBuilder.title', 'Script Builder')}</span>}
                    <button className="sb-icon-btn" onClick={() => setSidebarCollapsed((v) => !v)} title={sidebarCollapsed ? 'Expand' : 'Collapse'}>
                        {sidebarCollapsed ? <IconChevronsRight size={16} stroke={1.7} /> : <IconChevronsLeft size={16} stroke={1.7} />}
                    </button>
                </div>
                {!sidebarCollapsed && (
                    <button className="sb-btn-new" onClick={handleCreate}>
                        <IconPlus size={15} stroke={1.8} /> {t('scriptBuilder.newSession', 'New Session')}
                    </button>
                )}
                <div className="sb-sidebar-list">
                    {loadingConvos && !sidebarCollapsed && <div className="sb-sidebar-empty">Loading…</div>}
                    {!loadingConvos && conversations.length === 0 && !sidebarCollapsed && (
                        <div className="sb-sidebar-empty">{t('scriptBuilder.noSessions', 'No sessions yet')}</div>
                    )}
                    {conversations.map((c) => (
                        <div
                            key={c.id}
                            className={`sb-sidebar-item${c.id === activeId ? ' active' : ''}${sidebarCollapsed ? ' compact' : ''}`}
                            onClick={() => setActiveId(c.id)}
                            title={c.title}
                        >
                            {!sidebarCollapsed && <span className="sb-sidebar-item-label">{c.title}</span>}
                            {!sidebarCollapsed && (
                                <button
                                    className="sb-sidebar-item-delete"
                                    onClick={(e) => { e.stopPropagation(); handleDelete(c.id); }}
                                    title="Delete"
                                >
                                    <IconTrash size={13} stroke={1.7} />
                                </button>
                            )}
                        </div>
                    ))}
                </div>
            </aside>

            {/* ─── Main chat ─── */}
            <main className="sb-main">
                <div className="sb-chat-header">
                    <span className="sb-chat-title">
                        {conversations.find((c) => c.id === activeId)?.title || t('scriptBuilder.title', 'Script Builder')}
                    </span>
                </div>

                <div className="sb-chat-stream" ref={scrollRef}>
                    {messages.length === 0 && !streaming && (
                        <div className="sb-chat-welcome">
                            <div className="sb-chat-welcome-title">{t('scriptBuilder.welcomeTitle', 'Describe the agent you want to build')}</div>
                            <div className="sb-chat-welcome-sub">{t('scriptBuilder.welcomeSub', 'Try one of these to get started:')}</div>
                            <div className="sb-prompt-row">
                                {EXAMPLE_PROMPTS.map((p) => (
                                    <button key={p} className="sb-prompt-btn" onClick={() => setInput(p)}>{p}</button>
                                ))}
                            </div>
                        </div>
                    )}

                    {messages.map((m) => (
                        <div key={m.id} className={`sb-msg ${m.role === 'user' ? 'sb-msg-user' : 'sb-msg-bot'}`}>
                            <div className="sb-msg-avatar">{m.role === 'user' ? 'You' : 'AI'}</div>
                            <div className="sb-msg-bubble">
                                {m.role === 'user' ? <div className="sb-prose">{m.content}</div> : <AssistantBody text={m.content} />}
                            </div>
                        </div>
                    ))}

                    {streaming && (
                        <div className="sb-msg sb-msg-bot">
                            <div className="sb-msg-avatar">AI</div>
                            <div className="sb-msg-bubble">
                                {streamedContent ? <AssistantBody text={streamedContent} /> : (
                                    <div className="sb-typing"><span /><span /><span /></div>
                                )}
                            </div>
                        </div>
                    )}
                </div>

                {error && (
                    <div className="sb-error-banner">
                        <IconAlertTriangle size={15} stroke={1.7} />
                        <span>{error}</span>
                        <button className="sb-icon-btn" onClick={() => setError(null)}><IconX size={14} stroke={1.8} /></button>
                    </div>
                )}

                <div className="sb-chat-input">
                    <textarea
                        className="sb-textarea"
                        value={input}
                        onChange={(e) => setInput(e.target.value)}
                        onKeyDown={onKeyDown}
                        placeholder={t('scriptBuilder.inputPlaceholder', 'Describe the agent you want… (Enter to send, Shift+Enter for newline)')}
                        rows={2}
                        disabled={activeId === null}
                    />
                    {streaming ? (
                        <button className="sb-stop-btn" onClick={handleStop}>
                            <IconPlayerStop size={15} stroke={1.8} /> {t('scriptBuilder.stop', 'Stop')}
                        </button>
                    ) : (
                        <button className="sb-send-btn" onClick={sendMessage} disabled={activeId === null || !input.trim()}>
                            <IconSend size={15} stroke={1.8} /> {t('scriptBuilder.send', 'Send')}
                        </button>
                    )}
                </div>
            </main>

            {/* ─── Code panel ─── */}
            <aside className="sb-code-panel">
                <div className="sb-code-header">
                    <div className="sb-code-dots">
                        <span style={{ background: '#ff5f57' }} /><span style={{ background: '#febc2e' }} /><span style={{ background: '#28c840' }} />
                    </div>
                    <span className="sb-code-title">agent.ascript</span>
                    <div className="sb-code-actions">
                        <button className="sb-code-icon-btn" onClick={handleCopy} disabled={!currentScript} title="Copy">
                            {copied ? <IconCheck size={15} stroke={1.8} /> : <IconCopy size={15} stroke={1.7} />}
                        </button>
                        <button className="sb-code-icon-btn" onClick={handleDownload} disabled={!currentScript} title="Download">
                            <IconDownload size={15} stroke={1.7} />
                        </button>
                    </div>
                </div>

                <div className="sb-code-scroll">
                    {currentScript ? <SyntaxHighlighter code={currentScript} /> : (
                        <div className="sb-code-empty">{t('scriptBuilder.codeEmpty', 'Generated script will appear here')}</div>
                    )}
                </div>

                <div className="sb-code-divider" />

                <div className="sb-code-context">
                    <button className="sb-code-context-head" onClick={() => setShowTools((v) => !v)}>
                        <IconTools size={14} stroke={1.7} /> <span>Tools</span>
                        <span className="sb-code-count">{tools.length}</span>
                    </button>
                    {showTools && (
                        <div className="sb-code-bubbles">
                            {tools.length === 0 && <span className="sb-code-none">None</span>}
                            {tools.map((t2) => (
                                <span key={`t${t2.name}`} className="sb-bubble sb-bubble-tool" title={t2.description}>{t2.display_name || t2.name}</span>
                            ))}
                        </div>
                    )}

                    <button className="sb-code-context-head" onClick={() => setShowSkills((v) => !v)}>
                        <IconBolt size={14} stroke={1.7} /> <span>Skills</span>
                        <span className="sb-code-count">{skills.length}</span>
                    </button>
                    {showSkills && (
                        <div className="sb-code-bubbles">
                            {skills.length === 0 && <span className="sb-code-none">None</span>}
                            {skills.map((s) => (
                                <span key={`s${s.folder_name || s.name}`} className="sb-bubble sb-bubble-skill" title={s.description}>{s.folder_name || s.name}</span>
                            ))}
                        </div>
                    )}
                </div>

                <div className="sb-code-divider" />

                <div className="sb-code-footer">
                    <button className="sb-action-btn sb-action-analyze" onClick={runAnalyze} disabled={!currentScript}>
                        <IconWand size={15} stroke={1.7} /> {t('scriptBuilder.analyze', 'Analyze')}
                    </button>
                    <button className="sb-action-btn sb-action-apply" onClick={runApply} disabled={!currentScript}>
                        <IconRocket size={15} stroke={1.7} /> {t('scriptBuilder.apply', 'Apply As Agent')}
                    </button>
                </div>
            </aside>

            {/* ─── Analyze modal ─── */}
            {analyzeOpen && (
                <div className="sb-modal-overlay" onClick={() => !analyzing && setAnalyzeOpen(false)}>
                    <div className="sb-modal" onClick={(e) => e.stopPropagation()}>
                        <div className="sb-modal-header">
                            <span>{t('scriptBuilder.analyzeTitle', 'Script Analysis')}</span>
                            <button className="sb-modal-close" onClick={() => !analyzing && setAnalyzeOpen(false)}><IconX size={16} stroke={1.8} /></button>
                        </div>
                        <div className="sb-modal-body">
                            {analyzing && (
                                <div className="sb-modal-loading">
                                    <IconLoader2 size={22} stroke={1.8} className="sb-spinner" />
                                    <span>{t('scriptBuilder.analyzing', 'Analyzing…')}</span>
                                </div>
                            )}
                            {analyzeError && <div className="sb-error-banner"><IconAlertTriangle size={15} stroke={1.7} /><span>{analyzeError}</span></div>}
                            {!analyzing && !analyzeError && analyzeResult && <AnalyzeResult result={analyzeResult} />}
                        </div>
                    </div>
                </div>
            )}

            {/* ─── Apply modal ─── */}
            {applyOpen && (
                <div className="sb-modal-overlay" onClick={() => !applying && setApplyOpen(false)}>
                    <div className="sb-modal" onClick={(e) => e.stopPropagation()}>
                        <div className="sb-modal-header">
                            <span>{t('scriptBuilder.applyTitle', 'Apply As Agent')}</span>
                            <button className="sb-modal-close" onClick={() => !applying && setApplyOpen(false)}><IconX size={16} stroke={1.8} /></button>
                        </div>
                        <div className="sb-modal-body">
                            {applying && (
                                <div className="sb-modal-loading">
                                    <IconLoader2 size={22} stroke={1.8} className="sb-spinner" />
                                    <span>{t('scriptBuilder.applying', 'Creating agent…')}</span>
                                </div>
                            )}
                            {!applying && applyError && (
                                <div className="sb-apply-result">
                                    <div className="sb-apply-status sb-apply-fail">
                                        <IconX size={18} stroke={1.8} /> <span>{t('scriptBuilder.applyFailed', 'Cannot create agent')}</span>
                                    </div>
                                    <div className="sb-apply-msg">{applyError.message}</div>
                                    {(applyError.missing_tools?.length || applyError.missing_skills?.length) ? (
                                        <div className="sb-apply-missing">
                                            {applyError.missing_tools?.map((m) => (
                                                <span key={`mt${m}`} className="sb-missing-tag">tool://{m}</span>
                                            ))}
                                            {applyError.missing_skills?.map((m) => (
                                                <span key={`ms${m}`} className="sb-missing-tag">skill://{m}</span>
                                            ))}
                                        </div>
                                    ) : null}
                                </div>
                            )}
                            {!applying && applyResult && (
                                <div className="sb-apply-result">
                                    <div className="sb-apply-status sb-apply-ok">
                                        <IconCheck size={18} stroke={1.8} /> <span>{t('scriptBuilder.applySuccess', 'Agent created successfully')}</span>
                                    </div>
                                    <div className="sb-apply-name">{applyResult.agent_name}</div>
                                    {applyResult.installed_tools.length > 0 && (
                                        <div className="sb-apply-section">
                                            <div className="sb-apply-section-title">Installed Tools</div>
                                            <div className="sb-apply-tags">
                                                {applyResult.installed_tools.map((m) => <span key={`it${m}`} className="sb-bubble sb-bubble-tool">{m}</span>)}
                                            </div>
                                        </div>
                                    )}
                                    {applyResult.installed_skills.length > 0 && (
                                        <div className="sb-apply-section">
                                            <div className="sb-apply-section-title">Installed Skills</div>
                                            <div className="sb-apply-tags">
                                                {applyResult.installed_skills.map((m) => <span key={`is${m}`} className="sb-bubble sb-bubble-skill">{m}</span>)}
                                            </div>
                                        </div>
                                    )}
                                    <button
                                        className="sb-action-btn sb-action-apply"
                                        onClick={() => navigate(`/agents/${applyResult.agent_id}/chat`)}
                                    >
                                        {t('scriptBuilder.openAgent', 'Open Agent Chat')}
                                    </button>
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
