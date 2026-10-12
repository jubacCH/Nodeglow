"""Per-tenant envelope encryption (owner decision 7, revision 046).

Every tenant has its own **data-encryption key** (DEK): 32 random bytes,
used as a Fernet key for that tenant's secrets — integration configs
(``integration_configs.config_json``), credentials (``credentials.data_json``)
and the secret tenant settings (``tenant_settings``: rows flagged
``encrypted`` and the keys the settings registry declares ``secret``). The
DEK is stored only **wrapped** (encrypted) in ``tenant_keys`` by the
installation's **key-encryption key** (KEK), derived from ``SECRET_KEY``.
Installation secrets (``settings``: LDAP bind password, AI provider keys,
the licence, GeoIP) keep the installation key (``models.base.encrypt_value``).

Destroying a tenant's keys (:func:`destroy_tenant_key`, the purge of a
deleted tenant) is a crypto-shred: the wrapped DEKs are deleted, and the
tenant's ciphertext — in the database, a replica, every dump or JSON export
taken afterwards — is unreadable from then on, even with ``SECRET_KEY``.
A dump or export taken *before* the destroy holds the wrapped keys like it
holds the data, so it stays readable with ``SECRET_KEY`` until it leaves
backup retention. Decrypts fail closed with :class:`TenantKeyDestroyed`.

Value format (one string column, self-describing)::

    v2:<key_id>:<Fernet token under the DEK <key_id>>

Anything else is a value written before 046: a Fernet token under the
installation key (``legacy``), read transparently, or — for the notification
channel secrets of very old installs — plaintext. ``python -m
services.tenant_keys verify`` reports how many of each there are.

Keys and versions:

* ``key_id`` names a DEK (random, unique per tenant). A tenant has one
  **active** key (the newest that is neither retired nor destroyed); new
  values use it. :func:`rotate_tenant_key` makes a new active key,
  re-encrypts every secret of the tenant and **retires** the old ones —
  retired keys stay readable (a value written concurrently with the old key
  is not lost) until the tenant's keys are destroyed.
* ``kek_id`` names the KEK that wrapped a DEK: ``sk1`` = HKDF-SHA256 over
  the PBKDF2-stretched ``SECRET_KEY`` (``models.base.installation_key_material``)
  with its own label. A KMS-held KEK later is a new ``kek_id``; unwrap
  dispatches on it.
* The wrapped plaintext is ``ngdek1:<tenant_id>:<key_id>:`` + DEK, checked on
  unwrap: a wrapped DEK copied into another tenant's row does not unwrap
  there.

Caching: a session reads the tenant's key rows once per transaction
(``Session.info``, cleared at commit and rollback — a destroy or rotation in
another process is seen by the next transaction); the unwrapped DEK is
cached per process, keyed by tenant, key and the wrapped value. Reads go
through the tenant filter and row-level security like every tenant-owned
table: in tenant A's context, B's keys cannot even be read.
"""
from __future__ import annotations

import base64
import functools
import hashlib
import logging
import os
import re
import secrets
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)

FORMAT_PREFIX = "v2:"
KEK_ID = "sk1"
_KEK_INFO = {KEK_ID: b"nodeglow tenant key-encryption key v1"}
_WRAP_MAGIC = b"ngdek1"
DEK_BYTES = 32
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

# Fernet tokens start with version byte 0x80 + timestamp → "gAAAAA".
_FERNET_PREFIX = "gAAAAA"

# Tenant-owned columns that always hold a secret (JSON under the tenant DEK).
SECRET_COLUMNS: tuple[tuple[str, str], ...] = (
    ("integration_configs", "config_json"),
    ("credentials", "data_json"),
)

_SESSION_KEY = "nodeglow_tenant_keys"


# ── Errors ───────────────────────────────────────────────────────────────────

class TenantKeyError(RuntimeError):
    """A tenant secret cannot be encrypted or decrypted."""


class TenantKeyDestroyed(TenantKeyError):
    """The tenant's key was destroyed (crypto-shred): the data is gone."""


class TenantKeyUnavailable(TenantKeyError):
    """The value names a key this tenant does not have (another tenant's
    value, a restored row without its key, a corrupted value)."""


# ── Primitives (no database; revision 046 uses them too) ────────────────────

@functools.lru_cache(maxsize=4)
def _kek(kek_id: str) -> Fernet:
    info = _KEK_INFO.get(kek_id)
    if info is None:
        raise TenantKeyError(f"unknown key-encryption key {kek_id!r}")
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    from models.base import installation_key_material

    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(
        installation_key_material()
    )
    return Fernet(base64.urlsafe_b64encode(key))


def new_key_id() -> str:
    return secrets.token_hex(8)


def new_dek() -> bytes:
    return os.urandom(DEK_BYTES)


def _wrap_aad(tenant_id: int, key_id: str) -> bytes:
    return b"%s:%d:%s:" % (_WRAP_MAGIC, int(tenant_id), key_id.encode())


def wrap_dek(dek: bytes, tenant_id: int, key_id: str, kek_id: str = KEK_ID) -> str:
    """The DEK encrypted by the KEK, bound to its tenant and key id."""
    if len(dek) != DEK_BYTES:
        raise ValueError("a data key is 32 bytes")
    if not _KEY_ID_RE.match(key_id):
        raise ValueError(f"invalid key id {key_id!r}")
    return _kek(kek_id).encrypt(_wrap_aad(tenant_id, key_id) + dek).decode()


def unwrap_dek(wrapped: str, tenant_id: int, key_id: str, kek_id: str) -> bytes:
    """The DEK from :func:`wrap_dek`; raises :class:`TenantKeyError` when it
    does not unwrap (other SECRET_KEY) or belongs to another tenant/key."""
    try:
        plain = _kek(kek_id).decrypt(wrapped.encode())
    except InvalidToken as exc:
        raise TenantKeyError(
            f"the data key {key_id!r} of tenant {tenant_id} does not unwrap with this "
            "installation's SECRET_KEY"
        ) from exc
    aad = _wrap_aad(tenant_id, key_id)
    if not plain.startswith(aad) or len(plain) != len(aad) + DEK_BYTES:
        raise TenantKeyError(
            f"the wrapped data key {key_id!r} does not belong to tenant {tenant_id}"
        )
    return plain[len(aad):]


def dek_fernet(dek: bytes) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(dek))


def is_v2(value: str | None) -> bool:
    return bool(value) and value.startswith(FORMAT_PREFIX)


def parse_v2(value: str) -> tuple[str, str]:
    """``(key_id, token)`` of a ``v2:`` value."""
    rest = value[len(FORMAT_PREFIX):]
    key_id, sep, token = rest.partition(":")
    if not sep or not _KEY_ID_RE.match(key_id) or not token:
        raise TenantKeyUnavailable("malformed tenant secret (v2 value without key id)")
    return key_id, token


def seal(fernet: Fernet, key_id: str, plaintext: str) -> str:
    return f"{FORMAT_PREFIX}{key_id}:{fernet.encrypt(plaintext.encode()).decode()}"


def legacy_decrypt(value: str) -> str:
    """A value under the installation key (everything before 046)."""
    from models.base import decrypt_value

    return decrypt_value(value)


def classify(value: str | None) -> str:
    """``empty`` | ``v2`` | ``legacy`` (installation key, decrypts) |
    ``undecryptable`` (looks like a Fernet token, does not decrypt — another
    SECRET_KEY) | ``plaintext``. Never returns the value."""
    if not value:
        return "empty"
    if is_v2(value):
        return "v2"
    try:
        legacy_decrypt(value)
        return "legacy"
    except Exception:
        return "undecryptable" if value.startswith(_FERNET_PREFIX) else "plaintext"


# ── Process cache of unwrapped keys ─────────────────────────────────────────

_CACHE_MAX = 4096
_unwrapped: OrderedDict[tuple, Fernet] = OrderedDict()


def _cached_fernet(tenant_id: int, key_id: str, kek_id: str, wrapped: str) -> Fernet:
    """Unwrapped DEK of one key row, cached per tenant. The cache key holds
    the wrapped value too, so a re-created row never reuses a stale entry."""
    ck = (int(tenant_id), key_id, kek_id, hashlib.sha256(wrapped.encode()).hexdigest())
    f = _unwrapped.get(ck)
    if f is not None:
        _unwrapped.move_to_end(ck)
        return f
    f = dek_fernet(unwrap_dek(wrapped, tenant_id, key_id, kek_id))
    _unwrapped[ck] = f
    while len(_unwrapped) > _CACHE_MAX:
        _unwrapped.popitem(last=False)
    return f


def clear_cache(tenant_id: int | None = None) -> None:
    """Drop cached DEKs (of one tenant, or all)."""
    if tenant_id is None:
        _unwrapped.clear()
        return
    for ck in [k for k in _unwrapped if k[0] == int(tenant_id)]:
        _unwrapped.pop(ck, None)


# ── Per-session key sets ─────────────────────────────────────────────────────

@dataclass
class _KeySet:
    tenant_id: int
    # key_id -> Fernet, or None for a destroyed key
    keys: dict[str, Fernet | None] = field(default_factory=dict)
    active: str | None = None
    destroyed: bool = False   # the tenant had keys and they were destroyed
    # key_id -> why it does not unwrap (another SECRET_KEY, a copied row)
    broken: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DataKey:
    """The active data key of a tenant."""

    tenant_id: int
    key_id: str
    fernet: Fernet = field(repr=False)


def _memo(db) -> dict[int, _KeySet]:
    return db.info.setdefault(_SESSION_KEY, {})


def _forget_session(session, *args) -> None:
    session.info.pop(_SESSION_KEY, None)


def _install_session_events() -> None:
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    for name in ("after_commit", "after_rollback", "after_soft_rollback"):
        if not event.contains(Session, name, _forget_session):
            event.listen(Session, name, _forget_session)


_install_session_events()


async def _load(db, tenant_id: int) -> _KeySet:
    """The tenant's key rows (once per transaction). Caller holds the scope."""
    memo = _memo(db)
    ks = memo.get(tenant_id)
    if ks is not None:
        return ks
    from sqlalchemy import select

    from models.tenant import TenantKey

    rows = (await db.execute(
        select(TenantKey.key_id, TenantKey.kek_id, TenantKey.wrapped_dek,
               TenantKey.retired_at, TenantKey.destroyed_at, TenantKey.created_at)
        .where(TenantKey.tenant_id == tenant_id)
        .order_by(TenantKey.created_at, TenantKey.key_id)
    )).all()
    ks = _KeySet(tenant_id=tenant_id)
    for key_id, kek_id, wrapped, retired_at, destroyed_at, _created in rows:
        if destroyed_at is not None or not wrapped:
            ks.keys[key_id] = None
            ks.destroyed = True
            continue
        try:
            ks.keys[key_id] = _cached_fernet(tenant_id, key_id, kek_id, wrapped)
        except TenantKeyError as exc:
            # Fail on use, not on load: values under the installation key
            # and the tenant's other keys stay readable.
            log.error("tenant %s: %s", tenant_id, exc)
            ks.broken[key_id] = str(exc)
        if retired_at is None:
            ks.active = key_id   # rows are ordered: the newest wins
    if ks.active is not None:
        ks.destroyed = False     # a tombstone next to a live key is history
    memo[tenant_id] = ks
    return ks


async def _create(db, tenant_id: int, ks: _KeySet) -> str:
    """A new active key for the tenant, in the caller's transaction."""
    from models.tenant import TenantKey

    key_id = new_key_id()
    dek = new_dek()
    db.add(TenantKey(tenant_id=tenant_id, key_id=key_id, kek_id=KEK_ID,
                     wrapped_dek=wrap_dek(dek, tenant_id, key_id), created_at=datetime.utcnow()))
    await db.flush()
    ks.keys[key_id] = dek_fernet(dek)
    ks.active = key_id
    ks.destroyed = False
    log.info("created data key %s for tenant %s", key_id, tenant_id)
    return key_id


# ── Cipher ───────────────────────────────────────────────────────────────────

class TenantCipher:
    """Encrypt/decrypt one tenant's secrets — synchronous, for code that
    takes a decrypt callback. Obtain with :func:`tenant_cipher`."""

    def __init__(self, ks: _KeySet):
        self._ks = ks
        self.tenant_id = ks.tenant_id

    @property
    def key_id(self) -> str | None:
        return self._ks.active

    def encrypt(self, plaintext: str) -> str:
        ks = self._ks
        if ks.active is None:
            if ks.destroyed:
                raise TenantKeyDestroyed(
                    f"tenant {ks.tenant_id}'s keys were destroyed; nothing can be encrypted for it"
                )
            raise TenantKeyError(
                f"tenant {ks.tenant_id} has no data key yet: use tenant_cipher(..., write=True)"
            )
        if ks.active in ks.broken:
            raise TenantKeyUnavailable(ks.broken[ks.active])
        return seal(ks.keys[ks.active], ks.active, plaintext)

    def decrypt(self, stored: str) -> str:
        """Plaintext of a stored secret. ``v2`` values need this tenant's key;
        older values decrypt with the installation key. Raises
        :class:`TenantKeyError` (fail closed) — never returns ciphertext."""
        if not is_v2(stored):
            return legacy_decrypt(stored)
        key_id, token = parse_v2(stored)
        ks = self._ks
        if key_id in ks.broken:
            raise TenantKeyUnavailable(ks.broken[key_id])
        if key_id not in ks.keys:
            raise TenantKeyUnavailable(
                f"secret encrypted under key {key_id!r}, which tenant {ks.tenant_id} does not have"
            )
        f = ks.keys[key_id]
        if f is None:
            raise TenantKeyDestroyed(
                f"tenant {ks.tenant_id}'s key {key_id!r} was destroyed (crypto-shred); "
                "the secret cannot be recovered"
            )
        try:
            return f.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise TenantKeyUnavailable(
                f"secret does not decrypt under tenant {ks.tenant_id}'s key {key_id!r}"
            ) from exc

    def reveal(self, stored: str | None) -> str:
        """Like :meth:`decrypt`, but tolerant of what older versions stored:
        ``""`` for nothing, and a value that is not ours (plaintext from before
        the channel secrets were encrypted) passes through. A ``v2`` value
        that does not decrypt still raises."""
        if not stored:
            return ""
        if is_v2(stored):
            return self.decrypt(stored)
        try:
            return legacy_decrypt(stored)
        except Exception:
            return stored


async def tenant_cipher(db, tenant_id: int | None = None, *, write: bool = False) -> TenantCipher:
    """The cipher of ``tenant_id`` (default: the session's tenant).

    ``tenant_id`` must agree with the tenant context (system scope may name
    any tenant). ``write=True`` creates the tenant's first key if it has none
    (in the caller's transaction — commit it with the values it encrypts);
    after :func:`destroy_tenant_key` it raises instead.
    """
    from services import tenancy

    tid, scope = tenancy.tenant_access(db, tenant_id)
    with scope:
        ks = await _load(db, tid)
        if write and ks.active is None:
            if ks.destroyed:
                raise TenantKeyDestroyed(
                    f"tenant {tid}'s keys were destroyed; nothing can be encrypted for it"
                )
            await _create(db, tid, ks)
    return TenantCipher(ks)


async def get_dek(db, tenant_id: int | None = None, *, create: bool = True) -> DataKey:
    """The tenant's active data key (created on first use with ``create``).
    Raises :class:`TenantKeyDestroyed` after a destroy, :class:`TenantKeyError`
    when there is none and ``create`` is off."""
    cipher = await tenant_cipher(db, tenant_id, write=create)
    ks = cipher._ks
    if ks.active is None:
        if ks.destroyed:
            raise TenantKeyDestroyed(f"tenant {ks.tenant_id}'s keys were destroyed")
        raise TenantKeyError(f"tenant {ks.tenant_id} has no data key")
    if ks.active in ks.broken:
        # Fail closed like the cipher: a key that does not unwrap (another
        # SECRET_KEY, a copied row) is never replaced behind the caller's back.
        raise TenantKeyUnavailable(ks.broken[ks.active])
    return DataKey(ks.tenant_id, ks.active, ks.keys[ks.active])


async def encrypt_secret(db, plaintext: str, *, tenant_id: int | None = None) -> str:
    return (await tenant_cipher(db, tenant_id, write=True)).encrypt(plaintext)


async def decrypt_secret(db, stored: str, *, tenant_id: int | None = None) -> str:
    return (await tenant_cipher(db, tenant_id)).decrypt(stored)


async def reveal_secret(db, stored: str | None, *, tenant_id: int | None = None) -> str:
    if not stored:
        return ""
    return (await tenant_cipher(db, tenant_id)).reveal(stored)


async def create_tenant_key(db, tenant_id: int) -> DataKey:
    """Give a new tenant its key right away (ee tenant creation; the
    default tenant gets one from revision 046 or on first use). Idempotent:
    an existing active key is returned. Not committed."""
    return await get_dek(db, tenant_id, create=True)


# ── Secret rows of a tenant ──────────────────────────────────────────────────

def _secret_setting_keys() -> frozenset[str]:
    from services.settings_registry import secret_tenant_keys

    return secret_tenant_keys()


async def _secret_rows(db, tenant_id: int):
    """``(row, attribute, label)`` for every secret-bearing value of the
    tenant (ORM objects, caller holds the scope)."""
    from sqlalchemy import or_, select

    from models.credential import Credential
    from models.integration import IntegrationConfig
    from models.tenant import TenantSetting

    out: list[tuple[Any, str, str]] = []
    for cfg in (await db.execute(
        select(IntegrationConfig).where(IntegrationConfig.tenant_id == tenant_id).execution_options(populate_existing=True)
    )).scalars():
        out.append((cfg, "config_json", "integration_configs.config_json"))
    for cred in (await db.execute(
        select(Credential).where(Credential.tenant_id == tenant_id).execution_options(populate_existing=True)
    )).scalars():
        out.append((cred, "data_json", "credentials.data_json"))
    for row in (await db.execute(
        select(TenantSetting).where(
            TenantSetting.tenant_id == tenant_id,
            or_(TenantSetting.encrypted.is_(True),
                TenantSetting.key.in_(sorted(_secret_setting_keys()))),
        ).execution_options(populate_existing=True)
    )).scalars():
        out.append((row, "value", "tenant_settings.value"))
    return out


def _is_settings_plaintext_ok(row, attr: str) -> bool:
    """Plaintext that may be encrypted as is: a secret setting stored without
    the ``encrypted`` flag (its readers tolerate plaintext — the channel
    secrets of installs older than their encryption)."""
    return attr == "value" and not getattr(row, "encrypted", False)


@dataclass
class ReencryptReport:
    tenant_id: int
    key_id: str | None = None
    converted: int = 0          # values now under key_id
    unchanged: int = 0          # empty, or already under key_id
    failed: dict[str, int] = field(default_factory=dict)   # label -> count (left as they were)
    retired: list[str] = field(default_factory=list)

    def fail(self, label: str) -> None:
        self.failed[label] = self.failed.get(label, 0) + 1


async def _reencrypt(db, tenant_id: int, cipher: TenantCipher, old: TenantCipher,
                     *, only_legacy: bool) -> ReencryptReport:
    report = ReencryptReport(tenant_id=tenant_id, key_id=cipher.key_id)
    for row, attr, label in await _secret_rows(db, tenant_id):
        value = getattr(row, attr)
        if not value:
            report.unchanged += 1
            continue
        if is_v2(value):
            if only_legacy or parse_v2(value)[0] == cipher.key_id:
                report.unchanged += 1
                continue
        try:
            plain = old.decrypt(value)
        except Exception:
            if not is_v2(value) and _is_settings_plaintext_ok(row, attr) \
                    and not value.startswith(_FERNET_PREFIX):
                plain = value
            else:
                report.fail(label)
                continue
        setattr(row, attr, cipher.encrypt(plain))
        report.converted += 1
    await db.flush()
    return report


async def reencrypt_legacy(db, tenant_id: int) -> ReencryptReport:
    """Re-encrypt the tenant's values that are still under the installation
    key (or plaintext secret settings) with its data key. Idempotent — what
    revision 046 does, for data that arrives later in the old format (a
    restored backup, the startup encryption of old channel secrets). Values
    that do not decrypt are left as they are and counted. Not committed."""
    from services import tenancy

    tid, scope = tenancy.tenant_access(db, tenant_id)
    with scope:
        pending = any(
            getattr(row, attr) and not is_v2(getattr(row, attr))
            for row, attr, _label in await _secret_rows(db, tid)
        )
        if not pending:   # nothing to move: no key is created for it either
            cipher = await tenant_cipher(db, tid)
            return ReencryptReport(tenant_id=tid, key_id=cipher.key_id)
        cipher = await tenant_cipher(db, tid, write=True)
        return await _reencrypt(db, tid, cipher, cipher, only_legacy=True)


async def upgrade_legacy_secrets(session_factory=None) -> int:
    """Startup pass: move values still under the installation key to their
    tenant's key — rows written by the previous backend while an update ran
    (the update's migrate step runs before the old backend stops), restored
    backups, rows of revision 046 that failed. Idempotent and cheap when
    there is nothing to do (it reads the secret rows, nothing else).
    Returns the number of values re-encrypted; never raises for one tenant."""
    from sqlalchemy import select

    from models.tenant import Tenant
    from services import tenancy

    if session_factory is None:
        from models.base import AsyncSessionLocal as session_factory

    total = 0
    async with session_factory() as db:
        with tenancy.system_scope("startup: list tenants for the tenant-key upgrade"):
            tids = list((await db.execute(select(Tenant.id).order_by(Tenant.id))).scalars())
        for tid in tids:
            try:
                with tenancy.system_scope(f"startup: tenant secrets of tenant {tid} to its key"):
                    report = await reencrypt_legacy(db, tid)
                await db.commit()
            except TenantKeyDestroyed:
                await db.rollback()
                continue
            except Exception:
                await db.rollback()
                log.exception("moving tenant %s's secrets to its data key failed", tid)
                continue
            total += report.converted
            if report.converted:
                log.info("tenant %s: %d secret(s) moved to data key %s",
                         tid, report.converted, report.key_id)
            for label, n in report.failed.items():
                log.warning("tenant %s: %d value(s) in %s do not decrypt and were left as they are "
                            "(python -m services.tenant_keys verify)", tid, n, label)
    return total


async def rotate_tenant_key(db, tenant_id: int) -> ReencryptReport:
    """New active key for the tenant; every secret re-encrypted under it; the
    previous keys retired (still readable, so nothing written concurrently
    with the old key is lost). Not committed — the caller commits, and a
    rollback leaves everything as it was."""
    from sqlalchemy import update

    from models.tenant import TenantKey
    from services import tenancy

    tid, scope = tenancy.tenant_access(db, tenant_id)
    with scope:
        _memo(db).pop(tid, None)
        ks = await _load(db, tid)
        if ks.active is None and ks.destroyed:
            raise TenantKeyDestroyed(f"tenant {tid}'s keys were destroyed")
        old = TenantCipher(_KeySet(tid, dict(ks.keys), ks.active, ks.destroyed, dict(ks.broken)))
        # Every live key goes, the ones that do not unwrap included (they are
        # in ``broken``, not in ``keys``); tombstones (None) are history.
        previous = [k for k, f in ks.keys.items() if f is not None]
        previous += [k for k in ks.broken if k not in ks.keys]
        await _create(db, tid, ks)
        cipher = TenantCipher(ks)
        report = await _reencrypt(db, tid, cipher, old, only_legacy=False)
        if previous:
            await db.execute(
                update(TenantKey)
                .where(TenantKey.tenant_id == tid, TenantKey.key_id.in_(previous),
                       TenantKey.retired_at.is_(None))
                .values(retired_at=datetime.utcnow())
                .execution_options(synchronize_session=False)
            )
        report.retired = previous
        await db.flush()
    log.info("rotated tenant %s to key %s: %d re-encrypted, %d failed",
             tid, report.key_id, report.converted, sum(report.failed.values()))
    return report


async def destroy_tenant_key(db, tenant_id: int) -> int:
    """Crypto-shred: delete every wrapped data key of the tenant. Values
    still under the installation key are moved to the tenant key first, so
    they are shredded too. Afterwards every decrypt of the tenant's secrets
    fails with :class:`TenantKeyDestroyed` and nothing can be encrypted for
    it. Returns the number of keys destroyed. Not committed — the caller
    commits (the purge, in system scope).

    A tenant without a key gets a tombstone (a key row without a wrapped
    key, destroyed): the destroyed state is recorded either way, so a later
    encrypt for the tenant fails with :class:`TenantKeyDestroyed` instead of
    quietly creating a fresh key."""
    from sqlalchemy import update

    from models.tenant import TenantKey
    from services import tenancy

    tid, scope = tenancy.tenant_access(db, tenant_id)
    with scope:
        _memo(db).pop(tid, None)
        ks = await _load(db, tid)
        if ks.active is not None or not ks.destroyed:
            await reencrypt_legacy(db, tid)
        result = await db.execute(
            update(TenantKey)
            .where(TenantKey.tenant_id == tid, TenantKey.destroyed_at.is_(None))
            .values(wrapped_dek=None, destroyed_at=datetime.utcnow())
            .execution_options(synchronize_session=False)
        )
        count = result.rowcount or 0
        if count == 0 and not ks.destroyed:
            now = datetime.utcnow()
            db.add(TenantKey(tenant_id=tid, key_id=new_key_id(), kek_id=KEK_ID,
                             wrapped_dek=None, created_at=now, destroyed_at=now))
        await db.flush()
    _memo(db).pop(tid, None)
    clear_cache(tid)
    log.warning("destroyed %d data key(s) of tenant %s (crypto-shred)", count, tid)
    return count


# ── Backups ──────────────────────────────────────────────────────────────────
#
# The JSON export (services.backup) carries tenant secrets exactly as they
# are stored (``v2:`` under the tenant's data key) together with the
# ``tenant_keys`` rows, whose data keys are wrapped by the KEK — like a
# pg_dump. Opening a restored secret needs the installation's SECRET_KEY as
# well as the backup passphrase: an export alone (one operator API key and a
# passphrase the caller picks) never yields a tenant's credentials.


# ── Verify / CLI ─────────────────────────────────────────────────────────────

async def scan(db) -> dict:
    """Counts of secret values per column and format, and decrypt failures —
    every tenant (system scope). Never returns a secret."""
    from sqlalchemy import or_, select

    from models.credential import Credential
    from models.integration import IntegrationConfig
    from models.tenant import TenantKey, TenantSetting
    from services import tenancy

    report: dict = {"columns": {}, "keys": {}, "failures": 0}
    ciphers: dict[int, TenantCipher] = {}
    with tenancy.system_scope("tenant keys: verify secret formats"):
        for tid, key_id, retired, destroyed in (await db.execute(
            select(TenantKey.tenant_id, TenantKey.key_id, TenantKey.retired_at,
                   TenantKey.destroyed_at).order_by(TenantKey.tenant_id, TenantKey.created_at)
        )).all():
            state = "destroyed" if destroyed else "retired" if retired else "active"
            report["keys"].setdefault(str(tid), {})[key_id] = state
        sources = [
            ("integration_configs.config_json",
             select(IntegrationConfig.tenant_id, IntegrationConfig.config_json)),
            ("credentials.data_json",
             select(Credential.tenant_id, Credential.data_json)),
            ("tenant_settings.value",
             select(TenantSetting.tenant_id, TenantSetting.value).where(
                 or_(TenantSetting.encrypted.is_(True),
                     TenantSetting.key.in_(sorted(_secret_setting_keys()))))),
        ]
        for label, stmt in sources:
            counts: dict[str, int] = {}
            for tid, value in (await db.execute(stmt)).all():
                kind = classify(value)
                if kind == "v2":
                    if tid not in ciphers:
                        ciphers[tid] = await tenant_cipher(db, tid)
                    key_id = parse_v2(value)[0] if value else "?"
                    kind = f"v2:{key_id}"
                    try:
                        ciphers[tid].decrypt(value)
                    except TenantKeyError as exc:
                        kind = f"v2:{key_id}:{type(exc).__name__}"
                        report["failures"] += 1
                elif kind == "undecryptable":
                    report["failures"] += 1
                counts[kind] = counts.get(kind, 0) + 1
            report["columns"][label] = dict(sorted(counts.items()))
    return report


def _format_report(report: dict) -> str:
    lines = ["Tenant keys:"]
    if not report["keys"]:
        lines.append("  (none)")
    for tid, keys in sorted(report["keys"].items(), key=lambda kv: int(kv[0])):
        lines.append(f"  tenant {tid}: " + ", ".join(f"{k} ({s})" for k, s in keys.items()))
    lines.append("Secret values by format:")
    for label, counts in report["columns"].items():
        detail = ", ".join(f"{k}={n}" for k, n in counts.items()) or "no rows"
        lines.append(f"  {label}: {detail}")
    lines.append(f"Decrypt failures: {report['failures']}")
    return "\n".join(lines)


async def _cli(argv: list[str]) -> int:
    import argparse

    from sqlalchemy.ext.asyncio import AsyncSession

    from models.base import create_owner_engine
    from services import tenancy

    parser = argparse.ArgumentParser(prog="python -m services.tenant_keys")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify", help="count secret values per format and decrypt failures (read-only)")
    up = sub.add_parser("upgrade", help="re-encrypt values still under the installation key")
    up.add_argument("--tenant", type=int, action="append", help="tenant id (default: all)")
    rot = sub.add_parser("rotate", help="new data key for a tenant, re-encrypt its secrets")
    rot.add_argument("--tenant", type=int, required=True)
    args = parser.parse_args(argv)

    import models  # noqa: F401 — registers the models and the tenant filter

    engine = create_owner_engine()
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            if args.cmd == "verify":
                report = await scan(db)
                print(_format_report(report))
                return 1 if report["failures"] else 0
            if args.cmd == "upgrade":
                from sqlalchemy import select

                from models.tenant import Tenant

                with tenancy.system_scope("tenant keys: list tenants"):
                    ids = args.tenant or list((await db.execute(select(Tenant.id))).scalars())
                failed = 0
                for tid in ids:
                    with tenancy.system_scope(f"tenant keys: upgrade tenant {tid}"):
                        r = await reencrypt_legacy(db, tid)
                    failed += sum(r.failed.values())
                    print(f"tenant {tid}: {r.converted} re-encrypted under {r.key_id}, "
                          f"{r.unchanged} unchanged, {sum(r.failed.values())} failed")
                await db.commit()
                return 1 if failed else 0
            with tenancy.system_scope(f"tenant keys: rotate tenant {args.tenant}"):
                r = await rotate_tenant_key(db, args.tenant)
            await db.commit()
            print(f"tenant {args.tenant}: new key {r.key_id}, {r.converted} re-encrypted, "
                  f"{sum(r.failed.values())} failed, retired {', '.join(r.retired) or '-'}")
            return 1 if r.failed else 0
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    import asyncio
    import sys

    logging.basicConfig(level=logging.WARNING, format="[tenant-keys] %(message)s")
    return asyncio.run(_cli(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
