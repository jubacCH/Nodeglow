"""Certificates NPM renews itself warn later than ones nobody renews (2026-11-18).

NPM renews its Let's Encrypt certificates hourly once fewer than 30 days are
left, so four of them at 30 days were a renewal about to happen, not a problem.
"""
from datetime import datetime, timedelta

from integrations.npm import _parse
from routers.ssl_monitor import AUTO_RENEW_WARN_DAYS, CERT_WARN_DAYS, cert_warn_days


def _cert(provider: str, days: int) -> dict:
    expires = (datetime.utcnow() + timedelta(days=days, hours=12)).strftime("%Y-%m-%d %H:%M:%S")
    return {"id": 1, "nice_name": "x.example", "provider": provider,
            "domain_names": ["x.example"], "expires_on": expires}


def test_npm_marks_lets_encrypt_certificates_as_auto_renewing():
    data = _parse([], [], [], [], [_cert("letsencrypt", 30), _cert("other", 30)])
    by_provider = {c["provider"]: c for c in data["certificates"]}
    assert by_provider["letsencrypt"]["auto_renew"] is True
    assert by_provider["other"]["auto_renew"] is False


def test_auto_renewing_certificates_warn_later():
    assert cert_warn_days({"auto_renew": True}) == AUTO_RENEW_WARN_DAYS < CERT_WARN_DAYS
    assert cert_warn_days({"auto_renew": False}) == CERT_WARN_DAYS
    assert cert_warn_days({}) == CERT_WARN_DAYS
