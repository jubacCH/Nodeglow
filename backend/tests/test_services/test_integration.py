"""Tests for integration service config encryption."""
from models.integration import IntegrationConfig
from services.integration import decrypt_config, encrypt_config
from services.tenant_keys import is_v2


async def test_encrypt_decrypt_roundtrip(db):
    original = {"host": "10.0.0.1", "username": "admin", "password": "s3cret"}
    encrypted = await encrypt_config(db, original)
    assert encrypted != str(original)
    assert is_v2(encrypted)  # under the tenant's data key
    cfg = IntegrationConfig(type="proxmox", name="x", config_json=encrypted, tenant_id=1)
    assert await decrypt_config(db, cfg) == original


async def test_encrypt_produces_different_output(db):
    cfg = {"key": "value"}
    e1 = await encrypt_config(db, cfg)
    e2 = await encrypt_config(db, cfg)
    # Fernet uses random IV, so outputs differ
    assert e1 != e2


async def test_config_from_before_tenant_keys_still_decrypts(db):
    import json

    from models.base import encrypt_value

    cfg = IntegrationConfig(type="proxmox", name="x", tenant_id=1,
                            config_json=encrypt_value(json.dumps({"host": "h"})))
    assert await decrypt_config(db, cfg) == {"host": "h"}
