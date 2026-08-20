import { useCallback, useEffect, useState } from 'react';
import { Radio, Users, Eye } from 'lucide-react';
import { api, DataTable, useToast } from 'serverkit-sdk';
import { formatInt, formatClock, labelOrDirect } from '../../utils/format.js';
import { useTranslation } from 'serverkit-sdk';

const MINUTE_OPTIONS = [5, 15, 30, 60];
const POLL_MS = 15000;

const columns = [
    { key: 'ts', headerKey: 'analytics.realtimeTab.time', header: 'Time', render: (r) => <span className="analytics-cell-mono">{formatClock(r.ts)}</span> },
    { key: 'path', headerKey: 'analytics.realtimeTab.page', header: 'Page', className: 'analytics-col-grow', render: (r) => (
        <span className="analytics-cell-mono" title={r.path}>{labelOrDirect(r.path)}</span>
    ) },
    { key: 'referrer_host', headerKey: 'analytics.realtimeTab.referrer', header: 'Referrer', render: (r) => labelOrDirect(r.referrer_host) },
    { key: 'device_class', headerKey: 'analytics.realtimeTab.device', header: 'Device', render: (r) => r.device_class || '—' },
    { key: 'country', headerKey: 'analytics.realtimeTab.country', header: 'Country', render: (r) => r.country || '—' },
];

export default function RealtimeTab({ siteId }) {
    const { t } = useTranslation();
    const toast = useToast();
    const [minutes, setMinutes] = useState(30);
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);

    // `silent` polls skip the loading skeleton so the feed refreshes without flicker.
    const load = useCallback(async (silent = false) => {
        if (!silent) setLoading(true);
        try {
            const res = await api.request(`/analytics/sites/${siteId}/realtime?minutes=${minutes}`);
            setData(res);
        } catch (error) {
            if (!silent) {
                toast.error(t('analytics.realtimeTab.couldNotLoadRealtime', 'Could not load realtime: {{message}}', { message: error.message }));
                setData(null);
            }
        } finally {
            if (!silent) setLoading(false);
        }
    }, [siteId, minutes, toast]);

    useEffect(() => {
        load(false);
        const id = setInterval(() => load(true), POLL_MS);
        return () => clearInterval(id);
    }, [load]);

    // Realtime events carry no id; synthesize a stable unique key per row so the
    // table never collides two hits sharing a timestamp.
    const recent = (data?.recent || []).map((r, i) => ({ ...r, _rid: `${r.ts || 'x'}-${i}` }));

    return (
        <div className="analytics-tabbody">
            <div className="analytics-toolbar">
                <div className="analytics-range" role="tablist" aria-label={t('analytics.realtimeTab.timeWindow', 'Time window')}>
                    {MINUTE_OPTIONS.map((m) => (
                        <button
                            key={m}
                            type="button"
                            role="tab"
                            aria-selected={minutes === m}
                            className={`analytics-range__btn${minutes === m ? ' is-active' : ''}`}
                            onClick={() => setMinutes(m)}
                        >
                            {m}m
                        </button>
                    ))}
                </div>
                <span className="analytics-live" title={t('analytics.realtimeTab.autoRefreshing', 'Auto-refreshing')}>
                    <Radio size={14} className="analytics-live__dot" />
                    {t('analytics.realtimeTab.liveLast', 'Live · last')} {data?.minutes ?? minutes} min
                </span>
            </div>

            <div className="analytics-realtime">
                <div className="analytics-realtime__stat">
                    <span className="analytics-realtime__icon analytics-realtime__icon--accent"><Users size={18} /></span>
                    <div className="analytics-realtime__val">{loading ? '—' : formatInt(data?.active_visitors)}</div>
                    <div className="analytics-realtime__label">{t('analytics.realtimeTab.activeVisitors', 'Active visitors')}</div>
                </div>
                <div className="analytics-realtime__stat">
                    <span className="analytics-realtime__icon analytics-realtime__icon--cyan"><Eye size={18} /></span>
                    <div className="analytics-realtime__val">{loading ? '—' : formatInt(data?.pageviews)}</div>
                    <div className="analytics-realtime__label">{t('analytics.realtimeTab.pageviews', 'Pageviews')}</div>
                </div>
            </div>

            <div className="analytics-panel">
                <div className="analytics-panel__head">{t('analytics.realtimeTab.recentActivity', 'Recent activity')}</div>
                <DataTable
                    columns={columns}
                    data={recent}
                    keyField="_rid"
                    loading={loading}
                    emptyTitle="No activity yet"
                    emptyMessage={t('analytics.realtimeTab.hitsFromTheLastFewMinutes', 'Hits from the last few minutes will stream in here.')}
                />
            </div>
        </div>
    );
}
