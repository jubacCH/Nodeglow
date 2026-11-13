"""One-time token for the first-run setup.

Until the setup wizard has run, ``/setup/complete`` creates the installation
operator, and it is reachable without a login by design. Without a token,
whoever reached the port first became admin. The token is something only the
person installing has: ``install.sh`` generates it into ``.env`` and prints
it; on an installation started by hand it is generated here, written to the
server log and to ``<DATA_DIR>/setup-token``.
"""
from __future__ import annotations

import hmac
import logging
import os
import secrets
from pathlib import Path

import config

log = logging.getLogger("nodeglow.setup")

ENV = "NODEGLOW_SETUP_TOKEN"
_generated: str | None = None


def _file() -> Path:
    return Path(config.DATA_DIR) / "setup-token"


def current() -> str:
    """The token the wizard must be given. Stable until setup completes."""
    global _generated
    from_env = os.getenv(ENV, "").strip()
    if from_env:
        return from_env
    if _generated:
        return _generated
    path = _file()
    try:
        saved = path.read_text(encoding="utf-8").strip()
    except OSError:
        saved = ""
    if saved:
        _generated = saved
        return saved
    _generated = secrets.token_urlsafe(18)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(_generated + "\n")
    except OSError as exc:
        # No writable data directory: the token still stands, for this
        # process, and is in the log.
        log.warning("Could not write the setup token to %s: %s", path, exc)
    return _generated


def matches(value: str | None) -> bool:
    given = (value or "").strip()
    return bool(given) and hmac.compare_digest(given.encode(), current().encode())


def discard() -> None:
    """Setup is done: the token has no further use."""
    global _generated
    _generated = None
    try:
        _file().unlink()
    except OSError:
        pass


def announce() -> None:
    """Say where the token is, once at start while setup is pending."""
    if os.getenv(ENV, "").strip():
        log.warning("First-run setup is pending. The setup wizard asks for the setup token: "
                    "the value of %s in your .env (printed by install.sh).", ENV)
        return
    log.warning("First-run setup is pending. Setup token: %s (also in %s). "
                "The setup wizard asks for it.", current(), _file())
