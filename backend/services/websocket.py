"""
Global WebSocket hub — broadcasts events to all connected clients.
Used by dashboard, agents page, and any future live views.

Each connection is tracked with its user role for per-role filtering.
"""
import json
import logging
from dataclasses import dataclass
from datetime import datetime

from fastapi import WebSocket

logger = logging.getLogger(__name__)


@dataclass
class WsClient:
    ws: WebSocket
    role: str = "admin"  # admin | editor | readonly


_clients: list[WsClient] = []


def get_client_count() -> int:
    return len(_clients)


async def register(ws: WebSocket, role: str = "admin"):
    await ws.accept()
    _clients.append(WsClient(ws=ws, role=role))
    logger.debug("WebSocket client connected (role=%s, %d total)", role, len(_clients))


def unregister(ws: WebSocket):
    _clients[:] = [c for c in _clients if c.ws is not ws]
    logger.debug("WebSocket client disconnected (%d remaining)", len(_clients))


async def broadcast(event_type: str, data: dict, min_role: str | None = None):
    """Broadcast an event to connected WebSocket clients.

    Args:
        event_type: Event name (e.g. "ping_update", "agent_metric")
        data: Event payload
        min_role: If set, only send to clients with this role or higher.
                  Role hierarchy: admin > editor > readonly.
    """
    if not _clients:
        return

    _role_level = {"readonly": 0, "editor": 1, "admin": 2}
    min_level = _role_level.get(min_role, 0) if min_role else 0

    msg = json.dumps({"type": event_type, "ts": datetime.utcnow().isoformat(), **data}, default=str)
    dead: list[WsClient] = []
    for client in _clients[:]:
        if _role_level.get(client.role, 0) < min_level:
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

async def broadcast_ping_update(host_id: int, name: str, online: bool, latency_ms: float | None):
    await broadcast("ping_update", {
        "host_id": host_id,
        "name": name,
        "online": online,
        "latency_ms": latency_ms,
    })


async def broadcast_agent_metric(agent_id: int, agent_name: str, metrics: dict):
    await broadcast("agent_metric", {
        "agent_id": agent_id,
        "agent_name": agent_name,
        **metrics,
    })
