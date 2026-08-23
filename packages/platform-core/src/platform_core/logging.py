"""Structured (JSON) logging via structlog, with automatic trace correlation."""

from __future__ import annotations

import logging as _logging
from typing import Any

import structlog
from structlog.typing import EventDict, WrappedLogger

from .context import current_trace_id


def _add_trace_id(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    """structlog processor: stamp the active trace id onto every log line, when present."""
    tid = current_trace_id()
    if tid is not None:
        event_dict["trace_id"] = tid
    return event_dict


def configure_logging(level: str = "info") -> None:
    """Configure structlog to emit JSON with level, ISO timestamp, and trace correlation."""
    lvl = getattr(_logging, level.upper(), _logging.INFO)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            _add_trace_id,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(lvl),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> Any:
    """Return a bound structlog logger."""
    return structlog.get_logger(name)
