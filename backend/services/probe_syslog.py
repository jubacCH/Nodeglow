"""Probe syslog relay: what a probe is told, and what it reports back.

The relay itself runs in the agent (``agent/src/syslog_relay.rs``): in probe
mode with the relay enabled it listens for syslog on UDP and TCP inside the
customer network and forwards the raw lines over its token to
``POST /api/agent/logs``. The backend parses them in the probe's tenant and
site (``services.syslog.parse_relayed_entry``).

Configuration rides the heartbeat response (``config.syslog_relay``), like the
probe's check assignments: no second channel, and a change takes effect
within one interval. An agent that is not a probe gets no relay config, which
the agent reads as "relay off".
"""
from __future__ import annotations

import json
from typing import Any, Optional

DEFAULT_RELAY_PORT = 514

# Counters the agent reports (``syslog_relay`` in the heartbeat body). Only
# these are kept, as non-negative integers — the agent is not trusted to
# write arbitrary JSON into the row.
_STATUS_COUNTERS = ("received", "forwarded", "dropped", "buffered", "send_failures")


def relay_port(agent: Any) -> int:
    return int(agent.syslog_relay_port or DEFAULT_RELAY_PORT)


def relay_config(agent: Any) -> Optional[dict]:
    """The ``syslog_relay`` part of the heartbeat config, None for non-probes."""
    if not agent.is_probe:
        return None
    enabled = bool(agent.syslog_relay_enabled)
    return {"enabled": enabled, "port": relay_port(agent)}


def sanitize_status(reported: Any) -> Optional[str]:
    """The relay status from a heartbeat, as the JSON stored on the agent."""
    if not isinstance(reported, dict):
        return None
    out: dict[str, Any] = {}
    for key in _STATUS_COUNTERS:
        value = reported.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        out[key] = max(0, min(value, 2**63 - 1))
    listening = reported.get("listening")
    if isinstance(listening, bool):
        out["listening"] = listening
    port = reported.get("port")
    if isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536:
        out["port"] = port
    error = reported.get("error")
    if isinstance(error, str) and error:
        out["error"] = error[:300]
    return json.dumps(out, sort_keys=True)


def relay_view(agent: Any) -> Optional[dict]:
    """What the API shows about a probe's relay; None for non-probes."""
    if not agent.is_probe:
        return None
    status = None
    if agent.syslog_relay_status:
        try:
            status = json.loads(agent.syslog_relay_status)
        except (TypeError, ValueError):
            status = None
    return {
        "enabled": bool(agent.syslog_relay_enabled),
        "port": relay_port(agent),
        "status": status,
    }
