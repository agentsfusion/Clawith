import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { agentApi } from '../services/api';
import { useAuthStore } from '../stores';
import MarkdownRenderer from '../components/MarkdownRenderer';
import { IconSend, IconPlus, IconCode, IconCopy, IconCheck, IconTrash, IconMessage } from '@tabler/icons-react';

interface ToolCall {
    name: string;
    args: any;
    result?: string;
}

interface Message {
    role: 'user' | 'assistant';
    content: string;
    toolCalls?: ToolCall[];
    thinking?: string;
    timestamp?: string;
    _isToolGroup?: boolean;
}

interface SessionItem {
    id: string;
    title: string;
    created_at: string;
    last_message_at?: string;
    message_count: number;
}

function extractAscriptBlocks(text: string): string[] {
    const blocks: string[] = [];
    const re = /```ascript\s*\r?\n([\s\S]*?)```/g;
    let m;
    while ((m = re.exec(text)) !== null) {
        blocks.push(m[1].trim());
    }
    return blocks;
}

function extractPartialAscript(text: string): string | null {
    const full = extractAscriptBlocks(text);
    if (full.length > 0) return full[full.length - 1];
    const partial = text.match(/```ascript\s*\r?\n([\s\S]*)$/);
    if (partial) return partial[1];
    return null;
}

function AscriptCodePanel({ code, version, agentName }: { code: string; version?: number; agentName?: string }) {
    const [copied, setCopied] = useState(false);
    const codeRef = useRef<HTMLPreElement>(null);

    const handleCopy = useCallback(() => {
        navigator.clipboard.writeText(code).then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        });
    }, [code]);

    useEffect(() => {
        if (codeRef.current) {
            codeRef.current.scrollTop = codeRef.current.scrollHeight;
        }
    }, [code]);

    return (
        <div className="factory-code-panel">
            <div className="factory-code-header">
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <IconCode size={16} stroke={1.5} />
                    <span style={{ fontWeight: 600, fontSize: '13px' }}>Agent Script</span>
                    {agentName && (
                        <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginLeft: '4px' }}>
                            {agentName}
                        </span>
                    )}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    {version && (
                        <span style={{ fontSize: '10px', color: 'var(--text-tertiary)', background: 'rgba(99,102,241,0.1)', padding: '2px 6px', borderRadius: '4px' }}>
                            v{version}
                        </span>
                    )}
                    <button type="button" className="factory-code-copy" onClick={handleCopy} title="Copy">
                        {copied ? <IconCheck size={14} stroke={1.75} /> : <IconCopy size={14} stroke={1.75} />}
                    </button>
                </div>
            </div>
            <pre ref={codeRef} className="factory-code-content">
                <code>{code}</code>
            </pre>
        </div>
    );
}

export default function AgentFactory() {
    const { t, i18n } = useTranslation();
    const isChinese = i18n.language?.startsWith('zh');
    const token = useAuthStore((s) => s.token);
    const currentTenant = localStorage.getItem('current_tenant_id') || '';
    const queryClient = useQueryClient();

    const [factoryAgentId, setFactoryAgentId] = useState<string | null>(null);
    const [initializing, setInitializing] = useState(true);
    const [sessions, setSessions] = useState<SessionItem[]>([]);
    const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
    const [messages, setMessages] = useState<Message[]>([]);
    const [input, setInput] = useState('');
    const [connected, setConnected] = useState(false);
    const [streaming, setStreaming] = useState(false);
    const [isWaiting, setIsWaiting] = useState(false);
    const [ascriptCode, setAscriptCode] = useState<string | null>(null);
    const [ascriptMeta, setAscriptMeta] = useState<{ version?: number; agent_name?: string } | null>(null);

    const wsRef = useRef<WebSocket | null>(null);
    const activeSessionRef = useRef<string | null>(null);
    const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const chatMessagesRef = useRef<HTMLDivElement>(null);
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const pendingToolCalls = useRef<ToolCall[]>([]);
    const streamContent = useRef('');
    const thinkingContent = useRef('');
    const [userScrolledUp, setUserScrolledUp] = useState(false);

    const { data: agents = [] } = useQuery({
        queryKey: ['agents', currentTenant],
        queryFn: () => agentApi.list(currentTenant || undefined),
    });

    useEffect(() => {
        if (!agents.length) return;
        const factoryNames = ['agent factory', 'clawevolver factory', 'clawevolver agent factory'];
        const existing = agents.find((a: any) =>
            factoryNames.includes((a.name || '').toLowerCase())
            || (a.role_description || '').toLowerCase().includes('agent factory')
        );
        if (existing) {
            setFactoryAgentId(existing.id);
            setInitializing(false);
        } else {
            (async () => {
                try {
                    const templates = await agentApi.templates();
                    const factoryTmpl = templates.find((t: any) => (t.name || '').toLowerCase() === 'agent factory');
                    const createData: any = {
                        name: 'Agent Factory',
                        role_description: 'Agent Factory — Creates Agent Script-powered digital employees through natural conversation',
                        personality: '',
                        boundaries: '',
                        tenant_id: currentTenant || undefined,
                    };
                    if (factoryTmpl) createData.template_id = factoryTmpl.id;
                    const newAgent = await agentApi.create(createData);
                    setFactoryAgentId(newAgent.id);
                    queryClient.invalidateQueries({ queryKey: ['agents'] });
                } catch (err) {
                    console.error('Failed to create Factory Agent:', err);
                } finally {
                    setInitializing(false);
                }
            })();
        }
    }, [agents, currentTenant, queryClient]);

    const loadSessions = useCallback(async () => {
        if (!factoryAgentId) return;
        try {
            const list = await agentApi.sessions(factoryAgentId);
            setSessions(list.sort((a: any, b: any) => {
                const ta = a.last_message_at || a.created_at;
                const tb = b.last_message_at || b.created_at;
                return new Date(tb).getTime() - new Date(ta).getTime();
            }));
        } catch { /* ignore */ }
    }, [factoryAgentId]);

    useEffect(() => { loadSessions(); }, [loadSessions]);

    const loadSessionMessages = useCallback(async (sessionId: string) => {
        if (!factoryAgentId) return;
        try {
            const history = await agentApi.sessionMessages(factoryAgentId, sessionId);
            if (activeSessionRef.current !== sessionId) return;
            const processed: Message[] = [];
            for (const h of history) {
                if (h.role === 'tool_call') {
                    const tc: ToolCall = {
                        name: h.toolName || h.tool_name || '',
                        args: h.toolArgs || h.tool_args || {},
                        result: h.toolResult || h.tool_result || '',
                    };
                    const last = processed[processed.length - 1];
                    if (last && last._isToolGroup) {
                        last.toolCalls = [...(last.toolCalls || []), tc];
                    } else if (last && last.role === 'assistant' && !(last.content && last.content.trim())) {
                        last._isToolGroup = true;
                        last.toolCalls = [...(last.toolCalls || []), tc];
                    } else {
                        processed.push({ role: 'assistant', content: '', toolCalls: [tc], timestamp: h.created_at || undefined, _isToolGroup: true });
                    }
                } else {
                    const msg: Message = { role: h.role, content: h.content, thinking: h.thinking };
                    msg.timestamp = h.created_at || undefined;
                    processed.push(msg);
                }
            }
            setMessages(processed);
            setAscriptCode(null);
            setAscriptMeta(null);
            for (let j = processed.length - 1; j >= 0; j--) {
                if (processed[j].role === 'assistant') {
                    const code = extractPartialAscript(processed[j].content);
                    if (code) { setAscriptCode(code); break; }
                }
            }
        } catch {
            setMessages([]);
            setAscriptCode(null);
            setAscriptMeta(null);
        }
    }, [factoryAgentId]);

    const connectWs = useCallback((sessionId: string) => {
        if (!factoryAgentId || !token) return;
        if (reconnectTimerRef.current) { clearTimeout(reconnectTimerRef.current); reconnectTimerRef.current = null; }
        if (wsRef.current) {
            wsRef.current.close();
            wsRef.current = null;
        }
        setConnected(false);
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/chat/${factoryAgentId}?token=${token}&session_id=${sessionId}`;
        const ws = new WebSocket(wsUrl);

        ws.onopen = () => {
            if (activeSessionRef.current !== sessionId) { ws.close(); return; }
            setConnected(true);
            wsRef.current = ws;
            setTimeout(() => textareaRef.current?.focus(), 50);
        };
        ws.onclose = () => {
            if (wsRef.current !== ws) return;
            setConnected(false);
            if (activeSessionRef.current === sessionId) {
                reconnectTimerRef.current = setTimeout(() => connectWs(sessionId), 3000);
            }
        };
        ws.onerror = () => {
            if (wsRef.current !== ws) return;
            setConnected(false);
        };
        ws.onmessage = (event) => {
            if (wsRef.current !== ws) return;
            let data: any;
            try { data = JSON.parse(event.data); } catch { return; }
            if (['thinking', 'chunk', 'tool_call', 'done', 'error', 'quota_exceeded'].includes(data.type)) {
                setIsWaiting(false);
            }
            if (['error', 'quota_exceeded'].includes(data.type)) {
                setStreaming(false);
            }
            if (data.type === 'connected') return;

            if (data.type === 'thinking') {
                thinkingContent.current += data.content;
                setMessages(prev => {
                    const last = prev[prev.length - 1];
                    if (last && last.role === 'assistant') {
                        const updated = [...prev];
                        updated[updated.length - 1] = { ...last, thinking: thinkingContent.current };
                        return updated;
                    }
                    return [...prev, { role: 'assistant', content: '', thinking: thinkingContent.current, timestamp: new Date().toISOString() }];
                });
            } else if (data.type === 'chunk') {
                streamContent.current += data.content;
                const extracted = extractPartialAscript(streamContent.current);
                if (extracted) setAscriptCode(extracted);
                setMessages(prev => {
                    const last = prev[prev.length - 1];
                    if (last && last.role === 'assistant') {
                        const updated = [...prev];
                        updated[updated.length - 1] = { ...last, content: streamContent.current };
                        return updated;
                    }
                    return [...prev, { role: 'assistant', content: streamContent.current, timestamp: new Date().toISOString() }];
                });
            } else if (data.type === 'tool_call') {
                if (data.status === 'running') {
                    const tc: ToolCall = { name: data.name, args: data.args || {} };
                    pendingToolCalls.current.push(tc);
                    const now = new Date().toISOString();
                    setMessages(prev => {
                        let msgs = [...prev];
                        while (msgs.length > 0) {
                            const last = msgs[msgs.length - 1];
                            if (last.role === 'assistant' && !last._isToolGroup && !(last.content && last.content.trim())) { msgs.pop(); } else break;
                        }
                        for (let i = msgs.length - 1; i >= Math.max(0, msgs.length - 5); i--) {
                            if (msgs[i].role === 'user') break;
                            if (msgs[i]._isToolGroup) {
                                msgs[i] = { ...msgs[i], toolCalls: [...(msgs[i].toolCalls || []), tc], timestamp: now };
                                return msgs;
                            }
                        }
                        return [...msgs, { role: 'assistant', content: '', toolCalls: [tc], timestamp: now, _isToolGroup: true }];
                    });
                } else if (data.status === 'done') {
                    streamContent.current = '';
                    thinkingContent.current = '';
                    const newCall: ToolCall = { name: data.name, args: data.args, result: data.result || '' };
                    const idx = pendingToolCalls.current.findIndex(tc => tc.name === data.name && !tc.result);
                    if (idx >= 0) pendingToolCalls.current[idx] = newCall;
                    else pendingToolCalls.current.push(newCall);
                    const now = new Date().toISOString();
                    setMessages(prev => {
                        let msgs = [...prev];
                        while (msgs.length > 0) {
                            const last = msgs[msgs.length - 1];
                            if (last.role === 'assistant' && !last._isToolGroup && !(last.content && last.content.trim())) { msgs.pop(); } else break;
                        }
                        for (let i = msgs.length - 1; i >= Math.max(0, msgs.length - 5); i--) {
                            if (msgs[i].role === 'user') break;
                            if (msgs[i]._isToolGroup) {
                                const existing = (msgs[i].toolCalls || []).map(tc => tc.name === data.name && !tc.result ? newCall : tc);
                                const hasIt = existing.some(tc => tc.name === data.name && tc.result);
                                msgs[i] = { ...msgs[i], toolCalls: hasIt ? existing : [...existing, newCall], timestamp: now };
                                return msgs;
                            }
                        }
                        return [...msgs, { role: 'assistant', content: '', toolCalls: [newCall], timestamp: now, _isToolGroup: true }];
                    });
                }
            } else if (data.type === 'done') {
                const toolCalls = pendingToolCalls.current.length > 0 ? [...pendingToolCalls.current] : undefined;
                const thinking = thinkingContent.current || undefined;
                pendingToolCalls.current = [];
                streamContent.current = '';
                thinkingContent.current = '';
                setStreaming(false);
                const finalCode = extractPartialAscript(data.content);
                if (finalCode) setAscriptCode(finalCode);
                if (data.ascript_saved) {
                    setAscriptMeta({ version: data.ascript_saved.version, agent_name: data.ascript_saved.agent_name });
                }
                setMessages(prev => {
                    const updated = [...prev];
                    if (updated.length > 0 && updated[updated.length - 1].role === 'assistant') {
                        updated[updated.length - 1] = { role: 'assistant', content: data.content, toolCalls, thinking };
                    } else {
                        updated.push({ role: 'assistant', content: data.content, toolCalls, thinking });
                    }
                    return updated;
                });
                loadSessions();
            } else if (data.type === 'error' || data.type === 'quota_exceeded') {
                const errorMsg = data.type === 'quota_exceeded'
                    ? (data.content || 'Usage quota exceeded. Please try again later.')
                    : (data.content || 'An error occurred.');
                setStreaming(false);
                setMessages(prev => [...prev, { role: 'assistant', content: errorMsg, timestamp: new Date().toISOString() }]);
            }
        };
    }, [factoryAgentId, token, loadSessions]);

    const selectSession = useCallback(async (sessionId: string) => {
        activeSessionRef.current = sessionId;
        setActiveSessionId(sessionId);
        setMessages([]);
        setAscriptCode(null);
        setAscriptMeta(null);
        setStreaming(false);
        setIsWaiting(false);
        await loadSessionMessages(sessionId);
        connectWs(sessionId);
    }, [loadSessionMessages, connectWs]);

    const createNewSession = useCallback(async () => {
        if (!factoryAgentId) return;
        try {
            const session = await agentApi.createSession(factoryAgentId, isChinese ? '新建对话' : 'New conversation');
            await loadSessions();
            selectSession(session.id);
        } catch (err) {
            console.error('Failed to create session:', err);
        }
    }, [factoryAgentId, loadSessions, selectSession, isChinese]);

    const deleteSession = useCallback(async (sessionId: string, e: React.MouseEvent) => {
        e.stopPropagation();
        if (!factoryAgentId) return;
        try {
            await agentApi.deleteSession(factoryAgentId, sessionId);
            if (activeSessionId === sessionId) {
                activeSessionRef.current = null;
                setActiveSessionId(null);
                setMessages([]);
                setAscriptCode(null);
                setAscriptMeta(null);
                if (reconnectTimerRef.current) { clearTimeout(reconnectTimerRef.current); reconnectTimerRef.current = null; }
                if (wsRef.current) { wsRef.current.close(); wsRef.current = null; }
            }
            await loadSessions();
        } catch { /* ignore */ }
    }, [factoryAgentId, activeSessionId, loadSessions]);

    useEffect(() => {
        if (factoryAgentId && sessions.length > 0 && !activeSessionId) {
            selectSession(sessions[0].id);
        }
    }, [factoryAgentId, sessions, activeSessionId, selectSession]);

    useEffect(() => {
        return () => {
            if (reconnectTimerRef.current) { clearTimeout(reconnectTimerRef.current); reconnectTimerRef.current = null; }
            if (wsRef.current) { wsRef.current.close(); wsRef.current = null; }
            activeSessionRef.current = null;
        };
    }, []);

    const handleChatScroll = useCallback(() => {
        const el = chatMessagesRef.current;
        if (!el) return;
        const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
        setUserScrolledUp(distanceFromBottom > 80);
    }, []);

    useEffect(() => {
        if (!userScrolledUp) messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [messages, userScrolledUp]);

    const sendMessage = () => {
        if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
        if (!input.trim()) return;
        pendingToolCalls.current = [];
        streamContent.current = '';
        thinkingContent.current = '';
        setIsWaiting(true);
        setStreaming(true);
        const userMsg = input.trim();
        setMessages(prev => [...prev, { role: 'user', content: userMsg, timestamp: new Date().toISOString() }]);
        wsRef.current.send(JSON.stringify({ content: userMsg, display_content: userMsg, file_name: '' }));
        setInput('');
    };

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing && !isWaiting && !streaming) {
            e.preventDefault();
            sendMessage();
        }
    };

    if (initializing) {
        return (
            <div className="factory-loading">
                <div className="factory-loading-spinner" />
                <span>{isChinese ? '正在初始化 Agent Factory...' : 'Initializing Agent Factory...'}</span>
            </div>
        );
    }

    if (!factoryAgentId) {
        return (
            <div className="factory-loading">
                <span style={{ color: 'var(--text-tertiary)' }}>
                    {isChinese ? 'Agent Factory 创建失败，请刷新重试。' : 'Failed to initialize Agent Factory. Please refresh.'}
                </span>
            </div>
        );
    }

    const hasCodePanel = !!ascriptCode;

    return (
        <div className="factory-layout">
            <div className="factory-sidebar">
                <div className="factory-sidebar-header">
                    <span className="factory-sidebar-title">
                        <IconMessage size={16} stroke={1.5} />
                        {isChinese ? '对话' : 'Sessions'}
                    </span>
                    <button className="factory-new-btn" onClick={createNewSession} title={isChinese ? '新建对话' : 'New session'}>
                        <IconPlus size={16} stroke={2} />
                    </button>
                </div>
                <div className="factory-session-list">
                    {sessions.length === 0 && (
                        <div className="factory-session-empty">
                            {isChinese ? '暂无对话，点击 + 开始创建' : 'No sessions yet. Click + to start.'}
                        </div>
                    )}
                    {sessions.map(s => (
                        <div
                            key={s.id}
                            className={`factory-session-item ${activeSessionId === s.id ? 'active' : ''}`}
                            onClick={() => selectSession(s.id)}
                        >
                            <div className="factory-session-item-content">
                                <div className="factory-session-title">{s.title}</div>
                                <div className="factory-session-meta">
                                    {s.message_count > 0 && <span>{s.message_count} msgs</span>}
                                    <span>{new Date(s.last_message_at || s.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</span>
                                </div>
                            </div>
                            <button
                                className="factory-session-delete"
                                onClick={(e) => deleteSession(s.id, e)}
                                title={isChinese ? '删除' : 'Delete'}
                            >
                                <IconTrash size={13} stroke={1.5} />
                            </button>
                        </div>
                    ))}
                </div>
            </div>

            <div className="factory-chat">
                {!activeSessionId ? (
                    <div className="factory-welcome">
                        <div className="factory-welcome-icon">
                            <svg width="32" height="32" viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                                <path d="M4 28V16l6-4v4l6-4v4l6-4v12" />
                                <rect x="4" y="28" width="24" height="0" />
                                <path d="M28 28V8l-4-4h-2l-4 4" />
                                <line x1="4" y1="28" x2="28" y2="28" />
                                <circle cx="11" cy="22" r="1.5" fill="currentColor" stroke="none" />
                                <circle cx="17" cy="22" r="1.5" fill="currentColor" stroke="none" />
                            </svg>
                        </div>
                        <h2>{isChinese ? 'Agent Factory' : 'Agent Factory'}</h2>
                        <p>
                            {isChinese
                                ? '通过自然语言对话，创建基于 Agent Script 的智能体。点击左侧 + 按钮开始新的创建对话。'
                                : 'Create Agent Script-powered digital employees through natural conversation. Click + on the left to start a new session.'}
                        </p>
                        <button className="btn btn-primary" onClick={createNewSession} style={{ marginTop: '16px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                            <IconPlus size={16} stroke={2} />
                            {isChinese ? '开始创建' : 'Start Creating'}
                        </button>
                    </div>
                ) : (
                    <>
                        <div className="factory-chat-messages" ref={chatMessagesRef} onScroll={handleChatScroll}>
                            {messages.length === 0 && (
                                <div style={{ textAlign: 'center', padding: '60px', color: 'var(--text-tertiary)' }}>
                                    <div style={{ marginBottom: '12px' }}>
                                        <IconMessage size={28} stroke={1.2} />
                                    </div>
                                    <div>{isChinese ? '描述你想创建的智能体' : 'Describe the agent you want to create'}</div>
                                    <div style={{ fontSize: '12px', marginTop: '8px', opacity: 0.7 }}>
                                        {isChinese
                                            ? '例如：帮我创建一个客服 Agent，能处理退款和查询订单'
                                            : 'e.g. Create a customer service agent that handles refunds and order inquiries'}
                                    </div>
                                </div>
                            )}
                            {messages.filter(m => {
                                if (m.role === 'assistant' && !m._isToolGroup && !(m.content && m.content.trim()) && !m.toolCalls?.length && !m.thinking) return false;
                                return true;
                            }).map((msg, i) => (
                                msg._isToolGroup ? (
                                    <div key={i} style={{ marginLeft: '48px', marginBottom: '8px' }}>
                                        {msg.toolCalls && msg.toolCalls.length > 0 && (
                                            <FactoryToolChain toolCalls={msg.toolCalls} />
                                        )}
                                    </div>
                                ) : (
                                    <div key={i} className={`chat-message ${msg.role}`}>
                                        <div className="chat-avatar" style={{ color: 'var(--text-tertiary)' }}>
                                            {msg.role === 'user' ? (
                                                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                                                    <circle cx="8" cy="5.5" r="2.5" />
                                                    <path d="M3 14v-1a4 4 0 018 0v1" />
                                                </svg>
                                            ) : (
                                                <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
                                                    <rect x="3" y="5" width="12" height="10" rx="2" />
                                                    <circle cx="7" cy="10" r="1" fill="currentColor" stroke="none" />
                                                    <circle cx="11" cy="10" r="1" fill="currentColor" stroke="none" />
                                                    <path d="M9 2v3M6 2h6" />
                                                </svg>
                                            )}
                                        </div>
                                        <div className="chat-bubble">
                                            {msg.thinking && (
                                                <details style={{ marginBottom: '8px', fontSize: '12px', background: 'rgba(147, 130, 220, 0.08)', borderRadius: '6px', border: '1px solid rgba(147, 130, 220, 0.15)' }}>
                                                    <summary style={{ padding: '6px 10px', cursor: 'pointer', color: 'rgba(147, 130, 220, 0.9)', fontWeight: 500, userSelect: 'none' }}>Thinking</summary>
                                                    <div style={{ padding: '4px 10px 8px', fontSize: '12px', lineHeight: '1.6', color: 'var(--text-secondary)', whiteSpace: 'pre-wrap', maxHeight: '300px', overflow: 'auto' }}>
                                                        {msg.thinking}
                                                    </div>
                                                </details>
                                            )}
                                            {msg.toolCalls && msg.toolCalls.length > 0 && (
                                                <FactoryToolChain toolCalls={msg.toolCalls} />
                                            )}
                                            {msg.role === 'assistant' ? (
                                                streaming && !msg.content && i === messages.length - 1 ? (
                                                    <div className="thinking-indicator">
                                                        <div className="thinking-dots"><span /><span /><span /></div>
                                                        <span style={{ color: 'var(--text-tertiary)', fontSize: '13px' }}>{isChinese ? '思考中...' : 'Thinking...'}</span>
                                                    </div>
                                                ) : (
                                                    <MarkdownRenderer content={msg.content} />
                                                )
                                            ) : (
                                                <div style={{ whiteSpace: 'pre-wrap' }}>{msg.content}</div>
                                            )}
                                            {msg.timestamp && (
                                                <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginTop: '4px', opacity: 0.7 }}>
                                                    {new Date(msg.timestamp).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                )
                            ))}
                            {(isWaiting || (streaming && (messages.length === 0 || messages[messages.length - 1].role === 'user'))) && (
                                <div className="chat-message assistant">
                                    <div className="chat-avatar" style={{ color: 'var(--text-tertiary)' }}>
                                        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
                                            <rect x="3" y="5" width="12" height="10" rx="2" />
                                            <circle cx="7" cy="10" r="1" fill="currentColor" stroke="none" />
                                            <circle cx="11" cy="10" r="1" fill="currentColor" stroke="none" />
                                            <path d="M9 2v3M6 2h6" />
                                        </svg>
                                    </div>
                                    <div className="chat-bubble">
                                        <div className="thinking-indicator">
                                            <div className="thinking-dots"><span /><span /><span /></div>
                                            <span style={{ color: 'var(--text-tertiary)', fontSize: '13px' }}>{isChinese ? '思考中...' : 'Thinking...'}</span>
                                        </div>
                                    </div>
                                </div>
                            )}
                            <div ref={messagesEndRef} />
                        </div>

                        {userScrolledUp && (
                            <button
                                className="scroll-to-bottom-btn"
                                onClick={() => { setUserScrolledUp(false); messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' }); }}
                                title="Scroll to bottom"
                            >
                                ↓
                            </button>
                        )}

                        <div className="chat-input-area">
                            <div className="chat-composer">
                                <div className="chat-composer-input-block">
                                    <textarea
                                        ref={textareaRef}
                                        className="chat-input"
                                        value={input}
                                        onChange={(e) => setInput(e.target.value)}
                                        onKeyDown={handleKeyDown}
                                        placeholder={isChinese ? '描述你想创建的智能体...' : 'Describe the agent you want to create...'}
                                        disabled={!connected}
                                        rows={1}
                                    />
                                </div>
                                <div className="chat-composer-toolbar">
                                    {(streaming || isWaiting) ? (
                                        <button
                                            type="button"
                                            className="btn btn-stop-generation"
                                            onClick={() => {
                                                if (wsRef.current?.readyState === WebSocket.OPEN) {
                                                    wsRef.current.send(JSON.stringify({ type: 'abort' }));
                                                    setStreaming(false);
                                                    setIsWaiting(false);
                                                }
                                            }}
                                            title={isChinese ? '停止' : 'Stop'}
                                        >
                                            <span className="stop-icon" />
                                        </button>
                                    ) : (
                                        <button
                                            type="button"
                                            className="btn btn-primary chat-composer-send"
                                            onClick={sendMessage}
                                            disabled={!connected || !input.trim()}
                                            title={isChinese ? '发送' : 'Send'}
                                        >
                                            <IconSend size={16} stroke={1.75} />
                                        </button>
                                    )}
                                </div>
                            </div>
                        </div>
                    </>
                )}
            </div>

            {hasCodePanel && (
                <AscriptCodePanel
                    code={ascriptCode!}
                    version={ascriptMeta?.version}
                    agentName={ascriptMeta?.agent_name}
                />
            )}
        </div>
    );
}

function FactoryToolChain({ toolCalls }: { toolCalls: ToolCall[] }) {
    const [expanded, setExpanded] = useState(false);
    const count = toolCalls.length;
    const activeIdx = (() => { for (let i = count - 1; i >= 0; i--) { if (!toolCalls[i].result) return i; } return -1; })();
    const allDone = activeIdx === -1;
    const activeName = activeIdx >= 0 ? toolCalls[activeIdx].name : toolCalls[count - 1]?.name;

    return (
        <div style={{ fontSize: '12px', borderRadius: '8px', border: '1px solid rgba(99,102,241,0.2)', background: 'rgba(99,102,241,0.04)', overflow: 'hidden', marginBottom: '6px' }}>
            <div onClick={() => setExpanded(v => !v)} style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '6px 10px', cursor: 'pointer', userSelect: 'none' }}>
                <span style={{ display: 'inline-block', width: '6px', height: '6px', borderRadius: '50%', background: allDone ? '#22c55e' : '#f59e0b', flexShrink: 0 }} />
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: '#818cf8', fontWeight: 600 }}>{activeName}</span>
                {count > 1 && <span style={{ fontSize: '10px', color: 'var(--text-tertiary)' }}>+{count - 1}</span>}
                <span style={{ marginLeft: 'auto', fontSize: '10px', color: 'var(--text-tertiary)' }}>{expanded ? '▲' : '▼'}</span>
            </div>
            {expanded && (
                <div style={{ borderTop: '1px solid rgba(99,102,241,0.15)' }}>
                    {toolCalls.map((tc, i) => (
                        <div key={i} style={{ padding: '7px 10px', borderBottom: i < count - 1 ? '1px solid rgba(99,102,241,0.10)' : 'none' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '5px', marginBottom: '4px' }}>
                                <span style={{ display: 'inline-block', width: '5px', height: '5px', borderRadius: '50%', background: !tc.result ? '#f59e0b' : '#22c55e', flexShrink: 0 }} />
                                <span style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: '#818cf8', fontWeight: 600 }}>{tc.name}</span>
                            </div>
                            {tc.args && Object.keys(tc.args).length > 0 && (
                                <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)', whiteSpace: 'pre-wrap', wordBreak: 'break-all', maxHeight: '80px', overflowY: 'auto', background: 'rgba(0,0,0,0.12)', borderRadius: '4px', padding: '4px 6px', marginBottom: tc.result ? '4px' : 0 }}>
                                    {JSON.stringify(tc.args, null, 2)}
                                </div>
                            )}
                            {tc.result && (
                                <div style={{ fontSize: '10px', color: 'var(--text-secondary)', whiteSpace: 'pre-wrap', wordBreak: 'break-all', maxHeight: '80px', overflowY: 'auto', borderTop: '1px solid rgba(99,102,241,0.10)', paddingTop: '4px' }}>
                                    {tc.result.length > 500 ? tc.result.slice(0, 500) + '…' : tc.result}
                                </div>
                            )}
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
