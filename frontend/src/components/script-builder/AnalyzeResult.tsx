interface Dimension {
  name: string;
  score: number;
  feedback: string;
}

interface AnalysisData {
  overallScore: number;
  dimensions: Dimension[];
  strengths: string[];
  suggestions: string[];
}

function getScoreColor(score: number) {
  if (score >= 80) return 'var(--primary)';
  if (score >= 60) return '#facc15';
  return 'var(--danger)';
}

export function AnalyzeResult({ data }: { data: AnalysisData }) {
  const dimensions = Array.isArray(data.dimensions) ? data.dimensions : [];
  const strengths = Array.isArray(data.strengths) ? data.strengths : [];
  const suggestions = Array.isArray(data.suggestions) ? data.suggestions : [];
  const overallScore = typeof data.overallScore === 'number' ? data.overallScore : 0;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '20px', background: 'var(--bg-secondary)', borderRadius: '12px',
        border: '1px solid var(--border-primary)'
      }}>
        <div>
          <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '4px' }}>Overall Quality</div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '6px' }}>
            <span style={{ fontSize: '36px', fontWeight: 700, color: getScoreColor(overallScore) }}>
              {overallScore}
            </span>
            <span style={{ color: 'var(--text-tertiary)' }}>/ 100</span>
          </div>
        </div>
        <div style={{ position: 'relative', width: '72px', height: '72px' }}>
          <svg style={{ width: '100%', height: '100%', transform: 'rotate(-90deg)' }} viewBox="0 0 100 100">
            <circle cx="50" cy="50" r="40" fill="transparent" stroke="var(--border-primary)" strokeWidth="8" />
            <circle
              cx="50" cy="50" r="40" fill="transparent"
              stroke={getScoreColor(overallScore)} strokeWidth="8"
              strokeDasharray="251.2"
              strokeDashoffset={251.2 - (251.2 * overallScore) / 100}
              style={{ transition: 'stroke-dashoffset 1s ease-out' }}
            />
          </svg>
        </div>
      </div>

      <div>
        <div style={{ fontSize: '11px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
          Dimensions
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {dimensions.map((dim, i) => (
            <div key={i}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px', marginBottom: '4px' }}>
                <span style={{ fontWeight: 500, color: 'var(--text-primary)' }}>{dim.name}</span>
                <span style={{ color: 'var(--text-tertiary)' }}>{dim.score}/100</span>
              </div>
              <div style={{ height: '6px', width: '100%', background: 'var(--border-primary)', borderRadius: '3px', overflow: 'hidden' }}>
                <div style={{
                  height: '100%', borderRadius: '3px',
                  background: getScoreColor(dim.score),
                  width: `${dim.score}%`,
                  transition: 'width 0.5s ease'
                }} />
              </div>
              <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px', lineHeight: 1.5 }}>
                {dim.feedback}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
        <div style={{
          padding: '14px', borderRadius: '10px',
          background: 'rgba(74, 222, 128, 0.05)', border: '1px solid rgba(74, 222, 128, 0.15)'
        }}>
          <div style={{ fontSize: '13px', fontWeight: 600, color: '#4ade80', marginBottom: '10px', display: 'flex', alignItems: 'center', gap: '6px' }}>
            ✦ Strengths
          </div>
          <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: '6px' }}>
            {strengths.map((s, i) => (
              <li key={i} style={{ fontSize: '12px', color: 'var(--text-secondary)', display: 'flex', gap: '6px', alignItems: 'flex-start' }}>
                <span style={{ color: '#4ade80', flexShrink: 0, marginTop: '2px' }}>✓</span>
                <span>{s}</span>
              </li>
            ))}
          </ul>
        </div>

        <div style={{
          padding: '14px', borderRadius: '10px',
          background: 'rgba(250, 204, 21, 0.05)', border: '1px solid rgba(250, 204, 21, 0.15)'
        }}>
          <div style={{ fontSize: '13px', fontWeight: 600, color: '#facc15', marginBottom: '10px', display: 'flex', alignItems: 'center', gap: '6px' }}>
            ⚠ Areas to Improve
          </div>
          <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: '6px' }}>
            {suggestions.map((s, i) => (
              <li key={i} style={{ fontSize: '12px', color: 'var(--text-secondary)', display: 'flex', gap: '6px', alignItems: 'flex-start' }}>
                <span style={{ width: '5px', height: '5px', borderRadius: '50%', background: '#facc15', flexShrink: 0, marginTop: '5px' }} />
                <span>{s}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
