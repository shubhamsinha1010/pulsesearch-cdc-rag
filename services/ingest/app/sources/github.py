"""GitHub public events source (fallback firehose).

Polls the unauthenticated public events API and adapts each push/PR/issue
event into a :class:`PageRecord`. Rate-limited to 60 req/h without a token,
which is enough for a demo. Kept intentionally small; Wikimedia is the primary
source.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Iterator, Optional

import httpx

from pulsesearch_common.models import PageRecord

_API_URL = "https://api.github.com/events"


class GitHubSource:
    name = "github"

    def __init__(self, poll_interval: float = 60.0, token: Optional[str] = None) -> None:
        self._poll_interval = poll_interval
        self._headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "PulseSearch/0.1",
        }
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    def stream(self) -> Iterator[PageRecord]:
        seen: set[str] = set()
        with httpx.Client(timeout=30.0, headers=self._headers) as client:
            while True:
                try:
                    resp = client.get(_API_URL)
                    resp.raise_for_status()
                    for event in resp.json():
                        event_id = event.get("id")
                        if event_id in seen:
                            continue
                        seen.add(event_id)
                        record = self._parse(event)
                        if record is not None:
                            yield record
                except httpx.HTTPError:
                    pass
                # Bound the dedupe set so it does not grow forever.
                if len(seen) > 5000:
                    seen = set(list(seen)[-1000:])
                time.sleep(self._poll_interval)

    def _parse(self, event: dict) -> Optional[PageRecord]:
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


def _parse_time(value: Optional[str]) -> datetime:
    if value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)
