"""An agent reporting a new version leaves an audit row — the change feed's
only source for "agent updated" (agents keep no version history)."""
from sqlalchemy import select

from models.agent import Agent
from models.audit import AuditLog
from tests.test_routers.conftest import make_client

RAW_TOKEN = "agent-token-for-version-tests"


async def _report(client, version):
    return await client.post("/api/agent/report",
                             json={"hostname": "srv-rds-01", "platform": "windows",
                                   "agent_version": version},
                             headers={"Authorization": f"Bearer {RAW_TOKEN}"})


async def test_version_change_is_audited_once():
    from routers.agents import _hash_agent_token

    async with make_client() as (client, sf):
        async with sf() as db:
            db.add(Agent(name="SRV-RDS-01", hostname="srv-rds-01",
                         token=_hash_agent_token(RAW_TOKEN), agent_version="0.4.1"))
            await db.commit()
        assert (await _report(client, "0.4.1")).status_code == 200
        assert (await _report(client, "0.4.2")).status_code == 200
        assert (await _report(client, "0.4.2")).status_code == 200
        async with sf() as db:
            rows = (await db.execute(
                select(AuditLog).where(AuditLog.action == "agent.version_change")
            )).scalars().all()
        assert len(rows) == 1
        assert '"to": "0.4.2"' in rows[0].details
