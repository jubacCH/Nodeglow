"""Microsoft 365 / Entra ID integration via Microsoft Graph (app-only auth).

Three independent parts, each behind its own Graph application permission:

* service health      ``ServiceHealth.Read.All``
* licences            ``LicenseAssignment.Read.All`` (or ``Organization.Read.All``)
* app registrations   ``Application.Read.All``

A missing permission on one part is reported in ``data["unavailable"]``; only
a failed token request, or every part failing, makes collect() fail.
"""
from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timezone

import httpx

from utils.net_safety import safe_async_client

from integrations._base import Alert, BaseIntegration, CollectorResult, ConfigField, Threshold

log = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
LOGIN_BASE = "https://login.microsoftonline.com"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"

# Page caps keep a misbehaving tenant from turning one poll into thousands of calls.
MAX_PAGES_HEALTH = 10
MAX_PAGES_ISSUES = 10
MAX_PAGES_APPS = 20

_TENANT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]*$")
_AADSTS_RE = re.compile(r"AADSTS\d+")

# serviceHealthStatus values (Graph v1.0), lower-cased. Graph has returned them
# both camelCase and PascalCase, so comparison is case-insensitive.
STATUS_OPERATIONAL = "serviceoperational"
_STATUS_CRITICAL = {"serviceinterruption"}
# Recovery / closing states: not operational, but nothing is broken any more.
_STATUS_RECOVERED = {"verifyingservice", "servicerestored", "postincidentreviewpublished", "falsepositive"}


class GraphPartError(Exception):
    """A single optional Graph part failed; carries the HTTP status if any."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


# ── Pure helpers ──────────────────────────────────────────────────────────────


def _parse_dt(value) -> datetime | None:
    """Parse a Graph timestamp into an aware UTC datetime; None when missing/invalid."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    # Graph emits 2..7 fractional digits; fromisoformat is picky about that.
    text = re.sub(r"\.(\d+)", lambda m: "." + (m.group(1) + "000000")[:6], text, count=1)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def _int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return int(value)
    return None


def classify_status(status) -> str:
    """Map a Graph serviceHealthStatus to ``ok | critical | warning | recovered``.

    ``serviceOperational`` is the only healthy value. Missing or unknown values
    are ``warning``: an unrecognised state is never reported as healthy.
    """
    s = str(status or "").strip().lower()
    if s == STATUS_OPERATIONAL:
        return "ok"
    if s in _STATUS_CRITICAL:
        return "critical"
    if s in _STATUS_RECOVERED:
        return "recovered"
    return "warning"


def parse_health_overviews(items: list) -> list[dict]:
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        status = it.get("status")
        state = classify_status(status)
        out.append({
            "id": str(it.get("id") or ""),
            "service": str(it.get("service") or it.get("id") or ""),
            "status": str(status) if status else None,
            "state": state,
        })
    out.sort(key=lambda s: s["service"].lower())
    return out


def parse_issues(items: list) -> list[dict]:
    """Open (unresolved) service issues, newest first."""
    out = []
    for it in items or []:
        if not isinstance(it, dict) or it.get("isResolved") is not False:
            continue
        status = it.get("status")
        out.append({
            "id": str(it.get("id") or ""),
            "title": str(it.get("title") or ""),
            "service": str(it.get("service") or ""),
            "status": str(status) if status else None,
            "state": classify_status(status),
            "classification": str(it.get("classification") or "").lower() or None,
            "feature": str(it.get("feature") or "") or None,
            "impact": str(it.get("impactDescription") or "") or None,
            "start": _iso(_parse_dt(it.get("startDateTime"))),
            "last_modified": _iso(_parse_dt(it.get("lastModifiedDateTime"))),
        })
    out.sort(key=lambda i: i["start"] or "", reverse=True)
    return out


def parse_skus(items: list) -> list[dict]:
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        prepaid = it.get("prepaidUnits") if isinstance(it.get("prepaidUnits"), dict) else {}
        enabled = _int(prepaid.get("enabled"))
        consumed = _int(it.get("consumedUnits"))
        pct = None
        if enabled is not None and consumed is not None and enabled > 0:
            pct = round(consumed / enabled * 100, 1)
        out.append({
            "sku_id": str(it.get("skuId") or it.get("id") or ""),
            "sku": str(it.get("skuPartNumber") or it.get("skuId") or ""),
            "capability_status": str(it.get("capabilityStatus") or "") or None,
            "consumed": consumed,
            "enabled": enabled,
            "suspended": _int(prepaid.get("suspended")),
            "warning": _int(prepaid.get("warning")),
            "locked_out": _int(prepaid.get("lockedOut")),
            "pct": pct,
        })
    out.sort(key=lambda s: s["sku"].lower())
    return out


def parse_applications(items: list, now: datetime) -> list[dict]:
    """Flatten every password credential (secret) and key credential (certificate).

    Each row carries ``days_left`` (floor of the remaining time at ``now``;
    negative when already expired, None when Graph gave no end date).
    """
    out = []
    for app in items or []:
        if not isinstance(app, dict):
            continue
        app_name = str(app.get("displayName") or app.get("appId") or app.get("id") or "")
        for kind, field in (("secret", "passwordCredentials"), ("certificate", "keyCredentials")):
            creds = app.get(field)
            if not isinstance(creds, list):
                continue
            for c in creds:
                if not isinstance(c, dict):
                    continue
                end = _parse_dt(c.get("endDateTime"))
                days = math.floor((end - now).total_seconds() / 86400) if end else None
                out.append({
                    "app": app_name,
                    "app_id": str(app.get("appId") or ""),
                    "object_id": str(app.get("id") or ""),
                    "kind": kind,
                    "name": str(c.get("displayName") or "") or None,
                    "key_id": str(c.get("keyId") or ""),
                    "start": _iso(_parse_dt(c.get("startDateTime"))),
                    "end": _iso(end),
                    "days_left": days,
                })
    out.sort(key=lambda c: (c["days_left"] is None, c["days_left"] if c["days_left"] is not None else 0, c["app"].lower()))
    return out


def _oauth_error(resp: httpx.Response) -> str:
    """Short description of a failed token request; never echoes the request."""
    code = ""
    aadsts = ""
    try:
        body = resp.json()
        if isinstance(body, dict):
            code = str(body.get("error") or "")[:60]
            m = _AADSTS_RE.search(str(body.get("error_description") or ""))
            aadsts = m.group(0) if m else ""
    except ValueError:
        pass
    detail = " ".join(p for p in (code, aadsts) if p)
    return f"Token request failed: HTTP {resp.status_code}" + (f" ({detail})" if detail else "")


# ── API client ────────────────────────────────────────────────────────────────


class M365API:
    def __init__(self, tenant_id: str, client_id: str, client_secret: str):
        self.tenant_id = (tenant_id or "").strip()
        self.client_id = (client_id or "").strip()
        self.client_secret = client_secret or ""

    async def get_token(self, client: httpx.AsyncClient) -> str:
        if not _TENANT_RE.match(self.tenant_id):
            raise ValueError("Invalid tenant ID")
        if not self.client_id or not self.client_secret:
            raise ValueError("Client ID and client secret are required")
        try:
            resp = await client.post(
                f"{LOGIN_BASE}/{self.tenant_id}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": GRAPH_SCOPE,
                },
            )
        except httpx.HTTPError as exc:
            raise ValueError(f"Token request failed: {type(exc).__name__}") from None
        if resp.status_code != 200:
            raise ValueError(_oauth_error(resp))
        try:
            token = resp.json().get("access_token")
        except (ValueError, AttributeError):
            token = None
        if not token or not isinstance(token, str):
            raise ValueError("Token request failed: response has no access_token")
        return token

    async def _pages(self, client: httpx.AsyncClient, token: str, path: str,
                     params: dict | None, max_pages: int) -> tuple[list, bool]:
        """GET a collection and follow ``@odata.nextLink``; returns (items, truncated)."""
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        url = f"{GRAPH_BASE}{path}"
        items: list = []
        pages = 0
        while url:
            try:
                resp = await client.get(url, headers=headers, params=params if pages == 0 else None)
            except httpx.HTTPError as exc:
                raise GraphPartError(type(exc).__name__) from None
            if resp.status_code != 200:
                raise GraphPartError(f"HTTP {resp.status_code}", resp.status_code)
            try:
                body = resp.json()
            except ValueError:
                raise GraphPartError("invalid JSON") from None
            value = body.get("value") if isinstance(body, dict) else None
            if not isinstance(value, list):
                raise GraphPartError("unexpected response")
            items.extend(value)
            pages += 1
            nxt = body.get("@odata.nextLink")
            if not nxt:
                return items, False
            # The bearer token must never be sent anywhere but Graph.
            if not isinstance(nxt, str) or not nxt.startswith(GRAPH_BASE + "/"):
                raise GraphPartError("unexpected nextLink")
            if pages >= max_pages:
                return items, True
            url = nxt
        return items, False

    async def fetch_all(self) -> dict:
        now = datetime.now(timezone.utc)
        unavailable: set[str] = set()
        parts_failed = 0

        async with safe_async_client(timeout=30.0) as client:
            token = await self.get_token(client)

            def fail(label: str, permission: str, exc: GraphPartError) -> None:
                if exc.status in (401, 403):
                    unavailable.add(f"{label} (missing permission {permission})")
                else:
                    unavailable.add(f"{label} ({exc})")

            # Service health: overviews and issues share one permission.
            services = issues = None
            try:
                items, trunc = await self._pages(client, token, "/admin/serviceAnnouncement/healthOverviews",
                                                 None, MAX_PAGES_HEALTH)
                services = parse_health_overviews(items)
                if trunc:
                    unavailable.add("service health (truncated)")
            except GraphPartError as exc:
                fail("service health", "ServiceHealth.Read.All", exc)
                parts_failed += 1
            if services is not None:
                try:
                    items, trunc = await self._pages(client, token, "/admin/serviceAnnouncement/issues",
                                                     None, MAX_PAGES_ISSUES)
                    issues = parse_issues(items)
                    if trunc:
                        unavailable.add("service issues (truncated)")
                except GraphPartError as exc:
                    fail("service issues", "ServiceHealth.Read.All", exc)

            skus = None
            try:
                items, _ = await self._pages(client, token, "/subscribedSkus", None, 5)
                skus = parse_skus(items)
            except GraphPartError as exc:
                fail("licences", "LicenseAssignment.Read.All or Organization.Read.All", exc)
                parts_failed += 1

            creds = None
            apps_total = None
            try:
                items, trunc = await self._pages(
                    client, token, "/applications",
                    {"$select": "id,appId,displayName,passwordCredentials,keyCredentials", "$top": "999"},
                    MAX_PAGES_APPS)
                creds = parse_applications(items, now)
                apps_total = len([a for a in items if isinstance(a, dict)])
                if trunc:
                    unavailable.add(f"app registrations (truncated after {MAX_PAGES_APPS} pages)")
            except GraphPartError as exc:
                fail("app credentials", "Application.Read.All", exc)
                parts_failed += 1

        if parts_failed >= 3:
            raise ValueError("All Microsoft Graph requests failed: " + "; ".join(sorted(unavailable)))

        return build_snapshot(now, services, issues, skus, creds, apps_total, sorted(unavailable))


def build_snapshot(now: datetime, services, issues, skus, creds, apps_total, unavailable: list[str]) -> dict:
    """Assemble the stored snapshot; totals are None when their part is missing."""
    totals = {
        "services_total": len(services) if services is not None else None,
        "services_unhealthy": sum(1 for s in services if s["state"] != "ok") if services is not None else None,
        "open_issues": len(issues) if issues is not None else None,
        "skus_total": len(skus) if skus is not None else None,
        "apps_total": apps_total,
        "credentials_total": len(creds) if creds is not None else None,
        "credentials_expired": sum(1 for c in creds if c["days_left"] is not None and c["days_left"] < 0)
        if creds is not None else None,
    }
    return {
        "collected_at": _iso(now),
        "services": services,
        "issues": issues,
        "skus": skus,
        "credentials": creds,
        "totals": totals,
        "unavailable": unavailable,
    }


# ── Integration Plugin ────────────────────────────────────────────────────────


class M365Integration(BaseIntegration):
    name = "m365"
    display_name = "Microsoft 365"
    icon = "microsoft365"
    color = "blue"
    description = ("Monitor Microsoft 365 / Entra ID through Microsoft Graph: service health, "
                   "licence usage and app registration secret/certificate expiry.")
    # A public SaaS API: the core collects it, never a probe.
    network_scope = "cloud"
    default_interval_seconds = 300

    config_fields = [
        ConfigField(key="tenant_id", label="Tenant ID", field_type="text",
                    placeholder="Directory (tenant) ID or verified domain"),
        ConfigField(key="client_id", label="Application (client) ID", field_type="text",
                    placeholder="App registration with these Application permissions (admin consent "
                                "required): ServiceHealth.Read.All, LicenseAssignment.Read.All "
                                "(or Organization.Read.All), Application.Read.All"),
        ConfigField(key="client_secret", label="Client secret", field_type="password", encrypted=True,
                    placeholder="Client secret value (not the secret ID)"),
    ]

    alert_thresholds = (
        Threshold("license_warn_pct", "Licence usage warning", 90, "%", 1, 100),
        Threshold("cred_warn_days", "Credential expiry warning", 30, "days", 1, 730),
        Threshold("cred_crit_days", "Credential expiry critical", 7, "days", 1, 730),
    )

    def _api(self) -> M365API:
        return M365API(
            tenant_id=self.config.get("tenant_id", ""),
            client_id=self.config.get("client_id", ""),
            client_secret=self.config.get("client_secret", ""),
        )

    async def collect(self) -> CollectorResult:
        try:
            data = await self._api().fetch_all()
            return CollectorResult(success=True, data=data)
        except ValueError as exc:
            return CollectorResult(success=False, error=str(exc))
        except Exception as exc:  # never echo str(exc): it may carry request details
            log.warning("Microsoft 365 collection failed: %s", type(exc).__name__)
            return CollectorResult(success=False, error=f"Collection failed: {type(exc).__name__}")

    def parse_alerts(self, data: dict) -> list[Alert]:
        alerts: list[Alert] = []
        if not isinstance(data, dict):
            return alerts

        # Service health
        for svc in data.get("services") or []:
            if not isinstance(svc, dict):
                continue
            state = classify_status(svc.get("status")) if svc.get("status") else None
            if state is None or state == "ok":
                continue
            name = svc.get("service") or svc.get("id") or "?"
            severity = {"critical": "critical", "warning": "warning", "recovered": "info"}[state]
            # The status stays out of the title (part of the incident key): one outage
            # passes through several statuses and must remain one incident.
            alerts.append(Alert(severity=severity, title=f"Microsoft 365 service {name} not operational",
                                detail=f"Service health status is {svc.get('status')}",
                                entity=f"service: {svc.get('id') or name}"))

        # Open issues
        for issue in data.get("issues") or []:
            if not isinstance(issue, dict):
                continue
            ident = issue.get("id") or issue.get("title") or "?"
            classification = issue.get("classification")
            state = classify_status(issue.get("status"))
            if classification == "incident":
                severity = "critical" if state == "critical" else ("info" if state == "recovered" else "warning")
                kind = "incident"
            else:
                severity = "info"
                kind = "advisory"
            # Microsoft rewords issue titles while an issue is open; the wording
            # goes into the detail so the incident key (title + entity) stays put.
            alerts.append(Alert(
                severity=severity,
                title=f"Microsoft 365 {kind}",
                detail=(f"{issue['title']}: " if issue.get("title") else "")
                       + f"{issue.get('service') or '?'} ({ident}), status {issue.get('status') or 'unknown'}"
                       + (f". {issue['impact']}" if issue.get("impact") else ""),
                entity=f"issue: {ident}"))

        # Licences
        warn_pct = self.threshold("license_warn_pct")
        for sku in data.get("skus") or []:
            if not isinstance(sku, dict):
                continue
            name = sku.get("sku") or sku.get("sku_id") or "?"
            cap = sku.get("capability_status")
            if cap in ("Warning", "Suspended", "LockedOut"):
                alerts.append(Alert(severity="warning", title=f"Licence {name} is {cap}",
                                    detail=f"Capability status of {name} is {cap}",
                                    entity=f"licence-status: {name}"))
            enabled, consumed = sku.get("enabled"), sku.get("consumed")
            if not isinstance(enabled, int) or not isinstance(consumed, int) or enabled <= 0:
                continue
            pct = consumed / enabled * 100
            if consumed >= enabled:
                # Exactly used up is how many tenants buy licences: worth a warning.
                # More assigned than purchased is the real problem.
                alerts.append(Alert(severity="critical" if consumed > enabled else "warning",
                                    title=f"Licence {name} exhausted",
                                    detail=f"{consumed} of {enabled} units assigned",
                                    entity=f"licence: {name}"))
            elif pct >= warn_pct:
                alerts.append(Alert(severity="warning", title=f"Licence {name} running low",
                                    detail=f"{consumed} of {enabled} units assigned ({pct:.1f}%)",
                                    entity=f"licence: {name}"))

        # App credentials
        collected = _parse_dt(data.get("collected_at"))
        creds = [c for c in (data.get("credentials") or []) if isinstance(c, dict)]
        if collected is not None:
            warn_s = self.threshold("cred_warn_days") * 86400
            crit_s = self.threshold("cred_crit_days") * 86400
            by_app: dict[str, list[dict]] = {}
            for c in creds:
                by_app.setdefault(c.get("object_id") or c.get("app") or "", []).append(c)
            for c in creds:
                end = _parse_dt(c.get("end"))
                if end is None:
                    continue
                remaining = (end - collected).total_seconds()
                kind = "secret" if c.get("kind") == "secret" else "certificate"
                app = c.get("app") or c.get("app_id") or "?"
                ident = c.get("key_id") or f"{c.get('name') or ''}@{c.get('end')}"
                entity = f"app: {app} {kind} {ident}"
                label = f"{kind} '{c['name']}'" if c.get("name") else kind
                if remaining <= 0:
                    siblings = by_app.get(c.get("object_id") or c.get("app") or "", [])
                    superseded = any(
                        s is not c and (e := _parse_dt(s.get("end"))) is not None and e > collected
                        for s in siblings)
                    # An expired credential next to a valid one is usually a rotated
                    # leftover: still visible, but not an incident.
                    alerts.append(Alert(
                        severity="info" if superseded else "critical",
                        title=f"App {app}: {kind} expired",
                        detail=f"{label} expired on {end.date().isoformat()}"
                               + (" (another credential is still valid)" if superseded else ""),
                        entity=entity))
                elif remaining <= warn_s:
                    days = math.ceil(remaining / 86400)
                    alerts.append(Alert(
                        severity="critical" if remaining <= crit_s else "warning",
                        title=f"App {app}: {kind} expires soon",
                        detail=f"{label} expires on {end.date().isoformat()} ({days} day{'s' if days != 1 else ''} left)",
                        entity=entity))
        return alerts

    async def health_check(self) -> bool:
        try:
            async with safe_async_client(timeout=10.0) as client:
                await self._api().get_token(client)
            return True
        except Exception:
            return False
