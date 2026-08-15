"""Single-process production worker for durable document ingestion jobs."""

from __future__ import annotations

import logging
import os
import signal
import time

from ekb_api.core.db import get_engine
from ekb_api.services.ingest_worker import run_ingest_tick

_LOG = logging.getLogger("ekb.runtime_worker")
_DEFAULT_IDLE_SECONDS = 2.0
_MIN_IDLE_SECONDS = 0.25
_MAX_IDLE_SECONDS = 30.0


def worker_idle_seconds(raw_value: str | None) -> float:
    """Parse the bounded idle period without accepting unsafe process settings."""

    if raw_value is None:
        return _DEFAULT_IDLE_SECONDS
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return _DEFAULT_IDLE_SECONDS
    if not _MIN_IDLE_SECONDS <= value <= _MAX_IDLE_SECONDS:
        return _DEFAULT_IDLE_SECONDS
    return value


def run_worker_cycle() -> bool:
    """Claim and process at most one document ingestion job."""

    return run_ingest_tick(get_engine(), worker_id="ekb-ingest-worker") is not None


def main() -> int:
    should_stop = False

    def _request_stop(_signal_number, _frame) -> None:
        nonlocal should_stop
        should_stop = True

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    idle_seconds = worker_idle_seconds(os.getenv("EKB_WORKER_IDLE_SECONDS"))
    _LOG.info("ingest_worker.started", extra={"idle_seconds": idle_seconds})

    while not should_stop:
        try:
            processed = run_worker_cycle()
        except Exception as exc:  # noqa: BLE001 - keep retrying durable queued work
            _LOG.error("ingest_worker.cycle_failed", extra={"error_type": type(exc).__name__})
            processed = False
        if not processed:
            time.sleep(idle_seconds)

    _LOG.info("ingest_worker.stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
