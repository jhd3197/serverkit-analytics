import { useCallback, useEffect, useState } from 'react';
import { api, DataTable, useToast } from 'serverkit-sdk';
import RangePicker from '../RangePicker.jsx';
import { formatInt, formatMs, labelOrDirect } from '../../utils/format.js';

const columns = [
    { key: 'value', header: 'Page', className: 'analytics-col-grow', render: (r) => (
        <span className="analytics-cell-mono" title={r.value}>{labelOrDirect(r.value)}</span>
    ) },
    { key: 'visitors', header: 'Visitors', sortable: true, render: (r) => formatInt(r.visitors) },
    { key: 'pageviews', header: 'Pageviews', sortable: true, render: (r) => formatInt(r.pageviews) },
    { key: 'avg_load_ms', header: 'Avg load', sortable: true, sortValue: (r) => r.avg_load_ms ?? -1, render: (r) => formatMs(r.avg_load_ms) },
];

export default function PagesTab({ siteId }) {
    const toast = useToast();
    const [range, setRange] = useState('7d');
    const [rows, setRows] = useState([]);
    const [loading, setLoading] = useState(true);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const res = await api.request(`/analytics/sites/${siteId}/pages?range=${range}`);
            setRows(res?.rows || []);
        } catch (error) {
            toast.error(`Could not load pages: ${error.message}`);
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
                    emptyTitle="No pages yet"
                    emptyMessage="Pageviews will appear here once visitors browse the site."
                />
            </div>
        </div>
    );
}
