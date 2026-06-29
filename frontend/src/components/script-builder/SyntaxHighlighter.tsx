import { ReactNode } from 'react';

const KEYWORDS = new Set([
    'config', 'system', 'variables', 'start_agent', 'topic', 'reasoning',
    'actions', 'instructions', 'if', 'else', 'run', 'transition', 'to',
    'set', 'with', 'available', 'when', 'after_reasoning', 'before_reasoning',
]);

const COLORS = {
    comment: '#6b7280',
    promptPrefix: '#6b7280',
    prompt: '#4ade80',
    string: '#4ade80',
    reference: '#60a5fa',
    keyword: '#c084fc',
    key: '#60a5fa',
    symbol: '#6b7280',
    text: 'var(--text-primary)',
};

// Tokenize a single non-comment / non-prompt line into colored spans.
function renderTokenLine(line: string): ReactNode[] {
    const nodes: ReactNode[] = [];
    // Regex captures: 1=string literal, 2=@ref, 3=word, 4=symbol run
    const re = /("[^"]*"|'[^']*')|(@[A-Za-z_][\w.]*)|([A-Za-z_][\w]*)|(:->|->|:|\{|\}|=|!|<|>|\+|\-|,|\(|\))/g;
    let last = 0;
    let m: RegExpExecArray | null;
    let key = 0;
    while ((m = re.exec(line)) !== null) {
        if (m.index > last) {
            nodes.push(<span key={`t${key++}`} style={{ color: COLORS.text }}>{line.slice(last, m.index)}</span>);
        }
        const tok = m[0];
        let color = COLORS.text;
        if (m[1]) {
            color = COLORS.string;
        } else if (m[2]) {
            color = COLORS.reference;
        } else if (m[3]) {
            // key name if immediately followed by ':'
            const after = line[re.lastIndex];
            if (KEYWORDS.has(tok)) {
                color = COLORS.keyword;
            } else if (after === ':') {
                color = COLORS.key;
            } else {
                color = COLORS.text;
            }
        } else if (m[4]) {
            color = COLORS.symbol;
        }
        nodes.push(<span key={`t${key++}`} style={{ color }}>{tok}</span>);
        last = re.lastIndex;
    }
    if (last < line.length) {
        nodes.push(<span key={`t${key++}`} style={{ color: COLORS.text }}>{line.slice(last)}</span>);
    }
    return nodes;
}

export default function SyntaxHighlighter({ code }: { code: string }) {
    if (!code || typeof code !== 'string') {
        return <div className="sb-code-empty">No script yet</div>;
    }
    const lines = code.replace(/\r\n/g, '\n').split('\n');
    return (
        <pre className="sb-code-body" style={{ whiteSpace: 'pre-wrap', overflowX: 'auto' }}>
            {lines.map((line, i) => {
                const trimmed = line.trimStart();
                const key = `l${i}`;
                if (trimmed.startsWith('#')) {
                    return (
                        <div key={key} className="sb-code-line" style={{ color: COLORS.comment, fontStyle: 'italic' }}>
                            {line || ' '}
                        </div>
                    );
                }
                if (trimmed.startsWith('|')) {
                    const idx = line.indexOf('|');
                    const prefix = line.slice(0, idx + 1);
                    const content = line.slice(idx + 1);
                    return (
                        <div key={key} className="sb-code-line">
                            <span style={{ color: COLORS.promptPrefix }}>{prefix}</span>
                            <span style={{ color: COLORS.prompt }}>{content || ' '}</span>
                        </div>
                    );
                }
                return (
                    <div key={key} className="sb-code-line">
                        {line === '' ? ' ' : renderTokenLine(line)}
                    </div>
                );
            })}
        </pre>
    );
}
