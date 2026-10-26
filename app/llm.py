"""The ONE place we talk to the model (class LiteLLM proxy via the OpenAI client).
Everything is temperature=0: extraction and SQL answers must be repeatable so the
evaluation numbers are reproducible."""
import base64
import json
import re
from functools import lru_cache

from openai import AsyncOpenAI

from app import config


@lru_cache(maxsize=1)
def _client() -> AsyncOpenAI:
    # Built lazily so tests can import the app (and stub `complete`) without a key.
    return AsyncOpenAI()


async def complete(messages: list[dict], *, max_tokens: int = 500) -> str:
    resp = await _client().chat.completions.create(
        model=config.CHAT_MODEL, messages=messages, temperature=0, max_tokens=max_tokens
    )
    return resp.choices[0].message.content or ""


async def complete_json(messages: list[dict]) -> dict:
    """One call that must return a JSON object. The proxy ignores `response_format`, so
    we parse the reply ourselves and, if it isn't JSON, show the model its own output
    once and ask again (parse-then-repair). Never raises: failure is `{"_error": ...}`."""
    first = await complete(messages)
    if (obj := parse_json_object(first)) is not None:
        return obj
    repaired = await complete([
        *messages,
        {"role": "assistant", "content": first},
        {"role": "user", "content": "That was not valid JSON. Reply with ONLY the JSON object."},
    ])
    return parse_json_object(repaired) or {"_error": "model did not return JSON", "_raw": first[:500]}


def image_part(image_bytes: bytes, mime: str) -> dict:
    """An image as an OpenAI-style content part (data URL) — gemma-4-E4B-it is multimodal."""
    data_url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"
    return {"type": "image_url", "image_url": {"url": data_url}}


def parse_json_object(text: str) -> dict | None:
    """Pull the first JSON object out of a reply (the model may add ``` fences or prose)."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group())
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None
