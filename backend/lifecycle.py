"""Install / upgrade / uninstall lifecycle hooks (serverkit-analytics).

Contract (per the plugin SDK): a single positional arg — the InstalledPlugin
row; ``on_uninstall`` also accepts a ``purge`` flag. Everything here is wrapped
so a hook failure never blocks install/uninstall.

* ``on_install``   — seed config defaults onto the row so they show pre-filled
                     in the Configure dialog; register notification events.
* ``on_upgrade``   — re-seed any newly-added config keys (schema evolves without
                     Alembic; ``register_models`` + ``db.create_all`` handles new
                     tables, this backfills new config defaults).
* ``on_uninstall`` — best-effort cleanup of injected trackers (WordPress
                     mu-plugin / nginx sub_filter) so we don't leave dead
                     snippets behind. The ``ext_serverkit_analytics_*`` tables
                     are dropped by the platform on ``--purge``.
"""
import logging

logger = logging.getLogger(__name__)


def _seed_config_defaults(plugin, overwrite=False):
    """Merge DEFAULTS into the plugin's saved config so the Configure UI is
    pre-filled. ``overwrite=False`` never clobbers an admin's saved value."""
    try:
        from app import db
        from .config import DEFAULTS
        merged = dict(plugin.config or {})
        changed = False
        for key, value in DEFAULTS.items():
            if overwrite or key not in merged:
                if merged.get(key) != value:
                    merged[key] = value
                    changed = True
        if changed:
            plugin.config = merged
            db.session.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning('serverkit-analytics config seed failed: %s', e)


def _register_events():
    try:
        from app.plugins_sdk import notify
        notify.register_event(
            'analytics.collector_error',
            'The web-analytics collector hit repeated errors',
            template='generic', severity='warning', category='system')
        notify.register_event(
            'analytics.site_injection_failed',
            'Injecting the analytics tracker into a site failed',
            template='generic', severity='warning', category='system')
    except Exception as e:  # noqa: BLE001
        logger.debug('serverkit-analytics event registration failed: %s', e)


def on_install(plugin):
    """Seed config defaults; register notification events."""
    _seed_config_defaults(plugin, overwrite=False)
    _register_events()
    logger.info('serverkit-analytics installed (config seeded, events registered)')


def on_upgrade(plugin):
    """Backfill newly-added config defaults on an in-place version change."""
    _seed_config_defaults(plugin, overwrite=False)
    _register_events()
    logger.info('serverkit-analytics upgraded')


def on_uninstall(plugin, purge=False):
    """Remove any tracker injections we performed. Data tables are dropped by
    the platform on --purge; this only unwinds the site-side snippets."""
    try:
        from . import wp_integration
        removed = wp_integration.remove_all_injections()
        if removed:
            logger.info('serverkit-analytics: removed %s WP injection(s)', removed)
    except Exception as e:  # noqa: BLE001
        logger.debug('serverkit-analytics WP injection cleanup skipped: %s', e)

    try:
        from . import nginx_integration
        removed = nginx_integration.remove_all_injections()
        if removed:
            logger.info('serverkit-analytics: removed %s nginx injection(s)', removed)
    except Exception as e:  # noqa: BLE001
        logger.debug('serverkit-analytics nginx injection cleanup skipped: %s', e)

    logger.info('serverkit-analytics uninstalled (purge=%s)', purge)
