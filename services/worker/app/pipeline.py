"""Transform pipeline: change events -> enriched documents.

Separates the CPU-bound enrichment (embeddings) from I/O (Kafka consume, ES
write). Batches embedding computation for throughput. Depends on the
:class:`EmbeddingProvider` abstraction, not a concrete model (DIP).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from pulsesearch_common.embeddings import EmbeddingProvider
from pulsesearch_common.metrics import EMBEDDING_LATENCY
from pulsesearch_common.models import PageDocument, PageRecord

from .handlers import ChangeEvent


@dataclass
class PipelineResult:
    upserts: list[PageDocument] = field(default_factory=list)
    delete_ids: list[str] = field(default_factory=list)
    # Kept to compute end-to-end sync latency after the ES write succeeds.
    source_ts_ms_by_id: dict[str, int] = field(default_factory=dict)


class EnrichmentPipeline:
    def __init__(self, embeddings: EmbeddingProvider) -> None:
        self._embeddings = embeddings

    def process(self, events: list[ChangeEvent]) -> PipelineResult:
        result = PipelineResult()

        upsert_events = [e for e in events if e.op.is_upsert and e.document]
        # Collapse duplicate ids within the batch, keeping the newest version so
        # we embed and write each document at most once per batch.
        latest: dict[str, ChangeEvent] = {}
        for event in upsert_events:
            existing = latest.get(event.doc_id)
            if existing is None or event.source_ts_ms >= existing.source_ts_ms:
                latest[event.doc_id] = event

        documents = [e.document for e in latest.values()]
        self._attach_embeddings(documents)

        for event in latest.values():
            result.upserts.append(event.document)  # type: ignore[arg-type]
            result.source_ts_ms_by_id[event.doc_id] = event.source_ts_ms

        for event in events:
            if event.op.is_delete:
                result.delete_ids.append(event.doc_id)

        return result

    def _attach_embeddings(self, documents: list[PageDocument]) -> None:
        if not documents:
            return
        texts = [
            PageRecord(
                wiki=d.wiki, title=d.title, last_comment=d.last_comment
            ).searchable_text()
            for d in documents
        ]
        started = time.perf_counter()
        vectors = self._embeddings.embed_batch(texts)
        EMBEDDING_LATENCY.observe(time.perf_counter() - started)
        for document, vector in zip(documents, vectors):
            document.embedding = vector
