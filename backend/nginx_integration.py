"""nginx tracker injection for serverkit-analytics.

For any managed, nginx-proxied app, ServerKit already owns the vhost. This
module injects an nginx ``sub_filter`` that rewrites ``</body>`` to include the
tracker snippet, so HTML responses carry the tracker with no app-side change.
Default OFF; opt-in per app.

Safety (blast-radius guardrails, same spirit as plan 46):
* Idempotent, reversible marker block (``# BEGIN/END serverkit-analytics``).
* Validate with ``nginx -t`` BEFORE reloading, and REVERT the file if the test
  fails, so a bad edit can never take nginx down.
* Reload (never restart) via the core ``NginxService`` seam.

Linux-only at runtime; the file/validate/reload seams are mockable for tests.
"""
import logging
import os

logger = logging.getLogger(__name__)

SLUG = 'serverkit-analytics'
BEGIN = '    # BEGIN serverkit-analytics'
END = '    # END serverkit-analytics'


def build_sub_filter_block(tracker_url, site_key):
    """The marked nginx sub_filter block (indented for a server context)."""
    src = str(tracker_url).replace("'", '').replace('"', '')
    key = str(site_key).replace("'", '').replace('"', '')
    snippet = (f'<script defer src="{src}" data-site-key="{key}"></script>')
    return (
        f'{BEGIN}\n'
        '    # Ask upstreams for an uncompressed body so sub_filter can rewrite it\n'
        '    # (best-effort at server scope; a location proxy_set_header block\n'
        '    # would override this — regenerate the vhost if so).\n'
        '    proxy_set_header Accept-Encoding "";\n'
        '    sub_filter_once on;\n'
        '    sub_filter_types text/html;\n'
        f"    sub_filter '</body>' '{snippet}</body>';\n"
        f'{END}\n'
    )


# --------------------------------------------------------------------------- #
# mockable seams
# --------------------------------------------------------------------------- #
def _vhost_path(vhost):
    from app.services.nginx_service import NginxService
    return os.path.join(NginxService.SITES_AVAILABLE, vhost)


def _read_vhost(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def _write_vhost(path, content):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)


def _nginx_reload():
    """Validate + reload nginx. NginxService.reload() runs nginx -t first."""
    try:
        from app.services.nginx_service import NginxService
        return NginxService.reload()
    except Exception as e:  # noqa: BLE001
        return {'success': False, 'error': str(e)}


def _nginx_test():
    try:
        from app.services.nginx_service import NginxService
        return NginxService.test_config()
    except Exception as e:  # noqa: BLE001
        return {'success': False, 'error': str(e)}


# --------------------------------------------------------------------------- #
# block insert / strip
# --------------------------------------------------------------------------- #
def _strip_block(content):
    """Remove an existing marker block (if present). Returns new content."""
    start = content.find(BEGIN)
    if start == -1:
        return content
    end = content.find(END, start)
    if end == -1:
        return content
    end += len(END)
    # Swallow a trailing newline left by the block.
    if end < len(content) and content[end] == '\n':
        end += 1
    return content[:start] + content[end:]


def _insert_block(content, block):
    """Insert the block just after the first ``server {`` (replacing any
    existing block first, so re-injection is idempotent)."""
    content = _strip_block(content)
    marker = content.find('server {')
    if marker == -1:
        return None
    brace = content.find('\n', marker)
    if brace == -1:
        return None
    at = brace + 1
    return content[:at] + block + content[at:]


def _vhost_for(site, vhost=None):
    """Resolve the vhost filename for a site (explicit, saved, or app name)."""
    if vhost:
        return vhost
    saved = site.get_settings().get('nginx_vhost')
    if saved:
        return saved
    if site.app_id:
        try:
            from app.models import Application
            app = Application.query.get(site.app_id)
            if app:
                return app.name
        except Exception:  # noqa: BLE001
            return None
    return None


def inject(site, tracker_url, vhost=None):
    """Insert the sub_filter block into the app's vhost, validate, reload."""
    name = _vhost_for(site, vhost)
    if not name:
        return {'success': False, 'error': 'No nginx vhost resolved for site'}
    path = _vhost_path(name)
    try:
        original = _read_vhost(path)
    except OSError as e:
        return {'success': False, 'error': f'vhost not readable: {e}'}

    block = build_sub_filter_block(tracker_url, site.site_key)
    updated = _insert_block(original, block)
    if updated is None:
        return {'success': False, 'error': 'no server block found in vhost'}

    _write_vhost(path, updated)
    test = _nginx_test()
    if not test.get('success'):
        _write_vhost(path, original)  # revert — never leave nginx broken
        return {'success': False, 'error': f"nginx config invalid, reverted: "
                f"{test.get('message') or test.get('error')}"}
    reload_res = _nginx_reload()
    if reload_res.get('success'):
        _mark(site, True, name)
    return reload_res


def remove(site, vhost=None):
    """Strip the sub_filter block from the app's vhost, validate, reload."""
    name = _vhost_for(site, vhost)
    if not name:
        _mark(site, False, None)
        return {'success': True, 'note': 'no vhost to clean'}
    path = _vhost_path(name)
    try:
        original = _read_vhost(path)
    except OSError:
        _mark(site, False, None)
        return {'success': True, 'note': 'vhost already gone'}
    stripped = _strip_block(original)
    if stripped != original:
        _write_vhost(path, stripped)
        test = _nginx_test()
        if not test.get('success'):
            _write_vhost(path, original)
            return {'success': False, 'error': 'nginx invalid after strip, reverted'}
        _nginx_reload()
    _mark(site, False, None)
    return {'success': True}


def _mark(site, injected, vhost):
    try:
        from app import db
        changes = {'nginx_injected': bool(injected)}
        if vhost and injected:
            changes['nginx_vhost'] = vhost
        site.update_settings(**changes)
        db.session.commit()
    except Exception as e:  # noqa: BLE001
        logger.debug('serverkit-analytics nginx _mark failed: %s', e)


def remove_all_injections():
    """Strip every nginx injection (called on uninstall)."""
    try:
        from .models import AnalyticsSite
    except Exception:  # noqa: BLE001
        return 0
    count = 0
    for site in AnalyticsSite.query.all():
        if site.get_settings().get('nginx_injected'):
            try:
                remove(site)
                count += 1
            except Exception as e:  # noqa: BLE001
                logger.debug('nginx remove_all skip %s: %s', site.id, e)
    return count
