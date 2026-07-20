import { useCallback, useEffect, useState } from 'react';
import {
    ResponsiveContainer, AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
} from 'recharts';
import { Users, Eye, TrendingDown, Zap, Radio, BarChart3 } from 'lucide-react';
import { api, DataTable, useToast } from 'serverkit-sdk';
import { EmptyState } from '../primitives.jsx';
import RangePicker from '../RangePicker.jsx';
import { formatInt, formatMs, formatPct, formatDay, labelOrDirect } from '../../utils/format.js';

const KPIS = [
    { key: 'visitors', label: 'Visitors', icon: Users, tone: 'accent', fmt: formatInt },
    { key: 'pageviews', label: 'Pageviews', icon: Eye, tone: 'cyan', fmt: formatInt },
    { key: 'bounce_rate', label: 'Bounce rate', icon: TrendingDown, tone: 'amber', fmt: formatPct },
    { key: 'avg_load_ms', label: 'Avg load', icon: Zap, tone: 'violet', fmt: formatMs },
];

const pageColumns = [
    { key: 'value', header: 'Page', className: 'analytics-col-grow', render: (r) => (
        <span className="analytics-cell-mono" title={r.value}>{labelOrDirect(r.value)}</span>
    ) },
    { key: 'visitors', header: 'Visitors', sortable: true, render: (r) => formatInt(r.visitors) },
    { key: 'pageviews', header: 'Views', sortable: true, render: (r) => formatInt(r.pageviews) },
    { key: 'avg_load_ms', header: 'Avg load', render: (r) => formatMs(r.avg_load_ms) },
];

const referrerColumns = [
    { key: 'value', header: 'Referrer', className: 'analytics-col-grow', render: (r) => (
        <span className="analytics-cell-mono" title={r.value}>{labelOrDirect(r.value)}</span>
    ) },
    { key: 'visitors', header: 'Visitors', sortable: true, render: (r) => formatInt(r.visitors) },
    { key: 'pageviews', header: 'Views', sortable: true, render: (r) => formatInt(r.pageviews) },
];

export default function OverviewTab({ siteId }) {
    const toast = useToast();
    const [range, setRange] = useState('7d');
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const res = await api.request(`/analytics/sites/${siteId}/overview?range=${range}`);
            setData(res);
        } catch (error) {
            toast.error(`Could not load overview: ${error.message}`);
            setData(null);
        } finally {
            setLoading(false);
        }
    }, [siteId, range, toast]);

    useEffect(() => { load(); }, [load]);

    const totals = data?.totals || {};
    const timeseries = data?.timeseries || [];
    const realtime = data?.realtime || {};
    const hasTraffic = (totals.pageviews || 0) > 0 || (totals.visitors || 0) > 0;

    return (
        <div className="analytics-tabbody">
            <div className="analytics-toolbar">
                <RangePicker value={range} onChange={setRange} />
                <span className="analytics-live" title="Active visitors in the last 30 minutes">
                    <Radio size={14} className="analytics-live__dot" />
                    {formatInt(realtime.active_visitors)} active now
                </span>
            </div>

            <div className="analytics-kpis">
                {KPIS.map(({ key, label, icon: Icon, tone, fmt }) => (
                    <div className="analytics-kpi" key={key}>
                        <span className={`analytics-kpi__icon analytics-kpi__icon--${tone}`}>
                            <Icon size={16} />
                        </span>
                        <div className="analytics-kpi__val">{loading ? '—' : fmt(totals[key])}</div>
                        <div className="analytics-kpi__label">{label}</div>
                    </div>
                ))}
            </div>

            {loading ? (
                <EmptyState loading title="Loading overview…" />
            ) : !hasTraffic ? (
                <div className="analytics-empty">
                    <EmptyState
                        icon={BarChart3}
                        title="No traffic in this range yet"
                        description="Once the tracking snippet is installed and visitors arrive, their pageviews show up here."
                    />
                </div>
            ) : (
                <>
                    <div className="analytics-chart-card">
                        <div className="analytics-chart-card__head">Visitors &amp; pageviews</div>
                        <ResponsiveContainer width="100%" height={260}>
                            <AreaChart data={timeseries} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
                                <defs>
                                    <linearGradient id="anVisitors" x1="0" y1="0" x2="0" y2="1">
                                        <stop offset="5%" stopColor="#6d7cff" stopOpacity={0.35} />
                                        <stop offset="95%" stopColor="#6d7cff" stopOpacity={0} />
                                    </linearGradient>
                                    <linearGradient id="anPageviews" x1="0" y1="0" x2="0" y2="1">
                                        <stop offset="5%" stopColor="#22c7d6" stopOpacity={0.3} />
                                        <stop offset="95%" stopColor="#22c7d6" stopOpacity={0} />
                                    </linearGradient>
                                </defs>
                                <CartesianGrid strokeDasharray="3 3" stroke="#888" strokeOpacity={0.15} />
                                <XAxis
                                    dataKey="date"
                                    tickFormatter={formatDay}
                                    tick={{ fontSize: 11, fill: '#888' }}
                                    minTickGap={24}
                                    axisLine={false}
                                    tickLine={false}
                                />
                                <YAxis
                                    allowDecimals={false}
                                    tick={{ fontSize: 11, fill: '#888' }}
                                    width={40}
                                    axisLine={false}
                                    tickLine={false}
                                />
                                <Tooltip labelFormatter={formatDay} />
                                <Area type="monotone" dataKey="visitors" name="Visitors" stroke="#8b93ff" fill="url(#anVisitors)" strokeWidth={2} />
                                <Area type="monotone" dataKey="pageviews" name="Pageviews" stroke="#22c7d6" fill="url(#anPageviews)" strokeWidth={2} />
                            </AreaChart>
                        </ResponsiveContainer>
                    </div>

                    <div className="analytics-grid">
                        <div className="analytics-panel">
                            <div className="analytics-panel__head">Top pages</div>
                            <DataTable
                                columns={pageColumns}
                                data={data?.top_pages || []}
                                keyField="value"
                                emptyTitle="No pages yet"
                                emptyMessage="Pageviews will appear here once visitors browse the site."
                            />
                        </div>
                        <div className="analytics-panel">
                            <div className="analytics-panel__head">Top referrers</div>
                            <DataTable
                                columns={referrerColumns}
                                data={data?.top_referrers || []}
                                keyField="value"
                                emptyTitle="No referrers yet"
                                emptyMessage="Sources that send traffic to the site will appear here."
                            />
                        </div>
                    </div>
                </>
            )}
        </div>
    );
}
