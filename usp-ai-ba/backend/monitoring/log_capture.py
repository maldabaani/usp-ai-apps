"""A logging.Handler that feeds every ERROR+ record emitted anywhere in the
codebase into monitoring/error_log.py's store. Every module already does
`logger = logging.getLogger(__name__)` and calls
`logger.exception(...)`/`logger.error(...)` -- attaching this to the root
logger captures all of them for free, with no per-call-site changes needed."""
from __future__ import annotations

import logging

from monitoring.error_log import record_error


class ErrorCaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            record_error(
                logger_name=record.name,
                level=record.levelname,
                message=record.getMessage(),
                traceback=self.format(record) if record.exc_info else None,
            )
        except Exception:
            # A broken error-capture path must never itself crash logging.
            pass


def install() -> None:
    logging.getLogger().addHandler(ErrorCaptureHandler())
