"""Tests for the firehose source registry."""

from __future__ import annotations

import pytest

from app.sources import create_source
from app.sources.wikimedia import WikimediaSource
from pulsesearch_common.config import IngestSettings


def _settings(**overrides) -> IngestSettings:
    # Field aliases are the env var names, and the model does not allow
    # population by field name.
    values = {
        "INGEST_SOURCE": "wikimedia",
        "INGEST_STREAM_URL": "https://example.invalid/stream",
        "INGEST_WIKIS": "enwiki",
    }
    values.update(overrides)
    return IngestSettings(**values)


def test_wikimedia_source_is_built_from_settings():
    source = create_source(_settings())
    assert isinstance(source, WikimediaSource)
    assert source.name == "wikimedia"


def test_source_name_is_case_insensitive():
    assert create_source(_settings(INGEST_SOURCE="WikiMedia")).name == "wikimedia"


def test_wikis_are_parsed_into_a_set_ignoring_whitespace_and_blanks():
    source = create_source(_settings(INGEST_WIKIS=" enwiki , dewiki ,, "))
    assert source._wikis == {"enwiki", "dewiki"}


def test_unknown_source_lists_the_available_options():
    with pytest.raises(ValueError, match="Unknown ingest source 'nope'"):
        create_source(_settings(INGEST_SOURCE="nope"))
