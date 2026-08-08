"""Health and readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
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
    repo: PageRepository = Depends(get_repository),
    llm: LLMClient = Depends(get_llm),
) -> ReadyResponse:
    es_ok = True
    doc_count = 0
    try:
        doc_count = repo.count()
    except Exception:  # noqa: BLE001
        es_ok = False
    return ReadyResponse(
        status="ok" if es_ok else "degraded",
        elasticsearch=es_ok,
        documents=doc_count,
        llm=llm.health(),
    )
