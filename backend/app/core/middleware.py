"""RequestContextMiddleware: one request id per request, on every log line and response.

Pure ASGI on purpose, not `BaseHTTPMiddleware`: that one runs the app in a separate task
(so contextvars set by the endpoint don't flow back out) and has a history of trouble
with streaming responses and BackgroundTasks.
"""

import logging
import re
import time
import uuid
from collections.abc import Collection

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import new_log_context

logger = logging.getLogger("app.request")

REQUEST_ID_HEADER = "X-Request-ID"
# Accept the caller's id (load balancer, frontend, another service) only if it looks like
# an id: a client-controlled string goes straight into our logs (log injection, huge ids).
_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, quiet_paths: Collection[str] = ()) -> None:
        self.app = app
        # Health checks hit us every few seconds: log them at DEBUG, not INFO.
        self.quiet_paths = frozenset(quiet_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # "lifespan" / "websocket"
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get(REQUEST_ID_HEADER, "")
        request_id = incoming if _VALID_REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
        # No reset at the end, on purpose: Starlette's ServerErrorMiddleware (which runs the
        # `Exception` handler) sits OUTSIDE every user middleware, so the 500 handler must
        # still see the context after we return. Each request runs in its own task under
        # uvicorn, so nothing leaks into the next request.
        new_log_context(request_id=request_id)

        start = time.perf_counter()
        status_code = 500  # if the app raises before sending a response
        ttfb_ms: float | None = None

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, ttfb_ms
            if message["type"] == "http.response.start":
                status_code = message["status"]
                ttfb_ms = _elapsed_ms(start)  # headers sent: what the client waited for
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            # Logged in `finally` so crashed requests are counted too. `total_ms` covers the
            # whole ASGI call: the full SSE stream for /chat/stream, and the ingestion that
            # runs as a BackgroundTask after POST /documents has already answered 202.
            # No route template ("/documents/{document_id}") yet: FastAPI 0.143 doesn't expose
            # the full one publicly; OpenTelemetry's `http.route` will (todo.md §4a).
            logger.log(
                logging.DEBUG if scope["path"] in self.quiet_paths else logging.INFO,
                "Request finished",
                extra={
                    "http_method": scope["method"],
                    # The path without the query string: queries can carry tokens or PII.
                    "http_path": scope["path"],
                    "http_status": status_code,
                    "ttfb_ms": ttfb_ms,
                    "total_ms": _elapsed_ms(start),
                    "client_ip": (scope.get("client") or (None,))[0],
                },
            )


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)
