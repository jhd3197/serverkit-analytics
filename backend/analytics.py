"""Web Analytics API endpoints (serverkit-analytics extension).

Mounted under ``/api/v1/analytics`` via the manifest ``url_prefix``. Thin
routing layer over the extension services:

* ``ingest_service``      — validation, bot filter, visitor hashing, buffer+flush
* ``report_service``      — rollup-backed dashboard queries (Phase 2)
* ``log_ingest_service``  — access-log parsing (Phase 6)
* ``wp_integration`` / ``nginx_integration`` — one-click tracker injection (Ph5)

Auth split mirrors serverkit-tramo / serverkit-k8s: report reads need viewer,
site mutations need admin. The public tracker surface — ``POST /collect`` and
``GET /tracker.js`` — carries NO JWT because a tracked site's visitors have
none. It authenticates per-site via the ``site_key`` (not a JWT) and is
protected by a body-size cap, per-site origin allowlist, an in-process token
bucket, and bot/DNT filtering. The plugin-disabled 503 guard (attached by
plugin_service) still runs on every route, so disabling the extension stops
collection for free; the tracker fails silently on any non-2xx.
"""
import json
import logging
import os

from flask import Blueprint, current_app, jsonify, request

from app.middleware.rbac import admin_required, viewer_required
from app.utils.client_ip import get_client_ip

from . import (
    ingest_service, log_ingest_service, nginx_integration, report_service,
    rollup_service, site_service, wp_integration,
)
from .config import cfg_bool
from .ingest_service import MAX_BODY_BYTES, referrer_host
from .models import AnalyticsSite

logger = logging.getLogger(__name__)

analytics_bp = Blueprint('analytics', __name__)

_TRACKER_CACHE = {'js': None}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _plain(body='', status=204):
    """A bare Response (empty by default) used for accept-and-drop / success."""
    return current_app.make_response((body, status))


def _origin_allowed(origin, allowed):
    ohost = referrer_host(origin) or origin
    for entry in allowed:
        ehost = referrer_host(entry) or entry
        if origin == entry or ohost == ehost:
            return True
    return False


def _acao(site, origin):
    """The Access-Control-Allow-Origin value for this site+origin, or None."""
    if not origin:
        return None
    allowed = site.allowed_origin_list()
    if allowed:
        return origin if _origin_allowed(origin, allowed) else None
    return origin  # default: reflect the origin (token already validated)


def _cors(resp, site, origin):
    acao = _acao(site, origin)
    if acao:
        resp.headers['Access-Control-Allow-Origin'] = acao
        resp.headers['Vary'] = 'Origin'
    return resp


def _preflight(origin):
    resp = _plain('', 204)
    if origin:
        resp.headers['Access-Control-Allow-Origin'] = origin
        resp.headers['Vary'] = 'Origin'
    resp.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    resp.headers['Access-Control-Max-Age'] = '86400'
    return resp


# --------------------------------------------------------------------------- #
# public collector (NO auth — per-site token + rate limit + bot filter)
# --------------------------------------------------------------------------- #
@analytics_bp.route('/collect', methods=['POST', 'OPTIONS'])
def collect():
    origin = request.headers.get('Origin')
    if request.method == 'OPTIONS':
        return _preflight(origin)

    # Body-size cap (declared length first, then actual bytes).
    if request.content_length and request.content_length > MAX_BODY_BYTES:
        return jsonify({'error': 'payload too large'}), 413
    raw = request.get_data(cache=False) or b''
    if len(raw) > MAX_BODY_BYTES:
        return jsonify({'error': 'payload too large'}), 413

    # Beacons send text/plain (a CORS-safelisted content-type) to avoid a
    # preflight, so parse the body ourselves regardless of Content-Type.
    try:
        payload = json.loads(raw.decode('utf-8', 'ignore') or '{}')
    except (ValueError, TypeError):
        return jsonify({'error': 'invalid payload'}), 400
    if not isinstance(payload, dict):
        return jsonify({'error': 'invalid payload'}), 400

    site_key = payload.get('k') or payload.get('site_key')
    if not site_key or not isinstance(site_key, str):
        return jsonify({'error': 'missing site_key'}), 400

    site = AnalyticsSite.query.filter_by(site_key=site_key).first()
    if not site:
        return jsonify({'error': 'unknown site'}), 404
    if not site.enabled:
        return jsonify({'error': 'site disabled'}), 403

    # Per-site origin allowlist (empty list => reflect after token check).
    allowed = site.allowed_origin_list()
    if allowed and origin and not _origin_allowed(origin, allowed):
        return jsonify({'error': 'origin not allowed'}), 403

    ip = get_client_ip()
    ua = request.headers.get('User-Agent', '') or ''

    # Rate limit (per site_key AND per IP).
    if not ingest_service.rate_ok(site_key, ip):
        resp = jsonify({'error': 'rate limited'})
        resp.status_code = 429
        return _cors(resp, site, origin)

    # Do Not Track — accepted but not stored (server double-checks the tracker).
    honor_dnt = site.honor_dnt if site.honor_dnt is not None else cfg_bool('honor_dnt')
    if honor_dnt and request.headers.get('DNT') == '1':
        return _cors(_plain(), site, origin)

    # Bot filter: known bots and empty-UA JS hits are silently dropped (204) so
    # we never reveal filtering to an abuser.
    if not ua or ingest_service.is_bot(ua):
        return _cors(_plain(), site, origin)

    event = ingest_service.build_event(site, payload, ip, ua)
    if event is None:
        return _cors(_plain(), site, origin)

    ingest_service.record_event(event)
    ingest_service.ensure_flush_thread(current_app._get_current_object())
    return _cors(_plain(), site, origin)


# --------------------------------------------------------------------------- #
# public tracker script
# --------------------------------------------------------------------------- #
def _load_tracker_js():
    """Return the tracker JS body. Prefers a checked-in minified artifact
    (Phase 3 build output) and falls back to a tiny placeholder."""
    if _TRACKER_CACHE['js'] is not None:
        return _TRACKER_CACHE['js']
    here = os.path.dirname(os.path.abspath(__file__))
    for rel in (os.path.join('tracker', 'sk.min.js'),
                os.path.join('tracker', 'sk.js')):
        path = os.path.join(here, rel)
        try:
            with open(path, encoding='utf-8') as f:
                _TRACKER_CACHE['js'] = f.read()
                return _TRACKER_CACHE['js']
        except OSError:
            continue
    _TRACKER_CACHE['js'] = (
        '/* serverkit-analytics tracker (placeholder; real build in Phase 3) */\n'
        '(function(){})();\n'
    )
    return _TRACKER_CACHE['js']


@analytics_bp.route('/tracker.js', methods=['GET'])
def tracker_js():
    resp = current_app.make_response(_load_tracker_js())
    resp.headers['Content-Type'] = 'application/javascript; charset=utf-8'
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['Access-Control-Allow-Origin'] = '*'  # a public static script
    return resp


@analytics_bp.route('/ping', methods=['GET'])
def ping():
    """Unauthenticated liveness probe (also proves the 503 status guard)."""
    return jsonify({'ok': True, 'plugin': 'serverkit-analytics'}), 200


# --------------------------------------------------------------------------- #
# site CRUD (JWT: viewer reads, admin mutations)
# --------------------------------------------------------------------------- #
def _site_or_404(site_id):
    site = site_service.get_site(site_id)
    if not site:
        return None, (jsonify({'error': 'Site not found'}), 404)
    return site, None


@analytics_bp.route('/sites', methods=['GET'])
@viewer_required
def list_sites():
    return jsonify({'sites': site_service.list_sites()}), 200


@analytics_bp.route('/sites', methods=['POST'])
@admin_required
def create_site():
    data = request.get_json(silent=True) or {}
    try:
        site = site_service.create_site(data)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify(site.to_dict()), 201


@analytics_bp.route('/sites/<int:site_id>', methods=['GET'])
@viewer_required
def get_site(site_id):
    site, err = _site_or_404(site_id)
    if err:
        return err
    return jsonify(site.to_dict()), 200


@analytics_bp.route('/sites/<int:site_id>', methods=['PUT', 'PATCH'])
@admin_required
def update_site(site_id):
    data = request.get_json(silent=True) or {}
    try:
        site = site_service.update_site(site_id, data)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    if not site:
        return jsonify({'error': 'Site not found'}), 404
    return jsonify(site.to_dict()), 200


@analytics_bp.route('/sites/<int:site_id>', methods=['DELETE'])
@admin_required
def delete_site(site_id):
    if not site_service.delete_site(site_id):
        return jsonify({'error': 'Site not found'}), 404
    return jsonify({'deleted': True}), 200


@analytics_bp.route('/sites/<int:site_id>/rotate-key', methods=['POST'])
@admin_required
def rotate_key(site_id):
    site = site_service.rotate_key(site_id)
    if not site:
        return jsonify({'error': 'Site not found'}), 404
    return jsonify(site.to_dict()), 200


def _panel_base_url():
    """Best public base URL for the tracker script, preferring a configured
    public URL over the request host so a copied snippet works off-panel."""
    from flask import current_app as _app
    configured = (_app.config.get('SERVERKIT_PUBLIC_URL')
                  or _app.config.get('PUBLIC_URL') or '').strip()
    if configured:
        return configured.rstrip('/')
    return (request.host_url or '').rstrip('/')


@analytics_bp.route('/sites/<int:site_id>/snippet', methods=['GET'])
@viewer_required
def site_snippet(site_id):
    """Return the ready-to-paste tracker snippet for a site."""
    site, err = _site_or_404(site_id)
    if err:
        return err
    tracker_url = f'{_panel_base_url()}/api/v1/analytics/tracker.js'
    outlinks = str(request.args.get('outlinks', '')).lower() in ('1', 'true', 'yes')
    attrs = f' src="{tracker_url}" data-site-key="{site.site_key}"'
    if outlinks:
        attrs += ' data-outlinks="true"'
    snippet = f'<script defer{attrs}></script>'
    return jsonify({
        'snippet': snippet,
        'tracker_url': tracker_url,
        'site_key': site.site_key,
    }), 200


# --------------------------------------------------------------------------- #
# report queries (JWT: viewer)
# --------------------------------------------------------------------------- #
@analytics_bp.route('/sites/<int:site_id>/overview', methods=['GET'])
@viewer_required
def site_overview(site_id):
    site, err = _site_or_404(site_id)
    if err:
        return err
    start, end = report_service.parse_range(request.args)
    return jsonify(report_service.overview(site_id, start, end)), 200


@analytics_bp.route('/sites/<int:site_id>/timeseries', methods=['GET'])
@viewer_required
def site_timeseries(site_id):
    site, err = _site_or_404(site_id)
    if err:
        return err
    start, end = report_service.parse_range(request.args)
    return jsonify({'series': report_service.timeseries(site_id, start, end)}), 200


@analytics_bp.route('/sites/<int:site_id>/pages', methods=['GET'])
@viewer_required
def site_pages(site_id):
    site, err = _site_or_404(site_id)
    if err:
        return err
    start, end = report_service.parse_range(request.args)
    return jsonify(report_service.pages(site_id, start, end)), 200


@analytics_bp.route('/sites/<int:site_id>/referrers', methods=['GET'])
@viewer_required
def site_referrers(site_id):
    site, err = _site_or_404(site_id)
    if err:
        return err
    start, end = report_service.parse_range(request.args)
    return jsonify(report_service.referrers(site_id, start, end)), 200


@analytics_bp.route('/sites/<int:site_id>/devices', methods=['GET'])
@viewer_required
def site_devices(site_id):
    site, err = _site_or_404(site_id)
    if err:
        return err
    start, end = report_service.parse_range(request.args)
    return jsonify(report_service.devices(site_id, start, end)), 200


@analytics_bp.route('/sites/<int:site_id>/realtime', methods=['GET'])
@viewer_required
def site_realtime(site_id):
    site, err = _site_or_404(site_id)
    if err:
        return err
    minutes = request.args.get('minutes', 30)
    return jsonify(report_service.realtime(site_id, minutes)), 200


@analytics_bp.route('/rollup', methods=['POST'])
@admin_required
def trigger_rollup():
    """Run the rollup on demand (ops/testing convenience)."""
    return jsonify(rollup_service.run_rollup()), 200


# --------------------------------------------------------------------------- #
# tracker injection into managed sites (JWT: admin)
# --------------------------------------------------------------------------- #
def _tracker_url():
    return f'{_panel_base_url()}/api/v1/analytics/tracker.js'


@analytics_bp.route('/sites/<int:site_id>/inject/wordpress', methods=['POST'])
@admin_required
def inject_wordpress(site_id):
    site, err = _site_or_404(site_id)
    if err:
        return err
    result = wp_integration.inject(site, _tracker_url())
    code = 200 if result.get('success') else 400
    return jsonify(result), code


@analytics_bp.route('/sites/<int:site_id>/inject/wordpress', methods=['DELETE'])
@admin_required
def remove_wordpress(site_id):
    site, err = _site_or_404(site_id)
    if err:
        return err
    return jsonify(wp_integration.remove(site)), 200


@analytics_bp.route('/sites/<int:site_id>/inject/nginx', methods=['POST'])
@admin_required
def inject_nginx(site_id):
    site, err = _site_or_404(site_id)
    if err:
        return err
    vhost = (request.get_json(silent=True) or {}).get('vhost')
    result = nginx_integration.inject(site, _tracker_url(), vhost=vhost)
    code = 200 if result.get('success') else 400
    return jsonify(result), code


@analytics_bp.route('/sites/<int:site_id>/inject/nginx', methods=['DELETE'])
@admin_required
def remove_nginx(site_id):
    site, err = _site_or_404(site_id)
    if err:
        return err
    return jsonify(nginx_integration.remove(site)), 200


@analytics_bp.route('/sites/<int:site_id>/ingest-logs', methods=['POST'])
@admin_required
def ingest_logs(site_id):
    """Run server-log ingestion for one site on demand (ops/testing)."""
    site, err = _site_or_404(site_id)
    if err:
        return err
    return jsonify(log_ingest_service.ingest_site(site)), 200
