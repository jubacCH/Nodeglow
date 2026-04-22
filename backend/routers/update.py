"""Self-update: check GitHub for new commits and apply updates via sidecar."""
import logging
import os

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ratelimit import rate_limit

router = APIRouter(prefix="/api/update")
log = logging.getLogger(__name__)

SIDECAR_URL = os.environ.get("UPDATE_SIDECAR_URL", "http://updater:9100")
# Shared secret for authenticating to the updater sidecar. Must match the
# UPDATE_SIDECAR_TOKEN env var on the `updater` container.
_SIDECAR_TOKEN = os.environ.get("UPDATE_SIDECAR_TOKEN", "").strip()


def _sidecar_headers() -> dict[str, str]:
    if not _SIDECAR_TOKEN:
        # Fail-closed: refuse to even contact the sidecar without a token.
        raise RuntimeError(
            "UPDATE_SIDECAR_TOKEN is not configured on the backend container."
        )
    return {"Authorization": f"Bearer {_SIDECAR_TOKEN}"}


@router.get("/check")
@rate_limit(max_requests=5, window_seconds=60)
async def check_for_updates(request: Request):
    """Check for available updates via the update sidecar."""
    try:
        headers = _sidecar_headers()
        async with httpx.AsyncClient(timeout=35.0, headers=headers) as client:
            ver_resp = await client.get(f"{SIDECAR_URL}/version")
            local = ver_resp.json() if ver_resp.status_code == 200 else {"commit": "unknown"}

            check_resp = await client.get(f"{SIDECAR_URL}/check")
            check = check_resp.json() if check_resp.status_code == 200 else {}
    except RuntimeError as e:
        log.error("Update sidecar misconfigured: %s", e)
        return JSONResponse({
            "local": {"commit": "unknown"},
            "update_available": False,
            "error": "Update service is not configured (missing UPDATE_SIDECAR_TOKEN).",
        }, status_code=503)
    except Exception as e:
        log.error("Update sidecar unreachable: %s", e)
        return JSONResponse({
            "local": {"commit": "unknown"},
            "update_available": False,
            "error": "Update service unavailable. Is the updater sidecar running?",
        })

    return JSONResponse({
        "local": local,
        "remote_commit": check.get("changelog", [{}])[0].get("hash", "") if check.get("changelog") else "",
        "remote_version": "",
        "commits_behind": check.get("commits_behind", 0),
        "update_available": check.get("update_available", False),
        "changelog": check.get("changelog", []),
    })


@router.post("/apply")
@rate_limit(max_requests=2, window_seconds=300)
async def apply_update(request: Request):
    """Apply update via the sidecar (which has Docker socket access)."""
    user = getattr(request.state, "current_user", None)
    if not user or getattr(user, "role", "admin") != "admin":
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    try:
        headers = _sidecar_headers()
        async with httpx.AsyncClient(timeout=90.0, headers=headers) as client:
            resp = await client.post(f"{SIDECAR_URL}/apply")
            result = resp.json()
    except RuntimeError as e:
        log.error("Update sidecar misconfigured: %s", e)
        return JSONResponse(
            {"ok": False, "error": "Update service is not configured (missing UPDATE_SIDECAR_TOKEN)."},
            status_code=503,
        )
    except Exception as e:
        log.error("Update sidecar error: %s", e)
        return JSONResponse({"ok": False, "error": f"Update service unavailable: {e}"})

    if not result.get("ok"):
        return JSONResponse({"ok": False, "error": result.get("error", "Update failed")})

    return JSONResponse({
        "ok": True,
        "message": "Update started. The application will restart in a few seconds.",
        "pull_output": result.get("pull_output", ""),
    })
