"""Time-based one-time passwords (RFC 6238) for sign-in.

Standard parameters every authenticator app understands: SHA-1, six digits,
30-second steps. Implemented on the standard library; the algorithm is a
dozen lines and a dependency would add nothing but supply chain.

The secret is stored encrypted with the installation key (users are not
tenant-owned, so there is no tenant key to put it under). Recovery codes are
stored as HMACs: they are shown once and can only be compared afterwards.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
from urllib.parse import quote

STEP_SECONDS = 30
DIGITS = 6
# One step either side: clocks drift and people type slowly.
WINDOW = 1
RECOVERY_CODES = 10
ISSUER = "Nodeglow"


def new_secret() -> str:
    """A fresh shared secret, base32 without padding (160 bits)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    cleaned = secret.strip().replace(" ", "").upper()
    return base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))


def counter_at(when: float | None = None) -> int:
    return int((time.time() if when is None else when) // STEP_SECONDS)


def code_for(secret: str, counter: int) -> str:
    digest = hmac.new(_key(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10 ** DIGITS).zfill(DIGITS)


def verify(secret: str, code: str, *, last_counter: int | None = None,
           when: float | None = None) -> int | None:
    """The time step ``code`` is valid for, or None.

    A step at or before ``last_counter`` is refused: a code somebody watched
    being typed cannot be used a second time within its 30 seconds.
    """
    digits = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(digits) != DIGITS:
        return None
    now = counter_at(when)
    for counter in range(now - WINDOW, now + WINDOW + 1):
        if last_counter is not None and counter <= last_counter:
            continue
        if hmac.compare_digest(code_for(secret, counter), digits):
            return counter
    return None


def provisioning_uri(secret: str, username: str, issuer: str = ISSUER) -> str:
    """The otpauth:// URI an authenticator app reads from a QR code."""
    label = quote(f"{issuer}:{username}")
    return (f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}")


# ── Recovery codes ───────────────────────────────────────────────────────────

def _normalise(code: str) -> str:
    return "".join(ch for ch in (code or "").lower() if ch.isalnum())


def _hash_recovery(code: str, pepper: str) -> str:
    return hmac.new(pepper.encode(), _normalise(code).encode(), hashlib.sha256).hexdigest()


def new_recovery_codes(pepper: str) -> tuple[list[str], str]:
    """Ten one-time codes: (the codes to show once, the JSON to store)."""
    codes = []
    for _ in range(RECOVERY_CODES):
        raw = secrets.token_hex(5)
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes, json.dumps([_hash_recovery(c, pepper) for c in codes])


def recovery_codes_left(stored: str | None) -> int:
    try:
        return len(json.loads(stored or "[]"))
    except (TypeError, ValueError):
        return 0


def use_recovery_code(stored: str | None, code: str, pepper: str) -> str | None:
    """The stored JSON without ``code`` if it was a valid unused one, else None."""
    if len(_normalise(code)) != 10:
        return None
    try:
        hashes = list(json.loads(stored or "[]"))
    except (TypeError, ValueError):
        return None
    wanted = _hash_recovery(code, pepper)
    for known in hashes:
        if hmac.compare_digest(known, wanted):
            hashes.remove(known)
            return json.dumps(hashes)
    return None
