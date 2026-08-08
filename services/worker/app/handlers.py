"""Debezium envelope -> domain model adapter.

Isolates knowledge of the Debezium MySQL change-event format so the rest of the
pipeline works purely with :class:`ChangeEvent` / :class:`PageDocument`
(Adapter pattern, Anti-Corruption Layer).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pulsesearch_common.models import PageDocument


class Operation(str, Enum):
    CREATE = "c"
    UPDATE = "u"
    DELETE = "d"
    SNAPSHOT = "r"

    @property
    def is_upsert(self) -> bool:
        return self in (Operation.CREATE, Operation.UPDATE, Operation.SNAPSHOT)

    @property
    def is_delete(self) -> bool:
        return self is Operation.DELETE


@dataclass(frozen=True)
class ChangeEvent:
    """A normalised representation of a single Debezium change event."""

    doc_id: str
    op: Operation
    source_ts_ms: int
    document: Optional[PageDocument]

    @property
    def source_time(self) -> datetime:
        return datetime.fromtimestamp(self.source_ts_ms / 1000, tz=timezone.utc)


class DebeziumEventParser:
    """Parses a Debezium ``payload`` envelope into a :class:`ChangeEvent`."""

    def parse(self, payload: dict[str, Any]) -> Optional[ChangeEvent]:
        if payload is None:
            return None

        op_raw = payload.get("op")
        try:
            op = Operation(op_raw)
        except ValueError:
            return None

        ts_ms = int(payload.get("ts_ms") or 0)
        row = payload.get("before") if op.is_delete else payload.get("after")
        if not row:
            return None

        doc_id = str(row.get("id"))
        if doc_id in (None, "None"):
            return None

        if op.is_delete:
            return ChangeEvent(doc_id=doc_id, op=op, source_ts_ms=ts_ms, document=None)

        document = self._row_to_document(row, ts_ms)
        return ChangeEvent(doc_id=doc_id, op=op, source_ts_ms=ts_ms, document=document)

    def _row_to_document(self, row: dict[str, Any], ts_ms: int) -> PageDocument:
        return PageDocument(
            id=str(row.get("id")),
            wiki=row.get("wiki") or "",
            title=row.get("title") or "",
            title_url=row.get("title_url"),
            last_comment=row.get("last_comment"),
            last_user=row.get("last_user"),
            event_type=row.get("event_type"),
            namespace=int(row.get("namespace") or 0),
            is_bot=_as_bool(row.get("is_bot")),
            is_minor=_as_bool(row.get("is_minor")),
            length_new=row.get("length_new"),
            edit_count=int(row.get("edit_count") or 1),
            event_time=_debezium_datetime(row.get("event_time")),
            # ``ts_ms`` is monotonic per row and drives version-guarded upserts.
            version=ts_ms,
        )


def _as_bool(value: Any) -> bool:
    # Debezium may encode TINYINT(1) as 0/1 or true/false.
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.lower() in ("1", "true", "t", "yes")
    return False


def _debezium_datetime(value: Any) -> Optional[datetime]:
    """Debezium emits MySQL DATETIME as epoch microseconds by default."""

    if value is None:
        return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if isinstance(value, (int, float)):
        # Heuristic: distinguish micro/milli/second precision by magnitude.
        if value > 1e14:  # microseconds
            return datetime.fromtimestamp(value / 1_000_000, tz=timezone.utc)
        if value > 1e11:  # milliseconds
            return datetime.fromtimestamp(value / 1_000, tz=timezone.utc)
        return datetime.fromtimestamp(value, tz=timezone.utc)
    return None
