"""Centralised logging setup: readable text in development, one JSON object per line in
production (CloudWatch Logs Insights can then filter and aggregate on any field).

Conventions (see tutorial/platform/05_Observability.md):
- The message is a constant sentence; the variable parts go in `extra=`:
      logger.info("Document ingested", extra={"chunks": 12, "duration_ms": 830})
- Request-wide fields (request_id, user_id, conversation_id...) are bound once with
  `bind_log_context()` and then appear on every record, without passing ids around.
- Never log secrets, document content or prompts. Field names that look like secrets
  are masked, JWTs and Bearer tokens are masked in messages: a safety net, not a licence.
"""

import json
import logging
import re
from collections.abc import Mapping
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Literal

LogFormat = Literal["text", "json"]

# One immutable mapping per request/task. contextvars follow asyncio tasks: a value set in
# a request is visible to everything that request awaits, and invisible to other requests.
_EMPTY: Mapping[str, Any] = MappingProxyType({})
_log_context: ContextVar[Mapping[str, Any]] = ContextVar("log_context", default=_EMPTY)


def new_log_context(**fields: Any) -> Token[Mapping[str, Any]]:
    """Start a fresh context (one per request). Returns a token for `reset_log_context`."""
    return _log_context.set(MappingProxyType(dict(fields)))


def bind_log_context(**fields: Any) -> None:
    """Add fields to every record logged from now on in the current request/task."""
    _log_context.set(MappingProxyType({**_log_context.get(), **fields}))


def reset_log_context(token: Token[Mapping[str, Any]]) -> None:
    _log_context.reset(token)


def get_log_context() -> Mapping[str, Any]:
    return _log_context.get()


# ---- Records ---------------------------------------------------------------------------

_base_record_factory = logging.getLogRecordFactory()


def _record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
    # Snapshot the context when the record is CREATED (in the caller's task), not when a
    # handler formats it. A record factory also works for every handler (pytest's caplog
    # included), where a Filter would only run for the handler it is attached to.
    # One `context` attribute, not one attribute per field: `extra={"request_id": ...}`
    # would otherwise raise "Attempt to overwrite 'request_id' in LogRecord".
    record = _base_record_factory(*args, **kwargs)
    record.context = _log_context.get()
    return record


# Attributes every LogRecord has: anything else on a record came from `extra=`.
_STANDARD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "context",
    "color_message",  # uvicorn's copy of the message with ANSI colour codes
}


def record_fields(record: logging.LogRecord) -> dict[str, Any]:
    """Context fields + `extra=` fields of a record (extra wins on a name clash), masked."""
    fields: dict[str, Any] = dict(getattr(record, "context", _EMPTY))
    fields.update((k, v) for k, v in record.__dict__.items() if k not in _STANDARD_ATTRS)
    return {k: REDACTED if _SECRET_KEY.search(k) else v for k, v in fields.items()}


# ---- Redaction -------------------------------------------------------------------------

REDACTED = "[REDACTED]"
# `access_token`, `password`, `api_key`... but not `input_tokens` (an LLM usage count).
_SECRET_KEY = re.compile(
    r"(^|_)(password|passwd|secret|token|authorization|cookie|api_key|apikey)$", re.I
)
_SECRET_TEXT = re.compile(
    r"eyJ[\w-]+\.[\w-]+\.[\w-]+"  # a JWT (header.payload.signature)
    r"|(?<=Bearer )[\w.~+/-]+=*",  # whatever follows "Bearer "
)


def redact(text: str) -> str:
    return _SECRET_TEXT.sub(REDACTED, text)


# ---- Formatters ------------------------------------------------------------------------


class JsonFormatter(logging.Formatter):
    """One JSON object per line: `{"timestamp", "level", "logger", "message", ...fields}`."""

    def __init__(self, static_fields: Mapping[str, Any] | None = None) -> None:
        super().__init__()
        self._static = dict(static_fields or {})

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
            **self._static,
            **record_fields(record),
        }
        if record.exc_info:
            # The whole traceback in ONE field: a multi-line traceback written as plain
            # text becomes one log event per line in CloudWatch.
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """`time | LEVEL | logger | message | key=value ...`, for humans in a terminal."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")

    def formatMessage(self, record: logging.LogRecord) -> str:
        line = super().formatMessage(record)
        fields = record_fields(record)
        if fields:
            line += " | " + " ".join(f"{k}={v}" for k, v in fields.items())
        return line

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


# ---- Setup -----------------------------------------------------------------------------

_HANDLER_NAME = "app"


def setup_logging(
    level: str = "INFO", fmt: LogFormat = "text", static_fields: Mapping[str, Any] | None = None
) -> None:
    """Idempotent: safe to call again (tests build many apps, the lifespan calls it too)."""
    logging.setLogRecordFactory(_record_factory)

    handler = logging.StreamHandler()  # stderr; containers ship it to CloudWatch
    handler.set_name(_HANDLER_NAME)
    handler.setFormatter(JsonFormatter(static_fields) if fmt == "json" else TextFormatter())

    root = logging.getLogger()
    # Replace only our own handler: others (pytest's caplog) must survive.
    for old in [h for h in root.handlers if h.get_name() == _HANDLER_NAME]:
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Third-party libraries are noisy at DEBUG level.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # uvicorn ships its own handlers and format. Send its logs through ours instead, and
    # drop its access log: RequestContextMiddleware logs every request, with the context.
    for name in ("uvicorn", "uvicorn.error"):
        server_logger = logging.getLogger(name)
        server_logger.handlers.clear()
        server_logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
