"""
Log Intelligence Engine – template extraction, baseline learning, noise scoring,
auto-tagging, precursor detection, and burst detection.  Pure Python, no ML libraries.

Architecture:
- Template extraction runs on every incoming syslog message (in-memory, fast)
- Burst detection tracks per-template rate in a sliding 5-min window
- Baseline computation + precursor detection run periodically (scheduler)
- Noise scores are updated periodically based on template frequency patterns
"""
import hashlib
import logging
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, bindparam, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.log_template import HostBaseline, LogTemplate, PrecursorPattern
from services._stats import wilson_lower_bound
from services.clickhouse_client import query as ch_query
from services.predictor_config import get_blacklist_regexes, is_template_blacklisted

log = logging.getLogger("nodeglow.intelligence")

# ── Drain-lite: Template Extraction ───────────────────────────────────────────

# Patterns to replace with wildcards (order matters: more specific first)
_VARIABLE_PATTERNS = [
    # UUIDs
    (re.compile(r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b'), '<UUID>'),
    # Email addresses
    (re.compile(r'\b[\w.+-]+@[\w.-]+\.\w{2,}\b'), '<EMAIL>'),
    # URLs (http/https)
    (re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+'), '<URL>'),
    # Docker container IDs (12 or 64 hex chars)
    (re.compile(r'\b[0-9a-f]{64}\b'), '<CONTAINER_ID>'),
    (re.compile(r'\b[0-9a-f]{12}\b'), '<SHORT_ID>'),
    # MAC addresses
    (re.compile(r'\b[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}\b'), '<MAC>'),
    # IPv6 (simplified)
    (re.compile(r'\b[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4}){7}\b'), '<IPv6>'),
    # IPv4
    (re.compile(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b'), '<IP>'),
    # ISO timestamps
    (re.compile(r'\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[\.\d]*[Z+\-\d:]*\b'), '<TS>'),
    # Date-like patterns
    (re.compile(r'\b\d{4}[-/]\d{2}[-/]\d{2}\b'), '<DATE>'),
    # Time-like patterns (HH:MM:SS)
    (re.compile(r'\b\d{2}:\d{2}:\d{2}\b'), '<TIME>'),
    # Hex strings (8+ chars)
    (re.compile(r'\b0x[0-9a-fA-F]{4,}\b'), '<HEX>'),
    (re.compile(r'\b[0-9a-fA-F]{8,}\b'), '<HEX>'),
    # File paths
    (re.compile(r'(?:/[\w.\-]+){2,}'), '<PATH>'),
    # Quoted strings (often variable content in logs)
    (re.compile(r"'[^']{2,60}'"), "'<*>'"),
    (re.compile(r'"[^"]{2,60}"'), '"<*>"'),
    # Usernames after common prefixes
    (re.compile(r'(?<=user[= ])\S+'), '<USER>'),
    (re.compile(r'(?<=for user )\S+'), '<USER>'),
    (re.compile(r'(?<=from user )\S+'), '<USER>'),
    # Port-like numbers after specific keywords (before the generic number
    # rule, which would otherwise swallow them first)
    (re.compile(r'(?<=port\s)\d+\b'), '<PORT>'),
    (re.compile(r'(?<=pid\s)\d+\b'), '<PID>'),
    (re.compile(r'(?<=pid=)\d+\b'), '<PID>'),
    # Numbers with a unit glued on: "5ms", "1.5s", "512MB", "30sec"
    (re.compile(
        r'\b\d+(?:\.\d+)?(?=(?:ns|us|ms|s|sec|secs|min|mins|h|hrs|d|'
        r'b|kb|mb|gb|tb|kib|mib|gib|tib|bps|kbps|mbps|gbps)\b)',
        re.IGNORECASE,
    ), '<NUM>'),
    # Any standalone number, including 1-2 digits: counters, durations,
    # retry attempts, percentages and the like vary per message, and leaving
    # them in split one message into a new template per value — most of the
    # 305k seen-once templates in production. \b keeps digits that are part of
    # an identifier ("eth0", "vlan10", "sda1", "ipv6") untouched, since there
    # is no word boundary between a letter and a digit.
    (re.compile(r'\b\d+(?:\.\d+)?\b'), '<NUM>'),
]


def extract_template(message: str) -> tuple[str, str]:
    """
    Extract a template from a log message using Drain-lite algorithm.
    Returns (template_string, template_hash).
    """
    if not message:
        return ("", hashlib.md5(b"").hexdigest()[:16])

    tpl = message
    for pattern, replacement in _VARIABLE_PATTERNS:
        tpl = pattern.sub(replacement, tpl)

    # Collapse repeated wildcards
    tpl = re.sub(r'(<\w+>)(\s*\1)+', r'\1', tpl)

    # Normalize whitespace
    tpl = ' '.join(tpl.split())

    h = hashlib.md5(tpl.encode()).hexdigest()[:16]
    return tpl, h


# ── Auto-Tagging ─────────────────────────────────────────────────────────────

_TAG_RULES = [
    # (tag, compiled_regex_pattern)
    ("security", re.compile(
        r'(?i)\b(failed\s+password|unauthorized|denied|authentication|'
        r'invalid\s+user|brute.?force|attack|intrusion|forbidden|'
        r'login\s+failed|access.?denied|permission|firewall|'
        r'blocked|malware|virus|exploit|scan|vulnerability|'
        r'injection|overflow|escalation|privilege)\b'
    )),
    ("hardware", re.compile(
        r'(?i)\b(disk|memory|temperature|temp|fan|sensor|cpu|'
        r'hardware|smart|i/?o\s+error|ecc|parity|thermal|voltage|power|'
        r'battery|ups|overclock|overheat|dimm|bios|uefi|pci|usb)\b'
    )),
    ("network", re.compile(
        r'(?i)\b(link\s+down|link\s+up|unreachable|timeout|connection\s+refused|'
        r'dns|dhcp|arp|route|interface|packet|dropped|retransmit|'
        r'network|carrier|negotiat|duplex|mtu|latency|bandwidth|'
        r'vlan|bridge|bond|lacp|spanning.?tree|bgp|ospf|vpn|wireguard|'
        r'tcp\s+reset|connection\s+closed|port\s+unreachable)\b'
    )),
    ("storage", re.compile(
        r'(?i)\b(zfs|zpool|raid|mdadm|lvm|mount|unmount|filesystem|'
        r'quota|inode|scrub|resilver|snapshot|backup|nfs|smb|iscsi|'
        r'ceph|btrfs|ext4|xfs|disk\s+full|no\s+space|trim|defrag)\b'
    )),
    ("service", re.compile(
        r'(?i)\b(started|stopped|restart|crashed|exited|failed|'
        r'systemd|service|unit|docker|container|supervisor|'
        r'enabling|disabling|loaded|activated|'
        r'oom.?kill|segfault|core\s+dump|signal|sigterm|sigkill)\b'
    )),
    ("update", re.compile(
        r'(?i)\b(upgrade|update|patch|install|dpkg|apt|yum|rpm|'
        r'package|version|firmware|release)\b'
    )),
    ("auth", re.compile(
        r'(?i)\b(login|logout|session|pam|sudo|su\b|ssh|'
        r'accepted\s+key|publickey|certificate|token|oauth|'
        r'ldap|radius|kerberos|saml|mfa|2fa|totp)\b'
    )),
    ("database", re.compile(
        r'(?i)\b(postgres|mysql|mariadb|sqlite|mongodb|redis|'
        r'query|transaction|deadlock|slow\s+query|replication|'
        r'connection\s+pool|vacuum|checkpoint|wal|tablespace)\b'
    )),
    ("web", re.compile(
        r'(?i)\b(nginx|apache|httpd|haproxy|traefik|caddy|'
        r'GET|POST|PUT|DELETE|status\s+[45]\d\d|'
        r'upstream|proxy|ssl|tls|handshake|cert\s+expir|'
        r'rate.?limit|throttl|cors|redirect)\b'
    )),
    ("cron", re.compile(
        r'(?i)\b(cron|anacron|at\b|scheduled|timer|'
        r'CMD\s*\(|CRON\[)\b'
    )),
    ("kernel", re.compile(
        r'(?i)\b(kernel|dmesg|panic|oops|bug:|call\s+trace|'
        r'out\s+of\s+memory|oom|segmentation|page\s+fault|'
        r'nmi|watchdog|hung_task|soft\s+lockup)\b'
    )),
]


# ── Structured Field Extraction ──────────────────────────────────────────────

_KV_RE = re.compile(r'(\w[\w.-]*)=((?:"[^"]*"|\S+))')
_JSON_RE = re.compile(r'\{[^{}]{5,}\}')
_CEF_RE = re.compile(
    r"CEF:\d+\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|(.*)"
)

_MAX_FIELDS = 20
_MAX_VALUE_LEN = 256


def extract_structured_fields(message: str) -> dict[str, str]:
    """Extract key=value pairs, embedded JSON, and CEF fields from a message.
    Returns a dict of field name → value (max 20 fields, 256 chars per value)."""
    fields: dict[str, str] = {}
    if not message:
        return fields

    # CEF format
    cef = _CEF_RE.match(message)
    if cef:
        fields["cef_vendor"] = cef.group(1)[:_MAX_VALUE_LEN]
        fields["cef_product"] = cef.group(2)[:_MAX_VALUE_LEN]
        fields["cef_event"] = cef.group(5)[:_MAX_VALUE_LEN]
        for m in _KV_RE.finditer(cef.group(7)):
            if len(fields) >= _MAX_FIELDS:
                break
            fields[m.group(1)] = m.group(2).strip('"')[:_MAX_VALUE_LEN]
        return fields

    # Embedded JSON
    for jm in _JSON_RE.finditer(message):
        try:
            import json as _json
            obj = _json.loads(jm.group())
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if len(fields) >= _MAX_FIELDS:
                        break
                    if isinstance(v, (str, int, float, bool)):
                        fields[str(k)] = str(v)[:_MAX_VALUE_LEN]
        except (ValueError, TypeError):
            pass

    # key=value pairs
    for m in _KV_RE.finditer(message):
        if len(fields) >= _MAX_FIELDS:
            break
        key, val = m.group(1), m.group(2).strip('"')
        if len(key) > 2 and not key.isdigit():
            fields[key] = val[:_MAX_VALUE_LEN]

    return fields


def auto_tag(message: str) -> list[str]:
    """Return auto-detected tags for a message."""
    tags = []
    for tag, pattern in _TAG_RULES:
        if pattern.search(message):
            tags.append(tag)
    return tags


# ── Noise Score Calculation ───────────────────────────────────────────────────

def compute_noise_score(
    count: int,
    hours_active: float,
    first_seen: datetime,
    severity: Optional[int] = None,
    tags: Optional[list[str]] = None,
    is_precursor: bool = False,
    avg_severity: Optional[float] = None,
) -> int:
    """
    Compute noise score 0-100 (0 = very interesting, 100 = total noise).

    Factors:
    - High frequency + consistent rate = noise
    - Low severity (info/debug) = more likely noise
    - Security/hardware/kernel tags = less likely noise
    - Recently first seen = interesting
    - Precursor templates = never noise
    - Average severity of the template matters
    """
    score = 50  # neutral start

    # Frequency factor: >100/hour sustained = very noisy
    rate = count / max(hours_active, 0.1)
    if rate > 100:
        score += 30
    elif rate > 50:
        score += 20
    elif rate > 10:
        score += 10
    elif rate < 1:
        score -= 10  # rare = interesting

    # Severity factor
    if severity is not None:
        if severity <= 2:  # emergency/alert/critical
            score -= 30
        elif severity == 3:  # error
            score -= 15
        elif severity == 4:  # warning
            score -= 5
        elif severity >= 6:  # info/debug
            score += 10

    # Tag factor — more categories now contribute
    if tags:
        if "security" in tags or "hardware" in tags or "kernel" in tags:
            score -= 15
        if "database" in tags:
            score -= 10
        if "service" in tags and "started" not in str(tags):
            score -= 5
        if "cron" in tags:
            score += 5  # cron output is usually expected noise
        # Multi-tag bonus: messages with 3+ tags are usually significant
        if len(tags) >= 3:
            score -= 10

    # Novelty factor: first seen < 24h ago
    age_hours = (datetime.utcnow() - first_seen).total_seconds() / 3600
    if age_hours < 1:
        score -= 25  # brand new = very interesting
    elif age_hours < 24:
        score -= 10

    # Precursor bonus
    if is_precursor:
        score -= 30

    return max(0, min(100, score))


# ── In-Memory Template Cache (for fast per-message extraction) ────────────────

_template_cache: dict[str, int] = {}  # hash -> template_id
_template_counts: dict[str, int] = defaultdict(int)  # hash -> count since last flush
_new_templates: dict[str, tuple[str, str, list[str]]] = {}  # hash -> (template, example, tags)
_FLUSH_INTERVAL = 30  # seconds
_last_flush: float = 0.0

# ── Burst Detection (sliding 5-min window per template) ───────────────────────
_BURST_WINDOW = 300  # 5 minutes
_BURST_THRESHOLD = 50  # messages in 5 min to count as burst
_burst_timestamps: dict[str, deque] = defaultdict(lambda: deque(maxlen=200))
_active_bursts: set[str] = set()  # hashes currently in burst state


_burst_last_cleanup: float = 0.0
_BURST_CLEANUP_INTERVAL = 600  # prune stale entries every 10 min

def _check_burst(h: str, now: float) -> bool:
    """Track template occurrence and detect bursts (>50 msgs in 5 min)."""
    global _burst_last_cleanup
    dq = _burst_timestamps[h]
    dq.append(now)
    # Evict old entries outside window
    while dq and dq[0] < now - _BURST_WINDOW:
        dq.popleft()
    count = len(dq)
    if count >= _BURST_THRESHOLD:
        if h not in _active_bursts:
            _active_bursts.add(h)
            return True  # new burst detected
    elif h in _active_bursts and count < _BURST_THRESHOLD // 2:
        _active_bursts.discard(h)  # burst ended
    # Periodic cleanup: remove hashes with no recent activity
    if now - _burst_last_cleanup > _BURST_CLEANUP_INTERVAL:
        _burst_last_cleanup = now
        cutoff = now - _BURST_WINDOW
        stale = [k for k, v in _burst_timestamps.items() if not v or v[-1] < cutoff]
        for k in stale:
            del _burst_timestamps[k]
            _active_bursts.discard(k)
    return False


def get_active_bursts() -> list[dict]:
    """Return currently active burst templates for display."""
    now = time.time()
    bursts = []
    for h in list(_active_bursts):
        dq = _burst_timestamps.get(h)
        if not dq:
            continue
        count = sum(1 for t in dq if t >= now - _BURST_WINDOW)
        if count < _BURST_THRESHOLD // 2:
            _active_bursts.discard(h)
            continue
        bursts.append({"template_hash": h, "count_5m": count, "rate_per_min": round(count / 5, 1)})
    return bursts


def process_message(message: str, severity: Optional[int] = None) -> dict:
    """
    Process a single message through the intelligence pipeline.
    Called for every incoming syslog message (must be fast).

    Returns enrichment dict: {template_hash, tags, is_new_template, noise_score, is_burst}
    """
    template, h = extract_template(message)
    tags = auto_tag(message)

    is_new = h not in _template_cache and h not in _new_templates

    _template_counts[h] += 1

    if is_new:
        _new_templates[h] = (template, message, tags)

    # Burst detection (sliding window)
    now = time.time()
    new_burst = _check_burst(h, now)
    is_burst = h in _active_bursts

    # Rough noise estimate for immediate use (refined later by periodic job)
    noise = 50
    if is_new:
        noise = 10  # new templates are interesting
    elif is_burst:
        noise = 85  # bursting templates are noise
    elif h in _template_cache:
        count = _template_counts.get(h, 0)
        if count > 100:
            noise = 80

    # Severity-aware adjustment (fast path)
    if severity is not None and severity <= 3:
        noise = max(0, noise - 20)  # errors are never pure noise

    # Structured field extraction (fast regex, ~5μs)
    extracted = extract_structured_fields(message)

    return {
        "template_hash": h,
        "tags": tags,
        "is_new_template": is_new,
        "noise_score": noise,
        "is_burst": is_burst,
        "extracted_fields": extracted,
    }


# ── Periodic DB Flush (called by scheduler or flush loop) ─────────────────────

async def flush_templates(db: AsyncSession):
    """Flush accumulated template counts and new templates to DB."""
    global _template_counts, _new_templates

    if not _template_counts and not _new_templates:
        return

    counts = dict(_template_counts)
    new_tpls = dict(_new_templates)
    _template_counts = defaultdict(int)
    _new_templates = {}

    try:
        await _write_template_batch(db, counts, new_tpls, datetime.utcnow())
        await db.commit()
    except Exception:
        # Hand the batch back so a transient database error delays these
        # counts instead of losing them.
        for h, c in counts.items():
            _template_counts[h] += c
        for h, v in new_tpls.items():
            _new_templates.setdefault(h, v)
        raise


async def _write_template_batch(
    db: AsyncSession,
    counts: dict[str, int],
    new_tpls: dict[str, tuple[str, str, list[str]]],
    now: datetime,
) -> None:
    """Persist one flush worth of template counts in a handful of statements.

    The old path issued one SELECT (and maybe an INSERT) per new template and
    one UPDATE per seen template — tens of thousands of round trips every 30 s
    on a busy fleet. Now:

    * new templates: one multi-row INSERT ... ON CONFLICT (template_hash)
      DO UPDATE per chunk, adding the count and keeping the later last_seen,
      with RETURNING to fill the id cache. A hash another writer (or an earlier
      run) already inserted just has its count added — no lookup needed.
    * known templates: one executemany UPDATE per chunk.
    """
    if new_tpls:
        insert, dialect = _dialect_insert(db)
        greatest = func.greatest if dialect == "postgresql" else func.max
        rows = [
            {
                "template_hash": h,
                "template": template,
                "example": example,
                "count": counts.get(h, 1),
                "first_seen": now,
                "last_seen": now,
                "tags": ",".join(tags)[:256],
                "noise_score": 10,  # new = interesting
                "avg_rate_per_hour": 0.0,
                "trend_direction": "stable",
                "trend_score": 0.0,
            }
            for h, (template, example, tags) in new_tpls.items()
        ]
        inserted = 0
        for chunk in _chunks(rows, _UPSERT_CHUNK):
            stmt = insert(LogTemplate).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=["template_hash"],
                set_={
                    "count": LogTemplate.count + stmt.excluded.count,
                    "last_seen": greatest(LogTemplate.last_seen, stmt.excluded.last_seen),
                },
            ).returning(LogTemplate.id, LogTemplate.template_hash, LogTemplate.first_seen)
            for tpl_id, tpl_hash, first_seen in (await db.execute(stmt)).all():
                _template_cache[tpl_hash] = tpl_id
                if first_seen == now:
                    inserted += 1
                    template, _, tags = new_tpls[tpl_hash]
                    log.debug("New log template: %s (tags: %s)", template[:80], ",".join(tags) or "none")
        if inserted:
            log.info("Stored %d new log template(s)", inserted)

    known = [(h, c) for h, c in counts.items() if h not in new_tpls]
    if known:
        tbl = LogTemplate.__table__
        stmt = (
            update(tbl)
            .where(tbl.c.template_hash == bindparam("b_hash"))
            .values(count=tbl.c.count + bindparam("b_count"), last_seen=bindparam("b_now"))
        )
        for chunk in _chunks(known, _UPSERT_CHUNK):
            await db.execute(
                stmt, [{"b_hash": h, "b_count": c, "b_now": now} for h, c in chunk]
            )


async def load_template_cache(db: AsyncSession):
    """Load all template hashes into memory cache on startup."""
    global _template_cache
    rows = (await db.execute(select(LogTemplate.template_hash, LogTemplate.id))).all()
    _template_cache = {row.template_hash: row.id for row in rows}
    log.info("Loaded %d log templates into cache", len(_template_cache))


# ── Baseline Computation (periodic) ──────────────────────────────────────────
#
# One HostBaseline row per (host_key, hour_of_day, day_of_week) holds a running
# estimate of how many messages a source sends in that hour of the day.
#
# * day_of_week 0-6 is the weekday slot. It gets one sample a week.
# * day_of_week BASELINE_ANY_DAY (7) is an hour-of-day slot shared by every
#   day. It gets seven samples a week, so it is usable after three days instead
#   of three weeks, and serves as the fallback while the weekday slot is young.
#
# The old implementation recomputed everything every 30 s from a 7-day GROUP BY
# over raw syslog, keyed by (dow, hour) — which yields at most one or two
# samples per slot. Every consumer requires sample_count >= 3, so none of the
# volume or content anomaly rules could ever fire.
#
# Now each completed hour is folded in exactly once (progress is persisted in
# the settings table, so a restart neither skips nor double-counts an hour)
# with an exponentially weighted mean and variance: the first samples form a
# plain running mean (weight 1/n, identical to Welford), later ones weigh
# BASELINE_ALPHA_MIN, so the estimate follows slow drift and a decommissioned
# source fades out instead of alerting "silent" forever.
#
# Hours are bucketed on received_at (ingest time). A device whose clock runs
# behind would otherwise deliver its messages into an hour that was already
# folded in, and its baseline would read as zero.

BASELINE_ANY_DAY = 7
BASELINE_MIN_SAMPLES = 3
BASELINE_ALPHA_MIN = 0.2
# On first run (or after downtime) at most this many completed hours are folded
# in. Short on purpose: the syslog TTL already removes debug/noisy rows after
# one to two days, so older hours would be undercounted.
BASELINE_BACKFILL_HOURS = 24
# An hour is "completed" this long after it ends, so the syslog write buffer
# has flushed its last messages.
BASELINE_SETTLE_SECONDS = 120
BASELINE_STATE_KEY = "log_baseline_done_through"
_UPSERT_CHUNK = 1000

# In-memory mirror of the persisted progress, so the 30 s tick costs nothing
# until the next hour completes.
_baseline_done_through: Optional[datetime] = None


def ewma_update(
    mean: float, var: float, n: int, x: float, alpha_min: float = BASELINE_ALPHA_MIN,
) -> tuple[float, float, int]:
    """Fold sample ``x`` into an exponentially weighted (mean, variance, n).

    Weight is 1/n until that falls below ``alpha_min`` — exact running mean and
    population variance for the first samples — then constant.
    """
    n += 1
    a = max(1.0 / n, alpha_min)
    diff = x - mean
    mean = mean + a * diff
    var = (1.0 - a) * (var + a * diff * diff)
    return mean, var, n


def _floor_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def baseline_hours_due(done_through: Optional[datetime], now: datetime) -> list[datetime]:
    """Start times of the completed hours not yet folded into the baselines."""
    latest = _floor_hour(now - timedelta(seconds=BASELINE_SETTLE_SECONDS)) - timedelta(hours=1)
    earliest = latest - timedelta(hours=BASELINE_BACKFILL_HOURS - 1)
    if done_through is None:
        start = earliest
    else:
        start = max(done_through + timedelta(hours=1), earliest)
    hours: list[datetime] = []
    h = start
    while h <= latest:
        hours.append(h)
        h += timedelta(hours=1)
    return hours


def _dialect_insert(db: AsyncSession):
    """Return (insert construct with ON CONFLICT support, dialect name)."""
    name = db.get_bind().dialect.name
    if name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:  # pragma: no cover - only Postgres (prod) and SQLite (tests) exist
        raise NotImplementedError(f"upsert not supported on {name}")
    return insert, name


def _chunks(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


async def _upsert_baselines(db: AsyncSession, values: list[dict]) -> None:
    """Bulk INSERT ... ON CONFLICT (host_key, hour_of_day, day_of_week) DO UPDATE.

    Only the rate statistics are overwritten on conflict; the template
    diversity columns belong to compute_template_diversity.
    """
    if not values:
        return
    insert, _ = _dialect_insert(db)
    for chunk in _chunks(values, _UPSERT_CHUNK):
        stmt = insert(HostBaseline).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=["host_key", "hour_of_day", "day_of_week"],
            set_={
                "avg_rate": stmt.excluded.avg_rate,
                "std_rate": stmt.excluded.std_rate,
                "sample_count": stmt.excluded.sample_count,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        await db.execute(stmt)


async def _load_baseline_progress(db: AsyncSession) -> Optional[datetime]:
    from models.settings import get_setting

    raw = await get_setting(db, BASELINE_STATE_KEY, "")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


async def compute_baselines(db: AsyncSession, now: Optional[datetime] = None) -> int:
    """Fold every completed, not yet processed hour into the volume baselines.

    Cheap no-op until an hour completes. Returns the number of hours folded in.
    Uses source_ip as host_key.
    """
    global _baseline_done_through
    now = now or datetime.utcnow()
    if _baseline_done_through is not None and not baseline_hours_due(_baseline_done_through, now):
        return 0

    # Re-read the persisted progress whenever there is work: another instance
    # may have been leader in between.
    done_through = await _load_baseline_progress(db)
    hours = baseline_hours_due(done_through, now)
    if not hours:
        if done_through is not None:
            _baseline_done_through = done_through
        return 0

    from services import clickhouse_client as ch

    start, end = hours[0], hours[-1] + timedelta(hours=1)
    since_sql, params = ch.received_since_clause(start, param="start")
    params["end"] = end
    ch_rows = await ch.query(
        f"""SELECT source_ip,
                   toUnixTimestamp(toStartOfHour(received_at)) AS hour_ts,
                   count() AS cnt
            FROM syslog_messages
            WHERE {since_sql}
              AND received_at < {{end:DateTime64(3)}}
            GROUP BY source_ip, hour_ts""",
        params,
    )

    counts: dict[datetime, dict[str, int]] = defaultdict(dict)
    first_hour: dict[str, datetime] = {}
    for r in ch_rows:
        ip = r.get("source_ip")
        if not ip:
            continue
        h = datetime.fromtimestamp(int(r["hour_ts"]), tz=timezone.utc).replace(tzinfo=None)
        counts[h][ip] = counts[h].get(ip, 0) + int(r["cnt"])
        if ip not in first_hour or h < first_hour[ip]:
            first_hour[ip] = h

    if done_through is None:
        # First run of the incremental scheme: whatever the rows hold was
        # computed by the old full-recompute algorithm and is not a running
        # estimate. Start the rate statistics over; keep template diversity.
        await db.execute(
            update(HostBaseline).values(avg_rate=0.0, std_rate=0.0, sample_count=0)
        )

    # Sources that already have a baseline get a sample every hour — zero when
    # they were silent, which is what makes silence detectable. New sources
    # start at the first hour they were seen.
    known = {
        k for (k,) in (await db.execute(select(HostBaseline.host_key).distinct())).all()
        if k and not k.startswith("host:")
    }

    written = 0
    for hour in hours:
        hour_counts = counts.get(hour, {})
        sources = sorted(known | {ip for ip, fh in first_hour.items() if fh <= hour})
        if not sources:
            continue
        dow, hod = hour.weekday(), hour.hour
        existing = {
            (r.host_key, r.day_of_week): r
            for r in (await db.execute(
                select(
                    HostBaseline.host_key, HostBaseline.day_of_week,
                    HostBaseline.avg_rate, HostBaseline.std_rate, HostBaseline.sample_count,
                ).where(
                    HostBaseline.hour_of_day == hod,
                    HostBaseline.day_of_week.in_([dow, BASELINE_ANY_DAY]),
                )
            )).all()
        }
        values: list[dict] = []
        for ip in sources:
            x = float(hour_counts.get(ip, 0))
            for slot in (dow, BASELINE_ANY_DAY):
                prev = existing.get((ip, slot))
                if prev is not None:
                    state = (prev.avg_rate or 0.0, (prev.std_rate or 0.0) ** 2, prev.sample_count or 0)
                else:
                    state = (0.0, 0.0, 0)
                mean, var, n = ewma_update(*state, x)
                values.append({
                    "host_key": ip,
                    "hour_of_day": hod,
                    "day_of_week": slot,
                    "avg_rate": mean,
                    "std_rate": var ** 0.5,
                    "sample_count": n,
                    "avg_template_count": 0.0,
                    "std_template_count": 0.0,
                    "updated_at": now,
                })
        await _upsert_baselines(db, values)
        written += len(values)
        known.update(sources)

    from models.settings import set_setting

    # set_setting commits: the baseline rows and the progress marker land in
    # the same transaction, so an hour is never folded in twice.
    await set_setting(db, BASELINE_STATE_KEY, hours[-1].isoformat())
    _baseline_done_through = hours[-1]
    log.info(
        "Baselines: folded %d hour(s) up to %s into %d slot rows",
        len(hours), hours[-1].isoformat(), written,
    )
    return len(hours)


def pick_baseline(
    weekday_slot: Optional[HostBaseline],
    any_day_slot: Optional[HostBaseline],
    min_samples: int = BASELINE_MIN_SAMPLES,
) -> Optional[HostBaseline]:
    """Prefer the weekday-specific slot; fall back to the hour-of-day slot."""
    for b in (weekday_slot, any_day_slot):
        if b is not None and (b.sample_count or 0) >= min_samples:
            return b
    return None


async def load_effective_baselines(
    db: AsyncSession,
    now: Optional[datetime] = None,
    require_templates: bool = False,
) -> dict[str, HostBaseline]:
    """{host_key: usable baseline for the current hour}, one query.

    ``require_templates`` additionally requires learned template diversity
    (avg_template_count > 0) on the chosen slot.
    """
    now = now or datetime.utcnow()
    dow = now.weekday()
    rows = (await db.execute(
        select(HostBaseline).where(
            HostBaseline.hour_of_day == now.hour,
            HostBaseline.day_of_week.in_([dow, BASELINE_ANY_DAY]),
        )
    )).scalars().all()
    by_key: dict[str, dict[int, HostBaseline]] = defaultdict(dict)
    for b in rows:
        if require_templates and not (b.avg_template_count or 0) > 0:
            continue
        by_key[b.host_key][b.day_of_week] = b
    out: dict[str, HostBaseline] = {}
    for key, slots in by_key.items():
        chosen = pick_baseline(slots.get(dow), slots.get(BASELINE_ANY_DAY))
        if chosen is not None:
            out[key] = chosen
    return out


async def detect_baseline_anomalies(db: AsyncSession) -> list[dict]:
    """
    Check current hour's message rate against learned baselines.
    Returns list of anomaly dicts.
    """
    from services import clickhouse_client as ch

    now = datetime.utcnow()
    window_start = _floor_hour(now)

    baseline_map = {
        k: b for k, b in (await load_effective_baselines(db, now)).items()
        if not k.startswith("host:")
    }
    if not baseline_map:
        return []

    current_counts = await ch.count_syslog_received_by_source(window_start)

    anomalies = []
    for source_ip, cnt in current_counts.items():
        baseline = baseline_map.get(source_ip)
        if not baseline:
            continue

        # z-score: how many std devs above normal
        if baseline.std_rate > 0:
            z = (cnt - baseline.avg_rate) / baseline.std_rate
        elif cnt > baseline.avg_rate * 3:
            z = 5.0  # no variance but way above average
        else:
            continue

        if z >= 3.0:  # 3 sigma = significant
            anomalies.append({
                "source_ip": source_ip,
                "current_count": cnt,
                "expected": round(baseline.avg_rate, 1),
                "z_score": round(z, 1),
                "type": "rate_spike",
            })

    # Also detect silence (host normally sends logs but now silent). This must
    # run even when nothing at all arrived this hour — that is the loudest
    # silence there is.
    minutes_elapsed = max(1, now.minute + now.second / 60)
    for host_key, baseline in baseline_map.items():
        if baseline.avg_rate > 10:
            current = current_counts.get(host_key, 0)
            projected_rate = current * (60.0 / minutes_elapsed)
            if projected_rate < baseline.avg_rate * 0.1:  # <10% of normal
                anomalies.append({
                    "source_ip": host_key,
                    "current_count": current,
                    "expected": round(baseline.avg_rate, 1),
                    "z_score": 0,
                    "type": "silent",
                })

    return anomalies


# ── Precursor Detection (periodic) ───────────────────────────────────────────

PRECURSOR_WINDOW = timedelta(minutes=5)
PRECURSOR_MSGS_PER_EVENT = 50
_PRECURSOR_WINDOWS_PER_QUERY = 100


def _naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _merge_precursor_windows(
    events: list[tuple[int, datetime]],
) -> dict[int, list[tuple[datetime, datetime]]]:
    """{host_id: [(start, end), ...]} — the events' look-back windows, merged.

    A host that is down for an hour yields one failed check (one event) per
    minute; their 5-minute windows overlap and collapse into one range.
    """
    by_host: dict[int, list[datetime]] = defaultdict(list)
    for hid, ts in events:
        by_host[int(hid or 0)].append(_naive_utc(ts))
    merged: dict[int, list[tuple[datetime, datetime]]] = {}
    for hid, stamps in by_host.items():
        stamps.sort()
        ranges: list[tuple[datetime, datetime]] = []
        for ts in stamps:
            start = ts - PRECURSOR_WINDOW
            if ranges and start <= ranges[-1][1]:
                ranges[-1] = (ranges[-1][0], max(ranges[-1][1], ts))
            else:
                ranges.append((start, ts))
        merged[hid] = ranges
    return merged


async def _fetch_precursor_messages(
    events: list[tuple[int, datetime]],
) -> dict[int, list[tuple[datetime, str]]]:
    """Candidate messages for all events, in a few queries instead of one each.

    Returns {host_id: [(timestamp, message), ...] sorted by time}; host_id 0
    holds fleet-wide candidates (any host). Merged windows are sent in chunks
    as OR-ed ranges. ``LIMIT n BY ... minute`` keeps at most n messages per
    host per minute, so every 5-minute event window still has at least as
    many candidates as the old per-event ``LIMIT 50`` — while a long outage
    cannot pull its whole syslog history.
    """
    windows = _merge_precursor_windows(events)
    out: dict[int, list[tuple[datetime, str]]] = defaultdict(list)

    host_windows = [(hid, s, e) for hid, rs in windows.items() if hid != 0 for s, e in rs]
    fleet_windows = [(0, s, e) for s, e in windows.get(0, [])]

    for fleet, todo in ((False, host_windows), (True, fleet_windows)):
        for chunk in _chunks(todo, _PRECURSOR_WINDOWS_PER_QUERY):
            params: dict = {}
            # A module constant, inlined: LIMIT BY wants a literal.
            per_minute = int(PRECURSOR_MSGS_PER_EVENT)
            ranges = []
            for i, (hid, start, end) in enumerate(chunk):
                params[f"s{i}"] = start
                params[f"e{i}"] = end
                cond = f"(timestamp >= {{s{i}:DateTime64(3)}} AND timestamp <= {{e{i}:DateTime64(3)}})"
                if not fleet:
                    # Fleet-wide events (host_id 0: integration failures,
                    # incidents) look across all syslog; only host events
                    # filter by host.
                    params[f"h{i}"] = hid
                    cond = f"(host_id = {{h{i}:Int32}} AND {cond})"
                ranges.append(cond)
            limit_by = "toStartOfMinute(timestamp)" if fleet else "host_id, toStartOfMinute(timestamp)"
            rows = await ch_query(
                f"""SELECT host_id, message, timestamp FROM syslog_messages
                    WHERE severity <= 4
                      AND ({' OR '.join(ranges)})
                    LIMIT {per_minute} BY {limit_by}""",
                params,
            )
            for r in rows:
                ts = r.get("timestamp")
                if not isinstance(ts, datetime):
                    continue
                key = 0 if fleet else int(r.get("host_id") or 0)
                out[key].append((_naive_utc(ts), r.get("message") or ""))

    for msgs in out.values():
        msgs.sort(key=lambda m: m[0])
    return out


async def _learn_precursors_for_event(
    db: AsyncSession, event_type: str,
    events: list[tuple[int, datetime]], now: datetime,
):
    """Learn which templates appeared before a specific event type.
    Measures actual lead times instead of using hardcoded values.

    host_id 0 means "not tied to a single host": integration failures and
    incidents are fleet-wide, so their precursors are looked for across all
    syslog rather than under a host that cannot exist.
    """
    from bisect import bisect_left, bisect_right

    template_before: dict[int, int] = defaultdict(int)
    template_lead_times: dict[int, list[float]] = defaultdict(list)
    total_events = 0

    candidates = await _fetch_precursor_messages(events)
    stamps = {hid: [m[0] for m in msgs] for hid, msgs in candidates.items()}
    hash_of: dict[str, str] = {}  # message -> template hash, computed once

    for host_id, event_ts in events:
        hid = int(host_id or 0)
        event_ts = _naive_utc(event_ts)
        msgs = candidates.get(hid)
        if not msgs:
            continue
        times = stamps[hid]
        lo = bisect_left(times, event_ts - PRECURSOR_WINDOW)
        hi = bisect_right(times, event_ts)
        window = msgs[lo:hi][:PRECURSOR_MSGS_PER_EVENT]
        if not window:
            continue

        total_events += 1
        seen_templates = set()
        for msg_ts, message in window:
            h = hash_of.get(message)
            if h is None:
                _, h = extract_template(message)
                hash_of[message] = h
            tpl_id = _template_cache.get(h)
            if tpl_id and tpl_id not in seen_templates:
                seen_templates.add(tpl_id)
                template_before[tpl_id] += 1
                # Measure actual lead time
                delta = (event_ts - msg_ts).total_seconds()
                if 0 < delta <= PRECURSOR_WINDOW.total_seconds():
                    template_lead_times[tpl_id].append(delta)

    if not total_events:
        return

    blacklist = await get_blacklist_regexes(db)
    # Build a lookup of tpl_id → template text for the templates we touched,
    # so the blacklist check costs one query, not one per template.
    tpl_ids = list(template_before.keys())
    if tpl_ids:
        tpl_rows = (await db.execute(
            select(LogTemplate.id, LogTemplate.template).where(LogTemplate.id.in_(tpl_ids))
        )).all()
        tpl_text_by_id = {r[0]: r[1] for r in tpl_rows}
        existing_by_tpl = {
            pp.template_id: pp for pp in (await db.execute(
                select(PrecursorPattern).where(
                    PrecursorPattern.template_id.in_(tpl_ids),
                    PrecursorPattern.precedes_event == event_type,
                )
            )).scalars().all()
        }
    else:
        tpl_text_by_id = {}
        existing_by_tpl = {}

    for tpl_id, before_count in template_before.items():
        tpl_text = tpl_text_by_id.get(tpl_id, "")
        if is_template_blacklisted(tpl_text, blacklist):
            continue  # noise template – never let it become a precursor

        confidence = wilson_lower_bound(before_count, total_events)
        if confidence < 0.3:
            continue

        # Compute actual lead time stats
        deltas = template_lead_times.get(tpl_id, [])
        if deltas:
            avg_lead = int(sum(deltas) / len(deltas))
            min_lead = int(min(deltas))
            max_lead = int(max(deltas))
        else:
            avg_lead = 150
            min_lead = 0
            max_lead = 300

        existing = existing_by_tpl.get(tpl_id)

        if existing:
            existing.confidence = confidence
            existing.occurrence_count = before_count
            existing.total_checked = total_events
            existing.avg_lead_time_sec = avg_lead
            existing.min_lead_time_sec = min_lead
            existing.max_lead_time_sec = max_lead
            existing.updated_at = now
        else:
            db.add(PrecursorPattern(
                template_id=tpl_id,
                precedes_event=event_type,
                confidence=confidence,
                avg_lead_time_sec=avg_lead,
                min_lead_time_sec=min_lead,
                max_lead_time_sec=max_lead,
                occurrence_count=before_count,
                total_checked=total_events,
                updated_at=now,
            ))

    log.info("Precursor analysis (%s): %d templates, %d events",
             event_type, len(template_before), total_events)


async def cleanup_precursor_patterns(db: AsyncSession):
    """Drop blacklisted patterns + re-project naive confidences onto Wilson.

    Runs every intelligence cycle.  Idempotent: re-projecting an already-Wilson
    value gives the same value back, and blacklisted rows are simply absent on
    subsequent runs.
    """
    blacklist = await get_blacklist_regexes(db)

    rows = (await db.execute(
        select(PrecursorPattern, LogTemplate.template)
        .join(LogTemplate, PrecursorPattern.template_id == LogTemplate.id)
    )).all()

    removed = 0
    rescored = 0
    for pp, tpl_text in rows:
        if is_template_blacklisted(tpl_text or "", blacklist):
            await db.delete(pp)
            removed += 1
            continue
        if pp.total_checked and pp.total_checked > 0:
            new_conf = wilson_lower_bound(pp.occurrence_count, pp.total_checked)
            if abs((pp.confidence or 0.0) - new_conf) > 1e-6:
                pp.confidence = new_conf
                rescored += 1
    if removed or rescored:
        log.info("Precursor cleanup: removed=%d, rescored=%d", removed, rescored)


async def learn_precursors(db: AsyncSession):
    """
    Analyze which log templates appeared in the 5-minute window before
    host-down events, integration failures, and incidents.
    Build confidence scores over time.
    """
    await cleanup_precursor_patterns(db)

    from services.clickhouse_client import query as ch_query

    now = datetime.utcnow()
    lookback = now - timedelta(days=7)

    # 1. Host-down events from ClickHouse
    down_rows = await ch_query(
        "SELECT host_id, timestamp FROM ping_checks "
        "WHERE success = 0 AND timestamp >= {since:DateTime64(3)} "
        "ORDER BY timestamp",
        {"since": lookback},
    )
    down_events = [(int(r["host_id"]), r["timestamp"]) for r in down_rows]

    if down_events:
        await _learn_precursors_for_event(db, "host_down", down_events, now)

    # 2. Integration failures
    try:
        from models.integration import Snapshot
        fail_snaps = (await db.execute(
            select(Snapshot.entity_id, Snapshot.timestamp)
            .where(
                Snapshot.ok == False,
                Snapshot.timestamp >= lookback,
            )
            .order_by(Snapshot.timestamp)
        )).all()
        if fail_snaps:
            # entity_id is the integration config id, not a host. These
            # failures are fleet-wide, so 0 asks for syslog across all hosts.
            integration_events = [(0, ts) for _, ts in fail_snaps]
            await _learn_precursors_for_event(db, "integration_fail", integration_events, now)
    except Exception as exc:
        log.debug("Integration precursor learning skipped: %s", exc)

    # 3. Incidents (learn what templates precede manually-confirmed incidents)
    try:
        from models.incident import Incident
        incidents = (await db.execute(
            select(Incident)
            .where(
                Incident.created_at >= lookback,
                Incident.status.in_(["resolved", "acknowledged"]),
            )
        )).scalars().all()
        if incidents:
            incident_events = [(0, i.created_at) for i in incidents]
            await _learn_precursors_for_event(db, "incident", incident_events, now)
    except Exception as exc:
        log.debug("Incident precursor learning skipped: %s", exc)

    await db.commit()


# ── Noise Score Refresh (periodic) ───────────────────────────────────────────

# Rates below this delta are treated as unchanged, so float jitter alone never
# triggers a write.
NOISE_RATE_EPSILON = 0.01
# Rows per executemany batch when writing changed scores back.
NOISE_UPDATE_CHUNK = 1000


async def refresh_noise_scores(db: AsyncSession) -> int:
    """Recalculate noise scores for all templates. Returns the rows written.

    Reads only the columns the score depends on rather than whole ORM objects,
    and writes back just the rows whose values actually changed. On a fleet with
    hundreds of thousands of templates, materialising every row as an ORM
    instance and flushing it back made this the single most expensive query
    path in the database.
    """
    now = datetime.utcnow()

    # Batch-load all precursor template IDs to avoid N+1 queries
    precursor_ids = set(
        row[0] for row in (await db.execute(
            select(PrecursorPattern.template_id).where(
                PrecursorPattern.confidence >= 0.3,
            )
        )).all()
    )

    rows = (await db.execute(
        select(
            LogTemplate.id,
            LogTemplate.count,
            LogTemplate.first_seen,
            LogTemplate.tags,
            LogTemplate.noise_score,
            LogTemplate.avg_rate_per_hour,
        )
    )).all()

    changed: list[dict] = []
    for tpl_id, count, first_seen, tags_raw, old_score, old_rate in rows:
        if first_seen is None:
            continue
        count = count or 0
        hours_active = max(0.1, (now - first_seen).total_seconds() / 3600)

        score = compute_noise_score(
            count=count,
            hours_active=hours_active,
            first_seen=first_seen,
            tags=tags_raw.split(",") if tags_raw else [],
            is_precursor=tpl_id in precursor_ids,
        )
        rate = count / hours_active

        if score == old_score and abs(rate - (old_rate or 0.0)) < NOISE_RATE_EPSILON:
            continue

        changed.append({"id": tpl_id, "noise_score": score, "avg_rate_per_hour": rate})

    for start in range(0, len(changed), NOISE_UPDATE_CHUNK):
        await db.execute(
            update(LogTemplate),
            changed[start:start + NOISE_UPDATE_CHUNK],
            # Nothing is loaded into the session, so there is no identity map to
            # keep in sync — skipping it is the point of the bulk path.
            execution_options={"synchronize_session": None},
        )

    await db.commit()
    log.info(
        "Noise scores refreshed: %d of %d templates changed", len(changed), len(rows)
    )
    return len(changed)


# ── Severity Trend Detection (periodic) ──────────────────────────────────────

TREND_MIN_POINTS = 4       # active hours in the window needed for a trend
TREND_MIN_TEMPLATE_COUNT = 5


def trend_from_counts(counts: list[int]) -> tuple[str, float]:
    """(direction, relative slope per hour) from consecutive hourly counts."""
    n = len(counts)
    xs = range(n)
    sum_x = sum(xs)
    sum_y = sum(counts)
    sum_xy = sum(x * y for x, y in zip(xs, counts))
    sum_xx = sum(x * x for x in xs)
    denom = n * sum_xx - sum_x * sum_x
    slope = (n * sum_xy - sum_x * sum_y) / denom if abs(denom) > 1e-10 else 0.0

    avg_count = sum_y / n if n else 1
    # Normalize slope relative to average (% change per hour)
    rel_slope = slope / max(avg_count, 1.0)
    if rel_slope > 0.05:
        return "rising", rel_slope
    if rel_slope < -0.05:
        return "falling", rel_slope
    return "stable", rel_slope


async def compute_severity_trends(db: AsyncSession):
    """Detect templates increasing in frequency or escalating severity.
    Updates trend_direction, trend_score, and severity_mode on LogTemplate.

    ClickHouse does the grouping and keeps only templates with enough active
    hours; Postgres is touched only for those hits. The old path loaded every
    template with count >= 5 as an ORM object and sent all their hashes back to
    ClickHouse in 1000-element IN-lists.
    """
    from services import clickhouse_client as ch

    rows = await ch.query(
        """SELECT template_hash,
                  groupArray((h, cnt, avg_sev)) AS points
           FROM (
               SELECT template_hash,
                      toStartOfHour(timestamp) AS h,
                      count() AS cnt,
                      avg(severity) AS avg_sev
               FROM syslog_messages
               WHERE timestamp >= now() - INTERVAL 48 HOUR
                 AND template_hash != ''
               GROUP BY template_hash, h
           )
           GROUP BY template_hash
           HAVING count() >= {min_points:UInt32}""",
        {"min_points": TREND_MIN_POINTS},
    )
    points_by_hash = {r["template_hash"]: r["points"] for r in rows if r.get("points")}

    # Only templates with enough history overall are trended (as before).
    eligible: dict[str, int] = {}
    hashes = list(points_by_hash.keys())
    for chunk in _chunks(hashes, 5000):
        for tpl_id, tpl_hash in (await db.execute(
            select(LogTemplate.id, LogTemplate.template_hash).where(
                LogTemplate.template_hash.in_(chunk),
                LogTemplate.count >= TREND_MIN_TEMPLATE_COUNT,
            )
        )).all():
            eligible[tpl_hash] = tpl_id

    changes: list[dict] = []
    for h, tpl_id in eligible.items():
        # groupArray gives no order guarantee; sort the hourly points.
        points = sorted(points_by_hash[h], key=lambda p: p[0])
        direction, rel_slope = trend_from_counts([int(p[1]) for p in points])
        sevs = [float(p[2]) for p in points if p[2] is not None]
        change = {
            "id": tpl_id,
            "trend_direction": direction,
            "trend_score": round(rel_slope, 4),
        }
        if sevs:
            change["severity_mode"] = round(sum(sevs) / len(sevs))
        changes.append(change)

    # A template that stopped appearing keeps whatever trend it last had —
    # including "rising", which made the severity_trend rule fire on it for as
    # long as it stayed quiet. Anything not trended this run is not rising.
    trended_ids = {c["id"] for c in changes}
    stale = [
        tpl_id for (tpl_id,) in (await db.execute(
            select(LogTemplate.id).where(LogTemplate.trend_direction != "stable")
        )).all()
        if tpl_id not in trended_ids
    ]
    changes.extend({"id": i, "trend_direction": "stable", "trend_score": 0.0} for i in stale)

    # Rows carry different key sets (severity_mode is optional); the ORM bulk
    # path groups them by keys itself.
    for chunk in _chunks(changes, NOISE_UPDATE_CHUNK):
        await db.execute(
            update(LogTemplate), chunk,
            execution_options={"synchronize_session": None},
        )

    await db.commit()
    if changes:
        log.info(
            "Severity trends computed for %d templates (%d reset to stable)",
            len(trended_ids), len(stale),
        )


# ── Template Diversity Per Host (periodic) ───────────────────────────────────

async def compute_template_diversity(db: AsyncSession):
    """Count distinct templates per host and update baselines."""
    from services.clickhouse_client import query as ch_query

    rows = await ch_query(
        """SELECT source_ip,
                  countDistinct(template_hash) AS diversity,
                  countDistinctIf(template_hash, severity <= 3) AS error_diversity
           FROM syslog_messages
           WHERE timestamp >= now() - INTERVAL 1 HOUR
             AND template_hash != ''
           GROUP BY source_ip""",
    )

    if not rows:
        return

    now = datetime.utcnow()
    hour = now.hour
    dow = now.weekday()

    # One query for every baseline in this hour/day slot rather than one per
    # host. The per-host lookup made this the second-heaviest index consumer in
    # the database, and the cost grew with fleet size for no reason: the rows
    # are all in the same slot. Both the weekday slot and the any-day slot are
    # updated, so content detection has the same fallback as volume detection.
    host_keys = [r["source_ip"] for r in rows]
    baselines = (await db.execute(
        select(HostBaseline).where(
            HostBaseline.host_key.in_(host_keys),
            HostBaseline.hour_of_day == hour,
            HostBaseline.day_of_week.in_([dow, BASELINE_ANY_DAY]),
        )
    )).scalars().all()
    by_host: dict[str, list[HostBaseline]] = defaultdict(list)
    for b in baselines:
        by_host[b.host_key].append(b)

    alpha = 0.3
    for r in rows:
        for baseline in by_host.get(r["source_ip"], ()):
            # Exponential moving average for template diversity
            baseline.avg_template_count = (
                alpha * r["diversity"] + (1 - alpha) * (baseline.avg_template_count or 0.0)
            )
            # Update std using Welford's online algorithm (simplified)
            diff = r["diversity"] - baseline.avg_template_count
            baseline.std_template_count = max(
                1.0, (1 - alpha) * (baseline.std_template_count or 0.0) + alpha * abs(diff)
            )

    await db.commit()
    log.info("Template diversity computed for %d hosts", len(rows))


# ── Cross-Host Correlation (fleet-wide issue detection) ──────────────────────

# Templates at or above this noise score are treated as benign chatter and
# never raise a fleet-wide incident, even when seen on many hosts at once.
FLEET_NOISE_MAX = 70


async def detect_fleet_patterns(db: AsyncSession) -> list[dict]:
    """Detect same template hash appearing on 3+ hosts simultaneously.

    Blacklisted templates and high-noise chatter (``noise_score >=
    FLEET_NOISE_MAX``) are skipped — vendor firmware noise that fires
    synchronously across a fleet (e.g. UniFi APs) is not an incident.
    """
    from services.clickhouse_client import query as ch_query
    from models.log_template import FleetPattern, LogTemplate

    rows = await ch_query(
        """SELECT template_hash,
                  countDistinct(source_ip) AS host_count,
                  groupArray(DISTINCT source_ip) AS hosts
           FROM syslog_messages
           WHERE timestamp >= now() - INTERVAL 10 MINUTE
             AND severity <= 4
             AND template_hash != ''
           GROUP BY template_hash
           HAVING host_count >= 3
           ORDER BY host_count DESC
           LIMIT 20""",
    )

    if not rows:
        return []

    # Batch-load template text + noise score for the candidate hashes so the
    # blacklist/noise gate costs one query, not one per template.
    candidate_hashes = [r["template_hash"] for r in rows if r.get("template_hash")]
    tpl_meta: dict[str, tuple[str, int]] = {}
    if candidate_hashes:
        meta_rows = (await db.execute(
            select(LogTemplate.template_hash, LogTemplate.template, LogTemplate.noise_score)
            .where(LogTemplate.template_hash.in_(candidate_hashes))
        )).all()
        tpl_meta = {h: (text or "", score if score is not None else 50) for h, text, score in meta_rows}

    blacklist = await get_blacklist_regexes(db)

    now = datetime.utcnow()
    fleet_issues = []

    for r in rows:
        th = r["template_hash"]
        host_count = r["host_count"]
        hosts = r["hosts"] if isinstance(r["hosts"], list) else []

        # Skip benign vendor chatter: blacklisted templates or high-noise
        # patterns never raise a fleet-wide incident. Unknown templates (no
        # LogTemplate row yet) are genuinely new and pass through.
        tpl_text, noise_score = tpl_meta.get(th, ("", 50))
        if is_template_blacklisted(tpl_text, blacklist) or noise_score >= FLEET_NOISE_MAX:
            continue

        # Check if this pattern is normally fleet-wide (baseline check)
        existing = (await db.execute(
            select(FleetPattern).where(
                FleetPattern.template_hash == th,
                FleetPattern.status == "active",
            )
        )).scalar_one_or_none()

        if existing:
            existing.host_count = host_count
            existing.source_ips = ",".join(hosts[:20])
            existing.last_checked = now
        else:
            # Check if this was fleet-wide yesterday (baseline)
            baseline_count = await ch_query(
                """SELECT countDistinct(source_ip) AS cnt
                   FROM syslog_messages
                   WHERE template_hash = {th:String}
                     AND timestamp >= now() - INTERVAL 25 HOUR
                     AND timestamp <= now() - INTERVAL 24 HOUR
                     AND severity <= 4""",
                {"th": th},
            )
            is_baseline = False
            if baseline_count and baseline_count[0]["cnt"] >= 3:
                is_baseline = True

            if not is_baseline:
                fp = FleetPattern(
                    template_hash=th,
                    host_count=host_count,
                    source_ips=",".join(hosts[:20]),
                    first_seen=now,
                    last_checked=now,
                    is_baseline=False,
                    status="active",
                )
                db.add(fp)
                fleet_issues.append({
                    "template_hash": th,
                    "host_count": host_count,
                    "hosts": hosts[:10],
                })

    # Auto-resolve old fleet patterns
    stale = (await db.execute(
        select(FleetPattern).where(
            FleetPattern.status == "active",
            FleetPattern.last_checked < now - timedelta(minutes=15),
        )
    )).scalars().all()
    for fp in stale:
        fp.status = "resolved"

    await db.commit()
    return fleet_issues


# ── Content-Based Anomalies ──────────────────────────────────────────────────

# A template that normally logs at info/debug (severity_mode >= this) and
# suddenly appears at error level or worse is a severity upgrade.
SEVERITY_UPGRADE_NORMAL_MIN = 5
SEVERITY_UPGRADE_MIN_COUNT = 3


async def detect_content_anomalies(db: AsyncSession) -> list[dict]:
    """Detect template diversity spikes on stable hosts and severity upgrades.

    ClickHouse aggregates first and Postgres is asked only about the hits.
    The old path loaded every template with severity_mode >= 5 from Postgres
    (a large share of the table) and shipped their hashes back to ClickHouse in
    1000-element IN-lists — hundreds of queries per run.
    """
    from services import clickhouse_client as ch

    now = datetime.utcnow()
    anomalies: list[dict] = []

    # ── Diversity spike on hosts whose template mix is normally stable ──
    baselines = await load_effective_baselines(db, now, require_templates=True)
    stable_hosts = {
        k: b for k, b in baselines.items()
        if not k.startswith("host:")
        and b.avg_template_count < 10 and b.std_template_count < 3
    }
    if stable_hosts:
        rows = await ch.query(
            """SELECT source_ip, uniqExact(template_hash) AS diversity
               FROM syslog_messages
               WHERE timestamp >= now() - INTERVAL 1 HOUR
                 AND source_ip IN ({ips:Array(String)})
                 AND template_hash != ''
               GROUP BY source_ip""",
            {"ips": list(stable_hosts.keys())},
        )
        for r in rows:
            sip = r["source_ip"]
            baseline = stable_hosts.get(sip)
            if not baseline:
                continue
            diversity = int(r["diversity"])
            threshold = baseline.avg_template_count + 3 * max(baseline.std_template_count, 1)
            if diversity > threshold and diversity > 5:
                anomalies.append({
                    "source_ip": sip,
                    "type": "template_diversity_spike",
                    "current": diversity,
                    "baseline": round(baseline.avg_template_count, 1),
                })

    # ── Severity upgrade: independent of host baselines ──
    # (It used to sit behind the baseline early-returns, so it could only run
    # once diversity baselines existed — which, with the old sample counts,
    # was never.)
    sev_rows = await ch.query(
        """SELECT template_hash, min(severity) AS min_sev, count() AS cnt
           FROM syslog_messages
           WHERE timestamp >= now() - INTERVAL 2 HOUR
             AND severity <= 3
             AND template_hash != ''
           GROUP BY template_hash
           HAVING cnt >= {min_cnt:UInt32}""",
        {"min_cnt": SEVERITY_UPGRADE_MIN_COUNT},
    )
    hits = {r["template_hash"]: r for r in sev_rows}
    if hits:
        tpl_rows = []
        for chunk in _chunks(list(hits.keys()), 5000):
            tpl_rows.extend((await db.execute(
                select(LogTemplate.template_hash, LogTemplate.template, LogTemplate.severity_mode)
                .where(
                    LogTemplate.template_hash.in_(chunk),
                    LogTemplate.severity_mode.isnot(None),
                    LogTemplate.severity_mode >= SEVERITY_UPGRADE_NORMAL_MIN,
                )
            )).all())
        for tpl_hash, tpl_text, sev_mode in tpl_rows:
            r = hits[tpl_hash]
            anomalies.append({
                "type": "severity_upgrade",
                "template_hash": tpl_hash,
                "template": (tpl_text or "")[:100],
                "normal_severity": sev_mode,
                "current_severity": r["min_sev"],
                "count": r["cnt"],
            })

    return anomalies


# ── Main periodic job (called by scheduler) ──────────────────────────────────

async def run_intelligence():
    """Fast path – flush buffered templates and refresh host baselines.

    Runs on the short scheduler tick. The template flush touches only the
    templates seen since the last run; the baseline pass is a no-op until an
    hour completes and then folds in just that hour. Each runs in its own
    session so a failure in one does not discard the other's work.
    """
    from models.base import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        try:
            await flush_templates(db)
        except Exception as e:
            log.error("Template flush error: %s", e, exc_info=True)
            await db.rollback()

    async with AsyncSessionLocal() as db:
        try:
            await compute_baselines(db)
        except Exception as e:
            log.error("Baseline computation error: %s", e, exc_info=True)
            await db.rollback()


async def run_analytics():
    """Slow path – fleet-wide passes that scan the entire template table.

    Each of these walks every known template, so on a large fleet they cost
    orders of magnitude more than the fast path and must not share its tick.
    Noise scores, trends and diversity are statistical aggregates; a coarser
    interval does not change what they tell an operator.
    """
    from models.base import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        try:
            await learn_precursors(db)
            await refresh_noise_scores(db)
            await compute_severity_trends(db)
            await compute_template_diversity(db)
        except Exception as e:
            log.error("Analytics engine error: %s", e, exc_info=True)
            await db.rollback()


# ── Template Retention ───────────────────────────────────────────────────────

DEFAULT_TEMPLATE_RETENTION_DAYS = 90
# A template seen exactly once and not again for this long was a one-off — in
# practice almost always a message whose variable part the extractor missed.
DEFAULT_SINGLETON_RETENTION_DAYS = 7


async def cleanup_log_templates(
    db: AsyncSession,
    retention_days: int,
    singleton_retention_days: int = DEFAULT_SINGLETON_RETENTION_DAYS,
) -> int:
    """Delete templates that have not been seen within the retention window.

    Without this the table only ever grows. Measured on production after five
    months: 372'414 templates, of which 305'861 were seen exactly once and
    288'298 had not been seen for over 30 days — roughly 1 GB of rows that no
    longer describe anything the system is observing.

    Templates seen only once are dropped much sooner
    (``singleton_retention_days``; 0 disables that rule): they are the bulk of
    the table and carry no statistics worth keeping.

    Templates referenced by a learned precursor pattern are kept regardless of
    age: they carry the predictor's history and are foreign-key referenced.
    A retention of 0 disables pruning entirely.
    """
    if retention_days <= 0:
        return 0

    now = datetime.utcnow()
    expired = LogTemplate.last_seen < now - timedelta(days=retention_days)
    if singleton_retention_days > 0:
        expired = or_(expired, and_(
            LogTemplate.count <= 1,
            LogTemplate.last_seen < now - timedelta(days=singleton_retention_days),
        ))

    protected = select(PrecursorPattern.template_id)
    result = await db.execute(
        delete(LogTemplate).where(expired, LogTemplate.id.notin_(protected))
    )
    deleted = result.rowcount or 0
    if deleted:
        log.info(
            "Pruned %d log templates (unseen for %d days, or seen once and not "
            "for %d days)", deleted, retention_days, singleton_retention_days,
        )
    return deleted
