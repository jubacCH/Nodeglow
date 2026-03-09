"""Double-submit cookie CSRF protection."""
import hashlib
import hmac
import secrets

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse

from config import SECRET_KEY

COOKIE_NAME = "ng_csrf"
HEADER_NAME = "x-csrf-token"
FORM_FIELD = "csrf_token"


def _sign(token: str) -> str:
    """HMAC-sign a token so it can't be forged."""
    return hmac.new(SECRET_KEY.encode(), token.encode(), hashlib.sha256).hexdigest()[:16]


def generate_csrf_token(request: Request) -> str:
    """Get or create CSRF token for this request (stored on request.state)."""
    existing = getattr(request.state, "_csrf_token", None)
    if existing:
        return existing
    cookie_val = request.cookies.get(COOKIE_NAME)
    if cookie_val and "." in cookie_val:
        token, sig = cookie_val.rsplit(".", 1)
        if hmac.compare_digest(sig, _sign(token)):
            request.state._csrf_token = cookie_val
            return cookie_val
    # Generate new
    token = secrets.token_hex(16)
    signed = f"{token}.{_sign(token)}"
    request.state._csrf_token = signed
    request.state._csrf_new = True
    return signed


def _is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def set_csrf_cookie(request: Request, response):
    """Set CSRF cookie on response if newly generated."""
    if getattr(request.state, "_csrf_new", False):
        response.set_cookie(
            COOKIE_NAME,
            request.state._csrf_token,
            httponly=False,  # JS needs to read it
            samesite="lax",
            secure=_is_https(request),
            max_age=86400 * 30,
        )


def validate_csrf(request: Request, form_data: dict | None = None) -> bool:
    """Validate CSRF token from header or form field against cookie."""
    cookie_val = request.cookies.get(COOKIE_NAME)
    if not cookie_val:
        return False
    # Verify cookie signature
    if "." not in cookie_val:
        return False
    token, sig = cookie_val.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(token)):
        return False
    # Check header first, then form field
    submitted = request.headers.get(HEADER_NAME)
    if not submitted and form_data:
        submitted = form_data.get(FORM_FIELD)
    if not submitted:
        return False
    return hmac.compare_digest(submitted, cookie_val)


_403_HTML = (
    '<html><body style="background:#0b0d14;color:#e2e8f0;font-family:sans-serif;'
    'display:flex;align-items:center;justify-content:center;height:100vh;">'
    '<div style="text-align:center"><p style="font-size:3rem;margin:0">403</p>'
    '<p style="color:#94a3b8">CSRF validation failed.</p>'
    '<a href="/" style="color:#3b82f6;font-size:.875rem">&larr; Back</a></div></body></html>'
)


def csrf_error_response(request: Request):
    """Return appropriate 403 for CSRF failure."""
    if request.headers.get("accept", "").startswith("application/json") or \
       request.url.path.startswith("/api/"):
        return JSONResponse({"error": "CSRF validation failed"}, status_code=403)
    return HTMLResponse(_403_HTML, status_code=403)
