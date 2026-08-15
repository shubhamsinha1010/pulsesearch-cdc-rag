"""Tests for the Debezium envelope -> domain adapter."""

from __future__ import annotations

import pytest

from app.handlers import DebeziumEventParser, Operation


def _envelope(op: str, after=None, before=None, ts_ms=1_700_000_000_000):
    return {"op": op, "after": after, "before": before, "ts_ms": ts_ms}


def _row(**overrides):
    row = {
        "id": 42,
        "wiki": "enwiki",
        "title": "Alan Turing",
        "last_comment": "grammar",
        "namespace": 0,
        "is_bot": 0,
        "is_minor": 1,
        "edit_count": 3,
        "event_time": 1_700_000_000_000_000,  # microseconds
    }
    row.update(overrides)
    return row


def test_parse_create_produces_upsert_document():
    parser = DebeziumEventParser()
    event = parser.parse(_envelope("c", after=_row()))
    assert event is not None
    assert event.op is Operation.CREATE
    assert event.op.is_upsert
    assert event.doc_id == "42"
    assert event.document is not None
    assert event.document.title == "Alan Turing"
    # ts_ms drives the version for idempotent upserts.
    assert event.document.version == 1_700_000_000_000


def test_parse_delete_produces_tombstone_document():
    parser = DebeziumEventParser()
    event = parser.parse(_envelope("d", before=_row()))
    assert event is not None
    assert event.op.is_delete
    assert event.document is not None
    assert event.document.deleted is True
    assert event.document.title == "Alan Turing"
    assert event.doc_id == "42"
    assert event.document.version == 1_700_000_000_000


def test_tinyint_booleans_are_coerced():
    parser = DebeziumEventParser()
    event = parser.parse(_envelope("u", after=_row(is_bot=1, is_minor=0)))
    assert event.document.is_bot is True
    assert event.document.is_minor is False


def test_unknown_operation_is_rejected():
    parser = DebeziumEventParser()
    with pytest.raises(ValueError, match="unsupported Debezium op"):
        parser.parse(_envelope("x", after=_row()))
