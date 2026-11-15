#!/usr/bin/env bash
#
# Carry the data of an existing Hector9000 Pi over to the slim-OS install.
#
#   migrate-data.sh export [REPO_DIR] [OUT.tar.gz]   on the OLD Pi
#   sudo migrate-data.sh import OUT.tar.gz            on the NEW Pi
#
# export collects: the database(s) h9k.db*, the local (uncommitted) code
# changes as a patch plus full copies of the changed files (the calibration
# hx711.ref and the valve positions live in conf/HectorConfig.py, the drinks
# in conf/drinks.py), the list of untracked files and the git revision.
# Contains no passwords or keys; still treat the archive as private.
#
# import puts the databases into /var/lib/hector9000 (existing ones are saved
# as *.bak-<time>) and unpacks the code changes to
# /var/lib/hector9000/migrated/ for REVIEW. It never edits the checkout.
#
set -euo pipefail

STATE_DIR="/var/lib/hector9000"
HECTOR_USER="hector"

die() { echo "ERROR: $*" >&2; exit 1; }

TMP_DIR=""
cleanup() { [ -z "$TMP_DIR" ] || rm -rf "$TMP_DIR"; }
trap cleanup EXIT

cmd_export() {
    local repo="${1:-$PWD}" out="${2:-hector-data-$(date +%Y%m%d-%H%M%S).tar.gz}" tmp
    [ -d "$repo/Hector9000" ] || die "$repo is not a Hector9000 checkout (pass REPO_DIR)"
    tmp="$(mktemp -d)"
    TMP_DIR="$tmp"
    mkdir -p "$tmp/hector-data/db" "$tmp/hector-data/files"
    local f n=0
    for f in "$repo"/h9k.db*; do
        [ -f "$f" ] || continue
        cp -p "$f" "$tmp/hector-data/db/"
        n=$((n + 1))
    done
    echo "databases: $n"
    if git -C "$repo" rev-parse --git-dir >/dev/null 2>&1; then
        {
            echo "date:   $(date -Is)"
            echo "branch: $(git -C "$repo" rev-parse --abbrev-ref HEAD)"
            echo "commit: $(git -C "$repo" rev-parse HEAD)"
        } > "$tmp/hector-data/info.txt"
        git -C "$repo" diff > "$tmp/hector-data/local-changes.patch" || true
        git -C "$repo" status --porcelain > "$tmp/hector-data/status.txt" || true
        git -C "$repo" diff --name-only | while read -r changed; do
            mkdir -p "$tmp/hector-data/files/$(dirname "$changed")"
            cp -p "$repo/$changed" "$tmp/hector-data/files/$changed"
        done
        echo "local changes: $(git -C "$repo" diff --name-only | tr '\n' ' ')"
    else
        echo "no git repository - copying the config files instead"
        for changed in Hector9000/conf/HectorConfig.py Hector9000/conf/drinks.py Hector9000/conf/database.py; do
            if [ -f "$repo/$changed" ]; then
                mkdir -p "$tmp/hector-data/files/$(dirname "$changed")"
                cp -p "$repo/$changed" "$tmp/hector-data/files/$changed"
            fi
        done
    fi
    (umask 077; tar -C "$tmp" -czf "$out" hector-data)
    echo "written: $out"
}

cmd_import() {
    local archive="${1:-}" tmp ts
    [ -f "$archive" ] || die "usage: migrate-data.sh import ARCHIVE.tar.gz"
    [ "$(id -u)" -eq 0 ] || die "run as root"
    id -u "$HECTOR_USER" >/dev/null 2>&1 || die "user $HECTOR_USER missing - run deploy/provision.sh first"
    tmp="$(mktemp -d)"
    TMP_DIR="$tmp"
    tar -C "$tmp" --no-same-owner -xzf "$archive"
    [ -d "$tmp/hector-data" ] || die "not a hector-data archive"
    # the archive may come from anywhere (often /tmp): as root we must not follow links
    if [ -n "$(find "$tmp" -type l -print -quit)" ]; then
        die "the archive contains symbolic links - refusing to import it"
    fi
    ts="$(date +%Y%m%d-%H%M%S)"

    if [ -d /run/systemd/system ]; then
        echo "stopping Hector services (databases must not be open, nothing may run as $HECTOR_USER)"
        systemctl stop hector.target hector-kiosk hector-controller hector-server hector-led hector-stats 2>/dev/null || true
    fi

    # The checks below only hold while nothing runs as the service user (it could create a
    # link in $STATE_DIR between our check and our write). Stopping the units is not enough
    # for a stray process (e.g. an SSH session), so refuse to go on if one exists.
    if pgrep -u "$HECTOR_USER" >/dev/null 2>&1; then
        die "processes are still running as $HECTOR_USER ($(pgrep -u "$HECTOR_USER" -l | head -c 200 | tr '\n' ' ')) - stop them (pkill -u $HECTOR_USER) and run again"
    fi
    install -d -m 0751 -o "$HECTOR_USER" -g "$HECTOR_USER" "$STATE_DIR"
    # $STATE_DIR belongs to the service user, but this script writes there as root. A symbolic
    # link planted by that user would redirect root's writes (backup copies) to any file.
    if [ -n "$(find "$STATE_DIR" -maxdepth 1 -type l -print -quit)" ]; then
        die "$STATE_DIR contains symbolic links ($(find "$STATE_DIR" -maxdepth 1 -type l -printf '%f ' | head -c 200)) - remove them and run again"
    fi
    local f base
    for f in "$tmp"/hector-data/db/*; do
        [ -f "$f" ] || continue
        base="$(basename "$f")"
        case "$base" in
            h9k.db|h9k.db-*) ;;
            *) echo "skipping unexpected file in the archive: $base"; continue ;;
        esac
        if [ -f "$STATE_DIR/$base" ]; then
            [ ! -e "$STATE_DIR/$base.bak-$ts" ] || die "$STATE_DIR/$base.bak-$ts already exists"
            cp -p --no-dereference "$STATE_DIR/$base" "$STATE_DIR/$base.bak-$ts"
            echo "saved existing $base as $base.bak-$ts"
        fi
        # 0644: the statistics service (own user) reads the bottle names; the file holds recipes, no secrets
        install -m 0644 -o "$HECTOR_USER" -g "$HECTOR_USER" "$f" "$STATE_DIR/$base"
        echo "database: $STATE_DIR/$base"
    done

    local review="$STATE_DIR/migrated"
    rm -rf "$review"
    install -d -m 0750 -o "$HECTOR_USER" -g "$HECTOR_USER" "$review"
    cp -a "$tmp"/hector-data/. "$review/"
    rm -rf "$review/db"
    chown -R "$HECTOR_USER":"$HECTOR_USER" "$review"
    echo
    echo "Code changes of the old Pi are in $review for review:"
    echo "  info.txt, local-changes.patch, files/ (full copies), status.txt"
    if [ -s "$review/local-changes.patch" ] && [ -d /opt/hector9000/backend/.git ]; then
        echo "Does the patch apply to the new checkout? (dry check, nothing changed)"
        if git -C /opt/hector9000/backend apply --check "$review/local-changes.patch" 2>/dev/null; then
            echo "  yes: git -C /opt/hector9000/backend apply $review/local-changes.patch"
        else
            echo "  no (different code base). Compare by hand: calibration hx711.ref and"
            echo "  valvepositions in conf/HectorConfig.py, recipes in conf/drinks.py."
        fi
    fi
    echo "Then: sudo systemctl restart hector.target"
}

case "${1:-}" in
    export) shift; cmd_export "$@" ;;
    import) shift; cmd_import "$@" ;;
    *) sed -n '3,/^set -euo/p' "${BASH_SOURCE[0]}" | sed -e '$d' -e 's/^# \{0,1\}//' -e 's/^#$//'; exit 2 ;;
esac
