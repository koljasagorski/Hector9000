#!/usr/bin/env bash
#
# Builds the Hector9000 disk image from the official Raspberry Pi OS Lite image.
#
# Runs as root on arm64 Linux with loop devices - normally inside the container from
# Dockerfile.builder, started by build-in-docker.sh. See deploy/image/README.md.
#
#   build-image.sh --base base.img --out hector9000.img \
#                  --backend SRC --webui SRC [options]
#
# What happens (nothing of it needs a Raspberry Pi):
#   1. copy the base image, lay out the partitions:
#        p1  boot   FAT   unchanged (512 MiB)
#        p2  system ext4  fixed size (--root-mib, default 3584)
#        p3  data   ext4  label HECTORDATA, small (--data-mib, default 512); it grows to the end
#                         of the card at the first boot (hector-firstboot)
#   2. mount everything below one folder and chroot into it
#   3. clone the two repositories to /opt/hector9000/{backend,webui} and run
#        deploy/provision.sh --image   (packages, user, venv, units, mosquitto, sudoers)
#   4. install the package overlayroot (read-only root is OFF by default, hector-overlay on)
#   5. slim down, remove everything that must not be baked into a shared image (SSH host keys,
#      machine-id, logs, caches, shell history), set fstab/cmdline.txt for the 3 partitions
#   6. check the file system, trim, write <out>.sha256
#
# No password, user name, Wi-Fi or key is put into the image. The Raspberry Pi Imager adds
# those (OS customisation); the admin password of the dashboard is made at the first start.
#
set -euo pipefail

BASE="" OUT=""
BACKEND_SRC="" BACKEND_BRANCH="feature/congress-webui"
BACKEND_ORIGIN="https://github.com/koljasagorski/Hector9000.git"
WEBUI_SRC="" WEBUI_BRANCH="feature/congress-design"
WEBUI_ORIGIN="https://github.com/koljasagorski/Hector9000WebUI.git"
ROOT_MIB=3584
DATA_MIB=512
HOSTNAME_DEFAULT="hector9000"
KEEP_BUILD_TOOLS=0
MNT="/mnt/hector-image"

log() { printf '\033[1m==>\033[0m %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die() { printf '\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

while [ "$#" -gt 0 ]; do
    case "$1" in
        --base) BASE="$2"; shift ;;
        --out) OUT="$2"; shift ;;
        --backend) BACKEND_SRC="$2"; shift ;;
        --backend-branch) BACKEND_BRANCH="$2"; shift ;;
        --backend-origin) BACKEND_ORIGIN="$2"; shift ;;
        --webui) WEBUI_SRC="$2"; shift ;;
        --webui-branch) WEBUI_BRANCH="$2"; shift ;;
        --webui-origin) WEBUI_ORIGIN="$2"; shift ;;
        --root-mib) ROOT_MIB="$2"; shift ;;
        --data-mib) DATA_MIB="$2"; shift ;;
        --hostname) HOSTNAME_DEFAULT="$2"; shift ;;
        --keep-build-tools) KEEP_BUILD_TOOLS=1 ;;
        -h|--help) sed -n '3,/^set -euo/p' "$0" | sed -e '$d' -e 's/^# \{0,1\}//'; exit 0 ;;
        *) die "unknown option: $1" ;;
    esac
    shift
done
[ -f "$BASE" ] || die "--base IMAGE missing or not a file"
[ -n "$OUT" ] || die "--out IMAGE missing"
[ -n "$BACKEND_SRC" ] && [ -n "$WEBUI_SRC" ] || die "--backend and --webui (git bundle, directory or URL) are required"
[ "$(id -u)" -eq 0 ] || die "run as root"
[ "$(uname -m)" = "aarch64" ] || die "needs an arm64 host (this is $(uname -m)); the image is arm64"
for tool in losetup sfdisk e2fsck resize2fs mkfs.ext4 mount git chroot fstrim; do
    command -v "$tool" >/dev/null || die "missing tool: $tool"
done
case "$ROOT_MIB$DATA_MIB" in *[!0-9]*) die "--root-mib/--data-mib must be numbers" ;; esac

LOOPS=()
cleanup() {
    set +e
    if mountpoint -q "$MNT" 2>/dev/null || [ -d "$MNT/proc" ]; then
        umount -R -l "$MNT" 2>/dev/null
    fi
    for l in "${LOOPS[@]:-}"; do [ -n "$l" ] && losetup -d "$l" 2>/dev/null; done
}
trap cleanup EXIT

# ------------------------------------------------------------------ 1. partitions
log "Partition layout"
read -r P1_START P1_SIZE < <(sfdisk -d "$BASE" | sed -n 's/^[^ ]*1 *: *start= *\([0-9]*\), *size= *\([0-9]*\),.*/\1 \2/p' | head -1)
read -r P2_START _ < <(sfdisk -d "$BASE" | sed -n 's/^[^ ]*2 *: *start= *\([0-9]*\), *size= *\([0-9]*\),.*/\1 \2/p' | head -1)
[ -n "${P1_START:-}" ] && [ -n "${P2_START:-}" ] || die "could not read the partition table of $BASE"
DISK_ID="$(sfdisk --disk-id "$BASE")"          # kept: root=PARTUUID=<id>-02 in cmdline.txt/fstab stays valid
P2_SIZE=$((ROOT_MIB * 2048))
P3_START=$((P2_START + P2_SIZE))
P3_SIZE=$((DATA_MIB * 2048))
TOTAL=$((P3_START + P3_SIZE))
[ $((P3_START % 2048)) -eq 0 ] || die "data partition would not be 1 MiB aligned"
info "disk id ${DISK_ID}: p1 ${P1_START}+${P1_SIZE}, p2 ${P2_START}+${P2_SIZE} (${ROOT_MIB} MiB), p3 ${P3_START}+${P3_SIZE} (${DATA_MIB} MiB)"
info "image size: $((TOTAL / 2048)) MiB"

rm -f "$OUT"
cp --sparse=always "$BASE" "$OUT"
truncate -s $((TOTAL * 512)) "$OUT"
sfdisk --wipe never --wipe-partitions never --no-reread "$OUT" >/dev/null <<EOF
label: dos
label-id: ${DISK_ID}
unit: sectors

start=${P1_START}, size=${P1_SIZE}, type=c
start=${P2_START}, size=${P2_SIZE}, type=83
start=${P3_START}, size=${P3_SIZE}, type=83
EOF

attach() { local l; l="$(losetup -f --show -o $(($2 * 512)) --sizelimit $(($3 * 512)) "$1")"; LOOPS+=("$l"); echo "$l"; }
L1="$(attach "$OUT" "$P1_START" "$P1_SIZE")"
L2="$(attach "$OUT" "$P2_START" "$P2_SIZE")"
L3="$(attach "$OUT" "$P3_START" "$P3_SIZE")"

log "System partition: grow the file system to ${ROOT_MIB} MiB"
e2fsck -fy "$L2" >/dev/null || [ $? -le 1 ] || die "e2fsck of the system partition failed"
resize2fs "$L2" >/dev/null 2>&1 || die "resize2fs failed"

log "Data partition HECTORDATA"
# -m 0: no reserved blocks (nobody logs in as root there). orphan_file off: also readable by older tools.
mkfs.ext4 -q -F -L HECTORDATA -m 0 -O ^orphan_file "$L3" 2>/dev/null || mkfs.ext4 -q -F -L HECTORDATA -m 0 "$L3"

# ------------------------------------------------------------------ 2. mount
log "Mounting"
mkdir -p "$MNT"
mount "$L2" "$MNT"
mkdir -p "$MNT/boot/firmware" "$MNT/var/lib/hector9000" "$MNT/proc" "$MNT/sys" "$MNT/dev" "$MNT/run"
mount "$L1" "$MNT/boot/firmware"
mount "$L3" "$MNT/var/lib/hector9000"
mount -t proc proc "$MNT/proc"
mount --rbind /sys "$MNT/sys" && mount --make-rslave "$MNT/sys"
mount --rbind /dev "$MNT/dev" && mount --make-rslave "$MNT/dev"
mount -t tmpfs tmpfs "$MNT/run"

[ -f "$MNT/etc/os-release" ] && grep -q 'VERSION_CODENAME=bookworm' "$MNT/etc/os-release" \
    || die "the base image is not Debian 12 bookworm - deploy/provision.sh is only checked for that"

# network and no service starts inside the chroot
RESOLV_LINK=""
if [ -L "$MNT/etc/resolv.conf" ]; then RESOLV_LINK="$(readlink "$MNT/etc/resolv.conf")"; rm -f "$MNT/etc/resolv.conf"; fi
cp /etc/resolv.conf "$MNT/etc/resolv.conf"
printf '#!/bin/sh\nexit 101\n' > "$MNT/usr/sbin/policy-rc.d"
chmod 755 "$MNT/usr/sbin/policy-rc.d"

CH() {   # run a command in the image with a clean environment
    chroot "$MNT" /usr/bin/env -i HOME=/root LANG=C.UTF-8 TERM=dumb \
        PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
        DEBIAN_FRONTEND=noninteractive "$@"
}

# ------------------------------------------------------------------ 3. repositories + provisioning
log "Repositories"
mkdir -p "$MNT/opt/hector9000"
rm -rf "$MNT/opt/hector9000/backend" "$MNT/opt/hector9000/webui"
git clone --quiet --no-hardlinks -b "$BACKEND_BRANCH" "$BACKEND_SRC" "$MNT/opt/hector9000/backend"
git clone --quiet --no-hardlinks -b "$WEBUI_BRANCH" "$WEBUI_SRC" "$MNT/opt/hector9000/webui"
git -C "$MNT/opt/hector9000/backend" remote set-url origin "$BACKEND_ORIGIN"
git -C "$MNT/opt/hector9000/webui" remote set-url origin "$WEBUI_ORIGIN"
BACKEND_REV="$(git -C "$MNT/opt/hector9000/backend" rev-parse --short HEAD)"
WEBUI_REV="$(git -C "$MNT/opt/hector9000/webui" rev-parse --short HEAD)"
info "backend ${BACKEND_BRANCH} @ ${BACKEND_REV}, webui ${WEBUI_BRANCH} @ ${WEBUI_REV}"

log "provision.sh --image (in the chroot)"
CH /opt/hector9000/backend/deploy/provision.sh --image \
    --backend-dir /opt/hector9000/backend --webui-dir /opt/hector9000/webui

# ------------------------------------------------------------------ 4. overlayroot
log "overlayroot (the read-only root is off until 'hector-overlay on')"
CH apt-get install -y --no-install-recommends overlayroot

# ------------------------------------------------------------------ 5. slim down
log "Slimming down"
PURGE=(modemmanager triggerhappy dphys-swapfile man-db manpages)
[ "$KEEP_BUILD_TOOLS" = 1 ] || PURGE+=(build-essential libffi-dev python3-dev)
INSTALLED=()
for p in "${PURGE[@]}"; do
    if CH dpkg -s "$p" >/dev/null 2>&1; then INSTALLED+=("$p"); fi
done
if [ "${#INSTALLED[@]}" -gt 0 ]; then
    # simulate first: never let a purge pull out something the system needs
    REMOVALS="$(CH apt-get -s purge --autoremove "${INSTALLED[@]}" | sed -n -E 's/^(Remv|Purg) ([^ ]+).*/\2/p' | sed 's/:.*//')"
    [ -n "$REMOVALS" ] || die "could not read the list of packages that would be removed (apt output changed?)"
    for keep in systemd sudo network-manager openssh-server raspi-config raspberrypi-sys-mods mosquitto cage chromium \
                python3 python3-venv git linux-image-rpi-v8 raspi-firmware overlayroot; do
        if printf '%s\n' "$REMOVALS" | grep -qx "$keep"; then die "purging ${INSTALLED[*]} would remove '$keep' - adjust the list"; fi
    done
    info "removing: $(printf '%s' "$REMOVALS" | tr '\n' ' ')"
    CH apt-get purge -y --autoremove "${INSTALLED[@]}"
fi
CH apt-get clean
rm -rf "$MNT"/var/lib/apt/lists/* "$MNT"/var/cache/apt/* "$MNT"/var/cache/debconf/*-old "$MNT"/var/lib/dpkg/*-old \
       "$MNT"/usr/share/man/* "$MNT"/usr/share/info/* "$MNT"/root/.cache "$MNT"/root/.bash_history \
       "$MNT"/tmp/* "$MNT"/var/tmp/* 2>/dev/null || true
# documentation goes, the licence texts (copyright) stay
find "$MNT/usr/share/doc" -type f ! -name copyright -delete 2>/dev/null || true
find "$MNT/usr/share/doc" -depth -type d -empty -delete 2>/dev/null || true
find "$MNT/usr/share/locale" -mindepth 1 -maxdepth 1 ! -name 'de*' ! -name 'en*' ! -name 'locale.alias' -exec rm -rf {} + 2>/dev/null || true
# __pycache__ stays: the services run as users that may not write below /opt or /usr

log "Removing what must not be shared between cards"
rm -f "$MNT"/etc/ssh/ssh_host_* "$MNT"/home/*/.bash_history "$MNT"/home/*/.lesshst "$MNT"/home/*/.viminfo
: > "$MNT/etc/machine-id"
rm -f "$MNT/var/lib/dbus/machine-id"
find "$MNT/var/log" -type f -exec truncate -s 0 {} + 2>/dev/null || true
rm -rf "$MNT"/var/log/journal/* "$MNT"/var/lib/systemd/random-seed "$MNT"/var/lib/NetworkManager/*.lease \
       "$MNT"/var/lib/NetworkManager/*.state "$MNT"/etc/NetworkManager/system-connections/* 2>/dev/null || true

log "Host name, fstab, cmdline.txt"
echo "$HOSTNAME_DEFAULT" > "$MNT/etc/hostname"
if grep -q '^127\.0\.1\.1' "$MNT/etc/hosts"; then
    sed -i "s/^127\.0\.1\.1.*/127.0.1.1\t${HOSTNAME_DEFAULT}/" "$MNT/etc/hosts"
else
    printf '127.0.1.1\t%s\n' "$HOSTNAME_DEFAULT" >> "$MNT/etc/hosts"
fi
# Data partition by PARTUUID (disk id + partition number), NOT by label: a USB stick that is
# labelled HECTORDATA must not be able to take its place. nosuid,nodev,noexec: nothing is run from
# there. nofail: a damaged data partition must not stop the boot; the hector services then refuse
# to start (RequiresMountsFor) and the maintenance menu shows up.
DATA_PARTUUID="${DISK_ID#0x}-03"
grep -q '/var/lib/hector9000' "$MNT/etc/fstab" || \
    printf 'PARTUUID=%s  /var/lib/hector9000  ext4  defaults,noatime,nosuid,nodev,noexec,nofail,x-systemd.device-timeout=15s  0  2\n' \
        "$DATA_PARTUUID" >> "$MNT/etc/fstab"
CMDLINE="$MNT/boot/firmware/cmdline.txt"
# The stock "init=.../firstboot" makes the initramfs grow the SYSTEM partition to the end of the
# card (it would collide with the data partition) and reboot once. hector-firstboot replaces it.
sed -i -E 's# ?init=/usr/lib/raspberrypi-sys-mods/firstboot##' "$CMDLINE"
tr -d '\n' < "$CMDLINE" > "$CMDLINE.tmp" && printf '\n' >> "$CMDLINE.tmp" && mv "$CMDLINE.tmp" "$CMDLINE"
info "cmdline.txt: $(cat "$CMDLINE")"
grep -q 'firstboot' "$CMDLINE" && die "cmdline.txt still contains 'firstboot' (the initramfs would resize the system partition)"

# ------------------------------------------------------------------ 6. version, undo chroot tweaks
mkdir -p "$MNT/etc/hector9000"
{
    echo "image_built: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "backend: ${BACKEND_BRANCH} ${BACKEND_REV}"
    echo "webui: ${WEBUI_BRANCH} ${WEBUI_REV}"
    echo "base: $(grep PRETTY_NAME "$MNT/etc/os-release" | cut -d= -f2- | tr -d '"')"
} > "$MNT/etc/hector9000/image-version"
rm -f "$MNT/usr/sbin/policy-rc.d" "$MNT/etc/resolv.conf"
[ -z "$RESOLV_LINK" ] || ln -s "$RESOLV_LINK" "$MNT/etc/resolv.conf"
sync

log "Trim and unmount"
fstrim "$MNT" >/dev/null 2>&1 || true
fstrim "$MNT/var/lib/hector9000" >/dev/null 2>&1 || true
fstrim "$MNT/boot/firmware" >/dev/null 2>&1 || true
USED="$(df -h --output=used,size "$MNT" | tail -1 | xargs)"
info "system partition used/size: ${USED}"
umount -R "$MNT"
for l in "${LOOPS[@]}"; do losetup -d "$l"; done
LOOPS=()

log "File system checks"
L2="$(attach "$OUT" "$P2_START" "$P2_SIZE")"; L3="$(attach "$OUT" "$P3_START" "$P3_SIZE")"
e2fsck -fn "$L2" >/dev/null && info "system partition: clean" || die "e2fsck found errors on the system partition"
e2fsck -fn "$L3" >/dev/null && info "data partition: clean" || die "e2fsck found errors on the data partition"
for l in "${LOOPS[@]}"; do losetup -d "$l"; done
LOOPS=()
( cd "$(dirname "$OUT")" && sha256sum "$(basename "$OUT")" > "$(basename "$OUT").sha256" )
log "Done: $OUT ($(du -h "$OUT" | cut -f1) on disk, $((TOTAL / 2048)) MiB)"
