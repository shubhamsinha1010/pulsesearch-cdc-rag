"""LLM client abstraction (Strategy pattern).

RAG depends on the :class:`LLMClient` protocol so the generation backend can be
swapped (local Ollama, a hosted model, a deterministic fake for tests) without
changing the RAG service (DIP/OCP).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import httpx

from pulsesearch_common.config import LLMSettings


@runtime_checkable
class LLMClient(Protocol):
    def generate(self, prompt: str, system: str | None = None) -> str:
        ...

    def health(self) -> bool:
        ...


class OllamaClient:
    """Talks to a local Ollama server (free, offline-capable)."""

    def __init__(self, settings: LLMSettings) -> None:
        self._settings = settings
        self._client = httpx.Client(timeout=settings.request_timeout)

    def generate(self, prompt: str, system: str | None = None) -> str:
        payload = {
            "model": self._settings.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1},
        }
        if system:
            payload["system"] = system
        resp = self._client.post(f"{self._settings.base_url}/api/generate", json=payload)
        resp.raise_for_status()
        return resp.json().get("response", "").strip()

    def health(self) -> bool:
        try:
            resp = self._client.get(f"{self._settings.base_url}/api/tags")
            return resp.status_code == 200
        except httpx.HTTPError:
            return False


class EchoLLMClient:
    """Deterministic fallback used when no LLM is available.

    Produces an extractive answer from the provided context so the RAG endpoint
    stays functional (and demonstrable) even without Ollama running.
    """

    def generate(self, prompt: str, system: str | None = None) -> str:  # noqa: D401
        marker = "CONTEXT:"
        if marker in prompt:
            context = prompt.split(marker, 1)[1]
            snippet = context.strip().splitlines()
            head = " ".join(line.strip("- ") for line in snippet[:3])
            return (
                "Based on the most recent indexed changes: "
                + (head[:400] if head else "no relevant context was found.")
            )
        return "No language model is currently available to generate an answer."

    def health(self) -> bool:
        return True


class FallbackLLMClient:
    """Delegates to a primary LLM and degrades gracefully to a fallback.

    Keeps the RAG endpoint available even if the local Ollama server is not
    running, so the project is always demonstrable (graceful degradation).
    """

    def __init__(self, primary: LLMClient, fallback: LLMClient) -> None:
        self._primary = primary
        self._fallback = fallback

    def generate(self, prompt: str, system: str | None = None) -> str:
        try:
            return self._primary.generate(prompt, system=system)
        except Exception:  # noqa: BLE001 - fall back rather than 500
            return self._fallback.generate(prompt, system=system)

    def health(self) -> bool:
        return self._primary.health() or self._fallback.health()
