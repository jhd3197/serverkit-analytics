"""Tracked-site CRUD for serverkit-analytics.

A ``site`` groups one or more hostnames under a public ``site_key`` (baked into
the tracker snippet). Mutations are admin-gated at the route layer; this module
is pure data logic and raises ``ValueError`` for bad input (routes translate to
400/404).
"""
from .models import AnalyticsSite, generate_site_key


def _normalize_hostnames(value):
    """Accept a list or a comma/newline string; return a newline-joined string."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        items = [str(v).strip() for v in value if str(v).strip()]
    else:
        items = [c.strip() for c in str(value).replace(',', '\n').splitlines()
                 if c.strip()]
    return '\n'.join(items) if items else None


def list_sites():
    return [s.to_dict() for s in AnalyticsSite.query.order_by(AnalyticsSite.id).all()]


def get_site(site_id):
    return AnalyticsSite.query.get(site_id)


def get_by_key(site_key):
    return AnalyticsSite.query.filter_by(site_key=site_key).first()


def create_site(data):
    from app import db
    name = (data.get('name') or '').strip()
    if not name:
        raise ValueError('name is required')
    site = AnalyticsSite(
        name=name[:255],
        hostnames=_normalize_hostnames(data.get('hostnames')),
        site_key=generate_site_key(),
        created_from=(data.get('created_from') or 'manual')[:32],
        app_id=data.get('app_id'),
        honor_dnt=data.get('honor_dnt'),
        allowed_origins=_normalize_hostnames(data.get('allowed_origins')),
        enabled=bool(data.get('enabled', True)),
    )
    if data.get('settings'):
        site.set_settings(data.get('settings'))
    db.session.add(site)
    db.session.commit()
    return site


def update_site(site_id, data):
    from app import db
    site = get_site(site_id)
    if not site:
        return None
    if 'name' in data:
        name = (data.get('name') or '').strip()
        if not name:
            raise ValueError('name cannot be blank')
        site.name = name[:255]
    if 'hostnames' in data:
        site.hostnames = _normalize_hostnames(data.get('hostnames'))
    if 'allowed_origins' in data:
        site.allowed_origins = _normalize_hostnames(data.get('allowed_origins'))
    if 'honor_dnt' in data:
        site.honor_dnt = data.get('honor_dnt')
    if 'enabled' in data:
        site.enabled = bool(data.get('enabled'))
    if 'app_id' in data:
        site.app_id = data.get('app_id')
    if 'settings' in data and isinstance(data.get('settings'), dict):
        site.update_settings(**data['settings'])
    db.session.commit()
    return site


def rotate_key(site_id):
    from app import db
    site = get_site(site_id)
    if not site:
        return None
    site.site_key = generate_site_key()
    db.session.commit()
    return site


def delete_site(site_id):
    """Delete a site and all of its data (events, rollups, log cursors)."""
    from app import db
    from .models import AnalyticsEvent, AnalyticsDaily, AnalyticsLogCursor
    site = get_site(site_id)
    if not site:
        return False
    AnalyticsEvent.query.filter_by(site_id=site_id).delete(synchronize_session=False)
    AnalyticsDaily.query.filter_by(site_id=site_id).delete(synchronize_session=False)
    AnalyticsLogCursor.query.filter_by(site_id=site_id).delete(synchronize_session=False)
    db.session.delete(site)
    db.session.commit()
    return True
