"""Request IDs: one identifier per HTTP request, in the logs and the response.

Every request gets an ID — the caller's ``X-Request-ID`` if it sent a sane one
(a reverse proxy usually does), otherwise a fresh one. The ID is

* stored in a contextvar, which :mod:`logging_config` stamps onto every log
  record emitted while the request is handled (``request_id`` field),
* available to handlers as ``request.state.request_id``,
* echoed back in the ``X-Request-ID`` response header, so a user reporting an
  error can quote it and the operator can grep for it.

Pure ASGI rather than ``BaseHTTPMiddleware``: no response buffering, no
background-task quirks, and it works for streaming responses.
"""
from __future__ import annotations

import re
import uuid
from contextvars import ContextVar

HEADER = "x-request-id"
_HEADER_BYTES = HEADER.encode("latin-1")

# Accept what proxies commonly send (UUIDs, hex, base64-ish tokens) and refuse
# anything that could smuggle content into logs or headers.
_VALID = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")

_request_id: ContextVar[str | None] = ContextVar("nodeglow_request_id", default=None)


def get_request_id() -> str | None:
    """Return the ID of the request currently being handled, if any."""
    return _request_id.get()


def new_request_id() -> str:
    return uuid.uuid4().hex


def _incoming_id(scope) -> str | None:
    for name, value in scope.get("headers") or ():
        if name.lower() == _HEADER_BYTES:
            try:
                candidate = value.decode("latin-1").strip()
            except UnicodeDecodeError:  # pragma: no cover — latin-1 decodes all bytes
                return None
            return candidate if _VALID.match(candidate) else None
    return None


class RequestIdMiddleware:
    """ASGI middleware that assigns and propagates ``X-Request-ID``."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        request_id = _incoming_id(scope) or new_request_id()
        scope.setdefault("state", {})["request_id"] = request_id
        token = _request_id.set(request_id)

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                headers = [
                    (k, v) for k, v in message.get("headers", [])
                    if k.lower() != _HEADER_BYTES
                ]
                headers.append((_HEADER_BYTES, request_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            _request_id.reset(token)


def install(app) -> None:
    """Install the middleware as the OUTERMOST layer of a Starlette app.

    ``app.add_middleware`` puts a middleware inside every one added after it,
    so registering it early in main.py would leave later middlewares (auth,
    security headers) and the server-error handler outside of it — their logs
    would carry no ID and error responses no header. Wrapping the built stack
    instead makes the position independent of where this call sits.
    """
    if getattr(app, "_request_id_installed", False):
        return
    build = app.build_middleware_stack

    def build_with_request_id():
        return RequestIdMiddleware(build())

    app.build_middleware_stack = build_with_request_id
    app._request_id_installed = True
