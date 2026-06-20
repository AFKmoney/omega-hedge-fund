"""
Structured JSON logger for OMEGA.

Uses stdlib logging with a JSON formatter so logs can be ingested by ELK /
Datadog / CloudWatch without parsing. Adds a `trade_id` and `component` field
on every record for cross-layer traceability.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any, Dict, Optional

_CONFIGURED: Dict[str, logging.Logger] = {}


class JsonFormatter(logging.Formatter):
    """One-line JSON per log record. Survives CRLF injection by escaping."""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)
            ) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "component": getattr(record, "component", "omega"),
            "msg": record.getMessage(),
        }
        # Optional structured fields attached via extra=...
        for key in ("trade_id", "symbol", "agent", "regime", "latency_ms"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def get_logger(name: str = "omega", level: str = "INFO") -> logging.Logger:
    """Return a configured logger. Idempotent — repeated calls return the same instance."""
    if name in _CONFIGURED:
        return _CONFIGURED[name]
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False  # don't bubble to root (avoids duplicate lines)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    _CONFIGURED[name] = logger
    return logger
