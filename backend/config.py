"""Config defaults + accessor for the serverkit-analytics extension.

The manifest's ``config_schema`` documents the fields an admin can edit
(Marketplace → Installed → Configure), but the platform does not auto-apply the
schema's ``default`` values — it just stores whatever the admin saves. So the
canonical defaults live here, and :func:`get_cfg` merges the admin-saved values
(read via the plugin SDK) over these defaults. Every backend module reads config
through :func:`get_cfg` / :func:`cfg_*` so a fresh install behaves correctly
before anyone opens the Configure dialog.

``lifecycle.on_install`` also seeds these defaults onto the InstalledPlugin row
so they show up pre-filled in the Configure UI.
"""

SLUG = 'serverkit-analytics'

# Keep in sync with plugin.json ``config_schema`` defaults.
DEFAULTS = {
    'raw_retention_days': 30,
    'rollup_retention_months': 13,
    'honor_dnt': True,
    'geo_enabled': False,
    'geo_db_path': '',
    'collect_rate_per_min': 600,
    'buffer_flush_seconds': 5,
    'buffer_max': 100,
    'store_query_strings': False,
    'log_ingestion_enabled': True,
}


def get_cfg():
    """Return the effective config: admin-saved values merged over DEFAULTS.

    Safe outside an app/request context (returns a copy of DEFAULTS) so module
    import and background threads never explode.
    """
    merged = dict(DEFAULTS)
    try:
        from app.plugins_sdk import config as plugin_config
        saved = plugin_config(SLUG) or {}
    except Exception:  # noqa: BLE001 - no app context / plugin row yet
        saved = {}
    for key in DEFAULTS:
        if key in saved and saved[key] is not None:
            merged[key] = saved[key]
    return merged


def cfg_int(key, minimum=None, maximum=None):
    """Read an integer config value, clamped and default-safe."""
    try:
        val = int(get_cfg().get(key, DEFAULTS.get(key)))
    except (TypeError, ValueError):
        val = int(DEFAULTS.get(key, 0))
    if minimum is not None:
        val = max(minimum, val)
    if maximum is not None:
        val = min(maximum, val)
    return val


def cfg_bool(key):
    """Read a boolean config value (accepts real bools and truthy strings)."""
    val = get_cfg().get(key, DEFAULTS.get(key))
    if isinstance(val, str):
        return val.strip().lower() in ('1', 'true', 'yes', 'on')
    return bool(val)


def cfg_str(key):
    """Read a string config value."""
    val = get_cfg().get(key, DEFAULTS.get(key))
    return '' if val is None else str(val)
