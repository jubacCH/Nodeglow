"""/api/v1/sites and the effective site of a host (B-14, concept F phase 3, PR 4)."""
from sqlalchemy import select

from tests.test_routers.conftest import make_client


class EditorUser:
    id = 2
    username = "editor"
    role = "editor"


async def _setup(sf):
    """Default site 1, a branch site, a probe in the branch, hosts in various states."""
    from models.agent import Agent
    from models.ping import PingHost
    from models.tenant import Site

    async with sf() as db:
        # The test installation has its default site already (id 1); create it only if missing.
        default = (await db.execute(select(Site).order_by(Site.id))).scalars().first()
        if default is None:
            default = Site(name="Default")
            db.add(default)
        branch = Site(name="Bern")
        db.add(branch)
        await db.flush()
        probe = Agent(name="probe-bern-01", hostname="probe-bern-01", is_probe=True, site_id=branch.id,
                      token="probe-token-x")
        db.add(probe)
        await db.flush()
        hosts = {
            "direct": PingHost(name="srv-zh", hostname="10.0.0.1", site_id=default.id),
            "behind_probe": PingHost(name="srv-be", hostname="10.9.0.1", site_id=default.id, probe_id=probe.id),
            "unset": PingHost(name="nas-be", hostname="10.9.0.2", site_id=None, probe_id=probe.id),
            "pinned": PingHost(name="printer", hostname="10.9.0.3", site_id=default.id, probe_id=probe.id),
        }
        db.add_all(hosts.values())
        await db.commit()
        return {"default": default.id, "branch": branch.id, "probe": probe.id,
                **{k: h.id for k, h in hosts.items()}}


async def test_effective_site_follows_the_probe_unless_set():
    from services.sites import SiteContext

    ctx = SiteContext(default_id=1, sites={1: "Default", 2: "Bern", 3: "Basel"}, probe_site={7: 2, 8: 1})
    assert ctx.effective(3, 7) == 3, "a site on the host wins"
    assert ctx.effective(1, 7) == 2, "the default counts as not set: follow the probe"
    assert ctx.effective(None, 7) == 2
    assert ctx.effective(None, 8) == 1, "a probe in the default site gives the default"
    assert ctx.effective(None, None) == 1
    assert ctx.effective(99, None) == 1, "an unknown site falls back to the default"


async def test_list_counts_hosts_by_effective_site():
    async with make_client() as (client, sf):
        ids = await _setup(sf)
        sites = {s["id"]: s for s in (await client.get("/api/v1/sites")).json()}
        assert sites[ids["default"]]["is_default"] is True and sites[ids["branch"]]["is_default"] is False
        assert sites[ids["default"]]["host_count"] == 1, "only the core-checked host"
        sites = {s["name"]: s for s in sites.values()}
        assert sites["Bern"]["host_count"] == 3, "everything behind the Bern probe"
        assert sites["Bern"]["probe_count"] == 1

        hosts = {h["id"]: h for h in (await client.get("/api/v1/hosts")).json()}
        assert hosts[ids["behind_probe"]]["site_id"] == ids["branch"]
        assert hosts[ids["behind_probe"]]["site_set"] is None
        only_bern = (await client.get(f"/api/v1/hosts?site={ids['branch']}")).json()
        assert sorted(h["id"] for h in only_bern) == sorted([ids["behind_probe"], ids["unset"], ids["pinned"]])
        detail = (await client.get(f"/api/v1/hosts/{ids['unset']}")).json()
        assert detail["site_id"] == ids["branch"]


async def test_create_rename_and_name_rules():
    async with make_client() as (client, sf):
        await _setup(sf)
        resp = await client.post("/api/v1/sites", json={"name": "  Basel  "})
        assert resp.status_code == 201 and resp.json()["name"] == "Basel"
        sid = resp.json()["id"]
        assert (await client.post("/api/v1/sites", json={"name": "basel"})).status_code == 409
        assert (await client.post("/api/v1/sites", json={"name": ""})).status_code == 400
        assert (await client.post("/api/v1/sites", json={"name": "x" * 129})).status_code == 400
        assert (await client.patch(f"/api/v1/sites/{sid}", json={"name": "Bern"})).status_code == 409
        assert (await client.patch(f"/api/v1/sites/{sid}", json={"name": "Basel HQ"})).json()["name"] == "Basel HQ"
        assert (await client.patch("/api/v1/sites/999", json={"name": "y"})).status_code == 404


async def test_only_admins_change_sites():
    async with make_client(fake_user=EditorUser()) as (client, sf):
        ids = await _setup(sf)
        assert (await client.get("/api/v1/sites")).status_code == 200
        assert (await client.post("/api/v1/sites", json={"name": "Basel"})).status_code == 403
        assert (await client.delete(f"/api/v1/sites/{ids['branch']}")).status_code == 403
        resp = await client.patch(f"/api/v1/agents/{ids['probe']}", json={"site_id": ids["default"]})
        assert resp.status_code == 403, "moving a probe moves its hosts: admin only"


async def test_deleting_a_site_moves_its_members_to_the_default():
    from models.agent import Agent
    from models.ping import PingHost

    async with make_client() as (client, sf):
        ids = await _setup(sf)
        assert (await client.delete(f"/api/v1/sites/{ids['default']}")).status_code == 409
        await client.patch(f"/api/v1/hosts/{ids['direct']}", json={"site_id": ids["branch"]})
        resp = await client.delete(f"/api/v1/sites/{ids['branch']}")
        assert resp.status_code == 200
        assert resp.json()["moved"]["ping_hosts"] == 1 and resp.json()["moved"]["agents"] == 1
        async with sf() as db:
            assert (await db.get(PingHost, ids["direct"])).site_id == ids["default"]
            assert (await db.get(Agent, ids["probe"])).site_id == ids["default"]
        remaining = [s["id"] for s in (await client.get("/api/v1/sites")).json()]
        assert remaining == [ids["default"]]


async def test_hosts_and_probes_get_a_site():
    from models.agent import Agent
    from models.ping import PingHost

    async with make_client() as (client, sf):
        ids = await _setup(sf)
        resp = await client.patch(f"/api/v1/hosts/{ids['direct']}", json={"site_id": ids["branch"]})
        assert resp.status_code == 200
        assert (await client.patch(f"/api/v1/hosts/{ids['direct']}", json={"site_id": 999})).status_code == 400
        assert (await client.patch(f"/api/v1/hosts/{ids['direct']}", json={"site_id": True})).status_code == 400
        bulk = await client.patch("/api/v1/hosts/bulk",
                                  json={"ids": [ids["behind_probe"], ids["unset"]], "updates": {"site_id": None}})
        assert bulk.json()["updated"] == 2
        assert (await client.patch(f"/api/v1/agents/{ids['probe']}", json={"site_id": None})).status_code == 200
        async with sf() as db:
            assert (await db.get(PingHost, ids["direct"])).site_id == ids["branch"]
            assert (await db.get(PingHost, ids["unset"])).site_id is None
            assert (await db.get(Agent, ids["probe"])).site_id is None
            # The probe is in no site now, so its hosts follow the default.
            from services.sites import load_context
            ctx = await load_context(db)
            hosts = (await db.execute(select(PingHost))).scalars().all()
            effective = {h.id: ctx.effective(h.site_id, h.probe_id) for h in hosts}
        assert effective[ids["behind_probe"]] == ids["default"]
        assert effective[ids["direct"]] == ids["branch"]


async def test_topology_nodes_carry_the_effective_site():
    """The topology page filters by site (concept F phase 3, PR 5)."""
    async with make_client() as (client, sf):
        ids = await _setup(sf)
        nodes = {n["id"]: n for n in (await client.get("/api/v1/topology")).json()["nodes"]}
        assert nodes[ids["direct"]]["site_id"] == ids["default"]
        assert nodes[ids["behind_probe"]]["site_id"] == ids["branch"]
        assert nodes[ids["unset"]]["site_id"] == ids["branch"]
