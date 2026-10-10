"""Tests for image-based updates (NODEGLOW_UPDATE_MODE=image).

All I/O is faked: commands are answered by a responder, the release list is
injected, cosign's presence is a lambda.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import image_update as iu  # noqa: E402
from orchestrator import CmdResult, Ctx, DumpResult, StepError, run_update  # noqa: E402

REG = "ghcr.io/jubacch"
DIGEST_B = "sha256:" + "b" * 64
DIGEST_F = "sha256:" + "f" * 64
DIGEST_U = "sha256:" + "c" * 64


def releases(*tags, prerelease=(), draft=()):
    return [{"tag_name": t, "name": f"Nodeglow {t}", "prerelease": t in prerelease,
             "draft": t in draft, "html_url": f"https://example/{t}", "body": "notes",
             "published_at": "2026-10-10T00:00:00Z"} for t in tags]


def cfg(**over):
    base = {"releases_url": "https://example/releases", "allow_prerelease": False,
            "verify": True, "cert_identity_re": iu.DEFAULT_CERT_IDENTITY_RE,
            "oidc_issuer": iu.DEFAULT_OIDC_ISSUER}
    base.update(over)
    return base


def write_env(repo, version="1.0.0", extra=""):
    os.makedirs(repo, exist_ok=True)
    with open(os.path.join(repo, ".env"), "w") as fh:
        fh.write(f"POSTGRES_PASSWORD=x\nNODEGLOW_VERSION={version}\n{extra}")


def compose_config(version, data="/opt/nodeglow/data"):
    return json.dumps({"services": {
        "nodeglow": {"image": f"{REG}/nodeglow-backend:{version}",
                     "volumes": [{"type": "bind", "source": data, "target": "/data"}]},
        "frontend": {"image": f"{REG}/nodeglow-frontend:{version}"},
        "updater": {"image": f"{REG}/nodeglow-updater:{version}"},
        "db": {"image": "postgres:16-alpine"},
    }})


DIGESTS = {"nodeglow-backend": DIGEST_B, "nodeglow-frontend": DIGEST_F,
           "nodeglow-updater": DIGEST_U}

NEW_DEPLOY = {"docker-compose.yml": "# compose 1.1.0\n",
              "clickhouse/config.xml": "<new/>\n",
              "clickhouse/init.sql": "-- new\n"}
OLD_DEPLOY = {"docker-compose.yml": "# compose 1.0.0\n",
              "clickhouse/config.xml": "<old/>\n",
              "clickhouse/init.sql": "-- old\n"}


def read(repo, rel):
    with open(os.path.join(repo, rel)) as fh:
        return fh.read()


class Docker:
    """A fake docker/cosign CLI recording every call."""

    def __init__(self, target="1.1.0", cosign_rc=0, pull_digest_override=None,
                 running_data="/opt/nodeglow/data", deploy_files=None, migrate_rc=0):
        self.calls = []
        self.deploy_files = NEW_DEPLOY if deploy_files is None else deploy_files
        self.migrate_rc = migrate_rc
        self.target = target
        self.cosign_rc = cosign_rc
        self.pull_digest_override = pull_digest_override
        self.running_data = running_data

    def _version_from(self, argv):
        for a in argv:
            if a.startswith("NODEGLOW_VERSION="):
                return a.split("=", 1)[1]
        return "1.0.0"

    def __call__(self, argv, timeout=60, cwd=None):
        self.calls.append(argv)
        if "config" in argv and "json" in argv:
            return CmdResult(0, compose_config(self._version_from(argv)), "")
        if argv[:3] == ["docker", "inspect", "-f"] and argv[3] == "{{.State.Running}}":
            return CmdResult(0, "true", "")
        if argv[:3] == ["docker", "inspect", "-f"] and argv[3] == "{{json .Mounts}}":
            return CmdResult(0, json.dumps([{"Type": "bind", "Source": self.running_data,
                                             "Destination": "/data"}]), "")
        if argv[0] == "cosign":
            image = argv[2]
            repo = image.rsplit("/", 1)[1].split(":")[0]
            if self.cosign_rc:
                return CmdResult(self.cosign_rc, "", "no matching signatures")
            out = [{"critical": {"image": {"docker-manifest-digest": DIGESTS[repo]}}}]
            return CmdResult(0, json.dumps(out), "")
        if argv[:3] == ["docker", "image", "inspect"]:
            image = argv[-1]
            name = image.rsplit(":", 1)[0]
            repo = name.rsplit("/", 1)[1]
            digest = self.pull_digest_override or DIGESTS[repo]
            return CmdResult(0, json.dumps([f"{name}@{digest}"]), "")
        if argv[:2] == ["docker", "run"]:
            return CmdResult(0, "abc123", "")
        if argv[:2] in (["docker", "create"], ["docker", "rm"]):
            return CmdResult(0, "cid", "")
        if argv[:2] == ["docker", "cp"]:
            if self.deploy_files:
                dest = argv[3]
                for rel, content in self.deploy_files.items():
                    path = os.path.join(dest, rel)
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "w") as fh:
                        fh.write(content)
            return CmdResult(0, "", "")
        if self.migrate_rc and "alembic" in argv:
            return CmdResult(self.migrate_rc, "", "relation already exists")
        if "docker" in argv and "compose" in argv:
            return CmdResult(0, "ok", "")
        raise AssertionError(f"unexpected command: {argv}")


def make_ctx(tmp_path, run_cmd, **over):
    repo = str(tmp_path / "install")
    if not os.path.exists(os.path.join(repo, ".env")):
        write_env(repo)
    for rel, content in OLD_DEPLOY.items():
        os.makedirs(os.path.dirname(os.path.join(repo, rel)), exist_ok=True)
        with open(os.path.join(repo, rel), "w") as fh:
            fh.write(content)
    defaults = dict(
        run_cmd=run_cmd,
        run_dump=lambda argv, dest, timeout=1800: (open(dest, "wb").close() or DumpResult(0, 10, "")),
        now=lambda: "2026-10-10T12:00:00",
        disk_free=lambda p: 50 * 1024**3,
        path_exists=lambda p: True,
        log=lambda m: None,
        repo_path=repo,
        compose_file=os.path.join(repo, "docker-compose.yml"),
        compose_project="nodeglow",
        backup_dir=str(tmp_path / "backups"),
        backup_retention=5,
        db_container="nodeglow-db-1",
        db_user="nodeglow",
        db_name="nodeglow",
        state_path=str(tmp_path / "state.json"),
        run_id="2026-10-10T12-00-00",
        project_dir="/opt/nodeglow",
    )
    defaults.update(over)
    os.makedirs(defaults["backup_dir"], exist_ok=True)
    return Ctx(**defaults)


def steps(rels=None, conf=None, cosign=True):
    rels = rels if rels is not None else releases("v1.0.0", "v1.1.0", "v1.0.1")
    conf = conf or cfg()
    return iu.image_steps(fetch_json=lambda url: rels, cfg_fn=lambda: conf,
                          which=lambda name: "/usr/local/bin/cosign" if cosign else None)


# ── Versions ─────────────────────────────────────────────────────────────────

def test_semver_ordering_puts_release_above_its_prereleases():
    ordered = sorted(["1.10.0", "1.2.0", "1.2.0-rc.1", "1.2.0-rc.10", "1.2.0-rc.2", "v0.9.9"],
                     key=iu.semver_key)
    assert ordered == ["v0.9.9", "1.2.0-rc.1", "1.2.0-rc.2", "1.2.0-rc.10", "1.2.0", "1.10.0"]


def test_semver_rejects_garbage():
    assert iu.parse_semver("main") is None
    with pytest.raises(ValueError):
        iu.semver_key("latest")


def test_newer_releases_skips_drafts_prereleases_and_older():
    rels = releases("v0.9.0", "v1.0.0", "v1.1.0", "v1.2.0-rc.1", "v1.3.0", "nightly",
                    prerelease=("v1.2.0-rc.1",), draft=("v1.3.0",))
    found = iu.newer_releases(rels, "1.0.0", allow_prerelease=False)
    assert [r["version"] for r in found] == ["1.1.0"]


def test_newer_releases_treats_suffix_as_prerelease_even_without_flag():
    found = iu.newer_releases(releases("v1.1.0-beta.1"), "1.0.0", allow_prerelease=False)
    assert found == []
    found = iu.newer_releases(releases("v1.1.0-beta.1"), "1.0.0", allow_prerelease=True)
    assert [r["version"] for r in found] == ["1.1.0-beta.1"]


def test_newer_releases_never_downgrades():
    assert iu.newer_releases(releases("v1.0.0", "v0.5.0"), "1.0.0", False) == []


# ── .env handling ────────────────────────────────────────────────────────────

def test_env_roundtrip_keeps_other_lines_and_dedupes(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# comment\nA=1\nNODEGLOW_VERSION=1.0.0\nB='two'\nNODEGLOW_VERSION=0.9\n")
    assert iu.read_env_value(str(path), "NODEGLOW_VERSION") == "0.9"
    assert iu.read_env_value(str(path), "B") == "two"
    iu.write_env_value(str(path), "NODEGLOW_VERSION", "1.1.0")
    assert path.read_text() == "# comment\nA=1\nNODEGLOW_VERSION=1.1.0\nB='two'\n"


def test_env_write_appends_missing_key(tmp_path):
    path = tmp_path / ".env"
    path.write_text("A=1")
    iu.write_env_value(str(path), "NODEGLOW_VERSION", "2.0.0")
    assert path.read_text() == "A=1\nNODEGLOW_VERSION=2.0.0\n"


# ── /check ───────────────────────────────────────────────────────────────────

def test_check_reports_newer_releases_in_backend_shape(tmp_path):
    write_env(str(tmp_path))
    result = iu.check_for_updates(str(tmp_path), cfg(),
                                  fetch_json=lambda url: releases("v1.0.0", "v1.0.1", "v1.1.0"))
    assert result["update_available"] is True
    assert result["commits_behind"] == 2
    assert result["latest_version"] == "1.1.0"
    assert [c["hash"] for c in result["changelog"]] == ["v1.1.0", "v1.0.1"]


def test_check_disabled_for_air_gapped(tmp_path):
    write_env(str(tmp_path))
    result = iu.check_for_updates(str(tmp_path), cfg(releases_url=""),
                                  fetch_json=lambda url: pytest.fail("must not fetch"))
    assert result["update_available"] is False
    assert "offline bundle" in result["error"]


def test_check_survives_network_errors(tmp_path):
    write_env(str(tmp_path))

    def boom(url):
        raise OSError("name resolution failed")

    result = iu.check_for_updates(str(tmp_path), cfg(), fetch_json=boom)
    assert result["update_available"] is False
    assert "name resolution failed" in result["error"]


def test_check_requires_pinned_version(tmp_path):
    write_env(str(tmp_path), version="latest")
    result = iu.check_for_updates(str(tmp_path), cfg(), fetch_json=lambda url: [])
    assert "not a release version" in result["error"]


def test_settings_from_environment(monkeypatch):
    monkeypatch.setenv("NODEGLOW_RELEASES_URL", "off")
    monkeypatch.setenv("NODEGLOW_VERIFY_SIGNATURES", "0")
    monkeypatch.setenv("NODEGLOW_UPDATE_CHANNEL", "prerelease")
    s = iu.settings()
    assert s["releases_url"] == "" and s["verify"] is False and s["allow_prerelease"] is True
    monkeypatch.delenv("NODEGLOW_RELEASES_URL")
    monkeypatch.delenv("NODEGLOW_VERIFY_SIGNATURES")
    s = iu.settings()
    assert s["releases_url"] == iu.DEFAULT_RELEASES_URL and s["verify"] is True


# ── Full run ─────────────────────────────────────────────────────────────────

def test_full_image_update_run(tmp_path):
    docker = Docker()
    ctx = make_ctx(tmp_path, docker)

    state = run_update(ctx, steps=steps())

    assert state.status == "done", state.error
    assert [s.name for s in state.steps] == iu.IMAGE_STEP_NAMES
    # .env pinned to the new version, old one kept
    assert iu.read_env_value(os.path.join(ctx.repo_path, ".env"), "NODEGLOW_VERSION") == "1.1.0"
    assert os.path.exists(os.path.join(ctx.repo_path, ".env.bak-2026-10-10T12-00-00"))
    # the release's own deploy files replaced the old ones, old ones kept
    for rel, content in NEW_DEPLOY.items():
        assert read(ctx.repo_path, rel) == content
        assert read(ctx.repo_path, rel + ".bak-2026-10-10T12-00-00") == OLD_DEPLOY[rel]
    assert not os.path.exists(os.path.join(ctx.repo_path, iu.STAGING_DIRNAME))
    # the staging container is always removed
    assert any(c[:3] == ["docker", "rm", "-f"] for c in docker.calls)
    # every Nodeglow image was verified with the release workflow identity
    cosign = [c for c in docker.calls if c[0] == "cosign"]
    assert {c[2] for c in cosign} == {f"{REG}/nodeglow-backend:1.1.0",
                                      f"{REG}/nodeglow-frontend:1.1.0",
                                      f"{REG}/nodeglow-updater:1.1.0"}
    assert all(iu.DEFAULT_CERT_IDENTITY_RE in c for c in cosign)
    # pull, migrate and restart all ran at the new version
    for verb in ("pull", "run", "up"):
        call = next(c for c in docker.calls if "compose" in c and verb in c and c[0] == "env")
        assert "NODEGLOW_VERSION=1.1.0" in call
    # nothing was built
    assert not any("build" in c for c in docker.calls)
    # the updater hands off to a helper from the new image
    run = next(c for c in docker.calls if c[:2] == ["docker", "run"])
    assert f"{REG}/nodeglow-updater:1.1.0" in run
    assert state.by_name("resolve").detail == "1.0.0 → 1.1.0"


def test_verification_failure_stops_before_backup_and_changes_nothing(tmp_path):
    docker = Docker(cosign_rc=1)
    ctx = make_ctx(tmp_path, docker)

    state = run_update(ctx, steps=steps())

    assert state.status == "failed"
    assert state.by_name("verify").status == "failed"
    assert state.by_name("backup").status == "pending"
    assert "signature verification failed" in state.error
    assert iu.read_env_value(os.path.join(ctx.repo_path, ".env"), "NODEGLOW_VERSION") == "1.0.0"
    assert not any("pull" in c for c in docker.calls)


def test_missing_cosign_fails_closed(tmp_path):
    ctx = make_ctx(tmp_path, Docker())
    state = run_update(ctx, steps=steps(cosign=False))
    assert state.status == "failed"
    assert "cosign not found" in state.error


def test_verification_can_be_disabled_explicitly(tmp_path):
    docker = Docker()
    ctx = make_ctx(tmp_path, docker)
    state = run_update(ctx, steps=steps(conf=cfg(verify=False), cosign=False))
    assert state.status == "done", state.error
    assert state.by_name("verify").detail == "signature verification DISABLED"
    assert not any(c[0] == "cosign" for c in docker.calls)


def test_digest_mismatch_after_pull_fails_before_migrate(tmp_path):
    docker = Docker(pull_digest_override="sha256:" + "0" * 64)
    ctx = make_ctx(tmp_path, docker)
    state = run_update(ctx, steps=steps())
    assert state.status == "failed"
    assert state.by_name("pull").status == "failed"
    assert state.by_name("migrate").status == "pending"
    assert "does not match the verified digest" in state.error
    assert iu.read_env_value(os.path.join(ctx.repo_path, ".env"), "NODEGLOW_VERSION") == "1.0.0"


def test_updater_image_without_deploy_files_is_refused(tmp_path):
    ctx = make_ctx(tmp_path, Docker(deploy_files={}))
    state = run_update(ctx, steps=steps())
    assert state.status == "failed"
    assert state.by_name("stage").status == "failed"
    assert "carries no deploy files" in state.error
    assert read(ctx.repo_path, "docker-compose.yml") == OLD_DEPLOY["docker-compose.yml"]


def test_failed_migration_restores_compose_file_and_version(tmp_path):
    docker = Docker(migrate_rc=1)
    ctx = make_ctx(tmp_path, docker)

    state = run_update(ctx, steps=steps())

    assert state.status == "failed"
    assert state.by_name("migrate").status == "failed"
    assert state.by_name("restart").status == "pending"
    assert iu.read_env_value(os.path.join(ctx.repo_path, ".env"), "NODEGLOW_VERSION") == "1.0.0"
    for rel, content in OLD_DEPLOY.items():
        assert read(ctx.repo_path, rel) == content
    assert not any(c[0] == "env" and "up" in c for c in docker.calls)


def test_install_reverts_when_new_compose_moves_data(tmp_path):
    class Moving(Docker):
        def __call__(self, argv, timeout=60, cwd=None):
            if "config" in argv and "json" in argv and "NODEGLOW_VERSION=1.1.0" in argv:
                return CmdResult(0, compose_config("1.1.0", data="/elsewhere/data"), "")
            return super().__call__(argv, timeout, cwd)

    ctx = make_ctx(tmp_path, Moving())
    state = run_update(ctx, steps=steps())
    assert state.status == "failed"
    assert state.by_name("install").status == "failed"
    assert read(ctx.repo_path, "docker-compose.yml") == OLD_DEPLOY["docker-compose.yml"]
    assert iu.read_env_value(os.path.join(ctx.repo_path, ".env"), "NODEGLOW_VERSION") == "1.0.0"


def test_no_newer_release_stops_in_resolve(tmp_path):
    ctx = make_ctx(tmp_path, Docker())
    state = run_update(ctx, steps=steps(rels=releases("v1.0.0", "v0.9.0")))
    assert state.status == "failed"
    assert state.by_name("resolve").status == "failed"
    assert "nothing to do" in state.error


def test_preflight_refuses_unpinned_version(tmp_path):
    write_env(str(tmp_path / "install"), version="")
    ctx = make_ctx(tmp_path, Docker())
    with pytest.raises(StepError, match="not a release version"):
        iu.step_image_preflight(ctx)


def test_preflight_refuses_moving_data_dir(tmp_path):
    ctx = make_ctx(tmp_path, Docker(running_data="/srv/elsewhere/data"))
    with pytest.raises(StepError, match="Refusing to update"):
        iu.step_image_preflight(ctx)


def test_resolve_rejects_compose_without_version_tag(tmp_path):
    class Fixed(Docker):
        def __call__(self, argv, timeout=60, cwd=None):
            if "config" in argv and "json" in argv:
                return CmdResult(0, compose_config("latest"), "")
            return super().__call__(argv, timeout, cwd)

    ctx = make_ctx(tmp_path, Fixed())
    ctx.plan["current"] = "1.0.0"
    resolve = iu.make_step_resolve(fetch_json=lambda url: releases("v1.1.0"), cfg_fn=cfg)
    with pytest.raises(StepError, match="is not tagged with 1.1.0"):
        resolve(ctx)


def test_handoff_without_host_dir_asks_for_manual_step(tmp_path):
    ctx = make_ctx(tmp_path, Docker(), project_dir="")
    ctx.plan["images"] = {"updater": f"{REG}/nodeglow-updater:1.1.0"}
    assert "docker compose up -d updater" in iu.step_handoff(ctx)


def test_handoff_failure_is_not_fatal(tmp_path):
    def run_cmd(argv, timeout=60, cwd=None):
        return CmdResult(125, "", "Conflict. The container name is already in use")

    ctx = make_ctx(tmp_path, run_cmd)
    ctx.plan["images"] = {"updater": f"{REG}/nodeglow-updater:1.1.0"}
    assert "updater not recreated" in iu.step_handoff(ctx)


def test_verified_digest_parsing():
    assert iu._verified_digest(json.dumps([{"critical": {"image": {
        "docker-manifest-digest": DIGEST_B}}}])) == DIGEST_B
    assert iu._verified_digest("garbage " + DIGEST_F) == DIGEST_F
    assert iu._verified_digest("nothing") == ""
