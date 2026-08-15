"""MySQL write repository for the ingest service.

Encapsulates all SQL. The ingest loop only speaks in :class:`PageRecord`s and
never sees a cursor or a query string (Repository pattern, SRP).

The upsert deliberately produces both INSERTs (first sighting of a page) and
UPDATEs (subsequent edits bump ``edit_count`` and refresh fields), which is
what generates a realistic mix of Debezium change events for the CDC pipeline.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from pulsesearch_common.config import MySQLSettings
from pulsesearch_common.models import PageRecord

_UPSERT_SQL = text(
    """
    INSERT INTO pages (
        wiki, title, title_url, last_comment, last_user, event_type,
        namespace, is_bot, is_minor, length_new, edit_count,
        first_seen, event_time
    ) VALUES (
        :wiki, :title, :title_url, :last_comment, :last_user, :event_type,
        :namespace, :is_bot, :is_minor, :length_new, 1,
        :event_time, :event_time
    )
    ON DUPLICATE KEY UPDATE
        title_url    = VALUES(title_url),
        last_comment = VALUES(last_comment),
        last_user    = VALUES(last_user),
        event_type   = VALUES(event_type),
        is_bot       = VALUES(is_bot),
        is_minor     = VALUES(is_minor),
        length_new   = VALUES(length_new),
        edit_count   = edit_count + 1,
        event_time   = VALUES(event_time)
    """
)


class PageWriteRepository:
    def __init__(self, settings: MySQLSettings, engine: Engine | None = None) -> None:
        self._engine = engine or create_engine(
            settings.dsn,
            pool_size=settings.pool_size,
            pool_pre_ping=True,
            future=True,
        )

    @property
    def engine(self) -> Engine:
        return self._engine

    def upsert_many(self, records: Iterable[PageRecord]) -> int:
        rows = [self._to_params(r) for r in records]
        if not rows:
            return 0
        with self._engine.begin() as conn:
            conn.execute(_UPSERT_SQL, rows)
        return len(rows)

    @staticmethod
    def _to_params(record: PageRecord) -> dict:
        return {
            "wiki": record.wiki,
            "title": record.title[:512],
            "title_url": record.title_url,
            "last_comment": (record.last_comment or "")[:2000] or None,
            "last_user": record.last_user,
            "event_type": record.event_type,
            "namespace": record.namespace,
            "is_bot": record.is_bot,
            "is_minor": record.is_minor,
            "length_new": record.length_new,
            "event_time": record.event_time,
        }
