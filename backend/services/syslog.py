"""
Syslog receiver – asyncio UDP + TCP server with RFC 3164/5424 parsing,
write-buffered DB inserts, and auto-host assignment.
"""
import asyncio
import logging
import os
import re
import socket
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from models.base import AsyncSessionLocal
from models.ping import PingHost

log = logging.getLogger("nodeglow.syslog")

# ── Per-IP rate limiter for syslog ingestion ────────────────────────────────

def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float, minimum: float = 0.05) -> float:
    try:
        return max(minimum, float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


_MAX_MSG_SIZE = 64 * 1024       # 64 KB max syslog message
_MAX_TCP_CONNECTIONS = _env_int("NODEGLOW_SYSLOG_MAX_TCP_CONNECTIONS", 100)
# Rate limits, configurable for fleets that legitimately send more.
_SYSLOG_RATE_WINDOW = _env_int("NODEGLOW_SYSLOG_RATE_WINDOW", 10)          # seconds
_SYSLOG_RATE_MAX = _env_int("NODEGLOW_SYSLOG_RATE_MAX", 500)               # per IP per window
_GLOBAL_RATE_MAX = _env_int("NODEGLOW_SYSLOG_GLOBAL_RATE_MAX", 10_000)     # all IPs per window
_ip_msg_counts: dict[str, list] = {}  # ip -> [count, window_start]
_rate_dropped: dict[str, int] = {}    # ip -> dropped count (for periodic logging)
_global_msg_count: list = [0, 0.0]    # [count, window_start]
_tcp_connection_count: int = 0        # active TCP connections
_tcp_count_lock = asyncio.Lock()


_RATE_CACHE_MAX = 10_000  # max tracked IPs before cleanup


def _syslog_rate_ok(source_ip: str) -> bool:
    """Return True if this IP is within the rate limit, False if it should be dropped."""
    import time
    now = time.monotonic()
    entry = _ip_msg_counts.get(source_ip)
    if entry is None or now - entry[1] >= _SYSLOG_RATE_WINDOW:
        _ip_msg_counts[source_ip] = [1, now]
        # Log and reset drop counter on window reset
        dropped = _rate_dropped.pop(source_ip, 0)
        if dropped:
            log.warning("Syslog rate limit: dropped %d messages from %s in last window", dropped, source_ip)
        # Evict stale entries to prevent unbounded growth
        if len(_ip_msg_counts) > _RATE_CACHE_MAX:
            stale = [ip for ip, v in _ip_msg_counts.items() if now - v[1] >= _SYSLOG_RATE_WINDOW * 6]
            for ip in stale:
                del _ip_msg_counts[ip]
                _rate_dropped.pop(ip, None)
        return True
    entry[0] += 1
    if entry[0] > _SYSLOG_RATE_MAX:
        _rate_dropped[source_ip] = _rate_dropped.get(source_ip, 0) + 1
        _count_drop("rate_limit_ip")
        return False
    return True


def _global_rate_ok() -> bool:
    """Check global rate limit across all IPs."""
    import time
    now = time.monotonic()
    if now - _global_msg_count[1] >= _SYSLOG_RATE_WINDOW:
        _global_msg_count[0] = 1
        _global_msg_count[1] = now
        return True
    _global_msg_count[0] += 1
    if _global_msg_count[0] <= _GLOBAL_RATE_MAX:
        return True
    _count_drop("rate_limit_global")
    return False


def _count_drop(reason: str, n: int = 1) -> None:
    """nodeglow_syslog_messages_dropped_total{reason}; never raises."""
    try:
        from services.metrics import SYSLOG_MESSAGES_DROPPED
        SYSLOG_MESSAGES_DROPPED.labels(reason=reason).inc(n)
    except Exception:
        pass


# ── RFC 3164 (BSD syslog) parser ────────────────────────────────────────────

# <PRI>TIMESTAMP HOSTNAME APP[PID]: MESSAGE
_RFC3164_RE = re.compile(
    r"<(\d{1,3})>"
    r"(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+"  # Mon DD HH:MM:SS
    r"(\S+)\s+"                                      # hostname
    r"(.+)"                                           # rest (app + message)
)

_RFC3164_TS_FMTS = [
    "%b %d %H:%M:%S",
    "%b  %d %H:%M:%S",
]


def _parse_3164_ts(ts_str: str) -> datetime:
    now = datetime.utcnow()
    for fmt in _RFC3164_TS_FMTS:
        try:
            dt = datetime.strptime(ts_str, fmt)
            return dt.replace(year=now.year)
        except ValueError:
            continue
    return now


def _split_app_message(rest: str) -> tuple[Optional[str], str]:
    """Split 'app[pid]: message' or 'app: message' into (app_name, message)."""
    m = re.match(r"(\S+?)(?:\[\d+\])?:\s*(.*)", rest, re.DOTALL)
    if m:
        return m.group(1), m.group(2)
    return None, rest


# ── RFC 5424 parser ─────────────────────────────────────────────────────────

# <PRI>VERSION TIMESTAMP HOSTNAME APP-NAME PROCID MSGID [SD] MSG
_RFC5424_RE = re.compile(
    r"<(\d{1,3})>"
    r"(\d+)\s+"                                      # version
    r"(\S+)\s+"                                      # timestamp (ISO 8601)
    r"(\S+)\s+"                                      # hostname
    r"(\S+)\s+"                                      # app-name
    r"(\S+)\s+"                                      # procid
    r"(\S+)\s*"                                      # msgid
    r"(?:\[.*?\]\s*)?"                               # structured data (skip)
    r"(.*)"                                           # message
)


def _parse_5424_ts(ts_str: str) -> datetime:
    if ts_str == "-":
        return datetime.utcnow()
    # ISO 8601 with optional fractional seconds and timezone
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
    ):
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            continue
    return datetime.utcnow()


# ── Unified parser ──────────────────────────────────────────────────────────

def parse_syslog(raw: str, source_ip: str) -> dict:
    """Parse a raw syslog message. Returns dict ready for SyslogMessage fields."""
    raw = raw.strip()
    if not raw:
        return None

    # Try RFC 5424 first (has version number after PRI)
    m = _RFC5424_RE.match(raw)
    if m:
        pri = min(int(m.group(1)), 191)  # RFC max: facility 23 * 8 + severity 7
        facility = pri >> 3
        severity = pri & 7
        ts = _parse_5424_ts(m.group(3))
        hostname = m.group(4) if m.group(4) != "-" else None
        app_name = m.group(5) if m.group(5) != "-" else None
        message = m.group(8) or ""
        return {
            "timestamp": ts,
            "source_ip": source_ip,
            "hostname": hostname,
            "facility": facility,
            "severity": severity,
            "app_name": app_name,
            "message": message.strip(),
        }

    # Try RFC 3164
    m = _RFC3164_RE.match(raw)
    if m:
        pri = min(int(m.group(1)), 191)  # RFC max: facility 23 * 8 + severity 7
        facility = pri >> 3
        severity = pri & 7
        ts = _parse_3164_ts(m.group(2))
        hostname = m.group(3)
        rest = m.group(4)

        # Detect dual-timestamp format (UniFi etc.): hostname is actually an ISO timestamp
        # e.g. <PRI>Mon DD HH:MM:SS 2026-03-07T07:26:58.42112 HOSTNAME APP: message
        if re.match(r"\d{4}-\d{2}-\d{2}T", hostname):
            parts = rest.split(None, 1)
            if parts:
                hostname = parts[0]
                rest = parts[1] if len(parts) > 1 else ""

        app_name, message = _split_app_message(rest)
        return {
            "timestamp": ts,
            "source_ip": source_ip,
            "hostname": hostname,
            "facility": facility,
            "severity": severity,
            "app_name": app_name,
            "message": message.strip(),
        }

    # Fallback: just PRI + message
    pri_match = re.match(r"<(\d{1,3})>(.*)", raw, re.DOTALL)
    if pri_match:
        pri = int(pri_match.group(1))
        return {
            "timestamp": datetime.utcnow(),
            "source_ip": source_ip,
            "hostname": None,
            "facility": pri >> 3,
            "severity": pri & 7,
            "app_name": None,
            "message": pri_match.group(2).strip(),
        }

    # No PRI — try RFC 3164 timestamp pattern without PRI header
    m = re.match(
        r"(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+"
        r"(\S+)\s+"
        r"(.+)", raw, re.DOTALL
    )
    if m:
        ts = _parse_3164_ts(m.group(1))
        hostname = m.group(2)
        rest = m.group(3)

        # Dual-timestamp (UniFi): hostname slot is actually an ISO timestamp
        if re.match(r"\d{4}-\d{2}-\d{2}T", hostname):
            parts = rest.split(None, 1)
            if parts:
                hostname = parts[0]
                rest = parts[1] if len(parts) > 1 else ""

        app_name, message = _split_app_message(rest)
        return {
            "timestamp": ts,
            "source_ip": source_ip,
            "hostname": hostname,
            "facility": None,
            "severity": 6,  # informational (no PRI to derive from)
            "app_name": app_name,
            "message": message.strip(),
        }

    return {
        "timestamp": datetime.utcnow(),
        "source_ip": source_ip,
        "hostname": None,
        "facility": None,
        "severity": 6,  # informational
        "app_name": None,
        "message": raw,
    }


# ── Host cache for auto-assignment ──────────────────────────────────────────

_host_cache: dict[str, int] = {}  # ip_or_hostname -> host_id
_host_cache_ts: float = 0.0
_HOST_CACHE_TTL = 120  # seconds
_allowlist_only: bool = False  # when True, only accept syslog from known hosts
_allowlist_ips: set[str] = set()  # resolved IPs of known hosts (fast lookup)


def _is_allowed_source(source_ip: str) -> bool:
    """Check if source IP is from a known host. Only enforced when allowlist is enabled."""
    if not _allowlist_only:
        return True
    return source_ip in _allowlist_ips


_DNS_CONCURRENCY = _env_int("NODEGLOW_SYSLOG_DNS_CONCURRENCY", 16)
_DNS_TIMEOUT = _env_float("NODEGLOW_SYSLOG_DNS_TIMEOUT", 3.0)


async def _resolve_many(names: list[str], resolver) -> dict[str, Optional[str]]:
    """Run a blocking resolver for many names concurrently.

    ``resolver(name)`` runs in the default executor; at most _DNS_CONCURRENCY
    run at once and each is abandoned after _DNS_TIMEOUT. Returns
    {name: result or None}. ``resolver`` may return a str or a tuple whose
    first element is the name (gethostbyaddr).
    """
    loop = asyncio.get_running_loop()
    sem = asyncio.Semaphore(_DNS_CONCURRENCY)

    async def one(name: str):
        async with sem:
            try:
                res = await asyncio.wait_for(
                    loop.run_in_executor(None, resolver, name), timeout=_DNS_TIMEOUT,
                )
            except Exception:
                return name, None
            if isinstance(res, tuple):
                res = res[0] if res else None
            return name, res

    return dict(await asyncio.gather(*(one(n) for n in names)))


async def _refresh_host_cache():
    global _host_cache, _host_cache_ts, _allowlist_only, _allowlist_ips
    import time
    now = time.time()
    if now - _host_cache_ts < _HOST_CACHE_TTL and _host_cache:
        return
    try:
        from database import get_setting
        async with AsyncSessionLocal() as db:
            hosts = (await db.execute(select(PingHost))).scalars().all()
            _allowlist_only = (await get_setting(db, "syslog_allowlist_only", "0")) == "1"
        cache: dict[str, int] = {}
        allowed_ips: set[str] = set()
        raws: list[tuple[str, int]] = []
        for h in hosts:
            cache[h.hostname.lower()] = h.id
            if h.name:
                cache[h.name.lower()] = h.id

            # Strip URL parts to get raw hostname/IP
            raw = h.hostname
            for prefix in ("https://", "http://"):
                if raw.startswith(prefix):
                    raw = raw[len(prefix):]
            raw = raw.split("/")[0].split(":")[0]
            cache[raw.lower()] = h.id
            raws.append((raw, h.id))

            # If raw is already an IP, add it directly
            try:
                socket.inet_aton(raw)
                allowed_ips.add(raw)
            except OSError:
                pass

        # Forward DNS: resolve hostname to IP (catches FQDNs → IPs). These ran
        # one after another, so a few unresolvable names (each waiting for the
        # resolver timeout) stalled the refresh for minutes. Now concurrent,
        # bounded, and each lookup capped.
        resolved = await _resolve_many(
            sorted({raw for raw, _ in raws}), socket.gethostbyname,
        )
        for raw, host_id in raws:
            ip = resolved.get(raw)
            if ip:
                cache[ip] = host_id
                allowed_ips.add(ip)

        _host_cache = cache
        _allowlist_ips = allowed_ips
        _host_cache_ts = now
        log.debug("Syslog host cache refreshed: %d entries from %d hosts (allowlist=%s, %d IPs)",
                  len(cache), len(hosts), _allowlist_only, len(allowed_ips))
    except Exception as e:
        log.warning("Failed to refresh host cache: %s", e)


# Reverse DNS cache for unknown source IPs
_rdns_cache: dict[str, Optional[str]] = {}
_RDNS_CACHE_TTL = 300  # 5 minutes
_RDNS_CACHE_MAX = 5_000  # max cached entries
_rdns_cache_ts: float = 0.0


def _resolve_host_id(source_ip: str, hostname: Optional[str]) -> Optional[int]:
    """Try to match source_ip or hostname to a PingHost."""
    if hostname and hostname.lower() in _host_cache:
        return _host_cache[hostname.lower()]
    if source_ip in _host_cache:
        return _host_cache[source_ip]
    if source_ip.lower() in _host_cache:
        return _host_cache[source_ip.lower()]

    # Try reverse DNS on source_ip (cached)
    rdns_name = _rdns_cache.get(source_ip)
    if rdns_name and rdns_name.lower() in _host_cache:
        return _host_cache[rdns_name.lower()]
    # Also try short hostname from FQDN (e.g. "gw.example.com" → "gw")
    if rdns_name and "." in rdns_name:
        short = rdns_name.split(".")[0].lower()
        if short in _host_cache:
            return _host_cache[short]

    return None


async def _rdns_resolve_loop():
    """Periodically resolve reverse DNS for unknown source IPs."""
    global _rdns_cache, _rdns_cache_ts
    import time
    while True:
        await asyncio.sleep(30)
        now = time.time()
        if now - _rdns_cache_ts < _RDNS_CACHE_TTL:
            continue
        # Evict entire cache periodically to prevent unbounded growth
        if len(_rdns_cache) > _RDNS_CACHE_MAX:
            _rdns_cache.clear()
        _rdns_cache_ts = now
        # Collect unique source IPs from recent buffer entries that had no host_id.
        # Snapshot the shared buffer under the lock — other tasks reassign/mutate it.
        ips_to_resolve = set()
        async with _buffer_lock:
            snapshot = list(_buffer)
        for entry in snapshot:
            if not entry.get("host_id") and entry.get("source_ip"):
                ips_to_resolve.add(entry["source_ip"])
        # Also resolve IPs we haven't seen before (concurrently, bounded)
        todo = sorted(ip for ip in ips_to_resolve if ip not in _rdns_cache)
        if todo:
            _rdns_cache.update(await _resolve_many(todo, socket.gethostbyaddr))


# ── Live tail broadcast ────────────────────────────────────────────────────

_subscribers: list[asyncio.Queue] = []


def subscribe() -> asyncio.Queue:
    """Subscribe to live syslog stream. Returns an asyncio.Queue."""
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _subscribers.append(q)
    return q


def unsubscribe(q: asyncio.Queue):
    """Unsubscribe from live syslog stream."""
    if q in _subscribers:
        _subscribers.remove(q)


# ── Write buffer ────────────────────────────────────────────────────────────
#
# Messages are appended to _buffer and written to ClickHouse in batches by a
# single flusher task: whenever _BUFFER_SIZE messages are waiting, or every
# _FLUSH_INTERVAL seconds, whichever comes first. The lock only guards the
# list swap — the insert itself runs outside it. It used to be awaited while
# holding the lock, so every receiver stalled for the duration of each
# ClickHouse round trip, in batches of only 100.

_buffer: list[dict] = []
_buffer_lock = asyncio.Lock()
_insert_lock = asyncio.Lock()  # one insert at a time, in order
_BUFFER_SIZE = _env_int("NODEGLOW_SYSLOG_BATCH_SIZE", 1000)
_BUFFER_MAX = _env_int("NODEGLOW_SYSLOG_BUFFER_MAX", 50_000)  # hard cap — drop oldest
_FLUSH_INTERVAL = _env_float("NODEGLOW_SYSLOG_FLUSH_INTERVAL", 1.0)  # seconds
_flush_event: Optional[asyncio.Event] = None  # set while the flusher task runs

# UDP datagrams are parsed in the protocol callback and handed to a bounded
# queue drained by a worker, instead of one unbounded task per datagram — a
# burst could otherwise create hundreds of thousands of pending tasks.
_UDP_QUEUE_MAX = _env_int("NODEGLOW_SYSLOG_UDP_QUEUE", 10_000)
_udp_queue: Optional[asyncio.Queue] = None


def _trim_buffer_locked() -> None:
    """Drop the oldest messages beyond the hard cap. Caller holds the lock."""
    global _buffer
    if len(_buffer) > _BUFFER_MAX:
        dropped = len(_buffer) - _BUFFER_MAX
        _buffer = _buffer[dropped:]
        _count_drop("buffer_overflow", dropped)
        log.warning("Syslog buffer overflow: dropped %d oldest messages", dropped)


async def _enqueue(parsed: dict):
    # Run through intelligence pipeline (template extraction + tagging)
    try:
        from services.log_intelligence import process_message
        enrichment = process_message(parsed.get("message", ""), parsed.get("severity"))
        parsed["template_hash"] = enrichment["template_hash"]
        parsed["tags"] = enrichment["tags"]
        parsed["noise_score"] = enrichment["noise_score"]
        parsed["is_new_template"] = enrichment["is_new_template"]
        parsed["extracted_fields"] = enrichment.get("extracted_fields", {})
    except Exception:
        log.debug("Log intelligence enrichment failed", exc_info=True)

    # GeoIP enrichment (resolve external IPs in message to country/city)
    try:
        from services.geoip import enrich_message
        geo = enrich_message(parsed.get("message", ""))
        parsed["geo_country"] = geo["geo_country"]
        parsed["geo_city"] = geo["geo_city"]
    except Exception:
        log.debug("GeoIP enrichment failed", exc_info=True)

    # Ingest time is when we received it, not when the batch happens to flush.
    parsed.setdefault("received_at", datetime.utcnow())

    # Broadcast to live tail subscribers
    for q in _subscribers[:]:
        try:
            q.put_nowait(parsed)
        except asyncio.QueueFull:
            pass
    async with _buffer_lock:
        _buffer.append(parsed)
        _trim_buffer_locked()
        full = len(_buffer) >= _BUFFER_SIZE
    if full:
        if _flush_event is not None:
            _flush_event.set()  # wake the flusher; never wait for ClickHouse here
        else:
            # No flusher running (server not started, e.g. agent log ingest
            # in isolation): write inline, still outside the buffer lock.
            await _flush_buffer()


async def _flush_buffer():
    """Write buffered messages to ClickHouse.

    Takes the whole buffer under the lock, then inserts without holding it, so
    receivers keep appending while the insert is in flight. Inserts are
    serialised by _insert_lock. Must NOT be called with _buffer_lock held.
    """
    global _buffer
    async with _insert_lock:
        async with _buffer_lock:
            if not _buffer:
                return
            batch = _buffer
            _buffer = []
        await _write_batch(batch)


def _clean_rows(batch: list[dict]) -> list[dict]:
    _transient = {"is_new_template", "severity_label"}
    cleaned = []
    for msg in batch:
        row = {k: v for k, v in msg.items() if k not in _transient}
        if isinstance(row.get("tags"), list):
            row["tags"] = ",".join(row["tags"]) if row["tags"] else ""
        row.setdefault("tags", "")
        row.setdefault("template_hash", "")
        row.setdefault("noise_score", 50)
        row.setdefault("received_at", datetime.utcnow())
        # Ensure extracted_fields is a dict
        if not isinstance(row.get("extracted_fields"), dict):
            row["extracted_fields"] = {}
        cleaned.append(row)
    return cleaned


async def _write_batch(batch: list[dict]) -> None:
    global _buffer
    cleaned = _clean_rows(batch)
    try:
        from services.clickhouse_client import insert_batch
        await insert_batch(cleaned)
    except Exception as e:
        log.error("Failed to flush syslog buffer to ClickHouse (%d msgs): %s", len(cleaned), e)
        # Re-prepend the failed batch so the next flush retries it instead of
        # losing it on a transient ClickHouse error. Respect the hard cap by
        # dropping the oldest messages if the combined buffer would overflow.
        async with _buffer_lock:
            _buffer = batch + _buffer
            _trim_buffer_locked()


async def _flush_loop():
    """Flush when a batch is full or every _FLUSH_INTERVAL, whichever first."""
    event = _flush_event
    while True:
        if event is not None:
            try:
                await asyncio.wait_for(event.wait(), timeout=_FLUSH_INTERVAL)
            except asyncio.TimeoutError:
                pass
            event.clear()
        else:
            await asyncio.sleep(_FLUSH_INTERVAL)
        try:
            await _flush_buffer()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.error("Syslog flush loop error", exc_info=True)


async def _udp_worker():
    """Drain the UDP queue into the enrichment + buffer pipeline."""
    queue = _udp_queue
    while True:
        parsed = await queue.get()
        try:
            await _enqueue(parsed)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.debug("Syslog UDP enqueue failed", exc_info=True)
        finally:
            queue.task_done()


# ── Protocol handlers ───────────────────────────────────────────────────────

class SyslogUDPProtocol(asyncio.DatagramProtocol):
    def datagram_received(self, data: bytes, addr: tuple):
        if len(data) > _MAX_MSG_SIZE:
            return  # drop oversized message
        source_ip = addr[0]
        if not _is_allowed_source(source_ip):
            return
        if not _syslog_rate_ok(source_ip):
            return
        if not _global_rate_ok():
            return
        try:
            raw = data.decode("utf-8", errors="replace")
        except Exception:
            return
        parsed = parse_syslog(raw, source_ip)
        if not parsed:
            return
        parsed["host_id"] = _resolve_host_id(source_ip, parsed.get("hostname"))
        parsed["received_at"] = datetime.utcnow()
        queue = _udp_queue
        if queue is None:
            # Not started through start_syslog_server (tests, tooling).
            asyncio.ensure_future(_enqueue(parsed))
            return
        try:
            queue.put_nowait(parsed)
        except asyncio.QueueFull:
            _count_drop("queue_full")


class SyslogTCPHandler:
    """Handle one TCP connection (one message per line)."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer

    async def handle(self):
        global _tcp_connection_count
        addr = self.writer.get_extra_info("peername")
        source_ip = addr[0] if addr else "0.0.0.0"
        if not _is_allowed_source(source_ip):
            self.writer.close()
            return
        async with _tcp_count_lock:
            if _tcp_connection_count >= _MAX_TCP_CONNECTIONS:
                log.warning("Syslog TCP connection limit reached (%d), rejecting %s",
                            _MAX_TCP_CONNECTIONS, source_ip)
                self.writer.close()
                return
            _tcp_connection_count += 1
        try:
            while True:
                line = await asyncio.wait_for(
                    self.reader.readline(), timeout=60
                )
                if not line:
                    break
                if len(line) > _MAX_MSG_SIZE:
                    continue  # drop oversized message
                raw = line.decode("utf-8", errors="replace").strip()
                if not raw:
                    continue
                if not _syslog_rate_ok(source_ip):
                    continue
                if not _global_rate_ok():
                    continue
                parsed = parse_syslog(raw, source_ip)
                if not parsed:
                    continue
                parsed["host_id"] = _resolve_host_id(source_ip, parsed.get("hostname"))
                await _enqueue(parsed)
        except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError):
            pass
        finally:
            async with _tcp_count_lock:
                _tcp_connection_count -= 1
            self.writer.close()


# ── Server lifecycle ────────────────────────────────────────────────────────

_udp_transport = None
_tcp_server = None
_flush_task = None
_cache_task = None
_rdns_task = None
_udp_task = None


async def _cache_refresh_loop():
    """Periodically refresh the host cache."""
    while True:
        await _refresh_host_cache()
        await asyncio.sleep(_HOST_CACHE_TTL)


async def start_syslog_server(udp_port: int = 1514, tcp_port: int = 1514):
    """Start UDP + TCP syslog listeners."""
    # _rdns_task used to be assigned without being declared global, so
    # stop_syslog_server never saw (or cancelled) the rDNS loop.
    global _udp_transport, _tcp_server, _flush_task, _cache_task, _rdns_task
    global _udp_task, _udp_queue, _flush_event

    loop = asyncio.get_running_loop()

    # Initial host cache load (also populates _allowlist_only from DB setting).
    await _refresh_host_cache()

    if not _allowlist_only:
        log.warning(
            "Syslog host allowlist is DISABLED — every UDP/TCP source on "
            "port %d/%d can inject log entries and trigger host auto-assignment. "
            "Enable 'syslog_allowlist_only' in Settings → General for "
            "production/internet-exposed deployments.",
            udp_port, tcp_port,
        )

    # Load log intelligence template cache
    try:
        from services.log_intelligence import load_template_cache
        async with AsyncSessionLocal() as db:
            await load_template_cache(db)
    except Exception as e:
        log.warning("Failed to load template cache: %s", e)

    # Pipeline primitives first, so no datagram arrives before they exist.
    _flush_event = asyncio.Event()
    _udp_queue = asyncio.Queue(maxsize=_UDP_QUEUE_MAX)
    _flush_task = asyncio.create_task(_flush_loop())
    _udp_task = asyncio.create_task(_udp_worker())

    # UDP
    _udp_transport, _ = await loop.create_datagram_endpoint(
        SyslogUDPProtocol, local_addr=("0.0.0.0", udp_port)
    )
    log.info("Syslog UDP listening on port %d", udp_port)

    # TCP
    async def _tcp_client_connected(reader, writer):
        handler = SyslogTCPHandler(reader, writer)
        await handler.handle()

    _tcp_server = await asyncio.start_server(
        _tcp_client_connected, "0.0.0.0", tcp_port
    )
    log.info("Syslog TCP listening on port %d", tcp_port)

    # Background tasks
    _cache_task = asyncio.create_task(_cache_refresh_loop())
    _rdns_task = asyncio.create_task(_rdns_resolve_loop())


async def stop_syslog_server():
    """Stop syslog listeners and flush remaining buffer."""
    global _udp_transport, _tcp_server, _flush_task, _cache_task, _rdns_task
    global _udp_task, _udp_queue, _flush_event

    if _udp_transport:
        _udp_transport.close()
    if _tcp_server:
        _tcp_server.close()
        await _tcp_server.wait_closed()
    for task in (_flush_task, _cache_task, _rdns_task, _udp_task):
        if task:
            task.cancel()

    # Messages still queued from UDP go into the buffer before the last flush.
    queue, _udp_queue = _udp_queue, None
    _flush_event = None
    if queue is not None:
        while not queue.empty():
            try:
                await _enqueue(queue.get_nowait())
            except Exception:
                log.debug("Dropping queued syslog message on shutdown", exc_info=True)

    # Final flush
    await _flush_buffer()

    log.info("Syslog server stopped")
