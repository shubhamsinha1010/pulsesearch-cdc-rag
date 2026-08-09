"""Hybrid search service.

Combines lexical (BM25) and semantic (kNN dense-vector) retrieval using
Reciprocal Rank Fusion. RRF is implemented in the application layer on purpose:

* it works on the free Elasticsearch Basic license (native ``rrf`` retrievers
  require a paid tier), and
* it keeps the fusion logic explicit, testable and tunable.

The service depends on the :class:`PageRepository` and an
:class:`EmbeddingProvider` abstraction (DIP); it has no direct knowledge of
Elasticsearch query DSL.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from typing import Any, Optional

from pulsesearch_common.embeddings import EmbeddingProvider
from pulsesearch_common.es_client import PageRepository
from pulsesearch_common.models import PageDocument, SearchHit


class SearchMode(str, Enum):
    HYBRID = "hybrid"
    BM25 = "bm25"
    VECTOR = "vector"


# RRF dampening constant. 60 is the value from the original RRF paper and is a
# sensible, well-understood default.
_RRF_K = 60

# Slight BM25 preference: Wikipedia change titles are a strong lexical signal,
# while dense retrieval over short title+edit-summary text is noisier.
_BM25_WEIGHT = 1.15
_KNN_WEIGHT = 0.85

# Drop weak semantic neighbours. ES cosine similarity scores for unrelated
# short strings often land in the mid-0.6s; require a firmer match for vector
# / hybrid fusion (see Elastic hybrid-search guidance: filter before fuse).
_KNN_MIN_SCORE = 0.72

# Default to main-article namespace so User/Talk/meta pages do not dominate.
_DEFAULT_NAMESPACE = 0


class HybridSearchService:
    def __init__(
        self,
        repository: PageRepository,
        embeddings: EmbeddingProvider,
        candidate_pool: int = 50,
        knn_min_score: float = _KNN_MIN_SCORE,
        bm25_weight: float = _BM25_WEIGHT,
        knn_weight: float = _KNN_WEIGHT,
    ) -> None:
        self._repo = repository
        self._embeddings = embeddings
        self._candidate_pool = candidate_pool
        self._knn_min_score = knn_min_score
        self._bm25_weight = bm25_weight
        self._knn_weight = knn_weight
        # Overlap BM25 (I/O) with query embedding (CPU).
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="search")

    def search(
        self,
        query: str,
        size: int = 10,
        mode: SearchMode = SearchMode.HYBRID,
        wiki: Optional[str] = None,
        namespace: Optional[int] = _DEFAULT_NAMESPACE,
    ) -> list[SearchHit]:
        if not query.strip():
            return []

        filters = _search_filters(wiki=wiki, namespace=namespace)

        if mode is SearchMode.BM25:
            hits = self._repo.bm25_search(query, self._candidate_pool, filters)
            return self._as_hits(hits, size, source="bm25")

        if mode is SearchMode.VECTOR:
            vector = self._embeddings.embed(query)
            hits = self._repo.knn_search(
                vector,
                self._candidate_pool,
                filters=filters,
                min_score=self._knn_min_score,
            )
            return self._as_hits(hits, size, source="knn")

        return self._hybrid(query, size, filters)

    # -- hybrid fusion ----------------------------------------------------
    def _hybrid(
        self, query: str, size: int, filters: Optional[dict[str, Any]]
    ) -> list[SearchHit]:
        bm25_weight, knn_weight = _query_aware_weights(
            query, self._bm25_weight, self._knn_weight
        )
        bm25_future = self._pool.submit(
            self._repo.bm25_search, query, self._candidate_pool, filters
        )
        vector_future = self._pool.submit(self._embeddings.embed, query)
        bm25_hits = bm25_future.result()
        vector = vector_future.result()
        knn_hits = self._repo.knn_search(
            vector,
            self._candidate_pool,
            filters=filters,
            min_score=self._knn_min_score,
        )

        bm25_rank = {h["_id"]: i for i, h in enumerate(bm25_hits)}
        knn_rank = {h["_id"]: i for i, h in enumerate(knn_hits)}
        sources: dict[str, dict[str, Any]] = {
            h["_id"]: h for h in (*bm25_hits, *knn_hits)
        }

        fused: list[tuple[str, float]] = []
        for doc_id in sources:
            score = 0.0
            if doc_id in bm25_rank:
                score += bm25_weight / (_RRF_K + bm25_rank[doc_id])
            if doc_id in knn_rank:
                score += knn_weight / (_RRF_K + knn_rank[doc_id])
            fused.append((doc_id, score))

        fused.sort(key=lambda pair: pair[1], reverse=True)

        results: list[SearchHit] = []
        for doc_id, score in fused[:size]:
            results.append(
                SearchHit(
                    id=doc_id,
                    score=round(score, 6),
                    document=_to_document(doc_id, sources[doc_id]["_source"]),
                    bm25_rank=_rank_or_none(bm25_rank.get(doc_id)),
                    knn_rank=_rank_or_none(knn_rank.get(doc_id)),
                )
            )
        return results

    def _as_hits(
        self, hits: list[dict[str, Any]], size: int, source: str
    ) -> list[SearchHit]:
        results: list[SearchHit] = []
        for rank, hit in enumerate(hits[:size]):
            results.append(
                SearchHit(
                    id=hit["_id"],
                    score=float(hit.get("_score") or 0.0),
                    document=_to_document(hit["_id"], hit["_source"]),
                    bm25_rank=_rank_or_none(rank if source == "bm25" else None),
                    knn_rank=_rank_or_none(rank if source == "knn" else None),
                )
            )
        return results


def _search_filters(
    *, wiki: Optional[str], namespace: Optional[int]
) -> Optional[dict[str, Any]]:
    filters: dict[str, Any] = {}
    if wiki is not None:
        filters["wiki"] = wiki
    if namespace is not None:
        filters["namespace"] = namespace
    return filters or None


def _query_aware_weights(
    query: str, bm25_weight: float, knn_weight: float
) -> tuple[float, float]:
    """Shift RRF weights by query shape (weighted RRF practice).

    Short / quoted / Title-Case queries lean lexical; longer conceptual
    questions lean semantic.
    """

    q = query.strip()
    tokens = [t for t in q.split() if t]
    if not tokens:
        return bm25_weight, knn_weight

    quoted = q.startswith('"') and q.endswith('"')
    titleish = sum(1 for t in tokens if t[:1].isupper()) >= max(1, len(tokens) // 2)
    if quoted or len(tokens) <= 2 or titleish:
        return bm25_weight * 1.25, knn_weight * 0.75
    if len(tokens) >= 6:
        return bm25_weight * 0.85, knn_weight * 1.2
    return bm25_weight, knn_weight


def _to_document(doc_id: str, source: dict[str, Any]) -> PageDocument:
    return PageDocument(id=doc_id, **{k: v for k, v in source.items() if k != "id"})


def _rank_or_none(rank: Optional[int]) -> Optional[int]:
    # Expose 1-based ranks to clients for readability.
    return None if rank is None else rank + 1
