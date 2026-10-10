"""Encryption at rest for the legacy notification channel secrets.

Telegram, Discord and the generic webhook used to store their credentials in
plaintext. They are now written with ``encrypt_value`` (like smtp_password and
the Teams/Slack/ntfy secrets) and read through ``reveal``, which falls back to
the raw value so rows written before this change keep working.

``encrypt_plaintext_channel_secrets`` runs at startup and encrypts whatever is
still plaintext, so production gets encrypted without anyone re-saving.
"""
from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

LEGACY_SECRET_KEYS: tuple[str, ...] = (
    "telegram_bot_token", "discord_webhook_url", "webhook_url", "webhook_secret",
)
MIGRATION_MARKER = "channel_secrets_encrypted_at"

# Every Fernet token starts with version byte 0x80 + timestamp, which base64s
# to "gAAAAA". Neither a bot token, a URL nor a sane signing secret does.
_FERNET_PREFIX = "gAAAAA"


def reveal(value: str | None) -> str:
    """Plaintext of a stored secret; legacy plaintext rows pass through."""
    if not value:
        return ""
    from models.base import decrypt_value
    try:
        return decrypt_value(value)
    except Exception:
        return value


def is_encrypted(value: str) -> bool:
    """True when ``value`` is a Fernet token (ours or one we cannot read).

    A token we can decrypt is ours. One that only *looks* like a token (e.g.
    written under a previous SECRET_KEY) is left alone too: encrypting it
    again would bury it one level deeper without making it readable.
    """
    if not value:
        return False
    from models.base import decrypt_value
    try:
        decrypt_value(value)
        return True
    except Exception:
        return value.startswith(_FERNET_PREFIX)


async def load_legacy_secrets(db, get_setting) -> dict[str, str]:
    """All legacy channel secrets, decrypted."""
    return {k: reveal(await get_setting(db, k, "")) for k in LEGACY_SECRET_KEYS}


async def encrypt_plaintext_channel_secrets(session_factory=None) -> int:
    """Encrypt plaintext legacy channel secrets in place. Idempotent.

    Runs on every start: it touches at most four rows and skips anything
    already encrypted, so a restored backup with plaintext values is covered
    as well. The marker records when the first pass completed. Returns the
    number of values encrypted by this call.
    """
    from database import Setting
    from models.base import encrypt_value

    if session_factory is None:
        from database import AsyncSessionLocal as session_factory

    changed = 0
    async with session_factory() as db:
        for key in LEGACY_SECRET_KEYS:
            row = await db.get(Setting, key)
            if row is None or not row.value or row.encrypted:
                continue  # row.encrypted: get_setting already decrypts it
            if is_encrypted(row.value):
                continue
            row.value = encrypt_value(row.value)
            changed += 1
        if await db.get(Setting, MIGRATION_MARKER) is None:
            db.add(Setting(key=MIGRATION_MARKER,
                           value=datetime.utcnow().isoformat(timespec="seconds")))
        try:
            await db.commit()
        except Exception:
            # Another worker inserted the marker first; its pass covers ours.
            await db.rollback()
            return 0
    if changed:
        logger.info("Encrypted %d plaintext notification channel secret(s)", changed)
    return changed
