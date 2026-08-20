import { useCallback, useEffect, useState } from 'react';
import { Monitor, Globe, Cpu, MapPin } from 'lucide-react';
import { api, DataTable, useToast } from 'serverkit-sdk';
import { EmptyState } from '../primitives.jsx';
import RangePicker from '../RangePicker.jsx';
import { formatInt, labelOrDirect } from '../../utils/format.js';
import { useTranslation } from 'serverkit-sdk';

const makeColumns = (header) => [
    { key: 'value', header, className: 'analytics-col-grow', render: (r) => (
        <span title={r.value}>{labelOrDirect(r.value)}</span>
    ) },
    { key: 'visitors', headerKey: 'analytics.devicesTab.visitors', header: 'Visitors', sortable: true, render: (r) => formatInt(r.visitors) },
    { key: 'pageviews', headerKey: 'analytics.devicesTab.views', header: 'Views', sortable: true, render: (r) => formatInt(r.pageviews) },
];

const SECTIONS = [
    { key: 'device', labelKey: 'analytics.devicesTab.deviceClass', label: 'Device class', icon: Monitor, columns: makeColumns('Device') },
    { key: 'browser', labelKey: 'analytics.devicesTab.browser', label: 'Browser', icon: Globe, columns: makeColumns('Browser') },
    { key: 'os', labelKey: 'analytics.devicesTab.operatingSystem', label: 'Operating system', icon: Cpu, columns: makeColumns('OS') },
    { key: 'country', labelKey: 'analytics.devicesTab.country', label: 'Country', icon: MapPin, columns: makeColumns('Country') },
];

export default function DevicesTab({ siteId }) {
    const { t } = useTranslation();
    const toast = useToast();
    const [range, setRange] = useState('7d');
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const res = await api.request(`/analytics/sites/${siteId}/devices?range=${range}`);
            setData(res);
        } catch (error) {
            toast.error(t('analytics.devicesTab.couldNotLoadDevices', 'Could not load devices: {{message}}', { message: error.message }));
            setData(null);
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
            {loading ? (
                <EmptyState loading title={t('analytics.devicesTab.loadingDevices', 'Loading devices…')} />
            ) : (
                <div className="analytics-grid analytics-grid--devices">
                    {SECTIONS.map(({ key, label, icon: Icon, columns }) => (
                        <div className="analytics-panel" key={key}>
                            <div className="analytics-panel__head">
                                <Icon size={15} /> {label}
                            </div>
                            <DataTable
                                columns={columns}
                                data={data?.[key] || []}
                                keyField="value"
                                emptyTitle={`No ${label.toLowerCase()} data`}
                                emptyMessage={t('analytics.devicesTab.nothingRecordedInThisRangeYet', 'Nothing recorded in this range yet.')}
                            />
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
