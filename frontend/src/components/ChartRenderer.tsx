import {
    ResponsiveContainer, CartesianGrid, XAxis, YAxis, Tooltip, Legend,
    BarChart, Bar, LineChart, Line, AreaChart, Area, PieChart, Pie, Cell,
} from 'recharts';
import { IconChartBar } from '@tabler/icons-react';

const PALETTE = ['#60a5fa', '#34d399', '#fbbf24', '#f472b6', '#a78bfa', '#fb7185', '#22d3ee', '#facc15'];

export interface ChartSpec {
    __chart: true;
    type: 'bar' | 'line' | 'area' | 'pie';
    title: string;
    description?: string;
    xKey: string;
    yKeys: string[];
    data: Array<Record<string, unknown>>;
    truncated?: boolean;
    autoFilledFromLastQuery?: boolean;
}

export function tryParseChartSpec(output?: string): ChartSpec | null {
    if (!output) return null;
    try {
        const p = JSON.parse(output);
        if (p && typeof p === 'object' && p.__chart === true && Array.isArray(p.data)) {
            return p as ChartSpec;
        }
    } catch {
        return null;
    }
    return null;
}

export function ChartRenderer({ spec }: { spec: ChartSpec }) {
    const { type, title, description, xKey, yKeys, data, truncated, autoFilledFromLastQuery } = spec;

    // Coerce stringified numbers to numbers (DB rows often serialize as strings)
    const numericData = data.map(row => {
        const out: Record<string, unknown> = { ...row };
        for (const k of yKeys) {
            const v = out[k];
            if (typeof v === 'string' && v.trim() !== '' && !isNaN(Number(v))) {
                out[k] = Number(v);
            }
        }
        return out;
    });

    const tooltipStyle = {
        background: 'rgba(15,15,18,0.95)',
        border: '1px solid rgba(255,255,255,0.12)',
        borderRadius: 4,
        fontSize: 12,
        color: 'var(--text-primary, #eee)',
    };

    return (
        <div style={{
            border: '1px solid rgba(52,211,153,0.18)',
            background: 'rgba(52,211,153,0.04)',
            borderRadius: 6,
            overflow: 'hidden',
            margin: '4px 0',
        }}>
            <div style={{
                display: 'flex', alignItems: 'center', gap: 6,
                padding: '6px 10px',
                borderBottom: '1px solid rgba(255,255,255,0.06)',
            }}>
                <IconChartBar size={14} style={{ color: 'rgba(52,211,153,0.8)' }} />
                <span style={{ fontSize: 11, fontWeight: 500, color: 'rgba(110,231,183,0.9)' }}>
                    {title}
                </span>
                <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.35)', marginLeft: 'auto' }}>
                    {type} · {data.length} pts
                    {truncated ? ' (truncated)' : ''}
                    {autoFilledFromLastQuery ? ' · auto-filled' : ''}
                </span>
            </div>
            <div style={{ padding: 10, background: 'rgba(0,0,0,0.18)' }}>
                <div style={{ width: '100%', height: 260 }}>
                    <ResponsiveContainer>
                        {type === 'bar' ? (
                            <BarChart data={numericData}>
                                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                                <XAxis dataKey={xKey} stroke="rgba(255,255,255,0.4)" fontSize={11} />
                                <YAxis stroke="rgba(255,255,255,0.4)" fontSize={11} />
                                <Tooltip contentStyle={tooltipStyle} />
                                <Legend wrapperStyle={{ fontSize: 11 }} />
                                {yKeys.map((k, i) => (
                                    <Bar key={k} dataKey={k} fill={PALETTE[i % PALETTE.length]} />
                                ))}
                            </BarChart>
                        ) : type === 'line' ? (
                            <LineChart data={numericData}>
                                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                                <XAxis dataKey={xKey} stroke="rgba(255,255,255,0.4)" fontSize={11} />
                                <YAxis stroke="rgba(255,255,255,0.4)" fontSize={11} />
                                <Tooltip contentStyle={tooltipStyle} />
                                <Legend wrapperStyle={{ fontSize: 11 }} />
                                {yKeys.map((k, i) => (
                                    <Line key={k} type="monotone" dataKey={k}
                                          stroke={PALETTE[i % PALETTE.length]} dot={false} strokeWidth={2} />
                                ))}
                            </LineChart>
                        ) : type === 'area' ? (
                            <AreaChart data={numericData}>
                                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                                <XAxis dataKey={xKey} stroke="rgba(255,255,255,0.4)" fontSize={11} />
                                <YAxis stroke="rgba(255,255,255,0.4)" fontSize={11} />
                                <Tooltip contentStyle={tooltipStyle} />
                                <Legend wrapperStyle={{ fontSize: 11 }} />
                                {yKeys.map((k, i) => (
                                    <Area key={k} type="monotone" dataKey={k}
                                          stroke={PALETTE[i % PALETTE.length]}
                                          fill={PALETTE[i % PALETTE.length] + '55'} />
                                ))}
                            </AreaChart>
                        ) : (
                            <PieChart>
                                <Tooltip contentStyle={tooltipStyle} />
                                <Legend wrapperStyle={{ fontSize: 11 }} />
                                <Pie
                                    data={numericData}
                                    dataKey={yKeys[0]}
                                    nameKey={xKey}
                                    outerRadius={90}
                                    // eslint-disable-next-line @typescript-eslint/no-explicit-any
                                    label={(props: any) => {
                                        const name = props?.name ?? props?.payload?.[xKey] ?? '';
                                        const pct = typeof props?.percent === 'number'
                                            ? (props.percent * 100).toFixed(0)
                                            : '0';
                                        return `${name} ${pct}%`;
                                    }}
                                >
                                    {numericData.map((_, i) => (
                                        <Cell key={i} fill={PALETTE[i % PALETTE.length]} />
                                    ))}
                                </Pie>
                            </PieChart>
                        )}
                    </ResponsiveContainer>
                </div>
                {description && (
                    <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.5)', marginTop: 8 }}>
                        {description}
                    </div>
                )}
            </div>
        </div>
    );
}
