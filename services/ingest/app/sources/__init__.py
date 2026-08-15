"""Firehose source factory.

Selecting a source by name keeps ``main`` free of conditional wiring and makes
adding a source a one-line registry change (Factory + Registry).
"""

from __future__ import annotations

from collections.abc import Callable

from pulsesearch_common.config import IngestSettings

from .base import FirehoseSource
from .github import GitHubSource
from .wikimedia import WikimediaSource


def _build_wikimedia(settings: IngestSettings) -> FirehoseSource:
    wikis = {w.strip() for w in settings.wikis.split(",") if w.strip()}
    return WikimediaSource(stream_url=settings.stream_url, wikis=wikis)


def _build_github(settings: IngestSettings) -> FirehoseSource:
    return GitHubSource()


_REGISTRY: dict[str, Callable[[IngestSettings], FirehoseSource]] = {
    "wikimedia": _build_wikimedia,
    "github": _build_github,
}


def create_source(settings: IngestSettings) -> FirehoseSource:
    try:
        factory = _REGISTRY[settings.source.lower()]
    except KeyError as exc:
        raise ValueError(
            f"Unknown ingest source '{settings.source}'. Available: {', '.join(sorted(_REGISTRY))}"
        ) from exc
    return factory(settings)


__all__ = ["FirehoseSource", "create_source"]
