"""Predictor evaluation harness.

Makes the correlation/precursor engine measurable by scoring it against
operator feedback. Each incident can be labeled 'real' or 'noise' (see the
feedback loop in routers/api_v1.py); from those labels we derive per-rule and
overall precision / noise-rate metrics.

This intentionally operates purely over the relational ``incidents`` table
(SQLAlchemy ORM), so it is cheap and always available. A full historical
backtest — replaying ClickHouse syslog windows against learned precursor
patterns to estimate recall and lead-time accuracy on un-fired events — is a
documented future step and is NOT done here.
"""
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.incident import Incident


def _metrics(real: int, noise: int) -> tuple[float | None, float | None]:
    """Return (precision, noise_rate) given real/noise counts.

    precision  = real / (real + noise)   when (real+noise) > 0 else None
    noise_rate = noise / (real + noise)   when (real+noise) > 0 else None
    """
    labeled = real + noise
    if labeled == 0:
        return None, None
    return real / labeled, noise / labeled


async def compute_eval(db: AsyncSession) -> dict:
    """Compute predictor quality metrics from labeled incidents.

    Returns a JSON-serializable dict::

        {
          "overall": {total, labeled, real, noise, precision, noise_rate},
          "by_rule": [{rule, total, real, noise, precision, noise_rate}, ...],
        }

    ``total`` counts every incident for the scope (labeled + unlabeled);
    ``labeled`` counts only those with feedback in ('real', 'noise'). Precision
    and noise_rate are computed over labeled incidents only.
    """
    total = (await db.execute(select(func.count(Incident.id)))).scalar_one()

    # Per-rule labeled counts grouped by (rule, feedback).
    rows = (await db.execute(
        select(Incident.rule, Incident.feedback, func.count(Incident.id))
        .where(Incident.feedback.in_(["real", "noise"]))
        .group_by(Incident.rule, Incident.feedback)
    )).all()

    # Per-rule total counts (labeled + unlabeled) for the "total" field.
    rule_totals = dict((await db.execute(
        select(Incident.rule, func.count(Incident.id)).group_by(Incident.rule)
    )).all())

    by_rule: dict[str, dict[str, int]] = {}
    overall_real = 0
    overall_noise = 0
    for rule, feedback, count in rows:
        bucket = by_rule.setdefault(rule, {"real": 0, "noise": 0})
        bucket[feedback] = count
        if feedback == "real":
            overall_real += count
        else:
            overall_noise += count

    overall_precision, overall_noise_rate = _metrics(overall_real, overall_noise)
    overall = {
        "total": int(total),
        "labeled": overall_real + overall_noise,
        "real": overall_real,
        "noise": overall_noise,
        "precision": overall_precision,
        "noise_rate": overall_noise_rate,
    }

    by_rule_list = []
    for rule in sorted(by_rule):
        real = by_rule[rule]["real"]
        noise = by_rule[rule]["noise"]
        precision, noise_rate = _metrics(real, noise)
        by_rule_list.append({
            "rule": rule,
            "total": int(rule_totals.get(rule, real + noise)),
            "real": real,
            "noise": noise,
            "precision": precision,
            "noise_rate": noise_rate,
        })

    return {"overall": overall, "by_rule": by_rule_list}
