"""Async Wikipedia-summary enrichment (off the CDC critical path).

Documents are indexed immediately with title/comment embeddings. A background
worker later fetches the page summary, re-embeds, and version-guarded upserts
so semantic quality improves without inflating sync latency.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from pulsesearch_common.embeddings import EmbeddingProvider
from pulsesearch_common.es_client import PageRepository
from pulsesearch_common.metrics import DOCS_INDEXED, EMBEDDING_LATENCY
from pulsesearch_common.models import PageDocument

from .enrichment import NullSummaryClient, SummaryProvider

log = logging.getLogger("worker.summary_enricher")


@dataclass(frozen=True)
class _Job:
    doc_id: str
    wiki: str
    title: str
    version: int
    # Carry enough fields to rebuild a full upsert without an ES round-trip.
    snapshot: PageDocument


class SummaryEnricher:
    """Bounded background queue + workers for post-index summary enrichment."""

    def __init__(
        self,
        repository: PageRepository,
        embeddings: EmbeddingProvider,
        summaries: SummaryProvider | None = None,
        *,
        enabled: bool = True,
        workers: int = 4,
        queue_size: int = 2000,
        fetch_concurrency: int = 8,
    ) -> None:
        self._repo = repository
        self._embeddings = embeddings
        self._summaries = summaries or NullSummaryClient()
        self._enabled = enabled
        self._queue: queue.Queue[_Job | None] = queue.Queue(maxsize=queue_size)
        self._fetch_pool = ThreadPoolExecutor(
            max_workers=max(1, fetch_concurrency), thread_name_prefix="wiki-sum"
        )
        self._embed_lock = threading.Lock()
        self._stopped = threading.Event()
        self._threads: list[threading.Thread] = []
        if enabled:
            for i in range(max(1, workers)):
                thread = threading.Thread(
                    target=self._run,
                    name=f"summary-enricher-{i}",
                    daemon=True,
                )
                thread.start()
                self._threads.append(thread)

    def submit(self, documents: list[PageDocument]) -> int:
        """Enqueue live main-namespace docs for async enrichment."""

        if not self._enabled:
            return 0
        queued = 0
        for document in documents:
            if document.deleted or document.namespace != 0 or not document.title:
                continue
            if document.summary:
                continue
            job = _Job(
                doc_id=document.id,
                wiki=document.wiki,
                title=document.title,
                version=document.version,
                snapshot=document.model_copy(deep=True),
            )
            try:
                self._queue.put_nowait(job)
                queued += 1
            except queue.Full:
                # Searchable already — drop enrichment under backlog pressure.
                log.warning("summary enrichment queue full; dropping jobs")
                break
        return queued

    def stop(self, timeout: float = 5.0) -> None:
        if not self._enabled:
            return
        self._stopped.set()
        for _ in self._threads:
            with contextlib.suppress(queue.Full):
                self._queue.put_nowait(None)
        deadline = time.time() + timeout
        for thread in self._threads:
            remaining = max(0.0, deadline - time.time())
            thread.join(timeout=remaining)
        self._fetch_pool.shutdown(wait=False, cancel_futures=True)

    def _run(self) -> None:
        while not self._stopped.is_set():
            try:
                job = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if job is None:
                return
            try:
                self._enrich(job)
            except Exception as exc:
                log.warning(
                    "summary enrichment failed",
                    extra={"doc_id": job.doc_id, "error": str(exc)},
                )
            finally:
                self._queue.task_done()

    def _enrich(self, job: _Job) -> None:
        future = self._fetch_pool.submit(self._summaries.fetch, job.wiki, job.title)
        summary = future.result()
        if not summary:
            return

        doc = job.snapshot
        doc.summary = summary
        text = doc.searchable_text()
        started = time.perf_counter()
        with self._embed_lock:
            vector = self._embeddings.embed(text)
        EMBEDDING_LATENCY.observe(time.perf_counter() - started)
        doc.embedding = vector

        # Same Debezium version: succeeds if no newer edit landed; otherwise
        # external_gte rejects the stale enrichment (effectively-once).
        self._repo.upsert(doc)
        DOCS_INDEXED.inc()
