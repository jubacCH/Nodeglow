"""General settings: /json read endpoint, /save form, phpIPAM, GeoIP."""
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db, get_setting, set_setting
from models.integration import IntegrationConfig
from notification_channels import DAILY_SUMMARY_DEFAULT_CHANNELS
from ratelimit import rate_limit
from services.audit import log_action
from services.channel_secrets import LEGACY_SECRET_KEYS
from services.tenancy import per_tenant_job

from ._helpers import is_operator, require_admin, require_operator

logger = logging.getLogger(__name__)


# Retention keys the general form writes.
_FORM_RETENTION_KEYS = ("ping_retention_days", "proxmox_retention_days",
                        "integration_retention_days", "incident_event_retention_days")


async def _retention_caps(db: AsyncSession) -> dict[str, int | None]:
    """The cap of every retention key the form writes: the installation's
    (``<key>_max``) and the tenant's plan (ee), whichever is lower. Raises
    ``tenant_jobs.RetentionPolicyError`` when the plan cannot be read —
    the form then saves nothing rather than a value above the plan."""
    from services import tenant_jobs

    return {k: await tenant_jobs.retention_cap(db, k, strict=True) for k in _FORM_RETENTION_KEYS}


def _capped(caps: dict[str, int | None], key: str, value: int) -> int:
    from services import tenant_jobs

    return tenant_jobs.apply_retention_cap(value, caps[key])

router = APIRouter()


@router.get("/json")
async def settings_json(request: Request, db: AsyncSession = Depends(get_db)):
    """Return current settings as JSON for the SPA frontend."""
    if err := require_admin(request):
        return err
    keys = [
        "site_name", "timezone", "ping_interval", "latency_threshold_ms",
        "agent_server_url",
        "proxmox_interval", "notify_enabled", "notify_grace_minutes",
        "telegram_chat_id",
        "smtp_host", "smtp_port", "smtp_user", "smtp_from", "smtp_to",
        "notify_telegram_min_severity", "notify_discord_min_severity",
        "notify_webhook_min_severity", "notify_email_min_severity",
        "public_url", "teams_enabled", "slack_enabled", "ntfy_enabled",
        "ntfy_server_url", "ntfy_topic",
        "notify_teams_min_severity", "notify_slack_min_severity", "notify_ntfy_min_severity",
        "ping_retention_days", "proxmox_retention_days", "integration_retention_days",
        "incident_event_retention_days",
        "anomaly_threshold", "proxmox_cpu_threshold", "proxmox_ram_threshold",
        "proxmox_disk_threshold", "syslog_port", "syslog_allowlist_only",
        "digest_enabled", "digest_day", "digest_hour",
        "daily_ai_summary_enabled", "daily_ai_summary_hour", "daily_ai_summary_channels",
        "geoip_enabled", "geoip_last_updated",
        "correlation_min_failures", "correlation_min_cycles",
        "predictor_min_confidence", "predictor_min_occurrences", "predictor_template_blacklist",
    ]
    result = {}
    for key in keys:
        result[key] = await get_setting(db, key, "")
    defaults = {
        "site_name": "NODEGLOW", "ping_interval": "60", "proxmox_interval": "60",
        "timezone": "UTC", "smtp_port": "587", "ping_retention_days": "30",
        "proxmox_retention_days": "7", "integration_retention_days": "7",
        "incident_event_retention_days": "30",
        "anomaly_threshold": "2.0", "proxmox_cpu_threshold": "85",
        "proxmox_ram_threshold": "85", "proxmox_disk_threshold": "90",
        "syslog_port": "1514",
        "notify_grace_minutes": "5",
        "correlation_min_failures": "3",
        "correlation_min_cycles": "2",
        "predictor_min_confidence": "0.85",
        "predictor_min_occurrences": "20",
        "digest_day": "0", "digest_hour": "9",
        "daily_ai_summary_hour": "8",
        "daily_ai_summary_channels": DAILY_SUMMARY_DEFAULT_CHANNELS,
        "ntfy_server_url": "https://ntfy.sh",
        "teams_enabled": "0", "slack_enabled": "0", "ntfy_enabled": "0",
    }
    for key, default in defaults.items():
        if not result.get(key):
            result[key] = default
    if not result.get("predictor_template_blacklist"):
        from services.predictor_config import DEFAULT_BLACKLIST_PATTERNS
        result["predictor_template_blacklist"] = json.dumps(DEFAULT_BLACKLIST_PATTERNS)
    result["smtp_has_pw"] = bool(await get_setting(db, "smtp_password", ""))
    # Legacy channel secrets never leave the server either: flags only.
    for key in LEGACY_SECRET_KEYS:
        result[f"{key}_has_value"] = bool(await get_setting(db, key, ""))
    # Teams/Slack webhook URLs and the ntfy token are secrets: flags only.
    result["teams_has_url"] = bool(await get_setting(db, "teams_webhook_url", ""))
    result["slack_has_url"] = bool(await get_setting(db, "slack_webhook_url", ""))
    result["ntfy_has_token"] = bool(await get_setting(db, "ntfy_token", ""))
    result["claude_has_key"] = bool(await get_setting(db, "claude_api_key", ""))
    result["geoip_has_key"] = bool(await get_setting(db, "geoip_license_key", ""))
    ldap_keys = [
        "ldap_enabled", "ldap_server", "ldap_bind_dn", "ldap_base_dn",
        "ldap_user_filter", "ldap_display_attr", "ldap_group_attr",
        "ldap_admin_group", "ldap_editor_group", "ldap_use_ssl", "ldap_start_tls",
        "ldap_tls_verify",
    ]
    ldap_defaults = {
        "ldap_tls_verify": "1",
        "ldap_user_filter": "(&(objectClass=person)(sAMAccountName={username}))",
        "ldap_display_attr": "displayName",
        "ldap_group_attr": "memberOf",
    }
    for key in ldap_keys:
        result[key] = await get_setting(db, key, ldap_defaults.get(key, ""))
    result["ldap_has_bind_pw"] = bool(await get_setting(db, "ldap_bind_password", ""))
    # The highest value the form may offer per retention key (installation
    # cap and, ee, the tenant's plan tier); absent = no cap.
    from services import tenant_jobs
    from services.settings_registry import RETENTION_KEYS
    caps = {}
    for key in RETENTION_KEYS:
        # Display only (strict=False): the save refuses on its own when the
        # plan cannot be read.
        cap = await tenant_jobs.retention_cap(db, key, strict=False)
        if cap is not None:
            caps[key] = cap
    result["retention_caps"] = caps
    return JSONResponse(result)


@router.post("/save")
@rate_limit(max_requests=10, window_seconds=60)
async def save_settings(
    request: Request,
    site_name:          str = Form("NODEGLOW"),
    agent_server_url:   str = Form(""),
    ping_interval:      str = Form("60"),
    proxmox_interval:   str = Form("60"),
    ping_retention:     str = Form("30"),
    proxmox_retention:  str = Form("7"),
    anomaly_threshold:  str = Form("2.0"),
    timezone:           str = Form("UTC"),
    latency_threshold:  str = Form(""),
    cpu_threshold:      str = Form("85"),
    ram_threshold:      str = Form("85"),
    disk_threshold:     str = Form("90"),
    integration_retention: str = Form("7"),
    incident_event_retention: str = Form("30"),
    syslog_port:        str = Form("1514"),
    syslog_allowlist_only: str = Form("0"),
    predictor_min_confidence: str = Form(""),
    predictor_min_occurrences: str = Form(""),
    predictor_template_blacklist: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    if err := require_admin(request):
        return err
    # Before anything is written: a plan that cannot be read refuses the
    # whole save (fail closed) instead of saving retention above the plan.
    from services import tenant_jobs
    try:
        retention_caps = await _retention_caps(db)
    except tenant_jobs.RetentionPolicyError:
        logger.warning("Settings save refused: the tenant's retention plan could not be read",
                       exc_info=True)
        return JSONResponse({"error": "The retention plan of this tenant could not be read; "
                                      "nothing was saved. Try again later.",
                             "code": "retention_policy_unavailable"}, status_code=503)
    await set_setting(db, "site_name", site_name.strip())
    # site_name is the tenant's display name.
    from models.tenant import Tenant
    from services.tenancy import current_tenant_id
    tenant = await db.get(Tenant, current_tenant_id())
    if tenant is not None and site_name.strip():
        tenant.name = site_name.strip()[:128]
    operator = is_operator(request)
    # Installation keys (the URL agents call, the syslog listener) are the
    # operator's; in the core that is the admin of the one tenant, so this
    # form saves exactly what it always did.
    if operator:
        # The address agents call back on. Needed wherever the proxy in front of
        # this app does not pass a usable X-Forwarded-Host, and as an override
        # when agents must reach the server on a different name than operators do.
        await set_setting(db, "agent_server_url", agent_server_url.strip().rstrip("/"))

    # Intervals: the tenant's choice, never below the installation floor
    # (ping_interval_min / proxmox_interval_min, default 10 s).
    p_interval = tenant_jobs.clamp_interval(
        ping_interval, await tenant_jobs.interval_floor(db, tenant_jobs.PING_CADENCE),
        tenant_jobs.PING_CADENCE)
    await set_setting(db, "ping_interval", str(p_interval))

    px_interval = tenant_jobs.clamp_interval(
        proxmox_interval, await tenant_jobs.interval_floor(db, tenant_jobs.INTEGRATION_CADENCE),
        tenant_jobs.INTEGRATION_CADENCE)
    await set_setting(db, "proxmox_interval", str(px_interval))

    # Retention: within the installation's cap (<key>_max), if one is set.
    try:
        # ClickHouse keeps ping_checks 30 days (table TTL): more cannot be
        # honoured, so it is not offered.
        p_ret = max(1, min(30, int(ping_retention)))
    except ValueError:
        p_ret = 30
    await set_setting(db, "ping_retention_days", str(_capped(retention_caps, "ping_retention_days", p_ret)))

    try:
        px_ret = max(1, min(90, int(proxmox_retention)))
    except ValueError:
        px_ret = 7
    await set_setting(db, "proxmox_retention_days",
                      str(_capped(retention_caps, "proxmox_retention_days", px_ret)))

    try:
        threshold = max(1.5, min(10.0, float(anomaly_threshold)))
    except ValueError:
        threshold = 2.0
    await set_setting(db, "anomaly_threshold", str(threshold))

    await set_setting(db, "timezone", timezone.strip() or "UTC")

    if latency_threshold.strip():
        try:
            lt = max(1, int(float(latency_threshold)))
            await set_setting(db, "latency_threshold_ms", str(lt))
        except ValueError:
            pass
    else:
        await set_setting(db, "latency_threshold_ms", "")

    try:
        int_ret = max(1, min(90, int(integration_retention)))
    except ValueError:
        int_ret = 7
    await set_setting(db, "integration_retention_days",
                      str(_capped(retention_caps, "integration_retention_days", int_ret)))

    # 0 disables incident event pruning entirely.
    try:
        ev_ret = max(0, min(365, int(incident_event_retention)))
    except ValueError:
        ev_ret = 30
    await set_setting(db, "incident_event_retention_days",
                      str(_capped(retention_caps, "incident_event_retention_days", ev_ret)))

    for key, val, lo, hi, default in [
        ("proxmox_cpu_threshold",  cpu_threshold,  1, 100, 85),
        ("proxmox_ram_threshold",  ram_threshold,  1, 100, 85),
        ("proxmox_disk_threshold", disk_threshold, 1, 100, 90),
    ]:
        try:
            pct = max(lo, min(hi, int(float(val))))
        except ValueError:
            pct = default
        await set_setting(db, key, str(pct))

    old_syslog_port = await get_setting(db, "syslog_port", "1514")
    try:
        new_sp = max(1, min(65535, int(syslog_port)))
    except ValueError:
        new_sp = 1514
    if not operator:
        new_sp = int(old_syslog_port or 1514)
    else:
        await set_setting(db, "syslog_port", str(new_sp))
    await set_setting(db, "syslog_allowlist_only", "1" if syslog_allowlist_only == "1" else "0")

    if str(new_sp) != old_syslog_port:
        try:
            from services.syslog import stop_syslog_server, start_syslog_server
            await stop_syslog_server()
            await start_syslog_server(udp_port=new_sp, tcp_port=new_sp)
        except Exception:
            pass

    # The jobs tick at the fastest tenant's interval; this tenant's change
    # may move the tick either way. (set_setting has committed, so the
    # cadence query — its own session — sees the new values.)
    from scheduler import reschedule_cadence_jobs
    try:
        await reschedule_cadence_jobs()
    except Exception:
        logger.warning("Could not reschedule the ping/integration jobs", exc_info=True)

    if predictor_min_confidence:
        try:
            val = float(predictor_min_confidence)
            if 0.0 < val <= 1.0:
                await set_setting(db, "predictor_min_confidence", str(val))
        except ValueError:
            pass
    if predictor_min_occurrences:
        try:
            val = int(predictor_min_occurrences)
            if val >= 1:
                await set_setting(db, "predictor_min_occurrences", str(val))
        except ValueError:
            pass
    if predictor_template_blacklist:
        try:
            parsed = json.loads(predictor_template_blacklist)
            if isinstance(parsed, list) and all(isinstance(p, str) for p in parsed):
                await set_setting(db, "predictor_template_blacklist", predictor_template_blacklist)
        except json.JSONDecodeError:
            pass

    from main import invalidate_settings_cache
    invalidate_settings_cache()

    await log_action(db, request, "settings.update")
    await db.commit()

    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        return JSONResponse({"ok": True})
    return RedirectResponse(url="/settings?saved=1", status_code=303)


# ── phpIPAM ───────────────────────────────────────────────────────────────────

@router.post("/phpipam/save")
@rate_limit(max_requests=10, window_seconds=60)
async def save_phpipam(
    request: Request,
    phpipam_url:        str = Form(""),
    phpipam_app_id:     str = Form(""),
    phpipam_username:   str = Form(""),
    phpipam_password:   str = Form(""),
    phpipam_verify_ssl: str = Form("0"),
    phpipam_sync_hours: str = Form("0"),
    db: AsyncSession = Depends(get_db),
):
    if err := require_admin(request):
        return err
    await set_setting(db, "phpipam_url", phpipam_url.strip().rstrip("/"))
    await set_setting(db, "phpipam_app_id", phpipam_app_id.strip())
    await set_setting(db, "phpipam_username", phpipam_username.strip())
    await set_setting(db, "phpipam_verify_ssl", "1" if phpipam_verify_ssl == "on" else "0")

    if phpipam_password.strip():
        from services.tenant_keys import encrypt_secret
        await set_setting(db, "phpipam_password", await encrypt_secret(db, phpipam_password.strip()))

    try:
        sync_h = max(0, min(168, int(phpipam_sync_hours)))
    except ValueError:
        sync_h = 0
    await set_setting(db, "phpipam_sync_hours", str(sync_h))

    from scheduler import scheduler
    job = scheduler.get_job("phpipam_sync")
    if sync_h > 0:
        if job:
            job.reschedule(trigger="interval", hours=sync_h)
        else:
            scheduler.add_job(run_phpipam_sync, "interval", hours=sync_h, id="phpipam_sync",
                              replace_existing=True)
    else:
        if job:
            job.remove()

    return RedirectResponse(url="/settings?saved=1", status_code=303)


async def sync_phpipam_config(db: AsyncSession, cfg) -> dict:
    """Host import for one phpIPAM config row (the sync button and the
    scheduled sync).

    A config the core collects is fetched live, as before. A probe-collected
    one is applied from its latest successful snapshot: the backend neither
    reaches the probe's network nor needs the config's secrets (probe-side
    integrations, rule 2); an on-demand collect through the probe comes with
    the command channel."""
    import json

    from integrations.phpipam import apply_phpipam_hosts, sync_phpipam_hosts
    from models.integration import COLLECTOR_CORE
    from services import integration as int_svc
    from services import snapshot as snap_svc

    if (cfg.collector or COLLECTOR_CORE) == COLLECTOR_CORE:
        return await sync_phpipam_hosts(db, await int_svc.decrypt_config(db, cfg))

    snap = await snap_svc.get_latest_data(db, "phpipam", cfg.id)
    if snap is None:
        return {"added": 0, "merged": 0, "skipped": 0,
                "errors": ["No phpIPAM data from the probe yet"]}
    data = json.loads(snap.data_json)
    return await apply_phpipam_hosts(db, data, data.get("source_url") or cfg.name)


@per_tenant_job
async def run_phpipam_sync():
    """Scheduled phpIPAM import: every tenant's phpIPAM integrations.

    Declares its tenancy like the jobs in scheduler.py — scheduled jobs start
    without any context (scheduler.ContextFreeExecutor)."""
    from database import AsyncSessionLocal

    async with AsyncSessionLocal() as _db:
        cfgs = (await _db.execute(
            select(IntegrationConfig).where(IntegrationConfig.type == "phpipam")
        )).scalars().all()
        for cfg in cfgs:
            await sync_phpipam_config(_db, cfg)


@router.post("/phpipam/sync")
@rate_limit(max_requests=10, window_seconds=60)
async def manual_phpipam_sync(request: Request, db: AsyncSession = Depends(get_db)):
    """Trigger a manual phpIPAM host import. Returns JSON result."""
    if err := require_admin(request):
        return err
    cfgs = (await db.execute(
        select(IntegrationConfig).where(IntegrationConfig.type == "phpipam")
    )).scalars().all()
    results = []
    for cfg in cfgs:
        # Core configs are fetched live; probe configs from their snapshot.
        results.append(await sync_phpipam_config(db, cfg))
    return JSONResponse(results[0] if len(results) == 1 else {"synced": results})


# ── GeoIP ─────────────────────────────────────────────────────────────────────

@router.post("/geoip/download")
@rate_limit(max_requests=10, window_seconds=60)
async def geoip_download(request: Request, db: AsyncSession = Depends(get_db)):
    """Trigger a manual GeoLite2-City database download. Operator only (one
    GeoIP database for the installation; in the core: the admin)."""
    if err := require_operator(request):
        return err

    from database import decrypt_value
    from services.geoip_updater import download_geolite2

    key_enc = await get_setting(db, "geoip_license_key", "")
    if not key_enc:
        return JSONResponse({"success": False, "message": "No GeoIP license key configured"}, status_code=400)

    try:
        license_key = decrypt_value(key_enc)
    except Exception:
        license_key = key_enc

    result = await download_geolite2(license_key)

    if result["success"]:
        await set_setting(db, "geoip_last_updated", datetime.utcnow().isoformat())
        await db.commit()

    status_code = 200 if result["success"] else 500
    return JSONResponse(result, status_code=status_code)
