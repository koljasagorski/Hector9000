#!/usr/bin/env bash
#
# Testet mqtt-guard.sh lokal mit Docker (kein Pi nötig): Zwei Container in einem Netz, der eine
# hat die Sperre und lauscht auf 1883, 9001 und 8080; der andere versucht, sich zu verbinden.
#
#   deploy/sd-update/test-mqtt-guard.sh [BACKEND ...]     (Standard: nft iptables)
#
# Braucht Docker mit NET_ADMIN-Rechten und Internet für "apt-get install nftables iptables".
# Es werden nur eigene Container/Netze/Images mit dem Präfix "hector-guard-test" angelegt und
# am Ende wieder entfernt.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMG=hector-guard-test
NET=hector-guard-test-net
A=hector-guard-test-a
B=hector-guard-test-b
BACKENDS=("$@"); [ ${#BACKENDS[@]} -gt 0 ] || BACKENDS=(nft iptables)

FAILS=0
ok()   { echo "  ok    $*"; }
fail() { echo "  FAIL  $*"; FAILS=$((FAILS + 1)); }

cleanup() {
    docker rm -f "$A" "$B" >/dev/null 2>&1
    docker network rm "$NET" >/dev/null 2>&1
}
trap cleanup EXIT
cleanup

echo "==> Image bauen (Debian bullseye wie der alte Pi)"
docker build -q -t "$IMG" - >/dev/null <<'DOCKERFILE' || { echo "Image konnte nicht gebaut werden"; exit 1; }
FROM python:3.9-slim-bullseye
# Bullseye is out of support: the packages only live on archive.debian.org now
RUN printf 'deb http://archive.debian.org/debian bullseye main\ndeb http://archive.debian.org/debian-security bullseye-security main\n' > /etc/apt/sources.list \
    && rm -f /etc/apt/sources.list.d/* \
    && apt-get -o Acquire::Check-Valid-Until=false update -qq \
    && apt-get install -y -qq --no-install-recommends nftables iptables iproute2 \
    && rm -rf /var/lib/apt/lists/*
DOCKERFILE

docker network create "$NET" >/dev/null || exit 1
docker run -d --name "$A" --network "$NET" --cap-add NET_ADMIN "$IMG" sleep 900 >/dev/null || exit 1
docker run -d --name "$B" --network "$NET" "$IMG" sleep 900 >/dev/null || exit 1

# Skript ohne Volume-Mount in den Container bringen
COPYFILE_DISABLE=1 tar -C "$HERE" -cf - mqtt-guard.sh | docker exec -i "$A" tar -xf - -C /usr/local/sbin
docker exec "$A" sh -c 'mv /usr/local/sbin/mqtt-guard.sh /usr/local/sbin/hector9000-mqtt-guard && chmod 755 /usr/local/sbin/hector9000-mqtt-guard'

for port in 1883 9001 8080; do
    docker exec -d "$A" python3 -m http.server "$port" --bind 0.0.0.0 >/dev/null
done
sleep 2
A_IP=$(docker inspect -f "{{(index .NetworkSettings.Networks \"$NET\").IPAddress}}" "$A")

probe() {   # probe CONTAINER HOST PORT  -> 0 = Verbindung klappt
    docker exec "$1" python3 -c "
import socket, sys
s = socket.socket(); s.settimeout(2)
try:
    s.connect((sys.argv[1], int(sys.argv[2])))
except OSError:
    sys.exit(1)
" "$2" "$3"
}
guard() { docker exec -e HECTOR_GUARD_BACKEND="$1" "$A" hector9000-mqtt-guard "$2"; }

for be in "${BACKENDS[@]}"; do
    echo "==> Backend: $be (Ziel $A_IP)"
    guard "$be" stop >/dev/null 2>&1

    for p in 1883 9001 8080; do
        probe "$B" "$A_IP" "$p" && ok "vorher: von außen $p erreichbar" || fail "vorher: von außen $p NICHT erreichbar (Testaufbau kaputt?)"
    done

    guard "$be" start; rc=$?
    [ $rc -eq 0 ] && ok "start endet mit 0" || fail "start endet mit $rc"
    guard "$be" start >/dev/null 2>&1; [ $? -eq 0 ] && ok "zweiter start (idempotent)" || fail "zweiter start scheitert"

    probe "$B" "$A_IP" 1883 && fail "nachher: von außen 1883 noch erreichbar" || ok "nachher: von außen 1883 gesperrt"
    probe "$B" "$A_IP" 9001 && fail "nachher: von außen 9001 noch erreichbar" || ok "nachher: von außen 9001 gesperrt"
    probe "$B" "$A_IP" 8080 && ok "nachher: von außen 8080 (Dashboard) weiter erreichbar" || fail "nachher: 8080 wurde mitgesperrt"
    probe "$A" 127.0.0.1 1883 && ok "nachher: localhost:1883 geht" || fail "nachher: localhost:1883 gesperrt"
    probe "$A" 127.0.0.1 9001 && ok "nachher: localhost:9001 geht" || fail "nachher: localhost:9001 gesperrt"
    probe "$A" "$A_IP" 1883 && ok "nachher: eigene LAN-Adresse:1883 geht (läuft über lo)" || fail "nachher: eigene LAN-Adresse:1883 gesperrt"
    guard "$be" status >/dev/null 2>&1; [ $? -eq 0 ] && ok "status meldet aktiv" || fail "status meldet nicht aktiv"

    case "$be" in
        nft)      n=$(docker exec "$A" nft list table inet hector_guard | grep -c "drop") ;;
        iptables) n=$(docker exec "$A" iptables -S INPUT | grep -c "HECTOR_GUARD") ;;
    esac
    [ "$n" = 1 ] && ok "Regeln nicht doppelt ($n)" || fail "Regeln doppelt oder fehlen ($n)"

    guard "$be" stop >/dev/null 2>&1
    probe "$B" "$A_IP" 1883 && ok "nach stop: 1883 wieder offen" || fail "nach stop: 1883 immer noch gesperrt"
    guard "$be" status >/dev/null 2>&1; [ $? -eq 3 ] && ok "status meldet nach stop 'nicht aktiv' (Exit 3)" || fail "status nach stop falsch"
done

echo "==> Ohne Werkzeug"
docker exec -e HECTOR_GUARD_BACKEND=nft -e PATH=/nonexistent "$A" /bin/bash /usr/local/sbin/hector9000-mqtt-guard start >/dev/null 2>&1
[ $? -eq 1 ] && ok "ohne nft/iptables: Exit 1 mit Warnung" || fail "ohne Werkzeug nicht Exit 1"

echo
[ $FAILS -eq 0 ] && echo "Ergebnis: alles ok" || echo "Ergebnis: $FAILS Fehler"
exit $((FAILS > 0))
