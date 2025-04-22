# ruff: noqa: D102, ARG001
"""Logging configuration."""

from __future__ import annotations

from typing import Any, Protocol

import structlog
from plumbum import LocalPath


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


def stringify_localpaths(
    logger: StructLogger, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Stringify plumbum LocalPath objects in event_dict."""
    return {k: (str(v) if isinstance(v, LocalPath) else v) for k, v in event_dict.items()}


def get_logger(*, json: bool = True) -> StructLogger:
    """Get a configured structlog BindableLogger."""
    processors = [
        stringify_localpaths,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt='iso'),
    ]
    if json:
        processors.append(structlog.processors.JSONRenderer(sort_keys=True))
        processors.insert(0, structlog.processors.dict_tracebacks)
    else:
        # format_exc_info ?
        processors.append(structlog.dev.ConsoleRenderer(pad_event=24, pad_level=False))
    return structlog.get_logger(processors=processors)
