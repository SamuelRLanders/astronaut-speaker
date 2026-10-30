# astronaut-speaker

Push-to-talk "Mission Control" for an astronaut costume. Hold the button, talk, release:
Raspberry Pi 4 records -> Groq Whisper transcribes -> Groq gpt-oss answers as Houston ->
Piper (local, offline) speaks it through a MAX98357A amp + speaker in the suit.
Costs nothing: Groq's free tier needs no card, and Piper runs on the Pi.

## Files

- `main.py` - the loop (run this / the service)
- `config.py` - every setting; any of them can be overridden in `.env`
- `audio.py` - always-open output/input streams, resampling, radio effect, WAV helpers
- `button.py` - debounced push-to-talk on GPIO 17
- `stt.py`, `llm.py` - Groq (OpenAI-compatible API); `tts.py` - Piper. Swap a provider by rewriting one file
- `make_sounds.py` - generates `sounds/` (Quindar beeps + offline fallback voice clips)
- `checks.py` - step-by-step hardware/API checks
- `astronaut.service` - systemd unit

## Wiring (Pi OFF while wiring)

    Amp VIN  -> pin 2  (5V)        Amp BCLK -> pin 12 (GPIO 18)
    Amp GND  -> pin 6  (GND)       Amp LRC  -> pin 35 (GPIO 19)
    Amp DIN  -> pin 40 (GPIO 21)   Amp SD, GAIN -> unconnected
    Button   -> pin 11 (GPIO 17) and pin 9 (GND)
    Mic: earbuds -> splitter -> pink plug -> USB sound card -> Pi USB

## Setup

    sudo apt update && sudo apt install -y libportaudio2 python3-lgpio
    # Enable the amp (and drop onboard audio), then reboot
    sudo sed -i 's/^dtparam=audio=on/dtparam=audio=off/' /boot/firmware/config.txt
    echo 'dtoverlay=max98357a' | sudo tee -a /boot/firmware/config.txt
    sudo reboot

    cd ~/astronaut-speaker
    python3 -m venv --system-site-packages .venv
    .venv/bin/pip install -r requirements.txt
    .venv/bin/python -m piper.download_voices --data-dir voices en_US-ryan-medium
    cp .env.example .env && nano .env      # paste the free key from https://console.groq.com/keys
    .venv/bin/python make_sounds.py        # offline; commit sounds/ afterwards

If `aplay -l` shows no MAX98357A card after reboot, replace `dtoverlay=max98357a` with
`dtoverlay=hifiberry-dac` in /boot/firmware/config.txt and reboot (config already looks
for both names).

## Checkpoints (run in order)

    source .venv/bin/activate
    aplay -l; arecord -l                         # note the amp's card number N
    speaker-test -c2 -t wav -D plughw:N,0        # 1. amp works (Ctrl-C)
    python checks.py devices   # both devices found?
    python checks.py tone      # 1. beeps from the speaker
    python checks.py meter     # 2. live mic level; should jump when you talk
    python checks.py mic       # 2. record 4 s, hear it back; check the dBFS levels
    python checks.py button    # 3. press/release printed
    python checks.py stt       # 4. hold, talk, transcript printed
    python checks.py llm       # 5. type, Houston replies in text
    python checks.py tts       # 6. type, hear it in the radio voice
    python main.py             # 7. full thing: beeps, replies, interrupt, error clips

Mic too quiet (peak below about -20 dBFS when talking): `alsamixer -c Device`, F4, raise Mic capture.
Too much hiss/noise: lower it. Settings persist with `sudo alsactl store`.

## Run on boot

    sudo cp astronaut.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now astronaut
    journalctl -u astronaut -f          # live logs, with per-stage latency

Stop it before running checks by hand (both want the audio devices):
`sudo systemctl stop astronaut`.

Then do 8.: unplug the laptop, power from the power bank, turn on the hotspot, boot,
wait for "comms online", talk.

Turn the hotspot on BEFORE booting the Pi: the unit waits for network-online, which
can add up to about a minute if no network is around. The program itself does not need
the network to start; only replies do. If the wait is annoying, delete the two
network-online lines from the unit.

Shut down with `sudo shutdown now`, wait for the green LED to stop, then unplug.

## Tuning (in .env, then `sudo systemctl restart astronaut`)

    VOLUME=0.9          peak voice level 0..1
    BEEP_VOLUME=0.35
    RADIO_EFFECT=false  plain voice instead of radio band-pass
    PIPER_VOICE=en_US-ryan-medium   any voice from https://huggingface.co/rhasspy/piper-voices
                        (download it first, see Setup; "medium" voices are a good speed/quality mix)
    PIPER_SPEED=1.1     faster speech
    MIN_HOLD_S=0.5      shorter presses ignored
    SYSTEM_PROMPT=...   Houston's personality

## Behaviour

- Press: 2525 Hz beep, recording. Release: 2475 Hz beep. Held < 0.5 s: ignored.
- Nothing heard: "Say again, over." Any error/timeout (15 s per call): "Losing signal".
- Pressing during thinking or during a reply cancels it and starts a new recording.
- Remembers the last 5 exchanges. Restarting the service clears the memory.
- If an audio device disappears, the process exits and systemd restarts it every 2 s.
- If the free tier's daily limit on openai/gpt-oss-120b runs out, replies fall back to
  openai/gpt-oss-20b automatically (logged as "rate-limited").
