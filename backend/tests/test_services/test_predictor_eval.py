"""Tests for the predictor eval harness (services.predictor_eval.compute_eval)."""
from models.incident import Incident
from services.predictor_eval import compute_eval


async def _add(db, rule, feedback):
    inc = Incident(rule=rule, title="t", severity="warning", status="open", feedback=feedback)
    db.add(inc)
    return inc


async def test_compute_eval_overall_and_per_rule(db):
    # Rule A: 3 real, 1 noise → precision 0.75, noise_rate 0.25
    for _ in range(3):
        await _add(db, "learned_precursor", "real")
    await _add(db, "learned_precursor", "noise")
    # Rule B: 1 real, 3 noise → precision 0.25, noise_rate 0.75
    await _add(db, "host_down_syslog", "real")
    for _ in range(3):
        await _add(db, "host_down_syslog", "noise")
    # Unlabeled incidents that must be excluded from labeled metrics
    await _add(db, "learned_precursor", None)
    await _add(db, "host_down_syslog", None)
    await db.commit()

    res = await compute_eval(db)

    overall = res["overall"]
    assert overall["total"] == 10  # 8 labeled + 2 unlabeled
    assert overall["labeled"] == 8
    assert overall["real"] == 4
    assert overall["noise"] == 4
    assert overall["precision"] == 0.5
    assert overall["noise_rate"] == 0.5

    by_rule = {r["rule"]: r for r in res["by_rule"]}
    assert by_rule["learned_precursor"]["real"] == 3
    assert by_rule["learned_precursor"]["noise"] == 1
    assert by_rule["learned_precursor"]["precision"] == 0.75
    assert by_rule["learned_precursor"]["noise_rate"] == 0.25
    assert by_rule["learned_precursor"]["total"] == 5  # 4 labeled + 1 unlabeled

    assert by_rule["host_down_syslog"]["real"] == 1
    assert by_rule["host_down_syslog"]["noise"] == 3
    assert by_rule["host_down_syslog"]["precision"] == 0.25


async def test_compute_eval_no_labels(db):
    await _add(db, "learned_precursor", None)
    await db.commit()
    res = await compute_eval(db)
    assert res["overall"]["labeled"] == 0
    assert res["overall"]["precision"] is None
    assert res["overall"]["noise_rate"] is None
    assert res["by_rule"] == []
