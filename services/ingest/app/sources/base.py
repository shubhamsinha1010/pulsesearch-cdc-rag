"""Firehose source abstraction (Strategy pattern).

A source is any object that yields :class:`PageRecord` instances. New sources
(GitHub events, an internal Kafka topic, a file replay) can be added by
implementing :class:`FirehoseSource` without touching the ingest loop (OCP).
"""

from __future__ import annotations

from typing import Iterator, Protocol, runtime_checkable

from pulsesearch_common.models import PageRecord


@runtime_checkable
class FirehoseSource(Protocol):
    """Yields domain records from an external event stream."""

    name: str

    def stream(self) -> Iterator[PageRecord]:
        """Block and yield records until the stream ends or errors."""
        ...
