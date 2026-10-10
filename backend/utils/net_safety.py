"""Shared SSRF guards for outbound connections.

Nodeglow monitors LANs, so RFC1918 / ULA private ranges are deliberately
ALLOWED: the devices, integrations and webhook receivers it talks to live
there. What is blocked is what no legitimate target ever is — loopback,
link-local, ``0.0.0.0``, multicast, cloud metadata endpoints and our own
compose services (whose addresses are RFC1918 like any LAN host, so only the
name gives them away).

Two layers:

* :func:`validate_host` / :func:`is_safe_url` — checked when a target is
  saved and again right before a request (literal + DNS resolution).
* :class:`GuardedAsyncTransport` / :func:`safe_async_client` — re-checks the
  address at *connect time* and connects to exactly the IP it checked, so a
  name rebound between validation and use (DNS rebinding) or a redirect to an
  internal address still cannot reach a blocked target. TLS SNI and
  certificate verification keep using the hostname from the URL.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from typing import Any, Iterable
from urllib.parse import urlparse

import httpcore
import httpx

logger = logging.getLogger(__name__)

METADATA_IPS = frozenset({
    ipaddress.ip_address("169.254.169.254"),  # AWS / GCP / OpenStack metadata
    ipaddress.ip_address("168.63.129.16"),    # Azure wireserver
    ipaddress.ip_address("100.100.100.200"),  # Alibaba Cloud metadata
    ipaddress.ip_address("fd00:ec2::254"),    # AWS IMDS over IPv6
})
METADATA_HOSTS = frozenset({
    "metadata.google.internal", "metadata", "instance-data",
    "metadata.internal", "kubernetes.default.svc", "kubernetes.default",
})
# Our own compose services (docker-compose.yml service + container names).
INTERNAL_HOSTS = frozenset({
    "localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback",
    "db", "postgres", "clickhouse", "nodeglow-ch", "updater", "nodeglow-updater",
    "nodeglow", "frontend", "nodeglow-frontend",
})
_ZERO_NET = ipaddress.ip_network("0.0.0.0/8")  # "this host" on Linux


class UnsafeTargetError(httpx.ConnectError):
    """Raised by the guarded transport when a connection target is blocked.

    Subclasses ``httpx.ConnectError`` so callers that already handle
    connection failures treat it the same way.
    """


def extract_host(value: str) -> str:
    """Hostname (or IP literal) from a URL, host:port, [v6]:port or bare host."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:  # bare IPv6 literal ("fe80::1") — urlparse would read it as host:port
        return str(ipaddress.ip_address(raw.split("%", 1)[0]))
    except ValueError:
        pass
    if "://" not in raw:
        raw = "//" + raw
    try:
        host = urlparse(raw).hostname or ""
    except ValueError:  # malformed [v6 literal
        host = ""
    return host.strip().rstrip(".").lower()


def blocked_ip_reason(addr) -> str | None:
    """Why an address must not be an outbound target, or None if allowed."""
    if getattr(addr, "ipv4_mapped", None) is not None:
        addr = addr.ipv4_mapped
    if addr in METADATA_IPS:
        return "Cloud metadata endpoints are not allowed"
    if addr.is_loopback:
        return "Loopback addresses are not allowed"
    if addr.is_unspecified or (addr.version == 4 and addr in _ZERO_NET):
        return "Unspecified addresses (0.0.0.0) are not allowed"
    if addr.is_link_local:
        return "Link-local addresses are not allowed (cloud metadata risk)"
    if addr.is_multicast:
        return "Multicast addresses are not allowed"
    return None


def blocked_name_reason(host: str) -> str | None:
    """Reason a *name* (not an address) is blocked, or None."""
    host = host.strip().rstrip(".").lower()
    if host in INTERNAL_HOSTS:
        return "Internal service names are not allowed"
    if host in METADATA_HOSTS:
        return "Cloud metadata endpoints are not allowed"
    return None


def _parse_ip(value: str):
    try:
        return ipaddress.ip_address(str(value).split("%", 1)[0])
    except ValueError:
        return None


def resolve_all(host: str) -> list:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        return []
    out = []
    for info in infos:
        addr = _parse_ip(info[4][0])
        if addr is not None:
            out.append(addr)
    return out


def validate_host(value: str, resolve: bool = True) -> str | None:
    """Validate a host/URL value against SSRF. Returns an error or None.

    A name that does not resolve is allowed: the device may simply be offline,
    and an unresolvable target reaches nothing. Rebinding after this check is
    caught by :class:`GuardedAsyncTransport`, not here.

    Synchronous and may block on DNS — use :func:`validate_host_async` from
    async code.
    """
    if not value:
        return None
    host = extract_host(str(value))
    if not host:
        return None
    if reason := blocked_name_reason(host):
        return reason
    literal = _parse_ip(host)
    if literal is not None:
        return blocked_ip_reason(literal)
    if resolve:
        for addr in resolve_all(host):
            reason = blocked_ip_reason(addr)
            if reason:
                return f"{reason} ({host} resolves to {addr})"
    return None


async def validate_host_async(value: str) -> str | None:
    return await asyncio.to_thread(validate_host, value)


def url_error(url: str, resolve: bool = True) -> str | None:
    """Like :func:`validate_host` for a full URL; also requires http(s)."""
    try:
        parsed = urlparse(str(url or ""))
    except ValueError:
        return "Malformed URL"
    if parsed.scheme not in ("http", "https"):
        return "Only http:// and https:// URLs are allowed"
    try:
        hostname = parsed.hostname
    except ValueError:
        hostname = None
    if not hostname:
        return "URL has no host"
    return validate_host(url, resolve=resolve)


def is_safe_url(url: str, resolve: bool = True) -> bool:
    try:
        return url_error(url, resolve=resolve) is None
    except Exception:  # never let a malformed value pass by raising
        return False


# ── Connect-time guard ──────────────────────────────────────────────────────


class _GuardedBackend(httpcore.AsyncNetworkBackend):
    """Network backend that resolves, checks, then connects to the checked IP.

    httpcore passes the URL host to ``connect_tcp``; TLS is started afterwards
    with ``server_hostname`` taken from the request URL, so connecting to the
    IP literal keeps SNI and hostname verification intact.
    """

    def __init__(self, inner: httpcore.AsyncNetworkBackend):
        self._inner = inner

    async def connect_tcp(self, host: str, port: int, timeout: float | None = None,
                          local_address: str | None = None,
                          socket_options: Iterable[Any] | None = None):
        addrs = await _checked_addresses(host, port)
        last_exc: Exception | None = None
        for addr in addrs:
            try:
                return await self._inner.connect_tcp(
                    str(addr), port, timeout=timeout,
                    local_address=local_address, socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout, OSError) as exc:
                last_exc = exc
        if last_exc is not None:
            raise last_exc
        raise httpcore.ConnectError(f"Could not resolve {host}")

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise httpcore.ConnectError("Unix sockets are not allowed for outbound requests")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


async def _checked_addresses(host: str, port: int) -> list:
    name = host.strip().strip("[]")
    if reason := blocked_name_reason(name):
        raise httpcore.ConnectError(f"Blocked outbound connection to {host}: {reason}")
    literal = _parse_ip(name)
    if literal is not None:
        addrs = [literal]
    else:
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(name, port, proto=socket.IPPROTO_TCP)
        except (socket.gaierror, UnicodeError, OSError) as exc:
            raise httpcore.ConnectError(f"Could not resolve {host}: {exc}") from exc
        addrs = []
        for info in infos:
            addr = _parse_ip(info[4][0])
            if addr is not None and addr not in addrs:
                addrs.append(addr)
    # Any blocked answer refuses the whole name: an attacker controlling DNS
    # could otherwise mix a public and an internal address.
    for addr in addrs:
        if reason := blocked_ip_reason(addr):
            raise httpcore.ConnectError(
                f"Blocked outbound connection to {host} ({addr}): {reason}"
            )
    return addrs


class GuardedAsyncTransport(httpx.AsyncHTTPTransport):
    """``httpx.AsyncHTTPTransport`` that applies the SSRF guard on connect.

    Every new TCP connection — including the ones for redirect hops — goes
    through :class:`_GuardedBackend`. Requests routed through an environment
    proxy (``HTTP(S)_PROXY``) use httpx's proxy transport instead and are
    governed by the proxy; the pre-request URL check still applies to them.
    """

    def __init__(self, **kwargs: Any) -> None:
        kwargs.pop("proxy", None)
        kwargs.pop("uds", None)
        super().__init__(**kwargs)
        pool = self._pool
        if not hasattr(pool, "_network_backend"):  # httpcore internals changed
            raise RuntimeError("httpcore pool has no _network_backend; SSRF guard unavailable")
        pool._network_backend = _GuardedBackend(pool._network_backend)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            return await super().handle_async_request(request)
        except httpx.ConnectError as exc:
            if "Blocked outbound connection" in str(exc):
                raise UnsafeTargetError(str(exc), request=request) from exc
            raise


_TRANSPORT_KWARGS = ("verify", "cert", "http1", "http2", "limits", "retries",
                     "trust_env", "local_address", "socket_options")


def safe_async_client(**kwargs: Any) -> httpx.AsyncClient:
    """Drop-in for ``httpx.AsyncClient(...)`` with the connect-time SSRF guard.

    Transport-level options (``verify``, ``cert``, ``http2``, …) are handed to
    the guarded transport, because httpx ignores them on the client when an
    explicit transport is given.
    """
    if "transport" in kwargs:
        raise TypeError("safe_async_client builds its own transport")
    transport_kwargs = {k: kwargs.pop(k) for k in _TRANSPORT_KWARGS if k in kwargs}
    if "trust_env" in transport_kwargs:
        kwargs["trust_env"] = transport_kwargs["trust_env"]
    kwargs.setdefault("follow_redirects", False)
    return httpx.AsyncClient(transport=GuardedAsyncTransport(**transport_kwargs), **kwargs)
