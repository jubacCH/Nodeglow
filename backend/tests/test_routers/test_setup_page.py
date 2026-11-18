"""The first-run setup page is served by the backend and has to work on an
installation without internet access: everything it needs is in the response."""
from __future__ import annotations

import re

import pytest


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    from services import shared_state

    shared_state.reset()
    yield
    shared_state.reset()


NONCE = "test-nonce"


def _render(error: str = "") -> str:
    """The real template. The router fixtures replace TemplateResponse with a
    stub, so render through the same Jinja environment instead."""
    from types import SimpleNamespace

    from templating import templates

    request = SimpleNamespace(state=SimpleNamespace(csp_nonce=NONCE, _csrf_token="tok"))
    return templates.env.get_template("setup.html").render(request=request, error=error)


def test_setup_page_loads_nothing_from_the_network():
    html = _render()
    # No absolute URL anywhere (scripts, stylesheets, fonts, images): the only
    # references are same-origin paths.
    assert not re.search(r"""(?:https?:)?//[\w.-]+\.\w""", html), "external reference in setup.html"
    assert not re.search(r"<link[^>]+stylesheet", html)
    assert "@import" not in html and "url(http" not in html
    for ref in re.findall(r"""(?:src|href|action)="([^"]*)\"""", html):
        assert ref.startswith("/") and not ref.startswith("//"), ref
    # Styled from its own <style> block, in both themes.
    assert "<style>" in html and 'html[data-theme="light"]' in html


def test_setup_page_scripts_are_inline_and_carry_the_nonce():
    scripts = re.findall(r"<script\b([^>]*)>", _render())
    assert scripts and all(f'nonce="{NONCE}"' in attrs and "src=" not in attrs for attrs in scripts)


async def test_csp_allows_no_third_party_host(auth_client):
    from tests.conftest import put_settings

    client, sf = auth_client
    async with sf() as s:
        await put_settings(s, setup_complete="false")
    r = await client.get("/setup")
    assert r.status_code == 200, r.text
    csp = r.headers["content-security-policy"]
    assert "http" not in csp, csp
    assert re.search(r"script-src 'self' 'nonce-[^']+';", csp), csp


def test_setup_page_keeps_the_form_contract():
    html = _render("The setup token is missing or wrong.")
    assert '<form method="post" action="/setup/complete">' in html
    assert '<input type="hidden" name="csrf_token" value="tok">' in html
    for name in ("setup_token", "site_name", "username", "password"):
        assert f'name="{name}"' in html, name
    assert re.search(r'<p role="alert"[^>]*>The setup token is missing or wrong\.</p>', html)
