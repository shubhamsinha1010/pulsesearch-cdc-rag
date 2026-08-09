"""Tests for grounded RAG refusal + citation filtering."""

from __future__ import annotations

from app.services.rag_service import RAGService
from app.services.search_service import SearchMode
from pulsesearch_common.models import PageDocument, SearchHit


class FakeSearch:
    def __init__(self, hits):
        self._hits = hits
        self.last_kwargs = None

    def search(self, **kwargs):
        self.last_kwargs = kwargs
        return self._hits


class FakeLLM:
    def __init__(self, text: str):
        self._text = text

    def generate(self, prompt, system=None):
        return self._text

    def health(self):
        return True


def _hit(doc_id: str, title: str) -> SearchHit:
    return SearchHit(
        id=doc_id,
        score=0.05,
        document=PageDocument(id=doc_id, wiki="enwiki", title=title, namespace=0),
    )


def test_empty_retrieval_is_ungrounded():
    service = RAGService(FakeSearch([]), FakeLLM("should not run"))
    answer = service.answer("anything")
    assert answer.grounded is False
    assert answer.citations == []


def test_refusal_answer_is_ungrounded():
    hits = [_hit("1", "Climate change")]
    service = RAGService(
        FakeSearch(hits), FakeLLM("I don't know based on the indexed changes.")
    )
    answer = service.answer("Who invented the telephone?")
    assert answer.grounded is False
    assert answer.citations == []


def test_answer_without_citations_is_ungrounded():
    hits = [_hit("1", "Climate change")]
    service = RAGService(
        FakeSearch(hits), FakeLLM("Climate change is happening worldwide.")
    )
    answer = service.answer("What about climate?")
    assert answer.grounded is False
    assert answer.citations == []


def test_cited_answer_keeps_only_referenced_sources():
    hits = [_hit("1", "Climate change"), _hit("2", "El Niño"), _hit("3", "Unrelated")]
    service = RAGService(
        FakeSearch(hits),
        FakeLLM("Recent edits cover climate topics [1] and El Niño [2]."),
    )
    answer = service.answer("Summarize climate edits")
    assert answer.grounded is True
    assert [c.title for c in answer.citations] == ["Climate change", "El Niño"]
    assert service._search.last_kwargs["namespace"] == 0
    assert service._search.last_kwargs["mode"] is SearchMode.HYBRID
