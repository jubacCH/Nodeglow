"""License keys: verification, states, CLI (no backend process needed)."""
import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ee_license_helpers import TEST_KID, TEST_SIGNING_KEY, issue_test_license
from nodeglow_ee import licensing
from nodeglow_ee.licensing import (
    MODE_FULL,
    MODE_OFF,
    MODE_READ_ONLY,
    LicenseError,
    evaluate,
    verify_license,
)


def _envelope(token: str) -> dict:
    return json.loads(base64.b64decode(token))


def _encode(envelope: dict) -> str:
    return base64.b64encode(json.dumps(envelope).encode()).decode()


# ── Signature, key ids, tampering ─────────────────────────────────────────────


def test_a_valid_license_verifies():
    lic = verify_license(issue_test_license(customer="ACME AG", max_tenants=5, license_id="lic_1"))
    assert lic.customer == "ACME AG" and lic.license_id == "lic_1" and lic.max_tenants == 5
    assert lic.kid == TEST_KID and lic.grants("ai_assistant") and lic.grants("future_feature")
    assert lic.granted_features() == list(licensing.KNOWN_FEATURES)


def test_json_and_base64_forms_are_accepted():
    token = issue_test_license()
    as_json = base64.b64decode(token).decode()
    urlsafe = base64.urlsafe_b64encode(as_json.encode()).decode().rstrip("=")
    for form in (token, as_json, urlsafe, f"  {token}\n"):
        assert evaluate(form).status == "valid"


def test_a_signature_from_another_key_is_rejected():
    forged = issue_test_license(key=Ed25519PrivateKey.generate())  # claims the test kid
    with pytest.raises(LicenseError, match="signature"):
        verify_license(forged)
    status = evaluate(forged)
    assert status.status == "invalid" and "signature" in status.error
    assert status.feature_mode("ai_assistant") == MODE_OFF


def test_an_unknown_key_id_is_rejected():
    token = issue_test_license(kid="ng-1999-01")
    with pytest.raises(LicenseError, match="unknown signing key"):
        verify_license(token)


def test_swapping_the_key_id_breaks_the_signature(monkeypatch):
    other = Ed25519PrivateKey.generate()
    monkeypatch.setitem(licensing.TRUSTED_KEYS, "other", other.public_key().public_bytes_raw().hex())
    env = _envelope(issue_test_license())
    env["kid"] = "other"
    with pytest.raises(LicenseError, match="signature"):
        verify_license(_encode(env))


def test_a_tampered_payload_is_rejected():
    env = _envelope(issue_test_license(features=["ai_assistant"], days=1))
    payload = json.loads(base64.b64decode(env["payload"]))
    payload["features"] = ["*"]
    payload["expires_at"] = "2099-12-31T23:59:59Z"
    env["payload"] = base64.b64encode(json.dumps(payload).encode()).decode()
    with pytest.raises(LicenseError, match="signature"):
        verify_license(_encode(env))


@pytest.mark.parametrize("text, match", [
    ("", "empty"),
    ("not a license!", "neither JSON nor base64"),
    ('{"format": "other/9"}', "unsupported"),
    ('{"format": "nodeglow-license/1", "kid": "test-key"}', "payload or signature"),
    ('{"format": "nodeglow-license/1", "kid": "test-key", "payload": "%%%", "signature": "AA=="}', "base64"),
])
def test_malformed_licenses_are_rejected(text, match):
    with pytest.raises(LicenseError, match=match):
        verify_license(text)


def test_a_license_for_another_edition_is_rejected():
    payload = json.dumps({"license_id": "x", "customer": "c", "edition": "community", "features": ["*"],
                          "issued_at": "2026-01-01T00:00:00Z", "expires_at": "2099-01-01T00:00:00Z"}).encode()
    sig = TEST_SIGNING_KEY.sign(licensing._signed_message(TEST_KID, payload))
    token = _encode({"format": licensing.FORMAT, "kid": TEST_KID,
                     "payload": base64.b64encode(payload).decode(),
                     "signature": base64.b64encode(sig).decode()})
    with pytest.raises(LicenseError, match="edition"):
        verify_license(token)


def test_the_embedded_keys_are_real_ed25519_keys_and_not_the_test_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    real = {k: v for k, v in licensing.TRUSTED_KEYS.items() if k != TEST_KID}
    assert real, "at least one production key must be embedded"
    test_hex = TEST_SIGNING_KEY.public_key().public_bytes_raw().hex()
    for kid, hex_key in real.items():
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(hex_key))
        assert hex_key != test_hex, kid


# ── Expiry and grace ──────────────────────────────────────────────────────────


def test_expiry_grace_and_blocking():
    token = issue_test_license(days=10)
    lic = verify_license(token)
    at = lambda delta: evaluate(token, now=lic.expires_at + delta)  # noqa: E731

    assert at(-timedelta(days=1)).status == "valid"
    assert at(timedelta(0)).status == "valid"

    grace = at(timedelta(days=1))
    assert grace.status == "grace" and grace.feature_mode("ai_assistant") == MODE_FULL
    assert "keep working until" in grace.message()
    assert at(timedelta(days=licensing.GRACE_DAYS)).status == "grace"

    expired = at(timedelta(days=licensing.GRACE_DAYS, seconds=1))
    assert expired.status == "expired"
    for feature in ("ai_assistant", "ai_postmortem", "ai_daily_summary"):
        assert expired.feature_mode(feature) == MODE_READ_ONLY
        assert not expired.feature_active(feature)
    # Leader election protects monitoring and keeps running.
    assert expired.feature_mode("ha_scheduler") == MODE_FULL
    assert "Monitoring keeps running" in expired.message()


def test_feature_list_limits_what_is_granted():
    status = evaluate(issue_test_license(features=["ai_assistant"]))
    assert status.feature_active("ai_assistant")
    assert not status.feature_active("ai_postmortem") and not status.feature_active("ha_scheduler")
    assert status.detail()["features"] == ["ai_assistant"]


def test_missing_license():
    status = evaluate(None)
    assert status.status == "missing"
    assert all(status.feature_mode(f) == MODE_OFF for f in licensing.KNOWN_FEATURES)
    assert status.public()["expires_at"] is None


def test_install_id_binding_is_optional_and_enforced_when_set():
    assert evaluate(issue_test_license()).status == "valid"  # unbound: any installation
    bound = issue_test_license(install_id="ngi_abc")
    assert evaluate(bound, install_id="ngi_abc").status == "valid"
    status = evaluate(bound, install_id="ngi_other")
    assert status.status == "invalid" and "different installation" in status.error
    assert evaluate(bound).status == "invalid"


def test_public_summary_has_no_customer_details():
    public = evaluate(issue_test_license(customer="Secret Customer")).public()
    assert "Secret Customer" not in json.dumps({k: v for k, v in public.items() if k != "message"})
    assert set(public) == {"status", "message", "expires_at", "grace_until", "days_left"}


# ── CLI ───────────────────────────────────────────────────────────────────────


def test_cli_keygen_issue_verify_round_trip(tmp_path, monkeypatch, capsys):
    keys = tmp_path / "keys"
    assert licensing.main(["keygen", "--out-dir", str(keys), "--kid", "ng-test-cli"]) == 0
    out = capsys.readouterr().out
    pub_hex = (keys / "ng-test-cli.public.hex").read_text().strip()
    assert pub_hex in out and "PRIVATE" not in out
    # Refuses to overwrite an existing private key.
    assert licensing.main(["keygen", "--out-dir", str(keys), "--kid", "ng-test-cli"]) == 1

    monkeypatch.setitem(licensing.TRUSTED_KEYS, "ng-test-cli", pub_hex)
    lic_file = tmp_path / "owner-license.txt"
    rc = licensing.main(["issue", "--signing-key", str(keys / "ng-test-cli.private.pem"),
                         "--kid", "ng-test-cli", "--customer", "Owner", "--expires", "2036-12-31",
                         "--features", "all", "--out", str(lic_file)])
    assert rc == 0
    lic = verify_license(lic_file.read_text())
    assert lic.customer == "Owner" and lic.features == ("*",)
    assert lic.expires_at == datetime(2036, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

    capsys.readouterr()
    assert licensing.main(["verify", str(lic_file)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "valid"


def test_cli_issue_reads_the_key_from_env_and_checks_it(tmp_path, monkeypatch, capsys):
    pem, pub_hex = licensing.generate_keypair()
    monkeypatch.setenv(licensing.SIGNING_KEY_ENV, pem.decode())
    monkeypatch.setitem(licensing.TRUSTED_KEYS, "ng-env", pub_hex)
    assert licensing.main(["issue", "--kid", "ng-env", "--customer", "C", "--expires", "2030-01-01",
                           "--features", "ai_assistant,ai_postmortem"]) == 0
    token = capsys.readouterr().out.strip()
    assert verify_license(token).features == ("ai_assistant", "ai_postmortem")

    # A key that does not match the trusted key of that id is refused.
    monkeypatch.setitem(licensing.TRUSTED_KEYS, "ng-env", "00" * 32)
    assert licensing.main(["issue", "--kid", "ng-env", "--customer", "C", "--expires", "2030-01-01"]) == 1
    # Unknown feature names are refused.
    monkeypatch.setitem(licensing.TRUSTED_KEYS, "ng-env", pub_hex)
    assert licensing.main(["issue", "--kid", "ng-env", "--customer", "C", "--expires", "2030-01-01",
                           "--features", "ai_magic"]) == 1
    # No key at all.
    monkeypatch.delenv(licensing.SIGNING_KEY_ENV)
    assert licensing.main(["issue", "--kid", "ng-env", "--customer", "C", "--expires", "2030-01-01"]) == 1
