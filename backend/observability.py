"""
Lightweight observability: per-request trace IDs + optional JSON logging.

What it provides
----------------
1. `trace_id_var` — a `contextvars.ContextVar` populated by the FastAPI
   middleware for every inbound request. Any `logger.X(...)` call made
   while handling that request automatically carries the trace_id.

2. `TraceIDMiddleware` — generates a uuid4 per request, sets the
   context-var, mirrors the value back to the client via `X-Trace-Id`
   response header (so a user can paste it in a support email).

3. `setup_json_logging()` — opt-in JSON formatter, activated only when
   `OBSYD_JSON_LOGS=1` is set in the env. Default (off) keeps local-dev
   logs human-readable while production can flip the switch via systemd
   Environment= and feed something like Loki/Datadog/CloudWatch.

Wire-up in main.py:
    from backend.observability import TraceIDMiddleware, setup_logging

    setup_logging()                     # before first logger use
    app = FastAPI(...)
    app.add_middleware(TraceIDMiddleware)
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from contextvars import ContextVar
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

trace_id_var: ContextVar[str | None] = ContextVar("obsyd_trace_id", default=None)


class TraceIDMiddleware(BaseHTTPMiddleware):
    """Generate / propagate an X-Trace-Id per request.

    Honours an inbound `X-Trace-Id` header so an upstream proxy (Caddy,
    nginx) or a frontend retry can preserve correlation across hops.
    """

    HEADER = "X-Trace-Id"

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming = request.headers.get(self.HEADER)
        # Trust inbound values only if they look like a uuid (avoid log injection).
        trace_id = incoming if (incoming and _looks_like_uuid(incoming)) else uuid.uuid4().hex
        token = trace_id_var.set(trace_id)
        try:
            response = await call_next(request)
        finally:
            trace_id_var.reset(token)
        response.headers[self.HEADER] = trace_id
        return response


def _looks_like_uuid(value: str) -> bool:
    if not (16 <= len(value) <= 64):
        return False
    return all(c in "0123456789abcdefABCDEF-" for c in value)


class _TraceIdFilter(logging.Filter):
    """Inject the current trace_id into every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        record.trace_id = trace_id_var.get() or "-"
        return True


class _JsonFormatter(logging.Formatter):
    """One JSON object per line. Keys kept short for log-aggregator cost."""

    def format(self, record: logging.LogRecord) -> str:
        # Trace ID may be on the record (filter installed) OR live only in
        # the contextvar (e.g. tests, code paths that don't go through the
        # root handler). Fall back to the contextvar before "-".
        trace_id = getattr(record, "trace_id", None) or trace_id_var.get() or "-"
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "lvl": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "trace_id": trace_id,
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # Free-form extras (logger.info("...", extra={"key": "val"}))
        for key, value in record.__dict__.items():
            if key in payload or key in _LOG_RECORD_BUILTIN_ATTRS:
                continue
            try:
                json.dumps(value)  # serialisable?
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = repr(value)
        return json.dumps(payload, ensure_ascii=False)


# All standard LogRecord attrs we don't want to copy as "extras".
_LOG_RECORD_BUILTIN_ATTRS = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName", "trace_id",
    "taskName",
}


def setup_logging() -> None:
    """Configure root logging. Call before any logger.X() in main.py.

    Two modes:
      - Default: same human-friendly format the app already used.
        We add the trace_id filter so it's present in every line.
      - JSON: `OBSYD_JSON_LOGS=1` swaps the formatter to single-line JSON.

    Either way, the root logger gets exactly one handler — we strip any
    existing ones to avoid duplicate emission when uvicorn re-imports.
    """
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler()
    handler.addFilter(_TraceIdFilter())

    use_json = os.environ.get("OBSYD_JSON_LOGS", "").strip().lower() in {"1", "true", "yes"}
    if use_json:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s [%(trace_id)s]: %(message)s"
            )
        )
    root.addHandler(handler)
    root.setLevel(logging.INFO)
