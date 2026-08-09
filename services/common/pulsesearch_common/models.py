"""Domain models shared across services.

These are transport-agnostic representations of the two core concepts:

* :class:`PageRecord` — a row in the MySQL system-of-record.
* :class:`PageDocument` — the denormalised, embedding-enriched document that
  lives in Elasticsearch and is returned to clients.

Keeping these here means the ingest, worker and api services agree on one
schema and one (de)serialisation contract (Single Source of Truth).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

from .text import build_searchable_text, clean_edit_comment


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PageRecord(BaseModel):
    """A page in the MySQL system-of-record (one row of the ``pages`` table)."""

    wiki: str
    title: str
    title_url: Optional[str] = None
    last_comment: Optional[str] = None
    last_user: Optional[str] = None
    event_type: Optional[str] = None
    namespace: int = 0
    is_bot: bool = False
    is_minor: bool = False
    length_new: Optional[int] = None
    event_time: Optional[datetime] = None
    summary: Optional[str] = None

    def searchable_text(self) -> str:
        """Concatenate the fields we want the embedding model to see."""

        return build_searchable_text(
            self.title,
            summary=self.summary,
            last_comment=self.last_comment,
        )


class PageDocument(BaseModel):
    """The Elasticsearch document representation of a page."""

    id: str
    wiki: str
    title: str
    title_url: Optional[str] = None
    last_comment: Optional[str] = None
    last_user: Optional[str] = None
    event_type: Optional[str] = None
    namespace: int = 0
    is_bot: bool = False
    is_minor: bool = False
    length_new: Optional[int] = None
    edit_count: int = 1
    event_time: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=utcnow)
    # Optional Wikipedia lead/summary used to ground embeddings + RAG.
    summary: Optional[str] = None
    # Monotonic version used for idempotent, out-of-order-safe upserts.
    version: int = 0
    # Soft-delete tombstone so deletes stay version-guarded like upserts.
    deleted: bool = False
    embedding: Optional[list[float]] = None

    def to_source(self, include_embedding: bool = True) -> dict[str, Any]:
        data = self.model_dump(exclude_none=True)
        if not include_embedding:
            data.pop("embedding", None)
        # Persist cleaned comments so retrieval/RAG do not surface MOS noise.
        if "last_comment" in data:
            cleaned = clean_edit_comment(data.get("last_comment"))
            if cleaned:
                data["last_comment"] = cleaned
            else:
                data.pop("last_comment", None)
        return data

    def searchable_text(self) -> str:
        return build_searchable_text(
            self.title,
            summary=self.summary,
            last_comment=self.last_comment,
        )


class SearchHit(BaseModel):
    """A single hybrid-search result with provenance for ranking transparency."""

    id: str
    score: float
    document: PageDocument
    bm25_rank: Optional[int] = None
    knn_rank: Optional[int] = None


class Citation(BaseModel):
    """A grounded citation returned by the RAG endpoint."""

    id: str
    title: str
    title_url: Optional[str] = None
    wiki: str
    event_time: Optional[datetime] = None


class RAGAnswer(BaseModel):
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    grounded: bool = True
    freshest_source: Optional[datetime] = None
