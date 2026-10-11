"""Encryption at rest for the legacy notification channel secrets.

Telegram, Discord and the generic webhook used to store their credentials in
plaintext. They are now encrypted with the tenant's data key
(``services.tenant_keys``, like smtp_password and the Teams/Slack/ntfy
secrets) and read through ``reveal``, which falls back to the raw value so
rows written before this change keep working.

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


def reveal(value: str | None, cipher=None) -> str:
    """Plaintext of a stored secret; legacy plaintext rows pass through.

    ``cipher``: the tenant's :class:`services.tenant_keys.TenantCipher` —
    needed for values under the tenant's data key; without it such a value
    raises (fail closed) instead of being handed out as if it were plaintext.
    """
    if not value:
        return ""
    if cipher is not None:
        return cipher.reveal(value)
    from services import tenant_keys

    if tenant_keys.is_v2(value):
        raise tenant_keys.TenantKeyError("a tenant secret needs the tenant's cipher to be read")
    from models.base import decrypt_value
    try:
        return decrypt_value(value)
    except Exception:
        return value


def is_encrypted(value: str) -> bool:
    """True when ``value`` is ciphertext: under a tenant key, or a Fernet
    token (ours or one we cannot read).

    A token we can decrypt is ours. One that only *looks* like a token (e.g.
    written under a previous SECRET_KEY) is left alone too: encrypting it
    again would bury it one level deeper without making it readable.
    """
    if not value:
        return False
    from services import tenant_keys

    if tenant_keys.is_v2(value):
        return True
    from models.base import decrypt_value
    try:
        decrypt_value(value)
        return True
    except Exception:
        return value.startswith(_FERNET_PREFIX)


async def load_legacy_secrets(db, get_setting) -> dict[str, str]:
    """All legacy channel secrets, decrypted (the session's tenant)."""
    from services.tenant_keys import tenant_cipher

    cipher = await tenant_cipher(db)
    return {k: cipher.reveal(await get_setting(db, k, "")) for k in LEGACY_SECRET_KEYS}


async def encrypt_plaintext_channel_secrets(session_factory=None) -> int:
    """Encrypt plaintext legacy channel secrets in place. Idempotent.

    Runs on every start: it touches at most four rows per tenant and skips
    anything already encrypted, so a restored backup with plaintext values
    is covered as well. The marker records when the first pass completed.
    Returns the number of values encrypted by this call.
    """
    from sqlalchemy import select

    from database import Setting
    from models.tenant import TenantSetting
    from services.tenancy import system_scope
    from services.tenant_keys import tenant_cipher

    if session_factory is None:
        from database import AsyncSessionLocal as session_factory

    changed = 0
    async with session_factory() as db:
        # The secrets are tenant settings: one pass over every tenant's rows,
        # each encrypted with its own tenant's key.
        with system_scope("startup: encrypt plaintext notification channel secrets"):
            rows = (await db.execute(
                select(TenantSetting).where(TenantSetting.key.in_(LEGACY_SECRET_KEYS))
            )).scalars().all()
            ciphers: dict = {}
            for row in rows:
                if not row.value or row.encrypted:
                    continue  # row.encrypted: get_setting already decrypts it
                if is_encrypted(row.value):
                    continue
                if row.tenant_id not in ciphers:
                    ciphers[row.tenant_id] = await tenant_cipher(db, row.tenant_id, write=True)
                row.value = ciphers[row.tenant_id].encrypt(row.value)
                changed += 1
            await db.flush()
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
