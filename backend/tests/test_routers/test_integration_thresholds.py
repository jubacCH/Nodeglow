"""Settings -> Monitoring: integration alert thresholds."""


async def test_lists_only_configured_types_unless_all(client):
    from services import integration as int_svc
    from database import AsyncSessionLocal
    from services.tenancy import bind_session

    async with AsyncSessionLocal() as s:
        bind_session(s, 1)
        await int_svc.create_config(s, "proxmox", "pve", {"host": "https://192.0.2.1"})

    rows = (await client.get("/settings/integration-thresholds")).json()
    assert {r["integration"] for r in rows} == {"proxmox"}
    everything = (await client.get("/settings/integration-thresholds?all=1")).json()
    assert {"npm", "unas", "speedtest"} <= {r["integration"] for r in everything}


async def test_save_and_read_back(client):
    resp = await client.post("/settings/integration-thresholds/save",
                             json={"values": {"proxmox.node_cpu_pct": 80}})
    assert resp.status_code == 200, resp.text
    rows = {r["id"]: r for r in (await client.get("/settings/integration-thresholds?all=1")).json()}
    assert rows["proxmox.node_cpu_pct"]["value"] == 80


async def test_out_of_range_is_refused(client):
    resp = await client.post("/settings/integration-thresholds/save",
                             json={"values": {"proxmox.node_cpu_pct": 400}})
    assert resp.status_code == 400
    assert resp.json()["refused"] == ["proxmox.node_cpu_pct"]
    assert (await client.post("/settings/integration-thresholds/save", json=[1])).status_code == 400
