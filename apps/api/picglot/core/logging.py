"""Structured logging.

Two hard rules encoded here:

1. Document content (recognised text, translations, file bytes, filenames of
   user uploads) never reaches a log record. ``scrub`` drops the offending keys
   and ``SENSITIVE_KEYS`` is the single list to extend.
2. Every record carries the ambient request/job/trace identifiers so a support
   ticket quoting a request id can be traced end to end.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any

import structlog

from picglot.core.config import settings

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
job_id_var: ContextVar[str | None] = ContextVar("job_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)
workspace_id_var: ContextVar[str | None] = ContextVar("workspace_id", default=None)
trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)

#: Keys that must never be serialised into a log line.
SENSITIVE_KEYS = frozenset(
    {
        "text",
        "source_text",
        "translated_text",
        "normalized_text",
        "ocr_text",
        "content",
        "body",
        "raw",
        "password",
        "password_hash",
        "token",
        "token_hash",
        "api_key",
        "secret",
        "secret_key",
        "authorization",
        "cookie",
        "set-cookie",
        "credentials",
        "card",
        "filename",
        "original_filename",
        "email",
        "prompt",
        "messages",
    }
)

_REDACTED = "[redacted]"


def scrub(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive values recursively (bounded depth to stay cheap)."""

    def _walk(value: Any, depth: int = 0) -> Any:
        if depth > 4:
            return value
        if isinstance(value, dict):
            return {
                key: (_REDACTED if key.lower() in SENSITIVE_KEYS else _walk(val, depth + 1))
                for key, val in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [_walk(item, depth + 1) for item in value[:20]]
        if isinstance(value, (bytes, bytearray)):
            return f"<{len(value)} bytes>"
        return value

    return {
        key: (_REDACTED if key.lower() in SENSITIVE_KEYS else _walk(val))
        for key, val in event_dict.items()
    }


def add_context(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for key, var in (
        ("request_id", request_id_var),
        ("job_id", job_id_var),
        ("user_id", user_id_var),
        ("workspace_id", workspace_id_var),
        ("trace_id", trace_id_var),
    ):
        value = var.get()
        if value:
            event_dict.setdefault(key, value)
    return event_dict


def add_service(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    event_dict.setdefault("service", settings.otel_service_name)
    event_dict.setdefault("env", settings.environment)
    return event_dict


_configured = False


def configure_logging(force: bool = False) -> None:
    global _configured
    if _configured and not force:
        return

    level = getattr(logging, settings.log_level, logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level, force=True)

    for noisy in ("uvicorn.access", "botocore", "boto3", "urllib3", "s3transfer", "PIL"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        add_context,
        add_service,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        scrub,
    ]
    if settings.log_format == "json":
        processors += [structlog.processors.format_exc_info, structlog.processors.JSONRenderer()]
    else:
        processors += [structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str = "picglot") -> Any:
    # PrintLoggerFactory has no logger name of its own, so bind it explicitly:
    # every record still carries the originating module.
    configure_logging()
    return structlog.get_logger().bind(logger=name)


def bind_request(request_id: str | None = None, **extra: str | None) -> None:
    if request_id is not None:
        request_id_var.set(request_id)
    for key, value in extra.items():
        var = {
            "job_id": job_id_var,
            "user_id": user_id_var,
            "workspace_id": workspace_id_var,
            "trace_id": trace_id_var,
        }.get(key)
        if var is not None:
            var.set(value)


def clear_context() -> None:
    for var in (request_id_var, job_id_var, user_id_var, workspace_id_var, trace_id_var):
        var.set(None)
