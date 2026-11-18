#!/bin/bash
#
# One-time diagnosis of a Hector9000 card that cannot be reached any other way (no keyboard, no
# screen access, journal is volatile). Started once as root via cmdline.txt
# (systemd.run=/boot/firmware/hector-diag/run.sh), like deploy/sd-update/run.sh.
#
# It changes no configuration: the first thing it does is restore the original cmdline.txt, then
# it waits until the normal boot has had time to run into its problems and writes what it sees to
# hector-diag/diag.txt on the boot partition (a FAT partition: readable on any computer).
#
# Prepare on a computer (the card's boot partition is mounted there):
#   mkdir hector-diag && cp diagnose.sh hector-diag/run.sh && chmod +x hector-diag/run.sh
#   cp cmdline.txt hector-diag/cmdline.txt.orig
#   append " systemd.run=/boot/firmware/hector-diag/run.sh" to the ONE line of cmdline.txt
# Then put the card into the Pi, power on, wait about 4 minutes, pull the power, read diag.txt.

BOOT=/boot/firmware
[ -f "$BOOT/cmdline.txt" ] || BOOT=/boot
DIR="$BOOT/hector-diag"
LOG="$DIR/diag.txt"
exec >>"$LOG" 2>&1

echo "=================================================================="
echo "hector-diag start: $(date) (uptime: $(cut -d' ' -f1 /proc/uptime) s)"

# 1. normal boots from now on, whatever happens below
if [ -f "$DIR/cmdline.txt.orig" ]; then
    cp "$DIR/cmdline.txt.orig" "$BOOT/cmdline.txt" && echo "cmdline.txt restored"
else
    sed -i 's| systemd.run=[^ ]*||; s| systemd.run_success_action=[^ ]*||; s| systemd.unit=[^ ]*||' "$BOOT/cmdline.txt"
    echo "cmdline.txt cleaned with sed (no backup found)"
fi
sync
if grep -q "systemd.run" "$BOOT/cmdline.txt"; then
    echo "FATAL: could not restore cmdline.txt, stopping (fix it on a computer)"
    sync
    exit 1
fi

section() { echo; echo "--- $* ---"; }

# 2. make sure the normal system is starting (if systemd.run replaced the default target, this starts it)
if ! systemctl is-active --quiet multi-user.target; then
    echo "multi-user.target is not active: starting it"
    timeout 120 systemctl start multi-user.target
    echo "start multi-user.target -> rc=$?"
fi

# 3. give the services time to start, fail and be restarted
while [ "$(cut -d. -f1 /proc/uptime)" -lt 150 ]; do sleep 5; done
echo "dump at $(date) (uptime: $(cut -d' ' -f1 /proc/uptime) s)"

section "system"
echo "model: $(tr -d '\0' < /proc/device-tree/model 2>/dev/null)"
cat /etc/hector9000/image-version 2>/dev/null
uname -a
cat /proc/cmdline
timedatectl 2>&1 | head -8
free -m
df -h / /boot/firmware /var/lib/hector9000 2>&1
vcgencmd get_throttled 2>&1
findmnt /var/lib/hector9000 2>&1

section "units: failed, and the state of ours"
systemctl --failed --no-pager --plain 2>&1
systemctl list-units --all --no-pager --plain 'hector*' mosquitto.service NetworkManager.service ssh.service 2>&1
systemctl is-active hector.target multi-user.target 2>&1

for u in hector-server hector-controller hector-stats hector-led hector-kiosk hector-firstboot mosquitto; do
    section "status $u"
    systemctl status "$u" --no-pager -l -n 25 2>&1 | head -45
    section "journal $u (last 60 lines)"
    journalctl -b -u "$u" --no-pager -o short-precise 2>&1 | tail -60
done

section "journal: warnings and errors of this boot (last 150)"
journalctl -b -p warning --no-pager -o short-precise 2>&1 | tail -150

section "data partition"
ls -la /var/lib/hector9000 /var/lib/hector9000/hector /var/lib/hector9000/stats 2>&1

section "network"
ip -brief addr 2>&1
nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device 2>&1
nmcli -f NAME,TYPE,DEVICE,STATE connection show 2>&1
iw dev wlan0 link 2>&1
ss -ltnp 2>&1

section "kernel messages (last 60)"
dmesg 2>&1 | tail -60

# 4. start the controller by hand for a few seconds: an import or start error shows up here, whatever
#    the unit did. (It creates h9k.db in the data folder as a normal start would. The unit's own copy keeps
#    running; the broker may kick one of the two clients off.)
section "controller started by hand (12 s, as the service user, same folder and path)"
timeout 12 runuser -u hector -- env PYTHONPATH=/opt/hector9000/backend PYTHONUNBUFFERED=1 \
    sh -c 'cd /var/lib/hector9000/hector && exec /opt/hector9000/venv/bin/python /opt/hector9000/backend/Hector9000/HectorController.py' 2>&1 | tail -40
echo "rc=${PIPESTATUS[0]}"

section "import check as the service user"
runuser -u hector -- env PYTHONPATH=/opt/hector9000/backend sh -c 'cd /var/lib/hector9000/hector && /opt/hector9000/venv/bin/python -c "
import sys
print(sys.version)
for m in (\"paho.mqtt.client\", \"webcolors\", \"Hector9000.conf.HectorConfig\", \"Hector9000.conf.database\", \"Hector9000.conf.drinks\", \"Hector9000.HectorRemote\", \"Hector9000.utils.HectorSystem\"):
    try:
        __import__(m); print(\"ok   \", m)
    except Exception as e:
        print(\"FAIL \", m, type(e).__name__, e)
"' 2>&1

echo
echo "hector-diag done: $(date)"
sync
exit 0
