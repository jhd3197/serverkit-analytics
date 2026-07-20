"""Ingestion for the serverkit-analytics collector.

The public ``POST /collect`` route is a thin wrapper; the real work lives here:

* **Visitor hashing** — cookieless identity is ``HMAC(daily_salt, site|ip|ua)``.
  The salt is generated in-process, rotates at UTC midnight, and is NEVER
  persisted. Raw IPs and UAs are hashed and discarded; only the digest is
  stored. A panel restart mid-day rotates the salt early (a documented,
  acceptable cookieless imprecision).
* **Bot filtering** — UA denylist + empty-UA rejection for JS-source hits.
* **Rate limiting** — an in-process token bucket per site_key and per client IP
  (single-worker safe, same constraint as the login throttle and the api
  self-telemetry buffer).
* **Buffering** — events accumulate in an in-memory buffer and a daemon thread
  batch-inserts them every ``buffer_flush_seconds`` (or early at ``buffer_max``),
  mirroring ``app/middleware/api_analytics.py``. Under TESTING the thread is not
  started; tests call :func:`flush_buffer` directly.

Client IP always comes from the plan-48 trusted-proxy helper
(``app.utils.client_ip.get_client_ip``), never ``request.remote_addr`` raw, so a
reverse proxy doesn't turn every visitor into the nginx box.
"""
import hashlib
import hmac
import logging
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .config import cfg_bool, cfg_int

logger = logging.getLogger(__name__)

# Hard body cap for a collect payload (bytes). Larger => 413.
MAX_BODY_BYTES = 8192

# --------------------------------------------------------------------------- #
# Daily-rotating salt (in-memory, never persisted)
# --------------------------------------------------------------------------- #
_salt_lock = threading.Lock()
_salt_day = None      # 'YYYY-MM-DD' (UTC) the current salt belongs to
_salt_value = None    # bytes


def _today_utc():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


def _current_salt():
    """Return today's salt, rotating (and discarding yesterday's) on day change."""
    global _salt_day, _salt_value
    day = _today_utc()
    with _salt_lock:
        if _salt_day != day or _salt_value is None:
            _salt_day = day
            _salt_value = secrets.token_bytes(32)
        return _salt_value


def visitor_hash(site_id, ip, ua):
    """Cookieless visitor id: HMAC(daily_salt, site|ip|ua), hex-truncated to 32."""
    msg = f'{site_id}|{ip or ""}|{ua or ""}'.encode('utf-8', 'ignore')
    digest = hmac.new(_current_salt(), msg, hashlib.sha256).hexdigest()
    return digest[:32]


# --------------------------------------------------------------------------- #
# Bot filtering + lightweight UA parsing (no heavy dependency)
# --------------------------------------------------------------------------- #
_BOT_RE = re.compile(
    r'(bot|crawl|spider|slurp|curl|wget|python-requests|python-urllib|'
    r'httpclient|headless|phantomjs|puppeteer|playwright|scrapy|libwww|'
    r'go-http-client|okhttp|axios|node-fetch|facebookexternalhit|'
    r'pingdom|uptimerobot|monitor|semrush|ahrefs|mj12bot|dotbot)',
    re.IGNORECASE,
)

_OS_PATTERNS = [
    ('Windows', re.compile(r'Windows NT', re.I)),
    ('iOS', re.compile(r'iPhone|iPad|iPod', re.I)),
    ('Android', re.compile(r'Android', re.I)),
    ('macOS', re.compile(r'Mac OS X|Macintosh', re.I)),
    ('Linux', re.compile(r'Linux', re.I)),
    ('Chrome OS', re.compile(r'CrOS', re.I)),
]
# Order matters: Edge/OPR/Samsung before Chrome, Chrome before Safari.
_BROWSER_PATTERNS = [
    ('Edge', re.compile(r'Edg[/A ]', re.I)),
    ('Opera', re.compile(r'OPR/|Opera', re.I)),
    ('Samsung Internet', re.compile(r'SamsungBrowser', re.I)),
    ('Firefox', re.compile(r'Firefox/', re.I)),
    ('Chrome', re.compile(r'Chrome/|CriOS', re.I)),
    ('Safari', re.compile(r'Safari/', re.I)),
]
_MOBILE_RE = re.compile(r'Mobile|iPhone|Android.*Mobile', re.I)
_TABLET_RE = re.compile(r'iPad|Tablet|Android(?!.*Mobile)', re.I)


def is_bot(ua):
    """True for known bot/automation user agents."""
    if not ua:
        return False  # emptiness handled separately (js-source empty UA rejected)
    return bool(_BOT_RE.search(ua))


def parse_ua(ua):
    """Return (browser_family, os_family, device_class) from a UA string.

    Deliberately tiny and heuristic — v1 accepts imperfection (no ua-parser
    dependency). Bots are classed device='bot'.
    """
    if not ua:
        return (None, None, None)
    if is_bot(ua):
        return ('Bot', None, 'bot')
    browser = next((name for name, rx in _BROWSER_PATTERNS if rx.search(ua)), 'Other')
    os_family = next((name for name, rx in _OS_PATTERNS if rx.search(ua)), 'Other')
    if _TABLET_RE.search(ua):
        device = 'tablet'
    elif _MOBILE_RE.search(ua):
        device = 'mobile'
    else:
        device = 'desktop'
    return (browser, os_family, device)


# --------------------------------------------------------------------------- #
# In-process token-bucket rate limiter (per site_key + per IP)
# --------------------------------------------------------------------------- #
_rl_lock = threading.Lock()
_rl_buckets = {}          # key -> [tokens, last_refill_epoch]
_rl_last_sweep = 0.0


def _sweep_buckets(now):
    """Drop idle buckets so the map can't grow unbounded."""
    global _rl_last_sweep
    if now - _rl_last_sweep < 300:
        return
    _rl_last_sweep = now
    stale = [k for k, (_, last) in _rl_buckets.items() if now - last > 600]
    for k in stale:
        _rl_buckets.pop(k, None)


def _allow_one(key, rate_per_min, now):
    """Token bucket: capacity == rate_per_min, refill rate == rate/60 per sec."""
    capacity = float(rate_per_min)
    refill = capacity / 60.0
    tokens, last = _rl_buckets.get(key, (capacity, now))
    tokens = min(capacity, tokens + (now - last) * refill)
    if tokens < 1.0:
        _rl_buckets[key] = (tokens, now)
        return False
    _rl_buckets[key] = (tokens - 1.0, now)
    return True


def rate_ok(site_key, ip):
    """Return True if this hit is under the per-key AND per-IP limits."""
    rate = cfg_int('collect_rate_per_min', minimum=1)
    now = time.monotonic()
    with _rl_lock:
        _sweep_buckets(now)
        ok_key = _allow_one(f'k:{site_key}', rate, now)
        ok_ip = _allow_one(f'i:{ip or "?"}', rate, now)
    return ok_key and ok_ip


def reset_rate_limits():
    """Test/shutdown helper."""
    with _rl_lock:
        _rl_buckets.clear()


# --------------------------------------------------------------------------- #
# Buffer + flush
# --------------------------------------------------------------------------- #
_buffer = []
_buffer_lock = threading.Lock()
_flush_started = False
_flush_lock = threading.Lock()
# Safety cap so a stalled flusher can't OOM the process on a traffic spike.
_HARD_BUFFER_CAP = 50000


def buffer_size():
    with _buffer_lock:
        return len(_buffer)


def reset_buffer():
    """Test/shutdown helper — drop any un-flushed events."""
    with _buffer_lock:
        _buffer.clear()


def record_event(entry):
    """Append a built event dict to the in-memory buffer (drops oldest on
    overflow so memory stays bounded)."""
    with _buffer_lock:
        _buffer.append(entry)
        if len(_buffer) > _HARD_BUFFER_CAP:
            drop = len(_buffer) - _HARD_BUFFER_CAP
            del _buffer[:drop]
            logger.warning('analytics buffer overflow; dropped %s oldest events', drop)


def flush_buffer(app):
    """Batch-insert buffered events. Returns the number written. Best-effort."""
    with _buffer_lock:
        if not _buffer:
            return 0
        entries = _buffer[:]
        _buffer.clear()
    from app import db
    from .models import AnalyticsEvent
    written = 0
    try:
        with app.app_context():
            for entry in entries:
                db.session.add(AnalyticsEvent(**entry))
                written += 1
            db.session.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning('analytics flush failed (%s events lost): %s', len(entries), e)
        try:
            db.session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return 0
    return written


def _flush_loop(app):
    """Daemon loop: flush on the configured interval OR early at buffer_max."""
    elapsed = 0
    while True:
        time.sleep(1)
        elapsed += 1
        interval = cfg_int('buffer_flush_seconds', minimum=1, maximum=3600)
        maxsize = cfg_int('buffer_max', minimum=1)
        if elapsed >= interval or buffer_size() >= maxsize:
            elapsed = 0
            try:
                flush_buffer(app)
            except Exception as e:  # noqa: BLE001
                logger.warning('analytics flush loop error: %s', e)


def ensure_flush_thread(app):
    """Start the background flush thread once (skipped under TESTING — tests
    flush synchronously)."""
    global _flush_started
    if app.config.get('TESTING') or app.config.get('ENV') == 'testing':
        return
    with _flush_lock:
        if _flush_started:
            return
        t = threading.Thread(target=_flush_loop, args=(app,),
                             daemon=True, name='analytics-flush')
        t.start()
        _flush_started = True


# --------------------------------------------------------------------------- #
# Payload validation + event build
# --------------------------------------------------------------------------- #
_VALID_TYPES = {'pageview', 'event', 'outlink', 'download'}
_SCREEN_CLASSES = {'xs', 'sm', 'md', 'lg', 'xl'}


def _clean_path(raw):
    """Normalize a URL path: strip origin, cap length, default '/'."""
    if not raw:
        return '/'
    raw = str(raw)
    # Accept a full URL or a bare path; keep only the path component.
    if '://' in raw:
        raw = urlsplit(raw).path or '/'
    raw = raw.split('#', 1)[0]
    if not raw.startswith('/'):
        raw = '/' + raw
    return raw[:512]


def referrer_host(raw):
    """Extract a bare host from a referrer URL (None if same-page/empty)."""
    if not raw:
        return None
    try:
        host = urlsplit(str(raw)).hostname
    except ValueError:
        return None
    return host[:255] if host else None


def build_event(site, payload, ip, ua):
    """Build a persisted-event dict from a validated collect payload.

    Returns ``None`` if the payload is unusable. Does NOT enforce bot/DNT/rate
    policy — that's the collector route's job (so it can pick the right status).
    """
    etype = str(payload.get('t') or payload.get('type') or 'pageview').lower()
    if etype not in _VALID_TYPES:
        etype = 'event'

    raw_path = payload.get('p') if payload.get('p') is not None else payload.get('path')
    path = _clean_path(raw_path)

    query = None
    if cfg_bool('store_query_strings'):
        q = payload.get('q') or payload.get('query')
        if q:
            query = str(q)[:512]
        elif raw_path and '?' in str(raw_path):
            query = str(raw_path).split('?', 1)[1][:512]

    browser, os_family, device = parse_ua(ua)

    screen = payload.get('s') or payload.get('screen')
    screen = screen if screen in _SCREEN_CLASSES else None

    lang = payload.get('l') or payload.get('lang')
    lang = str(lang)[:16] if lang else None

    load_ms = payload.get('ms') or payload.get('load_ms')
    try:
        load_ms = int(load_ms) if load_ms is not None else None
        if load_ms is not None and (load_ms < 0 or load_ms > 3600000):
            load_ms = None
    except (TypeError, ValueError):
        load_ms = None

    name = payload.get('n') or payload.get('name')
    name = str(name)[:128] if name else None

    return {
        'site_id': site.id,
        'ts': datetime.utcnow(),
        'type': etype,
        'event_name': name,
        'url_path': path,
        'url_query': query,
        'referrer_host': referrer_host(payload.get('r') or payload.get('referrer')),
        'visitor_hash': visitor_hash(site.id, ip, ua),
        'ua_family': browser,
        'os_family': os_family,
        'device_class': device,
        'screen_class': screen,
        'lang': lang,
        'country': _country(ip),  # None unless geo is enabled + a GeoLite2 DB is present
        'source': 'js',
        'load_ms': load_ms,
    }


def _country(ip):
    try:
        from .geo import lookup_country
        return lookup_country(ip)
    except Exception:  # noqa: BLE001
        return None
