"""Structured logging configuration.

A single ``configure_logging`` entry point gives every service consistent,
JSON-formatted logs that play nicely with the ELK/Grafana style tooling the
project emulates. Falls back to human-readable logs when ``LOG_JSON`` is off.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from .config import observability_settings


class _JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON objects."""

    _RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys())

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        # Promote any structured ``extra=`` fields onto the top level.
        for key, value in record.__dict__.items():
            if key not in self._RESERVED and not key.startswith("_"):
                payload[key] = value
        return json.dumps(payload, default=str)


def configure_logging(service_name: str) -> logging.Logger:
    """Configure the root logger once and return a named service logger."""

    settings = observability_settings()
    handler = logging.StreamHandler(sys.stdout)
    if settings.log_json:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s :: %(message)s")
        )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())

    # Quieten noisy third-party libraries.
    for noisy in ("elastic_transport", "urllib3", "httpx", "kafka"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return logging.getLogger(service_name)
