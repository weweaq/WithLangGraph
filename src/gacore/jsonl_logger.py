"""JSONL structured logging for gacore per the project logging spec (AGENTS.md).

One JSON object per line in logs/<YYYY-MM-DD-HHmmss>/app.jsonl. A process writes to a single
log directory; every Logger instance returned by get_logger shares it.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final, final

from gacore.config import Config, ConfigError

_LOG_FILENAME: Final = "app.jsonl"
_LOG_DIR_FORMAT: Final = "%Y-%m-%d-%H%M%S"


class _JsonlFormatter(logging.Formatter):
    """Serialize a LogRecord as a single JSON object on one line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "module": record.__dict__.get("gacore_module", record.module),
            "message": record.getMessage(),
        }
        payload.update(record.__dict__.get("gacore_fields", {}))
        return json.dumps(payload, ensure_ascii=False, default=str)


@final
class Logger:
    """Module-scoped structured logger; all instances share the process's log file."""

    __slots__ = ("_logger", "_module")

    def __init__(self, module: str) -> None:
        self._module = module
        self._logger = logging.getLogger(f"gacore.{module}")
        if not self._logger.handlers:
            self._logger.addHandler(_get_sink())
            self._logger.setLevel(logging.DEBUG)
            self._logger.propagate = False

    def debug(self, message: str, **fields: object) -> None:
        """Emit a DEBUG line; extra kwargs become JSON fields."""
        self._emit(logging.DEBUG, message, fields)

    def info(self, message: str, **fields: object) -> None:
        """Emit an INFO line; extra kwargs become JSON fields."""
        self._emit(logging.INFO, message, fields)

    def warning(self, message: str, **fields: object) -> None:
        """Emit a WARNING line; extra kwargs become JSON fields."""
        self._emit(logging.WARNING, message, fields)

    def error(
        self,
        message: str,
        *,
        error_type: str | None = None,
        stack_trace: str | None = None,
        context: Mapping[str, object] | None = None,
        **fields: object,
    ) -> None:
        """Emit an ERROR line carrying error_type, stack_trace and context alongside any fields."""
        self._emit(
            logging.ERROR,
            message,
            {"error_type": error_type, "stack_trace": stack_trace, "context": context, **fields},
        )

    def _emit(self, level: int, message: str, fields: Mapping[str, object]) -> None:
        self._logger.log(level, message, extra={"gacore_module": self._module, "gacore_fields": dict(fields)})


_sink: logging.Handler | None = None


def _build_sink() -> logging.Handler:
    """Create the process-wide file handler; degrades to a no-op sink if setup fails."""
    try:
        config = Config.default()
        log_dir = config.logs_dir / time.strftime(_LOG_DIR_FORMAT)
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_dir / _LOG_FILENAME, mode="a", encoding="utf-8")
        handler.setFormatter(_JsonlFormatter())
        return handler
    except (OSError, ConfigError):
        handler = logging.NullHandler()
        handler.setFormatter(_JsonlFormatter())
        return handler


def _get_sink() -> logging.Handler:
    """Return the process-wide sink, building it once on first use."""
    global _sink
    if _sink is None:
        _sink = _build_sink()
    return _sink


def get_logger(module: str) -> Logger:
    """Return a JSONL logger scoped to a module, sharing the process's log file."""
    return Logger(module)
