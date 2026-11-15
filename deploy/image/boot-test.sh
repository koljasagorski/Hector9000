#!/usr/bin/env bash
#
# Starts the system of a finished Hector9000 image in a container and checks it (no Raspberry Pi
# and no QEMU needed). The image's own systemd boots in a chroot of its root file system; the
# hardware server is replaced by the simulator. See boot-entry.sh for what is different from a
# real card and therefore NOT tested here (partitions, fstab, kernel, initramfs, hardware, Wi-Fi).
#
#   boot-test.sh prepare [IMAGE]   unpack the image's partitions into the work volume
#   boot-test.sh up                start the container (boots in about a second)
#   boot-test.sh check             run boot-check.sh inside the running system
#   boot-test.sh logs              boot messages
#   boot-test.sh shell             root shell inside the running system
#   boot-test.sh down              remove the container
#   boot-test.sh all [IMAGE]       prepare, up, wait, check, down
#
# IMAGE is a path INSIDE the work volume (default /work/out/hector9000-test.img). The volume and
# the builder container are those of build-image.sh (hector-img-work, hector-image-builder).
# Needs Docker on an arm64 host (Apple silicon, colima is fine) and --privileged containers.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VOL="${HECTOR_IMG_VOLUME:-hector-img-work}"
BUILDER="${HECTOR_IMG_BUILDER:-hector-image-builder}"
NAME="${HECTOR_BOOT_NAME:-hector-boot}"
cmd="${1:-}"
IMG="${2:-/work/out/hector9000-test.img}"

prepare() {
    docker run --rm --privileged -e IMG="$IMG" -v "$VOL":/work "$BUILDER" bash -c '
set -eu
[ -f "$IMG" ] || { echo "no image at $IMG (path inside the volume)" >&2; exit 1; }
for i in $(seq 0 63); do [ -e /dev/loop$i ] || mknod /dev/loop$i b 7 $i 2>/dev/null || true; done
TABLE="$(sfdisk -d "$IMG")"
part() { echo "$TABLE" | sed -n "s/^[^ ]*$1 *: *start= *\\([0-9]*\\), *size= *\\([0-9]*\\),.*/\\1 \\2/p" | head -1; }
W=/mnt/x; mkdir -p $W; LOOPS=""
cleanup() { set +e; umount -R $W 2>/dev/null || umount -R -l $W 2>/dev/null; for l in $LOOPS; do losetup -d $l 2>/dev/null; done; }
trap cleanup EXIT
att() { read -r s n < <(part $1); l="$(losetup -f --show -r -o $((s*512)) --sizelimit $((n*512)) $IMG)"; LOOPS="$LOOPS $l"; echo $l; }
rm -rf /work/rootfs /work/data /work/upper /work/ovlwork /work/data.run; mkdir -p /work/rootfs /work/data
L2=$(att 2); L1=$(att 1); L3=$(att 3)
mount -o ro $L2 $W; mount -o ro $L1 $W/boot/firmware; mount -o ro,noload $L3 $W/var/lib/hector9000
tar -C $W/var/lib/hector9000 --numeric-owner -cf - . | tar -C /work/data --numeric-owner -xpf -
umount $W/var/lib/hector9000
tar -C $W --numeric-owner --xattrs -cf - . | tar -C /work/rootfs --numeric-owner --xattrs -xpf -
echo "unpacked: $(du -sx /work/rootfs | cut -f1) KiB root file system, data partition: $(ls -A /work/data | tr "\n" " ")"
'
}

up() {
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    docker run -dt --name "$NAME" --privileged --hostname hector9000 -v "$VOL":/work "$BUILDER" \
        bash -c "$(cat "$HERE/boot-entry.sh")" >/dev/null
    echo "container $NAME started"
}

wait_ready() {
    local i
    for i in $(seq 1 60); do
        if docker exec "$NAME" chroot /newroot systemctl is-system-running 2>/dev/null | grep -qE '^(running|degraded)$'; then return 0; fi
        sleep 1
    done
    echo "the system did not come up in 60 s - see: boot-test.sh logs" >&2
    return 1
}

check() {
    wait_ready
    docker exec -i "$NAME" chroot /newroot bash -s < "$HERE/boot-check.sh"
}

case "$cmd" in
    prepare) prepare ;;
    up) up ;;
    check) check ;;
    logs) docker logs "$NAME" 2>&1 | sed 's/\x1b\[[0-9;]*m//g' ;;
    shell) docker exec -it "$NAME" chroot /newroot bash ;;
    down) docker rm -f "$NAME" >/dev/null && echo "container $NAME removed" ;;
    all)
        prepare
        up
        rc=0; check || rc=$?
        docker rm -f "$NAME" >/dev/null 2>&1 || true
        exit $rc
        ;;
    *) sed -n '3,/^set -euo/p' "$0" | sed -e '$d' -e 's/^# \{0,1\}//'; exit 2 ;;
esac
