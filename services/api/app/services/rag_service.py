"""Grounded Retrieval-Augmented Generation service.

Pipeline: hybrid retrieval -> context assembly -> LLM generation ->
citation + freshness attribution. The service enforces grounding: if retrieval
returns nothing it refuses to answer rather than hallucinate.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pulsesearch_common.models import Citation, PageDocument, RAGAnswer

from .llm import LLMClient
from .search_service import HybridSearchService, SearchMode

_SYSTEM_PROMPT = (
    "You are PulseSearch, an assistant that answers questions strictly from the "
    "provided context of recently changed pages. Only use the context. If the "
    "context does not contain the answer, say you don't know. Cite sources by "
    "their [n] index."
)


class RAGService:
    def __init__(
        self,
        search: HybridSearchService,
        llm: LLMClient,
        top_k: int = 5,
    ) -> None:
        self._search = search
        self._llm = llm
        self._top_k = top_k

    def answer(self, question: str, wiki: Optional[str] = None) -> RAGAnswer:
        hits = self._search.search(
            query=question,
            size=self._top_k,
            mode=SearchMode.HYBRID,
            wiki=wiki,
        )

        if not hits:
            return RAGAnswer(
                answer="I don't have any indexed context relevant to that question yet.",
                citations=[],
                grounded=False,
            )

        documents = [hit.document for hit in hits]
        prompt = self._build_prompt(question, documents)
        answer_text = self._llm.generate(prompt, system=_SYSTEM_PROMPT)

        return RAGAnswer(
            answer=answer_text,
            citations=[_to_citation(doc) for doc in documents],
            grounded=True,
            freshest_source=_freshest(documents),
        )

    def _build_prompt(self, question: str, documents: list[PageDocument]) -> str:
        lines = []
        for idx, doc in enumerate(documents, start=1):
            when = doc.event_time.isoformat() if doc.event_time else "unknown time"
            lines.append(
                f"[{idx}] ({doc.wiki}, {when}) {doc.title}: "
                f"{(doc.last_comment or '').strip()}"
            )
        context = "\n".join(lines)
        return (
            f"QUESTION: {question}\n\n"
            f"CONTEXT:\n{context}\n\n"
            "Answer the question using only the context above and cite sources "
            "with [n]."
        )


def _to_citation(doc: PageDocument) -> Citation:
    return Citation(
        id=doc.id,
        title=doc.title,
        title_url=doc.title_url,
        wiki=doc.wiki,
        event_time=doc.event_time,
    )


def _freshest(documents: list[PageDocument]) -> Optional[datetime]:
    times = [d.event_time for d in documents if d.event_time]
    if not times:
        return None
    return max(times).astimezone(timezone.utc)
