"""Audio I/O and DSP helpers.

Both streams stay open for the life of the process:
- Player outputs silence when idle, so the MAX98357A never pops on start/stop,
  playback starts instantly, and stop() interrupts mid-clip.
- Recorder captures continuously and only keeps samples between start()/stop(),
  so the first syllable is not lost to stream start-up.
All audio is mono float32 internally; Player resamples to the device rate.
"""
import io
import logging
import threading
import time
import wave

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)


# --- device lookup --------------------------------------------------------

def find_device(spec, kind):
    """Return a PortAudio device index matching spec ('input' or 'output')."""
    chan_key = "max_input_channels" if kind == "input" else "max_output_channels"
    devices = sd.query_devices()
    spec = str(spec).strip()
    if spec.isdigit():
        return int(spec)
    for want in (s.strip().lower() for s in spec.split(",") if s.strip()):
        for i, d in enumerate(devices):
            if want in d["name"].lower() and d[chan_key] > 0:
                return i
    names = "\n  ".join(f"{i}: {d['name']}" for i, d in enumerate(devices) if d[chan_key] > 0)
    raise RuntimeError(f"No {kind} device matching {spec!r}. Available:\n  {names}")


def _pick_rate(check, device, channels, preferred):
    for rate in (preferred, 48000, 44100):
        try:
            check(device=device, channels=channels, samplerate=rate)
            return rate
        except Exception:
            continue
    raise RuntimeError(f"Device {device} supports none of {preferred}/48000/44100 Hz")


# --- DSP ------------------------------------------------------------------

def resample(x, src, dst):
    if src == dst or len(x) == 0:
        return x.astype(np.float32, copy=False)
    if src > dst and src % dst == 0:  # integer downsample: block average (cheap low-pass)
        f = src // dst
        n = len(x) // f * f
        return x[:n].reshape(-1, f).mean(axis=1).astype(np.float32)
    n_out = int(round(len(x) * dst / src))
    t_out = np.arange(n_out) * (src / dst)
    return np.interp(t_out, np.arange(len(x)), x).astype(np.float32)


def normalize(x, peak):
    m = float(np.max(np.abs(x))) if len(x) else 0.0
    return (x * (peak / m)).astype(np.float32) if m > 1e-6 else x.astype(np.float32)


def radio_fx(x, rate, low=300.0, high=3400.0, drive=2.5):
    """Band-limit to a radio channel and add a little saturation."""
    if len(x) == 0:
        return x
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(len(x), 1.0 / rate)
    spec[(freqs < low) | (freqs > high)] = 0
    y = np.fft.irfft(spec, n=len(x))
    y = normalize(y, 1.0)
    return (np.tanh(drive * y) / np.tanh(drive)).astype(np.float32)


def voice_fx(x, rate, radio=True, volume=0.9):
    """Everything spoken goes through this: optional radio effect, then loudness."""
    if radio:
        x = radio_fx(x, rate)
    return normalize(x, volume)


def tone(freq, seconds=0.25, rate=48000, volume=0.35):
    t = np.arange(int(seconds * rate)) / rate
    y = np.sin(2 * np.pi * freq * t)
    fade = min(int(0.005 * rate), len(y) // 2)  # 5 ms ramps, no clicks
    if fade:
        ramp = np.linspace(0, 1, fade)
        y[:fade] *= ramp
        y[-fade:] *= ramp[::-1]
    return (y * volume).astype(np.float32)


def silence(seconds, rate):
    return np.zeros(int(seconds * rate), np.float32)


# --- format conversion ----------------------------------------------------

def pcm16_to_float(pcm_bytes):
    return np.frombuffer(pcm_bytes, dtype="<i2").astype(np.float32) / 32768.0


def to_wav_bytes(x, rate):
    """Mono float32 (or int16) -> 16-bit WAV bytes."""
    if x.dtype != np.int16:
        x = (np.clip(x, -1.0, 1.0) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(x.astype("<i2").tobytes())
    return buf.getvalue()


def read_wav(path_or_bytes):
    """-> (mono float32, rate). 16-bit PCM WAV only."""
    src = io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, bytes) else str(path_or_bytes)
    with wave.open(src, "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("only 16-bit WAV supported")
        rate, ch = w.getframerate(), w.getnchannels()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, rate


# --- streams --------------------------------------------------------------

class Player:
    def __init__(self, device, rate=48000):
        info = sd.query_devices(device)
        # I2S DACs usually only accept stereo; SD pin unconnected = amp plays (L+R)/2.
        self.channels = min(2, info["max_output_channels"])
        self.rate = _pick_rate(sd.check_output_settings, device, self.channels, rate)
        self._lock = threading.Lock()
        self._buf = np.zeros(0, np.float32)
        self._pos = 0
        self._done = threading.Event()
        self._done.set()
        self.last_callback = time.monotonic()
        self.stream = sd.OutputStream(
            device=device, samplerate=self.rate, channels=self.channels,
            dtype="float32", callback=self._callback,
        )
        self.stream.start()
        log.info("Output: %s @ %d Hz, %d ch", info["name"], self.rate, self.channels)

    def _callback(self, outdata, frames, time_info, status):
        self.last_callback = time.monotonic()
        with self._lock:
            n = min(frames, len(self._buf) - self._pos)
            chunk = self._buf[self._pos:self._pos + n]
            self._pos += n
            finished = self._pos >= len(self._buf)
        outdata[:n] = chunk[:, None]
        outdata[n:] = 0
        if finished:
            self._done.set()

    def play(self, samples, rate):
        """Start playing (non-blocking). Replaces anything already playing."""
        x = np.clip(resample(samples, rate, self.rate), -1.0, 1.0)
        with self._lock:
            self._buf = x
            self._pos = 0
            self._done.clear()

    def stop(self):
        with self._lock:
            self._buf = np.zeros(0, np.float32)
            self._pos = 0
        self._done.set()

    @property
    def playing(self):
        return not self._done.is_set()

    def wait(self, timeout=None):
        return self._done.wait(timeout)

    def play_blocking(self, samples, rate):
        self.play(samples, rate)
        self.wait()

    def alive(self):
        return self.stream.active and time.monotonic() - self.last_callback < 3.0

    def close(self):
        self.stream.close()


class Recorder:
    def __init__(self, device, rate=48000):
        info = sd.query_devices(device)
        self.rate = _pick_rate(sd.check_input_settings, device, 1, rate)
        self._lock = threading.Lock()
        self._chunks = []
        self._on = False
        self.last_callback = time.monotonic()
        self.stream = sd.InputStream(
            device=device, samplerate=self.rate, channels=1,
            dtype="int16", callback=self._callback,
        )
        self.stream.start()
        log.info("Input: %s @ %d Hz", info["name"], self.rate)

    def _callback(self, indata, frames, time_info, status):
        self.last_callback = time.monotonic()
        if self._on:
            with self._lock:
                if self._on:
                    self._chunks.append(indata[:, 0].copy())

    def start(self):
        with self._lock:
            self._chunks = []
            self._on = True

    def stop(self):
        """-> int16 mono samples recorded since start()."""
        with self._lock:
            self._on = False
            chunks, self._chunks = self._chunks, []
        return np.concatenate(chunks) if chunks else np.zeros(0, np.int16)

    def alive(self):
        return self.stream.active and time.monotonic() - self.last_callback < 3.0

    def close(self):
        self.stream.close()


def level_dbfs(x_int16):
    """Peak and RMS in dBFS, for tuning mic gain from the logs."""
    if len(x_int16) == 0:
        return -120.0, -120.0
    x = x_int16.astype(np.float32) / 32768.0
    peak = float(np.max(np.abs(x)))
    rms = float(np.sqrt(np.mean(x * x)))
    db = lambda v: 20 * np.log10(max(v, 1e-6))
    return db(peak), db(rms)
