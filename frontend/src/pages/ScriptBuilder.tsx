import { useState, useEffect, useRef, useCallback } from 'react';
import {
  IconPlus, IconTrash, IconMessage, IconChevronRight, IconSend,
  IconPlayerStop, IconRobot, IconUser, IconLoader2, IconCode,
  IconCopy, IconCheck, IconDownload, IconActivity, IconChartBar
} from '@tabler/icons-react';
import ReactMarkdown from 'react-markdown';
import { SyntaxHighlighter } from '../components/script-builder/SyntaxHighlighter';
import { AnalyzeResult } from '../components/script-builder/AnalyzeResult';
import {
  scriptBuilderApi,
  type ScriptConversation,
  type ScriptMessage,
  type ScriptAnalysisResult,
} from '../services/api';

function extractScript(text: string): string | null {
  const match = text.match(/```ascript\n([\s\S]*?)```/);
  return match ? match[1].trim() : null;
}

function ChatMessage({ message }: { message: ScriptMessage }) {
  const isUser = message.role === 'user';
  const script = !isUser ? extractScript(message.content) : null;
  const displayContent = script
    ? message.content.replace(/```ascript[\s\S]*?```/g, '').trim()
    : message.content;

  return (
    <div className={`sb-msg ${isUser ? 'sb-msg-user' : 'sb-msg-bot'}`}>
      <div className={`sb-msg-avatar ${isUser ? 'sb-msg-avatar-user' : 'sb-msg-avatar-bot'}`}>
        {isUser ? <IconUser size={16} /> : <IconRobot size={16} />}
      </div>
      <div className={`sb-msg-bubble ${isUser ? 'sb-msg-bubble-user' : 'sb-msg-bubble-bot'}`}>
        {isUser ? (
          <p style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{message.content}</p>
        ) : (
          <div className="sb-prose">
            {displayContent && <ReactMarkdown>{displayContent}</ReactMarkdown>}
            {script && (
              <div className="sb-script-badge">
                <IconCode size={14} />
                <span>Agent Script generated — see code panel →</span>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function StreamingMessage({ content }: { content: string }) {
  const displayContent = content.replace(/```ascript[\s\S]*?```/g, '').trim();
  return (
    <div className="sb-msg sb-msg-bot">
      <div className="sb-msg-avatar sb-msg-avatar-bot">
        <IconRobot size={16} />
      </div>
      <div className="sb-msg-bubble sb-msg-bubble-bot">
        {displayContent ? (
          <div className="sb-prose"><ReactMarkdown>{displayContent}</ReactMarkdown></div>
        ) : (
          <div className="sb-typing">
            <span /><span /><span />
          </div>
        )}
      </div>
    </div>
  );
}

function Sidebar({
  conversations, activeId, onSelect, onCreate, onDelete, loading
}: {
  conversations: ScriptConversation[];
  activeId: number | null;
  onSelect: (id: number) => void;
  onCreate: () => void;
  onDelete: (id: number) => void;
  loading: boolean;
}) {
  return (
    <div className="sb-sidebar">
      <div className="sb-sidebar-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <div className="sb-sidebar-logo">
            <IconCode size={16} />
          </div>
          <span style={{ fontWeight: 600, fontSize: '14px' }}>Script Builder</span>
        </div>
      </div>
      <div style={{ padding: '10px' }}>
        <button className="sb-btn-new" onClick={onCreate} disabled={loading}>
          {loading ? <IconLoader2 size={16} className="spin" /> : <IconPlus size={16} />}
          New Session
        </button>
      </div>
      <div className="sb-sidebar-list">
        {conversations.length === 0 && !loading && (
          <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', textAlign: 'center', padding: '24px 12px' }}>
            No sessions yet. Create one above.
          </p>
        )}
        {conversations.map((c) => (
          <div
            key={c.id}
            className={`sb-sidebar-item ${activeId === c.id ? 'active' : ''}`}
            onClick={() => onSelect(c.id)}
          >
            <IconMessage size={14} style={{ opacity: 0.7, flexShrink: 0 }} />
            <span className="sb-sidebar-item-text">{c.title}</span>
            {activeId === c.id && <IconChevronRight size={14} style={{ opacity: 0.5, flexShrink: 0 }} />}
            <button
              className="sb-sidebar-delete"
              onClick={(e) => { e.stopPropagation(); onDelete(c.id); }}
            >
              <IconTrash size={14} />
            </button>
          </div>
        ))}
      </div>
      <div className="sb-sidebar-footer">
        Powered by Salesforce Agentforce<br />Agent Script Specification
      </div>
    </div>
  );
}

function CodePanel({
  script, onAnalyze, analyzeResult, isAnalyzing
}: {
  script: string | null;
  onAnalyze: () => void;
  analyzeResult: (ScriptAnalysisResult & { error?: string }) | { error: string } | null;
  isAnalyzing: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const [showAnalyze, setShowAnalyze] = useState(false);

  const handleCopy = async () => {
    if (!script) return;
    await navigator.clipboard.writeText(script);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleDownload = () => {
    if (!script) return;
    const blob = new Blob([script], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'agent.ascript';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const handleAnalyze = () => {
    setShowAnalyze(true);
    onAnalyze();
  };

  if (!script) {
    return (
      <div className="sb-code-empty">
        <div className="sb-code-empty-icon">
          <IconChartBar size={32} style={{ opacity: 0.4 }} />
        </div>
        <h3>No Script Generated Yet</h3>
        <p>Describe your agent requirements in the chat, and the AI will generate the Agent Script here automatically.</p>
      </div>
    );
  }

  return (
    <div className="sb-code">
      <div className="sb-code-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <div className="sb-code-dot" />
          <span style={{ fontWeight: 500, fontSize: '13px' }}>Agent Script</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
          <button className="sb-code-action-btn" onClick={handleAnalyze} disabled={isAnalyzing}>
            <IconActivity size={14} />
            {isAnalyzing ? 'Analyzing...' : 'Analyze'}
          </button>
          <div className="sb-code-divider" />
          <button className="sb-code-icon-btn" onClick={handleCopy} title="Copy">
            {copied ? <IconCheck size={16} style={{ color: 'var(--primary)' }} /> : <IconCopy size={16} />}
          </button>
          <button className="sb-code-icon-btn" onClick={handleDownload} title="Download .ascript">
            <IconDownload size={16} />
          </button>
        </div>
      </div>
      <div className="sb-code-body">
        <SyntaxHighlighter code={script} />
      </div>

      {showAnalyze && (
        <div className="sb-modal-overlay" onClick={() => setShowAnalyze(false)}>
          <div className="sb-modal" onClick={(e) => e.stopPropagation()}>
            <div className="sb-modal-header">
              <h3 style={{ margin: 0, fontSize: '16px' }}>Script Analysis</h3>
              <button className="sb-modal-close" onClick={() => setShowAnalyze(false)}>✕</button>
            </div>
            <div className="sb-modal-body">
              {isAnalyzing ? (
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '48px 0', gap: '16px' }}>
                  <div className="sb-spinner" />
                  <p style={{ fontSize: '13px', color: 'var(--text-tertiary)' }}>Running optimizations & checks...</p>
                </div>
              ) : analyzeResult?.error ? (
                <div style={{ padding: '48px', textAlign: 'center', color: 'var(--danger, #ef4444)' }}>
                  {analyzeResult.error}
                </div>
              ) : analyzeResult && 'overallScore' in analyzeResult ? (
                <AnalyzeResult data={analyzeResult} />
              ) : (
                <div style={{ padding: '48px', textAlign: 'center', color: 'var(--text-tertiary)' }}>
                  Failed to load analysis.
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function ScriptBuilder() {
  const [conversations, setConversations] = useState<ScriptConversation[]>([]);
  const [activeConvId, setActiveConvId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ScriptMessage[]>([]);
  const [input, setInput] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamedContent, setStreamedContent] = useState('');
  const [currentScript, setCurrentScript] = useState<string | null>(null);
  const [convLoading, setConvLoading] = useState(true);
  const [analyzeResult, setAnalyzeResult] = useState<(ScriptAnalysisResult & { error?: string }) | { error: string } | null>(null);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const loadConversations = useCallback(async () => {
    try {
      const data = await scriptBuilderApi.listConversations();
      setConversations(data);
    } catch (e) {
      console.error('Failed to load conversations', e);
    } finally {
      setConvLoading(false);
    }
  }, []);

  useEffect(() => { loadConversations(); }, [loadConversations]);

  useEffect(() => {
    if (conversations.length > 0 && !activeConvId) {
      setActiveConvId(conversations[conversations.length - 1].id);
    }
  }, [conversations, activeConvId]);

  const loadMessages = useCallback(async (convId: number) => {
    try {
      const data = await scriptBuilderApi.listMessages(convId);
      setMessages(data);
      const lastScript = [...data].reverse().find(m => m.role === 'assistant' && extractScript(m.content));
      if (lastScript) setCurrentScript(extractScript(lastScript.content));
      else setCurrentScript(null);
    } catch (e) {
      console.error('Failed to load messages', e);
    }
  }, []);

  useEffect(() => {
    if (activeConvId) {
      loadMessages(activeConvId);
      setAnalyzeResult(null);
    } else {
      setMessages([]);
      setCurrentScript(null);
    }
  }, [activeConvId, loadMessages]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamedContent]);

  const handleCreate = async () => {
    const title = `Session ${new Date().toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}`;
    try {
      const conv = await scriptBuilderApi.createConversation(title);
      await loadConversations();
      setActiveConvId(conv.id);
      setCurrentScript(null);
      setMessages([]);
    } catch (e) {
      console.error('Failed to create conversation', e);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this conversation?')) return;
    try {
      await scriptBuilderApi.deleteConversation(id);
      if (activeConvId === id) {
        setActiveConvId(null);
        setCurrentScript(null);
        setMessages([]);
      }
      loadConversations();
    } catch (e) {
      console.error('Failed to delete', e);
    }
  };

  const handleSelect = (id: number) => {
    setActiveConvId(id);
    setCurrentScript(null);
    setStreamedContent('');
  };

  const sendMessage = useCallback(async () => {
    if (!input.trim() || !activeConvId || isStreaming) return;
    const content = input.trim();
    setInput('');
    setIsStreaming(true);
    setStreamedContent('');
    setError(null);

    abortRef.current = new AbortController();

    try {
      const res = await scriptBuilderApi.streamMessage(activeConvId, content, abortRef.current.signal);

      if (!res.ok || !res.body) throw new Error('Stream failed');

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let full = '';
      let buffer = '';
      let streamDone = false;

      while (!streamDone) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6));
              if (data.error) {
                setError(data.error);
                streamDone = true;
                break;
              }
              if (data.done) {
                streamDone = true;
                break;
              }
              if (data.content) {
                full += data.content;
                setStreamedContent(full);
                const newScript = extractScript(full);
                if (newScript) setCurrentScript(newScript);
              }
            } catch {}
          }
        }
      }
    } catch (err) {
      if (err instanceof Error && err.name !== 'AbortError') {
        console.error(err);
        setError('Failed to send message. Please try again.');
      }
    } finally {
      setIsStreaming(false);
      setStreamedContent('');
      abortRef.current = null;
      if (activeConvId) loadMessages(activeConvId);
    }
  }, [input, activeConvId, isStreaming, loadMessages]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const handleAnalyze = async () => {
    if (!currentScript) return;
    setIsAnalyzing(true);
    setAnalyzeResult(null);
    try {
      const data = await scriptBuilderApi.analyze(currentScript);
      if (data && typeof data.overallScore === 'number' && Array.isArray(data.dimensions)) {
        setAnalyzeResult(data);
      } else {
        setAnalyzeResult({ error: 'Unexpected analysis format' });
      }
    } catch (e) {
      console.error('Analyze failed', e);
      setAnalyzeResult({ error: 'Analysis request failed' });
    } finally {
      setIsAnalyzing(false);
    }
  };

  const examplePrompts = [
    'Build a customer support agent that handles order status and returns',
    'Create an identity verification agent before processing sensitive requests',
    'Design a hotel booking agent with multi-step navigation',
  ];

  return (
    <div className="sb-root">
      <Sidebar
        conversations={conversations}
        activeId={activeConvId}
        onSelect={handleSelect}
        onCreate={handleCreate}
        onDelete={handleDelete}
        loading={convLoading}
      />

      <div className="sb-main">
        {!activeConvId ? (
          <div className="sb-empty-chat">
            <div className="sb-empty-icon"><IconMessage size={32} style={{ opacity: 0.4 }} /></div>
            <h2>No conversation selected</h2>
            <p>Create a new session to get started</p>
          </div>
        ) : (
          <div className="sb-chat-wrap">
            <div className="sb-chat-header">
              <IconRobot size={16} style={{ color: 'var(--accent-primary)' }} />
              <span style={{ fontSize: '13px', fontWeight: 500 }}>AI Assistant</span>
              <span style={{ marginLeft: 'auto', fontSize: '11px', color: 'var(--text-tertiary)' }}>Agent Script Expert</span>
            </div>
            <div className="sb-chat-messages">
              {messages.length === 0 && !isStreaming && (
                <div className="sb-chat-welcome">
                  <div className="sb-chat-welcome-icon"><IconRobot size={32} /></div>
                  <h2>ClawEvolver Script Builder</h2>
                  <p>Describe the agent you want to build. I'll ask clarifying questions and generate an optimized Agent Script for you.</p>
                  <div className="sb-prompts">
                    {examplePrompts.map((prompt, i) => (
                      <button key={i} className="sb-prompt-btn" onClick={() => { setInput(prompt); textareaRef.current?.focus(); }}>
                        {prompt}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {messages.map((msg) => (
                <ChatMessage key={msg.id} message={msg} />
              ))}
              {isStreaming && <StreamingMessage content={streamedContent} />}
              {error && (
                <div className="sb-error-banner" onClick={() => setError(null)}>
                  {error}
                </div>
              )}
              <div ref={messagesEndRef} />
            </div>
            <div className="sb-chat-input-area">
              <div className="sb-chat-input-box">
                <textarea
                  ref={textareaRef}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="Describe your agent requirements..."
                  rows={1}
                  disabled={isStreaming}
                  className="sb-textarea"
                  onInput={(e) => {
                    const t = e.target as HTMLTextAreaElement;
                    t.style.height = 'auto';
                    t.style.height = `${t.scrollHeight}px`;
                  }}
                />
                {isStreaming ? (
                  <button className="sb-send-btn sb-stop-btn" onClick={() => abortRef.current?.abort()} title="Stop">
                    <IconPlayerStop size={16} />
                  </button>
                ) : (
                  <button className="sb-send-btn" onClick={sendMessage} disabled={!input.trim()} title="Send (Enter)">
                    <IconSend size={16} />
                  </button>
                )}
              </div>
              <p className="sb-input-hint">
                Press <kbd>Enter</kbd> to send, <kbd>Shift+Enter</kbd> for newline
              </p>
            </div>
          </div>
        )}
      </div>

      <div className="sb-code-panel">
        <CodePanel
          script={currentScript}
          onAnalyze={handleAnalyze}
          analyzeResult={analyzeResult}
          isAnalyzing={isAnalyzing}
        />
      </div>
    </div>
  );
}
