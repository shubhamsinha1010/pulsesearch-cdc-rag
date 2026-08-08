"""Unit tests for the shared kernel.

These cover the pure, high-value logic that every service relies on:
serialisation contracts, the deterministic embedding fallback, and the
Elasticsearch filter builder. No external services required.
"""

from __future__ import annotations

import math

from pulsesearch_common.embeddings import HashingEmbeddings
from pulsesearch_common.es_client import _build_filters
from pulsesearch_common.models import PageDocument, PageRecord


def test_searchable_text_joins_title_and_comment():
    record = PageRecord(wiki="enwiki", title="Alan Turing", last_comment="typo fix")
    assert "Alan Turing" in record.searchable_text()
    assert "typo fix" in record.searchable_text()


def test_searchable_text_handles_missing_comment():
    record = PageRecord(wiki="enwiki", title="Alan Turing")
    assert record.searchable_text() == "Alan Turing"


def test_to_source_can_exclude_embedding():
    doc = PageDocument(id="1", wiki="enwiki", title="X", embedding=[0.1, 0.2])
    assert "embedding" in doc.to_source(include_embedding=True)
    assert "embedding" not in doc.to_source(include_embedding=False)


def test_hashing_embeddings_are_unit_normalised():
    provider = HashingEmbeddings(dimensions=64)
    vector = provider.embed("real-time change data capture")
    assert len(vector) == 64
    norm = math.sqrt(sum(v * v for v in vector))
    assert math.isclose(norm, 1.0, rel_tol=1e-6)


def test_hashing_embeddings_are_deterministic():
    provider = HashingEmbeddings(dimensions=32)
    assert provider.embed("hybrid search") == provider.embed("hybrid search")


def test_build_filters_skips_none_values():
    assert _build_filters(None) == []
    assert _build_filters({"wiki": None}) == []
    assert _build_filters({"wiki": "enwiki"}) == [{"term": {"wiki": "enwiki"}}]
