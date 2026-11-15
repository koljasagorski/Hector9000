#!/bin/bash
#
# One-time offline update of Hector9000 + Hector9000WebUI (congress design, stats, Doom).
# Started once as root via cmdline.txt (systemd.run=..., like the Raspberry Pi
# Imager firstrun.sh). The first thing it does is restore the original
# cmdline.txt, so the next boot is a normal one no matter what happens below.
#
# Writes everything it does to hector-update/update.log on the boot partition
# (readable from Windows). Never touches anything it cannot back up first.

BOOT=/boot
[ -f /boot/firmware/cmdline.txt ] && BOOT=/boot/firmware
DIR="$BOOT/hector-update"
LOG="$DIR/update.log"
exec >>"$LOG" 2>&1

echo "=================================================================="
echo "hector-update start: $(date)"

# --- 1. normal boot next time ---------------------------------------------
if [ -f "$DIR/cmdline.txt.orig" ]; then
    cp "$DIR/cmdline.txt.orig" "$BOOT/cmdline.txt" && echo "cmdline.txt restored"
else
    sed -i 's| systemd.run=[^ ]*||; s| systemd.run_success_action=[^ ]*||; s| systemd.unit=kernel-command-line.target||' "$BOOT/cmdline.txt"
    echo "cmdline.txt cleaned with sed (no backup found)"
fi
sync
echo "cmdline now: $(cat "$BOOT/cmdline.txt")"
if grep -q "systemd.run" "$BOOT/cmdline.txt"; then
    # exit non-zero -> no automatic reboot -> no boot loop; fix from Windows
    echo "FATAL: could not restore cmdline.txt, stopping without reboot"
    sync
    exit 1
fi

# each payload has its own VERSION; the same payload never runs twice
VERSION=$(cat "$DIR/VERSION" 2>/dev/null || echo unknown)
DONE=/var/lib/hector-update.done
echo "payload version: $VERSION"
if [ -e "$DONE" ] && grep -q "version=$VERSION" "$DONE"; then
    echo "this payload already ran ($(cat "$DONE")), nothing to do"
    sync
    exit 0
fi

STAMP=$(date +%Y%m%d-%H%M%S)

section() { echo; echo "--- $* ---"; }

# --- 2. discovery (read only) -----------------------------------------------
section "system"
echo "model: $(tr -d '\0' < /proc/device-tree/model 2>/dev/null)"
grep PRETTY_NAME /etc/os-release
echo "memory: $(free -m | awk '/Mem:/{print $2" MB"}')"
echo "ssh enabled: $(systemctl is-enabled ssh 2>&1)"
echo "network (configured, may be down this early):"
ip -brief addr 2>/dev/null | grep -v '^lo'
ls /etc/wpa_supplicant/wpa_supplicant.conf >/dev/null 2>&1 && echo "wifi config: present (country $(grep -o 'country=..' /etc/wpa_supplicant/wpa_supplicant.conf))"
uname -a
echo "git: $(git --version 2>&1)"
echo "python3: $(python3 --version 2>&1)"
for b in chromium-browser chromium; do command -v "$b" && "$b" --version 2>/dev/null | head -1; done
echo "flock: $(command -v flock)"
echo "lightdm autologin: $(grep -h '^autologin-user' /etc/lightdm/lightdm.conf /etc/lightdm/lightdm.conf.d/*.conf 2>/dev/null)"
ls -la /home

section "how is hector started"
for u in $(ls /home) root; do
    c=$(crontab -l -u "$u" 2>/dev/null | grep -v '^#' | grep -v '^$')
    [ -n "$c" ] && { echo "crontab $u:"; echo "$c"; }
done
[ -f /etc/rc.local ] && { echo "rc.local:"; grep -v '^#' /etc/rc.local | grep -v '^$'; }
grep -ril "hector\|Main.html\|chromium" /etc/systemd/system /etc/xdg/autostart \
    /etc/xdg/lxsession 2>/dev/null | while read -r f; do echo "== $f"; grep -iE "hector|Main.html|chromium|Exec" "$f"; done
for h in /home/*; do
    for f in "$h"/.config/autostart/* "$h"/.config/lxsession/*/autostart \
             "$h"/.config/systemd/user/*.service "$h"/.bashrc "$h"/.profile; do
        [ -f "$f" ] || continue
        if grep -qiE "hector|Main.html|chromium|start.sh" "$f"; then
            echo "== $f"; grep -iE "hector|Main.html|chromium|start.sh" "$f"
        fi
    done
done

section "mosquitto"
grep -hv '^#' /etc/mosquitto/mosquitto.conf /etc/mosquitto/conf.d/*.conf 2>/dev/null | grep -v '^$'

# --- 3. find the repos --------------------------------------------------------
section "repos"
BACKEND=""; WEBUI=""
for d in $(find /home /opt /root -maxdepth 4 -type d 2>/dev/null); do
    if [ -f "$d/Hector9000/HectorServer.py" ] && [ -f "$d/setup.py" ]; then
        echo "backend candidate: $d"; [ -z "$BACKEND" ] && BACKEND="$d"
    fi
    if [ -f "$d/Main.html" ] && [ -f "$d/js/hector9000.js" ]; then
        echo "webui candidate: $d"; [ -z "$WEBUI" ] && WEBUI="$d"
    fi
done
echo "backend: ${BACKEND:-NOT FOUND}"
echo "webui:   ${WEBUI:-NOT FOUND}"

# --- 4. update one repo from a bundle, with backup and rollback ---------------
update_repo() {
    local repo="$1" bundle="$2" branch="$3"
    local owner home stashed=0 orig origref
    owner=$(stat -c %U "$repo")
    home=$(getent passwd "$owner" | cut -d: -f6)
    G() { setpriv --reuid="$owner" --regid="$(id -g "$owner")" --init-groups \
              env HOME="$home" git -C "$repo" "$@"; }

    section "update $repo (owner $owner) -> $branch"
    if ! G rev-parse --git-dir >/dev/null 2>&1; then
        echo "SKIP: not a git checkout"
        return 1
    fi
    G remote -v
    orig=$(G rev-parse HEAD)
    origref=$(G rev-parse --abbrev-ref HEAD)
    back="$origref"; [ "$origref" = "HEAD" ] && back="$orig"   # detached HEAD
    echo "current: $origref $orig"
    G status --short
    echo "local changes (diff, max 200 lines):"
    G diff | head -200

    local backup="$home/hector-backup-$STAMP-$(basename "$repo").tar.gz"
    if ! tar czf "$backup" -C "$(dirname "$repo")" "$(basename "$repo")"; then
        echo "ABORT: backup failed, repo untouched"
        return 1
    fi
    chown "$owner": "$backup"
    echo "backup: $backup"

    if ! G fetch "$bundle" "+$branch:refs/remotes/congress-bundle/$branch"; then
        echo "ABORT: fetch from bundle failed, repo untouched"
        return 1
    fi

    if [ -n "$(G status --porcelain --untracked-files=no)" ]; then
        G stash push -m "hector-update $STAMP" && stashed=1
        echo "local changes stashed"
    fi

    if ! G checkout -B "$branch" "refs/remotes/congress-bundle/$branch"; then
        echo "ROLLBACK: checkout failed"
        G checkout -f "$back"
        [ "$stashed" = 1 ] && G stash pop
        return 1
    fi

    if [ "$stashed" = 1 ]; then
        if G stash pop; then
            echo "local changes re-applied"
        else
            echo "ROLLBACK: local changes conflict with the update"
            G reset --hard
            G checkout -f "$back"
            G stash pop
            return 1
        fi
    fi
    # old commits that are not in the new branch stay reachable on $origref
    echo "unique local commits kept on $back: $(G rev-list --count "$branch..$orig")"
    G remote get-url kolja >/dev/null 2>&1 || \
        G remote add kolja "https://github.com/koljasagorski/$(basename "$bundle" .bundle).git"
    echo "now: $(G log --oneline -1)"
    return 0
}

# Only as a pair: the new WebUI (kiosk, reboot, exit) needs the new backend.
BACKEND_OK=0; WEBUI_OK=0
[ -n "$BACKEND" ] && update_repo "$BACKEND" "$DIR/Hector9000.bundle" feature/congress-webui && BACKEND_OK=1
if [ "$BACKEND_OK" = 1 ] && [ -n "$WEBUI" ]; then
    update_repo "$WEBUI" "$DIR/Hector9000WebUI.bundle" feature/congress-design && WEBUI_OK=1
else
    section "webui"
    echo "SKIP WebUI update: backend was not updated"
fi

if [ "$BACKEND_OK" = 1 ]; then
    section "python import check"
    owner=$(stat -c %U "$BACKEND")
    setpriv --reuid="$owner" --regid="$(id -g "$owner")" --init-groups env HOME="$(getent passwd "$owner" | cut -d: -f6)" \
        python3 -c "import Hector9000.utils.HectorSystem as m, Hector9000.utils.Events, Hector9000; print('Hector9000 imported from', Hector9000.__file__, '(utils.Events found)')" \
        2>&1 || echo "WARNING: python3 imports Hector9000 not from the updated checkout (run 'sudo python3 setup.py develop' in $BACKEND)"
    (cd / && python3 -c "import Hector9000; print('root imports Hector9000 from', Hector9000.__file__)" 2>&1)
fi

# --- 5. kiosk autostart (only if the WebUI was updated) -----------------------
if [ "$WEBUI_OK" = 1 ]; then
    section "kiosk autostart"
    # autostart belongs to the desktop (autologin) user, fall back to the repo owner
    owner=$(grep -h '^autologin-user=' /etc/lightdm/lightdm.conf /etc/lightdm/lightdm.conf.d/*.conf 2>/dev/null | tail -1 | cut -d= -f2)
    [ -z "$owner" ] || [ "$owner" = "root" ] && owner=$(stat -c %U "$WEBUI")
    home=$(getent passwd "$owner" | cut -d: -f6)
    echo "desktop user: $owner ($home)"
    # old systemd user unit from the previous install.sh (would start a 2nd browser)
    wants="$home/.config/systemd/user/default.target.wants/Hector9000WebUI.service"
    unit="$home/.config/systemd/user/Hector9000WebUI.service"
    [ -L "$wants" ] && { rm -f "$wants"; echo "disabled old unit (removed $wants)"; }
    [ -f "$unit" ] && { mv "$unit" "$home/Hector9000WebUI.service.disabled-$STAMP"; echo "moved old unit to $home"; }
    setpriv --reuid="$owner" --regid="$(id -g "$owner")" --init-groups env HOME="$home" bash "$WEBUI/install.sh"
    chmod +x "$WEBUI/kiosk.sh" "$WEBUI/start.sh"
fi

# --- 6. backend autostart (was started by hand with ./start.sh) -------------
if [ "$BACKEND_OK" = 1 ] && [ "$WEBUI_OK" = 1 ]; then
    section "backend autostart"
    existing=$( { cat /etc/rc.local 2>/dev/null; for u in $(ls /home) root; do crontab -l -u "$u" 2>/dev/null; done; \
                  cat /etc/systemd/system/*.service 2>/dev/null; } | grep -v '^\s*#' | grep -E "HectorServer|HectorController|start\.sh" )
    if [ -n "$existing" ]; then
        echo "SKIP: backend already started at boot by:"; echo "$existing"
    else
        bowner=$(stat -c %U "$BACKEND")
        echo "sudo for $bowner (needed for reboot + LED server):"; sudo -n -l -U "$bowner" 2>&1 | tail -3
        cat > /etc/systemd/system/hector9000.service <<UNIT
[Unit]
Description=Hector9000 backend (HectorServer, HectorController, LED server via start.sh)
After=network-online.target mosquitto.service
Wants=network-online.target mosquitto.service

[Service]
Type=oneshot
RemainAfterExit=yes
User=$bowner
WorkingDirectory=$BACKEND
ExecStart=/bin/bash $BACKEND/start.sh

[Install]
WantedBy=multi-user.target
UNIT
        mkdir -p /etc/systemd/system/multi-user.target.wants
        ln -sf /etc/systemd/system/hector9000.service /etc/systemd/system/multi-user.target.wants/hector9000.service
        echo "created + enabled /etc/systemd/system/hector9000.service (user $bowner)"
        echo "disable again: sudo systemctl disable --now hector9000.service"
    fi
fi

# --- 7. statistics + admin dashboard (own unit; start.sh is never touched) ----------------
if [ "$BACKEND_OK" = 1 ] && [ -f "$BACKEND/Hector9000/StatsServer.py" ]; then
    section "statistics service"
    bowner=$(stat -c %U "$BACKEND")
    bgroup=$(id -gn "$bowner")
    STATE=/var/lib/hector9000
    install -d -m 0750 -o "$bowner" -g "$bgroup" "$STATE"
    echo "state dir: $STATE (owner $bowner)"
    setpriv --reuid="$bowner" --regid="$(id -g "$bowner")" --init-groups env HOME="$(getent passwd "$bowner" | cut -d: -f6)" \
        python3 -c "import sqlite3, paho.mqtt.client; print('python modules ok (sqlite3, paho-mqtt)')" 2>&1 \
        || echo "WARNING: python3 module missing for the stats service (needs paho-mqtt, like the controller)"

    # Which processes should the watchdog expect? The LED server is switched off by hand on this Pi.
    expect="server,controller"
    grep -v '^[[:space:]]*#' "$BACKEND/start.sh" 2>/dev/null | grep -q "LEDStripServer" && expect="server,controller,led"
    echo "watchdog expects: $expect"

    # Admin password: made here so it can be read from the SD card (no SSH needed). It is NOT
    # written to update.log. Read admin-password.txt from the boot partition, then delete it.
    PWFILE="$STATE/admin_password"
    if [ ! -s "$PWFILE" ]; then
        pw=$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 16)
        if [ ${#pw} -ge 12 ]; then
            ( umask 077; printf '%s\n' "$pw" > "$PWFILE" )
            chown "$bowner:$bgroup" "$PWFILE"; chmod 600 "$PWFILE"
            printf 'Hector9000 admin dashboard\nUser:     admin\nPassword: %s\nAddress:  http://<ip of the Pi>:8080/admin\n\nDelete this file after you have read it (the SD card is readable by anyone who holds it).\n' "$pw" > "$DIR/admin-password.txt"
            echo "admin password created ($PWFILE); copy for you: $DIR/admin-password.txt"
        else
            echo "WARNING: could not create a password, the service will make one on first start ($PWFILE)"
        fi
        unset pw
    else
        echo "admin password already exists ($PWFILE), unchanged"
    fi

    cat > /etc/systemd/system/hector9000-stats.service <<UNIT
[Unit]
Description=Hector9000 statistics and admin dashboard (HTTP port 8080)
After=network.target mosquitto.service
Wants=mosquitto.service

[Service]
Type=simple
User=$bowner
WorkingDirectory=$BACKEND
Environment=PYTHONUNBUFFERED=1
Environment=HECTOR_STATE_DIR=$STATE
Environment=HECTOR_DB=$BACKEND/h9k.db
Environment=HECTOR_EXPECT_PROCESSES=$expect
ExecStart=/usr/bin/python3 Hector9000/StatsServer.py
Restart=on-failure
RestartSec=5
TasksMax=160
LimitNOFILE=256
OOMScoreAdjust=500

[Install]
WantedBy=multi-user.target
UNIT
    mkdir -p /etc/systemd/system/multi-user.target.wants
    ln -sf /etc/systemd/system/hector9000-stats.service /etc/systemd/system/multi-user.target.wants/hector9000-stats.service
    echo "created + enabled /etc/systemd/system/hector9000-stats.service (user $bowner), starts at the next boot"
    echo "disable again: sudo systemctl disable --now hector9000-stats.service"
fi

section "result"
echo "backend updated: $BACKEND_OK   webui updated: $WEBUI_OK"
echo "$STAMP version=$VERSION backend=$BACKEND_OK webui=$WEBUI_OK" > "$DONE"
echo "hector-update end: $(date)"
sync
exit 0
