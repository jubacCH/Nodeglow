"""Domain integration – registration expiry via RDAP.

The companion to the SSL expiry check: a certificate can be perfect while the
domain itself lapses. The registry's RDAP server is looked up in the IANA
bootstrap file, then asked for ``/domain/<name>``.

Not every registry can answer. Some ccTLDs are absent from the IANA bootstrap
(.ch, .li, .de, .io, .at, .eu ...), and some run RDAP without publishing an
expiration event (.ch, .de and .nl answer, but the response has no expiry).
Such domains are reported per domain as ``expiry: None`` with a ``reason`` and
listed in ``data["unavailable"]`` — never as healthy.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone

import httpx

from utils.net_safety import safe_async_client

from integrations._base import Alert, BaseIntegration, CollectorResult, ConfigField, Threshold

BOOTSTRAP_URL = "https://data.iana.org/rdap/dns.json"
MAX_DOMAINS = 100
MAX_CONCURRENCY = 5
MAX_BODY_BYTES = 2 * 1024 * 1024

# Record kinds (DomainRecord["kind"]).
KIND_OK = "ok"                  # an expiry date was read
KIND_NO_EXPIRY = "no_expiry"    # registry answered but publishes no expiration event
KIND_NO_RDAP = "no_rdap"        # TLD has no RDAP service in the IANA bootstrap
KIND_NOT_FOUND = "not_found"    # registry says the domain does not exist
KIND_ERROR = "error"            # the query itself failed (network, HTTP, bad JSON)

_LABEL_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


# ── Domain list ──────────────────────────────────────────────────────────────


def normalize_domain(raw: str) -> str | None:
    """Lower-case ASCII (punycode) form of a domain, or None when invalid."""
    name = (raw or "").strip().lower().rstrip(".")
    if not name or "/" in name or " " in name or ":" in name or "@" in name:
        return None
    try:
        name = name.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if len(name) > 253:
        return None
    labels = name.split(".")
    if len(labels) < 2 or not all(_LABEL_RE.match(label) for label in labels):
        return None
    if labels[-1].isdigit():  # an IPv4 address, not a domain
        return None
    return name


def parse_domain_list(text: str) -> tuple[list[str], list[str]]:
    """Split the ``domains`` textarea into (valid unique domains, invalid lines).

    One domain per line; ``#`` starts a comment. Commas and semicolons also
    separate entries so a pasted list still works.
    """
    valid: list[str] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for line in str(text or "").splitlines():
        line = line.split("#", 1)[0]
        for part in re.split(r"[,;\s]+", line):
            if not part:
                continue
            name = normalize_domain(part)
            if name is None:
                invalid.append(part[:100])
            elif name not in seen:
                seen.add(name)
                valid.append(name)
    return valid, invalid


# ── RDAP parsing ─────────────────────────────────────────────────────────────


def parse_bootstrap(body: object) -> dict[str, str]:
    """TLD -> RDAP base URL (https preferred) from the IANA bootstrap file."""
    out: dict[str, str] = {}
    services = body.get("services") if isinstance(body, dict) else None
    if not isinstance(services, list):
        raise ValueError("IANA RDAP bootstrap has no 'services' list")
    for service in services:
        if not (isinstance(service, list) and len(service) >= 2):
            continue
        tlds, urls = service[0], service[1]
        if not (isinstance(tlds, list) and isinstance(urls, list)):
            continue
        urls = [u for u in urls if isinstance(u, str) and u.startswith(("https://", "http://"))]
        if not urls:
            continue
        base = next((u for u in urls if u.startswith("https://")), urls[0])
        for tld in tlds:
            if isinstance(tld, str):
                out[tld.lower()] = base
    if not out:
        raise ValueError("IANA RDAP bootstrap lists no TLDs")
    return out


def parse_rdap_datetime(value: object) -> datetime | None:
    """Timezone-aware UTC datetime from an RDAP date string, or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    # Registries send up to 9 fractional digits; fromisoformat takes 6.
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def extract_expiry(body: dict) -> datetime | None:
    """The earliest ``expiration`` event date (the conservative reading)."""
    found = []
    for event in body.get("events") or []:
        if not isinstance(event, dict):
            continue
        if str(event.get("eventAction", "")).strip().lower() != "expiration":
            continue
        dt = parse_rdap_datetime(event.get("eventDate"))
        if dt is not None:
            found.append(dt)
    return min(found) if found else None


def _vcard_name(entity: dict) -> str:
    vcard = entity.get("vcardArray")
    if isinstance(vcard, list) and len(vcard) >= 2 and isinstance(vcard[1], list):
        for prop in vcard[1]:
            if isinstance(prop, list) and len(prop) >= 4 and prop[0] == "fn" and isinstance(prop[3], str):
                return prop[3].strip()
    return ""


def extract_registrar(body: dict) -> str:
    """Name of the entity with the registrar role (searches nested entities)."""
    def walk(entities: object, depth: int) -> str:
        if not isinstance(entities, list) or depth > 3:
            return ""
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            roles = [str(r).lower() for r in (entity.get("roles") or [])]
            if "registrar" in roles:
                name = _vcard_name(entity)
                if not name:
                    for pid in entity.get("publicIds") or []:
                        if isinstance(pid, dict) and pid.get("identifier"):
                            name = str(pid["identifier"])
                            break
                return name or str(entity.get("handle") or "")
            nested = walk(entity.get("entities"), depth + 1)
            if nested:
                return nested
        return ""

    return walk(body.get("entities"), 0)[:200]


def extract_status(body: dict) -> list[str]:
    return [str(s) for s in (body.get("status") or []) if isinstance(s, str)][:20]


def is_hold_status(status: str) -> bool:
    """True for clientHold / serverHold in any spelling ("client hold", "clientHold")."""
    flat = re.sub(r"[^a-z]", "", status.lower())
    return flat in ("clienthold", "serverhold")


def _record(name: str, kind: str, reason: str = "", **extra: object) -> dict:
    return {
        "name": name, "tld": name.rsplit(".", 1)[-1], "kind": kind, "expiry": None,
        "registrar": "", "status": [], "rdap_server": "", "reason": reason, **extra,
    }


def parse_rdap_domain(name: str, body: object, rdap_server: str = "") -> dict:
    """One domain record from an RDAP ``/domain/<name>`` answer."""
    if not isinstance(body, dict):
        return _record(name, KIND_ERROR, "RDAP answer is not a JSON object", rdap_server=rdap_server)
    expiry = extract_expiry(body)
    rec = _record(
        name, KIND_OK if expiry else KIND_NO_EXPIRY,
        "" if expiry else "The registry's RDAP answer has no expiration date",
        rdap_server=rdap_server,
    )
    rec["expiry"] = expiry.isoformat() if expiry else None
    rec["registrar"] = extract_registrar(body)
    rec["status"] = extract_status(body)
    return rec


def tld_of(name: str) -> str:
    return name.rsplit(".", 1)[-1]


# ── Client ───────────────────────────────────────────────────────────────────


class BodyTooLarge(Exception):
    """A response exceeded MAX_BODY_BYTES."""


async def _get_bounded(client: httpx.AsyncClient, url: str, headers: dict | None = None) -> tuple[int, bytes]:
    """(status, body) of a GET; reading stops at MAX_BODY_BYTES instead of
    buffering whatever a server sends."""
    async with client.stream("GET", url, headers=headers) as resp:
        if resp.status_code != 200:
            return resp.status_code, b""
        chunks: list[bytes] = []
        size = 0
        async for chunk in resp.aiter_bytes():
            size += len(chunk)
            if size > MAX_BODY_BYTES:
                raise BodyTooLarge()
            chunks.append(chunk)
        return resp.status_code, b"".join(chunks)


class DomainAPI:
    def __init__(self, domains: list[str]):
        self.domains = domains

    async def _query(self, client: httpx.AsyncClient, sem: asyncio.Semaphore,
                     name: str, bootstrap: dict[str, str]) -> dict:
        base = bootstrap.get(tld_of(name))
        if not base:
            return _record(name, KIND_NO_RDAP,
                           f"No RDAP service for .{tld_of(name)} in the IANA bootstrap; "
                           "the registry does not publish expiry data this way")
        url = f"{base.rstrip('/')}/domain/{name}"
        try:
            async with sem:
                status, body = await _get_bounded(
                    client, url, {"Accept": "application/rdap+json, application/json"})
            if status == 404:
                return _record(name, KIND_NOT_FOUND, "Domain not found in the registry", rdap_server=base)
            if status != 200:
                return _record(name, KIND_ERROR, f"RDAP server answered HTTP {status}", rdap_server=base)
            return parse_rdap_domain(name, json.loads(body), rdap_server=base)
        except BodyTooLarge:
            return _record(name, KIND_ERROR, "RDAP answer is too large", rdap_server=base)
        except ValueError:
            return _record(name, KIND_ERROR, "RDAP answer is not valid JSON", rdap_server=base)
        except Exception as exc:
            return _record(name, KIND_ERROR, f"RDAP query failed: {type(exc).__name__}", rdap_server=base)

    async def fetch_all(self) -> dict:
        async with safe_async_client(timeout=15.0, follow_redirects=True) as client:
            try:
                status, body = await _get_bounded(client, BOOTSTRAP_URL)
            except BodyTooLarge:
                raise ValueError("IANA RDAP bootstrap is too large") from None
            if status != 200:
                raise ValueError(f"IANA RDAP bootstrap answered HTTP {status}")
            bootstrap = parse_bootstrap(json.loads(body))
            sem = asyncio.Semaphore(MAX_CONCURRENCY)
            records = await asyncio.gather(*(self._query(client, sem, d, bootstrap) for d in self.domains))
        return build_snapshot(list(records))

    async def health_check(self) -> bool:
        try:
            async with safe_async_client(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(BOOTSTRAP_URL)
                return resp.status_code == 200
        except Exception:
            return False


def build_snapshot(records: list[dict], invalid: list[str] | None = None,
                   now: datetime | None = None) -> dict:
    """Snapshot dict from per-domain records."""
    now = now or datetime.now(timezone.utc)
    unavailable = sorted(r["name"] for r in records if r["kind"] in (KIND_NO_EXPIRY, KIND_NO_RDAP, KIND_ERROR))
    return {
        "collected_at": now.isoformat(),
        "domains": sorted(records, key=lambda r: (r["expiry"] is None, r["expiry"] or "", r["name"])),
        "unavailable": unavailable,
        "invalid_domains": list(invalid or []),
        "totals": {
            "domains": len(records),
            "with_expiry": sum(1 for r in records if r["kind"] == KIND_OK),
            "unavailable": len(unavailable),
            "not_found": sum(1 for r in records if r["kind"] == KIND_NOT_FOUND),
        },
    }


# ── Integration Plugin ───────────────────────────────────────────────────────


class DomainIntegration(BaseIntegration):
    name = "domain"
    display_name = "Domain Expiry"
    icon = ""
    color = "teal"
    description = "Monitor domain registration expiry via RDAP, the companion to the SSL expiry check."
    # Public registry servers: the core collects them, never a probe.
    network_scope = "cloud"
    # Registrations change on a scale of months; a few lookups a day suffice.
    default_interval_seconds = 21600

    config_fields = [
        ConfigField(key="domains", label="Domains", field_type="textarea",
                    placeholder="example.com\nexample.org"),
    ]

    alert_thresholds = (
        Threshold("expiry_warn_days", "Domain expiry warning", 30, "days", 1, 3650),
        Threshold("expiry_crit_days", "Domain expiry critical", 7, "days", 1, 3650),
    )

    def _domains(self) -> tuple[list[str], list[str]]:
        return parse_domain_list(self.config.get("domains") or "")

    async def collect(self) -> CollectorResult:
        try:
            domains, invalid = self._domains()
            if not domains:
                detail = f" (ignored invalid entries: {', '.join(invalid[:5])})" if invalid else ""
                return CollectorResult(success=False, error=f"No valid domains configured{detail}")
            if len(domains) > MAX_DOMAINS:
                return CollectorResult(success=False,
                                       error=f"Too many domains ({len(domains)}); the limit is {MAX_DOMAINS}")
            data = await DomainAPI(domains).fetch_all()
            data["invalid_domains"] = invalid
            queried = [r for r in data["domains"] if r["kind"] != KIND_NO_RDAP]
            if queried and all(r["kind"] == KIND_ERROR for r in queried):
                return CollectorResult(success=False, error=f"All RDAP queries failed: {queried[0]['reason']}")
            return CollectorResult(success=True, data=data)
        except Exception as exc:
            return CollectorResult(success=False, error=f"{type(exc).__name__}: {exc}")

    async def health_check(self) -> bool:
        return await DomainAPI([]).health_check()

    def parse_alerts(self, data: dict) -> list[Alert]:
        alerts: list[Alert] = []
        if not isinstance(data, dict):
            return alerts
        now = parse_rdap_datetime(data.get("collected_at"))
        warn = self.threshold("expiry_warn_days")
        crit = self.threshold("expiry_crit_days")

        for entry in data.get("invalid_domains") or []:
            alerts.append(Alert(severity="warning", title="Invalid domain entry",
                                detail=f"{entry!r} is not a valid domain name and is not monitored",
                                entity=f"invalid domain: {entry}"))

        for rec in data.get("domains") or []:
            if not isinstance(rec, dict) or not rec.get("name"):
                continue
            name = str(rec["name"])
            entity = f"domain: {name}"
            kind = rec.get("kind")

            if kind == KIND_NOT_FOUND:
                alerts.append(Alert(severity="critical", title="Domain not found",
                                    detail=f"{name} is not registered according to the registry",
                                    entity=entity))
                continue

            for status in rec.get("status") or []:
                if isinstance(status, str) and is_hold_status(status):
                    alerts.append(Alert(severity="critical", title="Domain on hold",
                                        detail=f"{name} has status '{status}' and does not resolve",
                                        entity=f"domain hold: {name}"))
                    break

            expiry = parse_rdap_datetime(rec.get("expiry"))
            if expiry is None or now is None:
                continue  # no data is not "fine", but it is not an alert either
            days = (expiry - now).total_seconds() / 86400
            when = expiry.strftime("%Y-%m-%d")
            if days <= 0:
                alerts.append(Alert(severity="critical", title="Domain expired",
                                    detail=f"{name} expired on {when}", entity=entity))
            elif days <= crit:
                alerts.append(Alert(severity="critical", title="Domain expires soon",
                                    detail=f"{name} expires in {int(days)} days ({when})", entity=entity))
            elif days <= warn:
                alerts.append(Alert(severity="warning", title="Domain expiring",
                                    detail=f"{name} expires in {int(days)} days ({when})", entity=entity))
        return alerts
