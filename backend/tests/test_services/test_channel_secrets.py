"""Legacy channel secrets (Telegram, Discord, webhook) are encrypted at rest."""
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker

from models.base import decrypt_value, encrypt_value
from services import channel_secrets as cs

TG = "123456:ABC-DEF_secret"
DC = "https://discord.com/api/webhooks/1/secret"


def _factory(db):
    return async_sessionmaker(db.bind, expire_on_commit=False)


async def _seed(db, **values):
    from database import Setting
    for k, v in values.items():
        db.add(Setting(key=k, value=v))
    await db.commit()


async def _raw(factory, key):
    from database import Setting
    async with factory() as s:
        row = await s.get(Setting, key)
        return row.value if row else None


def test_reveal_decrypts_and_falls_back_to_plaintext():
    assert cs.reveal(encrypt_value(TG)) == TG
    assert cs.reveal(TG) == TG
    assert cs.reveal("") == "" and cs.reveal(None) == ""


def test_is_encrypted():
    assert cs.is_encrypted(encrypt_value("x"))
    assert not cs.is_encrypted(TG)
    assert not cs.is_encrypted(DC)
    # a Fernet token from another key: not ours, but must not be re-encrypted
    assert cs.is_encrypted("gAAAAABnotourtoken" + "A" * 80)


async def test_migration_encrypts_plaintext_in_place(db):
    already = encrypt_value("whsec")
    await _seed(db, telegram_bot_token=TG, discord_webhook_url=DC,
                webhook_url="", webhook_secret=already, telegram_chat_id="-100")
    factory = _factory(db)

    assert await cs.encrypt_plaintext_channel_secrets(factory) == 2

    tg_raw = await _raw(factory, "telegram_bot_token")
    assert tg_raw != TG and decrypt_value(tg_raw) == TG
    assert decrypt_value(await _raw(factory, "discord_webhook_url")) == DC
    assert await _raw(factory, "webhook_secret") == already  # untouched
    assert await _raw(factory, "webhook_url") == ""
    assert await _raw(factory, "telegram_chat_id") == "-100"  # not a secret
    assert await _raw(factory, cs.MIGRATION_MARKER)


async def test_migration_is_idempotent(db):
    await _seed(db, telegram_bot_token=TG)
    factory = _factory(db)
    assert await cs.encrypt_plaintext_channel_secrets(factory) == 1
    first = await _raw(factory, "telegram_bot_token")
    marker = await _raw(factory, cs.MIGRATION_MARKER)

    assert await cs.encrypt_plaintext_channel_secrets(factory) == 0
    assert await _raw(factory, "telegram_bot_token") == first
    assert await _raw(factory, cs.MIGRATION_MARKER) == marker


async def test_migration_on_empty_install(db):
    factory = _factory(db)
    assert await cs.encrypt_plaintext_channel_secrets(factory) == 0
    assert await _raw(factory, cs.MIGRATION_MARKER)


async def test_notify_decrypts_legacy_secrets():
    import notifications
    notifications._recent.clear()
    settings = {
        "notify_enabled": "1",
        "telegram_bot_token": encrypt_value(TG), "telegram_chat_id": "-100",
        "discord_webhook_url": DC,  # legacy plaintext row still works
    }

    async def fake_get_setting(db, key, default=""):
        return settings.get(key, default)

    with patch("database.AsyncSessionLocal") as mock_cls, \
         patch("database.get_setting", side_effect=fake_get_setting), \
         patch.object(notifications, "_send_telegram", new_callable=AsyncMock) as tg, \
         patch.object(notifications, "_send_discord", new_callable=AsyncMock) as dc, \
         patch.object(notifications, "_log_notification", new_callable=AsyncMock):
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        await notifications.notify("Secrets", "m")
    assert tg.await_args.args[0] == TG
    assert dc.await_args.args[0] == DC
