"""Transform pipeline: change events -> searchable documents (fast path).

Summary fetching is intentionally *not* on this path — a background enricher
improves semantic quality after the document is already searchable.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from pulsesearch_common.embeddings import EmbeddingProvider
from pulsesearch_common.metrics import EMBEDDING_LATENCY
from pulsesearch_common.models import PageDocument

from .handlers import ChangeEvent


@dataclass
class PipelineResult:
    upserts: list[PageDocument] = field(default_factory=list)
    # Kept to compute end-to-end sync latency after the ES write succeeds.
    source_ts_ms_by_id: dict[str, int] = field(default_factory=dict)


class EnrichmentPipeline:
    def __init__(self, embeddings: EmbeddingProvider) -> None:
        self._embeddings = embeddings

    def process(self, events: list[ChangeEvent]) -> PipelineResult:
        result = PipelineResult()

        # Collapse to the newest op per id (create/update/delete) so a
        # delete-then-recreate in the same batch cannot leave a stale tombstone,
        # and a recreate-then-delete cannot resurrect an older upsert.
        latest: dict[str, ChangeEvent] = {}
        for event in events:
            if event.document is None:
                continue
            existing = latest.get(event.doc_id)
            if existing is None or event.source_ts_ms >= existing.source_ts_ms:
                latest[event.doc_id] = event

        live_docs = [e.document for e in latest.values() if not e.document.deleted]
        self._attach_embeddings(live_docs)

        for event in latest.values():
            result.upserts.append(event.document)  # type: ignore[arg-type]
            result.source_ts_ms_by_id[event.doc_id] = event.source_ts_ms

        return result

    def _attach_embeddings(self, documents: list[PageDocument]) -> None:
        if not documents:
            return
        texts = [d.searchable_text() for d in documents]
        started = time.perf_counter()
        vectors = self._embeddings.embed_batch(texts)
        EMBEDDING_LATENCY.observe(time.perf_counter() - started)
        for document, vector in zip(documents, vectors, strict=True):
            document.embedding = vector
