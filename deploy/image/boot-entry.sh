#!/usr/bin/env bash
#
# PID 1 of the container used by boot-test.sh. Builds a chroot of the image's root file system
# (already unpacked into /work/rootfs, data partition in /work/data) and starts the image's own
# systemd in it. Not meant to be run anywhere else. Runs inside a privileged container.
#
# What is different from a real card (and therefore NOT tested here):
#   * no partitions: /etc/fstab is replaced by a copy without the PARTUUID lines; the data
#     folder is a bind mount with the same options as the real data partition
#   * no Raspberry Pi hardware: NetworkManager, Wi-Fi, Bluetooth, the LED server and the kiosk
#     are masked, the hardware server is replaced by the simulator
#   * the clock of the machine running the container must never be set (CAP_SYS_TIME is removed below)
set -eu

R=/work/rootfs
N=/newroot
[ -d "$R/etc/systemd" ] && [ -d /work/data ] || { echo "boot-entry: run 'boot-test.sh prepare' first" >&2; exit 1; }

# every run starts from the pristine files: changes go to a fresh overlay and a fresh data copy
rm -rf /work/upper /work/ovlwork /work/data.run
mkdir -p "$N" /work/upper /work/ovlwork
cp -a /work/data /work/data.run
mount -t overlay overlay -o "lowerdir=$R,upperdir=/work/upper,workdir=/work/ovlwork" "$N"
mount --bind /work/data.run "$N/var/lib/hector9000"
# the options of the fstab line of the data partition
mount -o remount,bind,nosuid,nodev,noexec,noatime "$N/var/lib/hector9000"
mount -t proc proc "$N/proc"
mount --rbind /sys "$N/sys"
mount --rbind /dev "$N/dev"
mount -t tmpfs -o mode=755 tmpfs "$N/run"
mount -t tmpfs -o mode=1777 tmpfs "$N/tmp"

# fstab without the devices that do not exist in a container
grep -v '^PARTUUID' "$R/etc/fstab" > /work/fstab.test
mount --bind /work/fstab.test "$N/etc/fstab"

# What cannot work without the Raspberry Pi (or in a container): skipped for this run only.
# A drop-in under /run applies to units from /etc, /lib and the SysV generator alike
# (a symlink to /dev/null in /run would lose against a unit file in /etc).
skip() {
    local u d
    for u in "$@"; do
        d="$N/run/systemd/system/$u.service.d"
        mkdir -p "$d"
        printf '[Unit]\nConditionPathExists=/nonexistent/not-in-the-container\n' > "$d/skip.conf"
    done
}
skip NetworkManager NetworkManager-wait-online wpa_supplicant bluetooth hciuart avahi-daemon \
     rpi-eeprom-update sshswitch regenerate_ssh_host_keys hector-led hector-kiosk \
     resize2fs_once rpi-display-backlight dphys-swapfile systemd-timesyncd

# the hardware server cannot run without GPIO: same server, simulated hardware
mkdir -p "$N/run/systemd/system/hector-server.service.d"
cat > "$N/run/systemd/system/hector-server.service.d/simulated.conf" <<'EOF'
[Service]
ExecStart=
ExecStart=/opt/hector9000/venv/bin/python -c "import sys, runpy, types; import Hector9000.HectorSimulator as S; m = types.ModuleType('Hector9000.HectorHardware'); m.HectorHardware = S.HectorSimulator; sys.modules['Hector9000.HectorHardware'] = m; runpy.run_module('Hector9000.HectorServer', run_name='__main__')"
EOF

echo "boot-entry: starting the image's systemd"
# "docker run --privileged" ignores --cap-drop, so the bounding set is cut here: nothing in this
# system (not even root, not sudo) can ever get CAP_SYS_TIME, i.e. cannot set the clock of the
# machine that runs the container. boot-check.sh verifies this before it tests the clock command.
command -v setpriv >/dev/null || { echo "boot-entry: setpriv missing" >&2; exit 1; }
exec setpriv --bounding-set=-sys_time -- env container=docker chroot "$N" /lib/systemd/systemd --log-target=console --log-level=info
