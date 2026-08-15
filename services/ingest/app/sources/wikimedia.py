"""Wikimedia EventStreams source.

Consumes the public, no-auth ``recentchange`` SSE firehose and adapts each
event into a :class:`PageRecord` (Adapter pattern). High volume, free, and a
great demonstration of a real change stream.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
from httpx_sse import connect_sse

from pulsesearch_common.models import PageRecord

_RELEVANT_TYPES = {"edit", "new", "categorize", "log"}


class WikimediaSource:
    name = "wikimedia"

    def __init__(self, stream_url: str, wikis: set[str] | None = None) -> None:
        self._stream_url = stream_url
        # Empty set == accept every wiki.
        self._wikis = wikis or set()

    def stream(self) -> Iterator[PageRecord]:
        # ``read`` timeout is None because SSE is a long-lived connection.
        timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
        with (
            httpx.Client(timeout=timeout, headers={"User-Agent": "PulseSearch/0.1"}) as client,
            connect_sse(client, "GET", self._stream_url) as event_source,
        ):
            for sse in event_source.iter_sse():
                record = self._parse(sse.data)
                if record is not None:
                    yield record

    def _parse(self, raw: str) -> PageRecord | None:
        if not raw:
            return None
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            return None

        if event.get("type") not in _RELEVANT_TYPES:
            return None

        wiki = event.get("wiki") or ""
        if self._wikis and wiki not in self._wikis:
            return None

        title = event.get("title")
        if not title:
            return None

        length = event.get("length") or {}
        return PageRecord(
            wiki=wiki,
            title=title,
            title_url=(event.get("meta") or {}).get("uri") or event.get("title_url"),
            last_comment=event.get("comment") or None,
            last_user=event.get("user") or None,
            event_type=event.get("type"),
            namespace=int(event.get("namespace") or 0),
            is_bot=bool(event.get("bot")),
            is_minor=bool(event.get("minor")),
            length_new=length.get("new"),
            event_time=_parse_time(event),
        )


def _parse_time(event: dict) -> datetime:
    ts = event.get("timestamp")
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=UTC)
    dt = (event.get("meta") or {}).get("dt")
    if isinstance(dt, str):
        try:
            return datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(UTC)
