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
