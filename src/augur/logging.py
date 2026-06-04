"""
Structured logging configuration for Augur.

Call configure_logging() once at application startup.
All modules obtain loggers via structlog.get_logger(__name__).

In development (LOG_FORMAT=text) output is colourised and human-readable.
In production (LOG_FORMAT=json) every line is a JSON object suitable for
log aggregators.
"""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(log_level: str = "INFO", log_format: str = "json") -> None:
    """Set up structlog and the stdlib root logger."""

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if log_format == "text":
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(log_level.upper())

    # Quieten noisy libraries
    for noisy in ("uvicorn.access", "httpx", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
