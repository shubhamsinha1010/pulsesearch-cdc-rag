"""Worker service entrypoint (composition root).

Wires the concrete implementations behind their abstractions and starts the
consume loop. This is the only place that knows about all the moving parts;
everything else depends on interfaces (Dependency Injection).
"""

from __future__ import annotations

import signal
import time

from pulsesearch_common.config import (
    embedding_settings,
    enrichment_settings,
    es_settings,
    kafka_settings,
    observability_settings,
)
from pulsesearch_common.embeddings import SentenceTransformerEmbeddings
from pulsesearch_common.es_client import PageRepository
from pulsesearch_common.logging import configure_logging
from pulsesearch_common.metrics import serve_metrics

from .consumer import SyncConsumer
from .enrichment import NullSummaryClient, WikipediaSummaryClient
from .handlers import DebeziumEventParser
from .pipeline import EnrichmentPipeline
from .sink import DeadLetterQueue, ElasticsearchSink
from .summary_enricher import SummaryEnricher

log = configure_logging("worker")


def _ensure_index_with_retry(repo: PageRepository, dims: int, attempts: int = 30) -> None:
    for attempt in range(1, attempts + 1):
        try:
            index_created = repo.ensure_index(dims)
            # Avoid LogRecord reserved attrs (e.g. "created") in extra=.
            log.info(
                "index ready",
                extra={"index_created": index_created, "index": repo.index},
            )
            return
        except Exception as exc:
            log.warning(
                "waiting for elasticsearch",
                extra={"attempt": attempt, "error": str(exc)},
            )
            time.sleep(2.0)
    raise RuntimeError("Elasticsearch not reachable; giving up")


def main() -> None:
    serve_metrics(observability_settings().metrics_port)

    embeddings = SentenceTransformerEmbeddings(embedding_settings().model_name)
    # Load the model before creating/validating the index so dims match reality.
    embeddings.embed("warmup")
    repo = PageRepository(settings=es_settings())
    _ensure_index_with_retry(repo, embeddings.dimensions)

    enrich_cfg = enrichment_settings()
    summaries = WikipediaSummaryClient() if enrich_cfg.enabled else NullSummaryClient()
    enricher = SummaryEnricher(
        repository=repo,
        embeddings=embeddings,
        summaries=summaries,
        enabled=enrich_cfg.enabled,
        workers=enrich_cfg.workers,
        queue_size=enrich_cfg.queue_size,
        fetch_concurrency=enrich_cfg.fetch_concurrency,
    )
    log.info(
        "summary enrichment configured",
        extra={
            "enabled": enrich_cfg.enabled,
            "workers": enrich_cfg.workers,
            "fetch_concurrency": enrich_cfg.fetch_concurrency,
        },
    )

    consumer = SyncConsumer(
        settings=kafka_settings(),
        parser=DebeziumEventParser(),
        pipeline=EnrichmentPipeline(embeddings),
        sink=ElasticsearchSink(repo, enricher=enricher),
        dlq=DeadLetterQueue(kafka_settings()),
    )

    def handle_signal(*_: object) -> None:
        log.info("shutdown signal received")
        consumer.stop()
        enricher.stop()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    log.info("worker starting")
    try:
        consumer.run()
    finally:
        enricher.stop()
    log.info("worker stopped")


if __name__ == "__main__":
    main()
