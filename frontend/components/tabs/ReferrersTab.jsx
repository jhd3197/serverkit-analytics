import { useCallback, useEffect, useState } from 'react';
import { api, DataTable, useToast } from 'serverkit-sdk';
import RangePicker from '../RangePicker.jsx';
import { formatInt, labelOrDirect } from '../../utils/format.js';
import { useTranslation } from 'serverkit-sdk';

const columns = [
    { key: 'value', headerKey: 'analytics.referrersTab.referrer', header: 'Referrer', className: 'analytics-col-grow', render: (r) => (
        <span className="analytics-cell-mono" title={r.value}>{labelOrDirect(r.value)}</span>
    ) },
    { key: 'visitors', headerKey: 'analytics.referrersTab.visitors', header: 'Visitors', sortable: true, render: (r) => formatInt(r.visitors) },
    { key: 'pageviews', headerKey: 'analytics.referrersTab.pageviews', header: 'Pageviews', sortable: true, render: (r) => formatInt(r.pageviews) },
];

export default function ReferrersTab({ siteId }) {
    const { t } = useTranslation();
    const toast = useToast();
    const [range, setRange] = useState('7d');
    const [rows, setRows] = useState([]);
    const [loading, setLoading] = useState(true);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const res = await api.request(`/analytics/sites/${siteId}/referrers?range=${range}`);
            setRows(res?.rows || []);
        } catch (error) {
            toast.error(t('analytics.referrersTab.couldNotLoadReferrers', 'Could not load referrers: {{message}}', { message: error.message }));
            setRows([]);
        } finally {
            setLoading(false);
        }
    }, [siteId, range, toast]);

    useEffect(() => { load(); }, [load]);

    return (
        <div className="analytics-tabbody">
            <div className="analytics-toolbar">
                <RangePicker value={range} onChange={setRange} />
            </div>
            <div className="analytics-panel">
                <DataTable
                    columns={columns}
                    data={rows}
                    keyField="value"
                    loading={loading}
                    defaultSort={{ key: 'pageviews', direction: 'desc' }}
                    emptyTitle="No referrers yet"
                    emptyMessage={t('analytics.referrersTab.sourcesThatSendTrafficToThe', 'Sources that send traffic to the site will appear here.')}
                />
            </div>
        </div>
    );
}
