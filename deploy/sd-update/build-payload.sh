#!/usr/bin/env bash
#
# Baut den Ordner "hector-update" für das einmalige Offline-Update per SD-Karte
# (siehe README.md in diesem Verzeichnis).
#
#   deploy/sd-update/build-payload.sh [AUSGABE-ORDNER]
#
# Umgebung (optional):
#   WEBUI_DIR        Pfad zum Hector9000WebUI-Repo (Standard: ../Hector9000WebUI neben diesem Repo)
#   BACKEND_BRANCH   Standard feature/congress-webui
#   WEBUI_BRANCH     Standard feature/congress-design
#
# Die Bundles enthalten nur Committetes. Nicht gepushte oder uncommittete Änderungen werden gemeldet.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$(cd "$HERE/../.." && pwd)"
WEBUI="${WEBUI_DIR:-$BACKEND/../Hector9000WebUI}"
BACKEND_BRANCH="${BACKEND_BRANCH:-feature/congress-webui}"
WEBUI_BRANCH="${WEBUI_BRANCH:-feature/congress-design}"
OUT="${1:-$PWD/hector-update-payload}"

die() { echo "FEHLER: $*" >&2; exit 1; }
[ -d "$WEBUI/.git" ] || die "WebUI-Repo nicht gefunden: $WEBUI (WEBUI_DIR setzen)"
[ -d "$BACKEND/.git" ] || die "kein Git-Repo: $BACKEND"

check() {   # check REPO BRANCH
    local repo="$1" branch="$2"
    git -C "$repo" rev-parse --verify -q "$branch" >/dev/null || die "Branch $branch fehlt in $repo"
    if [ -n "$(git -C "$repo" status --porcelain --untracked-files=no)" ]; then
        echo "WARNUNG: uncommittete Änderungen in $repo - sie sind NICHT im Bundle" >&2
    fi
    if git -C "$repo" rev-parse --verify -q "origin/$branch" >/dev/null &&
       [ "$(git -C "$repo" rev-parse "$branch")" != "$(git -C "$repo" rev-parse "origin/$branch")" ]; then
        echo "WARNUNG: $branch in $repo ist nicht mit origin/$branch identisch (gepusht?)" >&2
    fi
}
check "$BACKEND" "$BACKEND_BRANCH"
check "$WEBUI" "$WEBUI_BRANCH"

mkdir -p "$OUT/hector-update"
cp "$HERE/run.sh" "$OUT/hector-update/run.sh"
git -C "$BACKEND" bundle create "$OUT/hector-update/Hector9000.bundle" "$BACKEND_BRANCH" >/dev/null 2>&1
git -C "$WEBUI" bundle create "$OUT/hector-update/Hector9000WebUI.bundle" "$WEBUI_BRANCH" >/dev/null 2>&1
printf 'backend-%s-webui-%s' "$(git -C "$BACKEND" rev-parse --short "$BACKEND_BRANCH")" \
    "$(git -C "$WEBUI" rev-parse --short "$WEBUI_BRANCH")" > "$OUT/hector-update/VERSION"

echo "fertig: $OUT/hector-update  (VERSION $(cat "$OUT/hector-update/VERSION"))"
du -sh "$OUT/hector-update" | cut -f1 | sed 's/^/Größe: /'
cat <<'TXT'

Nächste Schritte (Details in deploy/sd-update/README.md):
  1. hector-update/update.log der Karte sichern (Ergebnis des letzten Laufs).
  2. Ordner hector-update auf die Boot-Partition der Karte kopieren.
  3. Optional, empfohlen: die ORIGINALE cmdline.txt der Karte als hector-update/cmdline.txt.orig sichern.
  4. An cmdline.txt (eine Zeile!) den Trigger anhängen.
TXT
