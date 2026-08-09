"""Tests for async summary enrichment queue behaviour."""

from __future__ import annotations

import time

from app.summary_enricher import SummaryEnricher
from pulsesearch_common.models import PageDocument


class FakeRepo:
    def __init__(self):
        self.upserts = []

    def upsert(self, document):
        self.upserts.append(document)


class FakeEmbeddings:
    dimensions = 4

    def embed(self, text: str):
        return [0.1, 0.2, 0.3, 0.4]

    def embed_batch(self, texts):
        return [self.embed(t) for t in texts]


class FakeSummaries:
    def fetch(self, wiki: str, title: str):
        return f"Summary of {title}"


def test_enricher_updates_document_with_summary():
    repo = FakeRepo()
    enricher = SummaryEnricher(
        repository=repo,
        embeddings=FakeEmbeddings(),
        summaries=FakeSummaries(),
        enabled=True,
        workers=1,
        fetch_concurrency=1,
        queue_size=10,
    )
    doc = PageDocument(id="1", wiki="enwiki", title="Climate change", version=42)
    assert enricher.submit([doc]) == 1

    deadline = time.time() + 3
    while time.time() < deadline and not repo.upserts:
        time.sleep(0.05)
    enricher.stop(timeout=2)

    assert len(repo.upserts) == 1
    assert repo.upserts[0].summary == "Summary of Climate change"
    assert repo.upserts[0].embedding is not None
    assert repo.upserts[0].version == 42
