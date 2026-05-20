"""Tests for predictor configuration helper."""
import json

import pytest

from database import set_setting
from services.predictor_config import (
    DEFAULT_BLACKLIST_PATTERNS,
    get_blacklist_regexes,
    get_min_confidence,
    get_min_occurrences,
    is_template_blacklisted,
)


async def test_defaults_when_no_settings_present(db):
    assert await get_min_confidence(db) == pytest.approx(0.85)
    assert await get_min_occurrences(db) == 20
    regexes = await get_blacklist_regexes(db)
    assert len(regexes) == len(DEFAULT_BLACKLIST_PATTERNS)


async def test_settings_override_defaults(db):
    await set_setting(db, "predictor_min_confidence", "0.9")
    await set_setting(db, "predictor_min_occurrences", "50")
    await db.commit()

    assert await get_min_confidence(db) == pytest.approx(0.9)
    assert await get_min_occurrences(db) == 50


async def test_invalid_settings_fall_back_to_defaults(db):
    await set_setting(db, "predictor_min_confidence", "not-a-float")
    await set_setting(db, "predictor_min_occurrences", "-5")
    await db.commit()

    assert await get_min_confidence(db) == pytest.approx(0.85)
    assert await get_min_occurrences(db) == 20


async def test_udhcpc_template_is_blacklisted_by_default(db):
    regexes = await get_blacklist_regexes(db)
    assert is_template_blacklisted("udhcpc[1234]: sending renew to server <*>", regexes)
    assert is_template_blacklisted("UDHCPC[1]: Lease of <*> obtained", regexes)


async def test_normal_template_not_blacklisted(db):
    regexes = await get_blacklist_regexes(db)
    assert not is_template_blacklisted("Failed password for <*> from <*> port <*>", regexes)
    assert not is_template_blacklisted("kernel: <*> oom-killer invoked", regexes)


async def test_custom_blacklist_via_setting(db):
    await set_setting(db, "predictor_template_blacklist", json.dumps(["my-noisy-app"]))
    await db.commit()

    regexes = await get_blacklist_regexes(db)
    # Custom replaces defaults entirely (explicit user intent).
    assert is_template_blacklisted("my-noisy-app: heartbeat", regexes)
    assert not is_template_blacklisted("udhcpc[1]: sending renew", regexes)


async def test_malformed_blacklist_json_falls_back_to_defaults(db):
    await set_setting(db, "predictor_template_blacklist", "{not valid json")
    await db.commit()

    regexes = await get_blacklist_regexes(db)
    assert is_template_blacklisted("udhcpc[1]: sending renew", regexes)


async def test_invalid_regex_in_blacklist_is_skipped(db):
    await set_setting(db, "predictor_template_blacklist", json.dumps(["[invalid(", "udhcpc"]))
    await db.commit()

    regexes = await get_blacklist_regexes(db)
    # Bad regex skipped, good one still compiled.
    assert len(regexes) == 1
    assert is_template_blacklisted("udhcpc[1]: sending renew", regexes)
