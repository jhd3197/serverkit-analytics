"""Optional country-level geolocation for serverkit-analytics.

Off by default. When the operator enables ``geo_enabled`` AND drops a
GeoLite2-Country MMDB at ``geo_db_path``, we fill a 2-letter country code from
the client IP. No database ships with the panel (licensing), and no IP is ever
stored — only the derived country. Any failure (feature off, no DB, no
``geoip2`` package, private IP) yields ``None`` and the country column stays
null.
"""
import logging

logger = logging.getLogger(__name__)

# Cache one reader per DB path so we don't reopen the MMDB on every hit.
_readers = {}
_unavailable = set()


def _reader(path):
    if path in _unavailable:
        return None
    reader = _readers.get(path)
    if reader is not None:
        return reader
    try:
        import geoip2.database  # noqa: PLC0415 - optional dependency
        reader = geoip2.database.Reader(path)
        _readers[path] = reader
        return reader
    except Exception as e:  # noqa: BLE001 - missing package / bad file
        logger.debug('geoip2 reader unavailable for %s: %s', path, e)
        _unavailable.add(path)
        return None


def lookup_country(ip):
    """Return an ISO-3166-1 alpha-2 country code for ``ip``, or ``None``."""
    if not ip:
        return None
    try:
        from .config import cfg_bool, cfg_str
        if not cfg_bool('geo_enabled'):
            return None
        path = cfg_str('geo_db_path')
        if not path:
            return None
        import os
        if not os.path.exists(path):
            return None
        reader = _reader(path)
        if reader is None:
            return None
        resp = reader.country(ip)
        code = getattr(getattr(resp, 'country', None), 'iso_code', None)
        return code or None
    except Exception as e:  # noqa: BLE001 - private/unknown IP, lookup miss
        logger.debug('geo lookup miss for %s: %s', ip, e)
        return None


def reset():
    """Test/shutdown helper — drop cached readers."""
    for reader in _readers.values():
        try:
            reader.close()
        except Exception:  # noqa: BLE001
            pass
    _readers.clear()
    _unavailable.clear()
