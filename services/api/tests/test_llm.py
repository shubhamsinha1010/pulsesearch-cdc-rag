"""Tests for LLM client behaviour and graceful fallback."""

from __future__ import annotations

import pytest

from pulsesearch_common.config import LLMSettings

from app.services.llm import EchoLLMClient, FallbackLLMClient, GroqClient


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
    prompt = "QUESTION: q\n\nCONTEXT:\n- Alan Turing: crypto\n"
    answer = fallback.generate(prompt)
    assert "Based on the most recent indexed changes" in answer
    # health is true because the Echo fallback is always healthy.
    assert fallback.health() is True
