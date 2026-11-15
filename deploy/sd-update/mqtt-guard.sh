#!/usr/bin/env bash
#
# hector9000-mqtt-guard - sperrt die MQTT-Ports (1883 und 9001) für das Netzwerk.
#
# Auf dem alten Pi lauscht Mosquitto auf allen Schnittstellen und nimmt anonyme Verbindungen an.
# Alle Hector-Programme sprechen aber nur mit "localhost". Dieses Skript lässt deshalb nur
# Verbindungen über die Loopback-Schnittstelle durch (auch die zur eigenen LAN-Adresse laufen
# im Kernel darüber) und verwirft alles andere auf diesen beiden Ports. Die Mosquitto-Konfiguration
# bleibt unangetastet.
#
#   hector9000-mqtt-guard start|stop|status
#
# Es wird nftables benutzt, sonst iptables/ip6tables. Gibt es keines von beiden, endet das Skript
# mit einer Warnung und Exit-Code 1, damit der Dienst als "failed" sichtbar bleibt.
# Zum Testen lässt sich das Verfahren mit HECTOR_GUARD_BACKEND=nft|iptables erzwingen.
set -u

PORTS_NFT="1883, 9001"
PORTS_IPT="1883,9001"
TABLE="hector_guard"
CHAIN="HECTOR_GUARD"

have() { command -v "$1" >/dev/null 2>&1; }
warn() { echo "hector9000-mqtt-guard: $*" >&2; }

backend() {
    case "${HECTOR_GUARD_BACKEND:-auto}" in
        nft)      have nft && { echo nft; return; } ;;
        iptables) have iptables && { echo iptables; return; } ;;
        auto)
            if have nft; then echo nft; return; fi
            if have iptables; then echo iptables; return; fi ;;
    esac
    echo none
}

# ---- nftables -------------------------------------------------------------------------
nft_start() {
    nft delete table inet "$TABLE" 2>/dev/null
    nft -f - <<EOF
table inet $TABLE {
    chain input {
        type filter hook input priority -10; policy accept;
        iifname "lo" accept
        tcp dport { $PORTS_NFT } drop
    }
}
EOF
}
nft_stop()   { nft delete table inet "$TABLE" 2>/dev/null; return 0; }
nft_active() { nft list table inet "$TABLE" >/dev/null 2>&1; }

# ---- iptables (IPv4 und IPv6) -----------------------------------------------------------
ipt_clear() {
    local tool
    for tool in iptables ip6tables; do
        have "$tool" || continue
        while "$tool" -D INPUT -j "$CHAIN" 2>/dev/null; do :; done
        "$tool" -F "$CHAIN" 2>/dev/null
        "$tool" -X "$CHAIN" 2>/dev/null
    done
    return 0
}
ipt_start() {
    ipt_clear
    local tool rc=0
    for tool in iptables ip6tables; do
        have "$tool" || continue
        "$tool" -N "$CHAIN" &&
        "$tool" -A "$CHAIN" -i lo -j RETURN &&
        "$tool" -A "$CHAIN" -p tcp -m multiport --dports "$PORTS_IPT" -j DROP &&
        "$tool" -I INPUT 1 -j "$CHAIN" || rc=1
    done
    return $rc
}
ipt_stop()   { ipt_clear; }
ipt_active() { iptables -C INPUT -j "$CHAIN" 2>/dev/null; }

# ---- Kommandos --------------------------------------------------------------------------
B=$(backend)
P=$B; [ "$B" = iptables ] && P=ipt     # Präfix der Funktionen oben
cmd="${1:-status}"

if [ "$B" = none ] && [ "$cmd" != status ] && [ "$cmd" != stop ]; then
    warn "WARNING: weder nftables (nft) noch iptables gefunden - die MQTT-Ports sind NICHT gesperrt"
    exit 1
fi

case "$cmd" in
    start)
        if "${P}_start"; then
            echo "hector9000-mqtt-guard: MQTT-Ports $PORTS_IPT sind für das Netzwerk gesperrt ($B), localhost bleibt frei"
        else
            warn "WARNING: Regeln konnten nicht gesetzt werden ($B) - die MQTT-Ports sind eventuell NICHT gesperrt"
            exit 1
        fi
        ;;
    stop)
        [ "$B" = none ] || "${P}_stop"
        echo "hector9000-mqtt-guard: Sperre entfernt"
        ;;
    status)
        if [ "$B" != none ] && "${P}_active"; then
            echo "aktiv ($B): MQTT-Ports $PORTS_IPT sind nur über localhost erreichbar"
        else
            echo "NICHT aktiv"
            exit 3
        fi
        ;;
    *)
        echo "Aufruf: hector9000-mqtt-guard start|stop|status" >&2
        exit 2
        ;;
esac
