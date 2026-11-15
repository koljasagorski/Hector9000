#!/usr/bin/env bash
#
# Runs INSIDE the privileged container of qemu-test.sh (image hector-qemu). Boots a COPY of the
# image three times in QEMU (Raspberry Pi 3B, the image's own Raspberry Pi kernel and initramfs)
# and lets qemu-guest.sh check things from the inside. The card is made bigger than the image
# (default 8 GiB), like a real card that is larger than the 4.6 GB image.
set -u

SRC="${SRC:-/work/out/hector9000-test.img}"
Q=/work/qemu
CARD_GIB="${CARD_GIB:-8}"
PHASE_TIMEOUT="${PHASE_TIMEOUT:-480}"
W=/mnt/q
TOTAL_FAILS=0

[ -f "$SRC" ] || { echo "no image at $SRC (path inside the volume)" >&2; exit 2; }
for i in $(seq 0 63); do [ -e "/dev/loop$i" ] || mknod "/dev/loop$i" b 7 "$i" 2>/dev/null || true; done
mkdir -p "$W"

LOOP=""
detach() { set +e; umount "$W" 2>/dev/null; [ -n "$LOOP" ] && losetup -d "$LOOP" 2>/dev/null; LOOP=""; set -e; }
trap 'detach' EXIT
part_range() { sfdisk -d "$Q/card.img" | sed -n "s/^[^ ]*$1 *: *start= *\\([0-9]*\\), *size= *\\([0-9]*\\),.*/\\1 \\2/p" | head -1; }
mount_part() { # mount_part N ro|rw
    local s n; read -r s n < <(part_range "$1")
    LOOP="$(losetup -f --show -o $((s * 512)) --sizelimit $((n * 512)) "$Q/card.img")"
    mount -o "$2" "$LOOP" "$W"
}

echo "==> Card: copy of the image, ${CARD_GIB} GiB"
rm -rf "$Q"; mkdir -p "$Q"
cp --sparse=always "$SRC" "$Q/card.img"
qemu-img resize -f raw "$Q/card.img" "${CARD_GIB}G" >/dev/null

mount_part 1 ro
cp "$W/kernel8.img" "$W/initramfs8" "$W/bcm2710-rpi-3-b.dtb" "$Q/"
detach
echo "    kernel $(stat -c %s "$Q/kernel8.img") bytes, initramfs $(stat -c %s "$Q/initramfs8") bytes"

echo "==> Test service into the COPY (never into the delivered image)"
mount_part 2 rw
install -m 0755 /work/qemu-tools/qemu-guest.sh "$W/usr/local/sbin/qemu-guest.sh"
cat > "$W/etc/systemd/system/qemu-test.service" <<'UNIT'
[Unit]
Description=Hector9000 QEMU test run
After=multi-user.target mosquitto.service hector-stats.service hector-firstboot.service
[Service]
Type=simple
ExecStart=/usr/local/sbin/qemu-guest.sh
StandardOutput=journal+console
StandardError=journal+console
[Install]
WantedBy=multi-user.target
UNIT
ln -sf /etc/systemd/system/qemu-test.service "$W/etc/systemd/system/multi-user.target.wants/qemu-test.service"
sync; detach

echo "==> custom.toml (as the Raspberry Pi Imager writes it) onto the boot partition of the COPY"
mount_part 1 rw
HASH="$(openssl passwd -6 -salt qemutest 'only-for-this-test')"     # a throw-away password for the test card
cat > "$W/custom.toml" <<TOML
config_version = 1

[system]
hostname = "hector-qemu"

[user]
name = "qemutester"
password = "$HASH"
password_encrypted = true
TOML
sync; detach

run_phase() { # run_phase N
    local n="$1" cmdline log pid t0 elapsed
    mount_part 1 ro
    # what the Raspberry Pi firmware would do: take cmdline.txt of the boot partition (as it is NOW)
    cmdline="$(cat "$W/cmdline.txt")"
    detach
    # only the console differs: in QEMU the PL011 UART is ttyAMA1; no splash/quiet
    cmdline="$(echo "$cmdline" | sed -E 's/console=serial0,115200//; s/console=tty1//; s/ quiet//')"
    cmdline="console=ttyAMA1,115200 earlycon=pl011,0x3f201000 loglevel=4 $cmdline"
    log="$Q/serial-$n.log"
    echo "==> Phase $n: boot with: $cmdline"
    qemu-system-aarch64 -M raspi3b -m 1G -smp 4 \
        -kernel "$Q/kernel8.img" -initrd "$Q/initramfs8" -dtb "$Q/bcm2710-rpi-3-b.dtb" \
        -drive "file=$Q/card.img,format=raw,if=sd" -append "$cmdline" \
        -display none -serial "file:$log" -monitor none &
    pid=$!
    t0=$SECONDS
    while kill -0 "$pid" 2>/dev/null; do
        grep -q 'QEMUTEST-PHASE-DONE' "$log" 2>/dev/null && break
        grep -q 'Kernel panic' "$log" 2>/dev/null && { echo "    KERNEL PANIC"; break; }
        elapsed=$((SECONDS - t0))
        [ "$elapsed" -gt "$PHASE_TIMEOUT" ] && { echo "    TIMEOUT after ${elapsed}s"; break; }
        sleep 3
    done
    kill -TERM "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
    echo "    took $((SECONDS - t0)) s"
    sed 's/\x1b\[[0-9;]*[A-Za-z]//g; s/\r//g' "$log" | grep -a 'QEMUTEST' | grep -av 'QEMUTEST-PHASE-DONE' | sed 's/^[^Q]*//'
    if ! grep -aq 'QEMUTEST-PHASE-DONE' "$log"; then
        echo "    FAIL  phase $n did not finish; last lines of the serial log:"
        sed 's/\x1b\[[0-9;]*[A-Za-z]//g; s/\r//g' "$log" | tail -15 | sed 's/^/        /'
        return 1
    fi
    local f; f="$(grep -a 'QEMUTEST-PHASE-DONE' "$log" | tail -1 | awk '{print $3}')"
    TOTAL_FAILS=$((TOTAL_FAILS + ${f:-1}))
    return 0
}

for n in 1 2 3; do
    run_phase "$n" || { TOTAL_FAILS=$((TOTAL_FAILS + 1)); break; }
done

echo
if [ "$TOTAL_FAILS" -eq 0 ]; then echo "QEMU test: all phases passed"; else echo "QEMU test: $TOTAL_FAILS failed"; fi
[ "$TOTAL_FAILS" -eq 0 ]
