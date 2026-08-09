"""Shared text helpers for indexing and retrieval quality."""

from __future__ import annotations

import re

# Wikipedia edit summaries are often template/MOS noise that hurts embeddings.
_BOILERPLATE_PATTERNS = (
    re.compile(r"use american english", re.I),
    re.compile(r"use british english", re.I),
    re.compile(r"\[\[mos:", re.I),
    re.compile(r"added .+ template", re.I),
    re.compile(r"categor(y|ies) added", re.I),
    re.compile(r"^/\*.*\*/\s*$"),
    re.compile(r"^revert(ed|ing)?\b", re.I),
    re.compile(r"^undid revision\b", re.I),
)

_WS_RE = re.compile(r"\s+")


def clean_edit_comment(comment: str | None) -> str:
    """Return a usable edit summary, or empty string when it is boilerplate."""

    text = _WS_RE.sub(" ", (comment or "").strip())
    if not text:
        return ""
    if any(pattern.search(text) for pattern in _BOILERPLATE_PATTERNS):
        return ""
    return text


def build_searchable_text(
    title: str,
    *,
    summary: str | None = None,
    last_comment: str | None = None,
) -> str:
    """Compose the text that goes into the dense embedding.

    Prefer page summary over edit comments; repeat the title so identity stays
    strong when the body text is short.
    """

    title = (title or "").strip()
    summary = _WS_RE.sub(" ", (summary or "").strip())
    comment = clean_edit_comment(last_comment)

    parts: list[str] = []
    if title:
        parts.append(f"{title} {title}")
    if summary:
        parts.append(summary[:800])
    elif comment:
        parts.append(comment[:400])
    return " — ".join(parts).strip()
