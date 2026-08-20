import { useCallback, useEffect, useState } from 'react';
import { Plus, Trash2, RefreshCw, Copy, Check, Code2, Power, Globe } from 'lucide-react';
import { api, DataTable, Pill, useToast } from 'serverkit-sdk';
import { Button, ConfirmDialog, EmptyState, Input, Label, Modal } from '../primitives.jsx';
import { useClipboard } from '../../hooks/useClipboard.js';
import { useTranslation } from 'serverkit-sdk';

const INITIAL_FORM = { name: '', hostnames: '', allowedOrigins: '', honorDnt: true, enabled: true };

// Split a textarea value on newlines/commas into a clean list of tokens.
const parseList = (raw) => (raw || '').split(/[\n,]/).map((s) => s.trim()).filter(Boolean);

export default function SitesTab({ sites, loading, reload }) {
    const { t } = useTranslation();
    const toast = useToast();
    const { copy, copied } = useClipboard({ successMessage: 'Snippet copied' });

    const [busy, setBusy] = useState(false);
    const [confirm, setConfirm] = useState(null);

    // Add-site modal
    const [addOpen, setAddOpen] = useState(false);
    const [form, setForm] = useState(INITIAL_FORM);

    // Snippet modal
    const [snippetSite, setSnippetSite] = useState(null);
    const [snippetData, setSnippetData] = useState(null);
    const [snippetLoading, setSnippetLoading] = useState(false);
    const [snippetOutlinks, setSnippetOutlinks] = useState(false);

    const fetchSnippet = useCallback(async (site, outlinks) => {
        setSnippetLoading(true);
        try {
            const res = await api.request(`/analytics/sites/${site.id}/snippet?outlinks=${outlinks}`);
            setSnippetData(res);
        } catch (error) {
            toast.error(t('analytics.sitesTab.couldNotLoadSnippet', 'Could not load snippet: {{message}}', { message: error.message }));
            setSnippetData(null);
        } finally {
            setSnippetLoading(false);
        }
    }, [toast]);

    useEffect(() => {
        if (snippetSite) fetchSnippet(snippetSite, snippetOutlinks);
    }, [snippetSite, snippetOutlinks, fetchSnippet]);

    const openAdd = () => { setForm(INITIAL_FORM); setAddOpen(true); };
    const openSnippet = (site) => { setSnippetOutlinks(false); setSnippetSite(site); };
    const closeSnippet = () => { setSnippetSite(null); setSnippetData(null); };

    const handleCreate = async () => {
        const name = form.name.trim();
        const hostnames = parseList(form.hostnames);
        if (!name) { toast.error(t('analytics.sitesTab.nameIsRequired', 'Name is required.')); return; }
        if (hostnames.length === 0) { toast.error(t('analytics.sitesTab.addAtLeastOneHostname', 'Add at least one hostname.')); return; }
        setBusy(true);
        try {
            const site = await api.request('/analytics/sites', {
                method: 'POST',
                body: {
                    name,
                    hostnames,
                    allowed_origins: parseList(form.allowedOrigins),
                    honor_dnt: form.honorDnt,
                    enabled: form.enabled,
                },
            });
            setAddOpen(false);
            toast.success(t('analytics.sitesTab.siteCreated', 'Site "{{name}}" created', { name: site.name }));
            await reload();
            openSnippet(site);
        } catch (error) {
            toast.error(t('analytics.sitesTab.couldNotCreateSite', 'Could not create site: {{message}}', { message: error.message }));
        } finally {
            setBusy(false);
        }
    };

    const handleToggle = async (site) => {
        try {
            await api.request(`/analytics/sites/${site.id}`, { method: 'PUT', body: { enabled: !site.enabled } });
            await reload();
        } catch (error) {
            toast.error(t('analytics.sitesTab.couldNotUpdateSite', 'Could not update site: {{message}}', { message: error.message }));
        }
    };

    const handleRotate = (site) => setConfirm({
        title: `Rotate key for ${site.name}?`,
        messageKey: 'analytics.sitesTab.aNewSiteKeyIsGenerated', message: 'A new site key is generated immediately. The old key stops accepting hits, so update the installed snippet afterward.',
        confirmTextKey: 'analytics.sitesTab.rotateKey', confirmText: 'Rotate key',
        variant: 'warning',
        onConfirm: async () => {
            setConfirm(null);
            setBusy(true);
            try {
                await api.request(`/analytics/sites/${site.id}/rotate-key`, { method: 'POST' });
                toast.success(t('analytics.sitesTab.siteKeyRotated', 'Site key rotated'));
                await reload();
                if (snippetSite && snippetSite.id === site.id) fetchSnippet(site, snippetOutlinks);
            } catch (error) {
                toast.error(t('analytics.sitesTab.couldNotRotateKey', 'Could not rotate key: {{message}}', { message: error.message }));
            } finally {
                setBusy(false);
            }
        },
    });

    const handleDelete = (site) => setConfirm({
        title: `Delete ${site.name}?`,
        messageKey: 'analytics.sitesTab.thisRemovesTheSiteAndAll', message: 'This removes the site and all of its collected analytics. This cannot be undone.',
        confirmTextKey: 'analytics.sitesTab.delete', confirmText: 'Delete',
        variant: 'danger',
        onConfirm: async () => {
            setConfirm(null);
            setBusy(true);
            try {
                await api.request(`/analytics/sites/${site.id}`, { method: 'DELETE' });
                toast.success(t('analytics.sitesTab.siteDeleted', 'Site deleted'));
                if (snippetSite && snippetSite.id === site.id) closeSnippet();
                await reload();
            } catch (error) {
                toast.error(t('analytics.sitesTab.couldNotDeleteSite', 'Could not delete site: {{message}}', { message: error.message }));
            } finally {
                setBusy(false);
            }
        },
    });

    const columns = [
        { key: 'name', headerKey: 'analytics.sitesTab.site', header: 'Site', sortable: true, render: (s) => (
            <div className="analytics-site-name">
                <span className="analytics-site-name__title">{s.name}</span>
                <span className="analytics-cell-mono analytics-site-name__key">{s.site_key}</span>
            </div>
        ) },
        { key: 'hostnames', headerKey: 'analytics.sitesTab.hostnames', header: 'Hostnames', render: (s) => {
            const list = (s.hostnames || []).join(', ');
            return <span className="analytics-cell-mono" title={list}>{list || '—'}</span>;
        } },
        { key: 'enabled', headerKey: 'analytics.sitesTab.status', header: 'Status', render: (s) => (
            s.enabled ? <Pill kind="green">{t('analytics.sitesTab.enabled', 'Enabled')}</Pill> : <Pill kind="gray">{t('analytics.sitesTab.disabled', 'Disabled')}</Pill>
        ) },
        { key: 'actions', header: '', className: 'analytics-col-actions', render: (s) => (
            <div className="analytics-row-actions">
                <Button variant="secondary" size="sm" onClick={() => openSnippet(s)} title={t('analytics.sitesTab.trackingSnippet', 'Tracking snippet')}>
                    <Code2 size={14} />
                </Button>
                <Button variant="secondary" size="sm" onClick={() => handleToggle(s)} title={s.enabled ? t('analytics.sitesTab.disable', 'Disable') : t('analytics.sitesTab.enable', 'Enable')}>
                    <Power size={14} />
                </Button>
                <Button variant="secondary" size="sm" disabled={busy} onClick={() => handleRotate(s)} title={t('analytics.sitesTab.rotateKey2', 'Rotate key')}>
                    <RefreshCw size={14} />
                </Button>
                <Button variant="destructive" size="sm" onClick={() => handleDelete(s)} title={t('analytics.sitesTab.delete2', 'Delete')}>
                    <Trash2 size={14} />
                </Button>
            </div>
        ) },
    ];

    return (
        <div className="analytics-tabbody">
            <div className="analytics-toolbar analytics-toolbar--between">
                <span className="analytics-toolbar__hint">
                    {sites.length} site{sites.length === 1 ? '' : 's'} tracked
                </span>
                <Button variant="default" size="sm" onClick={openAdd}>
                    <Plus size={14} /> {t('analytics.sitesTab.addSite', 'Add site')}
                </Button>
            </div>

            <div className="analytics-panel">
                <DataTable
                    columns={columns}
                    data={sites}
                    keyField="id"
                    loading={loading}
                    emptyState={(
                        <EmptyState
                            icon={Globe}
                            title={t('analytics.sitesTab.noTrackedSitesYet', 'No tracked sites yet')}
                            description={t('analytics.sitesTab.addASiteToGenerateA', 'Add a site to generate a tracking snippet and start collecting privacy-first analytics.')}
                            action={(
                                <Button variant="default" size="sm" onClick={openAdd}>
                                    <Plus size={14} /> {t('analytics.sitesTab.addSite2', 'Add site')}
                                </Button>
                            )}
                        />
                    )}
                />
            </div>

            {/* Add-site modal */}
            <Modal
                open={addOpen}
                onClose={() => setAddOpen(false)}
                title={t('analytics.sitesTab.addASite', 'Add a site')}
                size="lg"
                footer={(
                    <>
                        <Button variant="outline" onClick={() => setAddOpen(false)}>{t('analytics.sitesTab.cancel', 'Cancel')}</Button>
                        <Button variant="default" onClick={handleCreate} disabled={busy || !form.name.trim()}>
                            <Plus size={14} /> {t('analytics.sitesTab.createSite', 'Create site')}
                        </Button>
                    </>
                )}
            >
                <div className="analytics-form">
                    <div className="form-group">
                        <Label>{t('analytics.sitesTab.name', 'Name')}</Label>
                        <Input
                            value={form.name}
                            placeholder={t('analytics.sitesTab.myWebsite', 'My website')}
                            autoFocus
                            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                        />
                    </div>
                    <div className="form-group">
                        <Label>{t('analytics.sitesTab.hostnames2', 'Hostnames')}</Label>
                        <textarea
                            className="analytics-textarea"
                            value={form.hostnames}
                            placeholder={t('analytics.sitesTab.exampleComWwwExampleCom', 'example.com\nwww.example.com')}
                            spellCheck={false}
                            onChange={(e) => setForm((f) => ({ ...f, hostnames: e.target.value }))}
                        />
                        <p className="analytics-form__hint">{t('analytics.sitesTab.onePerLineOrCommaSeparated', 'One per line (or comma-separated). Hits are accepted from these hostnames.')}</p>
                    </div>
                    <div className="form-group">
                        <Label>{t('analytics.sitesTab.allowedOriginsOptional', 'Allowed origins (optional)')}</Label>
                        <textarea
                            className="analytics-textarea"
                            value={form.allowedOrigins}
                            placeholder={'https://example.com'}
                            spellCheck={false}
                            onChange={(e) => setForm((f) => ({ ...f, allowedOrigins: e.target.value }))}
                        />
                        <p className="analytics-form__hint">{t('analytics.sitesTab.restrictTheCollectorToTheseCors', 'Restrict the collector to these CORS origins. Leave empty to allow any.')}</p>
                    </div>
                    <label className="analytics-check">
                        <input
                            type="checkbox"
                            checked={form.honorDnt}
                            onChange={(e) => setForm((f) => ({ ...f, honorDnt: e.target.checked }))}
                        />
                        <span>{t('analytics.sitesTab.honorTheBrowserSDoNot', 'Honor the browser\'s Do Not Track signal')}</span>
                    </label>
                    <label className="analytics-check">
                        <input
                            type="checkbox"
                            checked={form.enabled}
                            onChange={(e) => setForm((f) => ({ ...f, enabled: e.target.checked }))}
                        />
                        <span>{t('analytics.sitesTab.startCollectingImmediately', 'Start collecting immediately')}</span>
                    </label>
                </div>
            </Modal>

            {/* Tracking snippet modal */}
            <Modal
                open={!!snippetSite}
                onClose={closeSnippet}
                title={snippetSite ? t('analytics.sitesTab.trackingSnippet2', 'Tracking snippet · {{name}}', { name: snippetSite.name }) : t('analytics.sitesTab.trackingSnippet3', 'Tracking snippet')}
                size="lg"
                footer={<Button variant="outline" onClick={closeSnippet}>{t('analytics.sitesTab.close', 'Close')}</Button>}
            >
                {snippetLoading ? (
                    <EmptyState loading title={t('analytics.sitesTab.loadingSnippet', 'Loading snippet…')} />
                ) : !snippetData ? (
                    <EmptyState title={t('analytics.sitesTab.snippetUnavailable', 'Snippet unavailable')} description={t('analytics.sitesTab.couldNotLoadTheTrackingSnippet', 'Could not load the tracking snippet for this site.')} />
                ) : (
                    <div className="analytics-snippet-wrap">
                        <p className="analytics-form__hint">
                            {t('analytics.sitesTab.pasteThisIntoTheHeadOf', 'Paste this into the <head> of every page you want to track. It is cookieless and under 4 KB.')}
                        </p>
                        <label className="analytics-check">
                            <input
                                type="checkbox"
                                checked={snippetOutlinks}
                                onChange={(e) => setSnippetOutlinks(e.target.checked)}
                            />
                            <span>{t('analytics.sitesTab.includeOutboundLinkTracking', 'Include outbound-link tracking')}</span>
                        </label>
                        <pre className="analytics-snippet"><code>{snippetData.snippet}</code></pre>
                        <div className="analytics-snippet__foot">
                            <span className="analytics-cell-mono analytics-snippet__key" title={snippetData.tracker_url}>
                                {snippetData.site_key}
                            </span>
                            <Button variant="default" size="sm" onClick={() => copy(snippetData.snippet)}>
                                {copied ? <Check size={14} /> : <Copy size={14} />}
                                {copied ? 'Copied' : 'Copy'}
                            </Button>
                        </div>
                    </div>
                )}
            </Modal>

            {confirm && (
                <ConfirmDialog
                    isOpen
                    title={confirm.title}
                    message={confirm.message}
                    confirmText={confirm.confirmText}
                    variant={confirm.variant}
                    onConfirm={confirm.onConfirm}
                    onCancel={() => setConfirm(null)}
                />
            )}
        </div>
    );
}
