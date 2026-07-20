"""WordPress tracker injection for serverkit-analytics.

Writes a tiny mu-plugin into a managed WordPress site's
``wp-content/mu-plugins/`` so the tracker snippet is emitted in ``wp_head`` on
every page, with no user action and surviving theme switches. This is the v1
fallback until plan 51 ships a proper companion plugin via the Plugin Library;
both paths bake the same site_key.

Design:
* The WordPress *models* (``WordPressSite`` / ``Application``) are core, so this
  module works whenever a linked site exists; it does not import the
  serverkit-wordpress *service* (which lives in the extension).
* The single write seam is :func:`_docker_exec` (Docker sites) / a host write
  (bare-metal), so tests can mock it. Everything is best-effort and never raises
  out of the lifecycle/uninstall path.

Linux-only at runtime (docker exec / www-data chown); guarded so the Windows dev
box and tests behave.
"""
import logging
import os
import subprocess
from types import SimpleNamespace

logger = logging.getLogger(__name__)

SLUG = 'serverkit-analytics'
WP_SLUG = 'serverkit-wordpress'
MU_PLUGIN_REL = 'wp-content/mu-plugins/serverkit-analytics.php'
CONTAINER_WEBROOT = '/var/www/html'


def wordpress_available():
    """True when the serverkit-wordpress extension is installed + active."""
    try:
        from app.models.plugin import InstalledPlugin
        row = InstalledPlugin.query.filter_by(slug=WP_SLUG).first()
        return bool(row and row.status == InstalledPlugin.STATUS_ACTIVE)
    except Exception:  # noqa: BLE001
        return False


def build_mu_plugin_php(site_key, tracker_url):
    """Return the mu-plugin PHP that emits the tracker snippet in wp_head."""
    src = tracker_url.replace("'", '')
    key = str(site_key).replace("'", '')
    return (
        "<?php\n"
        "/**\n"
        " * Plugin Name: ServerKit Analytics\n"
        " * Description: Injects the privacy-first ServerKit Analytics tracker. "
        "Managed by the ServerKit panel; do not edit by hand.\n"
        " * Version: 1.0.0\n"
        " */\n"
        "if (!defined('ABSPATH')) { exit; }\n"
        "add_action('wp_head', function () {\n"
        f"    $src = '{src}';\n"
        f"    $key = '{key}';\n"
        "    echo '<script defer src=\"' . esc_url($src) . '\" data-site-key=\"'"
        " . esc_attr($key) . '\"></script>' . \"\\n\";\n"
        "}, 1);\n"
    )


def _docker_exec(container, argv, input_text=None):
    """Run ``docker exec [-i] <container> <argv...>``. Mockable seam."""
    cmd = ['docker', 'exec']
    if input_text is not None:
        cmd.append('-i')
    cmd += [container] + list(argv)
    try:
        result = subprocess.run(
            cmd, input=input_text, capture_output=True, text=True, timeout=30)
        return {'success': result.returncode == 0,
                'output': result.stdout,
                'error': result.stderr if result.returncode != 0 else None}
    except Exception as e:  # noqa: BLE001
        return {'success': False, 'error': str(e)}


def _resolve_target(site):
    """Resolve the WordPress deployment behind an analytics site.

    Returns a namespace ``(container, is_docker, root)`` or ``None`` when no
    linked WordPress site/application can be found.
    """
    if not site or not site.app_id:
        return None
    try:
        from app.models.wordpress_site import WordPressSite
        from app.models import Application
    except Exception:  # noqa: BLE001
        return None
    wp = WordPressSite.query.get(site.app_id)
    if not wp:
        return None
    app = Application.query.get(wp.application_id)
    if not app:
        return None
    root = getattr(app, 'root_path', None)
    is_docker = bool(root and os.path.exists(os.path.join(root, 'docker-compose.yml')))
    return SimpleNamespace(container=app.name, is_docker=is_docker, root=root)


def _write_mu_plugin(target, content):
    if target.is_docker:
        _docker_exec(target.container,
                     ['mkdir', '-p', f'{CONTAINER_WEBROOT}/wp-content/mu-plugins'])
        return _docker_exec(target.container,
                            ['tee', f'{CONTAINER_WEBROOT}/{MU_PLUGIN_REL}'],
                            input_text=content)
    if not target.root:
        return {'success': False, 'error': 'no site root'}
    full = os.path.join(target.root, *MU_PLUGIN_REL.split('/'))
    try:
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w', encoding='utf-8') as f:
            f.write(content)
    except OSError as e:
        return {'success': False, 'error': str(e)}
    if os.name != 'nt':
        try:
            from app.utils.system import run_privileged
            run_privileged(['chown', 'www-data:www-data', full])
        except Exception:  # noqa: BLE001
            pass
    return {'success': True}


def _remove_mu_plugin(target):
    if target.is_docker:
        return _docker_exec(target.container,
                            ['rm', '-f', f'{CONTAINER_WEBROOT}/{MU_PLUGIN_REL}'])
    if not target.root:
        return {'success': True}
    full = os.path.join(target.root, *MU_PLUGIN_REL.split('/'))
    try:
        if os.path.exists(full):
            os.remove(full)
    except OSError as e:
        return {'success': False, 'error': str(e)}
    return {'success': True}


def inject(site, tracker_url):
    """Write the mu-plugin for a WordPress-linked analytics site."""
    target = _resolve_target(site)
    if not target:
        return {'success': False, 'error': 'No linked WordPress site found'}
    php = build_mu_plugin_php(site.site_key, tracker_url)
    result = _write_mu_plugin(target, php)
    if result.get('success'):
        _mark(site, True, target)
    return result


def remove(site):
    """Remove the mu-plugin and clear the injection flag."""
    target = _resolve_target(site)
    result = _remove_mu_plugin(target) if target else {'success': True}
    _mark(site, False, target)
    return result


def _mark(site, injected, target):
    try:
        from app import db
        changes = {'wp_injected': bool(injected)}
        if target and injected:
            changes['wp_container'] = target.container
        site.update_settings(**changes)
        db.session.commit()
    except Exception as e:  # noqa: BLE001
        logger.debug('serverkit-analytics wp _mark failed: %s', e)


def remove_all_injections():
    """Remove every WordPress mu-plugin injection (called on uninstall)."""
    try:
        from .models import AnalyticsSite
    except Exception:  # noqa: BLE001
        return 0
    count = 0
    for site in AnalyticsSite.query.all():
        if site.get_settings().get('wp_injected'):
            try:
                remove(site)
                count += 1
            except Exception as e:  # noqa: BLE001
                logger.debug('wp remove_all skip %s: %s', site.id, e)
    return count
