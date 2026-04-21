interface SyntaxHighlighterProps {
  code: string;
  className?: string;
}

const KEYWORDS = [
  "config", "system", "variables", "start_agent", "topic", "reasoning",
  "actions", "instructions", "if", "else", "run", "transition", "to",
  "set", "with", "available", "when", "after_reasoning", "before_reasoning"
];

export function SyntaxHighlighter({ code, className }: SyntaxHighlighterProps) {
  if (!code) return <pre className={className}><code></code></pre>;

  const lines = code.split('\n');

  return (
    <pre className={className} style={{ fontFamily: 'var(--font-mono, monospace)', fontSize: '13px', lineHeight: '1.7', whiteSpace: 'pre-wrap', overflowX: 'auto' }}>
      <code>
        {lines.map((line, i) => {
          const isComment = line.trim().startsWith('#');
          const isPrompt = line.trim().startsWith('|');

          if (isComment) {
            return <div key={i} style={{ color: 'var(--text-tertiary)', fontStyle: 'italic' }}>{line}</div>;
          }

          if (isPrompt) {
            const indentMatch = line.match(/^(\s*\|\s*)(.*)$/);
            if (indentMatch) {
              return (
                <div key={i}>
                  <span style={{ color: 'var(--text-tertiary)', opacity: 0.6 }}>{indentMatch[1]}</span>
                  <span style={{ color: '#4ade80' }}>{indentMatch[2]}</span>
                </div>
              );
            }
          }

          const tokens = line.split(/(\s+|@[a-zA-Z0-9_.]+|"{1}[^"]*"{1}|\b[a-zA-Z_]\w*\b|:->|:|!=|==|>=|<=|>|<|!|\{|\})/g).filter(Boolean);

          return (
            <div key={i}>
              {tokens.map((token, j) => {
                if (token.startsWith('"') && token.endsWith('"')) {
                  return <span key={j} style={{ color: '#4ade80' }}>{token}</span>;
                }
                if (token.startsWith('@')) {
                  return <span key={j} style={{ color: 'var(--accent-primary)', fontWeight: 500 }}>{token}</span>;
                }
                if (KEYWORDS.includes(token)) {
                  return <span key={j} style={{ color: 'var(--accent-primary)', fontWeight: 500 }}>{token}</span>;
                }
                if (j < tokens.length - 1 && tokens[j + 1] === ':' && /^[a-zA-Z_]\w*$/.test(token)) {
                  return <span key={j} style={{ color: '#60a5fa' }}>{token}</span>;
                }
                if ([':', '->', ':->', '{!', '}'].includes(token)) {
                  return <span key={j} style={{ color: 'var(--text-tertiary)', opacity: 0.8 }}>{token}</span>;
                }
                return <span key={j} style={{ color: 'var(--text-primary)', opacity: 0.9 }}>{token}</span>;
              })}
            </div>
          );
        })}
      </code>
    </pre>
  );
}
