"""Tests for the Wikimedia EventStreams -> domain adapter."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.sources.wikimedia import WikimediaSource, _parse_time


def _event(**overrides) -> dict:
    event = {
        "type": "edit",
        "wiki": "enwiki",
        "title": "Alan Turing",
        "comment": "fix grammar",
        "user": "SomeEditor",
        "namespace": 0,
        "bot": False,
        "minor": True,
        "length": {"old": 100, "new": 120},
        "meta": {"uri": "https://en.wikipedia.org/wiki/Alan_Turing", "dt": "2026-08-30T10:00:00Z"},
        "timestamp": 1_788_000_000,
    }
    event.update(overrides)
    return event


def _source(wikis: set[str] | None = None) -> WikimediaSource:
    return WikimediaSource(stream_url="https://example.invalid/stream", wikis=wikis)


def _parse(source: WikimediaSource, event: dict):
    return source._parse(json.dumps(event))


def test_parse_maps_event_to_page_record():
    record = _parse(_source({"enwiki"}), _event())
    assert record is not None
    assert record.wiki == "enwiki"
    assert record.title == "Alan Turing"
    assert record.title_url == "https://en.wikipedia.org/wiki/Alan_Turing"
    assert record.last_comment == "fix grammar"
    assert record.last_user == "SomeEditor"
    assert record.event_type == "edit"
    assert record.namespace == 0
    assert record.is_bot is False
    assert record.is_minor is True
    assert record.length_new == 120


def test_empty_and_malformed_payloads_are_skipped():
    source = _source()
    assert source._parse("") is None
    assert source._parse("not json") is None


def test_irrelevant_event_types_are_skipped():
    assert _parse(_source(), _event(type="abusefilter")) is None


def test_relevant_event_types_are_kept():
    source = _source()
    for event_type in ("edit", "new", "categorize", "log"):
        assert _parse(source, _event(type=event_type)) is not None


def test_wiki_filter_rejects_other_wikis():
    source = _source({"enwiki"})
    assert _parse(source, _event(wiki="dewiki")) is None
    assert _parse(source, _event(wiki="enwiki")) is not None


def test_empty_wiki_filter_accepts_every_wiki():
    # An empty set means "accept everything" rather than "accept nothing".
    source = _source(set())
    assert _parse(source, _event(wiki="dewiki")) is not None


def test_events_without_a_title_are_skipped():
    assert _parse(_source(), _event(title=None)) is None
    assert _parse(_source(), _event(title="")) is None


def test_blank_comment_and_user_become_none():
    record = _parse(_source(), _event(comment="", user=""))
    assert record is not None
    assert record.last_comment is None
    assert record.last_user is None


def test_title_url_falls_back_when_meta_uri_missing():
    record = _parse(_source(), _event(meta={}, title_url="https://fallback.example/wiki/X"))
    assert record is not None
    assert record.title_url == "https://fallback.example/wiki/X"


def test_missing_length_block_yields_no_new_length():
    record = _parse(_source(), _event(length={}))
    assert record is not None
    assert record.length_new is None


def test_namespace_and_flags_are_coerced():
    record = _parse(_source(), _event(namespace="14", bot=1, minor=0))
    assert record is not None
    assert record.namespace == 14
    assert record.is_bot is True
    assert record.is_minor is False


def test_parse_time_prefers_numeric_timestamp():
    assert _parse_time({"timestamp": 1_788_000_000}) == datetime.fromtimestamp(
        1_788_000_000, tz=UTC
    )


def test_parse_time_falls_back_to_meta_dt():
    parsed = _parse_time({"meta": {"dt": "2026-08-30T10:00:00Z"}})
    assert parsed == datetime(2026, 8, 30, 10, 0, tzinfo=UTC)


def test_parse_time_defaults_to_now_when_unusable():
    # Neither a timestamp nor a parseable dt: must still return an aware value
    # so downstream MySQL writes never see a naive datetime.
    parsed = _parse_time({"meta": {"dt": "not-a-timestamp"}})
    assert parsed.tzinfo is not None
