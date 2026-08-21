"""Async LLM wrapper with rate limiting and retries."""
import asyncio
import json
import logging
from openai import AsyncOpenAI
from backend.config import LLM_PROVIDER, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

logger = logging.getLogger(__name__)

_client_kwargs = {"api_key": LLM_API_KEY}
if LLM_BASE_URL:
    _client_kwargs["base_url"] = LLM_BASE_URL

_client = AsyncOpenAI(**_client_kwargs)
_semaphore = asyncio.Semaphore(5)


async def call_llm(system_prompt: str, user_prompt: str, max_retries: int = 3) -> dict:
    """Call the configured provider and return parsed JSON."""
    if not LLM_API_KEY:
        raise RuntimeError(f"{LLM_PROVIDER} API key is not configured")

    last_error: Exception | None = None

    async with _semaphore:
        for attempt in range(max_retries):
            try:
                resp = await _client.chat.completions.create(
                    model=LLM_MODEL,
                    temperature=0.1,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                )
                content = resp.choices[0].message.content or "{}"
                return json.loads(content)
            except Exception as e:
                last_error = e
                wait = 2 ** attempt
                logger.warning(
                    "%s call attempt %s failed for model %s: %s, retrying in %ss",
                    LLM_PROVIDER,
                    attempt + 1,
                    LLM_MODEL,
                    e,
                    wait,
                )
                await asyncio.sleep(wait)

        detail = str(last_error) if last_error else "unknown error"
        raise RuntimeError(
            f"{LLM_PROVIDER} call failed after {max_retries} retries for model {LLM_MODEL}: {detail}"
        )
