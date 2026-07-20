"""Data models for the serverkit-analytics (Web Analytics) extension.

Four tables, all namespaced ``ext_serverkit_analytics_*`` (dash -> underscore)
so ``--purge`` on uninstall drops exactly these:

* :class:`AnalyticsSite`   — a tracked site: name, hostnames, the public
  ``site_key`` baked into the tracker snippet (NOT a JWT), per-site settings.
* :class:`AnalyticsEvent`  — raw, append-only pageview/event rows (short
  retention). Visitor identity is a daily-rotating salted hash; raw IP/UA are
  never stored.
* :class:`AnalyticsDaily`  — the rollup time series (long retention), one row
  per (site, date, dimension, value).
* :class:`AnalyticsLogCursor` — per-site incremental server-log offsets so log
  ingestion is restart-safe.

Registration mirrors the serverkit-tramo / serverkit-k8s pattern: importing this
module defines the tables on the shared metadata as a side effect; the manifest's
``models: "models:register"`` then calls :func:`register` (a passthrough) and the
platform runs ``db.create_all()``.
"""
import json
import secrets
from datetime import datetime

from app import db


def generate_site_key():
    """A URL-safe public token embedded in the tracker snippet (not a secret in
    the JWT sense — it only names a site; abuse is handled by rate limits + bot
    filtering + origin allowlist)."""
    return secrets.token_urlsafe(16)


class AnalyticsSite(db.Model):
    __tablename__ = 'ext_serverkit_analytics_sites'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    # Newline/comma-normalized list of hostnames this site serves.
    hostnames = db.Column(db.Text, nullable=True)
    # Public, URL-safe token baked into the snippet. Unique + indexed for the
    # collector's hot-path lookup.
    site_key = db.Column(db.String(64), unique=True, nullable=False, index=True,
                         default=generate_site_key)

    # manual | wordpress | template | nginx
    created_from = db.Column(db.String(32), nullable=False, default='manual')
    # Optional link to a managed app/site id (WordPress site id, service id, ...).
    app_id = db.Column(db.Integer, nullable=True, index=True)

    # Per-site override of the global honor_dnt config. NULL = inherit global.
    honor_dnt = db.Column(db.Boolean, nullable=True)
    # Comma/newline list of allowed origins. Empty/NULL = reflect the request
    # origin after the site_key check (the default per the plan).
    allowed_origins = db.Column(db.Text, nullable=True)

    enabled = db.Column(db.Boolean, nullable=False, default=True)
    # Extensible per-site flags as JSON: wp_injected, nginx_injected,
    # log_ingestion, log_source_kind, log_source_ref, container_name, vhost_path…
    settings = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # ---- helpers ----
    def get_settings(self):
        if not self.settings:
            return {}
        try:
            return json.loads(self.settings)
        except (ValueError, TypeError):
            return {}

    def set_settings(self, value):
        self.settings = json.dumps(value or {})

    def update_settings(self, **changes):
        cur = self.get_settings()
        cur.update(changes)
        self.set_settings(cur)

    def hostname_list(self):
        return _split_list(self.hostnames)

    def allowed_origin_list(self):
        return _split_list(self.allowed_origins)

    def to_dict(self, include_key=True):
        out = {
            'id': self.id,
            'name': self.name,
            'hostnames': self.hostname_list(),
            'created_from': self.created_from,
            'app_id': self.app_id,
            'honor_dnt': self.honor_dnt,
            'allowed_origins': self.allowed_origin_list(),
            'enabled': self.enabled,
            'settings': self.get_settings(),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_key:
            out['site_key'] = self.site_key
        return out


class AnalyticsEvent(db.Model):
    __tablename__ = 'ext_serverkit_analytics_events'

    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, nullable=False, index=True)
    ts = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

    # pageview | event | outlink | download
    type = db.Column(db.String(16), nullable=False, default='pageview')
    event_name = db.Column(db.String(128), nullable=True)

    url_path = db.Column(db.String(512), nullable=True)
    url_query = db.Column(db.String(512), nullable=True)  # only if store_query_strings
    referrer_host = db.Column(db.String(255), nullable=True)

    # HMAC(daily_salt, site_id|ip|ua) — the cookieless visitor id. Never a raw IP.
    visitor_hash = db.Column(db.String(64), nullable=True, index=True)

    ua_family = db.Column(db.String(64), nullable=True)
    os_family = db.Column(db.String(64), nullable=True)
    device_class = db.Column(db.String(16), nullable=True)   # desktop|mobile|tablet|bot
    screen_class = db.Column(db.String(16), nullable=True)   # xs|sm|md|lg|xl
    lang = db.Column(db.String(16), nullable=True)
    country = db.Column(db.String(2), nullable=True)         # ISO-3166-1 alpha-2, optional

    source = db.Column(db.String(8), nullable=False, default='js')  # js | log
    load_ms = db.Column(db.Integer, nullable=True)

    __table_args__ = (
        db.Index('ix_ext_analytics_event_site_ts', 'site_id', 'ts'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'site_id': self.site_id,
            'ts': self.ts.isoformat() if self.ts else None,
            'type': self.type,
            'event_name': self.event_name,
            'url_path': self.url_path,
            'referrer_host': self.referrer_host,
            'ua_family': self.ua_family,
            'os_family': self.os_family,
            'device_class': self.device_class,
            'country': self.country,
            'source': self.source,
            'load_ms': self.load_ms,
        }


class AnalyticsDaily(db.Model):
    __tablename__ = 'ext_serverkit_analytics_daily'

    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, nullable=False, index=True)
    date = db.Column(db.Date, nullable=False, index=True)

    # overall | page | referrer | device | browser | os | country
    dim_type = db.Column(db.String(16), nullable=False)
    dim_value = db.Column(db.String(512), nullable=False, default='')

    visitors = db.Column(db.Integer, nullable=False, default=0)
    visits = db.Column(db.Integer, nullable=False, default=0)
    pageviews = db.Column(db.Integer, nullable=False, default=0)
    bounces = db.Column(db.Integer, nullable=False, default=0)
    avg_load_ms = db.Column(db.Float, nullable=True)

    __table_args__ = (
        db.UniqueConstraint('site_id', 'date', 'dim_type', 'dim_value',
                            name='uq_ext_analytics_daily_dim'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'site_id': self.site_id,
            'date': self.date.isoformat() if self.date else None,
            'dim_type': self.dim_type,
            'dim_value': self.dim_value,
            'visitors': self.visitors,
            'visits': self.visits,
            'pageviews': self.pageviews,
            'bounces': self.bounces,
            'avg_load_ms': self.avg_load_ms,
        }


class AnalyticsLogCursor(db.Model):
    __tablename__ = 'ext_serverkit_analytics_log_cursors'

    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, nullable=False, index=True)
    source_kind = db.Column(db.String(16), nullable=False, default='docker')  # docker|file
    source_ref = db.Column(db.String(512), nullable=False)  # container name or file path
    inode = db.Column(db.String(64), nullable=True)
    byte_offset = db.Column(db.BigInteger, nullable=False, default=0)
    last_ts = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('site_id', 'source_ref',
                            name='uq_ext_analytics_log_cursor'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'site_id': self.site_id,
            'source_kind': self.source_kind,
            'source_ref': self.source_ref,
            'byte_offset': self.byte_offset,
            'last_ts': self.last_ts.isoformat() if self.last_ts else None,
        }


def _split_list(text):
    """Split a comma/newline/whitespace-separated string into a clean list."""
    if not text:
        return []
    parts = []
    for chunk in str(text).replace('\r', '\n').replace(',', '\n').split('\n'):
        item = chunk.strip()
        if item:
            parts.append(item)
    return parts


def register(db):  # noqa: A002 - signature dictated by the platform (fn(db))
    """Passthrough required by the manifest ``models: "models:register"``.

    Importing this module already defined the tables on ``db.metadata``; the
    platform runs ``db.create_all()``. Returning the classes is convenient for
    tests.
    """
    return [AnalyticsSite, AnalyticsEvent, AnalyticsDaily, AnalyticsLogCursor]
