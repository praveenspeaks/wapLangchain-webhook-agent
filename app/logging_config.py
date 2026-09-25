"""Configure structured console logging."""

import logging

from pythonjsonlogger.json import JsonFormatter


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter(
            fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
