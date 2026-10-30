#!/bin/sh
# One-time root setup for the astronaut. Run from the repo: sudo ./install.sh
set -e
cd "$(dirname "$0")"
USER_NAME=landers2

# 1. Let the gpio group drive the onboard LEDs (status lights)
cat > /etc/udev/rules.d/99-astronaut-leds.rules <<'RULE'
SUBSYSTEM=="leds", KERNEL=="ACT|PWR", ACTION=="add", RUN+="/bin/sh -c 'chgrp gpio /sys%p/brightness /sys%p/trigger && chmod g+w /sys%p/brightness /sys%p/trigger'"
RULE
udevadm control --reload
udevadm trigger --subsystem-match=leds --action=add

# 2. Let the long-press shutdown power off without a password (only this command)
echo "$USER_NAME ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff" > /etc/sudoers.d/astronaut-poweroff
chmod 440 /etc/sudoers.d/astronaut-poweroff
visudo -cf /etc/sudoers.d/astronaut-poweroff

# 3. Start on boot
cp astronaut.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable astronaut
systemctl restart astronaut
echo "Installed. Logs: journalctl -u astronaut -f"
