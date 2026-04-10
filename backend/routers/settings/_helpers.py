"""Shared helpers for settings sub-routers."""
import logging

from fastapi import Request
from fastapi.responses import JSONResponse

log = logging.getLogger("nodeglow.settings")


def require_admin(request: Request):
    """Return a 403 JSONResponse if the current user is not an admin, else None."""
    user = getattr(request.state, "current_user", None)
    role = getattr(user, "role", "admin") or "admin"
    if role != "admin":
        return JSONResponse({"error": "Admin access required"}, status_code=403)
    return None
