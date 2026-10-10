"""Image-based updates: install a published release instead of building from git.

Selected with ``NODEGLOW_UPDATE_MODE=image`` (the default of
docker-compose.release.yml). The git mode in :mod:`orchestrator` stays the
default for installations that run from a checkout and build on the host.

An image-mode installation is a directory holding ``docker-compose.yml`` (the
release compose file), ``.env`` and ``clickhouse/``. The version that runs is
``NODEGLOW_VERSION`` in ``.env``; every image tag in the compose file derives
from it. An update therefore means: find a newer release, verify the images'
signatures, back up, pull, put the release's compose file in place, pin the
new version in ``.env``, migrate, restart.

Steps:

``preflight``  docker socket, disk, compose file, database, /data mount
``resolve``    newest release above the running one (GitHub Releases API)
``verify``     cosign keyless verification of every Nodeglow image; fail closed
``backup``     pg_dump, shared with git mode
``pull``       ``docker compose pull`` at the new version; digests must match
``stage``      copy the new compose file + ClickHouse config out of the new
               (verified) updater image into ``.release-staging/``
``install``    move them into place, pin ``NODEGLOW_VERSION`` in ``.env``; old
               files kept as ``*.bak-<run id>``; /data mount re-checked
``migrate``    shared with git mode, run from the new backend image; on failure
               the files of ``install`` are restored
``restart``    shared with git mode
``handoff``    recreate the updater itself from a short-lived helper container

Nothing is changed before ``backup``; the files the running stack is defined
by change in ``install`` and are reverted if the migration fails. The database
is never rolled back automatically — like the git mode, the pre-update dump is
the recovery path.

Stdlib only, all I/O through :class:`orchestrator.Ctx` (plus an injectable
``fetch_json``), so the steps are unit-testable without Docker or network.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import urllib.request

from orchestrator import (
    DOCKER_SOCKET,
    MAX_ERROR_CHARS,
    MIN_FREE_BYTES,
    Ctx,
    StepError,
    _compose,
    check_data_mount,
    step_backup,
    step_migrate,
    step_restart,
)

IMAGE_STEP_NAMES = ["preflight", "resolve", "verify", "backup", "pull", "stage",
                    "install", "migrate", "restart", "handoff"]

# Release-owned files of an installation directory. The release workflow bakes
# them into the updater image at DEPLOY_DIR_IN_IMAGE (sidecar/Dockerfile).
DEPLOY_DIR_IN_IMAGE = "/app/deploy"
DEPLOY_FILES = ["docker-compose.yml", "clickhouse/config.xml", "clickhouse/init.sql"]
STAGING_DIRNAME = ".release-staging"

DEFAULT_RELEASES_URL = "https://api.github.com/repos/jubacCH/Nodeglow/releases?per_page=50"
# Images built by .github/workflows/release.yml are signed keylessly by that
# workflow, for a tag. Anything else (a fork, a branch build) must not verify.
DEFAULT_CERT_IDENTITY_RE = (
    r"^https://github\.com/jubacCH/Nodeglow/\.github/workflows/release\.yml@refs/tags/v.*$"
)
DEFAULT_OIDC_ISSUER = "https://token.actions.githubusercontent.com"

VERSION_KEY = "NODEGLOW_VERSION"
# Services whose images are Nodeglow's own (and therefore signed). Postgres and
# ClickHouse are upstream images pinned by tag in the compose file.
SIGNED_SERVICES = ["nodeglow", "frontend", "updater"]
PULL_SERVICES = ["nodeglow", "frontend", "updater"]
VERIFY_TIMEOUT = 180
PULL_TIMEOUT = 1800

_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")


# ── Configuration ────────────────────────────────────────────────────────────


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")


def settings() -> dict:
    """Image-mode settings from the sidecar environment."""
    url = os.environ.get("NODEGLOW_RELEASES_URL", "").strip()
    return {
        "releases_url": "" if url.lower() in ("off", "none", "disabled") else (url or DEFAULT_RELEASES_URL),
        "allow_prerelease": os.environ.get("NODEGLOW_UPDATE_CHANNEL", "stable").strip().lower()
        in ("prerelease", "beta", "rc"),
        "verify": _env_flag("NODEGLOW_VERIFY_SIGNATURES", True),
        "cert_identity_re": os.environ.get("NODEGLOW_COSIGN_IDENTITY_REGEXP", "").strip()
        or DEFAULT_CERT_IDENTITY_RE,
        "oidc_issuer": os.environ.get("NODEGLOW_COSIGN_OIDC_ISSUER", "").strip()
        or DEFAULT_OIDC_ISSUER,
    }


# ── Versions ─────────────────────────────────────────────────────────────────


def parse_semver(value: str):
    """``"v1.2.3-rc.1"`` → ``(1, 2, 3, "rc.1")``; ``None`` if not semver."""
    m = _SEMVER_RE.match((value or "").strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4) or ""


def _pre_key(pre: str):
    # SemVer §11: identifiers compared one by one, numeric < alphanumeric.
    return [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split(".")]


def semver_key(value: str):
    """Sort key; a release sorts above its own pre-releases."""
    parsed = parse_semver(value)
    if parsed is None:
        raise ValueError(f"not a semantic version: {value!r}")
    major, minor, patch, pre = parsed
    return (major, minor, patch, 1 if not pre else 0, _pre_key(pre) if pre else [])


def is_prerelease(value: str) -> bool:
    parsed = parse_semver(value)
    return bool(parsed and parsed[3])


def read_env_value(path: str, key: str) -> str:
    """Value of ``key`` in a dotenv file, or ``""``. Quotes are stripped."""
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return ""
    value = ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("export "):
            stripped = stripped[len("export "):].lstrip()
        if not stripped.startswith(f"{key}="):
            continue
        value = stripped[len(key) + 1:].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
    return value  # last assignment wins, as in compose


def write_env_value(path: str, key: str, value: str) -> None:
    """Set ``key=value`` in a dotenv file atomically, keeping everything else."""
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except FileNotFoundError:
        lines = []
    out, done = [], False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("export "):
            stripped = stripped[len("export "):].lstrip()
        if stripped.startswith(f"{key}="):
            if not done:
                out.append(f"{key}={value}")
                done = True
            continue  # drop duplicates: one authoritative assignment
        out.append(line)
    if not done:
        out.append(f"{key}={value}")
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    try:
        shutil.copymode(path, tmp)
    except OSError:
        pass
    os.replace(tmp, path)


def env_path(ctx: Ctx) -> str:
    return os.path.join(ctx.repo_path, ".env")


def current_version(ctx_or_repo) -> str:
    repo = ctx_or_repo.repo_path if isinstance(ctx_or_repo, Ctx) else ctx_or_repo
    return read_env_value(os.path.join(repo, ".env"), VERSION_KEY) or os.environ.get(VERSION_KEY, "")


# ── Releases ─────────────────────────────────────────────────────────────────


def http_get_json(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "nodeglow-updater",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — fixed https URL
        return json.loads(resp.read().decode("utf-8"))


def newer_releases(releases: list, current: str, allow_prerelease: bool) -> list[dict]:
    """Published releases above ``current``, newest first.

    Drafts, unparsable tags and (unless allowed) pre-releases are skipped. A
    pre-release is also skipped when its tag carries a ``-suffix`` even if the
    GitHub flag was forgotten.
    """
    cur_key = semver_key(current)
    found = []
    for rel in releases or []:
        if not isinstance(rel, dict) or rel.get("draft"):
            continue
        tag = str(rel.get("tag_name") or "")
        if parse_semver(tag) is None:
            continue
        pre = bool(rel.get("prerelease")) or is_prerelease(tag)
        if pre and not allow_prerelease:
            continue
        if semver_key(tag) <= cur_key:
            continue
        found.append({
            "version": tag.lstrip("v"),
            "tag": tag,
            "name": rel.get("name") or tag,
            "prerelease": pre,
            "published_at": rel.get("published_at"),
            "url": rel.get("html_url"),
            "notes": (rel.get("body") or "")[:4000],
        })
    found.sort(key=lambda r: semver_key(r["version"]), reverse=True)
    return found


def check_for_updates(repo_path: str, cfg: dict | None = None, fetch_json=http_get_json) -> dict:
    """``/check`` in image mode, in the shape the backend already understands.

    ``changelog`` lists the newer releases (``hash`` carries the tag so the
    existing UI shows something meaningful); ``commits_behind`` is the number
    of newer releases.
    """
    cfg = cfg or settings()
    current = current_version(repo_path)
    base = {"mode": "image", "current_version": current, "update_available": False,
            "commits_behind": 0, "changelog": []}
    if parse_semver(current) is None:
        return {**base, "error": f"{VERSION_KEY} in .env is not a release version ({current or 'unset'})"}
    if not cfg["releases_url"]:
        return {**base, "error": "Update checks are disabled (NODEGLOW_RELEASES_URL=off). "
                                 "Update with an offline bundle, see docs/INSTALL.md."}
    try:
        releases = fetch_json(cfg["releases_url"])
    except Exception as exc:  # noqa: BLE001 — network, DNS, rate limit, JSON
        return {**base, "error": f"Release check failed: {str(exc)[-200:]}"}
    newer = newer_releases(releases, current, cfg["allow_prerelease"])
    return {
        **base,
        "update_available": bool(newer),
        "commits_behind": len(newer),
        "latest_version": newer[0]["version"] if newer else current,
        "changelog": [{"hash": r["tag"], "message": r["name"], "url": r["url"],
                       "prerelease": r["prerelease"]} for r in newer],
    }


# ── Steps ────────────────────────────────────────────────────────────────────


def step_image_preflight(ctx: Ctx) -> str:
    if not ctx.path_exists(DOCKER_SOCKET):
        raise StepError("Docker socket not available")
    if not ctx.path_exists(ctx.compose_file):
        raise StepError(f"Compose file not found at {ctx.compose_file}")
    current = current_version(ctx)
    if parse_semver(current) is None:
        raise StepError(f"{VERSION_KEY} in .env is not a release version ({current or 'unset'}); "
                        "image updates need a pinned version")
    free = ctx.disk_free(ctx.repo_path)
    if free < MIN_FREE_BYTES:
        raise StepError(f"Not enough free disk space: {free / 1024**3:.1f} GB "
                        f"(need {MIN_FREE_BYTES / 1024**3:.0f} GB)")
    if not ctx.db_container:
        raise StepError(f"Database container not found for compose project "
                        f"{ctx.compose_project!r}; set DB_CONTAINER in .env")
    inspect = ctx.run_cmd(["docker", "inspect", "-f", "{{.State.Running}}", ctx.db_container],
                          timeout=15)
    if inspect.returncode != 0 or inspect.stdout.strip() != "true":
        raise StepError(f"Database container {ctx.db_container} is not running")
    data = check_data_mount(ctx)
    ctx.plan["current"] = current
    return f"{free / 1024**3:.1f} GB free, {ctx.db_container} running, version {current}, {data}"


def _service_images(ctx: Ctx) -> dict[str, str]:
    """Image reference per service as compose resolves it (with compose_env)."""
    cfg = _compose(ctx, "config", "--format", "json", timeout=60)
    if cfg.returncode != 0:
        raise StepError(f"docker compose config failed: {cfg.stderr[-MAX_ERROR_CHARS:]}")
    try:
        services = json.loads(cfg.stdout).get("services", {})
    except ValueError as exc:
        raise StepError(f"docker compose config returned no JSON: {exc}") from exc
    images = {}
    for name in SIGNED_SERVICES:
        image = (services.get(name) or {}).get("image", "")
        if not image:
            raise StepError(f"service {name!r} has no image in the compose file — "
                            "image mode needs docker-compose.release.yml")
        images[name] = image
    return images


def make_step_resolve(fetch_json=http_get_json, cfg_fn=settings):
    def step_resolve(ctx: Ctx) -> str:
        cfg = cfg_fn()
        current = ctx.plan.get("current") or current_version(ctx)
        if not cfg["releases_url"]:
            raise StepError("Update checks are disabled (NODEGLOW_RELEASES_URL=off); "
                            "use an offline bundle")
        try:
            releases = fetch_json(cfg["releases_url"])
        except Exception as exc:  # noqa: BLE001
            raise StepError(f"Release check failed: {str(exc)[-200:]}") from exc
        newer = newer_releases(releases, current, cfg["allow_prerelease"])
        if not newer:
            raise StepError(f"No release newer than {current}; nothing to do")
        target = newer[0]["version"]
        ctx.plan["target"] = target
        ctx.compose_env[VERSION_KEY] = target
        ctx.plan["images"] = _service_images(ctx)
        for name, image in ctx.plan["images"].items():
            if not image.endswith(f":{target}"):
                raise StepError(f"{name} image {image} is not tagged with {target}; "
                                f"its tag must be ${{{VERSION_KEY}}}")
        return f"{current} → {target}"
    return step_resolve


def _verified_digest(output: str) -> str:
    """Digest from ``cosign verify`` JSON output (one object per signature)."""
    try:
        payloads = json.loads(output)
    except ValueError:
        payloads = None
    if isinstance(payloads, list):
        for item in payloads:
            digest = (((item or {}).get("critical") or {}).get("image") or {}).get(
                "docker-manifest-digest", "")
            if _DIGEST_RE.fullmatch(digest or ""):
                return digest
    m = _DIGEST_RE.search(output or "")
    return m.group(0) if m else ""


def make_step_verify(cfg_fn=settings, which=shutil.which):
    def step_verify(ctx: Ctx) -> str:
        cfg = cfg_fn()
        if not cfg["verify"]:
            ctx.log("WARNING: signature verification disabled (NODEGLOW_VERIFY_SIGNATURES=0)")
            return "signature verification DISABLED"
        if not which("cosign"):
            raise StepError("cosign not found in the updater image, but "
                            "NODEGLOW_VERIFY_SIGNATURES is on; refusing to install unverified images")
        digests = {}
        for name, image in ctx.plan["images"].items():
            result = ctx.run_cmd(
                ["cosign", "verify", image,
                 "--certificate-identity-regexp", cfg["cert_identity_re"],
                 "--certificate-oidc-issuer", cfg["oidc_issuer"],
                 "--output", "json"],
                timeout=VERIFY_TIMEOUT,
            )
            if result.returncode != 0:
                raise StepError(f"signature verification failed for {image}: "
                                f"{(result.stderr or result.stdout)[-MAX_ERROR_CHARS:]}")
            digest = _verified_digest(result.stdout)
            if not digest:
                raise StepError(f"cosign verified {image} but reported no digest")
            digests[image] = digest
        ctx.plan["digests"] = digests
        return f"{len(digests)} images signed by the release workflow"
    return step_verify


def step_image_pull(ctx: Ctx) -> str:
    result = _compose(ctx, "pull", *PULL_SERVICES, timeout=PULL_TIMEOUT)
    if result.returncode != 0:
        raise StepError(f"docker compose pull failed: {result.stderr[-MAX_ERROR_CHARS:]}")
    # The tag was verified, then pulled by tag: make sure both saw the same
    # manifest, so a tag moved in between cannot slip past verification.
    for image, digest in (ctx.plan.get("digests") or {}).items():
        insp = ctx.run_cmd(["docker", "image", "inspect", "--format",
                            "{{json .RepoDigests}}", image], timeout=15)
        try:
            repo_digests = json.loads(insp.stdout or "[]") if insp.returncode == 0 else []
        except ValueError:
            repo_digests = []
        if not any(str(d).endswith(f"@{digest}") for d in repo_digests or []):
            raise StepError(f"pulled {image} does not match the verified digest {digest}")
    return f"pulled {', '.join(PULL_SERVICES)} at {ctx.plan.get('target')}"


def staging_dir(ctx: Ctx) -> str:
    return os.path.join(ctx.repo_path, STAGING_DIRNAME)


def step_stage(ctx: Ctx) -> str:
    """Copy the new release's deploy files out of the (verified) updater image.

    A release may change the compose file or the ClickHouse config, so bumping
    the version alone is not enough. The release workflow bakes exactly those
    files into the updater image under /app/deploy, which makes them covered by
    the image signature checked in ``verify``. ``docker cp`` writes to the
    filesystem of the CLI — this container — so the files land in the
    installation directory through the /opt/repo mount.
    """
    image = ctx.plan["images"]["updater"]
    dest = staging_dir(ctx)
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest, exist_ok=True)
    name = f"{ctx.compose_project}-deploy-{ctx.run_id}".replace(":", "-")
    created = ctx.run_cmd(["docker", "create", "--name", name, image], timeout=60)
    if created.returncode != 0:
        raise StepError(f"docker create {image} failed: {created.stderr[-MAX_ERROR_CHARS:]}")
    try:
        copied = ctx.run_cmd(["docker", "cp", f"{name}:{DEPLOY_DIR_IN_IMAGE}/.", dest], timeout=60)
    finally:
        ctx.run_cmd(["docker", "rm", "-f", name], timeout=30)
    if copied.returncode != 0:
        raise StepError(f"could not copy deploy files from {image}: "
                        f"{copied.stderr[-MAX_ERROR_CHARS:]}")
    missing = [f for f in DEPLOY_FILES if not os.path.isfile(os.path.join(dest, f))]
    if missing:
        raise StepError(f"{image} carries no deploy files ({', '.join(missing)} missing) — "
                        "not an image built by the release workflow")
    staged = os.path.join(dest, "docker-compose.yml")
    cfg = ctx.run_cmd(["env", *(f"{k}={v}" for k, v in sorted(ctx.compose_env.items())),
                       "docker", "compose", "-p", ctx.compose_project,
                       "--env-file", env_path(ctx), "-f", staged, "config", "--quiet"],
                      timeout=60, cwd=ctx.repo_path)
    if cfg.returncode != 0:
        raise StepError("the new compose file does not render with this .env: "
                        f"{cfg.stderr[-MAX_ERROR_CHARS:]}")
    return f"{len(DEPLOY_FILES)} deploy files of {ctx.plan['target']} staged"


def _install_targets(ctx: Ctx):
    for rel in DEPLOY_FILES:
        yield (os.path.join(staging_dir(ctx), rel), os.path.join(ctx.repo_path, rel))


def revert_install(ctx: Ctx) -> None:
    """Put back the files :func:`step_install` replaced (never the database)."""
    for src, dst in ctx.plan.get("replaced", []):
        try:
            if src is None:
                os.remove(dst)
            else:
                shutil.copy2(src, dst)
        except OSError as exc:
            ctx.log(f"could not restore {dst}: {exc}")
    ctx.plan["replaced"] = []
    ctx.log("restored the previous compose file, ClickHouse config and .env")


def step_install(ctx: Ctx) -> str:
    """Put the new deploy files in place and pin the new version in .env.

    Every replaced file is kept as ``<file>.bak-<run id>``; a failed migration
    (next step) puts them back automatically.
    """
    target = ctx.plan["target"]
    replaced = ctx.plan["replaced"] = []
    suffix = f".bak-{ctx.run_id}"
    try:
        for src, dst in [*_install_targets(ctx), (None, env_path(ctx))]:
            if os.path.exists(dst):
                shutil.copy2(dst, dst + suffix)
                replaced.append((dst + suffix, dst))
            else:
                replaced.append((None, dst))
            if src is not None:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
        write_env_value(env_path(ctx), VERSION_KEY, target)
    except OSError as exc:
        revert_install(ctx)
        raise StepError(f"could not install the deploy files: {exc}") from exc
    try:
        data = check_data_mount(ctx)
    except StepError:
        revert_install(ctx)
        raise
    shutil.rmtree(staging_dir(ctx), ignore_errors=True)
    return f"{VERSION_KEY}={target}, compose file updated (previous files kept as *{suffix}); {data}"


def step_image_migrate(ctx: Ctx) -> str:
    try:
        return step_migrate(ctx)
    except Exception:
        revert_install(ctx)
        raise


def step_handoff(ctx: Ctx) -> str:
    """Recreate the updater container itself, on the new version.

    The updater cannot recreate its own container from inside: compose would
    stop it halfway through. A short-lived helper from the *new* updater image
    does it a few seconds after this run has written its final state. Best
    effort — the application is already updated, so a failure here is reported
    in the detail, not as a failed run.
    """
    image = (ctx.plan.get("images") or {}).get("updater")
    if not ctx.project_dir or not image:
        return ("updater not recreated (host directory unknown); "
                "run `docker compose up -d updater` in the installation directory")
    hd = ctx.project_dir
    env_args = [f"{k}={v}" for k, v in sorted(ctx.compose_env.items())]
    inner = " ".join(["sleep 5;", "env", *env_args, "docker", "compose", "-p", ctx.compose_project,
                      "--project-directory", hd, "-f", f"{hd}/docker-compose.yml",
                      "up", "-d", "--no-deps", "updater"])
    result = ctx.run_cmd(
        ["docker", "run", "-d", "--rm", "--name", f"{ctx.compose_project}-updater-handoff",
         "-v", f"{DOCKER_SOCKET}:{DOCKER_SOCKET}", "-v", f"{hd}:{hd}", "-w", hd,
         "--entrypoint", "sh", image, "-c", inner],
        timeout=60,
    )
    if result.returncode != 0:
        ctx.log(f"updater handoff failed: {result.stderr}")
        return ("updater not recreated: " + result.stderr[-200:]
                + " — run `docker compose up -d updater`")
    return "updater will be recreated on the new version in a few seconds"


def image_steps(fetch_json=http_get_json, cfg_fn=settings, which=shutil.which):
    return [
        ("preflight", step_image_preflight),
        ("resolve", make_step_resolve(fetch_json, cfg_fn)),
        ("verify", make_step_verify(cfg_fn, which)),
        ("backup", step_backup),
        ("pull", step_image_pull),
        ("stage", step_stage),
        ("install", step_install),
        ("migrate", step_image_migrate),
        ("restart", step_restart),
        ("handoff", step_handoff),
    ]
