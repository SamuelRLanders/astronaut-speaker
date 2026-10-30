"""Mission Control brain via Groq's free models (gpt-oss) (OpenAI-compatible API).
Swap providers by rewriting this file; keep reply()."""
import logging
import re
from functools import cache

import openai
from openai import OpenAI

import config

log = logging.getLogger(__name__)


@cache
def _client():
    if not config.GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not set in .env")
    return OpenAI(api_key=config.GROQ_API_KEY, base_url=config.GROQ_BASE_URL,
                  timeout=config.NET_TIMEOUT_S, max_retries=1)


def _ask(model, messages):
    kwargs = {}
    if "gpt-oss" in model and config.LLM_REASONING_EFFORT:
        kwargs["reasoning_effort"] = config.LLM_REASONING_EFFORT  # other models reject it
    resp = _client().chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=config.LLM_MAX_TOKENS,
        temperature=0.8,
        **kwargs,
    )
    return resp.choices[0].message.content or ""


def reply(text, history):
    """text: what the astronaut said. history: list of (user, assistant) pairs."""
    messages = [{"role": "system", "content": config.SYSTEM_PROMPT}]
    for user, assistant in history:
        messages.append({"role": "user", "content": user})
        messages.append({"role": "assistant", "content": assistant})
    messages.append({"role": "user", "content": text})

    try:
        out = _ask(config.LLM_MODEL, messages)
    except openai.RateLimitError:
        if not config.LLM_FALLBACK_MODEL:
            raise
        log.warning("%s rate-limited, using %s", config.LLM_MODEL, config.LLM_FALLBACK_MODEL)
        out = _ask(config.LLM_FALLBACK_MODEL, messages)

    out = re.sub(r"[*_#`>]", "", out).strip()  # nothing the TTS would read aloud oddly
    if not out:
        raise RuntimeError("empty LLM reply")
    return out
