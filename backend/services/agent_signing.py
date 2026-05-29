"""Ed25519 signing for agent auto-updates.

The server holds an ed25519 private key (persisted in ``DATA_DIR``). Agents embed
the matching public key — delivered inside the server-generated install script,
i.e. over the operator's authenticated browser session — and verify a detached
signature over the downloaded binary before applying an update.

This is defense-in-depth on top of enforced TLS: even a compromised transport or
a malicious update host cannot make an agent run an unsigned binary, because the
attacker does not hold the private key.

Wire format (must match the Rust agent in agent/src/updater.rs):
  * public key: hex-encoded 32 raw ed25519 bytes
  * signature:  hex-encoded 64 raw ed25519 bytes, computed over the exact binary
                bytes served by ``GET /agents/download/{platform}``
  * algorithm:  pure ed25519 (not ed25519ph) over the raw message
"""
import logging

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from config import DATA_DIR

log = logging.getLogger(__name__)

_KEY_FILE = DATA_DIR / "agent_update_ed25519.key"

_private_key: Ed25519PrivateKey | None = None


def get_private_key() -> Ed25519PrivateKey:
    """Return the persistent signing key, generating it on first use.

    The raw 32-byte private key is stored 0600 in DATA_DIR and cached in-process.
    """
    global _private_key
    if _private_key is not None:
        return _private_key

    if _KEY_FILE.exists():
        _private_key = Ed25519PrivateKey.from_private_bytes(_KEY_FILE.read_bytes())
    else:
        _private_key = Ed25519PrivateKey.generate()
        _KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        _KEY_FILE.write_bytes(_private_key.private_bytes_raw())
        try:
            _KEY_FILE.chmod(0o600)
        except OSError:
            pass
        log.info("Generated new agent-update signing key at %s", _KEY_FILE)

    return _private_key


def public_key_hex() -> str:
    """Hex-encoded raw ed25519 public key (64 hex chars) for agents to embed."""
    return get_private_key().public_key().public_bytes_raw().hex()


def sign_bytes(data: bytes) -> str:
    """Hex-encoded detached ed25519 signature (128 hex chars) over ``data``."""
    return get_private_key().sign(data).hex()


def _reset_for_tests() -> None:
    """Drop the cached key so tests can exercise generation/persistence."""
    global _private_key
    _private_key = None
