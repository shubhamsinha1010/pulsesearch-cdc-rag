"""Tests for LLM client behaviour and graceful fallback."""

from __future__ import annotations

import pytest

from app.services.llm import EchoLLMClient, FallbackLLMClient, GroqClient
from pulsesearch_common.config import LLMSettings


def test_groq_requires_api_key_to_generate():
    client = GroqClient(LLMSettings(GROQ_API_KEY=""))
    with pytest.raises(RuntimeError):
        client.generate("hello")


def test_groq_health_is_false_without_key():
    client = GroqClient(LLMSettings(GROQ_API_KEY=""))
    assert client.health() is False


class _BoomClient:
    def generate(self, prompt: str, system: str | None = None) -> str:
        raise RuntimeError("provider down")

    def health(self) -> bool:
        return False


def test_fallback_uses_secondary_on_primary_failure():
    fallback = FallbackLLMClient(_BoomClient(), EchoLLMClient())
    prompt = (
        "QUESTION: q\n\nCONTEXT:\n[1] (enwiki, t) Alan Turing: crypto\n\n"
        "Answer the question using only the context above and cite sources "
        "with [n]."
    )
    answer = fallback.generate(prompt)
    assert "Based on the most recent indexed changes" in answer
    assert "Alan Turing" in answer
    assert "Answer the question" not in answer
    # health reflects the primary provider, not the Echo fallback.
    assert fallback.health() is False
