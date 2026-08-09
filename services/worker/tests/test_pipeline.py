"""Tests for per-id collapse of upserts and deletes in a batch."""

from __future__ import annotations

from app.handlers import ChangeEvent, Operation
from app.pipeline import EnrichmentPipeline
from pulsesearch_common.models import PageDocument


class FakeEmbeddings:
    dimensions = 4

    def embed(self, text: str):
        return [0.1, 0.2, 0.3, 0.4]

    def embed_batch(self, texts):
        return [self.embed(t) for t in texts]


def _doc(doc_id: str, title: str, version: int, *, deleted: bool = False) -> PageDocument:
    return PageDocument(
        id=doc_id,
        wiki="enwiki",
        title=title,
        version=version,
        deleted=deleted,
    )


def test_newer_upsert_wins_over_earlier_delete_in_same_batch():
    pipeline = EnrichmentPipeline(FakeEmbeddings())
    events = [
        ChangeEvent("1", Operation.DELETE, 100, _doc("1", "Old", 100, deleted=True)),
        ChangeEvent("1", Operation.CREATE, 200, _doc("1", "New", 200)),
    ]
    result = pipeline.process(events)
    assert len(result.upserts) == 1
    assert result.upserts[0].title == "New"
    assert result.upserts[0].deleted is False
    assert result.upserts[0].embedding is not None


def test_newer_delete_wins_over_earlier_upsert_in_same_batch():
    pipeline = EnrichmentPipeline(FakeEmbeddings())
    events = [
        ChangeEvent("1", Operation.UPDATE, 100, _doc("1", "Live", 100)),
        ChangeEvent("1", Operation.DELETE, 200, _doc("1", "Live", 200, deleted=True)),
    ]
    result = pipeline.process(events)
    assert len(result.upserts) == 1
    assert result.upserts[0].deleted is True
    assert result.upserts[0].embedding is None


def test_fast_path_does_not_block_on_summaries():
    # Summaries are attached asynchronously after indexing; the sync pipeline
    # must remain title/comment-only.
    pipeline = EnrichmentPipeline(FakeEmbeddings())
    events = [
        ChangeEvent("1", Operation.CREATE, 100, _doc("1", "Climate change", 100)),
    ]
    result = pipeline.process(events)
    assert result.upserts[0].summary is None
    assert result.upserts[0].embedding is not None
