"""A part an integration could not fetch is reported as such, never as empty.

Before: Cloudflare's firewall events came from a retired endpoint (404) and
showed as "no events"; a Proxmox storage list that could not be read showed
"No backup" for every guest. All swallowed at DEBUG.
"""
import pytest

from integrations.cloudflare import summarize_firewall_event
from integrations.proxmox import ProxmoxAPI


def test_firewall_event_from_graphql():
    ev = summarize_firewall_event({
        "action": "block", "clientIP": "192.0.2.7", "clientCountryName": "CH",
        "clientRequestPath": "/wp-login.php", "clientRequestHTTPHost": "example.com",
        "datetime": "2026-10-29T10:00:00Z", "ruleId": "", "source": "waf",
    })
    assert ev == {"action": "block", "source": "192.0.2.7", "country": "CH", "rule": "waf",
                  "host": "example.com", "uri": "/wp-login.php", "timestamp": "2026-10-29T10:00:00Z"}


async def test_unreadable_storage_list_means_unknown_backups(monkeypatch):
    api = ProxmoxAPI("https://pve.example.com:8006", "t", "s")

    async def get(path):
        raise RuntimeError("401")

    monkeypatch.setattr(api, "get", get)
    with pytest.raises(RuntimeError):
        await api.fetch_backup_data([{"type": "node", "node": "pve1"}])


async def test_unreadable_backup_storage_is_named(monkeypatch):
    api = ProxmoxAPI("https://pve.example.com:8006", "t", "s")

    async def get(path):
        if path.endswith("/storage"):
            return [{"storage": "nfs", "content": "backup,iso"}]
        raise RuntimeError("timeout")

    monkeypatch.setattr(api, "get", get)
    assert await api.fetch_backup_data([{"type": "node", "node": "pve1"}]) == {}
    assert api.unavailable == {"backups on nfs (pve1)"}


async def test_failed_cluster_tasks_are_named(monkeypatch):
    api = ProxmoxAPI("https://pve.example.com:8006", "t", "s")

    async def get(path):
        raise RuntimeError("timeout")

    monkeypatch.setattr(api, "get", get)
    assert await api.cluster_tasks() == []
    assert api.unavailable == {"tasks"}
