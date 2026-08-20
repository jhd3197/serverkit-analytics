import { useCallback, useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { BarChart3, FileText, Link2, Monitor, Radio, Globe, Plus } from 'lucide-react';
import { api, PageTopbar, useToast } from 'serverkit-sdk';
import { Button, EmptyState } from './primitives.jsx';

import OverviewTab from './tabs/OverviewTab.jsx';
import PagesTab from './tabs/PagesTab.jsx';
import ReferrersTab from './tabs/ReferrersTab.jsx';
import DevicesTab from './tabs/DevicesTab.jsx';
import RealtimeTab from './tabs/RealtimeTab.jsx';
import SitesTab from './tabs/SitesTab.jsx';
import { useTranslation } from 'serverkit-sdk';

// Styles are injected once by runtime-entry.jsx (styles/analytics.css ?inline).

// Route-driven tabs. plugin.json maps /analytics and /analytics/:tab here, so the
// active tab is read from useParams().tab (undefined => overview).
const TABS = [
    { slug: 'overview', to: '/analytics', labelKey: 'analytics.analyticsPage.overview', label: 'Overview', end: true, icon: <BarChart3 size={15} /> },
    { slug: 'pages', to: '/analytics/pages', labelKey: 'analytics.analyticsPage.pages', label: 'Pages', icon: <FileText size={15} /> },
    { slug: 'referrers', to: '/analytics/referrers', labelKey: 'analytics.analyticsPage.referrers', label: 'Referrers', icon: <Link2 size={15} /> },
    { slug: 'devices', to: '/analytics/devices', labelKey: 'analytics.analyticsPage.devices', label: 'Devices', icon: <Monitor size={15} /> },
    { slug: 'realtime', to: '/analytics/realtime', labelKey: 'analytics.analyticsPage.realtime', label: 'Realtime', icon: <Radio size={15} /> },
    { slug: 'sites', to: '/analytics/sites', labelKey: 'analytics.analyticsPage.sites', label: 'Sites', icon: <Globe size={15} /> },
];
const VALID_TABS = TABS.map((t) => t.slug);
const REPORT_TABS = ['overview', 'pages', 'referrers', 'devices', 'realtime'];

export function AnalyticsPage() {
    const { t } = useTranslation();
    const toast = useToast();
    const navigate = useNavigate();
    const { tab } = useParams();
    const activeTab = VALID_TABS.includes(tab) ? tab : 'overview';

    const [sites, setSites] = useState([]);
    const [sitesLoading, setSitesLoading] = useState(true);
    const [selectedSiteId, setSelectedSiteId] = useState(null);

    const loadSites = useCallback(async () => {
        setSitesLoading(true);
        try {
            const data = await api.request('/analytics/sites');
            setSites(data?.sites || []);
        } catch (error) {
            toast.error(t('analytics.analyticsPage.couldNotLoadSites', 'Could not load sites: {{message}}', { message: error.message }));
            setSites([]);
        } finally {
            setSitesLoading(false);
        }
    }, [toast]);

    useEffect(() => { loadSites(); }, [loadSites]);

    // Keep a valid selection: default to the first site, and re-point if the
    // current selection disappears (e.g. after a delete).
    useEffect(() => {
        if (sites.length === 0) { setSelectedSiteId(null); return; }
        setSelectedSiteId((prev) => {
            if (prev != null && sites.some((s) => String(s.id) === String(prev))) return prev;
            return String(sites[0].id);
        });
    }, [sites]);

    const isReport = REPORT_TABS.includes(activeTab);

    const renderReportBody = () => {
        if (sitesLoading || (sites.length > 0 && selectedSiteId == null)) {
            return <EmptyState loading title={t('analytics.analyticsPage.loadingAnalytics', 'Loading analytics…')} />;
        }
        if (sites.length === 0) {
            return (
                <div className="analytics-empty">
                    <EmptyState
                        icon={Globe}
                        title={t('analytics.analyticsPage.noTrackedSitesYet', 'No tracked sites yet')}
                        description={t('analytics.analyticsPage.addASiteToGenerateA', 'Add a site to generate a tracking snippet and start collecting privacy-first analytics.')}
                        action={(
                            <Button variant="default" size="sm" onClick={() => navigate('/analytics/sites')}>
                                <Plus size={14} /> {t('analytics.analyticsPage.addASite', 'Add a site')}
                            </Button>
                        )}
                    />
                </div>
            );
        }
        // Remount on site change so each tab resets its own range/loading state.
        switch (activeTab) {
            case 'pages': return <PagesTab key={selectedSiteId} siteId={selectedSiteId} />;
            case 'referrers': return <ReferrersTab key={selectedSiteId} siteId={selectedSiteId} />;
            case 'devices': return <DevicesTab key={selectedSiteId} siteId={selectedSiteId} />;
            case 'realtime': return <RealtimeTab key={selectedSiteId} siteId={selectedSiteId} />;
            default: return <OverviewTab key={selectedSiteId} siteId={selectedSiteId} />;
        }
    };

    // Report tabs get a site picker in the topbar; the Sites tab manages its own.
    const topbarActions = isReport && sites.length > 0 ? (
        <select
            className="analytics-site-select"
            value={selectedSiteId ?? ''}
            onChange={(e) => setSelectedSiteId(e.target.value)}
            aria-label={t('analytics.analyticsPage.selectedSite', 'Selected site')}
        >
            {sites.map((s) => (
                <option key={s.id} value={String(s.id)}>{s.name}</option>
            ))}
        </select>
    ) : null;

    return (
        <div className="page-container page-container--full-bleed sk-tabgroup analytics-page">
            <PageTopbar
                icon={<BarChart3 size={18} />}
                title={t('analytics.analyticsPage.analytics', 'Analytics')}
                tabs={TABS}
                actions={topbarActions}
            />

            <div className="sk-tabgroup__content">
                <div className="sk-tabgroup__inner">
                    {activeTab === 'sites'
                        ? <SitesTab sites={sites} loading={sitesLoading} reload={loadSites} />
                        : renderReportBody()}
                </div>
            </div>
        </div>
    );
}
