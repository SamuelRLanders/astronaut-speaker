"""Push-to-talk button on GPIO 17 (pin 11) to GND (pin 9), internal pull-up."""
import time

from gpiozero import Button

import config

STABLE_S = 0.03  # must read the same for this long to count (extra debounce)


class PTT:
    def __init__(self, gpio=config.BUTTON_GPIO):
        self._b = Button(gpio, pull_up=True, bounce_time=0.05)

    def _stable(self, want):
        end = time.monotonic() + STABLE_S
        while time.monotonic() < end:
            if self._b.is_pressed != want:
                return False
            time.sleep(0.005)
        return True

    def pressed(self):
        """True if the button is held down (debounced). Cheap when not pressed."""
        return self._b.is_pressed and self._stable(True)

    def released(self):
        return (not self._b.is_pressed) and self._stable(False)

    def wait_press(self, timeout=None):
        """Block until pressed. Returns False on timeout."""
        end = None if timeout is None else time.monotonic() + timeout
        while not self.pressed():
            if end is not None and time.monotonic() >= end:
                return False
            time.sleep(0.01)
        return True

    def wait_release(self, timeout=None):
        """Block until released. Returns False on timeout (still held)."""
        end = None if timeout is None else time.monotonic() + timeout
        while not self.released():
            if end is not None and time.monotonic() >= end:
                return False
            time.sleep(0.01)
        return True
