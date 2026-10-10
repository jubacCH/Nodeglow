import logging
import os
import secrets
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

SECRET_KEY_FILE = DATA_DIR / ".secret_key"

# Database: prefer DATABASE_URL env, fall back to SQLite in DATA_DIR
DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    DATABASE_PATH = DATA_DIR / "nodeglow.db"
    DATABASE_URL = f"sqlite+aiosqlite:///{DATABASE_PATH}"

# Optional Redis URL for shared, durable state (e.g. rate-limit counters).
# When unset, state lives in-process (per-worker) exactly as before; when set,
# limits are shared across workers/nodes. No connection is made at import time.
REDIS_URL = os.getenv("REDIS_URL", "")


# ── Application version ──────────────────────────────────────────────────────
# Single source of truth is the VERSION file at the repository root. The
# backend image is built from ./backend, which does not contain that file, so
# the version reaches the image as the APP_VERSION build arg (compose passes
# it, the update sidecar fills it from VERSION) and the Dockerfile writes it to
# /app/VERSION. For local runs and tests the repo-root file is read directly.
_VERSION_FALLBACK = "0.0.0+unknown"
_BACKEND_DIR = Path(__file__).resolve().parent


def _version_candidates() -> list[Path]:
    return [
        _BACKEND_DIR / "VERSION",          # baked into the image (/app/VERSION)
        _BACKEND_DIR.parent / "VERSION",   # repo checkout (dev, CI)
    ]


def get_version() -> str:
    """Return the application version, e.g. ``"1.0.0"``.

    Order: ``APP_VERSION`` env, ``/app/VERSION`` (image), repo-root ``VERSION``,
    then a fallback that is obviously not a release. The VERSION file may carry
    trailing comment lines (``# <timestamp>``); only the first non-comment line
    counts.
    """
    env = os.getenv("APP_VERSION", "").strip()
    if env:
        return env
    for path in _version_candidates():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line
    return _VERSION_FALLBACK


# Where the key came from: "env", "file" (existing DATA_DIR/.secret_key) or
# "generated" (no key anywhere, a new one was just written to DATA_DIR).
SECRET_KEY_SOURCE = "env"
# Kept for backwards compatibility with code that checks the boolean.
SECRET_KEY_FROM_ENV = False


def get_secret_key() -> str:
    global SECRET_KEY_FROM_ENV, SECRET_KEY_SOURCE
    env_key = os.getenv("SECRET_KEY")
    if env_key:
        SECRET_KEY_FROM_ENV = True
        SECRET_KEY_SOURCE = "env"
        return env_key
    SECRET_KEY_FROM_ENV = False
    if SECRET_KEY_FILE.exists():
        SECRET_KEY_SOURCE = "file"
        return SECRET_KEY_FILE.read_text().strip()
    key = secrets.token_hex(32)
    SECRET_KEY_FILE.write_text(key)
    SECRET_KEY_FILE.chmod(0o600)
    SECRET_KEY_SOURCE = "generated"
    return key


_BANNER = "=" * 72


def warn_about_secret_key(logger: logging.Logger = log) -> None:
    """Log loudly when the encryption key is not supplied via the environment.

    The key encrypts every stored credential (Fernet) and peppers API-key
    hashes. Kept only in DATA_DIR it shares fate with the data: lose the
    directory and the restored database is full of credentials nobody can
    decrypt; leak a copy of the directory and key and ciphertext leak
    together. See docs/OPERATIONS.md, "The encryption key".
    """
    if SECRET_KEY_SOURCE == "env":
        return
    lines = [_BANNER]
    if SECRET_KEY_SOURCE == "generated":
        lines += [
            f"SECRET_KEY is not set and no key file existed: a NEW key was generated at {SECRET_KEY_FILE}.",
            "If this installation already had stored credentials (restored database, lost data",
            "directory), they can NOT be decrypted with this key. Put the original key into",
            "SECRET_KEY and restart.",
        ]
    else:
        lines += [
            f"SECRET_KEY is not set; the encryption key is read from {SECRET_KEY_FILE},",
            "in the SAME directory as the data it protects.",
        ]
    lines += [
        " * Losing that directory makes every stored credential undecryptable,",
        "   even when the database itself is restored from a backup.",
        " * A copy of that directory contains both the key and the ciphertext.",
        "Copy the key into SECRET_KEY (.env or a Docker secret), back it up SEPARATELY",
        "from the database backups, then delete the file.",
        'See docs/OPERATIONS.md, "The encryption key".',
        _BANNER,
    ]
    level = logging.ERROR if SECRET_KEY_SOURCE == "generated" else logging.WARNING
    for line in lines:
        logger.log(level, line)


def secret_key_fingerprint(key: str | None = None) -> str:
    """Short, non-reversible identifier of the encryption key.

    Lets an operator check that an escrowed key is the one an installation or
    a backup was made with, without ever printing the key. It reveals nothing
    the encrypted data does not already allow an attacker to verify.
    """
    import hashlib

    material = (key if key is not None else SECRET_KEY).encode()
    return hashlib.sha256(b"nodeglow-secret-key-fingerprint:" + material).hexdigest()[:16]


SECRET_KEY = get_secret_key()
warn_about_secret_key()
log.info("Encryption key fingerprint: %s (source: %s)", secret_key_fingerprint(), SECRET_KEY_SOURCE)

