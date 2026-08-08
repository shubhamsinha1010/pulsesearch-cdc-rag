"""RAG router: grounded question answering over the live index."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from pulsesearch_common.metrics import RAG_REQUESTS
from pulsesearch_common.models import RAGAnswer

from ..dependencies import get_rag_service
from ..services.rag_service import RAGService

router = APIRouter(prefix="/rag", tags=["rag"])


class RAGRequest(BaseModel):
    question: str = Field(..., min_length=3)
    wiki: Optional[str] = None


@router.post("", response_model=RAGAnswer)
def ask(
    request: RAGRequest,
    service: RAGService = Depends(get_rag_service),
) -> RAGAnswer:
    answer = service.answer(request.question, wiki=request.wiki)
    RAG_REQUESTS.labels(grounded=str(answer.grounded).lower()).inc()
    return answer
