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
    """Load the voice up front (takes several seconds on a Pi 4)."""
    _voice()


def speak(text):
    """text -> (pcm16 mono bytes, sample_rate)."""
    voice = _voice()
    syn = SynthesisConfig(length_scale=1.0 / config.PIPER_SPEED)
    with _lock:
        chunks = list(voice.synthesize(text, syn_config=syn))
    if not chunks:
        raise RuntimeError("Piper produced no audio")
    return b"".join(c.audio_int16_bytes for c in chunks), chunks[0].sample_rate
