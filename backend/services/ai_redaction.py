"""Redact personal data and secrets from text before it is sent to an LLM.

Everything Nodeglow sends to an AI provider (infrastructure context, syslog
examples, incident timelines, the user's own chat messages and the chat
history) goes through one :class:`Redactor` per request. Each distinct value
is replaced by a stable placeholder (``<IP_1>``, ``<USER_2>``, ...): the same
value gets the same placeholder everywhere in that request, so the model can
still correlate "<IP_3> failed 40 logins" with "<IP_3> is in the blocklist".

The mapping placeholder -> original value never leaves the process. When the
answer comes back, :meth:`Redactor.restore` maps placeholders back so the
user sees real values inside Nodeglow. Secrets are the exception: they are
never stored in the reverse map and therefore never restored.

This is best effort, not a guarantee. It is pattern based: values in formats
it does not know (a username in free text, a customer name in an incident
title, a short hostname that is not a known Nodeglow host) pass through.
"""
from __future__ import annotations

import ipaddress
import re
from typing import Callable, Iterable

# Placeholder kinds. SECRET is never restored.
IP = "IP"
IPV6 = "IPV6"
MAC = "MAC"
EMAIL = "EMAIL"
USER = "USER"
HOST = "HOST"
SECRET = "SECRET"
SID = "SID"

_NO_RESTORE = {SECRET}

PLACEHOLDER_RE = re.compile(r"<(IP|IPV6|MAC|EMAIL|USER|HOST|SECRET|SID)_(\d+)>")

# Longest placeholder we emit is well below this; used by the stream restorer
# to decide whether a dangling "<" can still become a placeholder.
_MAX_PLACEHOLDER_LEN = 16

# Values that look like a username/host field but carry no identity.
_EMPTY_VALUES = {"-", "", "n/a", "na", "none", "null", "unknown", "(null)", "nobody"}
# Built-in / service accounts: not personal data, and the model reasons
# better seeing "root" or "admin" than "<USER_1>" (brute-force targets).
_GENERIC_ACCOUNTS = {
    "root", "admin", "administrator", "guest", "user", "test", "daemon", "bin", "sys",
    "www-data", "nginx", "apache", "postgres", "mysql", "oracle", "ubuntu", "debian",
    "pi", "ec2-user", "centos", "git", "ftp", "operator", "support", "system",
    "local service", "network service", "anonymous", "dwm-1", "umfd-0",
}
# Below this length a value is not replaced elsewhere in the request (too
# likely to hit ordinary words).
_MIN_PROPAGATE_LEN = 4

# ── Secrets ──────────────────────────────────────────────────────────────────

_SECRET_KEYS = (
    r"password|passwd|passphrase|pwd|pass|secret|client_secret|client-secret"
    r"|token|access_token|refresh_token|auth_token|api_key|api-key|apikey"
    r"|access_key|access-key|secret_key|private_key|sessionid|session_id"
    r"|authorization|auth|signature"
)
# key=value / key: value / "key": "value" — the value is replaced, the key kept.
_SECRET_KV_RE = re.compile(
    r"""(?ix)
    (?P<key>["']?\b(?:""" + _SECRET_KEYS + r""")\b["']?)
    (?P<sep>\s*[=:]\s*)
    (?P<q>["']?)
    (?P<val>(?!<[A-Z0-9]+_\d+>|(?:Bearer|Basic|Token)\b)[^\s"',;&)}\]]{1,512})
    """
)
_BEARER_RE = re.compile(r"(?i)\b(Bearer|Basic|Token)\s+([A-Za-z0-9._~+/=-]{8,})")
_URL_CREDS_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)([^:/\s@]+):([^@/\s]+)@")
_TOKEN_FORMATS = [
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{10,}"),              # Anthropic
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}"),         # OpenAI
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                      # AWS access key id
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),            # GitHub
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),            # Slack
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),  # JWT
    re.compile(r"\bng_[A-Za-z0-9_-]{20,}"),                   # Nodeglow API keys
]

# ── Addresses ────────────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"(?i)\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,63}\b")
_MAC_RE = re.compile(
    r"(?i)(?<![0-9a-f:.-])(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}(?![0-9a-f:.-])"
    r"|(?<![0-9a-f.])[0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4}(?![0-9a-f.])"
)
_IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d]|\.\d)")
# Candidates only; every match is validated with ipaddress before replacing.
_IPV6_RE = re.compile(r"(?i)(?<![0-9a-f:])(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4}(?![0-9a-f:])")

# ── Usernames in common log formats ──────────────────────────────────────────

_USER_PATTERNS = [
    # sshd: "Failed password for invalid user admin from", "Accepted publickey for julian from"
    re.compile(r"(?i)\b(?:Accepted|Failed|Postponed)\s+\S+\s+for\s+(?:invalid\s+user\s+|illegal\s+user\s+)?(?P<v>\S+)\s+from\b"),
    # sshd: "Invalid user admin from", "Connection closed by authenticating user root",
    # PAM: "session opened for user root(uid=0)", "Disconnected from user julian"
    re.compile(r"(?i)\b(?:invalid|illegal|authenticating|for|from|by)\s+user\s+(?P<v>[^\s(),;:]+)"),
    # su/sudo: "pam_unix(sudo:auth): ... user=julian", key=value forms
    re.compile(
        r"""(?ix)\b(?:user|username|user_name|logname|acct|ruser|suser|duser
        |src_user|dst_user|usrName|TargetUserName|SubjectUserName)\s*[=:]\s*["']?(?P<v>[^\s"',;()\]]+)"""
    ),
    # sudo: "julian : TTY=pts/0 ; PWD=/home/julian ; USER=root ; COMMAND=..."
    re.compile(r"(?m)(?:^|sudo(?:\[\d+\])?:\s+)(?P<v>[a-z_][a-z0-9_.-]{0,31})\s+:\s+(?:TTY|PWD)="),
    # Windows Security log: "Account Name:\t\tjdoe", "User Name: jdoe"
    re.compile(r"(?im)\b(?:Account Name|User Name|Target User Name|Subject User Name|Logon Account)\s*:\s*(?P<v>[^\s\r\n]+)"),
    # DOMAIN\user
    re.compile(r"(?<![\w\\])(?P<v>(?!(?:HKLM|HKCU|HKCR|HKU|HKCC|AUTHORITY|BUILTIN|Windows|System32)\\)[A-Za-z][A-Za-z0-9-]{1,14}\\[A-Za-z0-9._$-]{1,64})(?![\w\\])"),
    # Home directories: /home/julian, C:\Users\julian
    re.compile(r"(?i)(?:/home/|/Users/|\\Users\\)(?P<v>[a-z0-9._-]{2,64})"),
    # nginx/apache combined log: '<ip> - julian [10/Oct/2026:13:55:36 +0200] "GET /'
    re.compile(r"(?m)(?:^|\s)\S+\s+-\s+(?P<v>[^\s\[-][^\s\[]*)\s+\[\d{1,2}/\w{3}/\d{4}:"),
]

_SID_RE = re.compile(r"\bS-1-5-21-\d+-\d+-\d+(?:-\d+)?\b")
_WIN_HOST_RE = re.compile(r"(?im)\b(?:Workstation Name|Workstation|Computer|ComputerName|Source Workstation)\s*[:=]\s*(?P<v>[^\s\r\n]+)")

# ── Hostnames / FQDNs (optional) ─────────────────────────────────────────────

_FQDN_RE = re.compile(
    r"(?i)(?<![\w@.-])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}(?![\w-]|\.[a-z0-9])"
)
# Last labels that are almost always file names or units, not DNS names.
_NOT_TLDS = {
    "conf", "cfg", "ini", "log", "txt", "json", "yaml", "yml", "xml", "html", "htm",
    "php", "py", "js", "ts", "css", "sh", "exe", "dll", "sys", "so", "ko", "pid",
    "sock", "socket", "service", "timer", "mount", "target", "slice", "scope", "path",
    "gz", "zip", "tar", "tgz", "bz2", "xz", "png", "jpg", "jpeg", "gif", "svg", "ico",
    "md", "rs", "go", "java", "class", "jar", "rb", "pl", "c", "h", "cpp", "lock",
    "tmp", "bak", "old", "db", "sqlite", "csv", "pem", "crt", "key", "cer", "d",
    "deb", "rpm", "msi", "bin", "img", "iso", "ps1", "bat", "cmd", "vbs", "env",
    "local",  # handled through known hosts; avoids "Account.local" style noise
}


def _is_placeholder(value: str) -> bool:
    return bool(PLACEHOLDER_RE.fullmatch(value))


class Redactor:
    """Per-request redaction state: value -> placeholder and back.

    Use one instance for everything sent in a single request (system prompt,
    every message) so placeholders stay consistent, then :meth:`restore` the
    answer with the same instance.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        redact_hostnames: bool = False,
        known_hostnames: Iterable[str] = (),
    ):
        self.enabled = enabled
        self.redact_hostnames = redact_hostnames
        self._forward: dict[tuple[str, str], str] = {}
        self._reverse: dict[str, str] = {}
        self._originals: dict[str, str] = {}
        self._counters: dict[str, int] = {}
        hosts = {
            h.strip() for h in known_hostnames
            if h and len(h.strip()) >= 3 and " " not in h.strip()
        } if redact_hostnames else set()
        # Longest first so "nas01.lan" wins over "nas01".
        self._known_hosts_re = (
            re.compile(
                r"(?i)(?<![\w.-])(?:" + "|".join(re.escape(h) for h in sorted(hosts, key=len, reverse=True)) + r")(?![\w-]|\.[a-z0-9])"
            ) if hosts else None
        )

    # ── placeholder bookkeeping ──

    def placeholder(self, kind: str, value: str) -> str:
        norm = value.lower() if kind in (EMAIL, HOST, MAC, IPV6, USER) else value
        key = (kind, norm)
        ph = self._forward.get(key)
        if ph is None:
            self._counters[kind] = self._counters.get(kind, 0) + 1
            ph = f"<{kind}_{self._counters[kind]}>"
            self._forward[key] = ph
            self._originals[ph] = value  # incl. secrets; private, for redact_many only
            if kind not in _NO_RESTORE:
                self._reverse[ph] = value
        return ph

    @property
    def mapping(self) -> dict[str, str]:
        """Placeholder -> original (secrets excluded). Never send this anywhere."""
        return dict(self._reverse)

    @property
    def count(self) -> int:
        return len(self._forward)

    # ── redaction ──

    def redact(self, text: str) -> str:
        if not self.enabled or not text:
            return text
        t = text

        # 1. Secrets first: a password must never survive as "<USER_1>".
        t = _URL_CREDS_RE.sub(
            lambda m: f"{m.group(1)}{self.placeholder(USER, m.group(2))}:{self.placeholder(SECRET, m.group(3))}@", t
        )
        t = _BEARER_RE.sub(lambda m: f"{m.group(1)} {self.placeholder(SECRET, m.group(2))}", t)
        for rx in _TOKEN_FORMATS:
            t = rx.sub(lambda m: self.placeholder(SECRET, m.group(0)), t)
        t = _SECRET_KV_RE.sub(self._secret_kv, t)

        # 2. Identities and addresses.
        t = _EMAIL_RE.sub(lambda m: self.placeholder(EMAIL, m.group(0)), t)
        t = _SID_RE.sub(lambda m: self.placeholder(SID, m.group(0)), t)
        for rx in _USER_PATTERNS:
            t = rx.sub(self._group_sub(USER), t)
        t = _WIN_HOST_RE.sub(self._group_sub(HOST), t)
        t = _MAC_RE.sub(lambda m: self.placeholder(MAC, m.group(0)), t)
        t = _IPV4_RE.sub(self._ipv4, t)
        t = _IPV6_RE.sub(self._ipv6, t)

        # 3. Hostnames (optional).
        if self.redact_hostnames:
            if self._known_hosts_re is not None:
                t = self._known_hosts_re.sub(lambda m: self.placeholder(HOST, m.group(0)), t)
            t = _FQDN_RE.sub(self._fqdn, t)
        return t

    def redact_many(self, texts: list[str]) -> list[str]:
        """Redact several texts of one request.

        After the pattern pass, every value found anywhere is also replaced
        wherever else it appears verbatim, so "Why did julian fail?" is
        caught once a log line has identified julian as a user.
        """
        first = [self.redact(t) for t in texts]
        if not self.enabled or not self._forward:
            return first
        lookup = {v: ph for ph, v in self._originals.items() if len(v) >= _MIN_PROPAGATE_LEN}
        if not lookup:
            return first
        rx = re.compile(
            r"(?<![\w.-])(?:"
            + "|".join(re.escape(v) for v in sorted(lookup, key=len, reverse=True))
            + r")(?![\w-]|\.\w)"
        )
        return [rx.sub(lambda m: lookup.get(m.group(0), m.group(0)), t) if t else t for t in first]

    def redact_messages(self, messages: list[dict]) -> list[dict]:
        idx = [i for i, m in enumerate(messages) if isinstance(m.get("content"), str)]
        redacted = self.redact_many([messages[i]["content"] for i in idx])
        out = list(messages)
        for i, text in zip(idx, redacted):
            out[i] = {**messages[i], "content": text}
        return out

    def _secret_kv(self, m: re.Match) -> str:
        val = m.group("val")
        if _is_placeholder(val):
            return m.group(0)
        # sudo's "PWD=/home/julian" is the working directory, not a password;
        # the home-directory pattern takes care of the username in it.
        if m.group("key") == "PWD" and val.startswith("/"):
            return m.group(0)
        return f"{m.group('key')}{m.group('sep')}{m.group('q')}{self.placeholder(SECRET, val)}"

    def _group_sub(self, kind: str) -> Callable[[re.Match], str]:
        def sub(m: re.Match) -> str:
            val = m.group("v")
            low = val.lower()
            if low in _EMPTY_VALUES or _is_placeholder(val) or val.startswith("<"):
                return m.group(0)
            if kind == USER and (low in _GENERIC_ACCOUNTS or low.rsplit("\\", 1)[-1] in _GENERIC_ACCOUNTS):
                return m.group(0)
            start, end = m.span("v")
            base = m.start(0)
            whole = m.group(0)
            return whole[: start - base] + self.placeholder(kind, val) + whole[end - base:]
        return sub

    def _ipv4(self, m: re.Match) -> str:
        raw = m.group(0)
        try:
            addr = ipaddress.IPv4Address(raw)
        except ValueError:
            return raw
        # Bind-all / loopback / broadcast describe a socket, not a person.
        if addr.is_unspecified or addr.is_loopback or raw == "255.255.255.255":
            return raw
        return self.placeholder(IP, raw)

    def _ipv6(self, m: re.Match) -> str:
        raw = m.group(0)
        # "2001:db8::1: message" — the candidate swallowed the separator.
        core = raw.rstrip(":") if not raw.endswith("::") else raw
        tail = raw[len(core):]
        if core.count(":") < 2:
            return raw
        try:
            addr = ipaddress.IPv6Address(core)
        except ValueError:
            return raw
        if addr.is_unspecified or addr.is_loopback:
            return raw
        return self.placeholder(IPV6, core) + tail

    def _fqdn(self, m: re.Match) -> str:
        raw = m.group(0)
        tld = raw.rsplit(".", 1)[-1].lower()
        if tld in _NOT_TLDS or tld.isdigit():
            return raw
        return self.placeholder(HOST, raw)

    # ── restore ──

    def restore(self, text: str) -> str:
        if not text or not self._reverse:
            return text
        return PLACEHOLDER_RE.sub(lambda m: self._reverse.get(m.group(0), m.group(0)), text)

    def stream_restorer(self) -> "StreamRestorer":
        return StreamRestorer(self)


class StreamRestorer:
    """Restore placeholders in a token stream.

    A placeholder can arrive split over several deltas ("<IP", "_1>"), so
    text from a trailing, still-open "<" is held back until it either closes
    or grows too long to be a placeholder.
    """

    def __init__(self, redactor: Redactor):
        self._r = redactor
        self._buf = ""

    def feed(self, delta: str) -> str:
        self._buf += delta
        idx = self._buf.rfind("<")
        if idx != -1 and ">" not in self._buf[idx:] and len(self._buf) - idx < _MAX_PLACEHOLDER_LEN:
            ready, self._buf = self._buf[:idx], self._buf[idx:]
        else:
            ready, self._buf = self._buf, ""
        return self._r.restore(ready)

    def flush(self) -> str:
        ready, self._buf = self._buf, ""
        return self._r.restore(ready)


REDACTION_SYSTEM_NOTE = (
    "Some values in the data were replaced by placeholders such as <IP_1>, <USER_2>, "
    "<HOST_3> or <SECRET_1> for privacy. The same placeholder always means the same "
    "value. Refer to them exactly as written (keep the angle brackets) and do not "
    "guess the original values."
)
