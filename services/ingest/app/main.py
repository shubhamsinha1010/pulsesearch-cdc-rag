"""Ingest service entrypoint.

Reads a firehose (Strategy) and writes batches into MySQL (Repository). A
small time/size-bounded buffer keeps write amplification low while preserving
freshness. The service owns only orchestration; parsing and persistence live
in their dedicated modules (SRP).
"""

from __future__ import annotations

import signal
import time
from types import FrameType

from pulsesearch_common.config import (
    ingest_settings,
    mysql_settings,
    observability_settings,
)
from pulsesearch_common.logging import configure_logging
from pulsesearch_common.metrics import EVENTS_INGESTED, serve_metrics

from .repository import PageWriteRepository
from .sources import create_source

log = configure_logging("ingest")


class BatchBuffer:
    """Accumulates records and flushes on size or elapsed-time thresholds."""

    def __init__(
        self, repo: PageWriteRepository, source_name: str, batch_size: int, flush_interval: float
    ) -> None:
        self._repo = repo
        self._source_name = source_name
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._buffer: list = []
        self._last_flush = time.monotonic()

    def add(self, record) -> None:
        self._buffer.append(record)
        if self._should_flush():
            self.flush()

    def _should_flush(self) -> bool:
        if len(self._buffer) >= self._batch_size:
            return True
        return (time.monotonic() - self._last_flush) >= self._flush_interval

    def flush(self) -> None:
        if not self._buffer:
            self._last_flush = time.monotonic()
            return
        attempt = 0
        while True:
            try:
                written = self._repo.upsert_many(self._buffer)
                EVENTS_INGESTED.labels(source=self._source_name).inc(written)
                log.info("flushed batch", extra={"count": written})
                self._buffer.clear()
                self._last_flush = time.monotonic()
                return
            except Exception:
                attempt += 1
                log.exception(
                    "failed to flush batch; retrying",
                    extra={"count": len(self._buffer), "attempt": attempt},
                )
                time.sleep(min(2**attempt, 30.0))


class IngestRunner:
    def __init__(self) -> None:
        self._settings = ingest_settings()
        self._source = create_source(self._settings)
        self._repo = PageWriteRepository(mysql_settings())
        self._buffer = BatchBuffer(
            self._repo,
            self._source.name,
            self._settings.batch_size,
            self._settings.flush_interval_seconds,
        )
        self._running = True

    def stop(self, *_: object) -> None:
        log.info("shutdown signal received")
        self._running = False

    def run(self) -> None:
        log.info(
            "ingest starting",
            extra={"source": self._source.name, "wikis": self._settings.wikis},
        )
        backoff = 1.0
        while self._running:
            try:
                for record in self._source.stream():
                    if not self._running:
                        break
                    self._buffer.add(record)
                    backoff = 1.0  # healthy stream resets backoff
            except Exception:
                log.exception("stream error; reconnecting", extra={"backoff": backoff})
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
            finally:
                self._buffer.flush()
        log.info("ingest stopped")


def _install_signal_handlers(runner: IngestRunner) -> None:
    def handler(signum: int, _frame: FrameType | None) -> None:
        runner.stop()

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


def main() -> None:
    serve_metrics(observability_settings().metrics_port)
    runner = IngestRunner()
    _install_signal_handlers(runner)
    runner.run()


if __name__ == "__main__":
    main()
