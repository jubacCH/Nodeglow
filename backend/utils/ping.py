"""
Ping/HTTP/TCP/DNS/SSL utilities for host monitoring.
"""
from __future__ import annotations

import asyncio
import ipaddress
import random
import re
import socket
import ssl
import struct
import subprocess
import time
from datetime import datetime
from typing import TYPE_CHECKING

import httpx

from utils.http_options import BODY_LIMIT, MAX_REDIRECTS, build_url, of_host, status_matches

if TYPE_CHECKING:
    from database import PingHost


# ── ICMP ──────────────────────────────────────────────────────────────────────

async def _resolve_for_ping(hostname: str) -> str | None:
    """Resolve a name in-process, so the getaddrinfo cache applies.

    The ping binary would otherwise resolve it again in its own process on
    every check. IP literals pass through; an unresolvable name returns None.
    """
    try:
        ipaddress.ip_address(hostname)
        return hostname
    except ValueError:
        pass
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(hostname, None, type=socket.SOCK_RAW)
    except (socket.gaierror, UnicodeError):
        return None
    return infos[0][4][0] if infos else None


async def ping_host(hostname: str, timeout: float = 2.0) -> tuple[bool, float | None]:
    """Ping a host using system ping binary. Returns (success, latency_ms)."""
    target = await _resolve_for_ping(hostname)
    if target is None:
        return False, None
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping", "-c", "1", "-W", str(int(timeout)),
            target,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            output = stdout.decode()
            for token in output.split():
                if token.startswith("time="):
                    try:
                        latency = float(token.split("=")[1].replace("ms", "").strip())
                        return True, latency
                    except ValueError:
                        pass
            return True, None
        return False, None
    except (OSError, asyncio.TimeoutError, subprocess.SubprocessError):
        return False, None


# ── HTTP / HTTPS ───────────────────────────────────────────────────────────────

# One client per TLS-verification mode, shared by every check. Building an
# AsyncClient per check meant a fresh SSL context (and CA bundle load) for each
# HTTP check of each host every cycle. Keep-alive is disabled on purpose: every
# check still opens its own connection, so the latency it reports keeps
# including connect + TLS handshake, and a host that went away cannot hide
# behind a pooled connection.
#
# The clients never follow redirects themselves: check_http follows them hop by
# hop so each target can be vetted first (see _redirect_block_reason).
_http_clients: dict[tuple[int, bool], httpx.AsyncClient] = {}

_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})


class CheckResult(tuple):
    """``(ok, latency_ms)`` that also says why a failed check failed.

    A plain 2-tuple to every caller that unpacks it; ``reason`` is extra.
    """

    reason: str | None

    def __new__(cls, ok: bool, latency: float | None, reason: str | None = None):
        self = super().__new__(cls, (ok, latency))
        self.reason = reason
        return self


class HostCheck(tuple):
    """``(online, port_error, latency_ms, detail)`` plus ``errors``.

    ``errors`` maps each failed check's label to its reason, e.g.
    ``{"https": "status 503"}``.
    """

    errors: dict[str, str]

    def __new__(cls, online, port_error, latency, detail, errors=None):
        self = super().__new__(cls, (online, port_error, latency, detail))
        self.errors = errors or {}
        return self


def _http_client(verify_ssl: bool) -> httpx.AsyncClient:
    # Keyed by event loop too: a client's pool belongs to the loop it was
    # used on (the test suite runs each test on a fresh loop).
    loop_id = id(asyncio.get_running_loop())
    key = (loop_id, bool(verify_ssl))
    client = _http_clients.get(key)
    if client is None or client.is_closed:
        for stale in [k for k in _http_clients if k[0] != loop_id]:
            _http_clients.pop(stale, None)
        client = httpx.AsyncClient(
            verify=verify_ssl,
            follow_redirects=False,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=0),
        )
        _http_clients[key] = client
    return client


async def close_http_clients() -> None:
    """Close the shared HTTP check clients (call on shutdown)."""
    clients = list(_http_clients.values())
    _http_clients.clear()
    for client in clients:
        try:
            await client.aclose()
        except Exception:
            pass


async def _redirect_block_reason(url: httpx.URL) -> str | None:
    """Why a redirect target must not be followed, or None if it may be.

    The same rules as for adding a host (routers.integrations._validate_host):
    RFC1918 stays allowed — Nodeglow monitors LANs — but loopback, link-local,
    cloud metadata and our own compose service names are refused, by literal
    and by what the name resolves to. Without this a monitored host could
    answer "302 Location: http://169.254.169.254/" and have us fetch it.
    """
    if url.scheme not in ("http", "https"):
        return f"unsupported scheme '{url.scheme}'"
    from utils.net_safety import validate_host as _validate_host

    return await asyncio.to_thread(_validate_host, url.host)


def _short(text: str, limit: int = 60) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def _read_body(resp: httpx.Response, limit: int) -> str:
    chunks: list[bytes] = []
    size = 0
    async for chunk in resp.aiter_bytes():
        chunks.append(chunk)
        size += len(chunk)
        if size >= limit:
            break
    raw = b"".join(chunks)[:limit]
    try:
        return raw.decode(resp.encoding or "utf-8", errors="replace")
    except LookupError:  # unknown charset in Content-Type
        return raw.decode("utf-8", errors="replace")


def _status_label(ranges: list[tuple[int, int]]) -> str:
    return ",".join(str(lo) if lo == hi else f"{lo}-{hi}" for lo, hi in ranges)


async def check_http(
    url: str,
    timeout: float = 5.0,
    verify_ssl: bool = True,
    *,
    method: str = "GET",
    expected_status: list[tuple[int, int]] | None = None,
    keyword: str | None = None,
    keyword_absent: str | None = None,
    follow_redirects: bool = True,
    max_redirects: int = MAX_REDIRECTS,
    body_limit: int = BODY_LIMIT,
) -> CheckResult:
    """HTTP(S) check. Returns ``CheckResult(ok, latency_ms)`` with ``.reason``.

    Without ``expected_status`` any status below 500 counts as up (the
    historical rule). Keywords are case-insensitive substrings searched in the
    first ``body_limit`` bytes of the body — never a regex, so no pattern can
    stall the event loop. Redirects are followed hop by hop, at most
    ``max_redirects``, and every hop's target is vetted against SSRF first.
    Latency is measured to the final response's headers.
    """
    client = _http_client(verify_ssl)
    want_body = method != "HEAD" and bool(keyword or keyword_absent)
    try:
        request = client.build_request(method, url, timeout=timeout)
        start = time.perf_counter()
        hops = 0
        while True:
            resp = await client.send(request, stream=True)
            try:
                if (follow_redirects and resp.status_code in _REDIRECT_CODES
                        and resp.next_request is not None):
                    if hops >= max_redirects:
                        return CheckResult(False, None, f"more than {max_redirects} redirects")
                    nxt = resp.next_request
                    blocked = await _redirect_block_reason(nxt.url)
                    if blocked:
                        target = _short(nxt.url.host or "?")
                        return CheckResult(False, None, f"redirect to {target} blocked: {blocked}")
                    hops += 1
                    request = nxt
                    continue

                latency = round((time.perf_counter() - start) * 1000, 2)
                code = resp.status_code
                if not status_matches(code, expected_status):
                    reason = f"status {code}"
                    if expected_status is not None:
                        reason += f" (expected {_status_label(expected_status)})"
                    return CheckResult(False, latency, reason)
                if want_body:
                    body = (await _read_body(resp, body_limit)).casefold()
                    if keyword and keyword.casefold() not in body:
                        return CheckResult(False, latency, f"keyword '{_short(keyword)}' missing")
                    if keyword_absent and keyword_absent.casefold() in body:
                        return CheckResult(False, latency, f"keyword '{_short(keyword_absent)}' present")
                return CheckResult(True, latency)
            finally:
                await resp.aclose()
    except httpx.TimeoutException:
        return CheckResult(False, None, f"timeout after {timeout:g}s")
    except httpx.ConnectError as exc:
        if "certificate verify failed" in str(exc).lower():
            return CheckResult(False, None, "TLS certificate verification failed")
        return CheckResult(False, None, "connection failed")
    except (httpx.HTTPError, OSError, asyncio.TimeoutError) as exc:
        return CheckResult(False, None, f"request failed ({type(exc).__name__})")


# ── TCP ───────────────────────────────────────────────────────────────────────

async def check_tcp(hostname: str, port: int, timeout: float = 3.0) -> tuple[bool, float | None]:
    """TCP connect check. Returns (success, latency_ms)."""
    try:
        start = time.perf_counter()
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(hostname, port), timeout=timeout
        )
        latency = round((time.perf_counter() - start) * 1000, 2)
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        return True, latency
    except (OSError, asyncio.TimeoutError):
        return False, None


# ── DNS ───────────────────────────────────────────────────────────────────────

def build_dns_query(qname: str, qid: int) -> bytes:
    """Recursive A query for qname in DNS wire format."""
    header = struct.pack("!HHHHHH", qid, 0x0100, 1, 0, 0, 0)  # RD set, one question
    labels = b"".join(bytes([len(p)]) + p.encode("idna") for p in qname.rstrip(".").split(".") if p)
    return header + labels + b"\x00" + struct.pack("!HH", 1, 1)  # QTYPE A, QCLASS IN


def dns_response_ok(data: bytes, qid: int) -> bool:
    """True if data answers query qid with NOERROR and at least one answer record."""
    if len(data) < 12:
        return False
    rid, flags, _qd, ancount, _ns, _ar = struct.unpack("!HHHHHH", data[:12])
    return rid == qid and bool(flags & 0x8000) and (flags & 0x000F) == 0 and ancount > 0


async def check_dns(server: str, qname: str, port: int = 53,
                    timeout: float = 3.0) -> tuple[bool, float | None]:
    """Ask a DNS server to resolve qname (A) over UDP. Returns (success, latency_ms).

    Success needs a NOERROR answer with records, so a server that is up but returns
    SERVFAIL/NXDOMAIN for a name it should know counts as failed.
    """
    loop = asyncio.get_running_loop()
    qid = random.randint(0, 0xFFFF)
    answer: asyncio.Future[bytes] = loop.create_future()

    class _Proto(asyncio.DatagramProtocol):
        def datagram_received(self, data, addr):
            if not answer.done() and len(data) >= 2 and struct.unpack("!H", data[:2])[0] == qid:
                answer.set_result(data)

        def error_received(self, exc):
            if not answer.done():
                answer.set_exception(exc)

    transport = None
    try:
        start = time.perf_counter()
        transport, _ = await loop.create_datagram_endpoint(_Proto, remote_addr=(server, port))
        transport.sendto(build_dns_query(qname, qid))
        data = await asyncio.wait_for(answer, timeout=timeout)
        latency = round((time.perf_counter() - start) * 1000, 2)
        return (True, latency) if dns_response_ok(data, qid) else (False, None)
    except (OSError, asyncio.TimeoutError, UnicodeError):
        return False, None
    finally:
        if transport is not None:
            transport.close()


# ── SSL expiry ─────────────────────────────────────────────────────────────────

async def get_ssl_expiry_days(hostname: str, port: int = 443) -> int | None:
    """Return days until SSL certificate expiry for an HTTPS host, or None on error."""
    try:
        loop = asyncio.get_event_loop()
        cert_pem = await loop.run_in_executor(
            None, lambda: ssl.get_server_certificate((hostname, port), timeout=5)
        )
        proc = await asyncio.create_subprocess_exec(
            "openssl", "x509", "-noout", "-enddate",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate(input=cert_pem.encode())
        line = stdout.decode().strip()
        date_str = line.split("=", 1)[1].strip()
        from datetime import timezone
        # Strip timezone name (e.g. "GMT", "UTC") — OpenSSL always outputs UTC
        clean_date = re.sub(r'\s+\w+$', '', date_str)
        expiry = datetime.strptime(clean_date, "%b %d %H:%M:%S %Y").replace(tzinfo=timezone.utc)
        delta = expiry - datetime.now(timezone.utc)
        return max(0, delta.days)
    except (ssl.SSLError, OSError, asyncio.TimeoutError,
            subprocess.SubprocessError, ValueError, IndexError):
        return None


# ── Dispatcher ─────────────────────────────────────────────────────────────────

def http_check_url(host: "PingHost", ct: str) -> str:
    """The URL an http/https check of this host requests."""
    # Use hostname (FQDN) for HTTP/HTTPS — SSL certs need the domain name
    hostname = host.hostname
    if hostname.startswith("http://") or hostname.startswith("https://"):
        base = hostname
    else:
        scheme = "https" if ct == "https" else "http"
        base = f"{scheme}://{hostname}"
    return build_url(base, of_host(host))


async def _check_single(host: "PingHost", ct: str) -> tuple[bool, float | None]:
    """Run a single check type for the given host.

    The result may be a :class:`CheckResult` carrying a failure reason.
    """
    ct = ct.lower()
    # Prefer ip_address for network checks, fall back to hostname
    target = getattr(host, "ip_address", None) or host.hostname
    if ct == "icmp":
        return await ping_host(target)
    if ct in ("http", "https"):
        opts = of_host(host)
        # verify_tls defaults to False: internal hosts often use self-signed certs
        return await check_http(
            http_check_url(host, ct),
            timeout=opts.timeout,
            verify_ssl=opts.verify_tls,
            method=opts.method,
            expected_status=opts.status_ranges,
            keyword=opts.keyword,
            keyword_absent=opts.keyword_absent,
            follow_redirects=opts.follow_redirects,
        )
    # TCP — supports both "tcp" (uses host.port) and "tcp:PORT" format
    if ct == "tcp" or ct.startswith("tcp:"):
        if ":" in ct:
            port = int(ct.split(":")[1])
        else:
            port = host.port or 80
        ok, lat = await check_tcp(target, port)
        return CheckResult(ok, lat, None if ok else f"port {port} unreachable")
    # DNS — "dns:NAME" asks the host (as a DNS server) to resolve NAME
    if ct.startswith("dns:") and ct[4:]:
        ok, lat = await check_dns(target, ct[4:])
        return CheckResult(ok, lat, None if ok else f"no answer for {_short(ct[4:])}")
    return await ping_host(target)


async def check_host(host: "PingHost") -> HostCheck:
    """Run all check types in parallel.

    Returns (online, port_error, latency_ms, check_detail):
      - online: True if ICMP succeeds (or no ICMP configured and any check passes)
      - port_error: True if host is online but a port/http/https check failed
      - latency_ms: ICMP latency preferred, else first available
      - check_detail: per-check results dict, e.g. {"icmp": true, "https": false}
    The returned tuple's ``errors`` attribute maps failed checks to a reason
    ({"https": "status 503"}); ICMP failures have none (the host is down).
    """
    types = [t.strip() for t in (host.check_type or "icmp").split(",") if t.strip()]
    results: list[tuple[bool, float | None]] = await asyncio.gather(
        *[_check_single(host, ct) for ct in types]
    )

    # Build per-check detail
    detail: dict = {}
    errors: dict[str, str] = {}
    for ct, res in zip(types, results):
        ok = res[0]
        label = ct
        # Legacy "tcp" without port suffix — add host.port for clarity
        if ct == "tcp" and host.port and ":" not in ct:
            label = f"tcp:{host.port}"
        detail[label] = ok
        reason = getattr(res, "reason", None)
        if not ok and reason:
            errors[label] = reason

    # Determine online status from ICMP only
    icmp_types = [i for i, t in enumerate(types) if t == "icmp"]
    service_types = [i for i, t in enumerate(types) if t != "icmp"]

    if icmp_types:
        online = all(results[i][0] for i in icmp_types)
    else:
        # No ICMP configured — use all checks for online status
        online = any(r[0] for r in results)

    # Port error: host is online but a non-ICMP check failed
    port_error = False
    if online and service_types:
        port_error = any(not results[i][0] for i in service_types)

    # Latency: prefer ICMP
    primary: float | None = None
    if icmp_types:
        primary = results[icmp_types[0]][1]
    if primary is None:
        primary = next((r[1] for r in results if r[1] is not None), None)
    return HostCheck(online, port_error, primary, detail, errors)
