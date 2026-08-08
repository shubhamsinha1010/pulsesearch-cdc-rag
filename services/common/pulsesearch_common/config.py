"""Typed application configuration.

Uses ``pydantic-settings`` so every service reads the same environment
variables with validation and sensible defaults. Each service instantiates
only the settings section it needs, but the definitions live here to avoid
drift between services (Single Source of Truth).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class _Base(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


class MySQLSettings(_Base):
    host: str = Field(default="mysql", alias="MYSQL_HOST")
    port: int = Field(default=3306, alias="MYSQL_PORT")
    user: str = Field(default="pulse", alias="MYSQL_USER")
    password: str = Field(default="pulse", alias="MYSQL_PASSWORD")
    database: str = Field(default="pulsesearch", alias="MYSQL_DATABASE")
    pool_size: int = Field(default=5, alias="MYSQL_POOL_SIZE")

    @property
    def dsn(self) -> str:
        return (
            f"mysql+pymysql://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}?charset=utf8mb4"
        )


class KafkaSettings(_Base):
    bootstrap_servers: str = Field(default="redpanda:9092", alias="KAFKA_BOOTSTRAP_SERVERS")
    group_id: str = Field(default="pulsesearch-sync", alias="KAFKA_GROUP_ID")
    # Debezium publishes to ``<server>.<db>.<table>`` — for this project:
    source_topic: str = Field(default="pulse.pulsesearch.pages", alias="KAFKA_SOURCE_TOPIC")
    dlq_topic: str = Field(default="pulsesearch.dlq", alias="KAFKA_DLQ_TOPIC")
    auto_offset_reset: str = Field(default="earliest", alias="KAFKA_AUTO_OFFSET_RESET")


class ElasticsearchSettings(_Base):
    url: str = Field(default="http://elasticsearch:9200", alias="ES_URL")
    index: str = Field(default="pages", alias="ES_INDEX")
    request_timeout: int = Field(default=30, alias="ES_REQUEST_TIMEOUT")


class EmbeddingSettings(_Base):
    model_name: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2", alias="EMBEDDING_MODEL"
    )
    dimensions: int = Field(default=384, alias="EMBEDDING_DIMENSIONS")


class LLMSettings(_Base):
    base_url: str = Field(default="http://ollama:11434", alias="OLLAMA_BASE_URL")
    model: str = Field(default="llama3.2:3b", alias="OLLAMA_MODEL")
    request_timeout: int = Field(default=120, alias="OLLAMA_TIMEOUT")


class IngestSettings(_Base):
    source: str = Field(default="wikimedia", alias="INGEST_SOURCE")
    stream_url: str = Field(
        default="https://stream.wikimedia.org/v2/stream/recentchange",
        alias="INGEST_STREAM_URL",
    )
    # Keep local demos light: only persist a subset of wikis by default.
    wikis: str = Field(default="enwiki", alias="INGEST_WIKIS")
    batch_size: int = Field(default=50, alias="INGEST_BATCH_SIZE")
    flush_interval_seconds: float = Field(default=2.0, alias="INGEST_FLUSH_INTERVAL")


class ObservabilitySettings(_Base):
    metrics_port: int = Field(default=9100, alias="METRICS_PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_json: bool = Field(default=True, alias="LOG_JSON")


@lru_cache
def mysql_settings() -> MySQLSettings:
    return MySQLSettings()


@lru_cache
def kafka_settings() -> KafkaSettings:
    return KafkaSettings()


@lru_cache
def es_settings() -> ElasticsearchSettings:
    return ElasticsearchSettings()


@lru_cache
def embedding_settings() -> EmbeddingSettings:
    return EmbeddingSettings()


@lru_cache
def llm_settings() -> LLMSettings:
    return LLMSettings()


@lru_cache
def ingest_settings() -> IngestSettings:
    return IngestSettings()


@lru_cache
def observability_settings() -> ObservabilitySettings:
    return ObservabilitySettings()
