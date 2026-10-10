"""The JSON backup is only handed out passphrase-encrypted.

The export holds users (bcrypt hashes), API key hashes, settings and the
stored credentials. It used to be downloaded as plaintext JSON.
"""
import base64
import json
from unittest.mock import AsyncMock, patch

import pytest

from services.backup import (
    BackupCryptoError,
    ENVELOPE_FORMAT,
    decrypt_backup,
    encrypt_backup,
    is_encrypted_backup,
)

@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """The backup routes allow 3 requests/min; these tests make more."""
    import services.shared_state as ss
    ss.reset()
    yield
    ss.reset()


async def _post(client, url, json):
    """POST with the rate limiter cleared first."""
    import services.shared_state as ss
    ss.reset()
    return await client.post(url, json=json)


PASS = "correct horse battery staple"
SAMPLE = {"_meta": {"format": "nodeglow-backup", "version": "1.0"},
          "tables": {"users": [{"id": 1, "username": "admin",
                                "password_hash": "$2b$12$abcdefghijklmnopqrstuv"}]}}


# ── Envelope ────────────────────────────────────────────────────────────────


def test_roundtrip_and_no_plaintext_in_envelope():
    env = encrypt_backup(SAMPLE, PASS)
    assert is_encrypted_backup(env)
    assert env["format"] == ENVELOPE_FORMAT and env["version"] == 1
    assert env["kdf"]["name"] == "scrypt" and env["cipher"]["name"] == "AES-256-GCM"
    dumped = json.dumps(env)
    assert "password_hash" not in dumped and "$2b$" not in dumped and "admin" not in dumped
    assert decrypt_backup(env, PASS) == SAMPLE


def test_each_export_uses_fresh_salt_and_nonce():
    a, b = encrypt_backup(SAMPLE, PASS), encrypt_backup(SAMPLE, PASS)
    assert a["kdf"]["salt"] != b["kdf"]["salt"]
    assert a["cipher"]["nonce"] != b["cipher"]["nonce"]


def test_short_passphrase_rejected():
    with pytest.raises(BackupCryptoError) as exc:
        encrypt_backup(SAMPLE, "short")
    assert exc.value.code == "passphrase_required"


def test_wrong_passphrase_fails():
    env = encrypt_backup(SAMPLE, PASS)
    with pytest.raises(BackupCryptoError) as exc:
        decrypt_backup(env, "wrong passphrase!!")
    assert exc.value.code == "decrypt_failed"


def test_missing_passphrase_fails():
    with pytest.raises(BackupCryptoError) as exc:
        decrypt_backup(encrypt_backup(SAMPLE, PASS), "")
    assert exc.value.code == "passphrase_required"


def test_tampered_header_fails_authentication():
    env = encrypt_backup(SAMPLE, PASS)
    env["created"] = "2000-01-01T00:00:00"
    with pytest.raises(BackupCryptoError) as exc:
        decrypt_backup(env, PASS)
    assert exc.value.code == "decrypt_failed"


def test_tampered_ciphertext_fails():
    env = encrypt_backup(SAMPLE, PASS)
    raw = bytearray(base64.b64decode(env["ciphertext"]))
    raw[0] ^= 1
    env["ciphertext"] = base64.b64encode(bytes(raw)).decode()
    with pytest.raises(BackupCryptoError):
        decrypt_backup(env, PASS)


@pytest.mark.parametrize("mutate", [
    lambda e: e["kdf"].__setitem__("n", 2 ** 30),        # memory bomb
    lambda e: e["kdf"].__setitem__("n", 1000),           # not a power of two
    lambda e: e["kdf"].__setitem__("name", "pbkdf2"),
    lambda e: e.__setitem__("version", 99),
    lambda e: e.__setitem__("ciphertext", "!!notbase64"),
])
def test_bad_parameters_rejected_before_kdf(mutate):
    env = encrypt_backup(SAMPLE, PASS)
    mutate(env)
    with pytest.raises(BackupCryptoError):
        decrypt_backup(env, PASS)


# ── API ─────────────────────────────────────────────────────────────────────


async def test_export_requires_passphrase(client):
    resp = await _post(client, "/api/v1/backup", json={})
    assert resp.status_code == 400
    assert resp.json()["code"] == "passphrase_required"
    resp = await _post(client, "/api/v1/backup", json={"passphrase": "short"})
    assert resp.status_code == 400


async def test_plaintext_get_export_is_gone(client):
    resp = await client.get("/api/v1/backup")
    assert resp.status_code == 405


async def test_export_is_encrypted_and_restorable(client):
    resp = await _post(client, "/api/v1/backup", json={"passphrase": PASS})
    assert resp.status_code == 200, resp.text
    env = resp.json()
    assert is_encrypted_backup(env)
    assert "tables" not in env and "site_name" not in resp.text
    data = decrypt_backup(env, PASS)
    assert data["_meta"]["format"] == "nodeglow-backup"
    assert any(r["key"] == "site_name" for r in data["tables"]["settings"])

    with patch("services.backup.import_backup", new_callable=AsyncMock,
               return_value={"imported": {}, "total_rows": 7}) as imp:
        resp = await _post(client, "/api/v1/backup/restore", json={"backup": env, "passphrase": PASS})
    assert resp.status_code == 200, resp.text
    assert resp.json()["total_rows"] == 7
    assert imp.await_args.args[1]["_meta"]["format"] == "nodeglow-backup"


async def test_restore_encrypted_wrong_or_missing_passphrase(client):
    env = encrypt_backup(SAMPLE, PASS)
    with patch("services.backup.import_backup", new_callable=AsyncMock) as imp:
        resp = await _post(client, "/api/v1/backup/restore", json={"backup": env, "passphrase": "nope nope nope"})
        assert resp.status_code == 400 and resp.json()["code"] == "decrypt_failed"
        resp = await _post(client, "/api/v1/backup/restore", json={"backup": env})
        assert resp.status_code == 400 and resp.json()["code"] == "passphrase_required"
        resp = await _post(client, "/api/v1/backup/restore", json=env)  # raw envelope as body
        assert resp.status_code == 400 and resp.json()["code"] == "passphrase_required"
    imp.assert_not_awaited()


async def test_legacy_plaintext_restore_needs_explicit_flag(client):
    with patch("services.backup.import_backup", new_callable=AsyncMock,
               return_value={"imported": {}, "total_rows": 1}) as imp:
        resp = await _post(client, "/api/v1/backup/restore", json=SAMPLE)
        assert resp.status_code == 400 and resp.json()["code"] == "unencrypted_backup"
        resp = await _post(client, "/api/v1/backup/restore", json={"backup": SAMPLE})
        assert resp.status_code == 400 and resp.json()["code"] == "unencrypted_backup"
        imp.assert_not_awaited()

        resp = await _post(client, "/api/v1/backup/restore",
                                 json={"backup": SAMPLE, "allow_unencrypted": True})
        assert resp.status_code == 200, resp.text
        resp = await _post(client, "/api/v1/backup/restore?allow_unencrypted=true", json=SAMPLE)
        assert resp.status_code == 200, resp.text
    assert imp.await_count == 2


async def test_restore_invalid_format_is_400(client):
    resp = await _post(client, "/api/v1/backup/restore",
                             json={"backup": {"foo": 1}, "allow_unencrypted": True})
    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_backup"
