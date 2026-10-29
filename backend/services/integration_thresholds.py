"""Per-tenant overrides of the integrations' alert limits.

Each integration declares its limits (``BaseIntegration.alert_thresholds``);
the tenant setting ``integration_alert_thresholds`` stores only the values a
tenant changed, as ``{"<type>.<key>": number}``. A value equal to the default
is not stored, so a later change of a default reaches every tenant that kept
it.
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)

SETTING = "integration_alert_thresholds"


def _declared() -> dict[str, tuple[str, object]]:
    """``"<type>.<key>"`` -> (integration display name, Threshold)."""
    from integrations import get_registry

    out: dict[str, tuple[str, object]] = {}
    for name, cls in sorted(get_registry().items()):
        for t in cls.alert_thresholds:
            out[f"{name}.{t.key}"] = (cls.display_name or name, t)
    return out


async def load(db) -> dict[str, float]:
    """The tenant's stored overrides (unknown or broken entries dropped)."""
    from database import get_setting

    raw = await get_setting(db, SETTING, "{}")
    try:
        stored = json.loads(raw or "{}")
    except ValueError:
        log.error("Setting %s is not valid JSON; using the defaults", SETTING)
        return {}
    declared = _declared()
    return {k: float(v) for k, v in (stored if isinstance(stored, dict) else {}).items()
            if k in declared and isinstance(v, (int, float)) and not isinstance(v, bool)}


async def overrides_for(db, integration_type: str) -> dict[str, float]:
    """``{key: value}`` of one integration type, for ``threshold_overrides``."""
    prefix = f"{integration_type}."
    return {k[len(prefix):]: v for k, v in (await load(db)).items() if k.startswith(prefix)}


async def catalogue(db, types: set[str] | None = None) -> list[dict]:
    """Every declared limit with its default and the tenant's value."""
    stored = await load(db)
    rows = []
    for full, (display, t) in _declared().items():
        integration_type = full.split(".", 1)[0]
        if types is not None and integration_type not in types:
            continue
        rows.append({
            "id": full, "integration": integration_type, "integration_label": display,
            "key": t.key, "label": t.label, "unit": t.unit, "default": t.default,
            "value": stored.get(full, t.default), "min": t.minimum, "max": t.maximum,
            "zero_disables": t.zero_disables,
        })
    return rows


def validate(values: dict) -> tuple[dict[str, float], list[str]]:
    """The overrides to store (defaults dropped) and the ids that were refused."""
    declared = _declared()
    keep: dict[str, float] = {}
    refused: list[str] = []
    for full, value in (values or {}).items():
        entry = declared.get(full)
        if entry is None or isinstance(value, bool) or not isinstance(value, (int, float)):
            refused.append(full)
            continue
        t = entry[1]
        if value < t.minimum or (t.maximum is not None and value > t.maximum):
            refused.append(full)
            continue
        if float(value) != float(t.default):
            keep[full] = float(value)
    return keep, refused


async def save(db, values: dict) -> list[str]:
    """Store the tenant's overrides; returns the refused ids (nothing stored then)."""
    from database import set_setting

    keep, refused = validate(values)
    if not refused:
        await set_setting(db, SETTING, json.dumps(keep, sort_keys=True))
    return refused
