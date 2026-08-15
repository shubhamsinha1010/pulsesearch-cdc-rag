"""GitHub public events source (fallback firehose).

Polls the unauthenticated public events API and adapts each push/PR/issue
event into a :class:`PageRecord`. Rate-limited to 60 req/h without a token,
which is enough for a demo. Kept intentionally small; Wikimedia is the primary
source.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx

from pulsesearch_common.models import PageRecord

_API_URL = "https://api.github.com/events"


class GitHubSource:
    name = "github"

    def __init__(self, poll_interval: float = 60.0, token: str | None = None) -> None:
        self._poll_interval = poll_interval
        self._headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "PulseSearch/0.1",
        }
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    def stream(self) -> Iterator[PageRecord]:
        seen: OrderedDict[str, None] = OrderedDict()
        with httpx.Client(timeout=30.0, headers=self._headers) as client:
            while True:
                try:
                    resp = client.get(_API_URL)
                    resp.raise_for_status()
                    for event in resp.json():
                        event_id = event.get("id")
                        if not event_id or event_id in seen:
                            continue
                        seen[event_id] = None
                        record = self._parse(event)
                        if record is not None:
                            yield record
                except httpx.HTTPError:
                    pass
                # Bound the dedupe set to the most recently seen ids.
                while len(seen) > 1000:
                    seen.popitem(last=False)
                time.sleep(self._poll_interval)

    def _parse(self, event: dict) -> PageRecord | None:
        repo = (event.get("repo") or {}).get("name")
        if not repo:
            return None
        actor = (event.get("actor") or {}).get("login")
        return PageRecord(
            wiki="github",
            title=repo,
            title_url=f"https://github.com/{repo}",
            last_comment=event.get("type"),
            last_user=actor,
            event_type=event.get("type"),
            event_time=_parse_time(event.get("created_at")),
        )


def _parse_time(value: str | None) -> datetime:
    if value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(UTC)
