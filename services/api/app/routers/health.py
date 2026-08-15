"""Health and readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from pulsesearch_common.es_client import PageRepository

from ..dependencies import get_llm, get_repository
from ..services.llm import LLMClient

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str


class ReadyResponse(BaseModel):
    status: str
    elasticsearch: bool
    documents: int
    llm: bool


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/health/ready", response_model=ReadyResponse)
def ready(
    response: Response,
    repo: PageRepository = Depends(get_repository),
    llm: LLMClient = Depends(get_llm),
) -> ReadyResponse:
    """Readiness for load balancers / kube probes.

    Elasticsearch is required to serve search; LLM may be down (RAG degraded).
    Returns HTTP 503 when ES is unreachable so readinessProbe fails correctly.
    """

    es_ok = True
    doc_count = 0
    try:
        doc_count = repo.count()
    except Exception:
        es_ok = False
    if not es_ok:
        response.status_code = 503
    return ReadyResponse(
        status="ok" if es_ok else "unavailable",
        elasticsearch=es_ok,
        documents=doc_count,
        llm=llm.health(),
    )
