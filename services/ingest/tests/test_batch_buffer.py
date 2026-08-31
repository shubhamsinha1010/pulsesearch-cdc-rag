"""Tests for the size/time-bounded write buffer."""

from __future__ import annotations

from app import main as ingest_main
from app.main import BatchBuffer
from pulsesearch_common.models import PageRecord


class FakeRepo:
    """Records batches instead of writing to MySQL."""

    def __init__(self, fail_times: int = 0) -> None:
        self.batches: list[list[PageRecord]] = []
        self._fail_times = fail_times

    def upsert_many(self, records) -> int:
        if self._fail_times > 0:
            self._fail_times -= 1
            raise RuntimeError("transient MySQL error")
        batch = list(records)
        self.batches.append(batch)
        return len(batch)


def _record(title: str = "Alan Turing") -> PageRecord:
    return PageRecord(wiki="enwiki", title=title)


def _buffer(repo: FakeRepo, batch_size: int = 3, flush_interval: float = 999.0) -> BatchBuffer:
    return BatchBuffer(repo, "wikimedia", batch_size, flush_interval)


def test_buffer_flushes_once_batch_size_is_reached():
    repo = FakeRepo()
    buffer = _buffer(repo, batch_size=3)

    buffer.add(_record("one"))
    buffer.add(_record("two"))
    assert repo.batches == []

    buffer.add(_record("three"))
    assert len(repo.batches) == 1
    assert [r.title for r in repo.batches[0]] == ["one", "two", "three"]


def test_buffer_flushes_on_elapsed_interval_before_batch_size():
    repo = FakeRepo()
    # A zero interval makes every add overdue, so size never gates the flush.
    buffer = _buffer(repo, batch_size=1000, flush_interval=0.0)

    buffer.add(_record("one"))
    assert len(repo.batches) == 1


def test_flushing_an_empty_buffer_does_not_call_the_repository():
    repo = FakeRepo()
    _buffer(repo).flush()
    assert repo.batches == []


def test_flush_retries_until_the_write_succeeds(monkeypatch):
    # Avoid the real exponential backoff sleep in the retry loop.
    monkeypatch.setattr(ingest_main.time, "sleep", lambda _seconds: None)
    repo = FakeRepo(fail_times=2)
    buffer = _buffer(repo, batch_size=1)

    buffer.add(_record("eventually"))

    assert len(repo.batches) == 1
    assert [r.title for r in repo.batches[0]] == ["eventually"]


def test_buffer_is_cleared_after_a_successful_flush():
    repo = FakeRepo()
    buffer = _buffer(repo, batch_size=1)

    buffer.add(_record("first"))
    buffer.add(_record("second"))

    # Two independent flushes, not one batch containing a duplicate.
    assert [[r.title for r in batch] for batch in repo.batches] == [["first"], ["second"]]
