"""Speech-to-text via Groq's free Whisper (OpenAI-compatible API).
Swap providers by rewriting this file; keep transcribe()."""
from functools import cache

from openai import OpenAI

import config


@cache
def _client():
    if not config.GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not set in .env")
    return OpenAI(api_key=config.GROQ_API_KEY, base_url=config.GROQ_BASE_URL,
                  timeout=config.NET_TIMEOUT_S, max_retries=1)


def transcribe(wav_bytes):
    """WAV bytes -> transcript text ('' if nothing was said)."""
    kwargs = {}
    if config.STT_LANGUAGE:
        kwargs["language"] = config.STT_LANGUAGE
    r = _client().audio.transcriptions.create(
        model=config.STT_MODEL,
        file=("speech.wav", wav_bytes, "audio/wav"),
        **kwargs,
    )
    return (r.text or "").strip()
