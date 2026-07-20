"""Read-side report queries for the serverkit-analytics dashboard.

Everything the dashboard renders comes from the ``daily`` rollup table (fast,
long-retention) EXCEPT the realtime tab, which reads the raw ``event`` table for
the last N minutes.

Range caveat (documented in docs/ANALYTICS.md): "unique visitors" over a
multi-day range is the SUM of each day's uniques — a visitor active on two days
counts twice. This is inherent to daily rollups and, combined with cookieless
hashing, means visitor counts are close approximations, not exact identities.
Pageviews sum exactly.
"""
from datetime import date, datetime, timedelta

_RANGE_DAYS = {'1d': 1, '7d': 7, '14d': 14, '30d': 30, '90d': 90, '180d': 180,
               '365d': 365, '12mo': 365, '13mo': 396}


def parse_range(args):
    """Resolve (start_date, end_date) from query args.

    Accepts ``?range=7d`` (default 7d) or explicit ``?start=YYYY-MM-DD&
    end=YYYY-MM-DD``. End defaults to today (UTC), inclusive.
    """
    today = datetime.utcnow().date()
    start_raw = (args.get('start') or '').strip()
    end_raw = (args.get('end') or '').strip()
    if start_raw:
        try:
            start = datetime.strptime(start_raw[:10], '%Y-%m-%d').date()
            end = (datetime.strptime(end_raw[:10], '%Y-%m-%d').date()
                   if end_raw else today)
            if end < start:
                start, end = end, start
            return start, end
        except ValueError:
            pass
    days = _RANGE_DAYS.get((args.get('range') or '7d').strip(), 7)
    return today - timedelta(days=days - 1), today


def _overall_query(site_id, start, end):
    from .models import AnalyticsDaily
    return (AnalyticsDaily.query
            .filter(AnalyticsDaily.site_id == site_id,
                    AnalyticsDaily.dim_type == 'overall',
                    AnalyticsDaily.date >= start,
                    AnalyticsDaily.date <= end))


def _totals(site_id, start, end):
    from app import db
    from sqlalchemy import func
    from .models import AnalyticsDaily
    row = (db.session.query(
        func.coalesce(func.sum(AnalyticsDaily.visitors), 0),
        func.coalesce(func.sum(AnalyticsDaily.pageviews), 0),
        func.coalesce(func.sum(AnalyticsDaily.bounces), 0),
        func.avg(AnalyticsDaily.avg_load_ms),
    ).filter(AnalyticsDaily.site_id == site_id,
             AnalyticsDaily.dim_type == 'overall',
             AnalyticsDaily.date >= start,
             AnalyticsDaily.date <= end).one())
    visitors, pageviews, bounces, avg_load = row
    bounce_rate = round(100.0 * bounces / visitors, 1) if visitors else 0.0
    return {
        'visitors': int(visitors or 0),
        'pageviews': int(pageviews or 0),
        'bounces': int(bounces or 0),
        'bounce_rate': bounce_rate,
        'avg_load_ms': round(avg_load, 1) if avg_load is not None else None,
    }


def timeseries(site_id, start, end):
    """Per-day visitors + pageviews across the range, zero-filled."""
    rows = {r.date: r for r in _overall_query(site_id, start, end).all()}
    out = []
    d = start
    while d <= end:
        r = rows.get(d)
        out.append({
            'date': d.isoformat(),
            'visitors': r.visitors if r else 0,
            'pageviews': r.pageviews if r else 0,
        })
        d += timedelta(days=1)
    return out


def _dim_table(site_id, start, end, dim_type, limit=None):
    from app import db
    from sqlalchemy import func
    from .models import AnalyticsDaily
    q = (db.session.query(
        AnalyticsDaily.dim_value,
        func.sum(AnalyticsDaily.visitors).label('visitors'),
        func.sum(AnalyticsDaily.pageviews).label('pageviews'),
        func.avg(AnalyticsDaily.avg_load_ms).label('avg_load'),
    ).filter(AnalyticsDaily.site_id == site_id,
             AnalyticsDaily.dim_type == dim_type,
             AnalyticsDaily.date >= start,
             AnalyticsDaily.date <= end)
        .group_by(AnalyticsDaily.dim_value)
        .order_by(func.sum(AnalyticsDaily.pageviews).desc()))
    if limit:
        q = q.limit(limit)
    return [{
        'value': value or '',
        'visitors': int(visitors or 0),
        'pageviews': int(pageviews or 0),
        'avg_load_ms': round(avg_load, 1) if avg_load is not None else None,
    } for value, visitors, pageviews, avg_load in q.all()]


def realtime(site_id, minutes=30):
    """Live view from RAW events (last N minutes): active visitors, pageviews,
    and the most recent hits."""
    from .models import AnalyticsEvent
    minutes = max(1, min(int(minutes or 30), 1440))
    since = datetime.utcnow() - timedelta(minutes=minutes)
    q = (AnalyticsEvent.query
         .filter(AnalyticsEvent.site_id == site_id, AnalyticsEvent.ts >= since))
    events = q.order_by(AnalyticsEvent.ts.desc()).limit(100).all()
    active = {e.visitor_hash for e in q.all() if e.visitor_hash}
    return {
        'minutes': minutes,
        'active_visitors': len(active),
        'pageviews': q.filter(AnalyticsEvent.type == 'pageview').count(),
        'recent': [{
            'ts': e.ts.isoformat() if e.ts else None,
            'path': e.url_path,
            'referrer_host': e.referrer_host,
            'device_class': e.device_class,
            'country': e.country,
        } for e in events],
    }


def overview(site_id, start, end):
    """Headline KPIs + sparkline + top pages/referrers + a live counter."""
    return {
        'range': {'start': start.isoformat(), 'end': end.isoformat()},
        'totals': _totals(site_id, start, end),
        'timeseries': timeseries(site_id, start, end),
        'top_pages': _dim_table(site_id, start, end, 'page', limit=8),
        'top_referrers': _dim_table(site_id, start, end, 'referrer', limit=8),
        'realtime': realtime(site_id, minutes=30),
    }


def pages(site_id, start, end, limit=200):
    return {'range': {'start': start.isoformat(), 'end': end.isoformat()},
            'rows': _dim_table(site_id, start, end, 'page', limit=limit)}


def referrers(site_id, start, end, limit=200):
    return {'range': {'start': start.isoformat(), 'end': end.isoformat()},
            'rows': _dim_table(site_id, start, end, 'referrer', limit=limit)}


def devices(site_id, start, end):
    return {
        'range': {'start': start.isoformat(), 'end': end.isoformat()},
        'device': _dim_table(site_id, start, end, 'device'),
        'browser': _dim_table(site_id, start, end, 'browser'),
        'os': _dim_table(site_id, start, end, 'os'),
        'country': _dim_table(site_id, start, end, 'country'),
    }
