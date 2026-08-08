"""Tests for hybrid Reciprocal Rank Fusion in the search service."""

from __future__ import annotations

from app.services.search_service import HybridSearchService, SearchMode


class FakeEmbeddings:
    dimensions = 4

    def embed(self, text: str):
        return [0.0, 0.0, 0.0, 0.0]

    def embed_batch(self, texts):
        return [self.embed(t) for t in texts]


def _hit(doc_id: str, score: float = 1.0):
    return {
        "_id": doc_id,
        "_score": score,
        "_source": {"wiki": "enwiki", "title": f"Title {doc_id}"},
    }


class FakeRepository:
    def __init__(self, bm25, knn):
        self._bm25 = bm25
        self._knn = knn

    def bm25_search(self, query, size, filters=None):
        return self._bm25[:size]

    def knn_search(self, vector, size, num_candidates=100, filters=None):
        return self._knn[:size]


def test_rrf_rewards_documents_ranked_by_both_retrievers():
    # "B" is mid-rank in both lists; RRF should lift it above items that only
    # appear strongly in one retriever.
    bm25 = [_hit("A"), _hit("B"), _hit("C")]
    knn = [_hit("D"), _hit("B"), _hit("E")]
    service = HybridSearchService(FakeRepository(bm25, knn), FakeEmbeddings())

    hits = service.search("anything", size=3, mode=SearchMode.HYBRID)

    assert hits[0].id == "B"
    top = hits[0]
    assert top.bm25_rank == 2  # 1-based
    assert top.knn_rank == 2


def test_bm25_mode_only_uses_lexical_ranking():
    bm25 = [_hit("A"), _hit("B")]
    service = HybridSearchService(FakeRepository(bm25, []), FakeEmbeddings())

    hits = service.search("q", size=2, mode=SearchMode.BM25)

    assert [h.id for h in hits] == ["A", "B"]
    assert hits[0].bm25_rank == 0
    assert hits[0].knn_rank is None


def test_empty_query_returns_no_hits():
    service = HybridSearchService(FakeRepository([], []), FakeEmbeddings())
    assert service.search("   ", size=5) == []
