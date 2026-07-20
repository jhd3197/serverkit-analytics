"""Background job handlers (serverkit-analytics extension).

Registered via the manifest ``jobs`` block; each handler takes the ``job`` row
and must never raise (a raised handler fails the job loudly — these are
best-effort maintenance tasks, and a disabled plugin's schedules are paused by
the platform anyway).

* ``analytics.rollup`` (:func:`rollup`) — aggregate raw ``event`` rows into the
  ``daily`` rollup table (hourly).
* ``analytics.retention_prune`` (:func:`retention_prune`) — drop raw events past
  ``raw_retention_days`` and rollups past ``rollup_retention_months`` (daily).
* ``analytics.log_tail`` (:func:`log_tail`) — ingest new access-log lines for
  sites that opted into server-log ingestion (every few minutes).

The heavy lifting lives in sibling services (``rollup_service``,
``log_ingest_service``); these wrappers keep the scheduler decoupled and
crash-proof. Filled out in Phase 2 (rollup/retention) and Phase 6 (log tail).
"""
import logging

logger = logging.getLogger(__name__)


def rollup(job):
    """Roll raw events up into the daily table. Never raises."""
    try:
        from .rollup_service import run_rollup
        return run_rollup()
    except Exception as e:  # noqa: BLE001 — a maintenance job must not crash the loop
        logger.warning('analytics rollup job failed: %s', e)
        return {'error': str(e)}


def retention_prune(job):
    """Prune raw events + old rollups past their retention windows. Never raises."""
    try:
        from .rollup_service import run_retention_prune
        return run_retention_prune()
    except Exception as e:  # noqa: BLE001
        logger.warning('analytics retention_prune job failed: %s', e)
        return {'error': str(e)}


def log_tail(job):
    """Ingest new access-log lines for opted-in sites. Never raises."""
    try:
        from .config import cfg_bool
        if not cfg_bool('log_ingestion_enabled'):
            return {'skipped': True, 'reason': 'log ingestion disabled'}
        from .log_ingest_service import run_log_tail
        return run_log_tail()
    except Exception as e:  # noqa: BLE001
        logger.warning('analytics log_tail job failed: %s', e)
        return {'error': str(e)}
