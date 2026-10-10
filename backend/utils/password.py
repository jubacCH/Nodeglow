"""Password strength validation and bcrypt hashing."""
import re

import bcrypt


_MIN_LENGTH = 8

# bcrypt only ever looked at the first 72 bytes of a password. bcrypt 4 cut
# longer input silently; bcrypt 5 raises ValueError instead. Truncating here
# keeps the old behaviour, so hashes stored before the upgrade still verify and
# a long password no longer turns login into a 500.
_BCRYPT_MAX_BYTES = 72
_BCRYPT_ROUNDS = 12


def validate_password(password: str) -> str | None:
    """Return an error message if password is too weak, or None if ok."""
    if len(password) < _MIN_LENGTH:
        return f"Password must be at least {_MIN_LENGTH} characters"
    if not re.search(r"[A-Z]", password):
        return "Password must contain at least one uppercase letter"
    if not re.search(r"[a-z]", password):
        return "Password must contain at least one lowercase letter"
    if not re.search(r"\d", password):
        return "Password must contain at least one digit"
    return None


def _bcrypt_input(password: str) -> bytes:
    return password.encode()[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_bcrypt_input(password), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode()


def verify_password(password: str, password_hash: str | bytes) -> bool:
    stored = password_hash.encode() if isinstance(password_hash, str) else password_hash
    try:
        return bcrypt.checkpw(_bcrypt_input(password), stored)
    except ValueError:
        # Malformed stored hash
        return False
