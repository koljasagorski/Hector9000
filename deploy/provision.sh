#!/usr/bin/env bash
#
# Hector9000 provisioning for Raspberry Pi OS Lite (Debian 12 "bookworm").
#
# Turns a freshly flashed Raspberry Pi OS Lite into a Hector9000 kiosk:
# mosquitto (localhost only), the Hector services as systemd units, a Python
# virtualenv, and a Chromium kiosk in "cage" on tty1. Idempotent: run it again
# after a `git pull` to roll out changes. See deploy/README.md.
#
#   sudo ./deploy/provision.sh [options]
#
# Options:
#   --backend-dir DIR   backend checkout (default: the repo this script is in)
#   --webui-dir DIR     existing WebUI checkout (default: /opt/hector9000/webui;
#                       cloned from --webui-repo if missing)
#   --webui-repo URL    where to clone the WebUI from
#   --webui-branch B    branch to clone (default feature/congress-design)
#   --requirements F    pip requirements file (default deploy/requirements-bookworm.txt)
#   --start             start the services right away (takes over tty1!)
#   --overlayfs         enable the read-only root overlay at the very end
#                       (refuses unless /var/lib/hector9000 is its own mount)
#   --image             building a disk image (chroot): no running systemd needed,
#                       units are only enabled, nothing is started or restarted
#   --skip-apt          do not run apt-get (packages already installed)
#   --skip-venv         do not (re)build the Python virtualenv
#   --no-systemd        copy the unit files but never call systemctl
#                       (containers, image building)
#   --dry-run           only print what would be done
#   -h, --help          this text
#
set -euo pipefail

# ------------------------------------------------------------------ defaults
HECTOR_USER="hector"
HECTOR_GROUPS=(gpio i2c spi video render input systemd-journal)
# The web-facing statistics service gets its own account: no groups, no sudo, no login. It reads
# nothing of the hardware side except the (world-readable) recipe database.
STATS_USER="hector-stats"
PREFIX="/opt/hector9000"
STATE_DIR="/var/lib/hector9000"            # owner ROOT: mount point of the data partition
HW_DIR="${STATE_DIR}/hector"               # hardware side: h9k.db (owner hector)
STATS_DIR="${STATE_DIR}/stats"             # stats.db, admin password, notify.json (owner hector-stats)
ETC_DIR="/etc/hector9000"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_BACKEND="$(cd "${SCRIPT_DIR}/.." && pwd)"

BACKEND_SRC="${DEFAULT_BACKEND}"
WEBUI_SRC=""
WEBUI_REPO="${HECTOR_WEBUI_REPO:-https://github.com/koljasagorski/Hector9000WebUI.git}"
WEBUI_BRANCH="${HECTOR_WEBUI_BRANCH:-feature/congress-design}"
REQUIREMENTS=""

DRY_RUN=0
USE_SYSTEMD=1
OVERLAYFS=0
IMAGE=0
START=0
SKIP_APT=0
SKIP_VENV=0
CHANGED_MOSQUITTO=0

# --------------------------------------------------------------------- utils
log()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[33mWARN:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

usage() { sed -n '3,/^set -euo/p' "${BASH_SOURCE[0]}" | sed -e '$d' -e 's/^# \{0,1\}//' -e 's/^#$//'; }

# run COMMAND...: print it, execute unless --dry-run
run() {
    if [ "$DRY_RUN" = 1 ]; then
        printf '    [dry-run] %s\n' "$*"
    else
        "$@"
    fi
}

need_arg() { if [ "$#" -lt 2 ] || [ -z "$2" ]; then die "option $1 needs a value"; fi; }

# install_file SRC DEST MODE [OWNER:GROUP]: copy only when content/mode differs
install_file() {
    local src="$1" dest="$2" mode="$3" owner="${4:-root:root}" want_mode
    [ -f "$src" ] || die "missing source file: $src"
    want_mode="$(printf '%o' "$((8#${mode}))")"   # "0755" -> "755", like stat prints it
    if [ -f "$dest" ] && cmp -s "$src" "$dest" \
        && [ "$(stat -c '%a' "$dest")" = "$want_mode" ] \
        && [ "$(stat -c '%U:%G' "$dest")" = "$owner" ]; then
        info "unchanged: $dest"
        return 1
    fi
    info "install:   $dest"
    run install -D -m "$mode" -o "${owner%%:*}" -g "${owner##*:}" "$src" "$dest"
    return 0
}

# link_dir SRC DEST: DEST becomes a symlink to SRC (or already is SRC)
link_dir() {
    local src dest="$2" cur
    src="$(cd "$1" && pwd -P)"
    if [ -L "$dest" ]; then
        cur="$(readlink -f "$dest")"
        if [ "$cur" = "$src" ]; then
            info "unchanged: $dest -> $src"
            return
        fi
        info "relink:    $dest -> $src (was $cur)"
        run ln -sfn "$src" "$dest"
    elif [ -e "$dest" ]; then
        [ "$(cd "$dest" && pwd -P)" = "$src" ] || \
            die "$dest exists and is not $src - move it away or pass the right --backend-dir/--webui-dir"
        info "unchanged: $dest"
    else
        info "link:      $dest -> $src"
        run mkdir -p "$(dirname "$dest")"
        run ln -s "$src" "$dest"
    fi
}

ensure_line() {   # ensure_line FILE LINE
    if grep -qxF -- "$2" "$1" 2>/dev/null; then
        info "unchanged: $1 ($2)"
    else
        info "append:    $1 ($2)"
        if [ "$DRY_RUN" = 1 ]; then printf '    [dry-run] echo %s >> %s\n' "$2" "$1"; else echo "$2" >> "$1"; fi
    fi
}

# ------------------------------------------------------------------ arguments
while [ "$#" -gt 0 ]; do
    case "$1" in
        --backend-dir)   need_arg "$@"; BACKEND_SRC="$2"; shift ;;
        --webui-dir)     need_arg "$@"; WEBUI_SRC="$2"; shift ;;
        --webui-repo)    need_arg "$@"; WEBUI_REPO="$2"; shift ;;
        --webui-branch)  need_arg "$@"; WEBUI_BRANCH="$2"; shift ;;
        --requirements)  need_arg "$@"; REQUIREMENTS="$2"; shift ;;
        --start)         START=1 ;;
        --overlayfs)     OVERLAYFS=1 ;;
        --image)         IMAGE=1; USE_SYSTEMD=1 ;;
        --skip-apt)      SKIP_APT=1 ;;
        --skip-venv)     SKIP_VENV=1 ;;
        --no-systemd)    USE_SYSTEMD=0 ;;
        --dry-run)       DRY_RUN=1 ;;
        -h|--help)       usage; exit 0 ;;
        *)               die "unknown option: $1 (see --help)" ;;
    esac
    shift
done

[ -d "$BACKEND_SRC/Hector9000" ] || die "not a Hector9000 backend checkout: $BACKEND_SRC"
BACKEND_SRC="$(cd "$BACKEND_SRC" && pwd -P)"
DEPLOY="${BACKEND_SRC}/deploy"
[ -d "$DEPLOY/systemd" ] || die "deploy/systemd not found in $BACKEND_SRC"
[ -n "$REQUIREMENTS" ] || REQUIREMENTS="${DEPLOY}/requirements-bookworm.txt"

if [ "$DRY_RUN" = 0 ] && [ "$(id -u)" -ne 0 ]; then
    die "run as root (sudo $0 ...), or use --dry-run"
fi

# -------------------------------------------------------------- 0. platform
log "Platform"
# shellcheck disable=SC1091
. /etc/os-release
case " ${ID:-} ${ID_LIKE:-} " in
    *" debian "*|*" raspbian "*) ;;
    *) die "needs Debian/Raspberry Pi OS (found ${PRETTY_NAME:-unknown})" ;;
esac
[ "${VERSION_CODENAME:-}" = "bookworm" ] || \
    warn "written for bookworm, found '${VERSION_CODENAME:-?}' - package names may differ"
ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"
info "${PRETTY_NAME:-?} / ${ARCH} / dry-run=${DRY_RUN} systemd=${USE_SYSTEMD}"
if [ "$USE_SYSTEMD" = 1 ] && [ "$IMAGE" = 0 ] && [ ! -d /run/systemd/system ] && [ "$DRY_RUN" = 0 ]; then
    die "systemd is not running here - use --no-systemd (container) or --image (chroot while building an image)"
fi

# ------------------------------------------------------------------ 1. apt
PACKAGES=(git rsync curl ca-certificates sudo mosquitto mosquitto-clients
          i2c-tools iproute2 python3 python3-venv python3-pip python3-dev
          build-essential libffi-dev cage libpam-systemd libgl1-mesa-dri)

if [ "$SKIP_APT" = 1 ]; then
    log "Packages: skipped (--skip-apt)"
else
    log "Packages"
    run env DEBIAN_FRONTEND=noninteractive apt-get update
    # Raspberry Pi OS ships "chromium"; older images only "chromium-browser".
    # (No `grep -q` in a pipe here: with pipefail it can fail the pipeline via SIGPIPE.)
    chromium_candidate="$(apt-cache policy chromium 2>/dev/null | awk '/Candidate:/ {print $2}')"
    if [ -n "$chromium_candidate" ] && [ "$chromium_candidate" != "(none)" ]; then
        PACKAGES+=(chromium)
    else
        PACKAGES+=(chromium-browser)
    fi
    run env DEBIAN_FRONTEND=noninteractive apt-get install -y "${PACKAGES[@]}"
fi

# ------------------------------------------------------- 2. interfaces I2C/SPI
log "I2C / SPI"
if command -v raspi-config >/dev/null 2>&1; then
    run raspi-config nonint do_i2c 0
    run raspi-config nonint do_spi 0
else
    found=0
    for cfg in /boot/firmware/config.txt /boot/config.txt; do
        if [ -f "$cfg" ]; then
            found=1
            ensure_line "$cfg" "dtparam=i2c_arm=on"
            ensure_line "$cfg" "dtparam=spi=on"
            break
        fi
    done
    [ "$found" = 1 ] || warn "no raspi-config and no config.txt - not a Raspberry Pi? I2C/SPI not enabled"
fi
run mkdir -p /etc/modules-load.d
if [ "$DRY_RUN" = 1 ]; then
    printf '    [dry-run] write /etc/modules-load.d/hector9000.conf (i2c-dev)\n'
else
    printf 'i2c-dev\n' > /etc/modules-load.d/hector9000.conf
fi

# ---------------------------------------------------------------- 3. user
log "User ${HECTOR_USER}"
for g in "${HECTOR_GROUPS[@]}"; do
    getent group "$g" >/dev/null || run groupadd --system "$g"
done
groups_csv="$(IFS=,; echo "${HECTOR_GROUPS[*]}")"
if id -u "$HECTOR_USER" >/dev/null 2>&1; then
    info "user exists, making sure of the groups"
    run usermod -aG "$groups_csv" "$HECTOR_USER"
else
    info "creating user (no password, login only via sudo/su)"
    run useradd --create-home --shell /bin/bash --groups "$groups_csv" "$HECTOR_USER"
fi
log "User ${STATS_USER} (statistics service, no groups, no login)"
if id -u "$STATS_USER" >/dev/null 2>&1; then
    info "user exists"
else
    run useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$STATS_USER"
fi

# --------------------------------------------------------------- 4. dirs
log "Directories"
run install -d -m 0755 "$PREFIX"
# STATE_DIR belongs to ROOT; each service user owns only its own sub folder. That way neither
# of them can rename or replace a folder (or plant a link) while root works below here, which
# would let them steer root's chown/mv/cat to other files.
[ ! -L "$STATE_DIR" ] || die "${STATE_DIR} is a symlink - refusing (changed by hand?)"
run install -d -m 0755 -o root -g root "$STATE_DIR"
for d in "$HW_DIR" "$STATS_DIR"; do
    [ ! -L "$d" ] || die "${d} is a symlink - refusing (changed by hand?)"
done
# hector/ is traversable (x) for others so that the statistics service can read the recipe
# database (bottle names); nobody but hector can list or write it.
run install -d -m 0751 -o "$HECTOR_USER" -g "$HECTOR_USER" "$HW_DIR"
run install -d -m 0750 -o "$STATS_USER" -g "$STATS_USER" "$STATS_DIR"
if [ "$DRY_RUN" = 0 ]; then
    # move_old SRC DST OWNER: rename a file/folder of an older layout into its new home.
    # mv renames the entry itself (never what a link points to); links are skipped.
    move_old() {
        local src="$1" dst="$2" owner="$3"
        if [ -L "$src" ]; then warn "ignoring ${src}: it is a symlink"; return 0; fi
        [ -e "$src" ] || return 0
        if [ -e "$dst" ] || [ -L "$dst" ]; then warn "${dst} exists, leaving ${src} where it is"; return 0; fi
        info "move: ${src} -> ${dst}"
        mv -n -T "$src" "$dst"
        chown -h "$owner" "$dst"
    }
    # first layout: everything directly in STATE_DIR (owned by hector, services ran as hector)
    for f in stats.db stats.db-wal stats.db-shm admin_password notify.json; do
        move_old "${STATE_DIR}/${f}" "${STATS_DIR}/${f}" "${STATS_USER}:${STATS_USER}"
    done
    for f in "${STATE_DIR}"/h9k.db* "${STATE_DIR}/migrated"; do
        [ -e "$f" ] || [ -L "$f" ] || continue
        move_old "$f" "${HW_DIR}/$(basename "$f")" "${HECTOR_USER}:${HECTOR_USER}"
    done
    # the recipe database may be read by the statistics service (bottle names). The owner does
    # the chmod itself, so a link in its own folder cannot make root change anything else.
    for f in "${HW_DIR}"/h9k.db*; do
        [ -f "$f" ] && [ ! -L "$f" ] || continue
        runuser -u "$HECTOR_USER" -- chmod o+r "$f" || warn "could not make ${f} readable"
    done
fi
# /etc/hector9000 is readable by everybody (config.json for the hardware services); only the
# secrets file hector.env is private to the statistics service
run install -d -m 0755 -o root -g root "$ETC_DIR"
link_dir "$BACKEND_SRC" "${PREFIX}/backend"

if [ -n "$WEBUI_SRC" ]; then
    [ -d "$WEBUI_SRC" ] || die "--webui-dir $WEBUI_SRC does not exist"
    link_dir "$WEBUI_SRC" "${PREFIX}/webui"
elif [ -e "${PREFIX}/webui" ]; then
    info "WebUI present: ${PREFIX}/webui"
else
    log "Cloning WebUI (${WEBUI_REPO}, branch ${WEBUI_BRANCH})"
    run git clone --branch "$WEBUI_BRANCH" "$WEBUI_REPO" "${PREFIX}/webui" || \
        die "could not clone the WebUI - clone it yourself and pass --webui-dir"
fi
if [ "$DRY_RUN" = 0 ]; then
    [ -f "${PREFIX}/webui/kiosk.sh" ] || die "${PREFIX}/webui/kiosk.sh missing - is that the Hector9000WebUI checkout?"
    chmod +x "${PREFIX}/webui/kiosk.sh"
fi

if [ ! -e "${ETC_DIR}/hector.env" ]; then
    info "create ${ETC_DIR}/hector.env from the template (edit it for admin password / Matrix)"
    run install -m 0640 -o root -g "$STATS_USER" "${DEPLOY}/hector.env.example" "${ETC_DIR}/hector.env"
else
    info "keep existing ${ETC_DIR}/hector.env"
    run chown root:"$STATS_USER" "${ETC_DIR}/hector.env"
    run chmod 0640 "${ETC_DIR}/hector.env"
fi

# --------------------------------------------------------------- 5. venv
if [ "$SKIP_VENV" = 1 ]; then
    log "Python virtualenv: skipped (--skip-venv)"
else
    log "Python virtualenv ${PREFIX}/venv"
    [ -f "$REQUIREMENTS" ] || die "requirements file not found: $REQUIREMENTS"
    VENV="${PREFIX}/venv"
    STAMP="${VENV}/.requirements.sha256"
    want="$(sha256sum "$REQUIREMENTS" | cut -d' ' -f1)"
    if [ "$DRY_RUN" = 0 ] && [ -x "${VENV}/bin/python" ] && [ "$(cat "$STAMP" 2>/dev/null || true)" = "$want" ]; then
        info "requirements unchanged ($(basename "$REQUIREMENTS"))"
    else
        [ -x "${VENV}/bin/python" ] || run python3 -m venv "$VENV"
        run "${VENV}/bin/pip" install --disable-pip-version-check -r "$REQUIREMENTS"
        if [ "$DRY_RUN" = 0 ]; then echo "$want" > "$STAMP"; fi
    fi
    if [ "$DRY_RUN" = 0 ]; then
        # imports that must work without hardware
        PYTHONPATH="${PREFIX}/backend" "${VENV}/bin/python" -c \
            "import paho.mqtt.client, webcolors, Hector9000.utils.LEDAnimations, Hector9000.utils.HectorSystem" \
            || die "Python smoke test failed (see above)"
        info "Python smoke test ok"
    fi
fi

# ----------------------------------------------- 6. units, scripts, configs
log "Install files"
install_file "${DEPLOY}/hector-maint"         /usr/local/bin/hector-maint         0755 || true
install_file "${DEPLOY}/hector-kiosk-session" /usr/local/bin/hector-kiosk-session 0755 || true
install_file "${DEPLOY}/hector-overlay"       /usr/local/sbin/hector-overlay      0755 || true

# the journal may not grow without limit on a small card
run mkdir -p /etc/systemd/journald.conf.d
if [ "$DRY_RUN" = 0 ]; then
    printf '[Journal]\nSystemMaxUse=50M\nRuntimeMaxUse=30M\n' > /etc/systemd/journald.conf.d/hector9000.conf
fi

if [ "$IMAGE" = 1 ]; then
    install_file "${DEPLOY}/image/hector-firstboot"         /usr/local/sbin/hector-firstboot              0755 || true
    install_file "${DEPLOY}/image/hector-firstboot.service" /etc/systemd/system/hector-firstboot.service 0644 || true
fi

for unit in "${DEPLOY}"/systemd/*.service "${DEPLOY}"/systemd/*.target; do
    install_file "$unit" "/etc/systemd/system/$(basename "$unit")" 0644 || true
done

if [ -d /etc/mosquitto ]; then
    install_file "${DEPLOY}/mosquitto/hector.conf" /etc/mosquitto/conf.d/hector.conf 0644 && CHANGED_MOSQUITTO=1
else
    warn "/etc/mosquitto missing - mosquitto not installed (--skip-apt?)"
fi

# sudoers: validate before it goes live, a broken file would lock out sudo
SUDOERS_SRC="${DEPLOY}/sudoers.d/hector9000"
if command -v visudo >/dev/null 2>&1; then
    visudo -cf "$SUDOERS_SRC" >/dev/null || die "sudoers file invalid: $SUDOERS_SRC"
    install_file "$SUDOERS_SRC" /etc/sudoers.d/hector9000 0440 || true
else
    warn "visudo not found - sudoers rules NOT installed (reboot/kiosk exit from the UI will not work)"
fi

# ---------------------------------------------------------- 7. services
if [ "$USE_SYSTEMD" = 1 ]; then
    log "Services"
    if [ "$IMAGE" = 0 ]; then run systemctl daemon-reload; fi
    run systemctl enable mosquitto.service
    run systemctl enable hector.target hector-server.service hector-controller.service \
        hector-led.service hector-stats.service hector-kiosk.service
    if [ "$IMAGE" = 1 ]; then run systemctl enable hector-firstboot.service; fi
    if [ "$IMAGE" = 1 ]; then
        info "image build: nothing is started or restarted"
    elif [ "$CHANGED_MOSQUITTO" = 1 ]; then
        info "mosquitto config changed, restarting broker"
        run systemctl restart mosquitto.service
    fi
    if [ "$START" = 1 ] && [ "$IMAGE" = 0 ]; then
        info "starting hector.target (the kiosk takes over tty1)"
        run systemctl restart hector.target
    else
        info "services start at the next boot (or: sudo systemctl start hector.target)"
    fi
else
    log "Services: skipped (--no-systemd)"
fi

# ---------------------------------------------------------- 8. overlayfs
if [ "$OVERLAYFS" = 1 ]; then
    log "Read-only root (overlay file system)"
    if ! mountpoint -q "$STATE_DIR"; then
        die "$STATE_DIR is not a separate partition. With a read-only root the databases would
       be lost at every reboot. Create a data partition and mount it there first (deploy/README.md)."
    fi
    run /usr/local/sbin/hector-overlay on
    info "takes effect after the next reboot. Undo with: sudo hector-overlay off"
fi

log "Done"
cat <<EOF
    Next steps:
      1. Copy your data (h9k.db, calibration) - see deploy/README.md, "Migrate data"
      2. Edit ${ETC_DIR}/hector.env (admin password, Matrix) if you want
      3. sudo reboot      (or: sudo systemctl start hector.target)
    The statistics page will be at http://<ip>:8080/ ; MQTT is localhost only.
EOF
