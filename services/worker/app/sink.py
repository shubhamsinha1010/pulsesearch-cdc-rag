"""Sinks: Elasticsearch writer and dead-letter queue.

The sink turns a :class:`PipelineResult` into idempotent Elasticsearch writes
and records the end-to-end sync latency. The DLQ isolates poison messages so a
single bad event never blocks the stream.
"""

from __future__ import annotations

import json
import time

from confluent_kafka import Producer

from pulsesearch_common.config import KafkaSettings
from pulsesearch_common.es_client import PageRepository
from pulsesearch_common.metrics import (
    DLQ_MESSAGES,
    DOCS_INDEXED,
    SYNC_LATENCY,
)

from .pipeline import PipelineResult
from .summary_enricher import SummaryEnricher


class ElasticsearchSink:
    """Writes enriched documents to Elasticsearch (Repository-backed)."""

    def __init__(
        self,
        repository: PageRepository,
        enricher: SummaryEnricher | None = None,
    ) -> None:
        self._repo = repository
        self._enricher = enricher

    def write(self, result: PipelineResult) -> int:
        if not result.upserts:
            return 0
        written = self._repo.bulk_upsert(result.upserts)
        DOCS_INDEXED.inc(written)
        self._record_latency(result)
        if self._enricher is not None:
            live = [d for d in result.upserts if not d.deleted]
            self._enricher.submit(live)
        return written

    @staticmethod
    def _record_latency(result: PipelineResult) -> None:
        now_ms = time.time() * 1000
        for ts_ms in result.source_ts_ms_by_id.values():
            if ts_ms > 0:
                SYNC_LATENCY.observe(max(0.0, (now_ms - ts_ms) / 1000.0))


class DeadLetterQueue:
    """Publishes unprocessable messages to a Kafka DLQ topic."""

    def __init__(self, settings: KafkaSettings, producer: Producer | None = None) -> None:
        self._topic = settings.dlq_topic
        self._producer = producer or Producer({"bootstrap.servers": settings.bootstrap_servers})

    def publish(self, raw_value: bytes, error: str, key: bytes | None = None) -> None:
        envelope = {
            "error": error,
            "payload": _safe_decode(raw_value),
        }
        self._producer.produce(
            self._topic,
            key=key,
            value=json.dumps(envelope).encode("utf-8"),
        )
        self._producer.poll(0)
        DLQ_MESSAGES.inc()

    def flush(self, timeout: float = 5.0) -> None:
        self._producer.flush(timeout)


def _safe_decode(raw: bytes) -> object:
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {"_raw": raw.decode("utf-8", errors="replace")}
