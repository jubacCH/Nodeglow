"""Per-host options for HTTP(S) checks: parsing, validation, defaults.

Stored as JSON in ``ping_hosts.http_options``. Only values that differ from
the defaults are kept, and the defaults reproduce how HTTP checks behaved
before the options existed: GET the host's URL, follow redirects, do not
verify TLS (internal hosts often use self-signed certificates), and count any
status below 500 as up.

Keyword checks are plain case-insensitive substring matches on the first
256 kB of the body. Regular expressions are deliberately not offered: Python's
``re`` has no timeout and holds the GIL, so one catastrophic pattern would
stall every check in the process.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

METHODS = ("GET", "HEAD")
DEFAULT_TIMEOUT = 5.0
MIN_TIMEOUT = 1.0
MAX_TIMEOUT = 60.0
MAX_KEYWORD_LEN = 200
MAX_URL_LEN = 2048
MAX_STATUS_SPEC_LEN = 100
MAX_REDIRECTS = 5
BODY_LIMIT = 256 * 1024


@dataclass(frozen=True)
class HttpOptions:
    method: str = "GET"
    url: str | None = None              # "/path?q" or an absolute http(s) URL
    expected_status: str | None = None  # "200-299,301"
    keyword: str | None = None          # must appear in the body
    keyword_absent: str | None = None   # must not appear in the body
    timeout: float = DEFAULT_TIMEOUT
    follow_redirects: bool = True
    verify_tls: bool = False

    @property
    def status_ranges(self) -> list[tuple[int, int]] | None:
        return parse_status_spec(self.expected_status) if self.expected_status else None

    def to_dict(self) -> dict:
        """Every option with its effective value (for the API)."""
        return {
            "method": self.method, "url": self.url,
            "expected_status": self.expected_status,
            "keyword": self.keyword, "keyword_absent": self.keyword_absent,
            "timeout": self.timeout, "follow_redirects": self.follow_redirects,
            "verify_tls": self.verify_tls,
        }


DEFAULTS = HttpOptions()


def parse_status_spec(spec: str) -> list[tuple[int, int]]:
    """"200-299,301" → [(200, 299), (301, 301)]. Raises ValueError if malformed."""
    if len(spec) > MAX_STATUS_SPEC_LEN:
        raise ValueError("expected status list is too long")
    ranges: list[tuple[int, int]] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo_s, hi_s = (p.strip() for p in part.split("-", 1))
        else:
            lo_s = hi_s = part
        if not (lo_s.isdigit() and hi_s.isdigit()):
            raise ValueError(f"'{part}' is not a status code or range")
        lo, hi = int(lo_s), int(hi_s)
        if not (100 <= lo <= 599 and 100 <= hi <= 599) or lo > hi:
            raise ValueError(f"'{part}' is not a valid status range (100-599)")
        ranges.append((lo, hi))
    if not ranges:
        raise ValueError("expected status list is empty")
    if len(ranges) > 20:
        raise ValueError("at most 20 status codes or ranges")
    return ranges


def status_matches(code: int, ranges: list[tuple[int, int]] | None) -> bool:
    if ranges is None:
        return code < 500
    return any(lo <= code <= hi for lo, hi in ranges)


def _clean_str(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _as_bool(value, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false", "1", "0"):
        return value.lower() in ("true", "1")
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise ValueError(f"{field} must be true or false")


def normalize(raw) -> dict | None:
    """Validate user input and return the dict to store (None = all defaults).

    Raises ValueError with a message fit for the API response. The absolute
    URL's host is NOT checked against SSRF here (that needs DNS) — callers do
    it with :func:`absolute_url`.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("http_options must be an object")
    unknown = set(raw) - set(DEFAULTS.to_dict())
    if unknown:
        raise ValueError(f"unknown http option(s): {', '.join(sorted(unknown))}")

    out: dict = {}

    method = (_clean_str(raw.get("method")) or "GET").upper()
    if method not in METHODS:
        raise ValueError("method must be GET or HEAD")
    if method != DEFAULTS.method:
        out["method"] = method

    url = _clean_str(raw.get("url"))
    if url:
        if len(url) > MAX_URL_LEN:
            raise ValueError("url is too long")
        if any(c.isspace() for c in url) or any(ord(c) < 32 for c in url):
            raise ValueError("url must not contain whitespace or control characters")
        if url.startswith("/"):
            if url.startswith("//"):
                raise ValueError("path must start with a single '/'")
        else:
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https") or not parts.hostname:
                raise ValueError("url must be a path starting with '/' or an http(s):// URL")
            if parts.username or parts.password:
                raise ValueError("url must not contain credentials")
        out["url"] = url

    status = _clean_str(raw.get("expected_status"))
    if status:
        parse_status_spec(status)
        out["expected_status"] = ",".join(p.strip() for p in status.split(",") if p.strip())

    for key in ("keyword", "keyword_absent"):
        kw = raw.get(key)
        kw = None if kw is None else str(kw)
        if kw is not None and kw.strip() == "":
            kw = None
        if kw is not None:
            if len(kw) > MAX_KEYWORD_LEN:
                raise ValueError(f"{key} is longer than {MAX_KEYWORD_LEN} characters")
            out[key] = kw
    if method == "HEAD" and ("keyword" in out or "keyword_absent" in out):
        raise ValueError("keyword checks need GET — a HEAD response has no body")

    timeout = raw.get("timeout")
    if timeout not in (None, ""):
        try:
            timeout = float(timeout)
        except (TypeError, ValueError):
            raise ValueError("timeout must be a number of seconds") from None
        if not (MIN_TIMEOUT <= timeout <= MAX_TIMEOUT):
            raise ValueError(f"timeout must be between {MIN_TIMEOUT:g} and {MAX_TIMEOUT:g} seconds")
        if timeout != DEFAULTS.timeout:
            out["timeout"] = timeout

    for key in ("follow_redirects", "verify_tls"):
        if raw.get(key) is not None:
            val = _as_bool(raw[key], key)
            if val != getattr(DEFAULTS, key):
                out[key] = val

    return out or None


def load(stored: str | None) -> HttpOptions:
    """Options from the stored JSON. Anything unreadable falls back to defaults."""
    if not stored:
        return DEFAULTS
    try:
        data = json.loads(stored)
        clean = normalize(data) or {}
    except (ValueError, TypeError):
        return DEFAULTS
    return HttpOptions(**clean)


def dump(clean: dict | None) -> str | None:
    return json.dumps(clean, sort_keys=True) if clean else None


def of_host(host) -> HttpOptions:
    return load(getattr(host, "http_options", None))


def absolute_url(clean: dict | None) -> str | None:
    """The absolute URL option, if one is set (it needs an SSRF check)."""
    url = (clean or {}).get("url")
    return url if url and not url.startswith("/") else None


async def normalize_checked(raw) -> dict | None:
    """:func:`normalize` plus the SSRF check of an absolute URL option.

    The URL gets the same vetting as a host's own hostname. Raises ValueError.
    """
    clean = normalize(raw)
    url = absolute_url(clean)
    if url:
        from utils.net_safety import validate_host_async

        err = await validate_host_async(url)
        if err:
            raise ValueError(f"url: {err}")
    return clean


def build_url(base: str, opts: HttpOptions) -> str:
    """The URL to check: base (scheme://host[/...]) with the path option applied."""
    if not opts.url:
        return base
    if not opts.url.startswith("/"):
        return opts.url
    parts = urlsplit(base)
    path, _, query = opts.url.partition("?")
    return urlunsplit((parts.scheme, parts.netloc, path, query, ""))
