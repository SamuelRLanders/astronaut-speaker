"""Astronaut push-to-talk loop: hold button, talk, release, Mission Control answers.

Never crashes on a bad cycle: any error plays "losing signal" and goes back to
idle. If an audio stream dies, the process exits and systemd restarts it.

Status signals:
  green LED  fast blink = starting up, slow blink = no internet, solid = ready
  red LED    solid = recording, fast blink = thinking, off = idle/speaking
  voice      "comms online" / "no signal" when the internet comes and goes
Hold the button for SHUTDOWN_HOLD_S seconds to power the Pi off safely.
"""
import logging
import queue
import signal
import socket
import subprocess
import sys
import threading
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
from leds import StatusLights
from make_sounds import BEEPS, CLIPS

log = logging.getLogger("astronaut")

SENTENCE_GAP_S = 0.15  # pause between streamed sentences


def load_sounds():
    """name -> (samples, rate). Missing files get a synthesized stand-in."""
    r = 48000
    stand_ins = {  # beep patterns, used only if make_sounds.py hasn't been run
        "comms_online": [(1500, 0.12), (0, 0.06), (2000, 0.12)],
        "say_again": [(1200, 0.12), (0, 0.06), (1200, 0.12)],
        "losing_signal": [(1800, 0.15), (0, 0.05), (1200, 0.15), (0, 0.05), (700, 0.3)],
        "no_signal": [(700, 0.3), (0, 0.1), (700, 0.3)],
        "shutting_down": [(1500, 0.2), (1000, 0.2), (500, 0.4)],
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


def process(wav_bytes, history, t_release, q, cancel):
    """Runs in a worker thread: STT -> LLM -> TTS, reporting through queue q:
    ("say_again",) | ("answer", heard, reply) then ("audio", samples, rate) per
    sentence | ("error", exc); always ends with ("done",). Stops early if
    cancel is set (button pressed again)."""
    try:
        t = time.monotonic()
        heard = stt.transcribe(wav_bytes)
        log.info("STT %.2fs: %r", time.monotonic() - t, heard)
        if not any(c.isalnum() for c in heard):
            q.put(("say_again",))
            return
        if cancel.is_set():
            return

        t = time.monotonic()
        answer = llm.reply(heard, history)
        log.info("LLM %.2fs: %r", time.monotonic() - t, answer)
        q.put(("answer", heard, answer))

        t = time.monotonic()
        first = True
        for pcm, rate in tts.speak_stream(answer):
            if cancel.is_set():
                return
            x = audio.voice_fx(audio.pcm16_to_float(pcm), rate, config.RADIO_EFFECT, config.VOLUME)
            q.put(("audio", np.concatenate([x, audio.silence(SENTENCE_GAP_S, rate)]), rate))
            if first:
                first = False
                log.info("TTS first sentence %.2fs. Release->speech %.2fs",
                         time.monotonic() - t, time.monotonic() - t_release)
        log.info("TTS all sentences %.2fs", time.monotonic() - t)
    except Exception as e:
        q.put(("error", e))
    finally:
        q.put(("done",))


def internet_ok():
    try:
        socket.create_connection((config.NET_CHECK_HOST, 443), timeout=3).close()
        return True
    except OSError:
        return False


def under_voltage():
    """True if the Pi reports under-voltage right now (weak power bank/cable)."""
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True,
                             text=True, timeout=3).stdout
        return bool(int(out.strip().split("=")[1], 16) & 0x1)
    except Exception:
        return False


class Astronaut:
    def __init__(self, lights):
        self.lights = lights
        out_dev = audio.find_device(config.OUTPUT_DEVICE, "output")
        in_dev = audio.find_device(config.INPUT_DEVICE, "input")
        self.player = audio.Player(out_dev, config.OUTPUT_RATE)
        self.rec = audio.Recorder(in_dev, config.INPUT_RATE)
        self.button = PTT()
        self.sounds = load_sounds()
        tts.load()  # Piper voice takes several seconds to load; do it before "comms online"
        self.history = deque(maxlen=config.HISTORY_TURNS)
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.ready = False
        self.online = None      # latest network state from the monitor thread
        self.announced = None   # network state we last told the astronaut about
        threading.Thread(target=self._monitor, daemon=True).start()

    # --- background monitor -------------------------------------------------

    def _monitor(self):
        fails, last_power_check = 0, 0.0
        while True:
            if internet_ok():
                fails, self.online = 0, True
            else:
                fails += 1
                if fails >= 2 or self.online is None:  # one blip isn't an outage
                    self.online = False
            if self.ready:
                self.lights.green = "on" if self.online else "slow"
            if time.monotonic() - last_power_check > 30:
                last_power_check = time.monotonic()
                if under_voltage():
                    log.warning("UNDER-VOLTAGE: power bank or cable can't supply enough current")
            time.sleep(5 if fails == 0 else 2)  # re-check quickly after a failure

    def announce_network(self):
        """Say "comms online" / "no signal" when the network state changes (idle only)."""
        if self.online is None or self.online == self.announced:
            return
        self.announced = self.online
        log.info("Network %s", "online" if self.online else "OFFLINE")
        self.play("comms_online" if self.online else "no_signal")

    # --- audio helpers -----------------------------------------------------

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

    # --- actions -----------------------------------------------------------

    def shutdown(self):
        log.warning("Button held %.0fs: shutting down", config.SHUTDOWN_HOLD_S)
        self.lights.green, self.lights.red = "off", "fast"
        self.player.play_blocking(*self.sounds["shutting_down"])
        r = subprocess.run(["sudo", "-n", "systemctl", "poweroff"], capture_output=True, text=True)
        if r.returncode == 0:
            time.sleep(60)  # systemd stops us
        log.error("Poweroff failed (%s); run sudo ./install.sh", r.stderr.strip())
        self.play("losing_signal")
        self.button.wait_release()

    def cycle(self):
        """One push-to-talk exchange. Button is already down. Returns True if the
        button was pressed again (interrupt) and a new cycle should start now."""
        self.player.stop()
        self.play("beep_start")
        self.rec.start()
        self.lights.red = "on"
        t0 = time.monotonic()
        log.info("PTT down, recording")
        limit = config.SHUTDOWN_HOLD_S if config.SHUTDOWN_HOLD_S > 0 else config.MAX_RECORD_S
        if not self.button.wait_release(timeout=limit):
            self.rec.stop()
            if config.SHUTDOWN_HOLD_S > 0:
                self.shutdown()
                return False
            log.warning("Held > %.0fs, cutting recording", limit)
        samples = self.rec.stop()
        t_release = time.monotonic()
        self.play("beep_end")
        held = t_release - t0
        peak, rms = audio.level_dbfs(samples)
        log.info("PTT up: %.2fs held, peak %.1f dBFS, rms %.1f dBFS", held, peak, rms)

        if held < config.MIN_HOLD_S:
            log.info("Too short, ignored")
            return False

        self.lights.red = "fast"
        x = audio.resample(samples.astype(np.float32) / 32768.0, self.rec.rate, config.STT_RATE)
        wav = audio.to_wav_bytes(x, config.STT_RATE)
        q, cancel = queue.Queue(), threading.Event()
        self.pool.submit(process, wav, list(self.history), t_release, q, cancel)

        # Play sentences as they arrive; a new press cancels everything.
        speaking = finished = False
        while not (finished and not self.player.playing):
            if self.button.pressed():
                cancel.set()
                self.player.stop()
                log.info("New press while %s; cancelling", "speaking" if speaking else "thinking")
                return True
            try:
                item = q.get(timeout=0.01)
            except queue.Empty:
                continue
            kind = item[0]
            if kind == "audio":
                if not speaking:
                    speaking = True
                    self.lights.red = "off"
                    self.player.play(*item[1:])
                else:
                    self.player.enqueue(*item[1:])
            elif kind == "answer":
                self.history.append(item[1:])
            elif kind == "say_again":
                self.lights.red = "off"
                self.play("say_again")
            elif kind == "error":
                cancel.set()
                raise item[1]
            elif kind == "done":
                finished = True
        return False

    def run(self):
        end = time.monotonic() + 8  # give the first network check a moment
        while self.online is None and time.monotonic() < end:
            time.sleep(0.1)
        self.ready = True
        self.lights.green = "on" if self.online else "slow"
        self.announce_network()
        log.info("Ready. Hold the button to talk.")
        again = False
        while True:
            if not again:
                while not self.button.wait_press(timeout=0.5):
                    self.check_streams()
                    if not self.player.playing:
                        self.announce_network()
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
            finally:
                self.lights.red = "off"


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    for noisy in ("httpx", "httpx2", "httpcore", "openai", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # systemd stop -> run finally
    if not config.GROQ_API_KEY:
        log.warning("GROQ_API_KEY missing in .env; every request will fail")
    lights = StatusLights()
    lights.green = "fast"  # starting up
    try:
        Astronaut(lights).run()
    finally:
        lights.close()


if __name__ == "__main__":
    main()
