"""logging_config + request_id: structured logs that carry the request ID."""
import io
import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from httpx import ASGITransport, AsyncClient
from starlette.middleware.base import BaseHTTPMiddleware

import logging_config
import request_id
from logging_config import JsonFormatter, RequestIdFilter, build_config


@pytest.fixture
def restore_logging():
    """dictConfig mutates global state; put the root logger back afterwards."""
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    yield
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


def _record(msg="hello", **extra):
    record = logging.LogRecord("nodeglow.test", logging.INFO, __file__, 1, msg, None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


# ── logging_config ───────────────────────────────────────────────────────────

def test_env_selects_level_and_format(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("LOG_FORMAT", "JSON")
    cfg = build_config()
    assert cfg["root"]["level"] == "DEBUG"
    assert cfg["handlers"]["console"]["formatter"] == "json"


def test_invalid_values_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "loud")
    monkeypatch.setenv("LOG_FORMAT", "xml")
    cfg = build_config()
    assert cfg["root"]["level"] == "INFO"
    assert cfg["handlers"]["console"]["formatter"] == "text"


def test_chatty_libraries_are_quiet_unless_debugging():
    assert build_config("INFO", "text")["loggers"]["apscheduler"]["level"] == "WARNING"
    assert build_config("DEBUG", "text")["loggers"]["httpx"]["level"] == "NOTSET"


def test_uvicorn_loggers_are_routed_through_root(restore_logging):
    logging_config.configure_logging("INFO", "text")
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        assert logger.handlers == []
        assert logger.propagate is True


def test_existing_loggers_stay_enabled(restore_logging):
    early = logging.getLogger("nodeglow.created.before.config")
    logging_config.configure_logging("INFO", "text")
    assert early.disabled is False


def test_json_formatter_emits_one_object_with_extras():
    record = _record("value=%s", request_id="abc123", host_id=7)
    record.args = (42,)
    line = JsonFormatter().format(record)
    payload = json.loads(line)
    assert payload["msg"] == "value=42"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "nodeglow.test"
    assert payload["request_id"] == "abc123"
    assert payload["host_id"] == 7
    assert payload["ts"].endswith("Z")
    assert "\n" not in line


def test_json_formatter_includes_exception():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        record = _record("failed")
        record.exc_info = sys.exc_info()
    payload = json.loads(JsonFormatter().format(record))
    assert "ValueError: boom" in payload["exc"]


def test_filter_uses_dash_outside_a_request():
    record = _record()
    RequestIdFilter().filter(record)
    assert record.request_id == "-"


def test_text_handler_writes_request_id(restore_logging):
    logging_config.configure_logging("INFO", "text")
    stream = io.StringIO()
    handler = logging.getLogger().handlers[0]
    handler.setStream(stream)
    token = request_id._request_id.set("req-42")
    try:
        logging.getLogger("nodeglow.test").info("inside")
    finally:
        request_id._request_id.reset(token)
    assert "[req-42] inside" in stream.getvalue()


# ── request_id middleware ────────────────────────────────────────────────────

def _app():
    app = FastAPI()
    seen = {}

    @app.get("/ping")
    async def ping():
        seen["ctx"] = request_id.get_request_id()
        return PlainTextResponse("pong")

    @app.get("/boom")
    async def boom():
        raise RuntimeError("kaputt")

    # A middleware registered AFTER install() — it must still run inside it.
    async def outer(request, call_next):
        seen["in_middleware"] = request_id.get_request_id()
        return await call_next(request)

    request_id.install(app)
    app.add_middleware(BaseHTTPMiddleware, dispatch=outer)
    return app, seen


async def _client(app):
    return AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                       base_url="http://test")


async def test_generates_id_and_returns_header():
    app, seen = _app()
    async with await _client(app) as client:
        resp = await client.get("/ping")
    rid = resp.headers["x-request-id"]
    assert len(rid) == 32
    assert seen["ctx"] == rid
    assert seen["in_middleware"] == rid
    assert request_id.get_request_id() is None  # reset after the request


async def test_propagates_a_sane_incoming_id():
    app, seen = _app()
    async with await _client(app) as client:
        resp = await client.get("/ping", headers={"X-Request-ID": "proxy-abc.123"})
    assert resp.headers["x-request-id"] == "proxy-abc.123"
    assert seen["ctx"] == "proxy-abc.123"


async def test_replaces_a_hostile_incoming_id():
    app, _ = _app()
    async with await _client(app) as client:
        resp = await client.get("/ping", headers={"X-Request-ID": "x" * 200})
    assert resp.headers["x-request-id"] != "x" * 200
    assert len(resp.headers["x-request-id"]) == 32


async def test_error_responses_carry_the_id_too():
    app, _ = _app()
    async with await _client(app) as client:
        resp = await client.get("/boom")
    assert resp.status_code == 500
    assert resp.headers.get("x-request-id")


def test_install_is_idempotent():
    app = FastAPI()
    request_id.install(app)
    first = app.build_middleware_stack
    request_id.install(app)
    assert app.build_middleware_stack is first
