"""HTTP(S) check options: status codes, keywords, method, redirects + SSRF."""
import json
from unittest.mock import patch

import httpx
import pytest

from utils import http_options as ho
from utils import ping


class FakeHost:
    def __init__(self, hostname="example.test", check_type="http", http_options=None, port=None):
        self.hostname = hostname
        self.check_type = check_type
        self.port = port
        self.http_options = json.dumps(http_options) if http_options else None


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _run(handler, url="http://example.test/", **kw):
    client = _client(handler)
    try:
        with patch.object(ping, "_http_client", return_value=client):
            return await ping.check_http(url, verify_ssl=False, **kw)
    finally:
        await client.aclose()


# ── option parsing ───────────────────────────────────────────────────────────

def test_defaults_store_nothing():
    assert ho.normalize(None) is None
    assert ho.normalize({}) is None
    assert ho.normalize({"method": "GET", "timeout": 5, "follow_redirects": True,
                         "verify_tls": False, "keyword": "  "}) is None


def test_normalize_keeps_only_non_defaults():
    out = ho.normalize({"method": "head", "expected_status": " 200-299, 301 ",
                        "timeout": "10", "follow_redirects": False, "verify_tls": True,
                        "url": "/health?x=1"})
    assert out == {"method": "HEAD", "expected_status": "200-299,301", "timeout": 10.0,
                   "follow_redirects": False, "verify_tls": True, "url": "/health?x=1"}


@pytest.mark.parametrize("raw", [
    {"expected_status": "abc"},
    {"expected_status": "299-200"},
    {"expected_status": "99"},
    {"expected_status": "600"},
    {"expected_status": ",".join(["200"] * 21)},
    {"method": "POST"},
    {"method": "HEAD", "keyword": "ok"},
    {"timeout": 0.1},
    {"timeout": 120},
    {"timeout": "soon"},
    {"keyword": "x" * 201},
    {"url": "ftp://example.test/"},
    {"url": "//evil.test/"},
    {"url": "http://user:pw@example.test/"},
    {"url": "/a b"},
    {"follow_redirects": "maybe"},
    {"regex": ".*"},
    "not-an-object",
])
def test_normalize_rejects(raw):
    with pytest.raises(ValueError):
        ho.normalize(raw)


def test_status_spec_and_matching():
    ranges = ho.parse_status_spec("200-299,301")
    assert ho.status_matches(204, ranges)
    assert ho.status_matches(301, ranges)
    assert not ho.status_matches(302, ranges)
    # No expectation configured: the historical "< 500 is up" rule.
    assert ho.status_matches(404, None)
    assert not ho.status_matches(503, None)


def test_load_ignores_garbage():
    assert ho.load("not json") == ho.DEFAULTS
    assert ho.load(json.dumps({"method": "PUT"})) == ho.DEFAULTS
    assert ho.load(json.dumps({"method": "HEAD"})).method == "HEAD"


def test_build_url_applies_path_or_absolute_url():
    opts = ho.HttpOptions(url="/status?full=1")
    assert ho.build_url("https://example.test/old/path", opts) == "https://example.test/status?full=1"
    assert ho.build_url("http://example.test:8080", opts) == "http://example.test:8080/status?full=1"
    assert ho.build_url("http://a.test", ho.HttpOptions(url="http://10.0.0.5/x")) == "http://10.0.0.5/x"
    assert ho.build_url("http://a.test/p", ho.DEFAULTS) == "http://a.test/p"


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://169.254.169.254/latest/meta-data",
    "http://metadata.google.internal/", "http://localhost:8000/", "http://db:5432/",
])
async def test_absolute_url_option_is_ssrf_checked(url):
    with pytest.raises(ValueError):
        await ho.normalize_checked({"url": url})


async def test_absolute_url_option_allows_lan():
    assert await ho.normalize_checked({"url": "http://192.168.1.20/health"}) == {
        "url": "http://192.168.1.20/health"}


# ── check_http ───────────────────────────────────────────────────────────────

async def test_default_rule_counts_4xx_as_up_and_5xx_as_down():
    assert (await _run(lambda r: httpx.Response(404)))[0] is True
    res = await _run(lambda r: httpx.Response(503))
    assert res[0] is False and res.reason == "status 503"


async def test_expected_status_mismatch_has_reason():
    res = await _run(lambda r: httpx.Response(404), expected_status=[(200, 299)])
    ok, latency = res
    assert ok is False
    assert latency is not None
    assert res.reason == "status 404 (expected 200-299)"


async def test_keyword_must_contain_is_case_insensitive():
    handler = lambda r: httpx.Response(200, text="Status: ALL SYSTEMS OK")  # noqa: E731
    assert (await _run(handler, keyword="systems ok"))[0] is True
    res = await _run(handler, keyword="healthy")
    assert res[0] is False and res.reason == "keyword 'healthy' missing"


async def test_keyword_must_not_contain():
    handler = lambda r: httpx.Response(200, text="<h1>Database Error</h1>")  # noqa: E731
    res = await _run(handler, keyword_absent="database error")
    assert res[0] is False and res.reason == "keyword 'database error' present"
    assert (await _run(handler, keyword_absent="exception"))[0] is True


async def test_keyword_search_is_capped():
    body = "a" * 1000 + "NEEDLE"
    handler = lambda r: httpx.Response(200, text=body)  # noqa: E731
    assert (await _run(handler, keyword="needle", body_limit=2000))[0] is True
    assert (await _run(handler, keyword="needle", body_limit=500))[0] is False


async def test_method_is_sent():
    seen = []

    def handler(request):
        seen.append(request.method)
        return httpx.Response(200)

    await _run(handler, method="HEAD")
    assert seen == ["HEAD"]


async def test_redirect_followed_to_lan_host():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.host == "example.test":
            return httpx.Response(302, headers={"Location": "http://10.0.0.9/login"})
        return httpx.Response(200, text="welcome")

    res = await _run(handler, keyword="welcome")
    assert res[0] is True
    assert seen == ["http://example.test/", "http://10.0.0.9/login"]


@pytest.mark.parametrize("location", [
    "http://127.0.0.1/admin",
    "http://169.254.169.254/latest/meta-data/",
    "http://metadata.google.internal/",
    "http://clickhouse:8123/",
    "http://[::1]/",
    "file:///etc/passwd",
])
async def test_redirect_to_internal_target_is_blocked(location):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"Location": location})

    res = await _run(handler)
    assert res[0] is False
    assert "blocked" in res.reason or "unsupported" in (res.reason or "") or "redirect" in res.reason
    assert seen == ["http://example.test/"], "the blocked target must never be requested"


async def test_redirect_limit():
    count = {"n": 0}

    def handler(request):
        count["n"] += 1
        return httpx.Response(302, headers={"Location": f"http://10.0.0.1/{count['n']}"})

    res = await _run(handler)
    assert res[0] is False
    assert res.reason == "more than 5 redirects"
    assert count["n"] == 6  # the original request + 5 followed hops


async def test_redirects_not_followed_when_disabled():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(301, headers={"Location": "http://127.0.0.1/"})

    assert (await _run(handler, follow_redirects=False))[0] is True  # 3xx < 500
    res = await _run(handler, follow_redirects=False, expected_status=[(200, 200)])
    assert res[0] is False and res.reason.startswith("status 301")
    assert all(u == "http://example.test/" for u in seen)


async def test_timeout_reason():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    res = await _run(handler, timeout=2)
    assert tuple(res) == (False, None)
    assert res.reason == "timeout after 2s"


async def test_connection_error_reason():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    res = await _run(handler)
    assert res.reason == "connection failed"


# ── wired into check_host ────────────────────────────────────────────────────

async def test_check_host_applies_host_options_and_reports_errors():
    seen = []

    def handler(request):
        seen.append((request.method, str(request.url)))
        return httpx.Response(200, text="maintenance page")

    host = FakeHost(check_type="https", http_options={
        "url": "/healthz", "keyword": "ok", "keyword_absent": "maintenance",
    })
    client = _client(handler)
    with patch.object(ping, "_http_client", return_value=client) as factory:
        res = await ping.check_host(host)
    await client.aclose()

    online, port_error, latency, detail = res
    assert seen == [("GET", "https://example.test/healthz")]
    assert detail == {"https": False}
    assert res.errors == {"https": "keyword 'ok' missing"}
    factory.assert_called_with(False)  # TLS verification stays off by default


async def test_check_host_verify_tls_option_selects_verifying_client():
    client = _client(lambda r: httpx.Response(200))
    host = FakeHost(check_type="https", http_options={"verify_tls": True})
    with patch.object(ping, "_http_client", return_value=client) as factory:
        await ping.check_host(host)
    await client.aclose()
    factory.assert_called_with(True)


async def test_check_host_tcp_failure_reason():
    from unittest.mock import AsyncMock

    host = FakeHost(hostname="10.0.0.2", check_type="icmp,tcp:22")
    with (
        patch("utils.ping.ping_host", new_callable=AsyncMock, return_value=(True, 1.0)),
        patch("utils.ping.check_tcp", new_callable=AsyncMock, return_value=(False, None)),
    ):
        res = await ping.check_host(host)
    assert res.errors == {"tcp:22": "port 22 unreachable"}
    assert res[1] is True  # port_error
