"""ServerKit Web Analytics extension backend package.

Privacy-first, self-hosted web analytics for the sites this panel manages. A
lightweight cookieless JS tracker (and optional server-log ingestion) feed a
persistent time series stored on the panel's own database; a dashboard inside
the panel renders visitors / pageviews / referrers / devices / realtime.

The blueprint (``analytics_bp``) is the manifest ``entry_point``; everything
else (ingestion buffer, rollups, log parsing, WordPress/nginx wiring) lives in
sibling modules. See docs/plans/49_ANALYTICS_EXTENSION_PLAN.md.
"""
from .analytics import analytics_bp

__all__ = ['analytics_bp']
