"""Elasticsearch access layer (Repository pattern).

``PageRepository`` is the single place that knows how documents are stored and
queried. Both the worker (writes) and the api (reads) depend on it, so the
index mapping, the idempotent-upsert semantics and the hybrid-query shape are
defined exactly once.
"""

from __future__ import annotations

from typing import Any, Optional

from elasticsearch import Elasticsearch, NotFoundError
from elasticsearch.helpers import BulkIndexError, bulk

from .config import ElasticsearchSettings, es_settings
from .models import PageDocument


def build_client(settings: Optional[ElasticsearchSettings] = None) -> Elasticsearch:
    settings = settings or es_settings()
    return Elasticsearch(
        settings.url,
        request_timeout=settings.request_timeout,
        retry_on_timeout=True,
        max_retries=3,
    )


def index_mapping(embedding_dims: int) -> dict[str, Any]:
    """The index definition: BM25 text fields plus a kNN ``dense_vector``.

    ``dense_vector`` with ``index: true`` + cosine similarity is available on
    the free Basic license, which keeps the project at zero cost.
    """

    return {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
            "analysis": {
                "analyzer": {
                    "pulse_text": {
                        "type": "standard",
                    }
                }
            },
        },
        "mappings": {
            "properties": {
                "wiki": {"type": "keyword"},
                "title": {
                    "type": "text",
                    "analyzer": "pulse_text",
                    "fields": {"raw": {"type": "keyword"}},
                },
                "title_url": {"type": "keyword", "index": False},
                "last_comment": {"type": "text", "analyzer": "pulse_text"},
                "last_user": {"type": "keyword"},
                "event_type": {"type": "keyword"},
                "namespace": {"type": "integer"},
                "is_bot": {"type": "boolean"},
                "is_minor": {"type": "boolean"},
                "length_new": {"type": "integer"},
                "edit_count": {"type": "integer"},
                "event_time": {"type": "date"},
                "updated_at": {"type": "date"},
                "version": {"type": "long"},
                "embedding": {
                    "type": "dense_vector",
                    "dims": embedding_dims,
                    "index": True,
                    "similarity": "cosine",
                },
            }
        },
    }


class PageRepository:
    """Read/write access to the ``pages`` index."""

    def __init__(
        self,
        client: Optional[Elasticsearch] = None,
        settings: Optional[ElasticsearchSettings] = None,
    ) -> None:
        self._settings = settings or es_settings()
        self._client = client or build_client(self._settings)

    @property
    def client(self) -> Elasticsearch:
        return self._client

    @property
    def index(self) -> str:
        return self._settings.index

    # -- schema management ------------------------------------------------
    def ensure_index(self, embedding_dims: int) -> bool:
        """Create the index if missing. Returns True when newly created."""

        if self._client.indices.exists(index=self.index):
            return False
        self._client.indices.create(index=self.index, **index_mapping(embedding_dims))
        return True

    # -- writes -----------------------------------------------------------
    def upsert(self, document: PageDocument) -> None:
        """Idempotent, version-guarded upsert.

        We use external versioning: an upsert only takes effect when its
        ``version`` is >= the stored one, making replays and out-of-order
        delivery safe (effectively-once indexing on top of at-least-once
        Kafka delivery).
        """

        self._client.index(
            index=self.index,
            id=document.id,
            document=document.to_source(),
            version=document.version,
            version_type="external_gte",
        )

    def bulk_upsert(self, documents: list[PageDocument]) -> int:
        if not documents:
            return 0
        actions = [
            {
                "_op_type": "index",
                "_index": self.index,
                "_id": doc.id,
                "_version": doc.version,
                "_version_type": "external_gte",
                "_source": doc.to_source(),
            }
            for doc in documents
        ]
        try:
            success, _ = bulk(self._client, actions, raise_on_error=True)
            return success
        except BulkIndexError as exc:
            # Version conflicts are expected under replay and are not failures.
            real_errors = [
                e
                for e in exc.errors
                if e.get("index", {}).get("status") != 409
            ]
            if real_errors:
                raise
            return len(actions) - len(real_errors)

    def delete(self, doc_id: str) -> None:
        try:
            self._client.delete(index=self.index, id=doc_id)
        except NotFoundError:
            pass  # already absent — deletion is idempotent

    # -- reads ------------------------------------------------------------
    def bm25_search(
        self, query: str, size: int, filters: Optional[dict[str, Any]] = None
    ) -> list[dict[str, Any]]:
        body: dict[str, Any] = {
            "size": size,
            "query": {
                "bool": {
                    "must": {
                        "multi_match": {
                            "query": query,
                            "fields": ["title^3", "last_comment"],
                            "type": "best_fields",
                            "fuzziness": "AUTO",
                        }
                    },
                    "filter": _build_filters(filters),
                }
            },
            "_source": {"excludes": ["embedding"]},
        }
        resp = self._client.search(index=self.index, **body)
        return resp["hits"]["hits"]

    def knn_search(
        self,
        vector: list[float],
        size: int,
        num_candidates: int = 100,
        filters: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        knn: dict[str, Any] = {
            "field": "embedding",
            "query_vector": vector,
            "k": size,
            "num_candidates": max(num_candidates, size),
        }
        filter_clauses = _build_filters(filters)
        if filter_clauses:
            knn["filter"] = filter_clauses
        resp = self._client.search(
            index=self.index,
            knn=knn,
            size=size,
            source={"excludes": ["embedding"]},
        )
        return resp["hits"]["hits"]

    def get(self, doc_id: str) -> Optional[dict[str, Any]]:
        try:
            resp = self._client.get(index=self.index, id=doc_id)
        except NotFoundError:
            return None
        if not resp.get("found"):
            return None
        return resp["_source"]

    def count(self) -> int:
        return self._client.count(index=self.index)["count"]


def _build_filters(filters: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    if not filters:
        return []
    clauses: list[dict[str, Any]] = []
    for field, value in filters.items():
        if value is None:
            continue
        clauses.append({"term": {field: value}})
    return clauses
