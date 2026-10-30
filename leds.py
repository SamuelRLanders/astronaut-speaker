"""Onboard LEDs as status lights: green = ACT, red = PWR.

Needs write access to /sys/class/leds/{ACT,PWR} (udev rule from install.sh).
Without it the LEDs are skipped with one warning; nothing else breaks.
"""
import logging
import threading
import time

log = logging.getLogger(__name__)

BLINK = {"fast": (0.1, 0.1), "slow": (0.5, 0.5)}  # (on_s, off_s)


class Led:
    def __init__(self, name):
        self.dir = f"/sys/class/leds/{name}"
        self.mode = "off"
        self._lit = None
        try:
            with open(f"{self.dir}/trigger") as f:
                self._orig_trigger = f.read().split("[")[1].split("]")[0]
            self._write("trigger", "none")
            self.ok = True
        except (OSError, IndexError) as e:
            log.warning("LED %s not available (%s); run sudo ./install.sh", name, e)
            self.ok = False

    def _write(self, attr, value):
        with open(f"{self.dir}/{attr}", "w") as f:
            f.write(value)

    def update(self, now):
        if not self.ok:
            return
        if self.mode in BLINK:
            on, off = BLINK[self.mode]
            lit = now % (on + off) < on
        else:
            lit = self.mode == "on"
        if lit != self._lit:
            try:
                self._write("brightness", "1" if lit else "0")
                self._lit = lit
            except OSError:
                self.ok = False

    def restore(self):
        if self.ok:
            try:
                self._write("trigger", self._orig_trigger)
            except OSError:
                pass


class StatusLights:
    """Set .green / .red to 'off', 'on', 'fast' or 'slow'."""

    def __init__(self):
        self._green, self._red = Led("ACT"), Led("PWR")
        self._stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    @property
    def green(self):
        return self._green.mode

    @green.setter
    def green(self, mode):
        self._green.mode = mode

    @property
    def red(self):
        return self._red.mode

    @red.setter
    def red(self, mode):
        self._red.mode = mode

    def _run(self):
        while not self._stop.is_set():
            now = time.monotonic()
            self._green.update(now)
            self._red.update(now)
            time.sleep(0.05)

    def close(self):
        """Hand the LEDs back to the kernel (SD activity / power)."""
        self._stop.set()
        time.sleep(0.1)
        self._green.restore()
        self._red.restore()
