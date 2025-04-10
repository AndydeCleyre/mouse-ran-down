# ruff: noqa: D102, ANN401
"""Logging configuration."""

from __future__ import annotations

from typing import Any, Protocol

import structlog


class StructLogger(Protocol):
    """
    A structlog compatible logger.

    This is just spoon-feeding some BindableLogger functions to type checkers.
    """

    def bind(self, **kw: Any) -> StructLogger: ...
    def info(self, event: str, **kw: Any) -> StructLogger: ...
    def error(self, event: str, **kw: Any) -> StructLogger: ...
    def warning(self, event: str, **kw: Any) -> StructLogger: ...
    def debug(self, event: str, **kw: Any) -> StructLogger: ...


def get_logger(*, json: bool = True) -> StructLogger:
    """Get a configured structlog BindableLogger."""
    processors = [
        structlog.processors.dict_tracebacks,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt='iso'),
    ]
    if json:
        processors.append(structlog.processors.JSONRenderer(sort_keys=True))
    else:
        processors.append(structlog.dev.ConsoleRenderer())
    return structlog.get_logger(processors=processors)
