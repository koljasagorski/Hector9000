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
# By default no password, user name, Wi-Fi or key is put into the image. The Raspberry Pi Imager
# adds those (OS customisation); the admin password of the dashboard is made at the first start.
#
# Optional --access-file FILE bakes a login and/or a Wi-Fi in (KEY=VALUE lines, see
# access.env.example): Wi-Fi that joins by itself (NetworkManager profile, autoconnect), and an
# SSH login (sshd enabled, user + password). That image CONTAINS credentials: keep the file out of
# the repository and never publish such an image. Without the option nothing of this happens.
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
ACCESS_FILE=""
WIFI_SSID="" WIFI_PSK="" WIFI_COUNTRY="DE" SSH_USER="" SSH_PASSWORD="" SSH_SUDO=1
HAS_WIFI=0 HAS_SSH=0
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
        --access-file) ACCESS_FILE="$2"; shift ;;
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

# ---- optional access preset (Wi-Fi + SSH login) -------------------------------------------------
load_access() {   # plain KEY=VALUE lines; the file is parsed, never executed
    local line key val
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line%$'\r'}"
        case "$line" in ''|'#'*) continue ;; esac
        case "$line" in *=*) ;; *) die "access file: a line without '=' (comments start with #)" ;; esac
        key="${line%%=*}"; val="${line#*=}"
        case "$val" in
            \"*\") val="${val#\"}"; val="${val%\"}" ;;
            \'*\') val="${val#\'}"; val="${val%\'}" ;;
        esac
        case "$key" in
            WIFI_SSID|WIFI_PSK|WIFI_COUNTRY|SSH_USER|SSH_PASSWORD|SSH_SUDO) printf -v "$key" '%s' "$val" ;;
            *) die "access file: unknown key '${key}' (allowed: WIFI_SSID WIFI_PSK WIFI_COUNTRY SSH_USER SSH_PASSWORD SSH_SUDO)" ;;
        esac
    done < "$ACCESS_FILE"
}
if [ -n "$ACCESS_FILE" ]; then
    [ -f "$ACCESS_FILE" ] || die "--access-file: $ACCESS_FILE is not a file"
    load_access
    if [ -n "$WIFI_SSID$WIFI_PSK" ]; then
        [ -n "$WIFI_SSID" ] && [ -n "$WIFI_PSK" ] || die "access file: WIFI_SSID and WIFI_PSK belong together"
        [ "${#WIFI_SSID}" -le 32 ] || die "access file: WIFI_SSID is longer than 32 characters"
        case "$WIFI_SSID$WIFI_PSK" in *$'\n'*|*$'\r'*) die "access file: no line breaks in WIFI_SSID/WIFI_PSK" ;; esac
        case "$WIFI_SSID" in *';'*) die "access file: WIFI_SSID must not contain ';' (NetworkManager keyfile)" ;; esac
        [ "${#WIFI_PSK}" -ge 8 ] && [ "${#WIFI_PSK}" -le 63 ] || die "access file: WIFI_PSK must have 8..63 characters (WPA-PSK)"
        [[ "$WIFI_COUNTRY" =~ ^[A-Z]{2}$ ]] || die "access file: WIFI_COUNTRY must be a two-letter code such as DE"
        HAS_WIFI=1
    fi
    if [ -n "$SSH_USER$SSH_PASSWORD" ]; then
        [ -n "$SSH_USER" ] && [ -n "$SSH_PASSWORD" ] || die "access file: SSH_USER and SSH_PASSWORD belong together"
        [[ "$SSH_USER" =~ ^[A-Za-z_][A-Za-z0-9_-]{0,31}$ ]] || die "access file: SSH_USER has characters that are not allowed in a login name"
        case "$SSH_USER" in root|pi|hector|hector-stats) die "access file: SSH_USER '$SSH_USER' is reserved" ;; esac
        case "$SSH_PASSWORD" in *$'\n'*|*$'\r'*) die "access file: no line breaks in SSH_PASSWORD" ;; esac
        case "$SSH_SUDO" in 0|1) ;; *) die "access file: SSH_SUDO must be 0 or 1" ;; esac
        HAS_SSH=1
    fi
    [ "$HAS_WIFI$HAS_SSH" != 00 ] || die "access file: no WIFI_*/SSH_* values in it"
fi

# In a container /dev is a snapshot: loop devices created later have no node. Make plenty.
for i in $(seq 0 63); do [ -e "/dev/loop$i" ] || mknod "/dev/loop$i" b 7 "$i" 2>/dev/null || true; done

LOOPS=()
cleanup() {
    set +e
    if mountpoint -q "$MNT" 2>/dev/null || [ -d "$MNT/proc" ]; then
        # not lazy first: a lazily unmounted file system keeps its loop device busy
        umount -R "$MNT" 2>/dev/null || umount -R -l "$MNT" 2>/dev/null
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
# The stock SysV script resize2fs_once would grow the system partition's file system once at the first
# boot. Here the system partition has its fixed size and the data partition is grown by hector-firstboot.
rm -f "$MNT/etc/init.d/resize2fs_once" "$MNT"/etc/rc?.d/*resize2fs_once
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

# ------------------------------------------------------------------ 5b. access preset (optional)
if [ "$HAS_WIFI" = 1 ] || [ "$HAS_SSH" = 1 ]; then
    log "Access preset: this image will CONTAIN credentials (never publish it)"
fi
if [ "$HAS_WIFI" = 1 ]; then
    info "Wi-Fi '${WIFI_SSID}' (country ${WIFI_COUNTRY}): NetworkManager profile with autoconnect"
    grep -q "^${WIFI_COUNTRY}[[:space:]]" "$MNT/usr/share/zoneinfo/iso3166.tab" || die "WIFI_COUNTRY ${WIFI_COUNTRY} is not an ISO 3166 code"
    # same as raspi-config do_wifi_country: the regulatory domain goes on the kernel command line
    sed -i -e 's/ *cfg80211\.ieee80211_regdom=[^ ]*//' -e "s/\(.*\)/\1 cfg80211.ieee80211_regdom=${WIFI_COUNTRY}/" "$CMDLINE"
    NMDIR="$MNT/etc/NetworkManager/system-connections"
    mkdir -p "$NMDIR"
    # NetworkManager ignores connection files that are not 0600 root:root
    ( umask 077
      cat > "$NMDIR/hector-wifi.nmconnection" <<NM
[connection]
id=${WIFI_SSID}
uuid=$(cat /proc/sys/kernel/random/uuid)
type=wifi
autoconnect=true

[wifi]
mode=infrastructure
ssid=${WIFI_SSID}
hidden=false

[wifi-security]
key-mgmt=wpa-psk
psk=${WIFI_PSK}

[ipv4]
method=auto

[ipv6]
addr-gen-mode=default
method=auto

[proxy]
NM
    )
    chown root:root "$NMDIR/hector-wifi.nmconnection"; chmod 600 "$NMDIR/hector-wifi.nmconnection"
    # insurance: the radio must not stay soft-blocked now that the country is set
    cat > "$MNT/etc/systemd/system/hector-wifi-unblock.service" <<'UNIT'
[Unit]
Description=Unblock the Wi-Fi radio (country is set on the kernel command line)
Before=NetworkManager.service
After=systemd-rfkill.service

[Service]
Type=oneshot
ExecStart=-/usr/sbin/rfkill unblock wifi

[Install]
WantedBy=multi-user.target
UNIT
    CH systemctl enable hector-wifi-unblock.service >/dev/null 2>&1 || die "could not enable hector-wifi-unblock.service"
fi
if [ "$HAS_SSH" = 1 ]; then
    info "SSH: sshd enabled, login '${SSH_USER}' (password login)"
    USER_GROUPS=""
    if [ "$SSH_SUDO" = 1 ]; then
        for g in sudo adm netdev; do CH getent group "$g" >/dev/null 2>&1 && USER_GROUPS="${USER_GROUPS:+$USER_GROUPS,}$g"; done
    fi
    # --badname: Debian's useradd refuses upper-case letters otherwise. UID: the next free one (never 1000,
    # that is the placeholder 'pi' which the Raspberry Pi Imager renames).
    CH useradd --badname --create-home --shell /bin/bash ${USER_GROUPS:+--groups "$USER_GROUPS"} "$SSH_USER" \
        || die "useradd ${SSH_USER} failed"
    # password on stdin (not on a command line); stored as a crypt hash in /etc/shadow
    printf '%s:%s\n' "$SSH_USER" "$SSH_PASSWORD" | CH chpasswd || die "chpasswd failed"
    [ "$(CH passwd -S "$SSH_USER" | awk '{print $2}')" = P ] || die "the password of ${SSH_USER} was not set"
    [ "$(CH id -u "$SSH_USER")" != 1000 ] || die "${SSH_USER} got UID 1000 (the Imager's placeholder)"
    mkdir -p "$MNT/etc/ssh/sshd_config.d"
    printf '# Hector9000 image: password login is intended here\nPasswordAuthentication yes\n' > "$MNT/etc/ssh/sshd_config.d/10-hector-access.conf"
    CH systemctl enable ssh >/dev/null 2>&1 || die "could not enable ssh"
    [ -L "$MNT/etc/systemd/system/multi-user.target.wants/ssh.service" ] || die "ssh.service is not enabled after 'systemctl enable ssh'"
    # host keys stay out of the image: regenerate_ssh_host_keys.service (stock, enabled) makes them at the first boot
    [ -L "$MNT/etc/systemd/system/multi-user.target.wants/regenerate_ssh_host_keys.service" ] \
        || die "regenerate_ssh_host_keys.service is not enabled: sshd would start without host keys"
    rm -f "$MNT"/etc/ssh/ssh_host_*
fi

# ------------------------------------------------------------------ 6. version, undo chroot tweaks
mkdir -p "$MNT/etc/hector9000"
{
    echo "image_built: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "backend: ${BACKEND_BRANCH} ${BACKEND_REV}"
    echo "webui: ${WEBUI_BRANCH} ${WEBUI_REV}"
    echo "base: $(grep PRETTY_NAME "$MNT/etc/os-release" | cut -d= -f2- | tr -d '"')"
    if [ "$HAS_WIFI$HAS_SSH" = 00 ]; then
        echo "access: none"
    else
        # which preset was baked in (names only, never a secret); check-image.sh/boot-check.sh read this
        echo "access: preset"
        [ "$HAS_WIFI" = 1 ] && { echo "wifi_ssid: ${WIFI_SSID}"; echo "wifi_country: ${WIFI_COUNTRY}"; }
        [ "$HAS_SSH" = 1 ] && { echo "ssh_user: ${SSH_USER}"; echo "ssh_sudo: $([ "$SSH_SUDO" = 1 ] && echo yes || echo no)"; }
    fi
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
[ "$HAS_WIFI$HAS_SSH" = 00 ] || printf '\033[33mNOTE:\033[0m this image contains the Wi-Fi/SSH credentials of the access file - do not publish it, do not upload it.\n'

