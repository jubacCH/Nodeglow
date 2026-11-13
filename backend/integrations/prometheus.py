"""Prometheus integration – generic /metrics scrape with alert rules.

Monitors anything that exposes the Prometheus text format or OpenMetrics
without a dedicated integration. The exposition is parsed with a small strict
parser (standard library only), evaluated against the user's alert rules and
reduced to a bounded snapshot: metric families with sample counts, the series
the rules matched and a short preview. The scrape itself is never stored — an
exporter can return 50k series.

Rule syntax, one per line::

    <severity> <selector> <op> <number> [# title]
    critical node_filesystem_avail_bytes{mountpoint="/"} < 1e9 # Root disk almost full
    warning up == 0

``severity`` is critical, warning or info. The selector is a metric name with
optional exact-match label matchers (``=`` and ``!=``). ``op`` is one of
``< <= > >= == !=``. A rule fires for every matching series whose value
satisfies the comparison. NaN satisfies no comparison. A rule whose selector
matches no series at all raises a warning: a rule that silently matches
nothing is exactly the failure this product exists to prevent.
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, NamedTuple
from urllib.parse import urlsplit

from utils.net_safety import safe_async_client

from integrations._base import Alert, BaseIntegration, CollectorResult, ConfigField

# ── Limits ───────────────────────────────────────────────────────────────────

MAX_RESPONSE_BYTES = 10 * 1024 * 1024
MAX_LINE_BYTES = 64 * 1024
FETCH_TIMEOUT_S = 30.0
FETCH_DEADLINE_S = 60.0
MAX_RULES = 100
MAX_RULES_TEXT = 32 * 1024
PREVIEW_SERIES = 200
MAX_FAMILIES = 500
MAX_ALERTS_PER_RULE = 50
SAMPLE_ROWS_PER_RULE = 20
# Hard cap on firing series kept per rule while scanning (the count stays exact).
FIRING_KEEP = 2000
# Stored rows: series text and label values are bounded (hash-suffixed when cut).
MAX_SERIES_TEXT = 512
MAX_LABEL_VALUE = 256

SEVERITIES = ("critical", "warning", "info")
METRIC_TYPES = frozenset({
    "counter", "gauge", "histogram", "summary", "untyped",
    "gaugehistogram", "info", "stateset", "unknown",
})

_NAME_RE = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*")
_LABEL_NAME_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
_VALUE_RE = re.compile(
    r"^[+-]?(?:(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?|inf(?:inity)?|nan)$", re.IGNORECASE)
_TS_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_WS = " \t"

# Sample-name suffixes that belong to a declared family, and the family types
# that may own them.
_SUFFIX_OWNERS = {
    "_bucket": {"histogram", "gaugehistogram"},
    "_sum": {"histogram", "summary", "gaugehistogram"},
    "_count": {"histogram", "summary", "gaugehistogram"},
    "_gsum": {"gaugehistogram"},
    "_gcount": {"gaugehistogram"},
    "_total": {"counter"},
    "_created": {"counter", "histogram", "summary"},
    "_info": {"info"},
}


class ExpositionError(ValueError):
    """The scrape is not valid Prometheus/OpenMetrics text."""


class RuleError(ValueError):
    """A rule line is malformed."""


# ── Text format parser ───────────────────────────────────────────────────────


class Sample(NamedTuple):
    name: str
    labels: dict
    value: float
    timestamp: float | None
    family: str


@dataclass
class Family:
    name: str
    type: str = "untyped"
    help: str = ""
    samples: int = 0


@dataclass
class Exposition:
    families: dict[str, Family] = field(default_factory=dict)
    samples: list[Sample] = field(default_factory=list)
    series: int = 0
    eof: bool = False


def _read_quoted(text: str, i: int, err: Callable[[str], Exception]) -> tuple[str, int]:
    """Read a double-quoted string starting at text[i] == '"'; returns (value, index after)."""
    assert text[i] == '"'
    i += 1
    out: list[str] = []
    n = len(text)
    while i < n:
        c = text[i]
        if c == '"':
            return "".join(out), i + 1
        if c == "\\":
            i += 1
            if i >= n:
                break
            esc = text[i]
            if esc == "n":
                out.append("\n")
            elif esc in ('"', "\\"):
                out.append(esc)
            else:
                raise err(f"invalid escape sequence '\\{esc}' in a quoted string")
        else:
            out.append(c)
        i += 1
    raise err("unterminated quoted string")


def _skip_ws(text: str, i: int) -> int:
    while i < len(text) and text[i] in _WS:
        i += 1
    return i


def _parse_labels(line: str, i: int, err: Callable[[str], Exception]) -> tuple[dict, int]:
    """Parse ``name="value",...}`` starting after the opening brace."""
    labels: dict[str, str] = {}
    n = len(line)
    while True:
        i = _skip_ws(line, i)
        if i >= n:
            raise err("unterminated label set")
        if line[i] == "}":
            return labels, i + 1
        m = _LABEL_NAME_RE.match(line, i)
        if not m:
            raise err(f"invalid label name at column {i + 1}")
        lname = m.group()
        i = _skip_ws(line, m.end())
        if i >= n or line[i] != "=":
            raise err(f"expected '=' after label '{lname}'")
        i = _skip_ws(line, i + 1)
        if i >= n or line[i] != '"':
            raise err(f"label '{lname}' value must be a quoted string")
        value, i = _read_quoted(line, i, err)
        if lname in labels:
            raise err(f"duplicate label '{lname}'")
        labels[lname] = value
        i = _skip_ws(line, i)
        if i < n and line[i] == ",":
            i += 1
        elif i < n and line[i] == "}":
            continue
        else:
            raise err("expected ',' or '}' in label set")


def parse_float(token: str) -> float:
    """Prometheus float: NaN, +Inf, -Inf, Inf and plain numbers (no '_' separators)."""
    if not _VALUE_RE.match(token):
        raise ValueError(f"invalid number {token!r}")
    return float(token)


def _family_of(name: str, families: dict[str, Family]) -> str:
    if name in families:
        return name
    for suffix, owners in _SUFFIX_OWNERS.items():
        if name.endswith(suffix):
            base = name[: -len(suffix)]
            fam = families.get(base)
            if fam is not None and fam.type in owners:
                return base
    return name


def parse_exposition(text: str, on_sample: Callable[[Sample], None] | None = None) -> Exposition:
    """Parse Prometheus text format / OpenMetrics, strictly.

    Any malformed line raises :class:`ExpositionError` with its line number:
    an HTML error page or a half-written scrape must fail loudly instead of
    yielding "no metrics". Histograms and summaries are plain samples
    (``_bucket``, ``_sum``, ``_count``) counted into their declared family.
    ``# EOF`` ends the exposition (anything but blank lines after it is an
    error). With ``on_sample`` the samples are streamed to the callback
    instead of being collected.
    """
    exp = Exposition()
    families = exp.families

    def family(name: str) -> Family:
        fam = families.get(name)
        if fam is None:
            fam = families[name] = Family(name)
        return fam

    # Not splitlines(): label values may hold U+2028 and friends, which only
    # str.splitlines treats as line breaks.
    for lineno, raw in enumerate(text.split("\n"), start=1):
        def err(msg: str, _n: int = lineno) -> ExpositionError:
            return ExpositionError(f"line {_n}: {msg}")

        line = raw.strip()
        if not line:
            continue
        if exp.eof:
            raise err("content after '# EOF'")
        if len(line) > MAX_LINE_BYTES:
            raise err("line is too long")

        if line[0] == "#":
            body = line[1:].strip()
            if body == "EOF":
                exp.eof = True
                continue
            parts = body.split(None, 2)
            if len(parts) >= 2 and parts[0] in ("HELP", "TYPE", "UNIT"):
                kind, mname = parts[0], parts[1]
                if not _NAME_RE.fullmatch(mname):
                    raise err(f"invalid metric name {mname!r} in # {kind}")
                rest = parts[2] if len(parts) > 2 else ""
                if kind == "TYPE":
                    mtype = rest.strip().lower()
                    if mtype not in METRIC_TYPES:
                        raise err(f"unknown metric type {rest.strip()!r}")
                    family(mname).type = mtype
                elif kind == "HELP":
                    family(mname).help = rest[:200]
                else:
                    family(mname)
            elif parts and parts[0] in ("HELP", "TYPE", "UNIT"):
                raise err(f"incomplete # {parts[0]} line")
            continue  # any other comment

        m = _NAME_RE.match(line)
        if not m:
            raise err("expected a metric name")
        name = m.group()
        i = m.end()
        labels: dict = {}
        if i < len(line) and line[i] == "{":
            labels, i = _parse_labels(line, i + 1, err)
        if i >= len(line) or line[i] not in _WS:
            raise err("expected whitespace and a value after the metric name")
        # An OpenMetrics exemplar ('# {...} v ts') follows the sample; ignore it.
        fields = line[i:].split("#", 1)[0].split()
        if not 1 <= len(fields) <= 2:
            raise err("expected a value and an optional timestamp")
        try:
            value = parse_float(fields[0])
        except ValueError as exc:
            raise err(str(exc)) from None
        ts: float | None = None
        if len(fields) == 2:
            if not _TS_RE.match(fields[1]):
                raise err(f"invalid timestamp {fields[1]!r}")
            ts = float(fields[1])

        fam_name = _family_of(name, families)
        family(fam_name).samples += 1
        exp.series += 1
        sample = Sample(name, labels, value, ts, fam_name)
        if on_sample is not None:
            on_sample(sample)
        else:
            exp.samples.append(sample)
    return exp


# ── Rules ────────────────────────────────────────────────────────────────────

OPS: dict[str, Callable[[float, float], bool]] = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}
_OP_TOKENS = ("<=", ">=", "==", "!=", "<", ">")


@dataclass(frozen=True)
class Rule:
    severity: str
    name: str
    matchers: tuple[tuple[str, str, str], ...]   # (label, "=" | "!=", value)
    op: str
    threshold: float
    threshold_text: str
    title: str
    line: int

    @property
    def expr(self) -> str:
        """Canonical, stable text of the rule (the rule's identity)."""
        sel = self.name
        if self.matchers:
            sel += "{" + ",".join(f"{k}{op}{_quote(v)}" for k, op, v in self.matchers) + "}"
        return f"{sel} {self.op} {self.threshold_text}"

    def matches(self, sample: Sample) -> bool:
        if sample.name != self.name:
            return False
        for label, op, value in self.matchers:
            present = sample.labels.get(label, "")  # absent label == empty value
            if (present == value) != (op == "="):
                return False
        return True


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _split_title(line: str) -> tuple[str, str]:
    """Split ``body # title`` at the first '#' outside a quoted string."""
    in_quote = False
    i = 0
    while i < len(line):
        c = line[i]
        if in_quote:
            if c == "\\":
                i += 1
            elif c == '"':
                in_quote = False
        elif c == '"':
            in_quote = True
        elif c == "#":
            return line[:i], line[i + 1:].strip()
        i += 1
    return line, ""


def parse_rule(line: str, lineno: int = 0) -> Rule:
    """Parse one rule line; raises :class:`RuleError` with a precise message."""
    def err(msg: str) -> RuleError:
        return RuleError(msg)

    body, title = _split_title(line)
    body = body.strip()
    m = re.match(r"(\S+)\s+", body)
    if not m:
        raise err("expected '<severity> <selector> <op> <number>'")
    severity = m.group(1).lower()
    if severity not in SEVERITIES:
        raise err(f"unknown severity {m.group(1)!r} (use critical, warning or info)")
    i = m.end()
    nm = _NAME_RE.match(body, i)
    if not nm:
        raise err("expected a metric name after the severity")
    name = nm.group()
    i = nm.end()
    matchers: list[tuple[str, str, str]] = []
    if i < len(body) and body[i] == "{":
        i += 1
        while True:
            i = _skip_ws(body, i)
            if i >= len(body):
                raise err("unterminated label matcher list")
            if body[i] == "}":
                i += 1
                break
            lm = _LABEL_NAME_RE.match(body, i)
            if not lm:
                raise err(f"invalid label name at column {i + 1}")
            i = _skip_ws(body, lm.end())
            if body.startswith(("=~", "!~"), i):
                raise err("regex matchers (=~, !~) are not supported; use = or !=")
            if body.startswith("!=", i):
                mop, i = "!=", i + 2
            elif body.startswith("=", i):
                mop, i = "=", i + 1
            else:
                raise err(f"expected '=' or '!=' after label '{lm.group()}'")
            i = _skip_ws(body, i)
            if i >= len(body) or body[i] != '"':
                raise err(f"label matcher '{lm.group()}' needs a quoted value")
            value, i = _read_quoted(body, i, err)
            matchers.append((lm.group(), mop, value))
            i = _skip_ws(body, i)
            if i < len(body) and body[i] == ",":
                i += 1
            elif i < len(body) and body[i] == "}":
                continue
            else:
                raise err("expected ',' or '}' in the label matcher list")
    i = _skip_ws(body, i)
    op = next((o for o in _OP_TOKENS if body.startswith(o, i)), None)
    if op is None:
        raise err("expected a comparison operator (< <= > >= == !=) after the selector")
    i = _skip_ws(body, i + len(op))
    number = body[i:].strip()
    if not number:
        raise err("missing number after the operator")
    if " " in number or "\t" in number:
        raise err(f"unexpected text after the number: {number!r}")
    try:
        threshold = parse_float(number)
    except ValueError:
        raise err(f"invalid number {number!r}") from None
    if math.isnan(threshold):
        raise err("NaN is not a valid threshold")
    return Rule(severity, name, tuple(matchers), op, threshold, number, title[:200], lineno)


def parse_rules(text: str) -> tuple[list[Rule], list[dict]]:
    """All rules of the ``rules`` textarea plus per-line errors.

    Blank lines and lines starting with ``#`` are skipped. Errors are dicts
    ``{"line", "text", "error"}``; an identical rule twice is an error too
    (both would share one incident key).
    """
    rules: list[Rule] = []
    errors: list[dict] = []
    seen: dict[str, int] = {}
    text = str(text or "")
    if len(text) > MAX_RULES_TEXT:
        return [], [{"line": 0, "text": "", "error": f"rules text exceeds {MAX_RULES_TEXT // 1024} KB"}]
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            if len(rules) >= MAX_RULES:
                raise RuleError(f"too many rules (limit {MAX_RULES})")
            rule = parse_rule(line, lineno)
            if rule.expr in seen:
                raise RuleError(f"duplicate of the rule on line {seen[rule.expr]}")
            seen[rule.expr] = lineno
            rules.append(rule)
        except RuleError as exc:
            errors.append({"line": lineno, "text": line[:200], "error": str(exc)})
    return rules, errors


# ── Evaluation ───────────────────────────────────────────────────────────────


def encode_value(v: float) -> float | str:
    """JSON-safe sample value: NaN and infinities become strings."""
    if math.isnan(v):
        return "NaN"
    if math.isinf(v):
        return "+Inf" if v > 0 else "-Inf"
    return v


def series_id(name: str, labels: dict) -> str:
    if not labels:
        return name
    return name + "{" + ",".join(f"{k}={_quote(str(v))}" for k, v in sorted(labels.items())) + "}"


def _bounded(text: str, limit: int) -> str:
    """``text`` cut to about ``limit`` characters; the tail is a hash of the full
    text so distinct long values stay distinct (series ids are incident keys)."""
    if len(text) <= limit:
        return text
    return text[: limit - 12] + "~" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


def _row(sample: Sample) -> dict:
    # Lines may be 64 KiB; hundreds of stored rows must not each carry that.
    return {"series": _bounded(series_id(sample.name, sample.labels), MAX_SERIES_TEXT), "name": sample.name,
            "labels": {k: _bounded(str(v), MAX_LABEL_VALUE) for k, v in sample.labels.items()},
            "value": encode_value(sample.value)}


class _RuleState:
    def __init__(self, rule: Rule):
        self.rule = rule
        self.matched = 0
        # Series of the rule's metric, whatever their labels. Tells a rule
        # whose metric is missing (a typo: it can never fire) from one whose
        # label set simply has no series yet (`errors_total{status="error"}`
        # before the first error: normal).
        self.name_seen = 0
        self.nan = 0
        self.firing = 0
        self.firing_rows: list[dict] = []
        self.sample_rows: list[dict] = []

    def feed(self, sample: Sample) -> None:
        self.matched += 1
        if len(self.sample_rows) < SAMPLE_ROWS_PER_RULE:
            self.sample_rows.append(_row(sample))
        if math.isnan(sample.value):
            self.nan += 1
            return
        if OPS[self.rule.op](sample.value, self.rule.threshold):
            self.firing += 1
            if len(self.firing_rows) < FIRING_KEEP:
                self.firing_rows.append(_row(sample))

    def result(self) -> dict:
        # Sorted by series so the alerted subset is stable across scrapes.
        firing = sorted(self.firing_rows, key=lambda r: r["series"])[:MAX_ALERTS_PER_RULE]
        r = self.rule
        return {
            "line": r.line, "severity": r.severity, "expr": r.expr, "title": r.title,
            "matched": self.matched, "name_seen": self.name_seen,
            "nan": self.nan, "firing": self.firing,
            "firing_series": firing, "sample_series": self.sample_rows,
        }


def evaluate_scrape(text: str, rules: list[Rule]) -> dict:
    """Parse ``text`` and reduce it to the bounded snapshot parts.

    Returns ``{"series", "families", "families_truncated", "preview", "rules", "eof"}``.
    Raises :class:`ExpositionError` on malformed input.
    """
    states = [_RuleState(r) for r in rules]
    by_name: dict[str, list[_RuleState]] = {}
    for st in states:
        by_name.setdefault(st.rule.name, []).append(st)
    preview: list[dict] = []

    def on_sample(s: Sample) -> None:
        if len(preview) < PREVIEW_SERIES:
            preview.append(_row(s))
        for st in by_name.get(s.name, ()):
            st.name_seen += 1
            if st.rule.matches(s):
                st.feed(s)

    exp = parse_exposition(text, on_sample)
    fams = sorted(exp.families.values(), key=lambda f: f.name)
    fams = [f for f in fams if f.samples] + [f for f in fams if not f.samples]
    kept = fams[:MAX_FAMILIES]
    return {
        "series": exp.series,
        "families_total": len(fams),
        "families": [{"name": f.name, "type": f.type, "help": f.help, "samples": f.samples} for f in kept],
        "families_truncated": max(0, len(fams) - len(kept)),
        "preview": preview,
        "rules": [st.result() for st in states],
        "eof": exp.eof,
    }


# ── Config validation ────────────────────────────────────────────────────────


def validate_url(url: str) -> str | None:
    """Reason a metrics URL is unusable, or None."""
    try:
        parts = urlsplit((url or "").strip())
        hostname = parts.hostname
    except ValueError:
        return "Metrics URL is not a valid URL"
    if parts.scheme not in ("http", "https") or not hostname:
        return "Metrics URL must start with http:// or https://"
    if parts.username or parts.password:
        return "Put credentials into the username/password fields, not into the URL"
    return None


def config_problems(config: dict) -> list[str]:
    """Problems with the connection settings that need no network (empty = fine)."""
    problems: list[str] = []
    bad = validate_url(config.get("url") or "")
    if bad:
        problems.append(bad)
    if config.get("bearer_token") and (config.get("username") or config.get("password")):
        problems.append("Use either a bearer token or basic auth, not both")
    if bool(config.get("username")) != bool(config.get("password")):
        problems.append("Basic auth needs both username and password")
    return problems


def validate_config(config: dict) -> list[str]:
    """All problems of a config that can be found without a network (empty = fine).

    Includes malformed rule lines, so a bad rule can be rejected when the
    config is saved instead of surfacing at the first scrape.
    """
    problems = config_problems(config)
    _, errors = parse_rules(config.get("rules") or "")
    for e in errors:
        problems.append(f"Rule line {e['line']}: {e['error']}" if e["line"] else e["error"])
    return problems


# ── Client ───────────────────────────────────────────────────────────────────


def _redact(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


class PrometheusAPI:
    def __init__(self, url: str, bearer_token: str = "", username: str = "",
                 password: str = "", verify_ssl: bool = True):
        self.url = url.strip()
        self.bearer_token = bearer_token or ""
        self.username = username or ""
        self.password = password or ""
        self.verify_ssl = verify_ssl

    def _secrets(self) -> list[str]:
        return [self.url, self.bearer_token, self.password]

    async def _download(self) -> tuple[str, str, int]:
        """Fetch the body with a hard size cap; returns (text, content_type, bytes)."""
        headers = {"Accept": "application/openmetrics-text;version=1.0.0;q=0.9,"
                             "text/plain;version=0.0.4;q=0.5,*/*;q=0.1"}
        auth = None
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        elif self.username:
            auth = (self.username, self.password)
        async with safe_async_client(verify=self.verify_ssl, timeout=FETCH_TIMEOUT_S,
                                     follow_redirects=False) as client:
            async with client.stream("GET", self.url, headers=headers, auth=auth) as resp:
                if resp.status_code != 200:
                    hint = " (redirects are not followed; use the final URL)" if 300 <= resp.status_code < 400 else ""
                    raise RuntimeError(f"Exporter answered HTTP {resp.status_code}{hint}")
                chunks: list[bytes] = []
                size = 0
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise RuntimeError(
                            f"Response exceeds the {MAX_RESPONSE_BYTES // (1024 * 1024)} MB limit; "
                            "scrape a narrower endpoint or reduce the exporter's series")
                    chunks.append(chunk)
                ctype = resp.headers.get("content-type", "")
        try:
            text = b"".join(chunks).decode("utf-8")
        except UnicodeDecodeError:
            raise RuntimeError("Response is not valid UTF-8") from None
        return text, ctype, size

    async def fetch_all(self, rules: list[Rule]) -> dict:
        started = time.monotonic()
        try:
            text, ctype, size = await asyncio.wait_for(self._download(), timeout=FETCH_DEADLINE_S)
        except asyncio.TimeoutError:
            raise RuntimeError(f"Scrape did not finish within {int(FETCH_DEADLINE_S)} s") from None
        except RuntimeError as exc:
            raise RuntimeError(_redact(str(exc), self._secrets())) from None
        except Exception as exc:
            raise RuntimeError(_redact(f"{type(exc).__name__}: {exc}", self._secrets())) from None
        duration_ms = round((time.monotonic() - started) * 1000)
        return build_snapshot(text, ctype, size, duration_ms, rules)

    async def health_check(self) -> bool:
        try:
            async with safe_async_client(verify=self.verify_ssl, timeout=10.0) as client:
                headers = {"Authorization": f"Bearer {self.bearer_token}"} if self.bearer_token else {}
                auth = (self.username, self.password) if self.username and not self.bearer_token else None
                # Streamed and not read: only the status matters, the body may be huge.
                async with client.stream("GET", self.url, headers=headers, auth=auth) as resp:
                    return resp.status_code == 200
        except Exception:
            return False


def build_snapshot(text: str, content_type: str, size: int, duration_ms: int,
                   rules: list[Rule], rule_errors: list[dict] | None = None,
                   now: datetime | None = None) -> dict:
    """Parse and reduce a scrape to the stored snapshot.

    Raises ``RuntimeError`` for scrapes that must count as failures: not
    valid exposition text, an OpenMetrics body without ``# EOF`` (truncated),
    or no samples at all.
    """
    openmetrics = "openmetrics" in content_type.lower()
    try:
        parts = evaluate_scrape(text, rules)
    except ExpositionError as exc:
        raise RuntimeError(f"Not a valid Prometheus exposition: {exc}") from None
    if openmetrics and not parts["eof"]:
        raise RuntimeError("OpenMetrics response is truncated (no '# EOF' terminator)")
    if parts["series"] == 0:
        raise RuntimeError("The exporter returned no samples")
    now = now or datetime.now(timezone.utc)
    return {
        "collected_at": now.isoformat(),
        "scrape": {
            "bytes": size, "duration_ms": duration_ms,
            "format": "openmetrics" if openmetrics else "prometheus",
            "series": parts["series"], "families": parts["families_total"],
        },
        "families": parts["families"],
        "families_truncated": parts["families_truncated"],
        "preview": parts["preview"],
        "preview_truncated": parts["series"] > len(parts["preview"]),
        "rules": parts["rules"],
        "rule_errors": list(rule_errors or []),
        "unavailable": [],
    }


# ── Integration Plugin ───────────────────────────────────────────────────────


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v)


def _bounded_entity(text: str) -> str:
    """Entity keys stay readable and short; the tail is a hash of the full text."""
    if len(text) <= 180:
        return text
    return text[:160] + "~" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


class PrometheusIntegration(BaseIntegration):
    name = "prometheus"
    display_name = "Prometheus"
    icon = "prometheus"
    color = "orange"
    description = "Scrape any Prometheus or OpenMetrics /metrics endpoint and alert on rules you define."

    def validate_config(self, config: dict) -> list[str]:
        # The module-level function: connection settings and rule lines.
        return validate_config(config)

    config_fields = [
        ConfigField(key="url", label="Metrics URL", field_type="url",
                    placeholder="http://exporter.local:9100/metrics"),
        ConfigField(key="bearer_token", label="Bearer token", field_type="password",
                    encrypted=True, required=False),
        ConfigField(key="username", label="Basic auth user", field_type="text", required=False),
        ConfigField(key="password", label="Basic auth password", field_type="password",
                    encrypted=True, required=False),
        ConfigField(key="verify_ssl", label="Verify SSL", field_type="checkbox",
                    required=False, default=True),
        ConfigField(key="rules", label="Alert rules", field_type="textarea", required=False,
                    placeholder='critical node_filesystem_avail_bytes{mountpoint="/"} < 1e9 # Root disk almost full\n'
                                "warning up == 0"),
    ]

    def _api(self) -> PrometheusAPI:
        return PrometheusAPI(
            url=self.config.get("url") or "",
            bearer_token=self.config.get("bearer_token") or "",
            username=self.config.get("username") or "",
            password=self.config.get("password") or "",
            verify_ssl=self.config.get("verify_ssl", True),
        )

    async def collect(self) -> CollectorResult:
        try:
            # Malformed rules do not stop the scrape; they are reported in the snapshot.
            problems = config_problems(self.config)
            if problems:
                return CollectorResult(success=False, error="; ".join(problems))
            rules, rule_errors = parse_rules(self.config.get("rules") or "")
            data = await self._api().fetch_all(rules)
            data["rule_errors"] = rule_errors
            return CollectorResult(success=True, data=data)
        except Exception as exc:
            api = self._api()
            return CollectorResult(success=False, error=_redact(str(exc), api._secrets()))

    async def health_check(self) -> bool:
        return await self._api().health_check()

    def parse_alerts(self, data: dict) -> list[Alert]:
        alerts: list[Alert] = []
        if not isinstance(data, dict):
            return alerts

        for err in data.get("rule_errors") or []:
            if not isinstance(err, dict):
                continue
            alerts.append(Alert(
                severity="warning", title="Alert rule is malformed",
                detail=f"Line {err.get('line', '?')}: {err.get('error', 'invalid')} ({err.get('text', '')})",
                entity=_bounded_entity(f"rule error: line {err.get('line', '?')} {err.get('text', '')}"),
            ))

        for rule in data.get("rules") or []:
            if not isinstance(rule, dict) or not rule.get("expr"):
                continue
            expr = str(rule["expr"])
            severity = rule.get("severity") if rule.get("severity") in SEVERITIES else "warning"
            title = rule.get("title") or expr
            matched = rule.get("matched")
            if not isinstance(matched, int):
                continue  # no evaluation result: unknown, not an alert
            if matched == 0:
                name_seen = rule.get("name_seen")
                if isinstance(name_seen, int) and name_seen > 0:
                    # The metric is there, only no series carries these
                    # labels right now. Counters with an error label have
                    # no such series until the first error.
                    continue
                alerts.append(Alert(
                    severity="warning", title="Rule matches no series",
                    detail=f"'{expr}': the scrape has no metric of that name; the rule cannot "
                           "fire. Check the metric name.",
                    entity=_bounded_entity(f"{expr} :: no series"),
                ))
                continue
            nan = rule.get("nan")
            if isinstance(nan, int) and nan > 0:
                alerts.append(Alert(
                    severity="warning", title="Rule series report NaN",
                    detail=f"{nan} of {matched} series matched by '{expr}' report NaN and cannot be compared",
                    entity=_bounded_entity(f"{expr} :: NaN"),
                ))
            rows = [r for r in (rule.get("firing_series") or []) if isinstance(r, dict)]
            for row in rows[:MAX_ALERTS_PER_RULE]:
                alerts.append(Alert(
                    severity=severity, title=str(title),
                    detail=f"{row.get('series', '?')} = {_fmt(row.get('value'))} ({expr})",
                    entity=_bounded_entity(f"{expr} :: {row.get('series', '?')}"),
                ))
            firing = rule.get("firing")
            if isinstance(firing, int) and firing > MAX_ALERTS_PER_RULE:
                # The count lives in the detail: in the title (part of the incident
                # key) every change of it would open a new incident.
                alerts.append(Alert(
                    severity=severity, title=f"{title} (more series)",
                    detail=f"{firing} series match '{expr}' (+{firing - MAX_ALERTS_PER_RULE} more); "
                           f"only the first {MAX_ALERTS_PER_RULE} are listed individually",
                    entity=_bounded_entity(f"{expr} :: summary"),
                ))
        return alerts
