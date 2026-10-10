"""Offline license keys for Nodeglow Enterprise.

A license is a small JSON document signed with Ed25519 (the same primitive as
agent update signing, ``services/agent_signing.py``). It is verified offline
against the public keys embedded below — on-prem installations may be
air-gapped, so there is no phone-home.

Wire format (``nodeglow-license/1``)::

    {
      "format":    "nodeglow-license/1",
      "kid":       "<id of the signing key>",
      "payload":   "<base64 of the payload JSON bytes>",
      "signature": "<base64 of the 64-byte Ed25519 signature>"
    }

The signature covers ``b"nodeglow-license/1\\n" + kid + b"\\n" + payload_bytes``,
so the key id cannot be swapped and the payload is verified byte for byte (no
JSON canonicalisation). The whole envelope is usually passed around base64
encoded on one line ("the license key"); :func:`decode_license_text` accepts
the JSON or its base64 form.

Payload fields: ``license_id``, ``customer``, ``edition`` (``"enterprise"``),
``features`` (list of feature names, ``"*"`` = every enterprise feature,
including future ones), ``max_tenants``, ``issued_at``, ``expires_at`` (ISO
8601, UTC) and optional ``install_id`` (binds the license to one installation;
off unless the issuer sets it).

Key rotation: :data:`TRUSTED_KEYS` maps key ids to public keys. A new key is
added next to the old one; licenses signed with the old key keep working until
its id is removed in a later release. There is deliberately no way to add a
trusted key at runtime (env, settings): that would let anyone sign their own
license.

This module only needs ``cryptography`` and the standard library, so the CLI
works outside the backend process::

    python -m nodeglow_ee.licensing keygen --out-dir DIR [--kid ID]
    python -m nodeglow_ee.licensing issue --signing-key FILE --kid ID \\
        --customer NAME --expires 2036-12-31 [--features all|a,b] [--max-tenants N] \\
        [--install-id ID] [--out FILE]
    python -m nodeglow_ee.licensing verify FILE_OR_KEY

The private key never belongs in the repository. ``issue`` reads it from
``--signing-key`` or the ``NODEGLOW_LICENSE_SIGNING_KEY`` env var (PEM text or
a path to a PEM file).
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

FORMAT = "nodeglow-license/1"
EDITION = "enterprise"
GRACE_DAYS = 14
SIGNING_KEY_ENV = "NODEGLOW_LICENSE_SIGNING_KEY"

# Trusted public keys: key id → hex-encoded raw 32-byte Ed25519 public key.
# Add new keys here for rotation; remove an id only when every license signed
# with it has been replaced.
TRUSTED_KEYS: dict[str, str] = {
    "ng-2026-10": "a4417c3021f213a4c8d3395f21a7954444eed2202a3ac14f9a21a3df7eef8ab7",
}

# Every enterprise feature a license can grant (the names of the flags in
# GET /api/v2/features). "*" in a license grants all of them, including
# features added after the license was issued.
KNOWN_FEATURES: tuple[str, ...] = ("ha_scheduler", "ai_assistant", "ai_postmortem", "ai_daily_summary")

# Features that keep running after the grace period. Leader election protects
# monitoring itself (without it every replica would run every job), and an
# expired license must never break monitoring.
EXPIRY_EXEMPT: frozenset[str] = frozenset({"ha_scheduler"})

STATUS_MISSING = "missing"
STATUS_INVALID = "invalid"
STATUS_VALID = "valid"
STATUS_GRACE = "grace"
STATUS_EXPIRED = "expired"

MODE_FULL = "full"
MODE_READ_ONLY = "read_only"
MODE_OFF = "off"


class LicenseError(ValueError):
    """A license that cannot be used: malformed, unsigned, unknown key, tampered."""


# ── Data model ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class License:
    license_id: str
    customer: str
    edition: str
    features: tuple[str, ...]
    max_tenants: int | None
    issued_at: datetime
    expires_at: datetime
    install_id: str | None
    kid: str

    def grants(self, feature: str) -> bool:
        return "*" in self.features or feature in self.features

    @property
    def grace_until(self) -> datetime:
        return self.expires_at + timedelta(days=GRACE_DAYS)

    def granted_features(self) -> list[str]:
        if "*" in self.features:
            return list(KNOWN_FEATURES)
        return [f for f in self.features if f != "*"]


@dataclass
class LicenseStatus:
    """The license state of this installation at one point in time."""

    status: str
    license: License | None = None
    source: str | None = None  # "environment" | "settings"
    error: str | None = None
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def days_left(self) -> int | None:
        if self.license is None:
            return None
        return (self.license.expires_at - self.now).days

    def feature_mode(self, feature: str) -> str:
        """``full`` (usable), ``read_only`` (existing data visible) or ``off``."""
        lic = self.license
        if lic is None or self.status in (STATUS_MISSING, STATUS_INVALID) or not lic.grants(feature):
            return MODE_OFF
        if self.status in (STATUS_VALID, STATUS_GRACE) or feature in EXPIRY_EXEMPT:
            return MODE_FULL
        return MODE_READ_ONLY

    def feature_active(self, feature: str) -> bool:
        return self.feature_mode(feature) == MODE_FULL

    def message(self) -> str:
        lic = self.license
        if self.status == STATUS_MISSING:
            return "No license installed: enterprise features are inactive. Monitoring is not affected."
        if self.status == STATUS_INVALID:
            return (f"The installed license cannot be used ({self.error}). "
                    "Enterprise features are inactive. Monitoring is not affected.")
        assert lic is not None
        if self.status == STATUS_GRACE:
            return (f"The license expired on {lic.expires_at.date().isoformat()}. Enterprise features "
                    f"keep working until {lic.grace_until.date().isoformat()}; renew it before then.")
        if self.status == STATUS_EXPIRED:
            return ("The license has expired. Monitoring keeps running and existing data stays "
                    "visible, but enterprise features are blocked until a new license is installed.")
        return f"Licensed to {lic.customer} until {lic.expires_at.date().isoformat()}."

    def public(self) -> dict:
        """Summary safe for every signed-in user (GET /api/v2/features)."""
        lic = self.license
        return {
            "status": self.status,
            "message": self.message(),
            "expires_at": _iso(lic.expires_at) if lic else None,
            "grace_until": _iso(lic.grace_until) if lic else None,
            "days_left": self.days_left,
        }

    def detail(self) -> dict:
        """Everything the settings page shows (admins only)."""
        lic = self.license
        data = self.public()
        data.update({
            "edition": EDITION,
            "source": self.source,
            "error": self.error,
            "license_id": lic.license_id if lic else None,
            "customer": lic.customer if lic else None,
            "features": lic.granted_features() if lic else [],
            "all_features": bool(lic and "*" in lic.features),
            "max_tenants": lic.max_tenants if lic else None,
            "issued_at": _iso(lic.issued_at) if lic else None,
            "install_id_bound": bool(lic and lic.install_id),
            "grace_days": GRACE_DAYS,
        })
        return data


# ── Encoding helpers ──────────────────────────────────────────────────────────


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(value, name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise LicenseError(f"{name} is missing")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LicenseError(f"{name} is not an ISO 8601 time") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _signed_message(kid: str, payload: bytes) -> bytes:
    return FORMAT.encode() + b"\n" + kid.encode() + b"\n" + payload


def _b64decode(value: str, what: str) -> bytes:
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
        raise LicenseError(f"{what} is not valid base64") from exc


def decode_license_text(text: str) -> dict:
    """The envelope dict from the JSON text or its (urlsafe) base64 form."""
    text = (text or "").strip()
    if not text:
        raise LicenseError("the license is empty")
    if not text.startswith("{"):
        compact = "".join(text.split())
        try:
            text = base64.b64decode(compact + "=" * (-len(compact) % 4),
                                    altchars=b"-_" if ("-" in compact or "_" in compact) else None,
                                    validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
            raise LicenseError("the license is neither JSON nor base64") from exc
    try:
        envelope = json.loads(text)
    except ValueError as exc:
        raise LicenseError("the license is not valid JSON") from exc
    if not isinstance(envelope, dict):
        raise LicenseError("the license is not a JSON object")
    return envelope


def _public_key(kid: str, trusted: dict[str, str]) -> Ed25519PublicKey:
    hex_key = trusted.get(kid)
    if not hex_key:
        raise LicenseError(f"unknown signing key {kid!r}")
    return Ed25519PublicKey.from_public_bytes(bytes.fromhex(hex_key))


# ── Verify ────────────────────────────────────────────────────────────────────


def verify_license(text: str, *, trusted_keys: dict[str, str] | None = None,
                   install_id: str | None = None) -> License:
    """Parse and verify a license; raises :class:`LicenseError` if unusable.

    Expiry is *not* an error here: an expired license is still authentic, and
    :func:`evaluate` turns it into the grace / expired state.
    """
    trusted = TRUSTED_KEYS if trusted_keys is None else trusted_keys
    envelope = decode_license_text(text)
    if envelope.get("format") != FORMAT:
        raise LicenseError("unsupported license format")
    kid = envelope.get("kid")
    if not isinstance(kid, str) or not kid:
        raise LicenseError("the license has no key id")
    payload_b64, sig_b64 = envelope.get("payload"), envelope.get("signature")
    if not isinstance(payload_b64, str) or not isinstance(sig_b64, str):
        raise LicenseError("the license has no payload or signature")
    payload = _b64decode(payload_b64, "payload")
    signature = _b64decode(sig_b64, "signature")

    try:
        _public_key(kid, trusted).verify(signature, _signed_message(kid, payload))
    except InvalidSignature as exc:
        raise LicenseError("the signature is not valid") from exc

    try:
        data = json.loads(payload)
    except ValueError as exc:
        raise LicenseError("the payload is not valid JSON") from exc
    if not isinstance(data, dict):
        raise LicenseError("the payload is not a JSON object")
    if data.get("edition") != EDITION:
        raise LicenseError(f"the license is for edition {data.get('edition')!r}")
    features = data.get("features")
    if not isinstance(features, list) or not all(isinstance(f, str) for f in features):
        raise LicenseError("the feature list is malformed")
    max_tenants = data.get("max_tenants")
    if max_tenants is not None and (not isinstance(max_tenants, int) or max_tenants < 1):
        raise LicenseError("max_tenants is malformed")

    lic = License(
        license_id=str(data.get("license_id") or ""),
        customer=str(data.get("customer") or ""),
        edition=EDITION,
        features=tuple(features),
        max_tenants=max_tenants,
        issued_at=_parse_time(data.get("issued_at"), "issued_at"),
        expires_at=_parse_time(data.get("expires_at"), "expires_at"),
        install_id=data.get("install_id") or None,
        kid=kid,
    )
    if not lic.license_id or not lic.customer:
        raise LicenseError("license_id or customer is missing")
    if lic.install_id and lic.install_id != install_id:
        raise LicenseError("the license is bound to a different installation")
    return lic


def evaluate(text: str | None, *, source: str | None = None, now: datetime | None = None,
             trusted_keys: dict[str, str] | None = None, install_id: str | None = None) -> LicenseStatus:
    """The :class:`LicenseStatus` for a license text (``None`` = no license)."""
    now = now or datetime.now(timezone.utc)
    if not (text or "").strip():
        return LicenseStatus(STATUS_MISSING, now=now)
    try:
        lic = verify_license(text, trusted_keys=trusted_keys, install_id=install_id)
    except LicenseError as exc:
        return LicenseStatus(STATUS_INVALID, source=source, error=str(exc), now=now)
    if now <= lic.expires_at:
        status = STATUS_VALID
    elif now <= lic.grace_until:
        status = STATUS_GRACE
    else:
        status = STATUS_EXPIRED
    return LicenseStatus(status, license=lic, source=source, now=now)


# ── Issue (vendor side) ───────────────────────────────────────────────────────


def generate_keypair() -> tuple[bytes, str]:
    """``(private key PEM, public key hex)``."""
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    return pem, key.public_key().public_bytes_raw().hex()


def load_private_key(pem_or_path: str) -> Ed25519PrivateKey:
    raw = pem_or_path.strip()
    data = raw.encode() if raw.startswith("-----BEGIN") else Path(raw).expanduser().read_bytes()
    key = serialization.load_pem_private_key(data, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise LicenseError("the signing key is not an Ed25519 key")
    return key


def issue_license(private_key: Ed25519PrivateKey, kid: str, *, customer: str, expires_at: datetime,
                  features: list[str], max_tenants: int | None = None, install_id: str | None = None,
                  license_id: str | None = None, issued_at: datetime | None = None) -> str:
    """Sign a license; returns the one-line base64 license key."""
    payload = {
        "license_id": license_id or f"lic_{uuid.uuid4().hex[:16]}",
        "customer": customer,
        "edition": EDITION,
        "features": features,
        "max_tenants": max_tenants,
        "issued_at": _iso(issued_at or datetime.now(timezone.utc)),
        "expires_at": _iso(expires_at),
    }
    if install_id:
        payload["install_id"] = install_id
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    signature = private_key.sign(_signed_message(kid, payload_bytes))
    envelope = {
        "format": FORMAT,
        "kid": kid,
        "payload": base64.b64encode(payload_bytes).decode(),
        "signature": base64.b64encode(signature).decode(),
    }
    return base64.b64encode(json.dumps(envelope, separators=(",", ":")).encode()).decode()


# ── CLI ───────────────────────────────────────────────────────────────────────


def _parse_expiry(value: str) -> datetime:
    """``YYYY-MM-DD`` (end of that day, UTC) or a full ISO 8601 time."""
    if len(value) == 10:
        return datetime.fromisoformat(value).replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
    return _parse_time(value, "--expires")


def _cmd_keygen(args) -> int:
    out = Path(args.out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    priv_path = out / f"{args.kid}.private.pem"
    if priv_path.exists():
        print(f"refusing to overwrite {priv_path}", file=sys.stderr)
        return 1
    pem, pub_hex = generate_keypair()
    priv_path.write_bytes(pem)
    try:
        priv_path.chmod(0o600)
    except OSError:
        pass
    (out / f"{args.kid}.public.hex").write_text(pub_hex + "\n")
    print(f"private key: {priv_path}  (keep it secret, never commit it)")
    print("add to TRUSTED_KEYS in nodeglow_ee/licensing.py:")
    print(f'    "{args.kid}": "{pub_hex}",')
    return 0


def _cmd_issue(args) -> int:
    source = args.signing_key or os.environ.get(SIGNING_KEY_ENV, "")
    if not source:
        print(f"no signing key: pass --signing-key or set {SIGNING_KEY_ENV}", file=sys.stderr)
        return 1
    key = load_private_key(source)
    kid = args.kid
    if kid in TRUSTED_KEYS and key.public_key().public_bytes_raw().hex() != TRUSTED_KEYS[kid]:
        print(f"the signing key does not match the trusted key {kid!r}", file=sys.stderr)
        return 1
    if args.features.strip().lower() in ("all", "*"):
        features = ["*"]
    else:
        features = [f.strip() for f in args.features.split(",") if f.strip()]
        unknown = sorted(set(features) - set(KNOWN_FEATURES))
        if unknown:
            print(f"unknown features: {', '.join(unknown)} (known: {', '.join(KNOWN_FEATURES)})",
                  file=sys.stderr)
            return 1
    token = issue_license(key, kid, customer=args.customer, expires_at=_parse_expiry(args.expires),
                          features=features, max_tenants=args.max_tenants,
                          install_id=args.install_id, license_id=args.license_id)
    if args.out:
        Path(args.out).expanduser().write_text(token + "\n")
        print(f"license written to {args.out}", file=sys.stderr)
    else:
        print(token)
    return 0


def _cmd_verify(args) -> int:
    value = args.license
    path = Path(value).expanduser()
    text = path.read_text() if len(value) < 1024 and path.is_file() else value
    status = evaluate(text, source="cli", install_id=args.install_id)
    print(json.dumps(status.detail(), indent=2))
    return 0 if status.status in (STATUS_VALID, STATUS_GRACE) else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m nodeglow_ee.licensing",
                                     description="Nodeglow Enterprise license keys")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("keygen", help="create a new signing key pair")
    p.add_argument("--out-dir", required=True, help="directory for the key files (outside the repo)")
    p.add_argument("--kid", default=f"ng-{datetime.now(timezone.utc):%Y-%m}", help="key id")
    p.set_defaults(func=_cmd_keygen)

    p = sub.add_parser("issue", help="sign a license")
    p.add_argument("--signing-key", help=f"private key PEM file (or env {SIGNING_KEY_ENV})")
    p.add_argument("--kid", required=True, help="id of the signing key (a TRUSTED_KEYS entry)")
    p.add_argument("--customer", required=True)
    p.add_argument("--expires", required=True, help="YYYY-MM-DD (end of day, UTC) or ISO 8601")
    p.add_argument("--features", default="all", help=f"'all' or a comma list of {', '.join(KNOWN_FEATURES)}")
    p.add_argument("--max-tenants", type=int, default=None)
    p.add_argument("--install-id", default=None, help="bind to one installation (Settings → License)")
    p.add_argument("--license-id", default=None)
    p.add_argument("--out", help="write the license key to this file instead of stdout")
    p.set_defaults(func=_cmd_issue)

    p = sub.add_parser("verify", help="check a license key or file against the trusted keys")
    p.add_argument("license", help="license key, or a path to a file containing it")
    p.add_argument("--install-id", default=None)
    p.set_defaults(func=_cmd_verify)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except LicenseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
