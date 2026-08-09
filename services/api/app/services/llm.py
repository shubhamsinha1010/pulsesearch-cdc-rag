"""LLM client abstraction (Strategy pattern).

RAG depends on the :class:`LLMClient` protocol so the generation backend can be
swapped (a hosted model like Groq, a deterministic fake for tests) without
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


class GroqClient:
    """Talks to Groq's OpenAI-compatible chat API (hosted, free tier).

    Requires ``GROQ_API_KEY``. Fast inference; no local GPU needed. Because it
    speaks the OpenAI schema, swapping to any other OpenAI-compatible provider
    is just a base-url/model change (OCP).
    """

    def __init__(self, settings: LLMSettings) -> None:
        self._settings = settings
        self._client = httpx.Client(
            timeout=settings.request_timeout,
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
        )

    def generate(self, prompt: str, system: str | None = None) -> str:
        if not self._settings.groq_api_key:
            raise RuntimeError("GROQ_API_KEY is not configured")
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload = {
            "model": self._settings.groq_model,
            "messages": messages,
            "temperature": 0.1,
            "stream": False,
        }
        resp = self._client.post(
            f"{self._settings.groq_base_url}/chat/completions", json=payload
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()

    def health(self) -> bool:
        if not self._settings.groq_api_key:
            return False
        try:
            resp = self._client.get(f"{self._settings.groq_base_url}/models")
            return resp.status_code == 200
        except httpx.HTTPError:
            return False


class EchoLLMClient:
    """Deterministic fallback used when no LLM is available.

    Produces an extractive answer from the provided context so the RAG endpoint
    stays functional (and demonstrable) even without a real LLM configured.
    """

    def generate(self, prompt: str, system: str | None = None) -> str:  # noqa: D401
        marker = "CONTEXT:"
        if marker in prompt:
            context = prompt.split(marker, 1)[1]
            if "\n\nAnswer the question" in context:
                context = context.split("\n\nAnswer the question", 1)[0]
            lines = [
                line.strip("- ").strip()
                for line in context.strip().splitlines()
                if line.strip().startswith("[")
            ]
            head = " ".join(lines[:3])
            return (
                "Based on the most recent indexed changes: "
                + (head[:400] if head else "no relevant context was found.")
            )
        return "No language model is currently available to generate an answer."

    def health(self) -> bool:
        return True


class FallbackLLMClient:
    """Delegates to a primary LLM and degrades gracefully to a fallback.

    Keeps the RAG endpoint available even if the Groq API key is missing or the
    service is unreachable, so the project is always demonstrable (graceful
    degradation).
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
        # Report primary health only so the UI LED means "hosted LLM is live".
        return self._primary.health()
