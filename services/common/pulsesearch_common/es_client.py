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
from .models import PageDocument, utcnow


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
                "summary": {"type": "text", "analyzer": "pulse_text"},
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
                "deleted": {"type": "boolean"},
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
        """Create the index if missing. Returns True when newly created.

        If the index already exists, verifies the ``dense_vector`` dims match so
        a model/config mismatch cannot silently poison indexing.
        """

        if self._client.indices.exists(index=self.index):
            mapping = self._client.indices.get_mapping(index=self.index)
            props = mapping[self.index]["mappings"].get("properties", {})
            existing_dims = props.get("embedding", {}).get("dims")
            if existing_dims is not None and int(existing_dims) != int(embedding_dims):
                raise RuntimeError(
                    f"index {self.index!r} embedding dims={existing_dims} "
                    f"!= model dims={embedding_dims}; recreate the index"
                )
            # Additive field upgrades (safe on live indexes).
            if "summary" not in props:
                self._client.indices.put_mapping(
                    index=self.index,
                    properties={
                        "summary": {"type": "text", "analyzer": "pulse_text"},
                    },
                )
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

    def soft_delete(self, doc_id: str, version: int, *, wiki: str = "", title: str = "") -> None:
        """Version-guarded soft-delete (tombstone upsert).

        Hard ES deletes are not durable under external versioning / replays;
        writing ``deleted=true`` with ``external_gte`` keeps tombstones
        monotonic like normal upserts.
        """

        tombstone = PageDocument(
            id=doc_id,
            wiki=wiki,
            title=title,
            deleted=True,
            version=version,
            updated_at=utcnow(),
        )
        self.upsert(tombstone)

    # -- reads ------------------------------------------------------------
    def bm25_search(
        self, query: str, size: int, filters: Optional[dict[str, Any]] = None
    ) -> list[dict[str, Any]]:
        body: dict[str, Any] = {
            "size": size,
            "query": {
                "bool": {
                    "should": _bm25_should_clauses(query),
                    "minimum_should_match": 1,
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
        min_score: Optional[float] = None,
    ) -> list[dict[str, Any]]:
        # Over-fetch slightly when a score floor is applied so we still fill ``size``.
        fetch_size = size if min_score is None else max(size * 3, size)
        knn: dict[str, Any] = {
            "field": "embedding",
            "query_vector": vector,
            "k": fetch_size,
            "num_candidates": max(num_candidates, fetch_size),
            "filter": _build_filters(filters),
        }
        resp = self._client.search(
            index=self.index,
            knn=knn,
            size=fetch_size,
            source={"excludes": ["embedding"]},
        )
        hits = resp["hits"]["hits"]
        if min_score is not None:
            hits = [h for h in hits if float(h.get("_score") or 0.0) >= min_score]
        return hits[:size]

    def get(self, doc_id: str) -> Optional[dict[str, Any]]:
        try:
            resp = self._client.get(index=self.index, id=doc_id)
        except NotFoundError:
            return None
        if not resp.get("found"):
            return None
        source = resp["_source"]
        if source.get("deleted"):
            return None
        return source

    def count(self) -> int:
        return self._client.count(
            index=self.index,
            query={"bool": {"filter": _live_doc_filters()}},
        )["count"]


def _live_doc_filters() -> list[dict[str, Any]]:
    # Treat missing ``deleted`` as live so pre-tombstone docs still count.
    return [{"bool": {"must_not": {"term": {"deleted": True}}}}]


def _bm25_should_clauses(query: str) -> list[dict[str, Any]]:
    """Lexical clauses tuned for title-heavy Wikipedia change events.

    Exact/near-exact title phrases get a large boost; multi-term queries use
    ``operator=and`` and drop fuzziness so weak edit-summary noise does not
    outrank real title matches.
    """

    tokens = [t for t in query.split() if t]
    multi: dict[str, Any] = {
        "query": query,
        "fields": ["title^4", "summary^2", "last_comment"],
        "type": "best_fields",
    }
    if len(tokens) <= 2:
        multi["fuzziness"] = "AUTO"
    elif len(tokens) <= 4:
        multi["operator"] = "and"
    else:
        # Long conceptual queries should not require every token to match.
        multi["minimum_should_match"] = "60%"

    return [
        {"match_phrase": {"title": {"query": query, "boost": 8.0}}},
        {"multi_match": multi},
    ]


def _build_filters(filters: Optional[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    clauses = _live_doc_filters()
    if not filters:
        return clauses
    for field, value in filters.items():
        if value is None:
            continue
        clauses.append({"term": {field: value}})
    return clauses
