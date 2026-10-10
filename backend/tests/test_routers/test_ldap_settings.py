"""Saving LDAP settings with a plaintext transport warns instead of staying silent."""
import logging

import pytest

from routers.settings.ldap import ldap_transport_warnings


def _form(**overrides):
    data = {"ldap_enabled": "1", "ldap_server": "ldap://dc.lan", "ldap_bind_dn": "cn=svc",
            "ldap_base_dn": "dc=lan", "ldap_use_ssl": "0", "ldap_start_tls": "0"}
    data.update(overrides)
    return data


async def test_plaintext_ldap_save_returns_warning_and_logs(client, caplog):
    with caplog.at_level(logging.WARNING, logger="routers.settings.ldap"):
        resp = await client.post("/settings/ldap/save", data=_form())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["warnings"] and "plaintext" in body["warnings"][0]
    assert any("insecure transport" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("overrides", [
    {"ldap_start_tls": "1"},
    {"ldap_use_ssl": "1"},
    {"ldap_server": "ldaps://dc.lan"},
])
async def test_encrypted_ldap_save_has_no_warning(client, overrides):
    resp = await client.post("/settings/ldap/save", data=_form(**overrides))
    assert resp.status_code == 200, resp.text
    assert "warnings" not in resp.json()


def test_no_warning_without_server():
    assert ldap_transport_warnings("", False, False) == []
    assert ldap_transport_warnings("LDAPS://dc", False, False) == []
    assert ldap_transport_warnings("dc.lan", False, False)
