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


# Sentinel value for get_secret_key(): True when the key was sourced from
# the env var, False when it came from (or was just created in) the data
# volume. The app startup path logs a warning in the volume-fallback case
# so operators can migrate to env-provided secrets (keeps the encryption
# key out of the same volume that holds the encrypted data).
SECRET_KEY_FROM_ENV = False


def get_secret_key() -> str:
    global SECRET_KEY_FROM_ENV
    env_key = os.getenv("SECRET_KEY")
    if env_key:
        SECRET_KEY_FROM_ENV = True
        return env_key
    if SECRET_KEY_FILE.exists():
        return SECRET_KEY_FILE.read_text().strip()
    key = secrets.token_hex(32)
    SECRET_KEY_FILE.write_text(key)
    SECRET_KEY_FILE.chmod(0o600)
    return key


SECRET_KEY = get_secret_key()

if not SECRET_KEY_FROM_ENV:
    log.warning(
        "SECRET_KEY is being loaded from %s inside DATA_DIR. This means the "
        "Fernet key lives in the SAME volume as the encrypted credentials — "
        "a stolen/backed-up volume would contain both. Move the key to the "
        "SECRET_KEY env var (e.g. Docker secret or .env) and delete the file "
        "for stronger separation.",
        SECRET_KEY_FILE,
    )
