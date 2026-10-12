"""Shared helpers for settings sub-routers."""
import logging

from fastapi import Request
from fastapi.responses import JSONResponse

log = logging.getLogger("nodeglow.settings")


def require_admin(request: Request):
    """Return a 403 JSONResponse if the current user is not an admin of the
    request's tenant (membership role), else None."""
    from services.tenancy import request_is_admin

    if not request_is_admin(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)
    return None


def require_access_admin(request: Request):
    """403 unless the user may manage access to the request's tenant (users,
    passwords, API keys, install tokens): an admin, not through support
    access (``services.tenancy.request_may_manage_access``)."""
    from services.tenancy import SUPPORT_IAM_REFUSED, request_is_admin, request_via_support

    if not request_is_admin(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)
    if request_via_support(request):
        return JSONResponse({"error": SUPPORT_IAM_REFUSED, "code": "support_access"}, status_code=403)
    return None


def require_operator(request: Request):
    """403 unless the user may change installation settings (the operator;
    in the core: the admin of the one tenant)."""
    from services.tenancy import request_is_operator

    if not request_is_operator(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)
    return None


def is_operator(request: Request) -> bool:
    from services.tenancy import request_is_operator

    return request_is_operator(request)
