"""Per-job staleness overrides shared by the backup integrations (PBS, Veeam, Synology).

The tenant threshold ("backup older than 48 h") applies to every job of an
instance. A weekly job or a retired backup group kept on purpose needs its own
limit, so an instance may carry a small text of rules, one per line::

    weekly-* = 200        # hours
    archive-vm = off      # never stale
    # comments and blank lines are ignored

Matching is a case-insensitive glob against the job name; the first matching
line wins; no match leaves the tenant threshold in force. A malformed line is
ignored and reported, never silently turned into "off".

Standard library only: this module ships in the probe's collector pack.
"""
from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase

MAX_HOURS = 24 * 365 * 10  # ten years
MAX_TEXT = 64 * 1024
MAX_RULES = 500


@dataclass(frozen=True)
class OverrideRule:
    pattern: str
    hours: float | None  # None = "off" (never stale)
    line: int = 0


def parse_overrides(text) -> tuple[list[OverrideRule], list[str]]:
    """Rules of an overrides text plus one readable error per malformed line."""
    if text is None or text == "":
        return [], []
    if not isinstance(text, str):
        return [], ["Overrides must be text"]
    if len(text) > MAX_TEXT:
        return [], [f"Overrides exceed {MAX_TEXT // 1024} KB"]
    rules: list[OverrideRule] = []
    errors: list[str] = []
    for no, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        pattern, sep, value = line.rpartition("=")
        pattern, value = pattern.strip(), value.strip()
        if not sep:
            errors.append(f"Line {no}: expected '<pattern> = <hours>' or '<pattern> = off'")
            continue
        if not pattern:
            errors.append(f"Line {no}: the name pattern is empty")
            continue
        if value.lower() == "off":
            hours: float | None = None
        else:
            try:
                hours = float(value)
            except ValueError:
                errors.append(f"Line {no}: '{value}' is not a number of hours or 'off'")
                continue
            if not (0 < hours <= MAX_HOURS):  # also rejects nan
                errors.append(f"Line {no}: hours must be above 0 and at most {MAX_HOURS}")
                continue
        if len(rules) >= MAX_RULES:
            errors.append(f"Line {no}: too many rules (limit {MAX_RULES})")
            continue
        rules.append(OverrideRule(pattern=pattern, hours=hours, line=no))
    return rules, errors


def find_override(rules: list[OverrideRule], *names: str) -> OverrideRule | None:
    """The first rule (in file order) matching any of ``names``, else None."""
    lowered = [n.lower() for n in names if n]
    for rule in rules:
        pat = rule.pattern.lower()
        if any(fnmatchcase(n, pat) for n in lowered):
            return rule
    return None


def effective_limit(rules: list[OverrideRule], default: float, *names: str) -> float:
    """Staleness limit in hours for a job; 0 means "never stale"."""
    rule = find_override(rules, *names)
    if rule is None:
        return default
    return 0.0 if rule.hours is None else rule.hours


def override_label(rules: list[OverrideRule], *names: str) -> str | None:
    """Snapshot form of the matching override: "off", "200 h" or None."""
    rule = find_override(rules, *names)
    if rule is None:
        return None
    return "off" if rule.hours is None else f"{rule.hours:g} h"


ERRORS_ALERT_TITLE = "Backup overrides contain errors"
ERRORS_ALERT_ENTITY = "stale_overrides"


def errors_alert(errors: list[str]):
    """One warning for all malformed lines of an instance (stable title and entity)."""
    from integrations._base import Alert

    more = f"; and {len(errors) - 20} more" if len(errors) > 20 else ""
    return Alert(severity="warning", title=ERRORS_ALERT_TITLE, entity=ERRORS_ALERT_ENTITY,
                 detail="; ".join(errors[:20]) + more)
