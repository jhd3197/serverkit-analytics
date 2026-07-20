"""Server-log ingestion for serverkit-analytics.

A complementary, script-free source: parse apache/nginx **combined** access logs
into the same event table as the JS tracker, tagged ``source='log'`` so JS-source
and log-source hits stay comparable. Generalizes the on-demand
``WpAnalyticsService.get_traffic`` parse into a persistent, incremental one.

Per-site opt-in via ``site.settings``:
    log_ingestion   : bool   (enable)
    log_source_kind : 'file' | 'docker'
    log_source_ref  : file path (file) or container name (docker)

Incrementality uses :class:`AnalyticsLogCursor`:
* file   — (inode, byte_offset); a changed inode or shrunken file resets to 0
           (rotation/truncation safe).
* docker — ``docker logs --since <last_ts>`` with a ts-based skip, since docker
           logs has no byte offset.

Only page-like GET hits are counted (assets and non-GET are skipped) and bots
are dropped, mirroring the JS collector so the two sources line up. Visitor
hashing reuses the collector's daily salt; log lines older than today are hashed
with today's salt (a documented cookieless imprecision — tail runs every few
minutes, so this is the rare backfill case).

Linux-only at runtime (docker logs / access-log files); reading seams are
mockable for tests.
"""
import logging
import os
import re
import subprocess
from datetime import datetime

from .config import cfg_bool
from .ingest_service import is_bot, parse_ua, referrer_host, visitor_hash

logger = logging.getLogger(__name__)

# apache/nginx "combined" log format:
#   %h %l %u %t "%r" %>s %b "%{Referer}i" "%{User-Agent}i"
COMBINED_RE = re.compile(
    r'(?P<ip>\S+) \S+ \S+ \[(?P<time>[^\]]+)\] "(?P<request>[^"]*)" '
    r'(?P<status>\d{3}) (?P<bytes>\S+) "(?P<referer>[^"]*)" "(?P<ua>[^"]*)"'
)
_TIME_FMT = '%d/%b/%Y:%H:%M:%S %z'

# Skip static assets so log hits approximate real pageviews.
_ASSET_RE = re.compile(
    r'\.(?:css|js|mjs|png|jpe?g|gif|svg|webp|ico|bmp|woff2?|ttf|eot|otf|map|'
    r'json|xml|txt|mp4|webm|mp3|wav|pdf|zip|gz)(?:$|\?)', re.IGNORECASE)

# Cap lines processed per source per run so a huge backlog can't stall a job.
MAX_LINES_PER_RUN = 20000


def parse_combined_line(line):
    """Parse one combined-log line into a dict, or ``None`` if it doesn't match
    or isn't a page-like GET."""
    m = COMBINED_RE.match(line.strip())
    if not m:
        return None
    request = m.group('request') or ''
    parts = request.split()
    if len(parts) < 2 or parts[0].upper() != 'GET':
        return None
    path = parts[1].split('#', 1)[0]
    if _ASSET_RE.search(path):
        return None
    try:
        ts = datetime.strptime(m.group('time'), _TIME_FMT)
        ts = ts.replace(tzinfo=None) - _utc_offset(ts)
    except (ValueError, TypeError):
        ts = datetime.utcnow()
    referer = m.group('referer')
    return {
        'ip': m.group('ip'),
        'ts': ts,
        'path': path.split('?', 1)[0][:512],
        'status': m.group('status'),
        'referer': None if referer in ('', '-') else referer,
        'ua': m.group('ua') or '',
    }


def _utc_offset(dt):
    from datetime import timedelta
    off = dt.utcoffset()
    return off if off is not None else timedelta(0)


def build_log_event(site, parsed):
    """Build a persisted-event dict from a parsed log line (source='log')."""
    ua = parsed['ua']
    if not ua or is_bot(ua):
        return None
    browser, os_family, device = parse_ua(ua)
    country = None
    try:
        from .geo import lookup_country
        country = lookup_country(parsed['ip'])
    except Exception:  # noqa: BLE001
        country = None
    return {
        'site_id': site.id,
        'ts': parsed['ts'],
        'type': 'pageview',
        'event_name': None,
        'url_path': parsed['path'],
        'url_query': None,
        'referrer_host': referrer_host(parsed['referer']),
        'visitor_hash': visitor_hash(site.id, parsed['ip'], ua),
        'ua_family': browser,
        'os_family': os_family,
        'device_class': device,
        'screen_class': None,
        'lang': None,
        'country': country,
        'source': 'log',
        'load_ms': None,
    }


def ingest_lines(site, lines, since_ts=None):
    """Parse + persist log lines for a site. Returns (written, max_ts)."""
    from app import db
    from .models import AnalyticsEvent
    written = 0
    max_ts = since_ts
    for line in lines[:MAX_LINES_PER_RUN]:
        parsed = parse_combined_line(line)
        if not parsed:
            continue
        if since_ts and parsed['ts'] <= since_ts:
            continue  # already ingested (docker --since boundary)
        event = build_log_event(site, parsed)
        if not event:
            continue
        db.session.add(AnalyticsEvent(**event))
        written += 1
        if max_ts is None or parsed['ts'] > max_ts:
            max_ts = parsed['ts']
    if written:
        db.session.commit()
    return written, max_ts


# --------------------------------------------------------------------------- #
# source readers (mockable)
# --------------------------------------------------------------------------- #
def _read_file_lines(cursor, path):
    """Return new lines from a file, advancing the (inode, byte_offset) cursor."""
    try:
        st = os.stat(path)
    except OSError:
        return []
    inode = str(getattr(st, 'st_ino', '') or '')
    offset = cursor.byte_offset or 0
    if cursor.inode and inode and cursor.inode != inode:
        offset = 0  # rotated
    if offset > st.st_size:
        offset = 0  # truncated
    try:
        with open(path, 'rb') as f:
            f.seek(offset)
            data = f.read()
            new_offset = f.tell()
    except OSError:
        return []
    cursor.inode = inode
    cursor.byte_offset = new_offset
    return data.decode('utf-8', 'ignore').splitlines()


def _read_docker_lines(cursor, container):
    """Return recent access-log lines from a container's stdout."""
    args = ['docker', 'logs']
    if cursor.last_ts:
        args += ['--since', cursor.last_ts.strftime('%Y-%m-%dT%H:%M:%S')]
    else:
        args += ['--tail', '2000']
    args.append(container)
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=25)
    except Exception as e:  # noqa: BLE001
        logger.debug('docker logs failed for %s: %s', container, e)
        return []
    return (result.stdout or '').splitlines()


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #
def _get_cursor(site_id, source_ref, source_kind):
    from app import db
    from .models import AnalyticsLogCursor
    cur = AnalyticsLogCursor.query.filter_by(
        site_id=site_id, source_ref=source_ref).first()
    if not cur:
        cur = AnalyticsLogCursor(site_id=site_id, source_ref=source_ref,
                                 source_kind=source_kind, byte_offset=0)
        db.session.add(cur)
        db.session.commit()
    return cur


def ingest_site(site):
    """Ingest new log lines for one site. Returns a status dict."""
    settings = site.get_settings()
    if not settings.get('log_ingestion'):
        return {'skipped': True, 'reason': 'log ingestion off for site'}
    kind = settings.get('log_source_kind') or 'docker'
    ref = settings.get('log_source_ref')
    if not ref:
        return {'skipped': True, 'reason': 'no log source configured'}

    cursor = _get_cursor(site.id, ref, kind)
    if kind == 'file':
        # File dedup is by byte offset, so DON'T also filter by ts (that would
        # drop same-second appends that the offset already advanced past).
        lines = _read_file_lines(cursor, ref)
        since = None
    else:
        # docker logs --since re-reads the boundary second; ts-filter dedups it.
        lines = _read_docker_lines(cursor, ref)
        since = cursor.last_ts

    written, max_ts = ingest_lines(site, lines, since_ts=since)
    if max_ts:
        cursor.last_ts = max_ts
    from app import db
    cursor.updated_at = datetime.utcnow()
    db.session.commit()
    return {'site_id': site.id, 'written': written, 'lines': len(lines)}


def run_log_tail():
    """Ingest logs for every opted-in site (called by the scheduled job)."""
    if not cfg_bool('log_ingestion_enabled'):
        return {'skipped': True, 'reason': 'log ingestion globally disabled'}
    from .models import AnalyticsSite
    total = 0
    sites = 0
    for site in AnalyticsSite.query.filter_by(enabled=True).all():
        if not site.get_settings().get('log_ingestion'):
            continue
        try:
            res = ingest_site(site)
            total += res.get('written', 0)
            sites += 1
        except Exception as e:  # noqa: BLE001
            logger.warning('log ingest failed for site %s: %s', site.id, e)
    return {'sites': sites, 'written': total}
