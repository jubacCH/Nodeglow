"""
Global WebSocket hub — pushes live events to connected clients.
Used by dashboard, agents page, and any future live views.

Each connection is registered with its user's role and the tenant(s) it may
see (multi-tenancy: the active tenant; an MSP portal view in ee may register
several). Every event belongs to exactly one tenant and is only sent to
clients of that tenant — ``broadcast`` has no default for it, so each call
site has to say whose event it is.
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime

from fastapi import WebSocket

logger = logging.getLogger(__name__)


@dataclass
class WsClient:
    ws: WebSocket
    role: str = "readonly"  # admin | editor | readonly
    tenant_ids: frozenset[int] = field(default_factory=frozenset)


_clients: list[WsClient] = []


def get_client_count() -> int:
    return len(_clients)


async def register(ws: WebSocket, role: str = "readonly", *, tenant_id: int | None = None,
                   tenant_ids: frozenset[int] | set[int] | None = None):
    """Accept and register a client for ``tenant_id`` (or several ``tenant_ids``).

    A client registered for no tenant receives nothing.
    """
    ids = set(tenant_ids or ())
    if tenant_id is not None:
        ids.add(int(tenant_id))
    await ws.accept()
    _clients.append(WsClient(ws=ws, role=role, tenant_ids=frozenset(ids)))
    logger.debug("WebSocket client connected (role=%s, tenants=%s, %d total)",
                 role, sorted(ids), len(_clients))


def unregister(ws: WebSocket):
    _clients[:] = [c for c in _clients if c.ws is not ws]
    logger.debug("WebSocket client disconnected (%d remaining)", len(_clients))


_ROLE_LEVEL = {"readonly": 0, "editor": 1, "admin": 2}


async def broadcast(event_type: str, data: dict, *, tenant_id: int, min_role: str | None = None):
    """Send an event of ``tenant_id`` to that tenant's connected clients.

    Args:
        event_type: Event name (e.g. "ping_update", "agent_metric")
        data: Event payload
        tenant_id: The tenant the event belongs to (required).
        min_role: If set, only send to clients with this role or higher.
                  Role hierarchy: admin > editor > readonly.
    """
    if tenant_id is None:
        raise ValueError("broadcast() needs the tenant the event belongs to")
    if not _clients:
        return

    min_level = _ROLE_LEVEL.get(min_role, 0) if min_role else 0

    msg = json.dumps({"type": event_type, "ts": datetime.utcnow().isoformat(), **data}, default=str)
    dead: list[WsClient] = []
    for client in _clients[:]:
        if tenant_id not in client.tenant_ids:
            continue
        if _ROLE_LEVEL.get(client.role, 0) < min_level:
            continue
        try:
            await client.ws.send_text(msg)
        except Exception:
            dead.append(client)

    for d in dead:
        try:
            _clients.remove(d)
        except ValueError:
            pass


# Convenience methods for common events

async def broadcast_ping_update(host_id: int, name: str, online: bool, latency_ms: float | None,
                                *, tenant_id: int):
    await broadcast("ping_update", {
        "host_id": host_id,
        "name": name,
        "online": online,
        "latency_ms": latency_ms,
    }, tenant_id=tenant_id)


async def broadcast_agent_metric(agent_id: int, agent_name: str, metrics: dict, *, tenant_id: int):
    await broadcast("agent_metric", {
        "agent_id": agent_id,
        "agent_name": agent_name,
        **metrics,
    }, tenant_id=tenant_id)
