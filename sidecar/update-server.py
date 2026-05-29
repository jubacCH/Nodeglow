"""Minimal update sidecar — handles git + docker operations with Docker socket access.

Listens on port 9100 (internal only). The main app calls this instead of
accessing the Docker socket directly.

Authentication
--------------
All endpoints except ``/health`` require a bearer token matching the
``UPDATE_SIDECAR_TOKEN`` environment variable (shared with the backend
container). If the variable is unset or empty, the sidecar refuses every
non-health request — fail-closed default so a misconfigured deployment
cannot be driven to pull arbitrary code + rebuild containers.
"""
import hmac
import json
import os
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler

REPO_PATH = "/opt/repo"
COMPOSE_FILE = f"{REPO_PATH}/docker-compose.yml"

# Shared secret with the backend. Must be set in the environment of both
# containers via docker-compose. An empty value means no request can mutate
# anything — this is intentional (fail-closed).
AUTH_TOKEN = os.environ.get("UPDATE_SIDECAR_TOKEN", "").strip()


class UpdateHandler(BaseHTTPRequestHandler):
    """Simple HTTP handler for update operations."""

    # Paths that never require authentication (for compose healthcheck + liveness).
    PUBLIC_PATHS = {"/health"}

    def _authorized(self) -> bool:
        """Check Bearer token using constant-time comparison."""
        if self.path in self.PUBLIC_PATHS:
            return True
        if not AUTH_TOKEN:
            return False
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        provided = header[len("Bearer "):].strip()
        return hmac.compare_digest(provided, AUTH_TOKEN)

    def _reject_unauthorized(self):
        self._json(401, {"error": "Unauthorized"})

    def do_GET(self):
        if not self._authorized():
            return self._reject_unauthorized()
        if self.path == "/health":
            self._json(200, {"status": "ok"})
        elif self.path == "/version":
            self._json(200, self._get_version())
        elif self.path == "/check":
            self._json(200, self._check_updates())
        else:
            self._json(404, {"error": "Not found"})

    def do_POST(self):
        if not self._authorized():
            return self._reject_unauthorized()
        if self.path == "/apply":
            result = self._apply_update()
            self._json(200, result)
        else:
            self._json(404, {"error": "Not found"})

    def _json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"[update-sidecar] {fmt % args}")

    def _get_version(self):
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=5, cwd=REPO_PATH,
            )
            commit = r.stdout.strip() if r.returncode == 0 else "unknown"
        except Exception:
            commit = "unknown"
        version = ""
        try:
            with open(f"{REPO_PATH}/VERSION") as f:
                version = f.read().strip()
        except Exception:
            pass
        return {"commit": commit, "version": version}

    def _check_updates(self):
        if not os.path.isdir(f"{REPO_PATH}/.git"):
            return {"error": "Repository not mounted", "update_available": False}
        try:
            subprocess.run(
                ["git", "fetch", "origin", "main", "--quiet"],
                capture_output=True, text=True, timeout=30, cwd=REPO_PATH,
            )
        except Exception:
            return {"error": "Git fetch failed", "update_available": False}
        try:
            r = subprocess.run(
                ["git", "rev-list", "--count", "HEAD..origin/main"],
                capture_output=True, text=True, timeout=5, cwd=REPO_PATH,
            )
            behind = int(r.stdout.strip()) if r.returncode == 0 else 0
        except Exception:
            behind = 0
        changelog = []
        if behind > 0:
            try:
                r = subprocess.run(
                    ["git", "log", "--oneline", "--no-decorate", "HEAD..origin/main"],
                    capture_output=True, text=True, timeout=5, cwd=REPO_PATH,
                )
                if r.returncode == 0:
                    for line in r.stdout.strip().split("\n"):
                        if line.strip():
                            parts = line.split(" ", 1)
                            changelog.append({"hash": parts[0], "message": parts[1] if len(parts) > 1 else ""})
            except Exception:
                pass
        return {"update_available": behind > 0, "commits_behind": behind, "changelog": changelog}

    def _git(self, *args, timeout=30):
        """Run a git command in the repo, returning (rc, stdout, stderr)."""
        r = subprocess.run(
            ["git", *args],
            capture_output=True, text=True, timeout=timeout, cwd=REPO_PATH,
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()

    # SECURITY: This endpoint is host-root-equivalent. It runs `git pull` and
    # `docker compose up -d --build` against the host Docker socket, so anyone
    # who can reach it and presents the token can execute arbitrary code as root
    # on the host (e.g. by landing a malicious compose/Dockerfile in the repo).
    # It is reachable only over the internal docker network (port is `expose`d,
    # not host-published) and MUST NEVER be host-published or exposed publicly.
    EXPECTED_REF = "refs/heads/main"

    def _apply_update(self):
        if not os.path.isdir(f"{REPO_PATH}/.git"):
            return {"ok": False, "error": "Repository not mounted"}
        if not os.path.exists("/var/run/docker.sock"):
            return {"ok": False, "error": "Docker socket not available"}
        try:
            # 1. Refuse to operate on a dirty working tree — an unexpected local
            #    modification could be silently rebuilt into the running stack.
            rc, status_out, _ = self._git("status", "--porcelain", timeout=10)
            if rc != 0:
                return {"ok": False, "error": "Unable to read git status"}
            if status_out:
                return {"ok": False, "error": "Working tree is dirty, refusing to update"}

            # 2. Verify HEAD is on the expected branch before pulling, so the
            #    update cannot fast-forward an unexpected ref into production.
            rc, head_ref, _ = self._git("symbolic-ref", "-q", "HEAD", timeout=10)
            if rc != 0 or head_ref != self.EXPECTED_REF:
                return {
                    "ok": False,
                    "error": f"HEAD is not on {self.EXPECTED_REF} (got {head_ref or 'detached'})",
                }

            # 3. Pull the expected branch only.
            rc, pull_output, pull_err = self._git("pull", "origin", "main", timeout=60)
            if rc != 0:
                return {"ok": False, "error": f"Git pull failed: {pull_err[:200]}"}

            # 4. Re-verify HEAD did not move to an unexpected ref and resolve the
            #    commit SHA we are about to build, then log it for auditability.
            rc, head_ref_after, _ = self._git("symbolic-ref", "-q", "HEAD", timeout=10)
            if rc != 0 or head_ref_after != self.EXPECTED_REF:
                return {
                    "ok": False,
                    "error": f"HEAD moved to unexpected ref after pull ({head_ref_after or 'detached'})",
                }
            rc, commit_sha, _ = self._git("rev-parse", "HEAD", timeout=10)
            if rc != 0 or not commit_sha:
                return {"ok": False, "error": "Unable to resolve HEAD commit after pull"}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

        self.log_message("applying update: building commit %s on %s", commit_sha, self.EXPECTED_REF)

        # Fire-and-forget rebuild
        subprocess.Popen(
            ["docker", "compose", "-f", COMPOSE_FILE, "up", "-d", "--build",
             "--no-deps", "nodeglow", "frontend"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return {
            "ok": True,
            "message": "Update started",
            "commit": commit_sha,
            "pull_output": pull_output,
        }


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "9100"))
    if not AUTH_TOKEN:
        print("[update-sidecar] WARNING: UPDATE_SIDECAR_TOKEN is empty — "
              "all mutating endpoints will return 401 until it is set.")
    server = HTTPServer(("0.0.0.0", port), UpdateHandler)
    print(f"[update-sidecar] listening on :{port}")
    server.serve_forever()
