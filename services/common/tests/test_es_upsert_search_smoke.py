"""Live ES smoke: versioned upsert → BM25 find (middle of the CDC→search path).

Skipped automatically when Elasticsearch is unreachable so ``make test`` stays
offline-friendly. CI runs this against a GHA Elasticsearch service.
"""

from __future__ import annotations

import os
import uuid

import pytest

from pulsesearch_common.config import ElasticsearchSettings
from pulsesearch_common.embeddings import HashingEmbeddings
from pulsesearch_common.es_client import PageRepository
from pulsesearch_common.models import PageDocument


def _es_reachable(url: str) -> bool:
    try:
        from elasticsearch import Elasticsearch

        client = Elasticsearch(url, request_timeout=5)
        return bool(client.ping())
    except Exception:
        return False


@pytest.fixture(scope="module")
def repo() -> PageRepository:
    url = os.environ.get("ES_URL", "http://localhost:9200")
    if not _es_reachable(url):
        pytest.skip(f"Elasticsearch not reachable at {url}")
    index = os.environ.get("ES_INDEX", f"pages_smoke_{uuid.uuid4().hex[:8]}")
    settings = ElasticsearchSettings(url=url, index=index)
    repository = PageRepository(settings=settings)
    yield repository
    try:
        if repository.client.indices.exists(index=index):
            repository.client.indices.delete(index=index)
    except Exception:
        pass


def test_upsert_then_bm25_search(repo: PageRepository) -> None:
    """Worker write contract + API read contract share PageRepository."""

    dims = 384
    embeddings = HashingEmbeddings(dimensions=dims)
    repo.ensure_index(dims)

    title = f"PulseSearch Smoke {uuid.uuid4().hex[:8]}"
    doc = PageDocument(
        id=f"smoke-{uuid.uuid4().hex}",
        wiki="enwiki",
        title=title,
        last_comment="integration smoke upsert",
        namespace=0,
        version=1,
        embedding=embeddings.embed(title),
    )
    repo.upsert(doc)
    repo.client.indices.refresh(index=repo.index)

    hits = repo.bm25_search(title, size=5, filters={"wiki": "enwiki", "namespace": 0})
    ids = [h["_id"] for h in hits]
    assert doc.id in ids, f"expected {doc.id} in BM25 hits {ids}"
