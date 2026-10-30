"""One-time: generate sounds/ (Quindar beeps + fallback voice clips).

    python make_sounds.py              # beeps + voice clips (Piper, offline)
    python make_sounds.py --beeps-only

Voice clips are stored dry; main.py applies the radio effect at load time, so
changing RADIO_EFFECT/VOLUME doesn't need a regenerate. Commit sounds/ so the
Pi can still talk when the network is down.
"""
import sys

import audio
import config

BEEPS = {  # name: Hz. Real Quindar tones: 2525 Hz = start, 2475 Hz = end.
    "beep_start": 2525,
    "beep_end": 2475,
}
CLIPS = {
    "comms_online": "Houston here. Comms online, we read you loud and clear.",
    "say_again": "Say again, over.",
    "losing_signal": "Losing signal, Houston. Stand by.",
    "no_signal": "Warning. No signal. Check the hotspot.",
    "shutting_down": "Houston signing off. Shutting down.",
}
BEEP_RATE = 48000


def main():
    config.SOUNDS_DIR.mkdir(exist_ok=True)
    for name, hz in BEEPS.items():
        path = config.SOUNDS_DIR / f"{name}.wav"
        path.write_bytes(audio.to_wav_bytes(audio.tone(hz, 0.25, BEEP_RATE, volume=1.0), BEEP_RATE))
        print("wrote", path)
    if "--beeps-only" in sys.argv:
        return
    import tts
    for name, text in CLIPS.items():
        path = config.SOUNDS_DIR / f"{name}.wav"
        try:
            pcm, rate = tts.speak(text)
        except Exception as e:
            print(f"FAILED {name}: {e}")
            continue
        path.write_bytes(audio.to_wav_bytes(audio.pcm16_to_float(pcm), rate))
        print("wrote", path)


if __name__ == "__main__":
    main()
