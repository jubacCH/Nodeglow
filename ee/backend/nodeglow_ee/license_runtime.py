"""The license inside the running backend: source, cache, feature gates, settings API.

Where the license comes from (first match wins):

1. ``NODEGLOW_LICENSE`` — the license key itself (base64 or JSON) or a path
   to a file containing it. Set by the operator; the settings page shows it
   read-only.
2. The ``ee_license`` row of the ``settings`` table, written by an admin via
   ``POST /settings/license`` (audit logged).

The plugin always registers (routers, hooks, coordinator); each feature asks
this module at the moment it is used. So installing, replacing or removing a
license takes effect without a restart — except HA leader election, which is
decided when the scheduler starts.

Behaviour per state (see ``licensing.LicenseStatus.feature_mode``):

* missing / invalid — enterprise features inactive: endpoints answer 402,
  scheduled jobs skip, ``/api/v2/features`` reports every flag false.
* valid / grace (14 days after ``expires_at``) — features per license list.
* expired (after the grace period) — nothing is deleted and monitoring keeps
  running; enterprise actions are refused (402), data they produced earlier
  stays readable through the core (e.g. stored postmortems). HA leader
  election keeps running (``licensing.EXPIRY_EXEMPT``).

Several backend processes each cache the license for :data:`CACHE_TTL`
seconds, so an upload reaches the other workers within that time.
"""
from __future__ import annotations

import logging
import os
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from routers.settings._helpers import require_admin

from nodeglow_ee import licensing
from nodeglow_ee.licensing import (
    MODE_FULL,
    STATUS_EXPIRED,
    STATUS_GRACE,
    STATUS_INVALID,
    STATUS_MISSING,
    LicenseStatus,
)

log = logging.getLogger("nodeglow.ee.license")

LICENSE_ENV = "NODEGLOW_LICENSE"
SETTING_KEY = "ee_license"
INSTALL_ID_KEY = "ee_install_id"
CACHE_TTL = 60.0

SOURCE_ENV = "environment"
SOURCE_SETTINGS = "settings"

FEATURE_LABELS = {
    "ha_scheduler": "HA scheduler leader election",
    "ai_assistant": "Glow",
    "ai_postmortem": "AI postmortems",
    "ai_daily_summary": "the AI daily summary",
}


def _env_license() -> str | None:
    """The license from ``NODEGLOW_LICENSE``: the key itself or a file path."""
    value = os.environ.get(LICENSE_ENV, "").strip()
    if not value:
        return None
    if not value.startswith("{") and len(value) < 1024:
        try:
            path = Path(value).expanduser()
            if path.is_file():
                return path.read_text(encoding="utf-8")
        except OSError:
            pass
    return value


class LicenseManager:
    """Process-wide license cache. One instance: :data:`manager`."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._text: str | None = None
        self._source: str | None = None
        self._install_id: str | None = None
        self._loaded_at: float | None = None
        self._last_status: str | None = None

    async def _read_db(self) -> tuple[str | None, str | None]:
        import database  # at call time: tests patch database.AsyncSessionLocal

        async with database.AsyncSessionLocal() as db:
            text = await database.get_setting(db, SETTING_KEY, None)
            install_id = await database.get_setting(db, INSTALL_ID_KEY, None)
        return text, install_id

    async def refresh(self) -> LicenseStatus:
        """Re-read the license sources now."""
        env_text = _env_license()
        try:
            db_text, install_id = await self._read_db()
        except Exception as exc:
            # Settings not readable (DB starting up, transient error): keep
            # the license read last time instead of dropping a valid one.
            log.debug("License: settings not readable (%s)", exc)
            if env_text:
                self._text, self._source = env_text, SOURCE_ENV
        else:
            self._install_id = install_id
            if env_text:
                self._text, self._source = env_text, SOURCE_ENV
            else:
                self._text, self._source = db_text, (SOURCE_SETTINGS if db_text else None)
        self._loaded_at = time.monotonic()
        status = self.evaluate()
        self._log_transition(status)
        return status

    def evaluate(self) -> LicenseStatus:
        return licensing.evaluate(self._text, source=self._source, install_id=self._install_id)

    async def current(self) -> LicenseStatus:
        """The status, re-reading the sources at most every :data:`CACHE_TTL` seconds."""
        if self._loaded_at is None or time.monotonic() - self._loaded_at > CACHE_TTL:
            return await self.refresh()
        return self.evaluate()

    def cached(self) -> LicenseStatus:
        """The status from the last read, without database I/O (for synchronous
        callers). Before the first read only ``NODEGLOW_LICENSE`` is known."""
        if self._loaded_at is None:
            env_text = _env_license()
            if env_text:
                return licensing.evaluate(env_text, source=SOURCE_ENV, install_id=self._install_id)
        return self.evaluate()

    def install(self, text: str | None, source: str | None) -> LicenseStatus:
        """Use ``text`` right away (after an upload / removal in this process)."""
        self._text, self._source = text, source
        self._loaded_at = time.monotonic()
        status = self.evaluate()
        self._log_transition(status)
        return status

    @property
    def install_id(self) -> str | None:
        return self._install_id

    def _log_transition(self, status: LicenseStatus) -> None:
        if status.status == self._last_status:
            return
        self._last_status = status.status
        if status.status in (STATUS_MISSING, STATUS_INVALID, STATUS_EXPIRED, STATUS_GRACE):
            log.warning("Nodeglow Enterprise license: %s — %s", status.status, status.message())
        else:
            log.info("Nodeglow Enterprise license: %s — %s", status.status, status.message())


manager = LicenseManager()


# ── Gates used by the features ────────────────────────────────────────────────


async def is_active(feature: str) -> bool:
    return (await manager.current()).feature_active(feature)


def _block_code(status: LicenseStatus, feature: str) -> str:
    if status.status in (STATUS_MISSING, STATUS_INVALID):
        return f"license_{status.status}"
    if status.license is not None and not status.license.grants(feature):
        return "feature_not_licensed"
    return "license_expired"


def block_message(status: LicenseStatus, feature: str) -> str:
    label = FEATURE_LABELS.get(feature, feature)
    code = _block_code(status, feature)
    if code == "license_missing":
        return (f"{label[0].upper()}{label[1:]} needs a Nodeglow Enterprise license. "
                "An admin can install one in Settings → License.")
    if code == "license_invalid":
        return (f"The installed license cannot be used ({status.error}), so {label} is inactive. "
                "An admin can install a valid one in Settings → License.")
    if code == "feature_not_licensed":
        return f"The installed license does not include {label}."
    return (f"The Nodeglow Enterprise license has expired, so {label} is blocked. Monitoring and "
            "existing data are not affected. An admin can install a new license in Settings → License.")


async def blocked(feature: str) -> JSONResponse | None:
    """``None`` if ``feature`` may be used now, else the 402 answer for the endpoint."""
    status = await manager.current()
    if status.feature_mode(feature) == MODE_FULL:
        return None
    return JSONResponse(
        {"error": block_message(status, feature), "code": _block_code(status, feature),
         "license_status": status.status},
        status_code=402,
    )


async def license_provider() -> dict:
    """Registered as ``extensions.registry.license_provider``."""
    status = await manager.current()
    return {
        "license": status.public(),
        "active": {name: status.feature_active(name) for name in licensing.KNOWN_FEATURES},
    }


async def on_scheduler_start(_scheduler) -> None:
    """Scheduler hook: read the license before the coordinator decides on HA."""
    await manager.refresh()


# ── Settings API (admin) ──────────────────────────────────────────────────────

router = APIRouter(prefix="/settings", tags=["Settings"])


async def _ensure_install_id(db: AsyncSession) -> str:
    from database import get_setting, set_setting

    install_id = await get_setting(db, INSTALL_ID_KEY, None)
    if not install_id:
        install_id = f"ngi_{secrets.token_hex(12)}"
        await set_setting(db, INSTALL_ID_KEY, install_id)
    manager._install_id = install_id
    return install_id


def _detail(status: LicenseStatus, install_id: str | None) -> dict:
    data = status.detail()
    data["install_id"] = install_id
    data["managed_by_env"] = bool(_env_license())
    data["env_var"] = LICENSE_ENV
    return data


def _env_conflict() -> JSONResponse:
    return JSONResponse(
        {"error": f"The license is set by the {LICENSE_ENV} environment variable. "
                  "Change it there and restart the backend."},
        status_code=409,
    )


@router.get("/license", summary="License status (admin)")
async def get_license(request: Request, db: AsyncSession = Depends(get_db)):
    if err := require_admin(request):
        return err
    install_id = await _ensure_install_id(db)
    status = await manager.refresh()
    return _detail(status, install_id)


@router.post("/license", summary="Install or replace the license (admin)")
async def upload_license(request: Request, db: AsyncSession = Depends(get_db)):
    if err := require_admin(request):
        return err
    if _env_license():
        return _env_conflict()
    try:
        body = await request.json()
    except Exception:
        body = None
    text = (body or {}).get("license") if isinstance(body, dict) else None
    if not isinstance(text, str) or not text.strip():
        return JSONResponse({"error": "Paste the license key."}, status_code=400)
    text = text.strip()

    install_id = await _ensure_install_id(db)
    status = licensing.evaluate(text, source=SOURCE_SETTINGS, install_id=install_id)
    if status.status == STATUS_INVALID:
        return JSONResponse({"error": f"This license cannot be used: {status.error}.",
                             "code": "license_invalid"}, status_code=400)

    from database import set_setting
    from services.audit import log_action

    lic = status.license
    await log_action(db, request, "license.install", "license", None, lic.customer, details={
        "license_id": lic.license_id, "expires_at": status.public()["expires_at"],
        "features": lic.granted_features(), "status": status.status,
    })
    await set_setting(db, SETTING_KEY, text)  # commits the audit entry too
    return _detail(manager.install(text, SOURCE_SETTINGS), install_id)


@router.delete("/license", summary="Remove the license (admin)")
async def remove_license(request: Request, db: AsyncSession = Depends(get_db)):
    if err := require_admin(request):
        return err
    if _env_license():
        return _env_conflict()
    from database import Setting
    from services.audit import log_action

    row = await db.get(Setting, SETTING_KEY)
    lic = None
    if row is not None:
        lic = licensing.evaluate(row.value, install_id=manager.install_id).license
        await db.delete(row)
    await log_action(db, request, "license.remove", "license", None, lic.customer if lic else None,
                     details={"license_id": lic.license_id} if lic else None)
    await db.commit()
    install_id = await _ensure_install_id(db)
    return _detail(manager.install(None, None), install_id)


def register(registry) -> None:
    registry.set_license_provider(license_provider)
    registry.add_router(router)
    # First hook: the license is known before other hooks and the HA
    # coordinator run.
    registry.add_scheduler_hook(on_scheduler_start)
