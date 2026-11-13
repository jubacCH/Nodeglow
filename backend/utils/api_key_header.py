"""Where a request carries its API key.

One place, used by the auth middleware and by ``require_api_key``, so the two
can never disagree about whether a request is authenticated by a key (the
middleware skips the session cookie and CSRF for such a request).
"""
from __future__ import annotations

# Keys are generated as ``ng_<hex>`` (routers/api_v1.py). Only such a token is
# taken from the Authorization header: other bearer tokens (agents, probes)
# belong to other auth schemes and must not be tried as API keys.
KEY_PREFIX = "ng_"


def presented_api_key(headers) -> str | None:
    """The API key of a request: ``X-API-Key``, else ``Authorization: Bearer
    ng_…`` (for senders that can only set an Authorization header, such as
    Prometheus Alertmanager). Never the query string."""
    key = (headers.get("X-API-Key") or "").strip()
    if key:
        return key
    auth = headers.get("Authorization") or ""
    scheme, _, token = auth.partition(" ")
    token = token.strip()
    if scheme.lower() == "bearer" and token.startswith(KEY_PREFIX):
        return token
    return None
