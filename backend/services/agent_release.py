"""Release signatures of the agent binaries (spec 2026-10-11 rule 4, P2a).

CI (``.github/workflows/agent-build.yml``) signs every agent binary it builds
with the **release key** and commits ``<binary>.sig`` next to the binary in
``backend/static``. The private release key never reaches a Nodeglow server:
the server only *relays* the signature to agents, which verify it against the
release public keys compiled into them (``agent/src/release_keys.rs``). A
server therefore cannot make an agent that requires a release signature run
code CI did not sign.

``<binary>.sig`` (one line of JSON, written by tools/agent-release-key/sign.sh)::

    {"format": "nodeglow-agent-signature-v1", "alg": "ed25519",
     "platform": "linux", "key_id": "<16 hex>", "sha256": "<64 hex>",
     "signature": "<128 hex>"}

The signature is pure Ed25519 over :func:`release_message` — must match
``release_message`` in ``agent/src/updater.rs``.

The server-held key of :mod:`services.agent_signing` stays as the pre-P2a path
for single-tenant installs (agents with a local ``update_public_key``); in
multi-tenant mode agents ignore it and the installers no longer hand it out.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

SIGNATURE_FORMAT = "nodeglow-agent-signature-v1"
MESSAGE_PREFIX = "nodeglow-agent-binary:v1"

_HEX16 = re.compile(r"^[0-9a-f]{16}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX128 = re.compile(r"^[0-9a-f]{128}$")


def release_message(platform: str, sha256_hex: str) -> bytes:
    """The exact bytes the release key signs for a binary."""
    return f"{MESSAGE_PREFIX}:{platform}:{sha256_hex.lower()}".encode()


def key_id(public_key_raw: bytes) -> str:
    """Key id of a raw 32-byte Ed25519 public key: first 8 bytes of SHA-256, hex."""
    return hashlib.sha256(public_key_raw).hexdigest()[:16]


def signature_path(binary: Path) -> Path:
    return binary.with_name(binary.name + ".sig")


def load_release_signature(binary: Path, platform: str, sha256_hex: str) -> dict | None:
    """``{"key_id", "signature"}`` from ``<binary>.sig``, or None.

    None when there is no signature file (unsigned dev build, the Python
    fallback agent) or when it does not belong to exactly these bytes and this
    platform — a stale or mismatched file is never relayed, so agents fall back
    or refuse instead of failing on a signature that cannot verify.
    """
    path = signature_path(binary)
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("Agent release signature %s is unreadable: %s", path, exc)
        return None
    if not isinstance(doc, dict):
        log.warning("Agent release signature %s is not a JSON object", path)
        return None
    kid = str(doc.get("key_id", "")).lower()
    sig = str(doc.get("signature", "")).lower()
    sha = str(doc.get("sha256", "")).lower()
    if (doc.get("format") != SIGNATURE_FORMAT or doc.get("alg") != "ed25519"
            or not _HEX16.match(kid) or not _HEX128.match(sig) or not _HEX64.match(sha)):
        log.warning("Agent release signature %s has an unknown format", path)
        return None
    if doc.get("platform") != platform:
        log.warning("Agent release signature %s is for platform %r, not %r",
                    path, doc.get("platform"), platform)
        return None
    if sha != sha256_hex.lower():
        log.warning("Agent release signature %s is stale (it signs other bytes)", path)
        return None
    return {"key_id": kid, "signature": sig}


def multi_tenant_mode() -> bool:
    """True when this installation can hold more than one tenant.

    Decided by the build, not the licence or the current tenant count: the
    enterprise tenancy plugin registers the ``multi_tenancy`` feature. Agents
    latch this flag, so it is deliberately conservative.
    """
    from extensions import registry

    return bool(registry.features.get("multi_tenancy", False))
