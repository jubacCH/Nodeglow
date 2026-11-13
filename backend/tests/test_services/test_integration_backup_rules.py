"""Per-job staleness overrides of the backup integrations (PBS, Veeam, Synology)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from integrations import _backup_rules as br
from integrations._base import CollectorResult
from integrations.pbs import PbsIntegration
from integrations.synobackup import SynobackupIntegration
from integrations.veeam import VeeamIntegration
from models.incident import Incident
from services import integration as int_svc
from services import integration_alerts as ia
from services.integration_ingest import CollectStamp, ingest

NOW = datetime(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc)


def ago(hours: float) -> str:
    return (NOW - timedelta(hours=hours)).isoformat()


# ── Parser ────────────────────────────────────────────────────────────────────


def test_parse_rules_comments_blanks_and_off():
    rules, errors = br.parse_overrides("# header\n\nweekly-* = 200  # weekly jobs\nArchive-VM = OFF\n  x = 1.5 \n")
    assert errors == []
    assert [(r.pattern, r.hours, r.line) for r in rules] == [
        ("weekly-*", 200.0, 3), ("Archive-VM", None, 4), ("x", 1.5, 5)]


@pytest.mark.parametrize("text", [None, "", "   \n# only a comment\n"])
def test_empty_text_is_no_rules(text):
    assert br.parse_overrides(text) == ([], [])


@pytest.mark.parametrize("line", [
    "no equals sign", "= 5", "job = ", "job = soon", "job = 0", "job = -3", "job = nan", "job = inf",
    f"job = {br.MAX_HOURS + 1}",
])
def test_malformed_lines_are_ignored_and_reported(line):
    rules, errors = br.parse_overrides(f"good = 10\n{line}\n")
    assert [r.pattern for r in rules] == ["good"]
    assert len(errors) == 1 and errors[0].startswith("Line 2:")


def test_first_matching_line_wins_and_matching_ignores_case():
    rules, _ = br.parse_overrides("Weekly-A = off\nweekly-* = 200\n")
    assert br.effective_limit(rules, 48, "WEEKLY-a") == 0
    assert br.effective_limit(rules, 48, "weekly-b") == 200
    assert br.effective_limit(rules, 48, "daily") == 48
    assert br.override_label(rules, "weekly-b") == "200 h" and br.override_label(rules, "weekly-a") == "off"
    assert br.override_label(rules, "daily") is None


def test_a_bad_text_never_raises():
    assert br.parse_overrides(5)[1]
    assert br.parse_overrides("x" * (br.MAX_TEXT + 1))[1]


# ── Plugins (direct) ──────────────────────────────────────────────────────────


def pbs_data():
    def group(name, hours):
        return {"type": "vm", "id": name, "ns": "", "name": f"vm/{name}", "last_backup": ago(hours),
                "verify_failed_count": 0}
    return {"collected_at": NOW.isoformat(), "datastores": [
        {"name": "main", "error": None, "pct": 10, "groups": [group("100", 100), group("200", 100)]},
        {"name": "usb", "error": None, "pct": 10, "groups": [group("100", 100)]},
    ], "tasks": None}


def veeam_data():
    return {"collected_at": NOW.isoformat(), "repositories": [], "jobs": [
        {"id": "1", "name": "Weekly Full", "status": "inactive", "last_result": "success", "last_run": ago(100)},
        {"id": "2", "name": "Daily", "status": "inactive", "last_result": "success", "last_run": ago(100)},
    ]}


def syno_data():
    return {"collected_at": NOW.isoformat(), "tasks": [
        {"name": "Weekly", "source": "hyper_backup", "last_result": "success", "last_run": ago(100)},
        {"name": "Daily", "source": "hyper_backup", "last_result": "success", "last_run": ago(100)},
    ]}


def overdue(alerts):
    return sorted(a.entity for a in alerts if "overdue" in a.title)


def test_pbs_override_by_group_name_and_by_store_prefix():
    inst = PbsIntegration({"stale_overrides": "main/vm/100 = off\nvm/200 = 200\n"})
    # main:vm/100 is off, main:vm/200 has 200 h, usb:vm/100 keeps the 48 h default.
    assert overdue(inst.parse_alerts(pbs_data())) == ["usb:vm/100"]
    assert overdue(PbsIntegration({}).parse_alerts(pbs_data())) == ["main:vm/100", "main:vm/200", "usb:vm/100"]


def test_veeam_override_by_job_name_and_zero_threshold_still_disables():
    inst = VeeamIntegration({"stale_overrides": "weekly* = 200"})
    assert overdue(inst.parse_alerts(veeam_data())) == ["Daily"]
    off = VeeamIntegration({})
    off.threshold_overrides = {"job_stale_hours": 0}
    assert overdue(off.parse_alerts(veeam_data())) == []
    off.config = {"stale_overrides": "daily = 24"}
    assert overdue(off.parse_alerts(veeam_data())) == ["Daily"]  # a rule may enable a job on its own


def test_synology_override_by_task_name():
    inst = SynobackupIntegration({"stale_overrides": "weekly = off"})
    assert overdue(inst.parse_alerts(syno_data())) == ["Hyper Backup: Daily"]


@pytest.mark.parametrize("cls,data", [(PbsIntegration, pbs_data), (VeeamIntegration, veeam_data),
                                      (SynobackupIntegration, syno_data)])
def test_malformed_lines_raise_one_stable_warning_and_do_not_disable_anything(cls, data):
    baseline = overdue(cls({}).parse_alerts(data()))
    inst = cls({"stale_overrides": "daily = soon\nbroken\n"})
    out = inst.parse_alerts(data())
    errs = [a for a in out if a.title == "Backup overrides contain errors"]
    assert len(errs) == 1 and errs[0].severity == "warning" and errs[0].entity == "stale_overrides"
    assert "Line 1" in errs[0].detail and "Line 2" in errs[0].detail
    assert overdue(out) == baseline  # the typo changed nothing


@pytest.mark.parametrize("cls", [PbsIntegration, VeeamIntegration, SynobackupIntegration])
def test_validate_config_reports_override_errors(cls):
    inst = cls({})
    assert inst.validate_config({"stale_overrides": "a = 1\n"}) == []
    assert inst.validate_config({}) == []
    assert len(inst.validate_config({"stale_overrides": "a = x\nb = 2\nc"})) == 2
    field = next(f for f in cls.config_fields if f.key == "stale_overrides")
    assert field.field_type == "textarea" and field.required is False and field.placeholder


@pytest.mark.parametrize("cls", [PbsIntegration, VeeamIntegration, SynobackupIntegration])
def test_garbage_overrides_do_not_break_parse_alerts(cls):
    for junk in (5, ["a"], {"a": 1}, "\x00 = 1"):
        cls({"stale_overrides": junk}).parse_alerts({"collected_at": NOW.isoformat()})


# ── collect() puts the effective override on each row ─────────────────────────


async def test_collect_marks_rows_and_reports_errors(monkeypatch):
    async def fetch_all(self):
        return veeam_data()
    monkeypatch.setattr("integrations.veeam.VeeamAPI.fetch_all", fetch_all)
    cfg = {"host": "vbr", "username": "u", "password": "p", "stale_overrides": "weekly* = 200\nbogus"}
    res = await VeeamIntegration(cfg).collect()
    assert res.success
    assert [j["stale_override"] for j in res.data["jobs"]] == ["200 h", None]
    assert len(res.data["override_errors"]) == 1


# ── The real alerting path: ingest builds the instance from the stored config ─


@pytest.fixture(autouse=True)
def _quiet():
    ia._streaks.clear()
    with patch("notifications.notify", new=AsyncMock()):
        yield
    ia._streaks.clear()


async def _titles(db):
    rows = (await db.execute(
        select(Incident).where(Incident.rule == ia.ALERT_RULE, Incident.status == "open"))).scalars()
    return sorted(i.title for i in rows)


async def test_overrides_reach_parse_alerts_through_ingest(db):
    config = {"host": "https://vbr.example.com", "username": "u", "password": "p",
              "stale_overrides": "weekly* = off\nDaily = soon"}
    cfg = await int_svc.create_config(db, "veeam", "vbr", config)
    for _ in range(2):  # an alert needs two snapshots in a row
        await ingest(db, integration_type="veeam", cfg_id=cfg.id, stamp=CollectStamp.of(cfg),
                     result=CollectorResult(success=True, data=veeam_data()),
                     integration_cls=VeeamIntegration, config=config)
        await db.commit()
    titles = await _titles(db)
    assert any("Backup job overdue" in t and "Daily" in t for t in titles), titles
    assert not any("Weekly Full" in t and "overdue" in t for t in titles), titles
    assert any("Backup overrides contain errors" in t for t in titles), titles
