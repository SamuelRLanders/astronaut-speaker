"""All settings in one place. Secrets come from .env; any setting below can be
overridden there too (same name), so you never need to edit code at the party."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def _str(name, default):
    return os.getenv(name, default)


def _int(name, default):
    return int(os.getenv(name, default))


def _float(name, default):
    return float(os.getenv(name, default))


def _bool(name, default):
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# --- API keys -------------------------------------------------------------
GROQ_API_KEY = _str("GROQ_API_KEY", "")  # free at console.groq.com (STT + LLM)
GROQ_BASE_URL = _str("GROQ_BASE_URL", "https://api.groq.com/openai/v1")

# --- Hardware -------------------------------------------------------------
BUTTON_GPIO = _int("BUTTON_GPIO", 17)  # BCM numbering (physical pin 11)

# Devices are matched by case-insensitive substring of the PortAudio device
# name (run `python checks.py devices`). Comma-separated = try in order.
# An integer picks a device by index.
INPUT_DEVICE = _str("INPUT_DEVICE", "USB Audio")
OUTPUT_DEVICE = _str("OUTPUT_DEVICE", "MAX98357A,hifiberry")

INPUT_RATE = _int("INPUT_RATE", 48000)    # C-Media card: 44100 or 48000 only
OUTPUT_RATE = _int("OUTPUT_RATE", 48000)  # everything is resampled to this
STT_RATE = _int("STT_RATE", 16000)        # downsample before upload (smaller, faster)

VOLUME = _float("VOLUME", 0.9)            # peak level for voice, 0..1
BEEP_VOLUME = _float("BEEP_VOLUME", 0.35)  # peak level for Quindar beeps, 0..1
RADIO_EFFECT = _bool("RADIO_EFFECT", True)  # band-pass + light crunch on voice

# --- Behaviour ------------------------------------------------------------
MIN_HOLD_S = _float("MIN_HOLD_S", 0.5)     # shorter presses are ignored
MAX_RECORD_S = _float("MAX_RECORD_S", 30)  # stop recording if button is stuck
NET_TIMEOUT_S = _float("NET_TIMEOUT_S", 15)  # per network call
HISTORY_TURNS = _int("HISTORY_TURNS", 5)   # exchanges remembered

# --- Models ---------------------------------------------------------------
STT_MODEL = _str("STT_MODEL", "whisper-large-v3-turbo")  # Groq
STT_LANGUAGE = _str("STT_LANGUAGE", "en")  # empty = auto-detect

# Groq models your key can use: python checks.py models
LLM_MODEL = _str("LLM_MODEL", "openai/gpt-oss-120b")
# Used when the main model is rate-limited (free tier daily token cap).
LLM_FALLBACK_MODEL = _str("LLM_FALLBACK_MODEL", "openai/gpt-oss-20b")
LLM_MAX_TOKENS = _int("LLM_MAX_TOKENS", 400)  # gpt-oss reasoning tokens count toward this
LLM_REASONING_EFFORT = _str("LLM_REASONING_EFFORT", "low")  # gpt-oss only

# Piper runs locally (free, offline). Voices: python -m piper.download_voices --data-dir voices <name>
PIPER_VOICE = _str("PIPER_VOICE", "en_US-ryan-medium")
PIPER_SPEED = _float("PIPER_SPEED", 1.0)  # >1 = faster speech

SYSTEM_PROMPT = _str(
    "SYSTEM_PROMPT",
    "You are Mission Control in Houston, talking to an astronaut over the radio. "
    "The astronaut is really a person in a costume at a Halloween party, and the "
    "people around them are hearing your replies through a speaker in the suit. "
    "Stay in character, be playful and quick-witted, keep it party-friendly. "
    "Reply in one or two short spoken sentences, plain words only: no lists, "
    "markdown, emoji or stage directions. End with 'over' when it fits.",
)

# --- Files ----------------------------------------------------------------
SOUNDS_DIR = ROOT / "sounds"
VOICES_DIR = ROOT / "voices"
