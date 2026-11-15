#!/usr/bin/env bash
#
# Checks a finished Hector9000 disk image WITHOUT booting it: partition table, boot files,
# fstab, enabled units, users, owners and modes, packages, repositories, and a scan for things
# that must never be in a shared image (keys, passwords, tokens, and an optional extra pattern).
#
#   check-image.sh IMAGE
#
# Runs as root on Linux with loop devices (the builder container, privileged). The image is
# only mounted read-only. Exit code 0 = all checks passed, 1 = at least one FAIL.
#
set -uo pipefail

IMG="${1:-}"
[ -f "$IMG" ] || { echo "usage: check-image.sh IMAGE" >&2; exit 2; }
[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 2; }

PASS=0 FAILS=0 WARNS=0
ok()   { PASS=$((PASS + 1)); printf '  \033[32mok\033[0m    %s\n' "$*"; }
fail() { FAILS=$((FAILS + 1)); printf '  \033[31mFAIL\033[0m  %s\n' "$*"; }
warn() { WARNS=$((WARNS + 1)); printf '  \033[33mwarn\033[0m  %s\n' "$*"; }
section() { printf '\n\033[1m%s\033[0m\n' "$*"; }
check() { local what="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$what"; else fail "$what"; fi; }

# In a container /dev is a snapshot: loop devices created later have no node. Make plenty.
for i in $(seq 0 63); do [ -e "/dev/loop$i" ] || mknod "/dev/loop$i" b 7 "$i" 2>/dev/null || true; done

W="/mnt/hector-check"
LOOPS=()
cleanup() { set +e; umount -R "$W" 2>/dev/null || umount -R -l "$W" 2>/dev/null; for l in "${LOOPS[@]:-}"; do [ -n "$l" ] && losetup -d "$l" 2>/dev/null; done; }
trap cleanup EXIT

attach() { local l; l="$(losetup -f --show -r -o $(($2 * 512)) --sizelimit $(($3 * 512)) "$1")"; LOOPS+=("$l"); echo "$l"; }

# ----------------------------------------------------------------- partition table
section "Partition table"
TABLE="$(sfdisk -d "$IMG")"
DISK_ID="$(sfdisk --disk-id "$IMG")"
part() { echo "$TABLE" | sed -n "s/^[^ ]*$1 *: *start= *\\([0-9]*\\), *size= *\\([0-9]*\\), *type=\\([0-9a-fA-F]*\\).*/\\1 \\2 \\3/p" | head -1; }
read -r S1 N1 T1 < <(part 1); read -r S2 N2 T2 < <(part 2); read -r S3 N3 T3 < <(part 3)
[ "$(echo "$TABLE" | grep -c '^/.*: start=')" = 3 ] && ok "three partitions" || fail "expected 3 partitions"
[ "${T1:-}" = c ] && ok "p1 is FAT32 (c)" || fail "p1 type ${T1:-?}"
[ "${T2:-}" = 83 ] && [ "${T3:-}" = 83 ] && ok "p2/p3 are Linux (83)" || fail "p2/p3 types ${T2:-?}/${T3:-?}"
[ "${S2:-0}" -ge $((${S1:-0} + ${N1:-0})) ] && [ "${S3:-0}" -ge $((${S2:-0} + ${N2:-0})) ] && ok "partitions in order, no overlap" || fail "partitions overlap or are out of order"
[ $((${S3:-1} % 2048)) -eq 0 ] && ok "data partition is 1 MiB aligned" || fail "data partition not aligned"
echo "$DISK_ID" | grep -qiE '^0x[0-9a-f]{8}$' && ok "disk id ${DISK_ID}" || fail "disk id ${DISK_ID}"
SIZE_SECTORS=$(($(stat -c %s "$IMG") / 512))
[ "$SIZE_SECTORS" -ge $((S3 + N3)) ] && ok "image file covers the last partition ($((SIZE_SECTORS / 2048)) MiB)" || fail "image file is shorter than the partition table"
[ $((SIZE_SECTORS - S3 - N3)) -lt 2048 ] && ok "no unused space behind the data partition" || warn "$(((SIZE_SECTORS - S3 - N3) / 2048)) MiB unused at the end"

L1="$(attach "$IMG" "$S1" "$N1")"; L2="$(attach "$IMG" "$S2" "$N2")"; L3="$(attach "$IMG" "$S3" "$N3")"
mkdir -p "$W"
mount -o ro "$L2" "$W" && mkdir -p "$W/boot/firmware" "$W/var/lib/hector9000" || { fail "cannot mount the system partition"; exit 1; }
mount -o ro "$L1" "$W/boot/firmware" || fail "cannot mount the boot partition"
mount -o ro,noload "$L3" "$W/var/lib/hector9000" 2>/dev/null || mount -o ro "$L3" "$W/var/lib/hector9000" || fail "cannot mount the data partition"

# ----------------------------------------------------------------- boot partition
section "Boot partition"
CMD="$W/boot/firmware/cmdline.txt"
[ "$(wc -l < "$CMD")" = 1 ] && ok "cmdline.txt is a single line" || fail "cmdline.txt must be one line"
! grep -q 'firstboot' "$CMD" && ok "no init=.../firstboot (the initramfs would resize the system partition)" || fail "cmdline.txt still has firstboot"
grep -q "root=PARTUUID=${DISK_ID#0x}-02" "$CMD" && ok "root=PARTUUID matches the disk id (-02)" || fail "root=PARTUUID does not match ${DISK_ID}-02: $(cat "$CMD")"
! grep -q 'overlayroot=' "$CMD" && ok "read-only root is off by default" || fail "overlayroot= is set in the delivered image"
grep -q 'fsck.repair=yes' "$CMD" && ok "fsck.repair=yes" || warn "fsck.repair=yes missing"
CFG="$W/boot/firmware/config.txt"
grep -qE '^dtparam=i2c_arm=on' "$CFG" && ok "I2C enabled in config.txt" || fail "dtparam=i2c_arm=on missing in config.txt"
grep -qE '^dtparam=spi=on' "$CFG" && ok "SPI enabled in config.txt" || fail "dtparam=spi=on missing in config.txt"
for f in kernel8.img initramfs8; do [ -s "$W/boot/firmware/$f" ] && ok "$f present" || fail "$f missing"; done
[ -d "$W/boot/firmware/overlays" ] && ok "overlays/ present" || fail "overlays/ missing"
[ ! -e "$W/boot/firmware/custom.toml" ] && [ ! -e "$W/boot/firmware/firstrun.sh" ] && ok "no Imager customisation left on the card" || fail "custom.toml/firstrun.sh in the image"

section "initramfs"
INITRD="$W/boot/firmware/initramfs8"
LS="$(lsinitramfs "$INITRD" 2>/dev/null)"
if [ -n "$LS" ]; then
    echo "$LS" | grep -q 'scripts/init-bottom/overlayroot' && ok "initramfs8 contains the overlayroot script" || fail "initramfs8 has no overlayroot script (hector-overlay on would do nothing)"
    echo "$LS" | grep -q 'scripts/local-premount/firstboot' && ok "initramfs8 still has the stock firstboot resize script (idle without 'firstboot' on the command line)" || warn "no firstboot premount script in initramfs8"
    echo "$LS" | grep -qE '(^|/)(fsck\.ext4|e2fsck)$' && ok "initramfs8 can check ext4" || warn "no fsck.ext4 in initramfs8 (the stock image: see README, systemd-fsck checks later)"
    echo "$LS" | grep -qE 'overlay\.ko|kernel/fs/overlayfs' && ok "initramfs8 has the overlay module" || warn "overlay module not in initramfs8 (built into the kernel?)"
else
    fail "cannot list initramfs8 (lsinitramfs)"
fi

# ----------------------------------------------------------------- system partition
section "System partition"
grep -q "^PARTUUID=${DISK_ID#0x}-02 */ " "$W/etc/fstab" && ok "fstab: root by PARTUUID" || fail "fstab: root entry"
grep -q "^PARTUUID=${DISK_ID#0x}-01 */boot/firmware " "$W/etc/fstab" && ok "fstab: boot by PARTUUID" || fail "fstab: boot entry"
DL="$(grep '/var/lib/hector9000' "$W/etc/fstab" || true)"
echo "$DL" | grep -q "^PARTUUID=${DISK_ID#0x}-03 " && ok "fstab: data partition by PARTUUID (not by label)" || fail "fstab: data partition entry: ${DL:-none}"
for o in nosuid nodev noexec nofail noatime; do echo "$DL" | grep -q "$o" && ok "fstab: data partition has $o" || fail "fstab: data partition lacks $o"; done
! grep -q 'LABEL=HECTORDATA' "$W/etc/fstab" && ok "fstab does not trust the label" || fail "fstab mounts by LABEL"
[ -s "$W/etc/hostname" ] && ok "hostname: $(cat "$W/etc/hostname")" || fail "no /etc/hostname"
[ ! -s "$W/etc/machine-id" ] && ok "machine-id is empty (made at the first boot)" || fail "machine-id is baked in"
! ls "$W"/etc/ssh/ssh_host_* >/dev/null 2>&1 && ok "no SSH host keys in the image" || fail "SSH host keys are baked in"
! ls "$W/etc/init.d/resize2fs_once" "$W"/etc/rc?.d/*resize2fs_once >/dev/null 2>&1 && ok "no resize2fs_once (the system partition keeps its size)" || fail "resize2fs_once is still in the image"
[ -z "$(find "$W/home" "$W/root" -maxdepth 2 \( -name .bash_history -o -name .lesshst -o -name .viminfo -o -name authorized_keys \) 2>/dev/null)" ] && ok "no shell history / authorized_keys" || fail "history or authorized_keys found"
[ -z "$(find "$W/etc/NetworkManager/system-connections" -type f 2>/dev/null)" ] && ok "no Wi-Fi/network secrets (system-connections is empty)" || fail "network connections baked in"
[ ! -s "$W/etc/wpa_supplicant/wpa_supplicant.conf" ] || ! grep -q 'psk=' "$W/etc/wpa_supplicant/wpa_supplicant.conf" 2>/dev/null && ok "no wpa_supplicant psk" || fail "wpa_supplicant.conf has a psk"
[ ! -e "$W/etc/hector9000/firstboot.done" ] && ok "no first-boot flag (it must run on the card)" || fail "firstboot.done exists"
[ -s "$W/etc/hector9000/image-version" ] && ok "image-version: $(tr '\n' ' ' < "$W/etc/hector9000/image-version")" || fail "no image-version"

section "Users"
PW="$W/etc/passwd"
uid_of() { awk -F: -v u="$1" '$1==u {print $3}' "$PW"; }
gid_of() { awk -F: -v u="$1" '$1==u {print $3}' "$W/etc/group"; }
shell_of() { awk -F: -v u="$1" '$1==u {print $7}' "$PW"; }
[ -n "$(uid_of hector)" ] && ok "user hector (uid $(uid_of hector))" || fail "user hector missing"
[ -n "$(uid_of hector-stats)" ] && ok "user hector-stats (uid $(uid_of hector-stats))" || fail "user hector-stats missing"
case "$(shell_of hector-stats)" in */nologin|*/false) ok "hector-stats cannot log in" ;; *) fail "hector-stats shell: $(shell_of hector-stats)" ;; esac
[ "$(uid_of pi)" = 1000 ] && ok "placeholder user pi (uid 1000): the Imager renames it to the chosen user" || fail "no uid-1000 user: the Imager's user setup would fail"
[ "$(uid_of hector)" != 1000 ] && ok "hector is not uid 1000 (the Imager would rename it)" || fail "hector has uid 1000"
awk -F: '$1=="hector-stats" {print $5}' "$W/etc/group" >/dev/null
! grep -E '^(sudo|adm|root):.*(hector|hector-stats)' "$W/etc/group" >/dev/null && ok "no service user in sudo/adm/root groups" || fail "a service user is in a privileged group"
for u in hector hector-stats; do
    pwfield="$(awk -F: -v u="$u" '$1==u {print $2}' "$W/etc/shadow")"
    case "$pwfield" in '!'*|'*'*) ok "$u has no password" ;; *) fail "$u has a password hash or empty password: '${pwfield:0:3}...'" ;; esac
done

section "Services"
WANTS="$W/etc/systemd/system"
for u in hector-server hector-controller hector-led hector-stats hector-kiosk; do
    [ -L "$WANTS/hector.target.wants/$u.service" ] && ok "$u enabled (hector.target)" || fail "$u not enabled"
done
[ -L "$WANTS/multi-user.target.wants/hector.target" ] && ok "hector.target enabled" || fail "hector.target not enabled"
[ -L "$WANTS/multi-user.target.wants/hector-firstboot.service" ] && ok "hector-firstboot enabled" || fail "hector-firstboot not enabled"
{ [ -e "$WANTS/multi-user.target.wants/mosquitto.service" ] || [ -e "$W/etc/rc2.d/S01mosquitto" ] || [ -e "$W/etc/systemd/system/mosquitto.service" ] || [ -L "$W/etc/systemd/system/multi-user.target.wants/mosquitto.service" ]; } \
    && ok "mosquitto enabled" || warn "could not see mosquitto enabled (Debian enables it by package default)"
REPO="$W/opt/hector9000/backend"
for f in "$REPO"/deploy/systemd/*.service "$REPO"/deploy/systemd/*.target; do
    cmp -s "$f" "$WANTS/$(basename "$f")" && ok "unit $(basename "$f") equals the repository's" || fail "unit $(basename "$f") differs from deploy/systemd"
done
cmp -s "$REPO/deploy/image/hector-firstboot.service" "$WANTS/hector-firstboot.service" && ok "unit hector-firstboot.service" || fail "hector-firstboot.service differs"
cmp -s "$REPO/deploy/image/hector-firstboot" "$W/usr/local/sbin/hector-firstboot" && [ -x "$W/usr/local/sbin/hector-firstboot" ] && ok "hector-firstboot installed and executable" || fail "hector-firstboot missing"
cmp -s "$REPO/deploy/image/userconfig-hector.conf" "$WANTS/userconfig.service.d/10-hector.conf" && grep -q '^ConditionPathExists=|' "$WANTS/userconfig.service.d/10-hector.conf" \
    && ok "userconfig.service only runs with a userconf.txt (no interactive dialog on the touchscreen)" || fail "userconfig.service drop-in missing: the stock rename dialog would block the boot"
cmp -s "$REPO/deploy/hector-overlay" "$W/usr/local/sbin/hector-overlay" && [ -x "$W/usr/local/sbin/hector-overlay" ] && ok "hector-overlay installed and executable" || fail "hector-overlay missing"
grep -q 'overlayroot=tmpfs:recurse=0' "$W/usr/local/sbin/hector-overlay" && ok "hector-overlay uses recurse=0" || fail "hector-overlay without recurse=0"
grep -q '^EnvironmentFile=' "$WANTS/hector-kiosk.service" && fail "the kiosk must not read hector.env" || ok "kiosk does not read hector.env (no admin password / Matrix token for Chromium)"

section "sudoers"
SUD="$W/etc/sudoers.d/hector9000"
[ "$(stat -c '%a %u:%g' "$SUD")" = "440 0:0" ] && ok "sudoers file 0440 root:root" || fail "sudoers mode/owner: $(stat -c '%a %u:%g' "$SUD")"
visudo -cf "$SUD" >/dev/null 2>&1 && ok "sudoers syntax (visudo -cf)" || fail "sudoers syntax error"
! grep -E 'ALL *= *\(ALL(:ALL)?\)|NOPASSWD: *ALL' "$SUD" >/dev/null && ok "no blanket sudo rule" || fail "blanket sudo rule"

section "Owners and modes"
ENVF="$W/etc/hector9000/hector.env"
[ "$(stat -c '%a %u:%g' "$ENVF")" = "640 0:$(gid_of hector-stats)" ] && ok "hector.env 0640 root:hector-stats" || fail "hector.env: $(stat -c '%a %u:%g' "$ENVF") (hector-stats gid $(gid_of hector-stats))"
! grep -qE '^HECTOR_(ADMIN_PASSWORD|MATRIX_TOKEN)=.+' "$ENVF" && ok "hector.env holds no password or token" || fail "a secret is in hector.env"
[ "$(stat -c '%a %u:%g' "$W/etc/hector9000")" = "755 0:0" ] && ok "/etc/hector9000 0755 root" || fail "/etc/hector9000: $(stat -c '%a %u:%g' "$W/etc/hector9000")"
D="$W/var/lib/hector9000"
HU="$(uid_of hector)"; SU="$(uid_of hector-stats)"
[ "$(stat -c '%a %u:%g' "$D")" = "755 0:0" ] && ok "data root is 0755 root (service users cannot swap folders)" || fail "data root: $(stat -c '%a %u:%g' "$D")"
[ "$(stat -c '%a %u' "$D/hector")" = "751 $HU" ] && ok "hector/ 0751 hector" || fail "hector/: $(stat -c '%a %u' "$D/hector" 2>&1)"
[ "$(stat -c '%a %u' "$D/stats")" = "750 $SU" ] && ok "stats/ 0750 hector-stats" || fail "stats/: $(stat -c '%a %u' "$D/stats" 2>&1)"
[ -z "$(find "$D" -mindepth 2 -print -quit)" ] && ok "data partition has no files yet" || fail "unexpected files on the data partition"
[ "$(blkid -s LABEL -o value "$L3")" = HECTORDATA ] && ok "data partition label HECTORDATA" || fail "data partition label"
e2fsck -fn "$L3" >/dev/null 2>&1 && ok "data partition file system is clean" || fail "e2fsck on the data partition"
e2fsck -fn "$L2" >/dev/null 2>&1 && ok "system partition file system is clean" || fail "e2fsck on the system partition"
[ -z "$(find "$W/opt/hector9000/backend" "$W/opt/hector9000/webui" "$W/opt/hector9000/venv" -maxdepth 1 ! -user root -print -quit)" ] && ok "code and venv belong to root (services cannot modify them)" || fail "something below /opt/hector9000 is not owned by root"
[ -z "$(find "$W/opt/hector9000" -perm -0002 ! -type l -print -quit)" ] && ok "nothing below /opt/hector9000 is world-writable" || fail "world-writable file below /opt/hector9000"

section "Packages and code"
DPKG="$W/var/lib/dpkg/status"
inst() { awk -v p="$1" '$1=="Package:" {cur=$2} $1=="Status:" && cur==p && $4=="installed" {f=1} END {exit !f}' "$DPKG"; }
for p in mosquitto cage chromium overlayroot sudo git python3-venv raspi-config openssh-server network-manager; do
    inst "$p" && ok "package $p" || fail "package $p missing"
done
for p in build-essential modemmanager triggerhappy dphys-swapfile man-db; do
    inst "$p" && fail "package $p should have been removed" || ok "package $p removed"
done
[ -L "$W/opt/hector9000/venv/bin/python" ] && [ "$(readlink "$W/opt/hector9000/venv/bin/python")" = "python3" ] && [ -L "$W/opt/hector9000/venv/bin/python3" ] \
    && [ -e "$W/usr/bin/$(readlink "$W/opt/hector9000/venv/bin/python3" | sed 's#.*/##')" ] && ok "virtualenv present (python -> $(readlink "$W/opt/hector9000/venv/bin/python3"))" || fail "virtualenv missing or broken"
ls "$W"/opt/hector9000/venv/lib/python3*/site-packages/paho >/dev/null 2>&1 && ok "paho-mqtt in the venv" || fail "paho-mqtt not in the venv"
for r in backend webui; do
    url="$(git -c safe.directory='*' -C "$W/opt/hector9000/$r" config --get remote.origin.url 2>/dev/null)"
    case "$url" in https://github.com/koljasagorski/*) ok "$r: origin $url" ;; *) fail "$r: origin is '$url'" ;; esac
    git -c safe.directory='*' -C "$W/opt/hector9000/$r" diff --quiet 2>/dev/null && ok "$r: working tree clean" || fail "$r: working tree is not clean"
done
[ -x "$W/opt/hector9000/webui/kiosk.sh" ] && ok "kiosk.sh executable" || fail "kiosk.sh not executable"
du -sk "$W" 2>/dev/null | awk '{printf "  info  system partition content: %.0f MiB\n", $1/1024}'

section "Nothing private in the image"
# private keys (public CA certificates are fine); the snakeoil key of ssl-cert is a known throw-away
KEYS="$(grep -rIl --exclude-dir=proc --exclude-dir=sys -e '-----BEGIN [A-Z ]*PRIVATE KEY-----' "$W/etc" "$W/home" "$W/root" "$W/opt/hector9000/backend" "$W/opt/hector9000/webui" "$W/usr/local" "$W/var/lib" 2>/dev/null | grep -v 'ssl-cert-snakeoil' || true)"
[ -z "$KEYS" ] && ok "no private key files" || fail "private key material: $KEYS"
# Extra pattern for things that must not be in the image (an address, a name, a host): set
# HECTOR_CHECK_FORBIDDEN to an extended regular expression in the environment. It is deliberately
# not written down in this file.
if [ -n "${HECTOR_CHECK_FORBIDDEN:-}" ]; then
    HITS="$(grep -rIlE --exclude-dir=proc --exclude-dir=sys -e "$HECTOR_CHECK_FORBIDDEN" "$W/etc" "$W/home" "$W/root" "$W/opt/hector9000/backend" "$W/opt/hector9000/webui" "$W/usr/local" 2>/dev/null || true)"
    [ -z "$HITS" ] && ok "the pattern from HECTOR_CHECK_FORBIDDEN is nowhere in the image" || fail "HECTOR_CHECK_FORBIDDEN found in: $HITS"
else
    warn "HECTOR_CHECK_FORBIDDEN not set - no extra pattern was searched"
fi
SECRETS="$(grep -rIlE --exclude-dir=.git --exclude='*.md' --exclude='*.example' --exclude='test_*' -e '(syt_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[0-9A-Z]{16})' "$W/etc" "$W/home" "$W/root" "$W/opt/hector9000/backend" "$W/opt/hector9000/webui" 2>/dev/null || true)"
[ -z "$SECRETS" ] && ok "no access tokens (Matrix/GitHub/AWS patterns)" || fail "token-like strings in: $SECRETS"

printf '\n\033[1mResult: %d ok, %d warnings, %d FAILED\033[0m\n' "$PASS" "$WARNS" "$FAILS"
[ "$FAILS" -eq 0 ]
