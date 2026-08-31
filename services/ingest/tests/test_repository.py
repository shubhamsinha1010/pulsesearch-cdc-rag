"""Tests for the MySQL write repository's parameter mapping.

These cover the column-width guards, which is where a firehose event with an
unusually long title or comment would otherwise fail the INSERT at runtime.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.repository import PageWriteRepository
from pulsesearch_common.models import PageRecord


def _record(**overrides) -> PageRecord:
    fields = {
        "wiki": "enwiki",
        "title": "Alan Turing",
        "title_url": "https://en.wikipedia.org/wiki/Alan_Turing",
        "last_comment": "fix grammar",
        "last_user": "SomeEditor",
        "event_type": "edit",
        "namespace": 0,
        "is_bot": False,
        "is_minor": True,
        "length_new": 120,
        "event_time": datetime(2026, 8, 30, 10, 0, tzinfo=UTC),
    }
    fields.update(overrides)
    return PageRecord(**fields)


def test_params_map_every_column():
    params = PageWriteRepository._to_params(_record())
    assert params == {
        "wiki": "enwiki",
        "title": "Alan Turing",
        "title_url": "https://en.wikipedia.org/wiki/Alan_Turing",
        "last_comment": "fix grammar",
        "last_user": "SomeEditor",
        "event_type": "edit",
        "namespace": 0,
        "is_bot": False,
        "is_minor": True,
        "length_new": 120,
        "event_time": datetime(2026, 8, 30, 10, 0, tzinfo=UTC),
    }


def test_long_title_is_truncated_to_the_column_width():
    params = PageWriteRepository._to_params(_record(title="x" * 900))
    assert len(params["title"]) == 512


def test_long_comment_is_truncated_to_the_column_width():
    params = PageWriteRepository._to_params(_record(last_comment="y" * 5000))
    assert len(params["last_comment"]) == 2000


def test_blank_comment_is_stored_as_null():
    assert PageWriteRepository._to_params(_record(last_comment=""))["last_comment"] is None
    assert PageWriteRepository._to_params(_record(last_comment=None))["last_comment"] is None


def test_upsert_of_an_empty_batch_never_touches_the_engine():
    # A sentinel engine: if upsert_many tried to open a connection this would
    # raise AttributeError instead of returning 0.
    repo = PageWriteRepository(settings=None, engine=object())
    assert repo.upsert_many([]) == 0
