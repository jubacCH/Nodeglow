"""Shared Anthropic Claude API client for AI features (copilot, postmortem)."""
import asyncio
import logging
from typing import AsyncGenerator

import anthropic

from database import AsyncSessionLocal, decrypt_value, get_setting

log = logging.getLogger(__name__)

_semaphore = asyncio.Semaphore(2)  # max 2 concurrent API calls

DEFAULT_MODEL = "claude-haiku-4-5-20251001"  # fast + cheap for monitoring queries


async def _get_api_key() -> str | None:
    """Read and decrypt the Claude API key from settings."""
    async with AsyncSessionLocal() as db:
        raw = await get_setting(db, "claude_api_key", "")
        if not raw:
            return None
        try:
            return decrypt_value(raw)
        except Exception:
            return raw  # stored unencrypted (shouldn't happen)


async def generate_completion(
    system_prompt: str,
    user_message: str,
    max_tokens: int = 1024,
    model: str = DEFAULT_MODEL,
    return_usage: bool = False,
) -> str | tuple[str, dict]:
    """Non-streaming completion. If return_usage=True, returns (text, usage_dict)."""
    api_key = await _get_api_key()
    if not api_key:
        raise RuntimeError("Claude API key not configured")

    async with _semaphore:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        text = response.content[0].text
        if return_usage:
            usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "model": response.model,
            }
            return text, usage
        return text


async def stream_completion(
    system_prompt: str,
    messages: list[dict],
    max_tokens: int = 1024,
    model: str = DEFAULT_MODEL,
) -> AsyncGenerator[str, None]:
    """Streaming completion (used for copilot chat). Yields text deltas."""
    api_key = await _get_api_key()
    if not api_key:
        raise RuntimeError("Claude API key not configured")

    async with _semaphore:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        async with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield text
