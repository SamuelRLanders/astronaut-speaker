"""Astronaut push-to-talk loop: hold button, talk, release, Mission Control answers.

Never crashes on a bad cycle: any error plays "losing signal" and goes back to
idle. If an audio stream dies, the process exits and systemd restarts it.
"""
import logging
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import numpy as np

import audio
import config
import llm
import stt
import tts
from button import PTT
from make_sounds import BEEPS, CLIPS

log = logging.getLogger("astronaut")


def load_sounds():
    """name -> (samples, rate). Missing files get a synthesized stand-in."""
    r = 48000
    stand_ins = {  # beep patterns, used only if make_sounds.py hasn't been run
        "comms_online": [(1500, 0.12), (0, 0.06), (2000, 0.12)],
        "say_again": [(1200, 0.12), (0, 0.06), (1200, 0.12)],
        "losing_signal": [(1800, 0.15), (0, 0.05), (1200, 0.15), (0, 0.05), (700, 0.3)],
    }
    sounds = {}
    for name, hz in BEEPS.items():
        path = config.SOUNDS_DIR / f"{name}.wav"
        if path.exists():
            x, rate = audio.read_wav(path)
            sounds[name] = (audio.normalize(x, config.BEEP_VOLUME), rate)
        else:
            sounds[name] = (audio.tone(hz, 0.25, r, config.BEEP_VOLUME), r)
    for name in CLIPS:
        path = config.SOUNDS_DIR / f"{name}.wav"
        if path.exists():
            x, rate = audio.read_wav(path)
            sounds[name] = (audio.voice_fx(x, rate, config.RADIO_EFFECT, config.VOLUME), rate)
        else:
            log.warning("%s missing, using beeps (run make_sounds.py)", path.name)
            parts = [audio.tone(f, d, r, config.BEEP_VOLUME) if f else audio.silence(d, r)
                     for f, d in stand_ins[name]]
            sounds[name] = (np.concatenate(parts), r)
    return sounds


def process(wav_bytes, history, t_release):
    """Runs in a worker thread: STT -> LLM -> TTS. Returns (heard, reply, (samples, rate))."""
    t = time.monotonic()
    heard = stt.transcribe(wav_bytes)
    log.info("STT %.2fs: %r", time.monotonic() - t, heard)
    if not any(c.isalnum() for c in heard):
        return heard, None, None

    t = time.monotonic()
    answer = llm.reply(heard, history)
    log.info("LLM %.2fs: %r", time.monotonic() - t, answer)

    t = time.monotonic()
    pcm, rate = tts.speak(answer)
    samples = audio.voice_fx(audio.pcm16_to_float(pcm), rate, config.RADIO_EFFECT, config.VOLUME)
    log.info("TTS %.2fs (%.1fs audio). Release->speech %.2fs", time.monotonic() - t,
             len(samples) / rate, time.monotonic() - t_release)
    return heard, answer, (samples, rate)


class Astronaut:
    def __init__(self):
        out_dev = audio.find_device(config.OUTPUT_DEVICE, "output")
        in_dev = audio.find_device(config.INPUT_DEVICE, "input")
        self.player = audio.Player(out_dev, config.OUTPUT_RATE)
        self.rec = audio.Recorder(in_dev, config.INPUT_RATE)
        self.button = PTT()
        self.sounds = load_sounds()
        tts.load()  # Piper voice takes several seconds to load; do it before "comms online"
        self.history = deque(maxlen=config.HISTORY_TURNS)
        self.pool = ThreadPoolExecutor(max_workers=4)

    def play(self, name):
        self.player.play(*self.sounds[name])

    def play_interruptible(self, samples, rate):
        """Play; return True if the button was pressed (playback stops at once)."""
        self.player.play(samples, rate)
        while self.player.playing:
            if self.button.pressed():
                self.player.stop()
                log.info("Interrupted by button")
                return True
            time.sleep(0.01)
        return False

    def check_streams(self):
        if not (self.player.alive() and self.rec.alive()):
            log.error("Audio stream died; exiting so systemd restarts us")
            sys.exit(1)

    def cycle(self):
        """One push-to-talk exchange. Button is already down. Returns True if the
        button was pressed again (interrupt) and a new cycle should start now."""
        self.player.stop()
        self.play("beep_start")
        self.rec.start()
        t0 = time.monotonic()
        log.info("PTT down, recording")
        if not self.button.wait_release(timeout=config.MAX_RECORD_S):
            log.warning("Held > %.0fs, cutting recording", config.MAX_RECORD_S)
        samples = self.rec.stop()
        t_release = time.monotonic()
        self.play("beep_end")
        held = t_release - t0
        peak, rms = audio.level_dbfs(samples)
        log.info("PTT up: %.2fs held, peak %.1f dBFS, rms %.1f dBFS", held, peak, rms)

        if held < config.MIN_HOLD_S:
            log.info("Too short, ignored")
            return False

        x = audio.resample(samples.astype(np.float32) / 32768.0, self.rec.rate, config.STT_RATE)
        wav = audio.to_wav_bytes(x, config.STT_RATE)
        fut = self.pool.submit(process, wav, list(self.history), t_release)

        # Wait for the pipeline, but let a new press abandon it.
        while not fut.done():
            if self.button.pressed():
                log.info("New press while thinking; abandoning this one")
                return True
            time.sleep(0.01)

        heard, answer, speech = fut.result()  # re-raises pipeline errors
        if answer is None:
            return self.play_interruptible(*self.sounds["say_again"])
        self.history.append((heard, answer))
        return self.play_interruptible(*speech)

    def run(self):
        self.player.play_blocking(*self.sounds["comms_online"])
        log.info("Ready. Hold the button to talk.")
        again = False
        while True:
            if not again:
                while not self.button.wait_press(timeout=1.0):
                    self.check_streams()
            try:
                again = self.cycle()
            except Exception:
                log.exception("Cycle failed")
                try:
                    again = self.play_interruptible(*self.sounds["losing_signal"])
                except Exception:
                    log.exception("Could not even play the error clip")
                    again = False
                    self.check_streams()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    for noisy in ("httpx", "httpx2", "httpcore", "openai", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if not config.GROQ_API_KEY:
        log.warning("GROQ_API_KEY missing in .env; every request will fail")
    Astronaut().run()


if __name__ == "__main__":
    main()
