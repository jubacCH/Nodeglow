"""bcrypt 5 raises on input over 72 bytes; the helpers keep bcrypt 4 behaviour."""
import bcrypt

from utils.password import hash_password, verify_password


def test_round_trip():
    h = hash_password("Correct-Horse-1")
    assert verify_password("Correct-Horse-1", h)
    assert not verify_password("Correct-Horse-2", h)


def test_long_password_does_not_raise():
    long_pw = "A1" + "x" * 200
    h = hash_password(long_pw)
    assert verify_password(long_pw, h)


def test_hash_made_by_bcrypt_4_from_a_long_password_still_verifies():
    # bcrypt 4 hashed only the first 72 bytes; such a stored hash must keep working.
    long_pw = "B2" + "y" * 100
    legacy = bcrypt.hashpw(long_pw.encode()[:72], bcrypt.gensalt(rounds=4)).decode()
    assert verify_password(long_pw, legacy)


def test_malformed_hash_is_rejected_not_raised():
    assert not verify_password("whatever", "not-a-bcrypt-hash")
    assert not verify_password("whatever", b"")
