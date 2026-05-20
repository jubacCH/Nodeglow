"""Configuration accessors for the precursor predictor.

Single source of truth for the three user-tunable knobs:
- ``predictor_min_confidence``     (float, default 0.85)
- ``predictor_min_occurrences``    (int,   default 20)
- ``predictor_template_blacklist`` (JSON array of regex strings)
"""
import json
import logging
import re
from typing import Pattern

from sqlalchemy.ext.asyncio import AsyncSession

from database import get_setting

log = logging.getLogger(__name__)


DEFAULT_MIN_CONFIDENCE = 0.85
DEFAULT_MIN_OCCURRENCES = 20

# Periodic / housekeeping templates that should never count as precursors.
# Case-insensitive regex match against the LogTemplate ``template`` text.
DEFAULT_BLACKLIST_PATTERNS: list[str] = [
    r"udhcpc\[",
    r"dhclient\b",
    r"systemd-timesyncd",
    r"chronyd.*(synchronised|Selected source)",
    r"CRON\[\d+\]",
    r"logrotate",
    r"session-\d+\.scope",
    r"pam_unix.*session opened",
]


async def get_min_confidence(db: AsyncSession) -> float:
    raw = await get_setting(db, "predictor_min_confidence", "")
    if not raw:
        return DEFAULT_MIN_CONFIDENCE
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_MIN_CONFIDENCE
    if not (0.0 < val <= 1.0):
        return DEFAULT_MIN_CONFIDENCE
    return val


async def get_min_occurrences(db: AsyncSession) -> int:
    raw = await get_setting(db, "predictor_min_occurrences", "")
    if not raw:
        return DEFAULT_MIN_OCCURRENCES
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MIN_OCCURRENCES
    if val < 1:
        return DEFAULT_MIN_OCCURRENCES
    return val


async def get_blacklist_regexes(db: AsyncSession) -> list[Pattern[str]]:
    """Return compiled, case-insensitive regexes for the active blacklist.

    Empty/missing setting → defaults.  Malformed JSON → defaults.
    Individually invalid regexes are skipped with a warning.
    """
    raw = await get_setting(db, "predictor_template_blacklist", "")
    patterns: list[str]
    if raw:
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, list) or not all(isinstance(p, str) for p in parsed):
                raise ValueError("blacklist must be a JSON array of strings")
            patterns = parsed
        except (json.JSONDecodeError, ValueError) as exc:
            log.warning("predictor_template_blacklist malformed (%s) — using defaults", exc)
            patterns = DEFAULT_BLACKLIST_PATTERNS
    else:
        patterns = DEFAULT_BLACKLIST_PATTERNS

    compiled: list[Pattern[str]] = []
    for p in patterns:
        try:
            compiled.append(re.compile(p, re.IGNORECASE))
        except re.error as exc:
            log.warning("predictor blacklist regex %r invalid: %s — skipped", p, exc)
    return compiled


def is_template_blacklisted(template_text: str, regexes: list[Pattern[str]]) -> bool:
    """Cheap synchronous check used after ``get_blacklist_regexes`` has run."""
    if not template_text:
        return False
    return any(rx.search(template_text) for rx in regexes)
