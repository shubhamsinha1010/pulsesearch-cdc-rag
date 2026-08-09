"""Fetch short Wikipedia lead extracts to enrich embeddings (Strategy helper).

Uses the public Wikimedia REST summary API — no auth, free, rate-limit friendly
with caching. Failures are soft: indexing continues with title/comment only.
"""

from __future__ import annotations

import logging
import urllib.parse
from collections import OrderedDict
from typing import Optional, Protocol

import httpx

log = logging.getLogger("worker.enrichment")

_USER_AGENT = (
    "PulseSearch/1.0 (local CDC+search demo; https://github.com/shubhamsinha1010/"
    "pulsesearch-cdc-rag)"
)


class SummaryProvider(Protocol):
    def fetch(self, wiki: str, title: str) -> Optional[str]:
        ...


class WikipediaSummaryClient:
    """LRU-cached Wikipedia page summaries for main-namespace articles."""

    def __init__(self, cache_size: int = 2048, timeout: float = 2.5) -> None:
        self._cache_size = cache_size
        self._cache: OrderedDict[str, Optional[str]] = OrderedDict()
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            follow_redirects=True,
        )

    def fetch(self, wiki: str, title: str) -> Optional[str]:
        key = f"{wiki}:{title}"
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]

        summary = self._fetch_uncached(wiki, title)
        self._cache[key] = summary
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return summary

    def _fetch_uncached(self, wiki: str, title: str) -> Optional[str]:
        lang = _wiki_to_lang(wiki)
        if not lang or not title:
            return None
        # Skip obvious non-article titles even if namespace is wrong upstream.
        if title.startswith(("User:", "Talk:", "Wikipedia:", "Template:", "Category:")):
            return None

        encoded = urllib.parse.quote(title.replace(" ", "_"), safe="()'")
        url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{encoded}"
        try:
            resp = self._client.get(url)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            if data.get("type") in {"disambiguation", "no-extract"}:
                extract = (data.get("description") or "").strip()
            else:
                extract = (data.get("extract") or data.get("description") or "").strip()
            return extract[:1000] or None
        except Exception as exc:  # noqa: BLE001 - enrichment must not break CDC
            log.debug("summary fetch failed", extra={"title": title, "error": str(exc)})
            return None


class NullSummaryClient:
    def fetch(self, wiki: str, title: str) -> Optional[str]:
        return None


def _wiki_to_lang(wiki: str) -> Optional[str]:
    # enwiki -> en, dewiki -> de; ignore non-wikipedia projects.
    if not wiki.endswith("wiki"):
        return None
    lang = wiki[: -len("wiki")]
    return lang or None
