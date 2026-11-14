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


def test_credentials_in_query_strings_are_redacted():
    """The single sign-on callback carries the authorization code and state,
    the installers an install token; none of them reaches a log line."""
    import logging

    from logging_config import RedactSecretsFilter

    f = RedactSecretsFilter()
    # uvicorn's access log: the path is an argument.
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                            ("10.0.0.1:5000", "GET",
                             "/api/auth/sso/callback?code=SECRET-CODE&state=SECRET-STATE&iss=x",
                             "1.1", 303), None)
    assert f.filter(rec) is True
    line = rec.getMessage()
    assert "SECRET" not in line and "code=[redacted]&state=[redacted]&iss=x" in line
    rec = logging.LogRecord("x", logging.INFO, "", 0,
                            "GET /install/linux?token=ngi_abc&arch=x64 failed", None, None)
    f.filter(rec)
    assert rec.getMessage() == "GET /install/linux?token=[redacted]&arch=x64 failed"
    rec = logging.LogRecord("x", logging.INFO, "", 0, "nothing to hide in %s", ("/hosts?page=2",), None)
    f.filter(rec)
    assert rec.getMessage() == "nothing to hide in /hosts?page=2"


def test_the_console_handler_redacts():
    from logging_config import build_config

    assert "redact_secrets" in build_config()["handlers"]["console"]["filters"]


def test_url_objects_in_log_arguments_are_redacted():
    import logging

    import httpx

    from logging_config import RedactSecretsFilter

    rec = logging.LogRecord("httpx", logging.INFO, "", 0, 'HTTP Request: %s %s "%s"',
                            ("GET", httpx.URL("https://x.example/api/auth/sso/callback?code=abc&ok=1"), 200), None)
    RedactSecretsFilter().filter(rec)
    assert rec.getMessage() ==         'HTTP Request: GET https://x.example/api/auth/sso/callback?code=[redacted]&ok=1 "200"'



def test_redaction_covers_exceptions_stacks_extras_and_form_bodies():
    import logging

    from logging_config import JsonFormatter, RedactSecretsFilter

    try:
        raise RuntimeError("token request failed: code_verifier=SECRET-V&grant_type=x")
    except RuntimeError:
        import sys

        info = sys.exc_info()
    rec = logging.LogRecord("x", logging.ERROR, "", 0, "failed", None, info)
    rec.stack_info = "Stack: /api/auth/sso/callback?code=SECRET-C"
    rec.target = "/api/auth/sso/callback?state=SECRET-S"
    RedactSecretsFilter().filter(rec)
    text = logging.Formatter("%(message)s %(target)s").format(rec)
    assert "SECRET" not in text and "code_verifier=[redacted]" in text
    assert "SECRET" not in JsonFormatter().format(rec)


def test_ordinary_code_and_state_values_stay_outside_the_sso_paths():
    from logging_config import redact_query

    assert redact_query("GET /api/v1/incidents?state=open&code=500") ==         "GET /api/v1/incidents?state=open&code=500"
    assert redact_query("/api/auth/sso/callback?code=a&state=b") ==         "/api/auth/sso/callback?code=[redacted]&state=[redacted]"
    assert redact_query("agent_token=keep&token=drop") == "agent_token=keep&token=[redacted]"


def test_a_broken_argument_never_breaks_logging():
    import logging

    from logging_config import RedactSecretsFilter

    class Exploding:
        def __str__(self):
            raise RuntimeError("no")

    class URL(Exploding):
        pass

    calls = []

    class Counting:
        def __str__(self):
            calls.append(1)
            return "counted"

    for arg in (Exploding(), URL(), Counting()):
        rec = logging.LogRecord("x", logging.INFO, "", 0, "value %s", (arg,), None)
        assert RedactSecretsFilter().filter(rec) is True
        assert rec.args[0] is arg
    assert calls == []   # objects that are not URLs are not formatted here
