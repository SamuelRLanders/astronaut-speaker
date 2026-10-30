"""Step-by-step hardware/API checks. Run each in order; Ctrl-C to quit.

    python checks.py devices   # list audio devices and which ones config picks
    python checks.py tone      # beeps through the amp
    python checks.py meter     # live mic level bar (tap mic, wiggle plugs, talk)
    python checks.py mic       # record 4 s, play it back through the amp
    python checks.py button    # print press/release
    python checks.py stt       # hold button, talk, print transcript
    python checks.py models    # list the Groq models your key can use
    python checks.py llm       # type text, print Mission Control reply (no audio)
    python checks.py tts       # type text, hear it through the amp
"""
import logging
import sys
import time

import numpy as np

import config

logging.basicConfig(level=logging.INFO, format="%(message)s")


def _player():
    import audio
    return audio.Player(audio.find_device(config.OUTPUT_DEVICE, "output"), config.OUTPUT_RATE)


def _recorder():
    import audio
    return audio.Recorder(audio.find_device(config.INPUT_DEVICE, "input"), config.INPUT_RATE)


def devices():
    import sounddevice as sd
    import audio
    print(sd.query_devices())
    for kind, spec in (("output", config.OUTPUT_DEVICE), ("input", config.INPUT_DEVICE)):
        try:
            i = audio.find_device(spec, kind)
            print(f"{kind}: {spec!r} -> #{i} {sd.query_devices(i)['name']}")
        except RuntimeError as e:
            print(e)


def tone():
    import audio
    p = _player()
    for hz in (440, 880, 2525, 2475):
        print(f"{hz} Hz")
        p.play_blocking(audio.tone(hz, 0.6, 48000, config.BEEP_VOLUME), 48000)
        time.sleep(0.2)


def meter():
    import sounddevice as sd
    import audio
    dev = audio.find_device(config.INPUT_DEVICE, "input")
    print("Silence should sit near -50 dBFS; talking into the mic pod should jump above -20.")
    with sd.InputStream(device=dev, samplerate=config.INPUT_RATE, channels=1, dtype="int16") as s:
        while True:
            x, _ = s.read(config.INPUT_RATE // 10)
            peak, _ = audio.level_dbfs(x[:, 0])
            n = max(0, min(40, int((peak + 60) / 1.5)))  # -60..0 dBFS -> 0..40 chars
            print(f"\r{peak:6.1f} dBFS |{'#' * n:<40}|", end="", flush=True)


def mic():
    import audio
    p, r = _player(), _recorder()
    print("Recording 4 s... talk now")
    r.start()
    time.sleep(4)
    x = r.stop()
    peak, rms = audio.level_dbfs(x)
    print(f"Peak {peak:.1f} dBFS, RMS {rms:.1f} dBFS "
          "(talking should peak above about -20; raise gain with alsamixer if not)")
    print("Playing back")
    x = x.astype(np.float32) / 32768
    gain = min(config.VOLUME / max(float(np.max(np.abs(x))), 1e-6), 10.0)  # max +20 dB, so noise stays quiet
    p.play_blocking(x * gain, r.rate)


def button():
    from button import PTT
    b = PTT()
    print(f"Press the button (GPIO {config.BUTTON_GPIO})")
    while True:
        b.wait_press()
        t = time.monotonic()
        print("pressed")
        b.wait_release()
        print(f"released after {time.monotonic() - t:.2f}s")


def stt():
    import audio
    import stt as stt_mod
    from button import PTT
    b, r = PTT(), _recorder()
    print("Hold the button and talk")
    while True:
        b.wait_press()
        r.start()
        b.wait_release()
        x = r.stop()
        x = audio.resample(x.astype(np.float32) / 32768, r.rate, config.STT_RATE)
        t = time.monotonic()
        text = stt_mod.transcribe(audio.to_wav_bytes(x, config.STT_RATE))
        print(f"[{time.monotonic() - t:.2f}s] {text!r}")


def models():
    import stt as stt_mod
    for m in sorted(stt_mod._client().models.list().data, key=lambda m: m.id):
        print(m.id)


def llm():
    import llm as llm_mod
    history = []
    while True:
        text = input("you> ").strip()
        if not text:
            continue
        t = time.monotonic()
        answer = llm_mod.reply(text, history[-config.HISTORY_TURNS:])
        history.append((text, answer))
        print(f"[{time.monotonic() - t:.2f}s] houston> {answer}")


def tts():
    import audio
    import tts as tts_mod
    p = _player()
    while True:
        text = input("say> ").strip()
        if not text:
            continue
        t = time.monotonic()
        pcm, rate = tts_mod.speak(text)
        print(f"[{time.monotonic() - t:.2f}s]")
        p.play_blocking(audio.voice_fx(audio.pcm16_to_float(pcm), rate,
                                       config.RADIO_EFFECT, config.VOLUME), rate)


if __name__ == "__main__":
    cmds = {f.__name__: f for f in (devices, tone, meter, mic, button, stt, models, llm, tts)}
    if len(sys.argv) != 2 or sys.argv[1] not in cmds:
        print(__doc__)
        sys.exit(1)
    try:
        cmds[sys.argv[1]]()
    except (KeyboardInterrupt, EOFError):
        pass
