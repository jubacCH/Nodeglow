"""AI client: opt-in gate, providers, redaction on the wire, upgrade compat."""
import json
from contextlib import asynccontextmanager
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select

import models.audit  # noqa: F401 — register the table
from models.audit import AuditLog
from services import ai_client
from services.ai_config import (
    PROVIDER_ANTHROPIC,
    PROVIDER_OPENAI,
    AIConfig,
    AIDisabledError,
    AINotConfiguredError,
    load_ai_config,
    migrate_ai_opt_in,
    validate_ai_base_url,
)


def _openai_cfg(**kw) -> AIConfig:
    base = dict(enabled=True, provider=PROVIDER_OPENAI, openai_base_url="http://10.0.0.5:11434/v1",
                openai_model="llama3.1:8b")
    base.update(kw)
    return AIConfig(**base)


# ── opt-in gate ──────────────────────────────────────────────────────────────

async def test_disabled_ai_sends_nothing():
    cfg = AIConfig(enabled=False, anthropic_api_key="sk-ant-x")
    with patch.object(ai_client, "_anthropic_complete") as sent:
        with pytest.raises(AIDisabledError):
            await ai_client.generate_completion("sys", "hello", config=cfg)
        with pytest.raises(AIDisabledError):
            async for _ in ai_client.stream_completion("sys", [{"role": "user", "content": "x"}], config=cfg):
                pass
    sent.assert_not_called()


async def test_enabled_without_provider_config_is_reported():
    with pytest.raises(AINotConfiguredError):
        await ai_client.generate_completion("sys", "hi", config=AIConfig(enabled=True))
    with pytest.raises(AINotConfiguredError):
        await ai_client.generate_completion("sys", "hi", config=_openai_cfg(openai_model=""))


def test_default_anthropic_model_unchanged():
    assert AIConfig().anthropic_model == "claude-haiku-4-5-20251001"
    assert ai_client.DEFAULT_MODEL == "claude-haiku-4-5-20251001"


# ── OpenAI-compatible targets ────────────────────────────────────────────────

def test_ollama_target_and_optional_key():
    url, headers = ai_client.openai_request_target(_openai_cfg())
    assert url == "http://10.0.0.5:11434/v1/chat/completions"
    assert "Authorization" not in headers
    url, headers = ai_client.openai_request_target(_openai_cfg(openai_api_key="k", openai_base_url="https://api.example.ai/v1/"))
    assert url == "https://api.example.ai/v1/chat/completions"
    assert headers["Authorization"] == "Bearer k"


def test_azure_target_uses_deployment_api_version_and_api_key_header():
    cfg = _openai_cfg(openai_base_url="https://nodeglow-chn.openai.azure.com",
                      openai_model="gpt-4o-mini-chn", openai_api_version="2024-10-21",
                      openai_api_key="azkey")
    url, headers = ai_client.openai_request_target(cfg)
    assert url == ("https://nodeglow-chn.openai.azure.com/openai/deployments/gpt-4o-mini-chn"
                   "/chat/completions?api-version=2024-10-21")
    assert headers["api-key"] == "azkey" and "Authorization" not in headers
    assert cfg.provider_label == "Azure OpenAI"


def test_azure_requires_key():
    assert "ai_openai_api_key" in _openai_cfg(openai_api_version="2024-10-21").missing()
    assert _openai_cfg().missing() == []  # local servers need no key


# ── base URL validation ──────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "http://localhost:11434/v1", "http://127.0.0.1:8000/v1", "http://[::1]:1234/v1",
    "http://10.0.0.5:11434/v1", "http://192.168.1.10:1234/v1", "https://x.openai.azure.com",
])
def test_local_and_private_endpoints_are_allowed(url):
    assert validate_ai_base_url(url, resolve=False) is None


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest", "http://metadata.google.internal/v1",
    "http://[fe80::1]/v1", "http://0.0.0.0:8000", "http://db:5432", "http://clickhouse:8123",
    "ftp://10.0.0.5/v1", "http://user:pw@10.0.0.5/v1", "",
])
def test_dangerous_endpoints_are_rejected(url):
    assert validate_ai_base_url(url, resolve=False)


# ── redaction on the wire ────────────────────────────────────────────────────

def _mock_transport(captured: list, reply: str, stream: bool = False):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        if stream:
            parts = [reply[i:i + 3] for i in range(0, len(reply), 3)]
            body = "".join(
                "data: " + json.dumps({"choices": [{"delta": {"content": p}}]}) + "\n\n" for p in parts
            ) + "data: [DONE]\n\n"
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={
            "model": "llama3.1:8b",
            "choices": [{"message": {"content": reply}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        })
    return httpx.MockTransport(handler)


def _patched_client(transport):
    real = httpx.AsyncClient

    def factory(*a, **kw):
        kw["transport"] = transport
        return real(*a, **kw)
    return patch.object(ai_client.httpx, "AsyncClient", side_effect=factory)


async def test_openai_completion_is_redacted_and_restored():
    captured: list = []
    log = "sshd: Failed password for julian from 203.0.113.45 port 22; password=hunter2"
    with _patched_client(_mock_transport(captured, "Block <IP_1>, rotate <SECRET_1> for <USER_1>.")):
        text, usage = await ai_client.generate_completion(
            "Context:\n" + log, "Why did julian fail? He typed hunter2", config=_openai_cfg(),
            return_usage=True,
        )
    sent = json.dumps(captured[0])
    assert "203.0.113.45" not in sent and "hunter2" not in sent and "julian" not in sent
    assert "<IP_1>" in sent and "placeholders" in captured[0]["messages"][0]["content"]
    # The user's question is redacted with the same map as the context.
    assert captured[0]["messages"][1]["content"] == "Why did <USER_1> fail? He typed <SECRET_1>"
    assert text == "Block 203.0.113.45, rotate <SECRET_1> for julian."
    assert usage["provider"] == PROVIDER_OPENAI and usage["input_tokens"] == 11
    assert ai_client.estimate_cost_usd(usage) == 0.0


async def test_restore_can_be_switched_off_and_redaction_too():
    captured: list = []
    with _patched_client(_mock_transport(captured, "Block <IP_1>")):
        text = await ai_client.generate_completion(
            "from 203.0.113.45", "q", config=_openai_cfg(restore_placeholders=False),
        )
    assert text == "Block <IP_1>"
    captured.clear()
    with _patched_client(_mock_transport(captured, "ok")):
        await ai_client.generate_completion("from 203.0.113.45", "q", config=_openai_cfg(redact=False))
    assert "203.0.113.45" in captured[0]["messages"][0]["content"]


async def test_openai_stream_restores_split_placeholders():
    captured: list = []
    cfg = _openai_cfg()
    with _patched_client(_mock_transport(captured, "Host <IP_1> is under attack by <USER_1>.", stream=True)):
        out = "".join([
            d async for d in ai_client.stream_completion(
                "Failed password for invalid user mkeller from 198.51.100.7",
                [{"role": "assistant", "content": "earlier answer about 198.51.100.7"},
                 {"role": "user", "content": "what now?"}],
                config=cfg,
            )
        ])
    assert out == "Host 198.51.100.7 is under attack by mkeller."
    body = json.dumps(captured[0])
    assert "198.51.100.7" not in body and captured[0]["stream"] is True


async def test_http_errors_surface_as_ai_errors():
    def handler(request):
        return httpx.Response(401, json={"error": {"message": "bad key"}})
    with _patched_client(httpx.MockTransport(handler)):
        result = await ai_client.check_connection(_openai_cfg(enabled=False))
    assert result["ok"] is False and "401" in result["message"]


async def test_check_connection_works_before_opt_in():
    captured: list = []
    with _patched_client(_mock_transport(captured, "OK")):
        result = await ai_client.check_connection(_openai_cfg(enabled=False))
    assert result["ok"] is True and result["reply"] == "OK"
    assert captured[0]["messages"][1]["content"] == "Reply with OK."


# ── upgrade compatibility ────────────────────────────────────────────────────

def _factory(db):
    @asynccontextmanager
    async def factory():
        yield db
    return factory


async def test_auto_postmortem_is_skipped_when_ai_is_off(db):
    from datetime import datetime

    from models.incident import Incident
    from services import postmortem

    inc = Incident(rule="host_down", title="nas01 down", severity="critical", status="resolved",
                   created_at=datetime.utcnow(), resolved_at=datetime.utcnow())
    db.add(inc)
    await db.commit()
    with patch.object(postmortem, "AsyncSessionLocal", _factory(db)), \
         patch.object(postmortem, "generate_completion") as gen:
        await postmortem.generate_postmortem(inc.id)
    gen.assert_not_called()
    await db.refresh(inc)
    assert inc.postmortem is None and inc.postmortem_generated_at is None


async def test_upgrade_with_existing_key_enables_once_and_audits(db):
    from database import Setting, encrypt_value
    db.add(Setting(key="claude_api_key", value=encrypt_value("sk-ant-prod")))
    await db.commit()

    assert await migrate_ai_opt_in(_factory(db)) is True
    cfg = await load_ai_config(db)
    assert cfg.enabled and cfg.provider == PROVIDER_ANTHROPIC
    assert cfg.anthropic_api_key == "sk-ant-prod"
    assert cfg.enabled_by.startswith("system")
    audit = (await db.execute(select(AuditLog))).scalars().all()
    assert [a.action for a in audit] == ["settings.ai_enabled"]

    # Later the admin switches AI off: a restart must not switch it back on.
    row = await db.get(Setting, "ai_enabled")
    row.value = "0"
    await db.commit()
    assert await migrate_ai_opt_in(_factory(db)) is False
    assert (await load_ai_config(db)).enabled is False


async def test_new_install_stays_off(db):
    assert await migrate_ai_opt_in(_factory(db)) is False
    assert (await load_ai_config(db)).enabled is False
    from database import Setting
    # A key configured after the first start does not enable AI by itself.
    db.add(Setting(key="claude_api_key", value="x"))
    await db.commit()
    assert await migrate_ai_opt_in(_factory(db)) is False
    assert (await load_ai_config(db)).enabled is False
