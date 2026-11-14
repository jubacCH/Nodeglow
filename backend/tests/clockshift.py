"""pytest plugin: run the suite as if it were ``NG_SHIFT_DAYS`` days later.

    NG_SHIFT_DAYS=30 python -m pytest -p tests.clockshift -q

A test whose fixtures carry fixed dates passes on the day it is written and
fails on its own some days later, on every branch at once, when "two hours
ago" has become "overdue". Three such tests turned red within one day in
November 2026 and blocked every pull request until they were found. Run
ahead of the clock, they fail before they are merged.

How: ``datetime.datetime``, ``datetime.date`` and ``time.time`` are replaced
by shifted ones before the code under test imports them (``-p`` loads this
module first). Without ``NG_SHIFT_DAYS`` the plugin does nothing.

Limits: the database's own clock (``func.now()``) is not moved, and neither
is a module that took its reference to ``datetime`` before this one loaded.
"""
from __future__ import annotations

import datetime as _dt
import os
import time as _time

OFFSET = _dt.timedelta(days=float(os.environ.get("NG_SHIFT_DAYS") or 0))

real_datetime = _dt.datetime
real_date = _dt.date
real_time = _time.time


class _DatetimeMeta(type):
    # Values that come from drivers and libraries are real datetimes; code
    # that asks isinstance(value, datetime) must still get yes.
    def __instancecheck__(cls, obj):
        return isinstance(obj, real_datetime)

    def __subclasscheck__(cls, sub):
        return issubclass(sub, real_datetime)


class ShiftedDatetime(real_datetime, metaclass=_DatetimeMeta):
    @classmethod
    def now(cls, tz=None):
        return real_datetime.now(tz) + OFFSET

    @classmethod
    def utcnow(cls):
        return real_datetime.utcnow() + OFFSET

    @classmethod
    def today(cls):
        return real_datetime.today() + OFFSET


class _DateMeta(type):
    def __instancecheck__(cls, obj):
        return isinstance(obj, real_date)

    def __subclasscheck__(cls, sub):
        return issubclass(sub, real_date)


class ShiftedDate(real_date, metaclass=_DateMeta):
    @classmethod
    def today(cls):
        return (real_datetime.now() + OFFSET).date()


def _warm_cryptography() -> None:
    """Let cryptography look up the datetime class while it is the real one.

    Its compiled code checks the validity dates of a certificate against
    ``datetime.datetime``, fetched once on first use and kept. Fetched after
    the swap it is the shifted class, and that check, unlike ``isinstance``
    in Python, refuses every real datetime. Signing one certificate here
    makes the first use happen now.
    """
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "clockshift")])
        now = real_datetime(2026, 1, 1, tzinfo=_dt.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(1)
                .not_valid_before(now).not_valid_after(now + _dt.timedelta(days=1))
                .sign(key, hashes.SHA256()))
        # Reading dates back goes through the same cached class.
        _ = cert.not_valid_after_utc
    except Exception:  # noqa: BLE001 - not installed, or an API this does not know
        pass


def _install() -> None:
    # These derive classes of their own from date/datetime, or register
    # adapters for the exact type: they have to see the real ones first.
    for name in ("sqlite3", "pydantic", "pydantic.v1", "sqlalchemy", "httpx", "cryptography.x509"):
        try:
            __import__(name)
        except Exception:  # noqa: BLE001 - not installed, or not importable here
            pass
    import sqlite3

    _warm_cryptography()
    _dt.datetime = ShiftedDatetime
    _dt.date = ShiftedDate
    _time.time = lambda: real_time() + OFFSET.total_seconds()
    sqlite3.register_adapter(ShiftedDatetime, lambda value: value.isoformat(" "))


if OFFSET:
    _install()


def pytest_report_header(config):
    if not OFFSET:
        return None
    return f"clockshift: +{OFFSET.days} days, today reads as {_dt.datetime.utcnow():%Y-%m-%d}"
