"""Infomaniak integration – the account's products, their expiry and state.

Reads ``GET /1/products`` (Bearer token). The products list is the only
request and the core one: if it fails, the whole collection fails.

Expiry alerts are computed in ``parse_alerts`` against the ``collected_at``
timestamp stored in the snapshot, never against the wall clock.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from utils.net_safety import safe_async_client

from integrations._base import Alert, BaseIntegration, CollectorResult, ConfigField, Threshold

log = logging.getLogger(__name__)

API_BASE = "https://api.infomaniak.com"
PER_PAGE = 100
MAX_PAGES = 50


class InfomaniakError(Exception):
    """A request failed; the message is safe to show (never holds the token)."""


# ── Parsers ───────────────────────────────────────────────────────────────────


def to_utc(value: Any) -> datetime | None:
    """Timezone-aware UTC datetime from a unix timestamp or ISO string, else None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                value = float(text)
            except ValueError:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
                return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
        if isinstance(value, (int, float)):
            seconds = value / 1000 if abs(value) > 1e11 else value  # tolerate milliseconds
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None
    return None


def _iso(value: Any) -> str | None:
    dt = to_utc(value)
    return dt.isoformat() if dt else None


def _flag(raw: dict, *keys: str) -> bool | None:
    """First boolean found under any of the keys; None when the API did not say."""
    for key in keys:
        if isinstance(raw.get(key), bool):
            return raw[key]
        if raw.get(key) in (0, 1) and raw.get(key) is not None:
            return bool(raw[key])
    return None


def parse_product(raw: dict) -> dict:
    tags = raw.get("tags")
    return {
        "id": raw.get("id"),
        "account_id": raw.get("account_id"),
        "service_id": raw.get("service_id"),
        "service_name": raw.get("service_name") or "",
        "customer_name": raw.get("customer_name") or "",
        "internal_name": raw.get("internal_name") or "",
        "created_at": _iso(raw.get("created_at")),
        "expires_at": _iso(raw.get("expired_at")),
        "locked": _flag(raw, "is_locked"),
        "maintenance": _flag(raw, "has_maintenance", "in_maintenance"),
        "trial": _flag(raw, "is_trial"),
        "tags": [t for t in tags if isinstance(t, (str, int))] if isinstance(tags, list) else [],
    }


def product_label(product: dict) -> str:
    """Human name of a parsed product, never empty."""
    name = product.get("customer_name") or product.get("internal_name") or product.get("service_name")
    service = product.get("service_name")
    pid = product.get("id")
    base = name or (f"product {pid}" if pid is not None else "product")
    return f"{base} ({service})" if name and service and name != service else base


# ── API client ────────────────────────────────────────────────────────────────


class InfomaniakAPI:
    def __init__(self, api_token: str, account_id: str = ""):
        self._headers = {"Authorization": f"Bearer {api_token}", "Accept": "application/json"}
        self.account_id = str(account_id).strip() if account_id else ""

    async def _get(self, client: httpx.AsyncClient, path: str, params: dict) -> httpx.Response:
        try:
            return await client.get(f"{API_BASE}{path}", headers=self._headers, params=params)
        except httpx.HTTPError as exc:
            raise InfomaniakError(f"Request to Infomaniak failed ({type(exc).__name__})") from None

    @staticmethod
    def _body(resp: httpx.Response) -> dict:
        if resp.status_code == 429:
            raise InfomaniakError("Infomaniak API rate limit exceeded (HTTP 429, 60 requests per minute)")
        try:
            body = resp.json()
        except ValueError:
            body = None
        if resp.status_code in (401, 403):
            raise InfomaniakError(f"Infomaniak rejected the API token (HTTP {resp.status_code})")
        if resp.status_code >= 400:
            desc = ""
            if isinstance(body, dict) and isinstance(body.get("error"), dict):
                desc = str(body["error"].get("description") or "")[:120]
            raise InfomaniakError(f"Infomaniak API error (HTTP {resp.status_code})" + (f": {desc}" if desc else ""))
        if not isinstance(body, dict):
            raise InfomaniakError("Infomaniak returned a response that is not a JSON object")
        if body.get("result") != "success":
            err = body.get("error")
            desc = str(err.get("description") or "")[:120] if isinstance(err, dict) else ""
            raise InfomaniakError("Infomaniak reported an error" + (f": {desc}" if desc else ""))
        return body

    async def list_products(self, client: httpx.AsyncClient) -> list[dict]:
        """All products, following the pagination the API reports."""
        products: list[dict] = []
        seen: set[Any] = set()
        page = 1
        send_per_page = True
        for _ in range(MAX_PAGES):
            params: dict[str, Any] = {"page": page}
            if send_per_page:
                params["per_page"] = PER_PAGE
            if self.account_id:
                params["account_id"] = self.account_id
            resp = await self._get(client, "/1/products", params)
            if resp.status_code in (400, 422) and send_per_page and page == 1:
                # The page-size parameter is not documented for this endpoint:
                # retry once with the API's own default page size.
                send_per_page = False
                continue
            body = self._body(resp)
            data = body.get("data")
            if not isinstance(data, list):
                raise InfomaniakError("Infomaniak response has no product list")
            fresh = 0
            for item in data:
                if not isinstance(item, dict):
                    continue
                key = item.get("id")
                if key is not None and key in seen:
                    continue
                seen.add(key)
                products.append(item)
                fresh += 1
            pages = body.get("pages")
            total = body.get("total")
            if isinstance(pages, int) and not isinstance(pages, bool):
                if page >= pages:
                    return products
            elif isinstance(total, int) and not isinstance(total, bool) and len(products) >= total:
                return products
            elif not data:
                return products
            if fresh == 0:
                # A page that repeats what we have: the API ignores `page`.
                return products
            page += 1
        raise InfomaniakError(f"Infomaniak product list exceeds {MAX_PAGES} pages")

    async def fetch_all(self) -> dict:
        async with safe_async_client(timeout=30.0) as client:
            raw = await self.list_products(client)
        products = [parse_product(p) for p in raw]
        return {
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "products": products,
            "product_count": len(products),
            "unavailable": [],
        }


# ── Integration plugin ────────────────────────────────────────────────────────


class InfomaniakIntegration(BaseIntegration):
    name = "infomaniak"
    display_name = "Infomaniak"
    icon = ""  # no Simple Icons entry
    color = "sky"
    description = "Monitor Infomaniak products: expiry dates, locks and maintenance."
    # A public SaaS API: the core collects it, never a probe.
    network_scope = "cloud"
    default_interval_seconds = 300

    config_fields = [
        ConfigField(
            key="api_token",
            label="API Token",
            field_type="password",
            placeholder="Token with the 'accounts' scope (manager.infomaniak.com > API tokens)",
            encrypted=True,
            required=True,
        ),
        ConfigField(
            key="account_id",
            label="Account ID",
            field_type="text",
            placeholder="Optional: limit to one account",
            required=False,
        ),
    ]

    alert_thresholds = (
        Threshold("expiry_warn_days", "Product expiry warning", 30, "days", 1, 365),
        Threshold("expiry_crit_days", "Product expiry critical", 7, "days", 1, 365),
    )

    async def collect(self) -> CollectorResult:
        token = self.config.get("api_token")
        if not token:
            return CollectorResult(success=False, error="No API token configured")
        try:
            api = InfomaniakAPI(token, self.config.get("account_id") or "")
            return CollectorResult(success=True, data=await api.fetch_all())
        except InfomaniakError as exc:
            return CollectorResult(success=False, error=str(exc))
        except Exception as exc:
            log.warning("Infomaniak collection failed: %s", type(exc).__name__)
            return CollectorResult(success=False, error=f"Infomaniak collection failed ({type(exc).__name__})")

    def parse_alerts(self, data: dict) -> list[Alert]:
        alerts: list[Alert] = []
        if not isinstance(data, dict):
            return alerts
        now = to_utc(data.get("collected_at"))
        warn = self.threshold("expiry_warn_days")
        crit = self.threshold("expiry_crit_days")

        products = data.get("products")
        for product in products if isinstance(products, list) else []:
            if not isinstance(product, dict):
                continue
            label = product_label(product)
            pid = product.get("id")
            ref = str(pid) if pid is not None else label
            trial = " (trial)" if product.get("trial") is True else ""

            expires = to_utc(product.get("expires_at"))
            if expires is not None and now is not None:
                days = (expires - now).total_seconds() / 86400
                if days <= 0:
                    alerts.append(Alert(
                        severity="critical", title=f"{label} has expired{trial}",
                        detail=f"Expired on {expires.date().isoformat()}.", entity=f"expiry:{ref}",
                    ))
                elif days <= crit or days <= warn:
                    alerts.append(Alert(
                        severity="critical" if days <= crit else "warning",
                        # One title for both severities, so the incident escalates
                        # instead of a second one opening.
                        title=f"{label} expires soon{trial}",
                        detail=f"Expires on {expires.date().isoformat()} (in {int(days)} days).",
                        entity=f"expiry:{ref}",
                    ))

            if product.get("locked") is True:
                alerts.append(Alert(
                    severity="warning", title=f"{label} is locked",
                    detail="The product is locked at Infomaniak.", entity=f"locked:{ref}",
                ))
            if product.get("maintenance") is True:
                alerts.append(Alert(
                    severity="warning", title=f"{label} is in maintenance",
                    detail="The product reports an ongoing maintenance.", entity=f"maintenance:{ref}",
                ))
        return alerts

    def get_dashboard_summary(self, data: dict) -> dict | None:
        if not isinstance(data, dict) or not isinstance(data.get("products"), list):
            return None
        now = to_utc(data.get("collected_at"))
        soonest: float | None = None
        for p in data["products"]:
            exp = to_utc(p.get("expires_at")) if isinstance(p, dict) else None
            if exp is not None and now is not None:
                days = (exp - now).total_seconds() / 86400
                soonest = days if soonest is None else min(soonest, days)
        return {
            "products": len(data["products"]),
            "next_expiry_days": int(soonest) if soonest is not None else None,
        }
