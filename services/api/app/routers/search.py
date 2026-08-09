"""Search router: hybrid / BM25 / vector retrieval."""

from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from pulsesearch_common.metrics import SEARCH_LATENCY, SEARCH_REQUESTS
from pulsesearch_common.models import SearchHit

from ..dependencies import get_search_service
from ..services.search_service import HybridSearchService, SearchMode

router = APIRouter(prefix="/search", tags=["search"])


class SearchResponse(BaseModel):
    query: str
    mode: SearchMode
    count: int
    took_ms: float
    hits: list[SearchHit]


@router.get("", response_model=SearchResponse)
def search(
    q: str = Query(..., min_length=1, description="Search query"),
    size: int = Query(10, ge=1, le=50),
    mode: SearchMode = Query(SearchMode.HYBRID),
    wiki: Optional[str] = Query(None, description="Restrict to a single wiki"),
    namespace: Optional[int] = Query(
        0,
        description="Wikipedia namespace (default 0 = main articles). "
        "Use -1 to search all namespaces.",
    ),
    service: HybridSearchService = Depends(get_search_service),
) -> SearchResponse:
    started = time.perf_counter()
    ns = None if namespace is not None and namespace < 0 else namespace
    hits = service.search(
        query=q, size=size, mode=mode, wiki=wiki, namespace=ns
    )
    took_ms = (time.perf_counter() - started) * 1000

    SEARCH_REQUESTS.labels(mode=mode.value).inc()
    SEARCH_LATENCY.labels(mode=mode.value).observe(took_ms / 1000)

    return SearchResponse(
        query=q,
        mode=mode,
        count=len(hits),
        took_ms=round(took_ms, 2),
        hits=hits,
    )
