"""Host create/update carry validated HTTP check options."""


async def test_v1_create_with_http_options_round_trips(client):
    resp = await client.post("/api/v1/hosts", json={
        "name": "web", "hostname": "10.0.0.80", "check_type": "icmp,https",
        "http_options": {"url": "/health", "expected_status": "200-299",
                         "keyword": "ok", "timeout": 10, "verify_tls": True},
    })
    assert resp.status_code == 200, resp.text
    host_id = resp.json()["id"]

    detail = (await client.get(f"/api/v1/hosts/{host_id}")).json()
    assert detail["http_options"] == {
        "method": "GET", "url": "/health", "expected_status": "200-299",
        "keyword": "ok", "keyword_absent": None, "timeout": 10.0,
        "follow_redirects": True, "verify_tls": True,
    }
    assert detail["check_errors"] is None


async def test_v1_create_defaults_when_no_options(client):
    resp = await client.post("/api/v1/hosts", json={"name": "plain", "hostname": "10.0.0.81"})
    detail = (await client.get(f"/api/v1/hosts/{resp.json()['id']}")).json()
    assert detail["http_options"]["follow_redirects"] is True
    assert detail["http_options"]["verify_tls"] is False
    assert detail["http_options"]["method"] == "GET"


async def test_v1_create_rejects_bad_options(client):
    resp = await client.post("/api/v1/hosts", json={
        "name": "bad", "hostname": "10.0.0.82",
        "http_options": {"expected_status": "nope"},
    })
    assert resp.status_code == 400
    assert "http_options" in resp.json()["detail"]


async def test_v1_create_rejects_internal_absolute_url(client):
    resp = await client.post("/api/v1/hosts", json={
        "name": "ssrf", "hostname": "10.0.0.83",
        "http_options": {"url": "http://169.254.169.254/latest/meta-data"},
    })
    assert resp.status_code == 400


async def test_v1_create_rejects_internal_hostname(client):
    resp = await client.post("/api/v1/hosts", json={"name": "lo", "hostname": "127.0.0.1"})
    assert resp.status_code == 400


async def test_v1_update_http_options_and_clear(client):
    host_id = (await client.post("/api/v1/hosts", json={
        "name": "upd", "hostname": "10.0.0.84", "check_type": "http",
    })).json()["id"]

    resp = await client.patch(f"/api/v1/hosts/{host_id}", json={
        "http_options": {"method": "HEAD", "follow_redirects": False},
    })
    assert resp.status_code == 200, resp.text
    opts = (await client.get(f"/api/v1/hosts/{host_id}")).json()["http_options"]
    assert opts["method"] == "HEAD" and opts["follow_redirects"] is False

    bad = await client.patch(f"/api/v1/hosts/{host_id}", json={
        "http_options": {"method": "HEAD", "keyword": "x"},
    })
    assert bad.status_code == 400

    await client.patch(f"/api/v1/hosts/{host_id}", json={"http_options": None})
    opts = (await client.get(f"/api/v1/hosts/{host_id}")).json()["http_options"]
    assert opts["method"] == "GET" and opts["follow_redirects"] is True


async def test_v1_update_rejects_internal_hostname(client):
    host_id = (await client.post("/api/v1/hosts", json={
        "name": "mv", "hostname": "10.0.0.85",
    })).json()["id"]
    resp = await client.patch(f"/api/v1/hosts/{host_id}", json={"hostname": "localhost"})
    assert resp.status_code == 400


async def test_spa_create_accepts_http_options(client):
    resp = await client.post("/hosts/api/create", json={
        "name": "spa", "hostname": "10.0.0.86", "check_type": "http",
        "http_options": {"keyword_absent": "error"},
    })
    assert resp.status_code == 200, resp.text
    detail = (await client.get(f"/api/v1/hosts/{resp.json()['id']}")).json()
    assert detail["http_options"]["keyword_absent"] == "error"

    bad = await client.post("/hosts/api/create", json={
        "name": "spa2", "hostname": "10.0.0.87", "http_options": {"timeout": 999},
    })
    assert bad.status_code == 400
