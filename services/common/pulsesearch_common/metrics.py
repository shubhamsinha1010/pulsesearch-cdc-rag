"""Prometheus metric definitions and a tiny HTTP exposer.

Metrics are declared once here so dashboards can rely on stable names across
services. Each metric is labelled by ``service`` to allow a single Grafana
dashboard to slice by component.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, start_http_server

# --- Ingest -------------------------------------------------------------
EVENTS_INGESTED = Counter(
    "pulse_events_ingested_total",
    "Firehose events written to the system-of-record",
    ["source"],
)
EVENTS_DROPPED = Counter(
    "pulse_events_dropped_total",
    "Firehose events discarded before persistence",
    ["source", "reason"],
)

# --- Worker (CDC -> ES sync) -------------------------------------------
CDC_EVENTS_CONSUMED = Counter(
    "pulse_cdc_events_consumed_total",
    "Debezium change events consumed from Kafka",
    ["op"],
)
DOCS_INDEXED = Counter(
    "pulse_docs_indexed_total",
    "Documents upserted into Elasticsearch",
)
SYNC_FAILURES = Counter(
    "pulse_sync_failures_total",
    "Failures while syncing a change event",
    ["stage"],
)
DLQ_MESSAGES = Counter(
    "pulse_dlq_messages_total",
    "Messages routed to the dead-letter queue",
)
# End-to-end latency: source commit time (Debezium ts_ms) -> searchable in ES.
SYNC_LATENCY = Histogram(
    "pulse_sync_latency_seconds",
    "Latency from source change commit to Elasticsearch upsert",
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
)
EMBEDDING_LATENCY = Histogram(
    "pulse_embedding_latency_seconds",
    "Time spent computing embeddings per batch",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2),
)

# --- API ----------------------------------------------------------------
SEARCH_REQUESTS = Counter(
    "pulse_search_requests_total",
    "Search requests served",
    ["mode"],
)
SEARCH_LATENCY = Histogram(
    "pulse_search_latency_seconds",
    "Search request latency",
    ["mode"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)
RAG_REQUESTS = Counter(
    "pulse_rag_requests_total",
    "RAG answers generated",
    ["grounded"],
)
WS_CONNECTIONS = Gauge(
    "pulse_ws_connections",
    "Active WebSocket connections",
)


def serve_metrics(port: int) -> None:
    """Start a background HTTP server exposing ``/metrics``."""

    start_http_server(port)
