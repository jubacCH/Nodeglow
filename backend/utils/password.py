"""Password strength validation."""
import re


_MIN_LENGTH = 8


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
