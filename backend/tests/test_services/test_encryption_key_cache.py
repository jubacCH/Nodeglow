"""The Fernet key is derived once per process.

480k PBKDF2 rounds cost ~50 ms. Deriving them on every encrypt/decrypt put that
on the request path wherever a config was read (the dashboard paid it once per
Proxmox cluster per load).
"""
from unittest.mock import patch

from models import base


def test_key_is_derived_once():
    base._fernet.cache_clear()
    with patch.object(base, "PBKDF2HMAC", wraps=base.PBKDF2HMAC) as kdf:
        for _ in range(5):
            assert base.decrypt_value(base.encrypt_value("secret")) == "secret"
    assert kdf.call_count == 1


def test_values_encrypted_before_caching_still_decrypt():
    base._fernet.cache_clear()
    token = base.encrypt_value("payload")
    base._fernet.cache_clear()
    assert base.decrypt_value(token) == "payload"


def test_legacy_values_still_decrypt():
    token = base._fernet_legacy().encrypt(b"old").decode()
    assert base.decrypt_value(token) == "old"
