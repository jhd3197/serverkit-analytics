"""serverkit-analytics (Analytics) extension tests.

Moved out of the ServerKit repo along with the extension (the extension now
lives in its own repository and ships as a runtime-ESM bundle). The blueprint
+ collector/ingest/rollup/report services live under backend/ and mount at
/api/v1/analytics via the manifest's entry_point + url_prefix.

``_load_ext()`` registers this repo's backend/ as the dashed package
``app.plugins.serverkit-analytics`` — the same way the panel loads an
installed extension's backend at boot. The models module is imported at module
top so its ``ext_serverkit_analytics_*`` tables register on ``db.metadata``
before the ``app`` fixture runs ``db.create_all()``.

These tests still need the panel's Flask app + fixtures, so run them from a
ServerKit checkout — see tests/README.md.

Nothing here shells out or talks to Docker; the collector, ingestion buffer,
rollups, and log parsing are exercised against the in-memory test DB. Bots,
rate-limits, and visitor hashing are pure-Python and tested directly.
"""
import importlib
import importlib.util
import json
import os
import sys

import pytest

from app.services import plugin_service

SLUG = 'serverkit-analytics'
# realpath so a symlinked copy inside <panel>/backend/tests still resolves back
# to this repository root. SERVERKIT_ANALYTICS_DIR overrides when copied (not
# linked).
EXT_DIR = os.environ.get(
    'SERVERKIT_ANALYTICS_DIR',
    os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
)


def _load_ext():
    """Register this repo's backend/ as ``app.plugins.<slug>`` — mirrors how the
    panel loads an installed extension's backend at boot."""
    backend_dir = os.path.join(EXT_DIR, 'backend')
    assert os.path.isdir(backend_dir), f'extension backend not found at {backend_dir}'
    spec = importlib.util.spec_from_file_location(
        f'app.plugins.{SLUG}',
        os.path.join(backend_dir, '__init__.py'),
        submodule_search_locations=[backend_dir],
    )
    pkg = importlib.util.module_from_spec(spec)
    sys.modules[f'app.plugins.{SLUG}'] = pkg
    spec.loader.exec_module(pkg)
    mods = {}
    for name in ('config', 'models', 'ingest_service', 'rollup_service',
                 'report_service', 'site_service', 'wp_integration',
                 'nginx_integration', 'log_ingest_service', 'geo', 'lifecycle',
                 'jobs', 'analytics'):
        mods[name] = importlib.import_module(f'app.plugins.{SLUG}.{name}')
    return mods


_M = _load_ext()
config_mod = _M['config']
models_mod = _M['models']
ingest_mod = _M['ingest_service']
rollup_mod = _M['rollup_service']
report_mod = _M['report_service']
site_mod = _M['site_service']
wp_mod = _M['wp_integration']
nginx_mod = _M['nginx_integration']
log_mod = _M['log_ingest_service']
geo_mod = _M['geo']
lifecycle_mod = _M['lifecycle']
jobs_mod = _M['jobs']
bp_mod = _M['analytics']

AnalyticsSite = models_mod.AnalyticsSite
AnalyticsEvent = models_mod.AnalyticsEvent
AnalyticsDaily = models_mod.AnalyticsDaily
AnalyticsLogCursor = models_mod.AnalyticsLogCursor

# A realistic desktop UA so happy-path hits aren't bot/empty-UA filtered.
UA_CHROME = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
             '(KHTML, like Gecko) Chrome/120.0 Safari/537.36')


# --------------------------------------------------------------------------- #
# helpers / fixtures
# --------------------------------------------------------------------------- #
def _mk_plugin_row(config=None, status=None):
    from app import db
    from app.models.plugin import InstalledPlugin
    row = InstalledPlugin(
        name=SLUG, display_name='Analytics', slug=SLUG, version='1.0.0',
        status=status or InstalledPlugin.STATUS_ACTIVE,
    )
    row.config = config or {}
    db.session.add(row)
    db.session.commit()
    return row


def _mk_admin():
    from app import db
    from app.models.user import User
    from werkzeug.security import generate_password_hash
    u = User(email='analyticsadmin@t.local', username='analyticsadmin',
             password_hash=generate_password_hash('x'),
             role=User.ROLE_ADMIN, is_active=True)
    db.session.add(u)
    db.session.commit()
    return u


def _mk_viewer():
    from app import db
    from app.models.user import User
    from werkzeug.security import generate_password_hash
    u = User(email='analyticsviewer@t.local', username='analyticsviewer',
             password_hash=generate_password_hash('x'),
             role=User.ROLE_VIEWER, is_active=True)
    db.session.add(u)
    db.session.commit()
    return u


def _auth(user):
    from flask_jwt_extended import create_access_token
    return {'Authorization': f'Bearer {create_access_token(identity=user.id)}'}


@pytest.fixture(autouse=True)
def _clean_ingest_state():
    """Reset the process-global ingest buffer + rate limiter between tests."""
    ingest_mod.reset_buffer()
    ingest_mod.reset_rate_limits()
    yield
    ingest_mod.reset_buffer()
    ingest_mod.reset_rate_limits()


@pytest.fixture
def analytics_client(app):
    if 'analytics' not in app.blueprints:
        app.register_blueprint(bp_mod.analytics_bp, url_prefix='/api/v1/analytics')
    return app.test_client()


def _mk_site(**kwargs):
    from app import db
    kwargs.setdefault('name', 'Test Site')
    kwargs.setdefault('hostnames', 'example.com')
    site = AnalyticsSite(**kwargs)
    db.session.add(site)
    db.session.commit()
    return site


def _collect(client, site_key, path='/home', ua=UA_CHROME, extra_headers=None,
             body=None, **payload):
    headers = {'User-Agent': ua, 'Content-Type': 'text/plain'}
    if extra_headers:
        headers.update(extra_headers)
    if body is None:
        data = {'k': site_key, 'p': path}
        data.update(payload)
        body = json.dumps(data)
    return client.post('/api/v1/analytics/collect', data=body, headers=headers)


# --------------------------------------------------------------------------- #
# manifest
# --------------------------------------------------------------------------- #
def _manifest():
    with open(os.path.join(EXT_DIR, 'plugin.json'), encoding='utf-8') as f:
        return json.load(f)


def test_manifest_passes_validator():
    m = _manifest()
    assert plugin_service._validate_manifest(m) is True
    assert m['name'] == SLUG
    assert m['entry_point'] == 'analytics:analytics_bp'
    assert m['url_prefix'] == '/api/v1/analytics'
    assert m['models'] == 'models:register'
    nav = m['contributions']['nav'][0]
    assert nav['route'] == '/analytics' and nav['id'] == 'analytics'
    assert m['contributions']['page_titles']['/analytics'] == 'Analytics'


def test_manifest_permissions_known_and_no_dashes():
    from app.plugins_sdk import permissions as sdk_perms
    m = _manifest()
    assert sdk_perms.unknown_permissions(m['permissions']) == []
    assert set(m['permissions']) == {'db', 'network', 'filesystem'}
    assert '—' not in m['description'] and '–' not in m['description']


def test_manifest_jobs_and_schedules_pair_up():
    m = _manifest()
    job_kinds = {j['kind'] for j in m['jobs']}
    sched_kinds = {s['kind'] for s in m['schedules']}
    assert sched_kinds <= job_kinds
    assert {'analytics.rollup', 'analytics.retention_prune',
            'analytics.log_tail'} <= job_kinds


def test_lifecycle_and_job_refs_resolve():
    m = _manifest()
    for ref in m['lifecycle'].values():
        module_name, func_name = ref.split(':')
        mod = importlib.import_module(f'app.plugins.{SLUG}.{module_name}')
        assert callable(getattr(mod, func_name, None)), ref
    for job in m['jobs']:
        module_name, func_name = job['handler'].split(':')
        mod = importlib.import_module(f'app.plugins.{SLUG}.{module_name}')
        assert callable(getattr(mod, func_name, None)), job['handler']


def test_entry_point_resolves_to_blueprint():
    assert getattr(bp_mod, 'analytics_bp', None) is not None
    assert bp_mod.analytics_bp.name == 'analytics'


def test_manifest_declares_bridge_and_sdk():
    m = _manifest()
    # sdk_version drives the runtime SDK gate; it must satisfy the panel's SDK.
    from app.utils.sdk import SDK_VERSION, sdk_version_satisfies
    assert m['sdk_version']
    assert sdk_version_satisfies(m['sdk_version'], SDK_VERSION)
    # Runtime-ESM bundle, loaded by the no-rebuild frontend loader.
    assert m['frontend_entry'] == 'dist/index.mjs'
    routes = m['contributions']['routes']
    assert {'path': 'analytics', 'component': 'AnalyticsPage'} in routes
    assert {'path': 'analytics/:tab', 'component': 'AnalyticsPage'} in routes


def test_frontend_exports_route_component():
    with open(os.path.join(EXT_DIR, 'frontend', 'runtime-entry.jsx'), encoding='utf-8') as f:
        src = f.read()
    assert 'AnalyticsPage' in src
    # No default export — PluginLoader auto-renders plugin default exports.
    assert 'export default' not in src


def test_config_schema_keys_match_defaults():
    m = _manifest()
    schema_keys = set(m['config_schema'].keys())
    assert schema_keys == set(config_mod.DEFAULTS.keys())


# --------------------------------------------------------------------------- #
# config accessor
# --------------------------------------------------------------------------- #
def test_get_cfg_returns_defaults_without_row(app):
    cfg = config_mod.get_cfg()
    assert cfg['raw_retention_days'] == 30
    assert cfg['honor_dnt'] is True
    assert cfg['collect_rate_per_min'] == 600


def test_get_cfg_merges_saved_over_defaults(app):
    _mk_plugin_row(config={'raw_retention_days': 7, 'geo_enabled': True})
    cfg = config_mod.get_cfg()
    assert cfg['raw_retention_days'] == 7      # overridden
    assert cfg['geo_enabled'] is True          # overridden
    assert cfg['rollup_retention_months'] == 13  # still default


def test_cfg_int_clamps(app):
    _mk_plugin_row(config={'collect_rate_per_min': 5})
    assert config_mod.cfg_int('collect_rate_per_min', minimum=10) == 10


def test_cfg_bool_accepts_strings(app):
    _mk_plugin_row(config={'honor_dnt': 'false'})
    assert config_mod.cfg_bool('honor_dnt') is False


# --------------------------------------------------------------------------- #
# lifecycle
# --------------------------------------------------------------------------- #
def test_on_install_seeds_config_defaults(app):
    row = _mk_plugin_row(config={})
    lifecycle_mod.on_install(row)
    assert row.config['raw_retention_days'] == 30
    assert row.config['log_ingestion_enabled'] is True


def test_on_install_does_not_clobber_saved(app):
    row = _mk_plugin_row(config={'raw_retention_days': 90})
    lifecycle_mod.on_install(row)
    assert row.config['raw_retention_days'] == 90  # preserved
    assert 'buffer_max' in row.config              # new key seeded


def test_on_uninstall_is_safe_without_integrations(app):
    row = _mk_plugin_row()
    # wp_integration / nginx_integration may not import cleanly yet; must not raise.
    lifecycle_mod.on_uninstall(row, purge=True)


# --------------------------------------------------------------------------- #
# ping route (proves the blueprint mounts; 503 status guard covered separately)
# --------------------------------------------------------------------------- #
def test_ping_route(analytics_client):
    resp = analytics_client.get('/api/v1/analytics/ping')
    assert resp.status_code == 200
    assert resp.get_json()['plugin'] == SLUG


# --------------------------------------------------------------------------- #
# models + purge
# --------------------------------------------------------------------------- #
def test_models_register_and_tables_exist(app):
    from app import db
    from sqlalchemy import inspect
    assert set(models_mod.register(db)) == {
        AnalyticsSite, AnalyticsEvent, AnalyticsDaily, AnalyticsLogCursor}
    tables = [t for t in inspect(db.engine).get_table_names()
              if t.startswith('ext_serverkit_analytics')]
    assert 'ext_serverkit_analytics_sites' in tables
    assert 'ext_serverkit_analytics_events' in tables
    assert 'ext_serverkit_analytics_daily' in tables
    assert 'ext_serverkit_analytics_log_cursors' in tables


def test_purge_drops_only_prefixed_tables(app):
    from app import db
    from app.services import extension_lifecycle
    from sqlalchemy import inspect
    from types import SimpleNamespace
    dropped = extension_lifecycle.purge_models(SimpleNamespace(slug=SLUG))
    assert dropped >= 4
    remaining = [t for t in inspect(db.engine).get_table_names()
                 if t.startswith('ext_serverkit_analytics')]
    assert remaining == []
    assert 'users' in inspect(db.engine).get_table_names()  # core survives
    db.create_all()  # restore for later tests in this module


def test_site_key_is_generated_and_unique(app):
    a = _mk_site(name='A')
    b = _mk_site(name='B')
    assert a.site_key and b.site_key and a.site_key != b.site_key


# --------------------------------------------------------------------------- #
# ingest units: bot filter, UA parse, visitor hash, helpers
# --------------------------------------------------------------------------- #
def test_is_bot_matches_common_agents():
    assert ingest_mod.is_bot('curl/7.68.0')
    assert ingest_mod.is_bot('Googlebot/2.1')
    assert ingest_mod.is_bot('python-requests/2.31')
    assert not ingest_mod.is_bot(UA_CHROME)


def test_parse_ua_classifies():
    browser, os_family, device = ingest_mod.parse_ua(UA_CHROME)
    assert browser == 'Chrome'
    assert os_family == 'Windows'
    assert device == 'desktop'
    iphone = ('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) '
              'AppleWebKit/605 (KHTML, like Gecko) Version/17 Mobile/15E Safari/604')
    b2, os2, d2 = ingest_mod.parse_ua(iphone)
    assert os2 == 'iOS' and d2 == 'mobile'


def test_visitor_hash_stable_and_scoped(app):
    h1 = ingest_mod.visitor_hash(1, '1.2.3.4', UA_CHROME)
    h2 = ingest_mod.visitor_hash(1, '1.2.3.4', UA_CHROME)
    h_other_site = ingest_mod.visitor_hash(2, '1.2.3.4', UA_CHROME)
    h_other_ip = ingest_mod.visitor_hash(1, '9.9.9.9', UA_CHROME)
    assert h1 == h2                 # deterministic within the day
    assert len(h1) == 32
    assert h1 != h_other_site       # scoped by site
    assert h1 != h_other_ip         # scoped by ip
    assert '1.2.3.4' not in h1      # raw ip never leaks into the digest


def test_referrer_host_and_clean_path():
    assert ingest_mod.referrer_host('https://google.com/search?q=x') == 'google.com'
    assert ingest_mod.referrer_host('') is None
    assert ingest_mod._clean_path('https://example.com/a/b?x=1#frag') == '/a/b'
    assert ingest_mod._clean_path('') == '/'
    assert ingest_mod._clean_path('noslash') == '/noslash'


# --------------------------------------------------------------------------- #
# collector route
# --------------------------------------------------------------------------- #
def test_collect_happy_path_buffers_and_flushes(app, analytics_client):
    site = _mk_site()
    resp = _collect(analytics_client, site.site_key, path='/home', r='https://google.com/')
    assert resp.status_code == 204
    assert ingest_mod.buffer_size() == 1
    written = ingest_mod.flush_buffer(app)
    assert written == 1
    ev = AnalyticsEvent.query.filter_by(site_id=site.id).one()
    assert ev.url_path == '/home'
    assert ev.referrer_host == 'google.com'
    assert ev.ua_family == 'Chrome'
    assert ev.source == 'js'
    assert ev.visitor_hash and len(ev.visitor_hash) == 32


def test_collect_unknown_key_404(app, analytics_client):
    resp = _collect(analytics_client, 'nope-not-a-real-key')
    assert resp.status_code == 404
    assert ingest_mod.buffer_size() == 0


def test_collect_disabled_site_403(app, analytics_client):
    site = _mk_site(enabled=False)
    resp = _collect(analytics_client, site.site_key)
    assert resp.status_code == 403


def test_collect_missing_key_400(app, analytics_client):
    resp = analytics_client.post('/api/v1/analytics/collect',
                                 data=json.dumps({'p': '/x'}),
                                 headers={'User-Agent': UA_CHROME})
    assert resp.status_code == 400


def test_collect_malformed_json_400(app, analytics_client):
    resp = analytics_client.post('/api/v1/analytics/collect',
                                 data='{not json',
                                 headers={'User-Agent': UA_CHROME})
    assert resp.status_code == 400


def test_collect_oversized_body_413(app, analytics_client):
    site = _mk_site()
    big = json.dumps({'k': site.site_key, 'p': '/x', 'pad': 'A' * 9000})
    resp = _collect(analytics_client, site.site_key, body=big)
    assert resp.status_code == 413


def test_collect_bot_ua_dropped(app, analytics_client):
    site = _mk_site()
    resp = _collect(analytics_client, site.site_key, ua='curl/7.68.0')
    assert resp.status_code == 204
    assert ingest_mod.buffer_size() == 0  # accepted but not stored


def test_collect_empty_ua_dropped(app, analytics_client):
    site = _mk_site()
    resp = _collect(analytics_client, site.site_key, ua='')
    assert resp.status_code == 204
    assert ingest_mod.buffer_size() == 0


def test_collect_honors_dnt(app, analytics_client):
    site = _mk_site()
    resp = _collect(analytics_client, site.site_key, extra_headers={'DNT': '1'})
    assert resp.status_code == 204
    assert ingest_mod.buffer_size() == 0


def test_collect_per_site_dnt_override(app, analytics_client):
    # Global honor_dnt default True; a site opting OUT still records a DNT hit.
    site = _mk_site(honor_dnt=False)
    resp = _collect(analytics_client, site.site_key, extra_headers={'DNT': '1'})
    assert resp.status_code == 204
    assert ingest_mod.buffer_size() == 1


def test_collect_rate_limited_429(app, analytics_client):
    _mk_plugin_row(config={'collect_rate_per_min': 2})
    site = _mk_site()
    assert _collect(analytics_client, site.site_key).status_code == 204
    assert _collect(analytics_client, site.site_key).status_code == 204
    assert _collect(analytics_client, site.site_key).status_code == 429


def test_collect_origin_allowlist_enforced(app, analytics_client):
    site = _mk_site(allowed_origins='https://example.com')
    ok = _collect(analytics_client, site.site_key,
                  extra_headers={'Origin': 'https://example.com'})
    assert ok.status_code == 204
    assert ok.headers.get('Access-Control-Allow-Origin') == 'https://example.com'
    bad = _collect(analytics_client, site.site_key,
                   extra_headers={'Origin': 'https://evil.test'})
    assert bad.status_code == 403


def test_collect_reflects_origin_by_default(app, analytics_client):
    site = _mk_site()  # no allowlist => reflect
    resp = _collect(analytics_client, site.site_key,
                    extra_headers={'Origin': 'https://anything.test'})
    assert resp.headers.get('Access-Control-Allow-Origin') == 'https://anything.test'


def test_collect_options_preflight(app, analytics_client):
    resp = analytics_client.options('/api/v1/analytics/collect',
                                    headers={'Origin': 'https://example.com'})
    assert resp.status_code == 204
    assert resp.headers.get('Access-Control-Allow-Methods') == 'POST, OPTIONS'


def test_collect_stores_query_only_when_enabled(app, analytics_client):
    _mk_plugin_row(config={'store_query_strings': True})
    site = _mk_site()
    _collect(analytics_client, site.site_key, path='/search', q='term=hats')
    ingest_mod.flush_buffer(app)
    ev = AnalyticsEvent.query.filter_by(site_id=site.id).one()
    assert ev.url_query == 'term=hats'


def test_tracker_js_served_public(app, analytics_client):
    resp = analytics_client.get('/api/v1/analytics/tracker.js')
    assert resp.status_code == 200
    assert 'javascript' in resp.headers['Content-Type']
    assert resp.headers.get('Access-Control-Allow-Origin') == '*'
    assert resp.headers.get('Cache-Control') == 'no-cache'


# --------------------------------------------------------------------------- #
# retention prune
# --------------------------------------------------------------------------- #
def test_retention_prune_deletes_old_events(app):
    from app import db
    from datetime import datetime, timedelta
    site = _mk_site()
    old = AnalyticsEvent(site_id=site.id, type='pageview', url_path='/old',
                         ts=datetime.utcnow() - timedelta(days=99), source='js')
    fresh = AnalyticsEvent(site_id=site.id, type='pageview', url_path='/new',
                           ts=datetime.utcnow(), source='js')
    db.session.add_all([old, fresh])
    db.session.commit()
    _mk_plugin_row(config={'raw_retention_days': 30})
    result = rollup_mod.run_retention_prune()
    assert result['deleted_events'] == 1
    remaining = [e.url_path for e in AnalyticsEvent.query.filter_by(site_id=site.id)]
    assert remaining == ['/new']


def test_retention_job_handler_never_raises(app):
    _mk_plugin_row()
    out = jobs_mod.retention_prune(job=None)
    assert 'deleted_events' in out or 'error' in out


# --------------------------------------------------------------------------- #
# Phase 2: rollup + report API
# --------------------------------------------------------------------------- #
def _mk_event(site_id, visitor='v1', path='/home', ref=None, device='desktop',
              browser='Chrome', os_family='Windows', country=None, load_ms=None,
              days_ago=0, etype='pageview'):
    from app import db
    from datetime import datetime, timedelta
    ev = AnalyticsEvent(
        site_id=site_id, type=etype, url_path=path, referrer_host=ref,
        visitor_hash=visitor, device_class=device, ua_family=browser,
        os_family=os_family, country=country, load_ms=load_ms, source='js',
        ts=datetime.utcnow() - timedelta(days=days_ago))
    db.session.add(ev)
    db.session.commit()
    return ev


def test_rollup_aggregates_overall(app):
    site = _mk_site()
    # Two visitors today: v1 has 2 pageviews (not a bounce), v2 has 1 (a bounce).
    _mk_event(site.id, visitor='v1', path='/a', load_ms=100)
    _mk_event(site.id, visitor='v1', path='/b', load_ms=200)
    _mk_event(site.id, visitor='v2', path='/a', load_ms=300)
    out = rollup_mod.run_rollup()
    assert out['rows'] > 0
    row = AnalyticsDaily.query.filter_by(site_id=site.id, dim_type='overall').one()
    assert row.visitors == 2
    assert row.pageviews == 3
    assert row.bounces == 1              # only v2 had a single pageview
    assert row.avg_load_ms == 200.0      # (100+200+300)/3


def test_rollup_is_idempotent(app):
    site = _mk_site()
    _mk_event(site.id, visitor='v1', path='/a')
    rollup_mod.run_rollup()
    rollup_mod.run_rollup()  # second run must not double-count
    row = AnalyticsDaily.query.filter_by(site_id=site.id, dim_type='overall').one()
    assert row.pageviews == 1


def test_rollup_dimensions(app):
    site = _mk_site()
    _mk_event(site.id, visitor='v1', path='/a', ref='google.com', device='mobile',
              browser='Firefox', os_family='Android', country='US')
    _mk_event(site.id, visitor='v2', path='/a', ref='google.com', device='desktop',
              browser='Chrome', os_family='Windows', country='CA')
    rollup_mod.run_rollup()
    page = AnalyticsDaily.query.filter_by(site_id=site.id, dim_type='page',
                                          dim_value='/a').one()
    assert page.pageviews == 2 and page.visitors == 2
    ref = AnalyticsDaily.query.filter_by(site_id=site.id, dim_type='referrer',
                                         dim_value='google.com').one()
    assert ref.pageviews == 2
    countries = {r.dim_value for r in AnalyticsDaily.query.filter_by(
        site_id=site.id, dim_type='country')}
    assert countries == {'US', 'CA'}


def test_report_overview_and_pages(app):
    from datetime import datetime, timedelta
    site = _mk_site()
    _mk_event(site.id, visitor='v1', path='/a')
    _mk_event(site.id, visitor='v2', path='/a')
    _mk_event(site.id, visitor='v1', path='/b')
    rollup_mod.run_rollup()
    end = datetime.utcnow().date()
    start = end - timedelta(days=6)
    ov = report_mod.overview(site.id, start, end)
    assert ov['totals']['pageviews'] == 3
    assert ov['totals']['visitors'] == 2
    assert len(ov['timeseries']) == 7             # zero-filled 7-day window
    top = {p['value']: p['pageviews'] for p in ov['top_pages']}
    assert top['/a'] == 2 and top['/b'] == 1


def test_report_realtime_reads_raw_events(app):
    site = _mk_site()
    _mk_event(site.id, visitor='v1', path='/live')
    _mk_event(site.id, visitor='v1', path='/live2')
    _mk_event(site.id, visitor='v2', path='/live')
    rt = report_mod.realtime(site.id, minutes=30)
    assert rt['active_visitors'] == 2
    assert rt['pageviews'] == 3
    assert rt['recent'][0]['path'] in ('/live', '/live2')


def test_parse_range_defaults_and_explicit():
    from datetime import datetime
    s, e = report_mod.parse_range({})
    assert (e - s).days == 6                       # default 7d inclusive
    s2, e2 = report_mod.parse_range({'range': '30d'})
    assert (e2 - s2).days == 29
    s3, e3 = report_mod.parse_range({'start': '2026-01-01', 'end': '2026-01-10'})
    assert s3.isoformat() == '2026-01-01' and e3.isoformat() == '2026-01-10'


# --- site CRUD + RBAC ---
def test_sites_crud_flow(app, analytics_client):
    admin = _auth(_mk_admin())
    # create
    r = analytics_client.post('/api/v1/analytics/sites', headers=admin,
                              json={'name': 'Blog', 'hostnames': ['blog.example.com']})
    assert r.status_code == 201
    body = r.get_json()
    sid = body['id']
    assert body['site_key']
    assert body['hostnames'] == ['blog.example.com']
    # list
    r = analytics_client.get('/api/v1/analytics/sites', headers=admin)
    assert any(s['id'] == sid for s in r.get_json()['sites'])
    # update
    r = analytics_client.put(f'/api/v1/analytics/sites/{sid}', headers=admin,
                             json={'enabled': False})
    assert r.get_json()['enabled'] is False
    # rotate key
    old_key = body['site_key']
    r = analytics_client.post(f'/api/v1/analytics/sites/{sid}/rotate-key', headers=admin)
    assert r.get_json()['site_key'] != old_key
    # delete
    r = analytics_client.delete(f'/api/v1/analytics/sites/{sid}', headers=admin)
    assert r.status_code == 200
    assert AnalyticsSite.query.get(sid) is None


def test_create_site_requires_name_400(app, analytics_client):
    admin = _auth(_mk_admin())
    r = analytics_client.post('/api/v1/analytics/sites', headers=admin, json={})
    assert r.status_code == 400


def test_reads_allow_viewer(app, analytics_client):
    viewer = _auth(_mk_viewer())
    site = _mk_site()
    r = analytics_client.get('/api/v1/analytics/sites', headers=viewer)
    assert r.status_code == 200
    r = analytics_client.get(f'/api/v1/analytics/sites/{site.id}/overview',
                             headers=viewer)
    assert r.status_code == 200


def test_mutations_deny_viewer_403(app, analytics_client):
    viewer = _auth(_mk_viewer())
    r = analytics_client.post('/api/v1/analytics/sites', headers=viewer,
                              json={'name': 'x'})
    assert r.status_code == 403


def test_reports_require_auth_401(app, analytics_client):
    site = _mk_site()
    r = analytics_client.get(f'/api/v1/analytics/sites/{site.id}/overview')
    assert r.status_code in (401, 422)  # missing/invalid JWT


def test_overview_endpoint_shape(app, analytics_client):
    admin = _auth(_mk_admin())
    site = _mk_site()
    _mk_event(site.id, visitor='v1', path='/a')
    rollup_mod.run_rollup()
    r = analytics_client.get(f'/api/v1/analytics/sites/{site.id}/overview?range=30d',
                             headers=admin)
    assert r.status_code == 200
    body = r.get_json()
    assert set(body) >= {'totals', 'timeseries', 'top_pages', 'top_referrers',
                         'realtime', 'range'}


def test_devices_endpoint(app, analytics_client):
    admin = _auth(_mk_admin())
    site = _mk_site()
    _mk_event(site.id, visitor='v1', device='mobile', browser='Safari',
              os_family='iOS', country='US')
    rollup_mod.run_rollup()
    r = analytics_client.get(f'/api/v1/analytics/sites/{site.id}/devices',
                             headers=admin)
    body = r.get_json()
    assert body['device'][0]['value'] == 'mobile'
    assert body['os'][0]['value'] == 'iOS'


def test_rollup_endpoint_admin_only(app, analytics_client):
    viewer = _auth(_mk_viewer())
    assert analytics_client.post('/api/v1/analytics/rollup',
                                 headers=viewer).status_code == 403
    admin = _auth(_mk_admin())
    assert analytics_client.post('/api/v1/analytics/rollup',
                                 headers=admin).status_code == 200


def test_rollup_job_handler_never_raises(app):
    _mk_plugin_row()
    out = jobs_mod.rollup(job=None)
    assert 'rows' in out or 'error' in out or 'sites_dates' in out


# --------------------------------------------------------------------------- #
# Phase 3: tracker JS artifact + snippet
# --------------------------------------------------------------------------- #
def test_tracker_min_artifact_exists_and_bounded():
    path = os.path.join(EXT_DIR, 'backend', 'tracker', 'sk.min.js')
    assert os.path.exists(path), 'run scripts/build-tracker.mjs'
    body = open(path, encoding='utf-8').read()
    assert len(body.encode('utf-8')) < 4096  # <4 KB budget
    assert '/*' not in body and '*/' not in body  # comments stripped
    assert 'sendBeacon' in body and 'data-site-key' in body


def test_tracker_js_serves_real_build(app, analytics_client):
    bp_mod._TRACKER_CACHE['js'] = None  # bypass any placeholder cached earlier
    resp = analytics_client.get('/api/v1/analytics/tracker.js')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'placeholder' not in body
    assert 'sendBeacon' in body


def test_snippet_endpoint(app, analytics_client):
    viewer = _auth(_mk_viewer())
    site = _mk_site()
    r = analytics_client.get(f'/api/v1/analytics/sites/{site.id}/snippet',
                             headers=viewer)
    assert r.status_code == 200
    body = r.get_json()
    assert body['site_key'] == site.site_key
    assert '/api/v1/analytics/tracker.js' in body['tracker_url']
    assert f'data-site-key="{site.site_key}"' in body['snippet']
    assert body['snippet'].startswith('<script defer')


def test_snippet_outlinks_flag(app, analytics_client):
    viewer = _auth(_mk_viewer())
    site = _mk_site()
    r = analytics_client.get(
        f'/api/v1/analytics/sites/{site.id}/snippet?outlinks=true', headers=viewer)
    assert 'data-outlinks="true"' in r.get_json()['snippet']


def test_snippet_missing_site_404(app, analytics_client):
    viewer = _auth(_mk_viewer())
    r = analytics_client.get('/api/v1/analytics/sites/9999/snippet', headers=viewer)
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# Phase 5: WordPress mu-plugin injection
# --------------------------------------------------------------------------- #
def test_mu_plugin_php_content():
    php = wp_mod.build_mu_plugin_php('KEY123', 'https://panel.test/api/v1/analytics/tracker.js')
    assert php.startswith('<?php')
    assert "add_action('wp_head'" in php
    assert 'KEY123' in php and 'https://panel.test/api/v1/analytics/tracker.js' in php
    assert 'esc_url' in php and 'esc_attr' in php


def test_wp_inject_writes_and_is_idempotent(app, tmp_path, monkeypatch):
    from types import SimpleNamespace
    site = _mk_site(app_id=42)
    target = SimpleNamespace(container='wp1', is_docker=False, root=str(tmp_path))
    monkeypatch.setattr(wp_mod, '_resolve_target', lambda s: target)

    res = wp_mod.inject(site, 'https://panel.test/api/v1/analytics/tracker.js')
    assert res.get('success')
    mu = tmp_path / 'wp-content' / 'mu-plugins' / 'serverkit-analytics.php'
    assert mu.exists()
    body = mu.read_text(encoding='utf-8')
    assert site.site_key in body
    assert site.get_settings().get('wp_injected') is True

    # Re-run overwrites, stays single file, flag persists.
    wp_mod.inject(site, 'https://panel.test/api/v1/analytics/tracker.js')
    assert mu.exists()
    assert site.get_settings().get('wp_injected') is True


def test_wp_inject_docker_uses_exec(app, monkeypatch):
    from types import SimpleNamespace
    calls = []
    site = _mk_site(app_id=7)
    target = SimpleNamespace(container='wpc', is_docker=True, root=None)
    monkeypatch.setattr(wp_mod, '_resolve_target', lambda s: target)
    monkeypatch.setattr(wp_mod, '_docker_exec',
                        lambda c, argv, input_text=None: calls.append((c, argv, input_text)) or {'success': True})
    res = wp_mod.inject(site, 'https://p.test/api/v1/analytics/tracker.js')
    assert res.get('success')
    # mkdir then tee-with-content
    assert any(a[1][0] == 'tee' and a[2] and site.site_key in a[2] for a in calls)


def test_wp_remove_deletes_and_clears_flag(app, tmp_path, monkeypatch):
    from types import SimpleNamespace
    site = _mk_site(app_id=42)
    target = SimpleNamespace(container='wp1', is_docker=False, root=str(tmp_path))
    monkeypatch.setattr(wp_mod, '_resolve_target', lambda s: target)
    wp_mod.inject(site, 'https://p.test/api/v1/analytics/tracker.js')
    mu = tmp_path / 'wp-content' / 'mu-plugins' / 'serverkit-analytics.php'
    assert mu.exists()
    wp_mod.remove(site)
    assert not mu.exists()
    assert site.get_settings().get('wp_injected') is False


def test_wp_inject_no_linked_site(app):
    site = _mk_site()  # app_id None
    res = wp_mod.inject(site, 'https://p.test/x')
    assert res.get('success') is False


def test_wp_remove_all_injections(app, tmp_path, monkeypatch):
    from types import SimpleNamespace
    site = _mk_site(app_id=1)
    target = SimpleNamespace(container='wp1', is_docker=False, root=str(tmp_path))
    monkeypatch.setattr(wp_mod, '_resolve_target', lambda s: target)
    wp_mod.inject(site, 'https://p.test/api/v1/analytics/tracker.js')
    n = wp_mod.remove_all_injections()
    assert n == 1
    assert site.get_settings().get('wp_injected') is False


def test_wordpress_available_reflects_installed(app):
    from app import db
    from app.models.plugin import InstalledPlugin
    # Control the state: remove any seeded flagship row first.
    InstalledPlugin.query.filter_by(slug='serverkit-wordpress').delete()
    db.session.commit()
    assert wp_mod.wordpress_available() is False
    db.session.add(InstalledPlugin(name='serverkit-wordpress', display_name='WP',
                                   slug='serverkit-wordpress', version='1.0.0',
                                   status=InstalledPlugin.STATUS_ACTIVE))
    db.session.commit()
    assert wp_mod.wordpress_available() is True


# --------------------------------------------------------------------------- #
# Phase 5: nginx sub_filter injection
# --------------------------------------------------------------------------- #
_VHOST = (
    'server {\n'
    '    listen 80;\n'
    '    server_name app.test;\n'
    '    location / {\n'
    '        proxy_pass http://127.0.0.1:8001;\n'
    '    }\n'
    '}\n'
)


def _patch_nginx(monkeypatch, path, test_ok=True):
    monkeypatch.setattr(nginx_mod, '_vhost_path', lambda v: str(path))
    monkeypatch.setattr(nginx_mod, '_nginx_test', lambda: {'success': test_ok,
                                                           'message': 'ok' if test_ok else 'bad'})
    monkeypatch.setattr(nginx_mod, '_nginx_reload', lambda: {'success': True})


def test_nginx_block_build_and_strip():
    block = nginx_mod.build_sub_filter_block('https://p/t.js', 'KEY')
    assert 'sub_filter' in block and 'KEY' in block
    content = 'server {\n}\n'
    injected = nginx_mod._insert_block(content, block)
    assert 'BEGIN serverkit-analytics' in injected
    stripped = nginx_mod._strip_block(injected)
    assert 'serverkit-analytics' not in stripped
    assert stripped == content  # perfectly reversible


def test_nginx_inject_and_idempotent(app, tmp_path, monkeypatch):
    vpath = tmp_path / 'app.test'
    vpath.write_text(_VHOST, encoding='utf-8')
    site = _mk_site()
    site.update_settings(nginx_vhost='app.test')
    from app import db
    db.session.commit()
    _patch_nginx(monkeypatch, vpath)

    res = nginx_mod.inject(site, 'https://p/t.js')
    assert res.get('success')
    body = vpath.read_text(encoding='utf-8')
    assert body.count('BEGIN serverkit-analytics') == 1
    assert site.get_settings().get('nginx_injected') is True

    nginx_mod.inject(site, 'https://p/t.js')  # re-inject
    assert vpath.read_text(encoding='utf-8').count('BEGIN serverkit-analytics') == 1


def test_nginx_inject_reverts_on_invalid_config(app, tmp_path, monkeypatch):
    vpath = tmp_path / 'app.test'
    vpath.write_text(_VHOST, encoding='utf-8')
    site = _mk_site()
    site.update_settings(nginx_vhost='app.test')
    from app import db
    db.session.commit()
    _patch_nginx(monkeypatch, vpath, test_ok=False)

    res = nginx_mod.inject(site, 'https://p/t.js')
    assert res.get('success') is False
    # File must be reverted to the original — never leave nginx broken.
    assert vpath.read_text(encoding='utf-8') == _VHOST
    assert site.get_settings().get('nginx_injected') is not True


def test_nginx_remove(app, tmp_path, monkeypatch):
    vpath = tmp_path / 'app.test'
    vpath.write_text(_VHOST, encoding='utf-8')
    site = _mk_site()
    site.update_settings(nginx_vhost='app.test')
    from app import db
    db.session.commit()
    _patch_nginx(monkeypatch, vpath)
    nginx_mod.inject(site, 'https://p/t.js')
    nginx_mod.remove(site)
    assert 'serverkit-analytics' not in vpath.read_text(encoding='utf-8')
    assert site.get_settings().get('nginx_injected') is False


def test_inject_endpoints_admin_gated(app, analytics_client, monkeypatch):
    from types import SimpleNamespace
    site = _mk_site(app_id=5)
    monkeypatch.setattr(wp_mod, '_resolve_target',
                        lambda s: SimpleNamespace(container='c', is_docker=True, root=None))
    monkeypatch.setattr(wp_mod, '_docker_exec',
                        lambda c, argv, input_text=None: {'success': True})
    viewer = _auth(_mk_viewer())
    admin = _auth(_mk_admin())
    assert analytics_client.post(f'/api/v1/analytics/sites/{site.id}/inject/wordpress',
                                 headers=viewer).status_code == 403
    r = analytics_client.post(f'/api/v1/analytics/sites/{site.id}/inject/wordpress',
                              headers=admin)
    assert r.status_code == 200 and r.get_json().get('success')


# --------------------------------------------------------------------------- #
# Phase 6: log ingestion + geo
# --------------------------------------------------------------------------- #
_LOG_LINE = (
    '203.0.113.9 - - [10/Oct/2024:13:55:36 +0000] "GET /blog/post HTTP/1.1" '
    '200 5120 "https://google.com/search" '
    '"Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/120 Safari/537.36"'
)
_LOG_ASSET = (
    '203.0.113.9 - - [10/Oct/2024:13:55:37 +0000] "GET /style.css HTTP/1.1" '
    '200 1000 "-" "Mozilla/5.0 Chrome/120"'
)
_LOG_BOT = (
    '203.0.113.9 - - [10/Oct/2024:13:55:38 +0000] "GET / HTTP/1.1" '
    '200 1000 "-" "Googlebot/2.1"'
)
_LOG_POST = (
    '203.0.113.9 - - [10/Oct/2024:13:55:39 +0000] "POST /wp-login.php HTTP/1.1" '
    '200 1000 "-" "Mozilla/5.0 Chrome/120"'
)


def test_parse_combined_line():
    p = log_mod.parse_combined_line(_LOG_LINE)
    assert p['ip'] == '203.0.113.9'
    assert p['path'] == '/blog/post'
    assert p['status'] == '200'
    assert 'google' in p['referer']
    # assets, bots-by-request, and non-GET are filtered at parse time.
    assert log_mod.parse_combined_line(_LOG_ASSET) is None   # asset
    assert log_mod.parse_combined_line(_LOG_POST) is None    # non-GET
    assert log_mod.parse_combined_line('garbage line') is None


def test_ingest_lines_creates_log_events(app):
    site = _mk_site()
    written, max_ts = log_mod.ingest_lines(site, [_LOG_LINE, _LOG_ASSET, _LOG_BOT])
    assert written == 1  # only the real pageview (asset + bot dropped)
    ev = AnalyticsEvent.query.filter_by(site_id=site.id).one()
    assert ev.source == 'log'
    assert ev.url_path == '/blog/post'
    assert ev.referrer_host == 'google.com'
    assert ev.ua_family == 'Chrome'
    assert ev.visitor_hash and '203.0.113.9' not in ev.visitor_hash


def test_ingest_lines_dedup_by_since(app):
    from datetime import datetime
    site = _mk_site()
    # since_ts at/after the line's ts => skipped.
    boundary = datetime(2024, 10, 10, 14, 0, 0)
    written, _ = log_mod.ingest_lines(site, [_LOG_LINE], since_ts=boundary)
    assert written == 0


def test_log_file_cursor_advances(app, tmp_path):
    site = _mk_site()
    logf = tmp_path / 'access.log'
    logf.write_text(_LOG_LINE + '\n', encoding='utf-8')
    site.update_settings(log_ingestion=True, log_source_kind='file',
                         log_source_ref=str(logf))
    from app import db
    db.session.commit()

    res1 = log_mod.ingest_site(site)
    assert res1['written'] == 1
    # Second run with no new lines writes nothing (cursor advanced).
    res2 = log_mod.ingest_site(site)
    assert res2['written'] == 0
    # Append a new line -> ingested on the next run.
    with open(logf, 'a', encoding='utf-8') as f:
        f.write(_LOG_LINE.replace('/blog/post', '/blog/post2') + '\n')
    res3 = log_mod.ingest_site(site)
    assert res3['written'] == 1
    assert AnalyticsEvent.query.filter_by(site_id=site.id).count() == 2


def test_ingest_site_skips_when_disabled(app):
    site = _mk_site()  # no log_ingestion setting
    assert log_mod.ingest_site(site).get('skipped') is True


def test_run_log_tail_iterates_enabled_sites(app, tmp_path):
    _mk_plugin_row(config={'log_ingestion_enabled': True})
    site = _mk_site()
    logf = tmp_path / 'a.log'
    logf.write_text(_LOG_LINE + '\n', encoding='utf-8')
    site.update_settings(log_ingestion=True, log_source_kind='file',
                         log_source_ref=str(logf))
    from app import db
    db.session.commit()
    out = log_mod.run_log_tail()
    assert out['sites'] == 1 and out['written'] == 1


def test_run_log_tail_respects_global_toggle(app):
    _mk_plugin_row(config={'log_ingestion_enabled': False})
    out = log_mod.run_log_tail()
    assert out.get('skipped') is True


def test_log_tail_job_handler_never_raises(app):
    _mk_plugin_row(config={'log_ingestion_enabled': False})
    out = jobs_mod.log_tail(job=None)
    assert 'skipped' in out or 'sites' in out or 'error' in out


def test_ingest_logs_endpoint(app, analytics_client, tmp_path):
    admin = _auth(_mk_admin())
    site = _mk_site()
    logf = tmp_path / 'x.log'
    logf.write_text(_LOG_LINE + '\n', encoding='utf-8')
    site.update_settings(log_ingestion=True, log_source_kind='file',
                         log_source_ref=str(logf))
    from app import db
    db.session.commit()
    r = analytics_client.post(f'/api/v1/analytics/sites/{site.id}/ingest-logs',
                              headers=admin)
    assert r.status_code == 200 and r.get_json()['written'] == 1


# --- geo ---
def test_geo_disabled_returns_none(app):
    _mk_plugin_row(config={'geo_enabled': False})
    assert geo_mod.lookup_country('8.8.8.8') is None


def test_geo_enabled_but_no_db(app):
    _mk_plugin_row(config={'geo_enabled': True, 'geo_db_path': '/nonexistent/GeoLite2.mmdb'})
    geo_mod.reset()
    assert geo_mod.lookup_country('8.8.8.8') is None


def test_build_event_country_none_by_default(app):
    site = _mk_site()
    event = ingest_mod.build_event(site, {'k': site.site_key, 'p': '/x'},
                                   '8.8.8.8', UA_CHROME)
    assert event['country'] is None
