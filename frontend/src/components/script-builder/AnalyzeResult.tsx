import { ScriptAnalysisResult } from '../../services/api';

function ringColor(score: number): string {
    if (score >= 80) return 'var(--accent-primary)';
    if (score >= 60) return '#facc15';
    return '#ef4444';
}

function barColor(score: number): string {
    if (score >= 80) return '#4ade80';
    if (score >= 60) return '#facc15';
    return '#ef4444';
}

// SVG circular progress ring.
function ScoreRing({ score }: { score: number }) {
    const size = 96;
    const stroke = 8;
    const r = (size - stroke) / 2;
    const c = 2 * Math.PI * r;
    const offset = c - (Math.max(0, Math.min(100, score)) / 100) * c;
    const color = ringColor(score);
    return (
        <div className="sb-analysis-ring" style={{ width: size, height: size, position: 'relative' }}>
            <svg width={size} height={size} style={{ transform: 'rotate(-90deg)' }}>
                <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--bg-active)" strokeWidth={stroke} />
                <circle
                    cx={size / 2}
                    cy={size / 2}
                    r={r}
                    fill="none"
                    stroke={color}
                    strokeWidth={stroke}
                    strokeDasharray={c}
                    strokeDashoffset={offset}
                    strokeLinecap="round"
                    style={{ transition: 'stroke-dashoffset 0.6s ease' }}
                />
            </svg>
            <span className="sb-analysis-score" style={{ color }}>
                {Math.round(score)}
            </span>
        </div>
    );
}

export default function AnalyzeResult({ result }: { result: ScriptAnalysisResult }) {
    const dims = Array.isArray(result?.dimensions) ? result.dimensions : [];
    const strengths = Array.isArray(result?.strengths) ? result.strengths : [];
    const suggestions = Array.isArray(result?.suggestions) ? result.suggestions : [];
    const overall = typeof result?.overallScore === 'number' ? result.overallScore : 0;

    return (
        <div className="sb-analysis">
            <div className="sb-analysis-overall">
                <ScoreRing score={overall} />
                <div className="sb-analysis-overall-text">
                    <div className="sb-analysis-overall-label">Overall Score</div>
                    <div className="sb-analysis-overall-hint">{overall >= 80 ? 'Excellent' : overall >= 60 ? 'Good — room to improve' : 'Needs work'}</div>
                </div>
            </div>

            <div className="sb-analysis-dimensions">
                {dims.map((d, i) => (
                    <div className="sb-analysis-dim" key={`d${i}`}>
                        <div className="sb-analysis-dim-head">
                            <span className="sb-analysis-dim-name">{d.name}</span>
                            <span className="sb-analysis-dim-score" style={{ color: barColor(d.score) }}>{d.score}</span>
                        </div>
                        <div className="sb-analysis-bar">
                            <div
                                className="sb-analysis-bar-fill"
                                style={{ width: `${Math.max(0, Math.min(100, d.score))}%`, background: barColor(d.score) }}
                            />
                        </div>
                        {d.feedback && <div className="sb-analysis-dim-feedback">{d.feedback}</div>}
                    </div>
                ))}
            </div>

            <div className="sb-analysis-cols">
                <div className="sb-analysis-col">
                    <div className="sb-analysis-col-title">Strengths</div>
                    <ul className="sb-analysis-list sb-analysis-list-strength">
                        {strengths.length === 0 && <li className="sb-analysis-empty">None</li>}
                        {strengths.map((s, i) => (
                            <li key={`s${i}`}>✓ {s}</li>
                        ))}
                    </ul>
                </div>
                <div className="sb-analysis-col">
                    <div className="sb-analysis-col-title">Areas to Improve</div>
                    <ul className="sb-analysis-list sb-analysis-list-suggest">
                        {suggestions.length === 0 && <li className="sb-analysis-empty">None</li>}
                        {suggestions.map((s, i) => (
                            <li key={`g${i}`}><span className="sb-analysis-dot" /> {s}</li>
                        ))}
                    </ul>
                </div>
            </div>
        </div>
    );
}
