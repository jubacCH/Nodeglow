"""Shared LLM client for AI features (Glow chat, postmortems, daily summary).

Every call goes through :func:`generate_completion` or
:func:`stream_completion`, which:

1. refuse unless an admin opted in (``ai_enabled``) -> :class:`AIDisabledError`;
2. pick the configured provider (Anthropic, or any OpenAI-compatible Chat
   Completions endpoint: Azure OpenAI, Ollama, vLLM, LM Studio, ...);
3. redact personal data and secrets from everything sent (system prompt and
   all messages, one placeholder map per request) and, if configured, map the
   placeholders back in the answer. The map never leaves this process.

The features only use plain text completion (no tool/function calling), so
every provider that speaks Chat Completions supports all of them.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncGenerator
from urllib.parse import quote

import httpx

from services.ai_config import (
    DEFAULT_ANTHROPIC_MODEL,
    PROVIDER_ANTHROPIC,
    PROVIDER_OPENAI,
    AIConfig,
    AIDisabledError,
    AIError,
    AINotConfiguredError,
    get_ai_config,
    validate_ai_base_url_async,
)
from services.ai_redaction import REDACTION_SYSTEM_NOTE, Redactor

log = logging.getLogger(__name__)

_semaphore = asyncio.Semaphore(2)  # max 2 concurrent API calls

DEFAULT_MODEL = DEFAULT_ANTHROPIC_MODEL  # kept for callers that import it

_OPENAI_TIMEOUT = httpx.Timeout(120.0, connect=10.0)

# Anthropic Haiku 4.5 list price; other providers are billed elsewhere (or
# not at all for local models), so their cost is recorded as 0.
_HAIKU_COST_PER_INPUT = 1.0 / 1_000_000
_HAIKU_COST_PER_OUTPUT = 5.0 / 1_000_000


def estimate_cost_usd(usage: dict) -> float:
    if usage.get("provider", PROVIDER_ANTHROPIC) != PROVIDER_ANTHROPIC:
        return 0.0
    return round(
        usage.get("input_tokens", 0) * _HAIKU_COST_PER_INPUT
        + usage.get("output_tokens", 0) * _HAIKU_COST_PER_OUTPUT, 6,
    )


async def _get_api_key() -> str | None:
    """Back-compat helper: the Anthropic key, decrypted, or None."""
    cfg = await get_ai_config()
    return cfg.anthropic_api_key or None


async def _known_hostnames() -> list[str]:
    """Names of monitored hosts and integrations, for optional hostname redaction.

    Short names like "nas01" do not look like hostnames to a regex; the
    inventory is the only reliable list of them.
    """
    try:
        from sqlalchemy import select

        from database import AsyncSessionLocal
        from models.integration import IntegrationConfig
        from models.ping import PingHost

        names: set[str] = set()
        async with AsyncSessionLocal() as db:
            for name, hostname in (await db.execute(select(PingHost.name, PingHost.hostname))).all():
                names.update(v for v in (name, hostname) if v)
            for name, host in (await db.execute(select(IntegrationConfig.name, IntegrationConfig.host))).all():
                names.update(v for v in (name, host) if v)
        return sorted(names)
    except Exception as exc:  # never block a request on this
        log.debug("Known hostnames for redaction unavailable: %s", exc)
        return []


async def _prepare(cfg: AIConfig) -> Redactor:
    if not cfg.enabled:
        raise AIDisabledError()
    if not cfg.configured:
        raise AINotConfiguredError()
    if cfg.provider == PROVIDER_OPENAI:
        err = await validate_ai_base_url_async(cfg.openai_base_url)
        if err:
            raise AINotConfiguredError(f"AI endpoint rejected: {err}")
    hosts = await _known_hostnames() if (cfg.redact and cfg.redact_hostnames) else []
    return Redactor(enabled=cfg.redact, redact_hostnames=cfg.redact_hostnames, known_hostnames=hosts)


def _redacted_request(redactor: Redactor, system_prompt: str, messages: list[dict]) -> tuple[str, list[dict]]:
    # One pass over everything, so a value found in the context is also
    # caught where the user typed it or where an earlier answer quoted it.
    redacted = redactor.redact_messages([{"role": "system", "content": system_prompt}, *messages])
    system, msgs = redacted[0]["content"], redacted[1:]
    if redactor.count:
        system = f"{system}\n\n{REDACTION_SYSTEM_NOTE}"
    return system, msgs


# ── OpenAI-compatible Chat Completions ───────────────────────────────────────

def openai_request_target(cfg: AIConfig) -> tuple[str, dict[str, str]]:
    """URL and auth headers for an OpenAI-compatible endpoint.

    * Azure OpenAI (``api_version`` set): ``{base}/openai/deployments/{model}/
      chat/completions?api-version=...`` with an ``api-key`` header; the
      model field is the deployment name. A base URL that already contains the
      deployment path is used as is.
    * Everything else (OpenAI, Ollama ``/v1``, vLLM, LM Studio):
      ``{base}/chat/completions`` with an optional Bearer token.
    """
    base = cfg.openai_base_url.strip().rstrip("/")
    headers = {"Content-Type": "application/json"}
    if cfg.is_azure:
        if base.endswith("/chat/completions"):
            url = base
        elif "/openai/deployments/" in base:
            url = f"{base}/chat/completions"
        else:
            url = f"{base}/openai/deployments/{quote(cfg.openai_model, safe='')}/chat/completions"
        url += ("&" if "?" in url else "?") + "api-version=" + quote(cfg.openai_api_version, safe="")
        if cfg.openai_api_key:
            headers["api-key"] = cfg.openai_api_key
    else:
        url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
        if cfg.openai_api_key:
            headers["Authorization"] = f"Bearer {cfg.openai_api_key}"
    return url, headers


def _openai_body(cfg: AIConfig, system: str, messages: list[dict], max_tokens: int,
                 stream: bool, token_field: str = "max_tokens") -> dict:
    body = {
        "model": cfg.openai_model,
        "messages": [{"role": "system", "content": system}, *messages],
        token_field: max_tokens,
        "stream": stream,
    }
    return body


def _http_error(resp: httpx.Response, text: str) -> AIError:
    snippet = text.strip().replace("\n", " ")[:300]
    return AIError(f"AI provider returned HTTP {resp.status_code}: {snippet}")


def _wants_max_completion_tokens(status: int, text: str) -> bool:
    # Newer OpenAI / Azure reasoning models reject max_tokens.
    return status == 400 and "max_completion_tokens" in text


async def _openai_complete(cfg: AIConfig, system: str, messages: list[dict], max_tokens: int) -> tuple[str, dict]:
    url, headers = openai_request_target(cfg)
    async with httpx.AsyncClient(timeout=_OPENAI_TIMEOUT, follow_redirects=False) as client:
        resp = await client.post(url, headers=headers, json=_openai_body(cfg, system, messages, max_tokens, False))
        if _wants_max_completion_tokens(resp.status_code, resp.text):
            resp = await client.post(
                url, headers=headers,
                json=_openai_body(cfg, system, messages, max_tokens, False, "max_completion_tokens"),
            )
        if resp.status_code >= 400:
            raise _http_error(resp, resp.text)
        try:
            data = resp.json()
            text = data["choices"][0]["message"].get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AIError(f"AI provider sent an unexpected response: {exc}") from exc
    usage = data.get("usage") or {}
    return text, {
        "input_tokens": int(usage.get("prompt_tokens") or 0),
        "output_tokens": int(usage.get("completion_tokens") or 0),
        "model": data.get("model") or cfg.openai_model,
        "provider": PROVIDER_OPENAI,
    }


async def _openai_stream(cfg: AIConfig, system: str, messages: list[dict], max_tokens: int) -> AsyncGenerator[str, None]:
    url, headers = openai_request_target(cfg)
    async with httpx.AsyncClient(timeout=_OPENAI_TIMEOUT, follow_redirects=False) as client:
        for token_field in ("max_tokens", "max_completion_tokens"):
            body = _openai_body(cfg, system, messages, max_tokens, True, token_field)
            async with client.stream("POST", url, headers=headers, json=body) as resp:
                if resp.status_code >= 400:
                    text = (await resp.aread()).decode("utf-8", "replace")
                    if token_field == "max_tokens" and _wants_max_completion_tokens(resp.status_code, text):
                        continue
                    raise _http_error(resp, text)
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        return
                    try:
                        chunk = json.loads(payload)
                    except ValueError:
                        continue
                    for choice in chunk.get("choices") or []:
                        delta = (choice.get("delta") or {}).get("content")
                        if delta:
                            yield delta
                return


# ── Anthropic ────────────────────────────────────────────────────────────────

async def _anthropic_complete(cfg: AIConfig, system: str, messages: list[dict], max_tokens: int, model: str) -> tuple[str, dict]:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=cfg.anthropic_api_key)
    response = await client.messages.create(
        model=model, max_tokens=max_tokens, system=system, messages=messages,
    )
    text = "".join(getattr(b, "text", "") for b in response.content)
    return text, {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "model": response.model,
        "provider": PROVIDER_ANTHROPIC,
    }


async def _anthropic_stream(cfg: AIConfig, system: str, messages: list[dict], max_tokens: int, model: str) -> AsyncGenerator[str, None]:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=cfg.anthropic_api_key)
    async with client.messages.stream(
        model=model, max_tokens=max_tokens, system=system, messages=messages,
    ) as stream:
        async for text in stream.text_stream:
            yield text


# ── Public API ───────────────────────────────────────────────────────────────

async def generate_completion(
    system_prompt: str,
    user_message: str,
    max_tokens: int = 1024,
    model: str | None = None,
    return_usage: bool = False,
    config: AIConfig | None = None,
) -> str | tuple[str, dict]:
    """Non-streaming completion. If return_usage=True, returns (text, usage_dict).

    Raises :class:`AIDisabledError` / :class:`AINotConfiguredError` (both
    ``RuntimeError``) before anything is sent.
    """
    cfg = config or await get_ai_config()
    redactor = await _prepare(cfg)
    system, messages = _redacted_request(
        redactor, system_prompt, [{"role": "user", "content": user_message}],
    )
    async with _semaphore:
        if cfg.provider == PROVIDER_OPENAI:
            text, usage = await _openai_complete(cfg, system, messages, max_tokens)
        else:
            text, usage = await _anthropic_complete(cfg, system, messages, max_tokens, model or cfg.anthropic_model)
    if cfg.restore_placeholders:
        text = redactor.restore(text)
    usage["redacted_values"] = redactor.count
    return (text, usage) if return_usage else text


async def stream_completion(
    system_prompt: str,
    messages: list[dict],
    max_tokens: int = 1024,
    model: str | None = None,
    config: AIConfig | None = None,
) -> AsyncGenerator[str, None]:
    """Streaming completion (used for Glow chat). Yields text deltas."""
    cfg = config or await get_ai_config()
    redactor = await _prepare(cfg)
    system, msgs = _redacted_request(redactor, system_prompt, messages)
    restorer = redactor.stream_restorer() if cfg.restore_placeholders else None

    async with _semaphore:
        if cfg.provider == PROVIDER_OPENAI:
            source = _openai_stream(cfg, system, msgs, max_tokens)
        else:
            source = _anthropic_stream(cfg, system, msgs, max_tokens, model or cfg.anthropic_model)
        async for delta in source:
            out = restorer.feed(delta) if restorer else delta
            if out:
                yield out
    if restorer:
        tail = restorer.flush()
        if tail:
            yield tail


async def check_connection(cfg: AIConfig) -> dict:
    """Send a tiny, data-free prompt to check endpoint, key and model.

    Does not require ``ai_enabled``: it carries no infrastructure data, and an
    admin should be able to verify the provider before switching AI on.
    """
    if not cfg.configured:
        return {"ok": False, "message": "Missing: " + ", ".join(cfg.missing())}
    if cfg.provider == PROVIDER_OPENAI:
        err = await validate_ai_base_url_async(cfg.openai_base_url)
        if err:
            return {"ok": False, "message": err}
    system = "You are a connectivity check. Answer with the single word OK."
    messages = [{"role": "user", "content": "Reply with OK."}]
    started = time.monotonic()
    try:
        async with _semaphore:
            if cfg.provider == PROVIDER_OPENAI:
                text, usage = await _openai_complete(cfg, system, messages, 16)
            else:
                text, usage = await _anthropic_complete(cfg, system, messages, 16, cfg.anthropic_model)
    except httpx.ConnectError as exc:
        return {"ok": False, "message": f"Cannot connect: {exc}"}
    except httpx.TimeoutException:
        return {"ok": False, "message": "The AI endpoint did not answer in time"}
    except Exception as exc:
        return {"ok": False, "message": str(exc)[:400] or exc.__class__.__name__}
    latency = int((time.monotonic() - started) * 1000)
    return {
        "ok": True,
        "message": f"Connected to {cfg.provider_label} ({usage.get('model') or cfg.model}) in {latency} ms",
        "model": usage.get("model") or cfg.model,
        "latency_ms": latency,
        "reply": (text or "").strip()[:50],
    }
