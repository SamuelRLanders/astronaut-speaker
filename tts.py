"""Text-to-speech with Piper, running locally on the Pi (free, works offline).
Swap providers by rewriting this file; keep speak()."""
import threading
from functools import cache

from piper import PiperVoice, SynthesisConfig

import config

_lock = threading.Lock()  # one synthesis at a time; keeps CPU/RAM use predictable


@cache
def _voice():
    path = config.VOICES_DIR / f"{config.PIPER_VOICE}.onnx"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} missing. Run: python -m piper.download_voices --data-dir voices {config.PIPER_VOICE}")
    return PiperVoice.load(path)


def load():
    """Load the voice up front (takes several seconds on a Pi 4) and run one
    throwaway synthesis, so the first real reply isn't slowed by a cold start."""
    list(_voice().synthesize("Comms check."))


def speak_stream(text):
    """Yield (pcm16 mono bytes, sample_rate) one sentence at a time, so playback
    can start while later sentences are still being synthesized."""
    syn = SynthesisConfig(length_scale=1.0 / config.PIPER_SPEED)
    chunks = iter(_voice().synthesize(text, syn_config=syn))
    while True:
        with _lock:  # held per sentence only, never across a yield
            chunk = next(chunks, None)
        if chunk is None:
            return
        yield chunk.audio_int16_bytes, chunk.sample_rate


def speak(text):
    """text -> (pcm16 mono bytes, sample_rate) for the whole text."""
    parts = list(speak_stream(text))
    if not parts:
        raise RuntimeError("Piper produced no audio")
    return b"".join(p for p, _ in parts), parts[0][1]
