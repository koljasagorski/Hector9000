#!/usr/bin/env bash
#
# Checks of a booted Hector9000 system. Runs INSIDE the running system (as root, in the chroot of
# the boot-test container, see boot-test.sh). Prints ok/FAIL lines, exit code 1 if anything failed.
# Secrets (the admin password) are never printed.
set -u

PASS=0 FAILS=0
ok()   { PASS=$((PASS + 1)); printf '  \033[32mok\033[0m    %s\n' "$*"; }
fail() { FAILS=$((FAILS + 1)); printf '  \033[31mFAIL\033[0m  %s\n' "$*"; }
section() { printf '\n\033[1m%s\033[0m\n' "$*"; }
check() { local what="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$what"; else fail "$what"; fi; }
PY=/opt/hector9000/venv/bin/python

section "System state"
state="$(systemctl is-system-running --wait 2>&1 | tail -1)"
[ "$state" = running ] && ok "systemd: running" || fail "systemd is '$state'"
failed="$(systemctl --failed --no-legend --plain | awk '{print $1}' | tr '\n' ' ')"
[ -z "$failed" ] && ok "no failed units" || fail "failed units: $failed"
for u in mosquitto hector-server hector-controller hector-stats; do
    check "$u is active" systemctl is-active --quiet "$u"
done
[ -s /etc/hector9000/firstboot.done ] && ok "hector-firstboot ran (flag file)" || fail "no /etc/hector9000/firstboot.done"
systemctl is-enabled --quiet hector-firstboot 2>/dev/null; true
journalctl -u hector-firstboot --no-pager -o cat 2>/dev/null | grep -q "done" && ok "hector-firstboot logged 'done'" || fail "hector-firstboot did not finish"
journalctl -u hector-firstboot --no-pager -o cat 2>/dev/null | grep -E "not mounted|ERROR|WARNING" | sed 's/^/        /' | head -3

section "Who runs what"
owner() { ps -o user= -p "$(systemctl show -p MainPID --value "$1")" 2>/dev/null | tr -d ' '; }
[ "$(owner hector-server)" = hector ] && ok "hector-server runs as hector" || fail "hector-server runs as '$(owner hector-server)'"
[ "$(owner hector-controller)" = hector ] && ok "hector-controller runs as hector" || fail "hector-controller runs as '$(owner hector-controller)'"
[ "$(owner hector-stats)" = hector-stats ] && ok "hector-stats runs as hector-stats" || fail "hector-stats runs as '$(owner hector-stats)'"
[ "$(owner mosquitto)" = mosquitto ] && ok "mosquitto runs as mosquitto" || fail "mosquitto runs as '$(owner mosquitto)'"
[ "$(id -u hector)" != 1000 ] && ok "hector has a UID other than 1000 (the Imager renames the UID-1000 user)" || fail "hector has UID 1000"

section "Network exposure"
listen="$(ss -ltnH 2>/dev/null | awk '{print $4}')"
for p in 1883 9001; do
    if echo "$listen" | grep -q "^127.0.0.1:$p$" && ! echo "$listen" | grep -qE "^(\*|0.0.0.0|\[::\]):$p$"; then
        ok "MQTT port $p listens on 127.0.0.1 only"
    else
        fail "MQTT port $p: $(echo "$listen" | grep ":$p$" | tr '\n' ' ')"
    fi
done
echo "$listen" | grep -qE "^(\*|0.0.0.0):8080$" && ok "dashboard listens on :8080" || fail "nothing on :8080 ($(echo "$listen" | tr '\n' ' '))"
other="$(echo "$listen" | grep -vE '^(127\.0\.0\.1|\[::1\]|127\.0\.0\.53%lo|127\.0\.0\.54):' | grep -v ':8080$' | tr '\n' ' ')"
[ -z "$other" ] && ok "no other TCP port is open to the network" || fail "other open ports: $other"

section "Dashboard"
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$@"; }
[ "$(code http://127.0.0.1:8080/healthz)" = 200 ] && ok "/healthz answers 200" || fail "/healthz did not answer 200"
[ "$(code http://127.0.0.1:8080/)" = 200 ] && ok "public page answers 200" || fail "public page did not answer 200"
[ "$(code http://127.0.0.1:8080/api/admin/overview)" = 401 ] && ok "admin API without login: 401" || fail "admin API without login is not 401"
[ "$(code http://127.0.0.1:8080/admin)" = 401 ] || [ "$(code http://127.0.0.1:8080/admin)" = 200 ] && ok "/admin reachable (login required for data)" || true
PWF=/var/lib/hector9000/stats/admin_password
if [ -s "$PWF" ]; then
    [ "$(stat -c '%U %a' "$PWF")" = "hector-stats 600" ] && ok "admin_password is hector-stats 0600" || fail "admin_password is $(stat -c '%U %a' "$PWF")"
    PW="$(cat "$PWF")"
    [ "$(code -u "admin:$PW" http://127.0.0.1:8080/api/admin/overview)" = 200 ] && ok "admin API with the password: 200" || fail "admin API with the password is not 200"
    [ "$(code -u "admin:wrong-$PW" http://127.0.0.1:8080/api/admin/overview)" = 401 ] && ok "admin API with a wrong password: 401" || fail "wrong password was not refused"
    curl -s --max-time 5 -u "admin:$PW" http://127.0.0.1:8080/api/admin/overview > /tmp/overview.json
    "$PY" - <<'PYEOF' && ok "overview: mqtt_exposed is [] (broker only local)" || fail "overview: mqtt_exposed is not [] - see below"
import json, sys
d = json.load(open("/tmp/overview.json"))
print("        mqtt_exposed =", d.get("mqtt_exposed", "<missing>"), file=sys.stderr)
sys.exit(0 if d.get("mqtt_exposed") == [] else 1)
PYEOF
    rm -f /tmp/overview.json
    hector-maint --password >/tmp/pw.out 2>&1; rc=$?
    [ $rc -eq 0 ] && grep -qF "Passwort: $PW" /tmp/pw.out && grep -q "Benutzer: admin" /tmp/pw.out \
        && ok "hector-maint --password shows user and password" || fail "hector-maint --password rc=$rc"
    rm -f /tmp/pw.out; unset PW
else
    fail "no admin password file"
fi

section "Files and rights"
st() { stat -c '%U:%G %a' "$1" 2>/dev/null; }
[ "$(st /var/lib/hector9000)" = "root:root 755" ] && ok "/var/lib/hector9000 root:root 0755" || fail "/var/lib/hector9000 is $(st /var/lib/hector9000)"
[ "$(st /var/lib/hector9000/hector)" = "hector:hector 751" ] && ok "hector/ hector 0751" || fail "hector/ is $(st /var/lib/hector9000/hector)"
[ "$(st /var/lib/hector9000/stats)" = "hector-stats:hector-stats 750" ] && ok "stats/ hector-stats 0750" || fail "stats/ is $(st /var/lib/hector9000/stats)"
[ -e /var/lib/hector9000/hector/h9k.db ] && ok "hardware database exists" || fail "h9k.db missing"
opts="$(findmnt -no OPTIONS /var/lib/hector9000 2>/dev/null)"
for o in nosuid nodev noexec; do echo ",$opts," | grep -q ",$o," && ok "data mount has $o" || fail "data mount lacks $o ($opts)"; done
runuser -u hector-stats -- test -r /var/lib/hector9000/hector/h9k.db && ok "hector-stats can read h9k.db" || fail "hector-stats cannot read h9k.db"
! runuser -u hector-stats -- test -w /var/lib/hector9000/hector && ok "hector-stats cannot write to hector/" || fail "hector-stats can write to hector/"
! runuser -u hector -- test -r /var/lib/hector9000/stats/admin_password && ok "hector cannot read the admin password" || fail "hector can read the admin password"
[ -f /etc/systemd/journald.conf.d/hector9000.conf ] || ls /etc/systemd/journald.conf.d/*.conf >/dev/null 2>&1 && ok "journald limit drop-in present" || fail "no journald drop-in"

section "Accounts and remote access"
pis="$(passwd -S pi 2>/dev/null | awk '{print $2}')"
case "$pis" in
    L|LK|NP) ok "the placeholder user 'pi' has no usable password ($pis); the Imager sets name and password" ;;
    "") ok "no user 'pi' in the image" ;;
    *) fail "the user 'pi' has a password ($pis)" ;;
esac
ssh_state="$(systemctl is-enabled ssh 2>&1 | tail -1)"
[ "$ssh_state" != enabled ] && ok "SSH is not enabled by default ($ssh_state)" || fail "SSH is enabled in the image"
[ -z "$(find /etc/ssh -name 'ssh_host_*' 2>/dev/null)" ] && ok "no SSH host keys baked into the image" || fail "SSH host keys exist"

section "sudo"
visudo -c >/dev/null 2>&1 && ok "visudo -c" || fail "visudo -c reports errors"
sl="$(runuser -u hector -- sudo -n -l 2>&1)"
echo "$sl" | grep -q "hector-clock\|date -u -s" && ok "hector may set the clock (restricted command)" || fail "hector has no clock rule: $(echo "$sl" | tail -3 | tr '\n' ' ')"
echo "$sl" | grep -q "(ALL) NOPASSWD: ALL" && fail "hector has unrestricted sudo" || ok "hector has no blanket sudo"
runuser -u hector -- sudo -n -l /bin/cat /etc/shadow >/dev/null 2>&1 && fail "hector may run cat /etc/shadow" || ok "hector may not run cat /etc/shadow"
runuser -u hector-stats -- sudo -n -l >/dev/null 2>&1 && fail "hector-stats has sudo rights" || ok "hector-stats has no sudo rights"
runuser -u hector -- sudo -n -l /usr/bin/date -u -s @1790000000 >/dev/null 2>&1 && ok "sudo allows exactly: date -u -s @<epoch>" || fail "sudo does not allow date -u -s @<epoch>"
runuser -u hector -- sudo -n -l /usr/bin/date -s "2020-01-01" >/dev/null 2>&1 && fail "sudo allows date with other arguments" || ok "sudo refuses date with other arguments"

section "Sandbox of the dashboard"
pid="$(systemctl show -p MainPID --value hector-stats)"
# as root inside the service's mount namespace and root: ProtectSystem=strict must stop even root
nsrun() { nsenter -t "$pid" -m -r -- "$@"; }
nsrun sh -c 'touch /etc/.probe' >/dev/null 2>&1 && fail "the dashboard's view lets root write to /etc" || ok "/etc is read-only in the dashboard's view"
nsrun sh -c 'touch /opt/hector9000/backend/.probe' >/dev/null 2>&1 && fail "the program folder is writable in the dashboard's view" || ok "the program folder is read-only in the dashboard's view"
nsrun sh -c 'touch /var/lib/hector9000/hector/.probe' >/dev/null 2>&1 && fail "the hardware data is writable in the dashboard's view" || ok "the hardware data is read-only in the dashboard's view"
nsrun sh -c 'touch /var/lib/hector9000/stats/.probe && rm /var/lib/hector9000/stats/.probe' >/dev/null 2>&1 && ok "the state folder is writable in the dashboard's view" || fail "the state folder is not writable in the dashboard's view"
nsrun sh -c 'ls /home/* ' >/dev/null 2>&1 && fail "/home is visible to the dashboard" || ok "/home is hidden from the dashboard (ProtectHome)"
grep -q '^NoNewPrivs:\s*1' "/proc/$pid/status" && ok "NoNewPrivileges is set for the dashboard" || fail "NoNewPrivileges not set"
[ "$(grep '^CapEff:' "/proc/$pid/status" | awk '{print $2}')" = 0000000000000000 ] && ok "the dashboard has no capabilities" || fail "the dashboard has capabilities"

section "Units"
for u in hector-server hector-controller hector-stats hector-kiosk hector-led hector-maint hector-firstboot; do
    out="$(systemd-analyze verify "/etc/systemd/system/$u.service" 2>&1 | grep -v -E "^$|Failed to create|dbus|Unit .* not found|Cannot add dependency" | grep -i -E "$u" | head -3)"
    [ -z "$out" ] && ok "systemd-analyze verify $u" || { fail "systemd-analyze verify $u"; echo "$out" | sed 's/^/        /'; }
done
exposure="$(systemd-analyze security hector-stats.service --no-pager 2>/dev/null | tail -1)"
echo "        $exposure"

section "MQTT round trip (controller + simulated hardware)"
CAPBND="$(grep '^CapBnd:' /proc/1/status | awk '{print $2}')"
TIME_BIT=$(( (16#$CAPBND >> 25) & 1 ))
[ "$TIME_BIT" -eq 0 ] && ok "the container cannot set the host clock (CAP_SYS_TIME dropped)" || fail "CAP_SYS_TIME is available - the clock test is skipped for safety"
"$PY" - "$TIME_BIT" <<'PYEOF'
import sys, time
import paho.mqtt.client as mqtt

time_bit = int(sys.argv[1])
got = []
c = mqtt.Client(client_id="boot-check")
c.on_message = lambda cl, ud, m: got.append((m.topic, m.payload.decode("utf-8", "replace")))
c.connect("127.0.0.1", 1883, 30)
c.subscribe("Hector9000/#")
c.loop_start()
time.sleep(1)

def wait(topic_part, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        for t, p in got:
            if topic_part in t:
                return t, p
        time.sleep(0.2)
    return None

def report(ok, text):
    print(("  \033[32mok\033[0m    " if ok else "  \033[31mFAIL\033[0m  ") + text)
    return ok

results = []
# 1. the controller is up and says so (retained status)
st = wait("status/controller", 3)
results.append(report(st is not None and st[1] == "online", "controller status is online: %r" % (st,)))

# 2. a bad clock value is refused with an error answer and an error event
del got[:]
c.publish("Hector9000/system/setTime", "not-a-number")
r = wait("system/setTime/return")
results.append(report(r is not None and r[1] == "error", "setTime with a bad value answers 'error': %r" % (r,)))
e = wait("event/error", 3)
results.append(report(e is not None, "an error event is published: %r" % (e,)))

# 3. a plausible value reaches sudo (and fails only because this container may not set the clock)
if time_bit == 0:
    del got[:]
    c.publish("Hector9000/system/setTime", "1790000000")
    r = wait("system/setTime/return")
    results.append(report(r is not None and r[1] == "error", "setTime 1790000000: the command ran and failed (no CAP_SYS_TIME): %r" % (r,)))
else:
    print("  skipped: valid setTime test (CAP_SYS_TIME present)")

# 4. a drink request reaches the simulated hardware
del got[:]
c.publish("Hector9000/get_drinks", "")
r = wait("get_drinks/return", 5)
results.append(report(r is not None, "get_drinks is answered by the controller: %r" % ((r[0], r[1][:60]) if r else None,)))
c.loop_stop()
sys.exit(0 if all(results) else 1)
PYEOF
[ $? -eq 0 ] && PASS=$((PASS + 1)) || FAILS=$((FAILS + 1))
if [ "$TIME_BIT" -eq 0 ]; then
    journalctl --no-pager -o cat _COMM=sudo 2>/dev/null | grep -q "COMMAND=/usr/bin/date -u -s @1790000000" \
        && ok "sudo log: hector ran 'date -u -s @1790000000' (rule applied)" \
        || fail "no sudo log line for the clock command"
    journalctl --no-pager -o cat _COMM=sudo 2>/dev/null | grep -q -E "not allowed|a password is required" \
        && fail "sudo refused something: $(journalctl --no-pager -o cat _COMM=sudo | grep -E 'not allowed|password is required' | head -2)" \
        || ok "sudo log: nothing was refused"
fi

printf '\n%d ok, %d FAILED\n' "$PASS" "$FAILS"
[ "$FAILS" -eq 0 ]
