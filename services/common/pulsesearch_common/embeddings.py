"""Embedding provider abstraction (Strategy pattern).

Consumers depend on the :class:`EmbeddingProvider` protocol, not on a concrete
model, so the embedding backend can be swapped (local sentence-transformers,
a remote service, a fake for tests) without touching call sites (DIP).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .config import embedding_settings


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Turns text into dense vectors."""

    @property
    def dimensions(self) -> int:  # pragma: no cover - trivial
        ...

    def embed(self, text: str) -> list[float]:
        ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        ...


class SentenceTransformerEmbeddings:
    """Local, zero-cost embeddings via ``sentence-transformers``.

    The heavy model is loaded lazily so importing this module (e.g. in the API
    service, which never embeds) stays cheap.
    """

    def __init__(self, model_name: str | None = None) -> None:
        settings = embedding_settings()
        self._model_name = model_name or settings.model_name
        self._dimensions = settings.dimensions
        self._model = None  # lazy

    def _ensure_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name)
            self._dimensions = self._model.get_sentence_embedding_dimension()
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, text: str) -> list[float]:
        model = self._ensure_model()
        vector = model.encode(text, normalize_embeddings=True)
        return vector.tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._ensure_model()
        vectors = model.encode(texts, normalize_embeddings=True, batch_size=32)
        return [v.tolist() for v in vectors]


class HashingEmbeddings:
    """Deterministic, dependency-free fallback embeddings.

    Useful for tests and for running the API without downloading a model. It is
    *not* semantically meaningful, but keeps the vector contract intact.
    """

    def __init__(self, dimensions: int | None = None) -> None:
        self._dimensions = dimensions or embedding_settings().dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, text: str) -> list[float]:
        import hashlib
        import math

        vector = [0.0] * self._dimensions
        for token in text.lower().split():
            digest = hashlib.md5(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "little") % self._dimensions
            vector[idx] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


class CachedEmbeddings:
    """LRU cache around an :class:`EmbeddingProvider` for repeated queries."""

    def __init__(self, inner: EmbeddingProvider, maxsize: int = 512) -> None:
        from collections import OrderedDict

        self._inner = inner
        self._maxsize = maxsize
        self._cache: OrderedDict[str, list[float]] = OrderedDict()

    @property
    def dimensions(self) -> int:
        return self._inner.dimensions

    def embed(self, text: str) -> list[float]:
        key = text.strip().lower()
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        vector = self._inner.embed(text)
        self._cache[key] = vector
        if len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)
        return vector

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]
