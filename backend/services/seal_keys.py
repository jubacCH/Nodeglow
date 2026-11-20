"""The sealing key a probe reports with its heartbeat (trust on first use).

The first key a probe reports is trusted automatically; a different key later
is only recorded as reported until an admin trusts it.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
from datetime import datetime

from services.audit import log_action

SUITE = "hpke-x25519-sha256-chacha20poly1305"
_MAX_B64 = 64  # 32 bytes encode to 44 characters; anything longer is garbage


def _decode(public_key_b64) -> bytes | None:
    if not isinstance(public_key_b64, str) or not public_key_b64 or len(public_key_b64) > _MAX_B64:
        return None
    try:
        raw = base64.b64decode(public_key_b64, validate=True)
    except (binascii.Error, ValueError):
        return None
    return raw if len(raw) == 32 else None


def fingerprint(public_key_b64: str) -> str | None:
    raw = _decode(public_key_b64)
    if raw is None:
        return None
    return "SHA256:" + base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")


def parse_report(raw) -> str | None:
    """Canonical base64 public key of a ``seal`` report, or None if invalid."""
    if not isinstance(raw, dict) or raw.get("suite") != SUITE:
        return None
    key = _decode(raw.get("public_key"))
    return base64.b64encode(key).decode() if key is not None else None


def state(agent) -> dict:
    trusted, reported = agent.seal_public_key, agent.seal_key_reported
    if not trusted:
        status = "none"
    elif reported and reported != trusted:
        status = "changed"
    else:
        status = "trusted"
    return {
        "fingerprint": fingerprint(trusted) if trusted else None,
        # Public data: the browser seals to it, after checking it against the fingerprint.
        "public_key": trusted or None,
        "reported_fingerprint": fingerprint(reported) if reported else None,
        "state": status,
    }


def trust(agent, public_key: str, by: str | None) -> None:
    agent.seal_public_key = public_key
    agent.seal_key_trusted_at = datetime.utcnow()
    agent.seal_key_trusted_by = by


async def observe(db, agent, public_key: str | None) -> None:
    """Record a reported key; trust it when none is trusted yet.

    ``None`` (older agent, invalid report) leaves everything as it is."""
    if public_key is None:
        return
    agent.seal_key_reported = public_key
    if agent.seal_public_key is None:
        trust(agent, public_key, None)
        await log_action(db, None, "probe.seal_key.trust", "agent", agent.id, agent.name,
                         {"fingerprint": fingerprint(public_key), "automatic": True})
