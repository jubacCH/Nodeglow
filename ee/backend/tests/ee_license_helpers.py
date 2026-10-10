"""Test licenses, signed with a key generated per test run (never a real key)."""
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

TEST_KID = "test-key"
TEST_SIGNING_KEY = Ed25519PrivateKey.generate()


def issue_test_license(*, days: float = 365, features=("*",), kid: str = TEST_KID,
                       key: Ed25519PrivateKey | None = None, customer: str = "Test Customer",
                       **kw) -> str:
    """A license signed with the test key, expiring ``days`` from now (negative: in the past)."""
    from nodeglow_ee import licensing

    return licensing.issue_license(
        key or TEST_SIGNING_KEY, kid, customer=customer,
        expires_at=datetime.now(timezone.utc) + timedelta(days=days),
        features=list(features), **kw,
    )
