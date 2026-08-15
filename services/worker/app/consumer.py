"""Kafka consumer loop for the CDC -> Elasticsearch sync.

Delivery guarantees:

* Kafka gives **at-least-once** (manual offset commit only after a batch is
  durably written to Elasticsearch).
* The version-guarded upsert in :class:`PageRepository` makes reprocessing
  **effectively-once** in the index.

Poison messages are routed to a DLQ so one bad event cannot wedge the stream.
Replayable backfill is achieved by resetting this consumer group's offsets
(see the Makefile ``replay`` target).
"""

from __future__ import annotations

import json
import time

from confluent_kafka import Consumer, KafkaError, KafkaException, Message

from pulsesearch_common.config import KafkaSettings
from pulsesearch_common.logging import configure_logging
from pulsesearch_common.metrics import CDC_EVENTS_CONSUMED, SYNC_FAILURES

from .handlers import DebeziumEventParser
from .pipeline import EnrichmentPipeline
from .sink import DeadLetterQueue, ElasticsearchSink

log = configure_logging("worker.consumer")


class SyncConsumer:
    def __init__(
        self,
        settings: KafkaSettings,
        parser: DebeziumEventParser,
        pipeline: EnrichmentPipeline,
        sink: ElasticsearchSink,
        dlq: DeadLetterQueue,
        batch_size: int = 200,
        poll_timeout: float = 1.0,
        max_write_retries: int = 8,
    ) -> None:
        self._settings = settings
        self._parser = parser
        self._pipeline = pipeline
        self._sink = sink
        self._dlq = dlq
        self._batch_size = batch_size
        self._poll_timeout = poll_timeout
        self._max_write_retries = max_write_retries
        self._consumer = Consumer(
            {
                "bootstrap.servers": settings.bootstrap_servers,
                "group.id": settings.group_id,
                "auto.offset.reset": settings.auto_offset_reset,
                "enable.auto.commit": False,
                "partition.assignment.strategy": "cooperative-sticky",
            }
        )
        self._running = True

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        self._consumer.subscribe([self._settings.source_topic])
        log.info("subscribed", extra={"topic": self._settings.source_topic})
        try:
            while self._running:
                batch = self._poll_batch()
                if batch:
                    self._process_batch(batch)
        finally:
            self._shutdown()

    # -- internals --------------------------------------------------------
    def _poll_batch(self) -> list[Message]:
        batch: list[Message] = []
        deadline = time.monotonic() + self._poll_timeout
        while len(batch) < self._batch_size and time.monotonic() < deadline:
            msg = self._consumer.poll(timeout=max(0.0, deadline - time.monotonic()))
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                raise KafkaException(msg.error())
            batch.append(msg)
        return batch

    def _process_batch(self, batch: list[Message]) -> None:
        events = []
        for msg in batch:
            event = self._parse_message(msg)
            if event is not None:
                CDC_EVENTS_CONSUMED.labels(op=event.op.value).inc()
                events.append(event)

        if events:
            result = self._pipeline.process(events)
            if not self._write_with_retries(result, batch):
                # Offsets stay uncommitted so the batch is redelivered after a
                # brief pause (transient ES outages should not create holes).
                time.sleep(2.0)
                return

        # Commit only after the whole batch has been handled (at-least-once).
        self._consumer.commit(asynchronous=False)

    def _parse_message(self, msg: Message):
        raw = msg.value()
        if raw is None:
            # Debezium tombstone (post-delete marker) — nothing to index.
            return None
        try:
            envelope = json.loads(raw)
            payload = _unwrap(envelope)
            return self._parser.parse(payload)
        except Exception as exc:
            SYNC_FAILURES.labels(stage="parse").inc()
            self._dlq.publish(raw, error=f"parse: {exc}", key=msg.key())
            self._dlq.flush()
            log.warning("routed message to DLQ", extra={"error": str(exc)})
            return None

    def _write_with_retries(self, result, batch: list[Message]) -> bool:
        """Return True when the batch is safe to commit."""

        attempt = 0
        while True:
            try:
                self._sink.write(result)
                return True
            except Exception as exc:
                attempt += 1
                SYNC_FAILURES.labels(stage="write").inc()
                if attempt >= self._max_write_retries:
                    log.error(
                        "write failed after retries; holding offsets (no commit)",
                        extra={"attempts": attempt, "error": str(exc)},
                    )
                    # Preserve the failing payloads for inspection, but do not
                    # commit — avoiding permanent index holes from brief outages.
                    for msg in batch:
                        if msg.value() is not None:
                            self._dlq.publish(msg.value(), error=f"write: {exc}", key=msg.key())
                    self._dlq.flush()
                    return False
                backoff = min(2**attempt * 0.5, 15.0)
                log.warning(
                    "write failed; retrying",
                    extra={"attempt": attempt, "backoff": backoff},
                )
                time.sleep(backoff)

    def _shutdown(self) -> None:
        try:
            self._dlq.flush()
        finally:
            self._consumer.close()
            log.info("consumer closed")


def _unwrap(envelope: dict) -> dict | None:
    """Support both schema-wrapped and bare Debezium JSON payloads."""

    if envelope is None:
        return None
    if "payload" in envelope and "op" not in envelope:
        return envelope["payload"]
    return envelope
