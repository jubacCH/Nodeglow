"""Replay recorded vendor responses into an integration's collect().

Standard library + httpx only: this module runs inside the collector pack and
in the backend test suite alike. ``integrations`` and ``utils`` must already
be importable (the caller sets ``sys.path``).

Fixture format (backend/tests/fixtures/integrations/<type>.json)::

    {
      "type": "proxmox",
      "config": {...},                      # the decrypted config dict
      "reverse_dns": {"192.0.2.1": "gw.example.net", "192.0.2.9": null},
      "requests": [
        {"method": "GET", "url": "https://host/path?a=1",
         "params": {"b": "2"},              # optional, merged with the URL query
         "match_json": {...},               # optional, request JSON body must equal
         "match_form": {...},               # optional, form body must equal (str values)
         "match_headers": {"x-csrf-token": "..."},  # optional, must be present + equal
         "status": 200, "headers": {...},
         "json": {...}  |  "text": "..."}
      ]
    }

A request matches an entry when method, scheme, host, port and path are equal,
the query parameters are equal (exactly — a collector that adds or drops a
parameter is drift) and the optional body/header matchers hold. An unmatched
request raises :class:`ReplayError` and is recorded: collectors catch broad
exceptions (a failed optional sub-request is swallowed), so the caller must
check :attr:`Replay.unmatched` after collect() — :func:`run_fixture` does.
"""
from __future__ import annotations

import contextlib
import importlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx

# Transport-level options safe_async_client hands to the guarded transport;
# a MockTransport takes none of them.
_TRANSPORT_KWARGS = ("verify", "cert", "http1", "http2", "limits", "retries",
                     "trust_env", "local_address", "socket_options", "proxy", "uds")


class ReplayError(RuntimeError):
    """A request the fixture does not contain."""


def _split(url: str) -> tuple[str, str, int | None, str, dict[str, str]]:
    u = urlsplit(url)
    port = u.port or {"http": 80, "https": 443}.get(u.scheme)
    return u.scheme, (u.hostname or "").lower(), port, u.path or "/", dict(parse_qsl(u.query, keep_blank_values=True))


def _strs(d: dict) -> dict[str, str]:
    return {str(k): str(v) for k, v in d.items()}


@dataclass
class Replay:
    fixture: dict
    calls: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    used: set[int] = field(default_factory=set)

    def _matches(self, entry: dict, request: httpx.Request) -> bool:
        if entry.get("method", "GET").upper() != request.method:
            return False
        scheme, host, port, path, query = _split(entry["url"])
        query.update(_strs(entry.get("params") or {}))
        r_scheme, r_host, r_port, r_path, r_query = _split(str(request.url))
        if (scheme, host, port, path) != (r_scheme, r_host, r_port, r_path):
            return False
        if query != r_query:
            return False
        for name, value in (entry.get("match_headers") or {}).items():
            if request.headers.get(name) != value:
                return False
        if "match_json" in entry:
            try:
                if json.loads(request.content or b"null") != entry["match_json"]:
                    return False
            except ValueError:
                return False
        if "match_form" in entry:
            form = dict(parse_qsl(request.content.decode("utf-8"), keep_blank_values=True))
            if form != _strs(entry["match_form"]):
                return False
        return True

    def handler(self, request: httpx.Request) -> httpx.Response:
        desc = f"{request.method} {request.url}"
        self.calls.append(desc)
        for i, entry in enumerate(self.fixture["requests"]):
            if self._matches(entry, request):
                self.used.add(i)
                kwargs: dict[str, Any] = {"headers": entry.get("headers") or {}}
                if "json" in entry:
                    kwargs["json"] = entry["json"]
                else:
                    kwargs["text"] = entry.get("text", "")
                return httpx.Response(entry.get("status", 200), **kwargs)
        self.unmatched.append(desc)
        raise ReplayError(f"no fixture response for {desc}")

    def client_factory(self, **kwargs: Any) -> httpx.AsyncClient:
        for key in _TRANSPORT_KWARGS:
            kwargs.pop(key, None)
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler), **kwargs)

    def unused(self) -> list[str]:
        return [f"{e.get('method', 'GET')} {e['url']}"
                for i, e in enumerate(self.fixture["requests"]) if i not in self.used]


def load_fixture(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


@contextlib.contextmanager
def installed(fixture: dict):
    """Patch the integration module so collect() talks to the fixture."""
    mod = importlib.import_module(integration_class(fixture["type"]).__module__)
    replay = Replay(fixture)
    saved: dict[str, Any] = {}
    if not hasattr(mod, "safe_async_client"):
        raise ReplayError(f"integrations.{fixture['type']} has no safe_async_client to patch")
    saved["safe_async_client"] = mod.safe_async_client
    mod.safe_async_client = replay.client_factory
    if hasattr(mod, "reverse_lookup"):
        ptr = fixture.get("reverse_dns") or {}

        async def fake_reverse_lookup(ip: str) -> str | None:
            return ptr.get(ip)

        saved["reverse_lookup"] = mod.reverse_lookup
        mod.reverse_lookup = fake_reverse_lookup
    try:
        yield replay
    finally:
        for name, value in saved.items():
            setattr(mod, name, value)


def integration_class(type_name: str):
    """Registered class for an integration ``name`` (not always the module name)."""
    from integrations import get_integration

    cls = get_integration(type_name)
    if cls is None:
        raise LookupError(f"no integration named {type_name!r}")
    return cls


async def run_fixture(fixture: dict, strict_unused: bool = True) -> dict:
    """Run collect() against the fixture. Returns a result dict; ``ok`` is
    False if collect() failed, a request was unmatched or (strict) an entry
    went unused."""
    cls = integration_class(fixture["type"])
    t0 = time.perf_counter()
    with installed(fixture) as replay:
        result = await cls(dict(fixture["config"])).collect()
    problems = []
    if not result.success:
        problems.append(f"collect() failed: {result.error}")
    if replay.unmatched:
        problems.append(f"unmatched requests: {replay.unmatched}")
    if strict_unused and replay.unused():
        problems.append(f"fixture entries never requested: {replay.unused()}")
    return {
        "ok": not problems, "problems": problems,
        "success": result.success, "data": result.data, "error": result.error,
        "calls": replay.calls,
        "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
    }


def canonical(data: Any) -> str:
    """Canonical JSON used for the byte-for-byte comparison."""
    return json.dumps(data, sort_keys=True, ensure_ascii=False, indent=1, default=str) + "\n"
