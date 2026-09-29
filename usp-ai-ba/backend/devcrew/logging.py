"""Logging setup."""

from __future__ import annotations

import logging


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # Quiet noisy third-party loggers.
    for name in ("httpx", "httpcore", "urllib3", "docker"):
        logging.getLogger(name).setLevel(logging.WARNING)
