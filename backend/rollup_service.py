"""Rollups + retention for serverkit-analytics.

* :func:`run_retention_prune` — drop raw ``event`` rows older than
  ``raw_retention_days`` and ``daily`` rollups older than
  ``rollup_retention_months``. Implemented in Phase 1 (operates on the event
  table that exists from Phase 1; the daily table is pruned harmlessly while
  empty until Phase 2 populates it).
* :func:`run_rollup` — aggregate raw events into the daily table. Implemented in
  Phase 2.

Both are called by the scheduled jobs in ``jobs.py`` and must be safe to call
repeatedly (idempotent) and outside a request context (they open their own
session via the shared ``db``).
"""
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def run_retention_prune():
    """Delete raw events + rollups past their retention windows. Idempotent."""
    from app import db
    from .config import cfg_int
    from .models import AnalyticsEvent, AnalyticsDaily

    raw_days = cfg_int('raw_retention_days', minimum=1, maximum=3650)
    rollup_months = cfg_int('rollup_retention_months', minimum=1, maximum=120)

    event_cutoff = datetime.utcnow() - timedelta(days=raw_days)
    daily_cutoff = (datetime.utcnow() - timedelta(days=rollup_months * 31)).date()

    deleted_events = (AnalyticsEvent.query
                      .filter(AnalyticsEvent.ts < event_cutoff)
                      .delete(synchronize_session=False))
    deleted_daily = (AnalyticsDaily.query
                     .filter(AnalyticsDaily.date < daily_cutoff)
                     .delete(synchronize_session=False))
    db.session.commit()

    return {
        'deleted_events': int(deleted_events or 0),
        'deleted_daily': int(deleted_daily or 0),
        'event_cutoff': event_cutoff.isoformat(),
        'daily_cutoff': daily_cutoff.isoformat(),
    }


def _as_date(value):
    """Normalize a grouped day value (SQLite returns a str, PG a date)."""
    if isinstance(value, str):
        return datetime.strptime(value[:10], '%Y-%m-%d').date()
    if isinstance(value, datetime):
        return value.date()
    return value


# Dimension rollups: (dim_type, event column attribute, skip-null?).
_DIMENSIONS = [
    ('page', 'url_path', False),
    ('referrer', 'referrer_host', True),
    ('device', 'device_class', False),
    ('browser', 'ua_family', False),
    ('os', 'os_family', False),
    ('country', 'country', True),
]


def run_rollup():
    """Aggregate raw pageview events into the ``daily`` rollup table.

    Idempotent and backfill-safe: recomputes every (site, date) that currently
    has raw events (bounded by ``raw_retention_days``), deleting and re-inserting
    that day's rollup rows. Aggregation runs DB-side (grouped queries) so only
    summary rows — not raw events — reach Python; on a very busy SQLite panel the
    per-hour recompute is the documented pressure point (recommend PostgreSQL).

    Only ``type == 'pageview'`` events feed the rollup in v1; outlink/download/
    custom events are stored but not summarized here.
    """
    from app import db
    from sqlalchemy import distinct, func
    from .models import AnalyticsDaily, AnalyticsEvent

    day = func.date(AnalyticsEvent.ts)
    pageview = AnalyticsEvent.type == 'pageview'

    pairs = (db.session.query(AnalyticsEvent.site_id, day)
             .filter(pageview).distinct().all())
    date_set = {(sid, _as_date(d)) for sid, d in pairs}
    if not date_set:
        return {'sites_dates': 0, 'rows': 0}

    # Clear the days we're about to recompute (keeps late arrivals correct).
    for sid, d in date_set:
        AnalyticsDaily.query.filter_by(site_id=sid, date=d).delete(
            synchronize_session=False)

    rows = 0

    # --- overall (per site+day) + bounce (single-pageview visitors) ---
    overall = (db.session.query(
        AnalyticsEvent.site_id, day.label('d'),
        func.count(distinct(AnalyticsEvent.visitor_hash)).label('visitors'),
        func.count().label('pageviews'),
        func.avg(AnalyticsEvent.load_ms).label('avg_load'),
    ).filter(pageview).group_by(AnalyticsEvent.site_id, day).all())

    per_visitor = (db.session.query(
        AnalyticsEvent.site_id, day.label('d'), AnalyticsEvent.visitor_hash,
        func.count().label('c'),
    ).filter(pageview)
        .group_by(AnalyticsEvent.site_id, day, AnalyticsEvent.visitor_hash)
        .subquery())
    bounce_rows = (db.session.query(
        per_visitor.c.site_id, per_visitor.c.d, func.count().label('b'))
        .filter(per_visitor.c.c == 1)
        .group_by(per_visitor.c.site_id, per_visitor.c.d).all())
    bounce_map = {(sid, _as_date(d)): b for sid, d, b in bounce_rows}

    for sid, d, visitors, pageviews, avg_load in overall:
        dd = _as_date(d)
        db.session.add(AnalyticsDaily(
            site_id=sid, date=dd, dim_type='overall', dim_value='',
            visitors=visitors or 0, visits=visitors or 0,
            pageviews=pageviews or 0, bounces=bounce_map.get((sid, dd), 0),
            avg_load_ms=avg_load))
        rows += 1

    # --- dimension rollups ---
    for dim_type, attr, skip_null in _DIMENSIONS:
        col = getattr(AnalyticsEvent, attr)
        q = db.session.query(
            AnalyticsEvent.site_id, day.label('d'), col.label('v'),
            func.count(distinct(AnalyticsEvent.visitor_hash)).label('visitors'),
            func.count().label('pageviews'),
            func.avg(AnalyticsEvent.load_ms).label('avg_load'),
        ).filter(pageview)
        if skip_null:
            q = q.filter(col.isnot(None))
        q = q.group_by(AnalyticsEvent.site_id, day, col)
        for sid, d, value, visitors, pageviews, avg_load in q.all():
            db.session.add(AnalyticsDaily(
                site_id=sid, date=_as_date(d), dim_type=dim_type,
                dim_value=(str(value) if value is not None else '')[:512],
                visitors=visitors or 0, visits=visitors or 0,
                pageviews=pageviews or 0, bounces=0, avg_load_ms=avg_load))
            rows += 1

    db.session.commit()
    return {'sites_dates': len(date_set), 'rows': rows}
