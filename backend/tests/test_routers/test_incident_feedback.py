"""Router tests for the incident feedback loop endpoint."""
import json

from unittest.mock import AsyncMock, patch


async def _seed_incident(rule="learned_precursor", precursor_template=None):
    """Insert an incident directly via the app's (patched) session factory."""
    from database import AsyncSessionLocal
    from models.incident import Incident

    async with AsyncSessionLocal() as s:
        inc = Incident(
            rule=rule,
            title="Predicted: Host Down (90% confidence)",
            severity="warning",
            status="open",
            precursor_template=precursor_template,
        )
        s.add(inc)
        await s.commit()
        return inc.id


async def _get_setting(key):
    from database import AsyncSessionLocal, get_setting

    async with AsyncSessionLocal() as s:
        return await get_setting(s, key, None)


async def test_feedback_noise_blacklists_precursor_template(client):
    tpl = "udhcpc[<*>]: sending renew to server <*>"
    iid = await _seed_incident(rule="learned_precursor", precursor_template=tpl)

    with patch("notifications.notify", new_callable=AsyncMock):
        resp = await client.post(
            f"/api/v1/incidents/{iid}/feedback", json={"verdict": "noise"}
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["feedback"] == "noise"

    # The template should now be present in the blacklist setting (regex-escaped).
    raw = await _get_setting("predictor_template_blacklist")
    assert raw is not None
    patterns = json.loads(raw)
    import re as _re

    assert any(_re.escape(tpl) == p for p in patterns)


async def test_feedback_real_does_not_blacklist(client):
    iid = await _seed_incident(
        rule="learned_precursor", precursor_template="some template"
    )
    with patch("notifications.notify", new_callable=AsyncMock):
        resp = await client.post(
            f"/api/v1/incidents/{iid}/feedback", json={"verdict": "real"}
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["feedback"] == "real"

    raw = await _get_setting("predictor_template_blacklist")
    assert raw is None  # no blacklist mutation occurred


async def test_feedback_invalid_verdict(client):
    iid = await _seed_incident()
    resp = await client.post(
        f"/api/v1/incidents/{iid}/feedback", json={"verdict": "maybe"}
    )
    assert resp.status_code in (400, 422)


async def test_feedback_missing_incident(client):
    resp = await client.post(
        "/api/v1/incidents/999999/feedback", json={"verdict": "real"}
    )
    assert resp.status_code == 404
