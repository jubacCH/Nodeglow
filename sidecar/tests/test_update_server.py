"""Tests for the sidecar HTTP layer: run lifecycle, 409 guard, status, backups."""
import importlib.util
import json
import os
import sys
import threading
import time

SIDECAR_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, SIDECAR_DIR)


def load_server(monkeypatch, tmp_path):
    """Import update-server.py fresh with temp paths."""
    monkeypatch.setenv("UPDATE_SIDECAR_TOKEN", "test-token")
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("REPO_PATH", str(tmp_path / "repo"))
    monkeypatch.setenv("DB_CONTAINER", "vigil-db-1")
    os.makedirs(tmp_path / "backups", exist_ok=True)
    os.makedirs(tmp_path / "repo", exist_ok=True)

    spec = importlib.util.spec_from_file_location(
        "update_server", os.path.join(SIDECAR_DIR, "update-server.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_status_is_idle_before_any_run(monkeypatch, tmp_path):
    server = load_server(monkeypatch, tmp_path)
    status = server.current_status()
    assert status["run_id"] is None
    assert status["status"] == "idle"


def test_start_run_launches_runner_and_returns_202(monkeypatch, tmp_path):
    server = load_server(monkeypatch, tmp_path)
    started = threading.Event()

    def fake_runner(ctx, steps=None):
        started.set()
        return None

    code, payload = server.start_run(runner=fake_runner)

    assert code == 202
    assert payload["ok"] is True
    assert payload["run_id"]
    assert started.wait(timeout=5)


def test_second_start_while_running_returns_409(monkeypatch, tmp_path):
    server = load_server(monkeypatch, tmp_path)
    release = threading.Event()

    def slow_runner(ctx, steps=None):
        release.wait(timeout=5)
        return None

    first_code, _ = server.start_run(runner=slow_runner)
    time.sleep(0.1)
    second_code, second_payload = server.start_run(runner=slow_runner)
    release.set()

    assert first_code == 202
    assert second_code == 409
    assert "already" in second_payload["error"].lower()


def test_status_reflects_state_written_by_run(monkeypatch, tmp_path):
    server = load_server(monkeypatch, tmp_path)

    def runner(ctx, steps=None):
        from orchestrator import run_update
        return run_update(ctx, steps=[("alpha", lambda c: "done")])

    server.start_run(runner=runner)
    for _ in range(50):
        if server.current_status().get("status") == "done":
            break
        time.sleep(0.05)

    status = server.current_status()
    assert status["status"] == "done"
    assert status["steps"][0]["name"] == "alpha"


def test_status_recovers_from_state_file_after_restart(monkeypatch, tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({
        "run_id": "r1", "status": "running", "step": "migrate",
        "steps": [{"name": "migrate", "status": "running", "detail": None}],
        "started_at": "x", "finished_at": None, "error": None,
    }))
    server = load_server(monkeypatch, tmp_path)

    status = server.current_status()
    assert status["run_id"] == "r1"
    assert status["step"] == "migrate"


def _fake_docker(answers, calls):
    """A _run_cmd stand-in keyed on the first few argv tokens."""
    def run_cmd(argv, timeout=60, cwd=None):
        calls.append(argv)
        for prefix, result in answers:
            if argv[:len(prefix)] == prefix:
                return result
        return _cmd(1, "", "unexpected")
    return run_cmd


def _cmd(*args):
    from orchestrator import CmdResult
    return CmdResult(*args)


def test_db_container_explicit_env_wins(monkeypatch, tmp_path):
    server = load_server(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(server, "_run_cmd", _fake_docker([], calls))
    assert server._resolve_db_container("vigil") == "vigil-db-1"
    assert calls == []


def test_db_container_resolved_from_compose_labels(monkeypatch, tmp_path):
    server = load_server(monkeypatch, tmp_path)
    monkeypatch.delenv("DB_CONTAINER")
    calls = []
    monkeypatch.setattr(server, "_run_cmd", _fake_docker(
        [(["docker", "ps"], _cmd(0, "abc123\n", ""))], calls))

    assert server._resolve_db_container("vigil") == "abc123"
    assert "label=com.docker.compose.project=vigil" in calls[0]
    assert "label=com.docker.compose.service=db" in calls[0]


def test_db_container_falls_back_to_compose_ps_with_project(monkeypatch, tmp_path):
    server = load_server(monkeypatch, tmp_path)
    monkeypatch.delenv("DB_CONTAINER")
    calls = []
    monkeypatch.setattr(server, "_run_cmd", _fake_docker([
        (["docker", "ps"], _cmd(0, "", "")),
        (["docker", "compose"], _cmd(0, "def456\n", "")),
    ], calls))

    assert server._resolve_db_container("vigil") == "def456"
    # Without -p, compose would look up the project "repo" (the mount dir).
    assert calls[1][:4] == ["docker", "compose", "-p", "vigil"]


def test_db_container_has_no_hardcoded_legacy_default(monkeypatch, tmp_path):
    server = load_server(monkeypatch, tmp_path)
    monkeypatch.delenv("DB_CONTAINER")
    monkeypatch.setattr(server, "_run_cmd", _fake_docker([], []))
    assert server._resolve_db_container("nodeglow") == ""


def test_build_ctx_reads_settings_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("POSTGRES_USER", "nodeglow")
    monkeypatch.setenv("POSTGRES_DB", "nodeglow")
    monkeypatch.setenv("BACKUP_RETENTION", "7")
    server = load_server(monkeypatch, tmp_path)

    ctx = server.build_ctx("run-1")

    assert ctx.db_container == "vigil-db-1"
    # Falls back to the repo directory name when nothing else is available.
    assert ctx.compose_project
    assert ctx.db_user == "nodeglow"
    assert ctx.backup_retention == 7
    assert ctx.run_id == "run-1"


# ── Scheduled backups ────────────────────────────────────────────────────────

def _must_not_run(ctx):
    raise AssertionError("scheduled backup must not run")


def test_scheduled_backup_runs_and_records_status(monkeypatch, tmp_path):
    server = load_server(monkeypatch, tmp_path)
    monkeypatch.setattr(server, "build_ctx", lambda run_id: run_id)
    seen = []

    def fake_backup(ctx):
        seen.append(ctx)
        assert server.backup_schedule_status()["running"] is True
        return {"ok": True, "name": "scheduled-x.dump.gz"}

    result = server.scheduled_backup_once(backup=fake_backup)

    assert result["ok"] is True
    assert seen and seen[0].startswith("backup-")
    status = server.backup_schedule_status()
    assert status["running"] is False
    assert status["last"]["name"] == "scheduled-x.dump.gz"


def test_scheduled_backup_skips_while_an_update_runs(monkeypatch, tmp_path):
    server = load_server(monkeypatch, tmp_path)
    release = threading.Event()
    server.start_run(runner=lambda ctx, steps=None: release.wait(timeout=5) and None)
    try:
        assert server.scheduled_backup_once(backup=_must_not_run) is None
    finally:
        release.set()


def test_update_is_refused_while_a_scheduled_backup_runs(monkeypatch, tmp_path):
    server = load_server(monkeypatch, tmp_path)
    server._backup_status["running"] = True
    code, payload = server.start_run(runner=lambda ctx, steps=None: None)
    assert code == 409
    assert "backup" in payload["error"].lower()


def test_scheduler_disabled_and_invalid_specs(monkeypatch, tmp_path):
    server = load_server(monkeypatch, tmp_path)
    assert server.start_backup_scheduler("off") is None
    assert server.backup_schedule_status()["enabled"] is False

    assert server.start_backup_scheduler("nightly") is None
    status = server.backup_schedule_status()
    assert status["enabled"] is False
    assert "invalid" in status["error"]


def test_scheduler_thread_starts_and_stops(monkeypatch, tmp_path):
    server = load_server(monkeypatch, tmp_path)
    stop = threading.Event()
    thread = server.start_backup_scheduler("every 6h", stop=stop)
    try:
        assert thread is not None and thread.is_alive()
        for _ in range(100):
            if server.backup_schedule_status()["next_at"]:
                break
            time.sleep(0.01)
        status = server.backup_schedule_status()
        assert status["enabled"] is True
        assert status["next_at"]
    finally:
        stop.set()
        thread.join(timeout=2)
    assert not thread.is_alive()
