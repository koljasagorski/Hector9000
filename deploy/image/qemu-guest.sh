#!/bin/bash
#
# Runs INSIDE the emulated Raspberry Pi (as a systemd service that qemu-driver.sh puts into a COPY of
# the image). Three phases, remembered in /var/lib/hector9000/qemu-phase on the data partition:
#   1  first boot of a card that is bigger than the image: data partition grown? mounts? services?
#      then switch the read-only root on and reboot
#   2  read-only root running: / is an overlay, the data partition is still a normal writable mount
#      and keeps its files; then switch the read-only root off and reboot
#   3  normal again: changes in / made during the overlay are gone, data files are still there
# Every line starts with QEMUTEST so the driver can pick them out of the serial log.
PHASE_FILE=/var/lib/hector9000/qemu-phase
DATA=/var/lib/hector9000
phase="$(cat "$PHASE_FILE" 2>/dev/null || echo 1)"
FAILS=0
say()  { echo "QEMUTEST[$phase] $*"; }
ok()   { say "ok    $*"; }
fail() { say "FAIL  $*"; FAILS=$((FAILS + 1)); }
info() { say "      $*"; }
check() { local what="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$what"; else fail "$what"; fi; }

say "---- phase $phase, kernel $(uname -r), $(date -u +%T)"
# wait until the services are up (the emulated Pi is slow)
for _ in $(seq 1 100); do
    systemctl is-active --quiet mosquitto && systemctl is-active --quiet hector-stats && break
    sleep 3
done
sleep 5

fstype_root="$(findmnt -no FSTYPE /)"
data_src="$(findmnt -no SOURCE "$DATA")"
data_opts="$(findmnt -no OPTIONS "$DATA")"

case "$phase" in
1)
    info "cmdline: $(cat /proc/cmdline)"
    [ "$fstype_root" = ext4 ] && ok "/ is ext4 ($(findmnt -no SOURCE /))" || fail "/ is $fstype_root"
    [ "$(findmnt -no SOURCE /boot/firmware)" = /dev/mmcblk0p1 ] && ok "/boot/firmware is mmcblk0p1 (mounted by PARTUUID)" || fail "/boot/firmware: $(findmnt -no SOURCE /boot/firmware)"
    [ "$data_src" = /dev/mmcblk0p3 ] && ok "$DATA is mmcblk0p3 (mounted by PARTUUID)" || fail "$DATA is '$data_src'"
    for o in rw nosuid nodev noexec noatime; do echo ",$data_opts," | grep -q ",$o," && ok "data mount has $o" || fail "data mount lacks $o ($data_opts)"; done
    # did the data partition grow to the end of the 8 GiB card?
    disk=$(blockdev --getsz /dev/mmcblk0)
    start=$(cat /sys/class/block/mmcblk0p3/start); size=$(cat /sys/class/block/mmcblk0p3/size)
    info "card: $((disk / 2048)) MiB, p3 start $start size $size (end $((start + size)))"
    [ $((disk - start - size)) -lt 2048 ] && ok "the data partition reaches the end of the card" || fail "data partition does not reach the end of the card ($((disk - start - size)) sectors free)"
    avail_mb=$(df -BM --output=size "$DATA" | tail -1 | tr -dc 0-9)
    [ "$avail_mb" -gt 3500 ] && ok "the data file system is ${avail_mb} MiB (grown by resize2fs)" || fail "the data file system is only ${avail_mb} MiB"
    p2_end=$(( $(cat /sys/class/block/mmcblk0p2/start) + $(cat /sys/class/block/mmcblk0p2/size) ))
    [ "$p2_end" -le "$start" ] && ok "the system partition was not touched (ends at sector $p2_end)" || fail "system partition overlaps the data partition"
    [ -s /etc/hector9000/firstboot.done ] && ok "hector-firstboot finished (flag file)" || fail "no firstboot.done"
    journalctl -u hector-firstboot -o cat --no-pager | sed 's/^/        /'
    [ ! -e /etc/init.d/resize2fs_once ] && ok "no resize2fs_once" || fail "resize2fs_once present"
    # the Imager customisation (custom.toml on the boot partition) applied by hector-firstboot
    [ ! -e /boot/firmware/custom.toml ] && ok "custom.toml was applied and deleted (it may hold a password hash)" || fail "custom.toml is still on the boot partition"
    [ "$(id -un 1000 2>/dev/null)" = qemutester ] && ok "the placeholder user pi became qemutester (UID 1000)" || fail "UID 1000 is '$(id -un 1000 2>/dev/null)'"
    ! id pi >/dev/null 2>&1 && ok "no user pi any more" || fail "user pi still exists"
    [ "$(passwd -S qemutester 2>/dev/null | awk '{print $2}')" = P ] && ok "qemutester has a password" || fail "qemutester has no usable password: $(passwd -S qemutester 2>&1)"
    [ "$(hostname)" = hector-qemu ] && ok "hostname from custom.toml" || fail "hostname is '$(hostname)'"
    # the stock rename dialog (VT8) must not have blocked the boot; tty1 belongs to the kiosk
    ust="$(systemctl is-active userconfig 2>/dev/null)"
    [ "$ust" != active ] && [ "$ust" != activating ] && ok "userconfig (rename dialog) is $ust" || fail "userconfig is $ust"
    [ "$(systemctl is-system-running 2>/dev/null)" != starting ] && ok "boot finished ($(systemctl is-system-running 2>/dev/null))" || fail "the boot never finished (systemd is still 'starting')"
    gst="$(systemctl is-enabled getty@tty1 2>/dev/null)"; gac="$(systemctl is-active getty@tty1 2>/dev/null)"
    [ "$gst" != enabled ] && ok "getty@tty1 is not enabled ($gst)" || fail "getty@tty1 is enabled: it would fight the kiosk for tty1 at every boot"
    [ "$gac" != active ] && ok "getty@tty1 is not running ($gac)" || fail "getty@tty1 is running on the kiosk's tty"
    [ "$(systemctl show -p InactiveExitTimestampMonotonic --value hector-kiosk 2>/dev/null)" != 0 ] && ok "hector-kiosk was started (it fails here: no display)" || fail "hector-kiosk was never started"
    for u in mosquitto hector-controller hector-stats; do check "$u is active" systemctl is-active --quiet "$u"; done
    [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:8080/healthz)" = 200 ] && ok "dashboard /healthz 200" || fail "dashboard /healthz"
    ss -ltnH | awk '{print $4}' | grep -E ':(1883|9001)$' | grep -qv '^127.0.0.1:' && fail "MQTT listens on a non-local address" || ok "MQTT listens on 127.0.0.1 only"
    info "failed units (expected without hardware: server/led/kiosk, rpi-eeprom-update): $(systemctl --failed --no-legend --plain | awk '{print $1}' | tr '\n' ' ')"
    ! dmesg | grep -q -E "EXT4-fs error|I/O error" && ok "no file system or I/O errors in dmesg" || fail "file system errors in dmesg: $(dmesg | grep -E 'EXT4-fs error|I/O error' | head -3)"
    # the read-only root
    info "hector-overlay status: $(hector-overlay status 2>&1 | tr '\n' '|')"
    out="$(hector-overlay on 2>&1)"; rc=$?
    [ $rc -eq 0 ] && ok "hector-overlay on" || fail "hector-overlay on rc=$rc: $(echo "$out" | tail -3 | tr '\n' '|')"
    grep -q 'overlayroot=tmpfs:recurse=0' /boot/firmware/cmdline.txt && ok "cmdline.txt has overlayroot=tmpfs:recurse=0" || fail "cmdline.txt lacks overlayroot"
    [ "$(wc -l < /boot/firmware/cmdline.txt)" = 1 ] && ok "cmdline.txt is still one line" || fail "cmdline.txt is not one line"
    touch "$DATA/hector/persist-test" && ok "wrote a file to the data partition"
    echo 2 > "$PHASE_FILE"
    ;;
2)
    info "cmdline: $(cat /proc/cmdline)"
    [ "$fstype_root" = overlay ] && ok "/ is an overlay" || fail "/ is $fstype_root"
    [ "$data_src" = /dev/mmcblk0p3 ] && ok "$DATA is still its own mount (mmcblk0p3), not overlaid" || fail "$DATA is '$data_src'"
    echo ",$data_opts," | grep -q ",rw," && ok "the data partition is writable" || fail "the data partition is not writable ($data_opts)"
    [ -e "$DATA/hector/persist-test" ] && ok "the file from phase 1 is on the data partition" || fail "persist-test is gone"
    touch /etc/lost-after-reboot && ok "writing to / works (lands in RAM)" || fail "cannot write to /"
    touch "$DATA/hector/kept-after-reboot" && ok "writing to the data partition works"
    mount | grep -q 'overlayroot\|upperdir' && ok "overlay mount present: $(mount | grep -o 'upperdir=[^,)]*' | head -1)" || info "(no upperdir in mount output)"
    for u in mosquitto hector-controller hector-stats; do check "$u is active" systemctl is-active --quiet "$u"; done
    [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:8080/healthz)" = 200 ] && ok "dashboard /healthz 200 with the read-only root" || fail "dashboard /healthz"
    [ -s "$DATA/stats/admin_password" ] && ok "the admin password lives on the data partition" || fail "no admin password on the data partition"
    info "hector-overlay status: $(hector-overlay status 2>&1 | tr '\n' '|')"
    out="$(hector-overlay off 2>&1)"; rc=$?
    [ $rc -eq 0 ] && ok "hector-overlay off" || fail "hector-overlay off rc=$rc: $(echo "$out" | tail -3 | tr '\n' '|')"
    ! grep -q 'overlayroot' /boot/firmware/cmdline.txt && ok "cmdline.txt has no overlayroot any more" || fail "cmdline.txt still has overlayroot"
    echo 3 > "$PHASE_FILE"
    ;;
3)
    info "cmdline: $(cat /proc/cmdline)"
    [ "$fstype_root" = ext4 ] && ok "/ is ext4 again" || fail "/ is $fstype_root"
    [ ! -e /etc/lost-after-reboot ] && ok "the file written to / during the overlay is gone" || fail "/etc/lost-after-reboot survived"
    [ -e "$DATA/hector/persist-test" ] && [ -e "$DATA/hector/kept-after-reboot" ] && ok "both files on the data partition survived" || fail "data files missing"
    ! grep -q overlayroot /proc/cmdline && ok "no overlayroot in the running cmdline" || fail "overlayroot still on the cmdline"
    for u in mosquitto hector-controller hector-stats; do check "$u is active" systemctl is-active --quiet "$u"; done
    echo 4 > "$PHASE_FILE"
    ;;
esac

sync
say "result of phase $phase: $FAILS failed"
echo "QEMUTEST-PHASE-DONE $phase $FAILS"
sleep 3600
