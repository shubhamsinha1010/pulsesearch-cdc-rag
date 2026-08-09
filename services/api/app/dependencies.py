"""Dependency injection wiring for the API.

All object graphs are constructed here and exposed as cached providers. Routers
depend on these via FastAPI's ``Depends`` so handlers stay thin and testable
(each dependency can be overridden in tests).
"""

from __future__ import annotations

from functools import lru_cache

from pulsesearch_common.config import (
    es_settings,
    kafka_settings,
    llm_settings,
)
from pulsesearch_common.embeddings import (
    CachedEmbeddings,
    EmbeddingProvider,
    SentenceTransformerEmbeddings,
)
from pulsesearch_common.es_client import PageRepository

from .services.llm import EchoLLMClient, FallbackLLMClient, GroqClient, LLMClient
from .services.rag_service import RAGService
from .services.search_service import HybridSearchService
from .ws.hub import LiveHub


@lru_cache
def get_repository() -> PageRepository:
    return PageRepository(settings=es_settings())


@lru_cache
def get_embeddings() -> EmbeddingProvider:
    # Queries must use the *same* model the worker used to embed documents.
    # Never fall back to a different embedding space — that silently breaks kNN.
    provider: EmbeddingProvider = CachedEmbeddings(SentenceTransformerEmbeddings())
    provider.embed("warmup")  # force lazy load; surfaces errors early
    return provider


@lru_cache
def get_search_service() -> HybridSearchService:
    return HybridSearchService(get_repository(), get_embeddings())


@lru_cache
def get_llm() -> LLMClient:
    # Groq (hosted, free tier) wrapped in a graceful fallback so RAG stays
    # available even if the API key is missing or the service is unreachable.
    return FallbackLLMClient(GroqClient(llm_settings()), EchoLLMClient())


@lru_cache
def get_rag_service() -> RAGService:
    return RAGService(get_search_service(), get_llm())


@lru_cache
def get_live_hub() -> LiveHub:
    return LiveHub(kafka_settings())
