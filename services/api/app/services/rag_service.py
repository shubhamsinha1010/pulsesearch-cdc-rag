"""Grounded Retrieval-Augmented Generation service.

Pipeline: hybrid retrieval -> context assembly -> LLM generation ->
citation + freshness attribution. The service enforces grounding: if retrieval
returns nothing (or is too weak) it refuses to answer rather than hallucinate,
and post-checks the model output for unsupported / refusal answers.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from pulsesearch_common.models import Citation, PageDocument, RAGAnswer

from .llm import LLMClient
from .search_service import HybridSearchService, SearchMode

_SYSTEM_PROMPT = (
    "You are PulseSearch, an assistant that answers questions strictly from the "
    "provided context of recently changed Wikipedia pages. "
    "Rules:\n"
    "1. Use ONLY the context. Do not use prior knowledge.\n"
    "2. If the context does not contain enough information, reply exactly: "
    "I don't know based on the indexed changes.\n"
    "3. Every factual sentence must end with a citation like [1] or [2].\n"
    "4. Never invent page titles, users, or edit details that are not in context."
)

_REFUSAL_PATTERNS = (
    re.compile(r"\bi don't know\b", re.IGNORECASE),
    re.compile(r"\bdo not know\b", re.IGNORECASE),
    re.compile(r"\bdon't have (any |enough )?indexed\b", re.IGNORECASE),
    re.compile(r"\bnot (enough|sufficient) (information|context)\b", re.IGNORECASE),
    re.compile(r"\bcontext (does not|doesn't|provided does not)\b", re.IGNORECASE),
)

_CITATION_RE = re.compile(r"\[(\d+)\]")


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

    def answer(self, question: str, wiki: str | None = None) -> RAGAnswer:
        # Prefer main-article namespace — User/Talk pages create false retrievals.
        hits = self._search.search(
            query=question,
            size=self._top_k,
            mode=SearchMode.HYBRID,
            wiki=wiki,
            namespace=0,
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
        return self._finalize(answer_text, documents)

    def _build_prompt(self, question: str, documents: list[PageDocument]) -> str:
        lines = []
        for idx, doc in enumerate(documents, start=1):
            when = doc.event_time.isoformat() if doc.event_time else "unknown time"
            comment = (doc.last_comment or "").strip() or "(no edit summary)"
            summary = (doc.summary or "").strip()
            summary_bit = f" summary={summary[:280]!r}" if summary else ""
            lines.append(
                f"[{idx}] title={doc.title!r} wiki={doc.wiki} when={when} "
                f"user={doc.last_user or 'unknown'} edit_summary={comment!r}"
                f"{summary_bit}"
            )
        context = "\n".join(lines)
        return (
            f"QUESTION: {question}\n\n"
            f"CONTEXT:\n{context}\n\n"
            "Answer using only the context. Cite sources with [n]. "
            "If you cannot answer from the context, say you don't know."
        )

    def _finalize(self, answer_text: str, documents: list[PageDocument]) -> RAGAnswer:
        text = (answer_text or "").strip()
        if not text or _looks_like_refusal(text):
            return RAGAnswer(
                answer=text or "I don't know based on the indexed changes.",
                citations=[],
                grounded=False,
            )

        cited_idxs = {
            int(m.group(1))
            for m in _CITATION_RE.finditer(text)
            if 1 <= int(m.group(1)) <= len(documents)
        }
        if not cited_idxs:
            # Model answered without citations — treat as ungrounded.
            return RAGAnswer(
                answer=(
                    "I don't know based on the indexed changes. "
                    "(Retrieved context was too weak to ground an answer.)"
                ),
                citations=[],
                grounded=False,
            )

        cited_docs = [documents[i - 1] for i in sorted(cited_idxs)]
        return RAGAnswer(
            answer=text,
            citations=[_to_citation(doc) for doc in cited_docs],
            grounded=True,
            freshest_source=_freshest(cited_docs),
        )


def _looks_like_refusal(text: str) -> bool:
    return any(pattern.search(text) for pattern in _REFUSAL_PATTERNS)


def _to_citation(doc: PageDocument) -> Citation:
    return Citation(
        id=doc.id,
        title=doc.title,
        title_url=doc.title_url,
        wiki=doc.wiki,
        event_time=doc.event_time,
    )


def _freshest(documents: list[PageDocument]) -> datetime | None:
    times = [d.event_time for d in documents if d.event_time]
    if not times:
        return None
    return max(times).astimezone(UTC)
