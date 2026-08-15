"""Tests for hybrid Reciprocal Rank Fusion in the search service."""

from __future__ import annotations

from app.services.search_service import (
    HybridSearchService,
    SearchMode,
    _query_aware_weights,
)


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
        "_source": {"wiki": "enwiki", "title": f"Title {doc_id}", "namespace": 0},
    }


class FakeRepository:
    def __init__(self, bm25, knn):
        self._bm25 = bm25
        self._knn = knn
        self.last_knn_min_score = None
        self.last_filters = None

    def bm25_search(self, query, size, filters=None):
        self.last_filters = filters
        return self._bm25[:size]

    def knn_search(self, vector, size, num_candidates=100, filters=None, min_score=None):
        self.last_knn_min_score = min_score
        self.last_filters = filters
        return [h for h in self._knn if float(h.get("_score") or 0) >= (min_score or 0)][:size]


def test_rrf_rewards_documents_ranked_by_both_retrievers():
    # "B" is mid-rank in both lists; RRF should lift it above items that only
    # appear strongly in one retriever.
    bm25 = [_hit("A"), _hit("B"), _hit("C")]
    knn = [_hit("D", 0.9), _hit("B", 0.85), _hit("E", 0.8)]
    service = HybridSearchService(FakeRepository(bm25, knn), FakeEmbeddings(), knn_min_score=0.5)

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
    assert hits[0].bm25_rank == 1
    assert hits[0].knn_rank is None


def test_empty_query_returns_no_hits():
    service = HybridSearchService(FakeRepository([], []), FakeEmbeddings())
    assert service.search("   ", size=5) == []


def test_default_filters_main_namespace():
    repo = FakeRepository([_hit("A")], [])
    service = HybridSearchService(repo, FakeEmbeddings())
    service.search("q", mode=SearchMode.BM25)
    assert repo.last_filters == {"namespace": 0}


def test_vector_mode_applies_min_score():
    knn = [_hit("strong", 0.9), _hit("weak", 0.4)]
    repo = FakeRepository([], knn)
    service = HybridSearchService(repo, FakeEmbeddings(), knn_min_score=0.72)
    hits = service.search("q", mode=SearchMode.VECTOR, size=5)
    assert [h.id for h in hits] == ["strong"]
    assert repo.last_knn_min_score == 0.72


def test_weighted_rrf_prefers_strong_bm25_when_knn_is_absent():
    bm25 = [_hit("A"), _hit("B")]
    knn = [_hit("C", 0.9)]
    service = HybridSearchService(
        FakeRepository(bm25, knn),
        FakeEmbeddings(),
        knn_min_score=0.5,
        bm25_weight=1.15,
        knn_weight=0.85,
    )
    hits = service.search("q", mode=SearchMode.HYBRID, size=3)
    assert hits[0].id == "A"


def test_query_aware_weights_boost_lexical_for_short_queries():
    bm25_w, knn_w = _query_aware_weights("Alan Turing", 1.0, 1.0)
    assert bm25_w > knn_w


def test_query_aware_weights_boost_semantic_for_long_queries():
    bm25_w, knn_w = _query_aware_weights(
        "what are recent discussions about extreme heat and drought impacts",
        1.0,
        1.0,
    )
    assert knn_w > bm25_w
