#!/usr/bin/env python3
# -*- coding: utf8 -*-
##
#   StatsServer.py     Statistics, public stats page and admin dashboard
#
#   One small service next to the Hector server/controller/LED server:
#
#     MQTT  ->  StatsRecorder  ->  stats.db (SQLite)  ->  HTTP
#                                   |
#                      Watchdog --> Notifier --> Matrix chat (optional)
#
#   HTTP (default port 8080, see below for the environment):
#     /                    public statistics page, no login, read-only
#     /api/stats           the same data as JSON
#     /admin               admin dashboard (HTTP Basic login, user "admin")
#     /api/admin/...       admin API (overview, actions, CSV export)
#     /healthz             "ok" for monitoring
#
#   Environment (all optional):
#     HECTOR_STATE_DIR        where stats.db and admin_password live (default: .)
#     HECTOR_STATS_PORT       default 8080          HECTOR_STATS_BIND  default 0.0.0.0
#     HECTOR_ADMIN_PASSWORD   otherwise generated once and stored in the state dir
#     HECTOR_ADMIN_REMOTE     1 (default) = /admin reachable from the network, 0 = only from
#                             this machine (use an SSH tunnel; the login is plain HTTP otherwise)
#     HECTOR_MQTT_HOST        default localhost
#     HECTOR_DB               h9k.db, only read to learn the bottle names
#                             (default: $HECTOR_STATE_DIR/h9k.db, then ./h9k.db)
#     HECTOR_EXPECT_PROCESSES comma list of server,controller,led ("" = don't check)
#     HECTOR_ENV_FILE         KEY=VALUE file, default /etc/hector9000/hector.env
#     HECTOR_MATRIX_*         Matrix bot, see utils/Notifier.py and docs/matrix.md. The admin
#                             dashboard can set these too (stored in $HECTOR_STATE_DIR/notify.json,
#                             which then wins over the environment)
#
#   Usage: python3 Hector9000/StatsServer.py [--demo] [--port N]
#          --demo  fake data and no MQTT, to look at the pages
#

import argparse
import base64
import csv
import hmac
import io
import ipaddress
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# `python3 Hector9000/StatsServer.py` puts this folder, not the repository, on the
# import path - make the package importable even if it was never pip-installed.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from Hector9000.utils import Events, Notifier, NotifySettings, SystemHealth  # noqa: E402
from Hector9000.utils.StatsRecorder import StatsRecorder  # noqa: E402
from Hector9000.utils.StatsStore import StatsStore  # noqa: E402
from Hector9000.utils.Watchdog import Watchdog  # noqa: E402

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MAX_BODY = 4096
STATS_CACHE_S = 5
LOGIN_FAILS = 5              # per minute and address, then 429
GLOBAL_LOGIN_FAILS = 20      # per minute over all addresses, then 429 for strangers
TRUST_S = 3600               # an address that logged in successfully is exempt from the global limit this long
MAX_CONNECTIONS = 32         # simultaneous HTTP requests, more get a 503
REQUEST_DEADLINE = 30        # seconds for one whole request, however slowly it is sent
MIN_PASSWORD_LENGTH = 12
# "Set clock": must match the pattern in deploy/sudoers.d/hector9000 (@1[7-9] + 8 digits)
SET_TIME_MIN, SET_TIME_MAX = 1700000000, 1999999999      # Nov 2023 .. May 2033
LED_MODES = ("theme", "random", "idle", "off")

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8", ".json": "application/json",
    ".woff2": "font/woff2", ".woff": "font/woff", ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8", ".ico": "image/x-icon", ".png": "image/png",
}

PAGES = {"/": "public.html", "/admin": "admin.html"}

# admin action -> (MQTT topic, payload, retained)
MQTT_ACTIONS = {
    "reboot": ("Hector9000/system/reboot", "true", False),
    "kiosk_exit": ("Hector9000/system/kioskExit", "true", False),
    "clean": ("Hector9000/cleanMe", "true", False),
    "dry": ("Hector9000/dryMe", "true", False),
    "open_valves": ("Hector9000/openAllValves", "true", False),
    "close_valves": ("Hector9000/closeAllValves", "true", False),
}
BUSY_BLOCKS = ("clean", "dry", "open_valves", "reboot")   # not while a drink is being made

DEFAULT_SET_TIME = ["sudo", "-n", "date", "-u", "-s", "@{epoch}"]


def log(message):
    print("StatsServer: " + str(message), flush=True)


def load_env_file(path):
    """Read KEY=VALUE lines into os.environ without overriding what is already set."""
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))
    except OSError:
        pass


def load_admin_password(state_dir, env=None):
    env = os.environ if env is None else env
    if env.get("HECTOR_ADMIN_PASSWORD"):
        if len(env["HECTOR_ADMIN_PASSWORD"]) < MIN_PASSWORD_LENGTH:
            log("WARNING: HECTOR_ADMIN_PASSWORD has fewer than %d characters - "
                "anybody on the network can try to guess it" % MIN_PASSWORD_LENGTH)
        return env["HECTOR_ADMIN_PASSWORD"]
    path = os.path.join(state_dir, "admin_password")
    try:
        with open(path) as f:
            value = f.read().strip()
        if value:
            return value
    except OSError:
        pass
    value = secrets.token_urlsafe(12)
    os.makedirs(state_dir, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(value + "\n")
    log("admin password created and stored in %s" % path)
    return value


def read_bottle_names(db_path):
    """code -> name from the hardware DB (read only; the file belongs to the hardware setup)."""
    names = {}
    if not db_path or not os.path.exists(db_path):
        return names
    try:
        con = sqlite3.connect("file:%s?mode=ro" % urllib.parse.quote(os.path.abspath(db_path)),
                              uri=True, timeout=2)
        for code, name in con.execute(
                "SELECT s.Code, i.Name FROM Servos s LEFT JOIN Ingredients i ON i.Code = s.Code"):
            names[code] = name or code
        con.close()
    except sqlite3.Error:
        pass
    return names


def find_hardware_db(env, state_dir):
    """h9k.db: explicit HECTOR_DB, else the state dir (slim OS), else the working dir (old setup)."""
    if env.get("HECTOR_DB"):
        return env["HECTOR_DB"]
    for candidate in (os.path.join(state_dir, "h9k.db"), "h9k.db"):
        if os.path.exists(candidate):
            return candidate
    return None


class StatsApp:
    """All behaviour of the service without sockets, so it can be tested directly."""

    def __init__(self, store, recorder, notifier, publish, admin_password, health=None,
                 bottle_names=None, backend_dir=REPO_DIR, webui_dir=None, set_time_cmd=None,
                 clock=time.time, web_dir=WEB_DIR, admin_remote=True, state_dir=None, env=None):
        self.store, self.recorder, self.notifier = store, recorder, notifier
        self.state_dir = state_dir            # where notify.json lives; None = settings not editable
        self.env = dict(os.environ if env is None else env)
        self._notify_lock = threading.Lock()
        self.publish = publish
        self.password = admin_password
        self.health = health or SystemHealth.snapshot
        self.bottle_names = bottle_names or (lambda: {})
        self.backend_dir, self.webui_dir = backend_dir, webui_dir
        self.set_time_cmd = set_time_cmd or DEFAULT_SET_TIME
        self.clock = clock
        self.web_dir = web_dir
        self._cache = (0, None)
        self._fails = {}
        self._fails_all = []
        self._trusted = {}                    # ip -> time of its last successful login
        self.admin_remote = admin_remote      # False: /admin and /api/admin only from this machine
        self.processes = None
        self.synced = None
        self.last_health = {}
        self.expected_processes = None
        self.watchdog = None

    # ------------------------------------------------------------- auth ---
    def _blocked(self, ip, now):
        for key in [k for k, v in self._fails.items() if not any(now - t < 60 for t in v)]:
            del self._fails[key]                       # forget addresses that behave again
        self._fails_all = [t for t in self._fails_all if now - t < 60]
        stamps = [t for t in self._fails.get(ip, []) if now - t < 60]
        if stamps:
            self._fails[ip] = stamps
        if len(stamps) >= LOGIN_FAILS:
            return True
        if len(self._fails_all) >= GLOBAL_LOGIN_FAILS:
            # somebody is guessing from many addresses: lock out strangers, but not the admin's
            # own device (it proved the password recently) and not this machine (SSH tunnel)
            self._trusted = {k: t for k, t in self._trusted.items() if now - t < TRUST_S}
            return ip not in self._trusted and not self._is_local(ip)
        return False

    @staticmethod
    def _is_local(ip):
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        if getattr(addr, "ipv4_mapped", None):
            addr = addr.ipv4_mapped
        return addr.is_loopback

    def authorized(self, headers, ip):
        """True, False (wrong/missing) or None (too many attempts)."""
        now = self.clock()
        if self._blocked(ip, now):
            return None
        raw = headers.get("Authorization", "")
        if raw.startswith("Basic "):
            try:
                user, _, pw = base64.b64decode(raw[6:], validate=True).decode("utf-8").partition(":")
            except (ValueError, UnicodeDecodeError):
                user, pw = "", ""
            ok_user = hmac.compare_digest(user.encode(), b"admin")
            ok_pw = hmac.compare_digest(pw.encode(), self.password.encode())
            if ok_user and ok_pw:
                self._trusted[ip] = now
                return True
            self._fails.setdefault(ip, []).append(now)
            self._fails_all.append(now)
        return False

    # ----------------------------------------------------------- helpers --
    @staticmethod
    def _json(obj, status=200):
        body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return status, {"Content-Type": "application/json; charset=utf-8"}, body

    @staticmethod
    def _text(text, status=200, ctype="text/plain; charset=utf-8", extra=None):
        headers = {"Content-Type": ctype}
        headers.update(extra or {})
        return status, headers, text.encode("utf-8") if isinstance(text, str) else text

    def _static(self, rel):
        parts = rel.split("/")
        if (not rel or any(p in ("", ".", "..") or p.startswith(".") for p in parts)
                or len(parts) > 2 or "\\" in rel):
            return self._text("not found", 404)
        path = os.path.join(self.web_dir, *parts)
        ext = os.path.splitext(path)[1]
        if ext not in CONTENT_TYPES or not os.path.isfile(path):
            return self._text("not found", 404)
        with open(path, "rb") as f:
            data = f.read()
        return 200, {"Content-Type": CONTENT_TYPES[ext],
                     "Cache-Control": "public, max-age=300"}, data

    # ------------------------------------------------------------- data ---
    def machine_public(self):
        h = self.last_health or {}
        return {"uptime_s": h.get("uptime"), "cpu_temp_c": h.get("cpu_temp"),
                "load1": h.get("load1"), "mem_pct": h.get("mem_pct"), "disk_pct": h.get("disk_pct")}

    def public_stats(self, now=None):
        now = self.clock() if now is None else now
        stamp, data = self._cache
        if data is None or now - stamp > STATS_CACHE_S:
            data = self.store.public_stats(now, health=self.machine_public())
            self._cache = (now, data)
        out = dict(data)
        out["live"] = dict(self.recorder.live)
        out["clock"] = {"server_time": now, "synced": self.synced}
        return out

    def overview(self, now=None):
        now = self.clock() if now is None else now
        h = self.last_health or {}
        samples = self.store.samples_since(now - 6 * 3600)
        if len(samples) > 360:
            step = len(samples) / 360.0
            samples = [samples[int(i * step)] for i in range(360)]
        known = self.bottle_names()
        return {
            "stats": self.public_stats(now),
            "live": dict(self.recorder.live),
            "services": self.recorder.services,
            "processes": self.processes,
            "health": dict(h, throttle_reasons=SystemHealth.throttle_reasons(h.get("throttled"))),
            "ips": SystemHealth.ip_addresses(),
            "versions": {"backend": SystemHealth.git_revision(self.backend_dir),
                         "webui": SystemHealth.git_revision(self.webui_dir)},
            "clock": {"server_time": now, "synced": self.synced},
            "errors": self.store.recent_errors(100),
            "events": self.store.recent_events(100),
            "doses": self.store.recent_doses(50),
            "bottles": self.store.bottle_levels(now, known),
            "samples": samples,
            "notifier": self.notifier.status(),
            "notify_config": self.notify_config(),
            "messages": self.recorder.messages,
            "actions": sorted(list(MQTT_ACTIONS) + [
                "led_mode", "refill", "set_bottle", "ack_errors", "test_notification", "set_time",
                "save_notify", "reset_notify", "join_matrix"]),
        }

    # ---------------------------------------------------------- actions ---
    def action(self, name, args, now=None):
        now = self.clock() if now is None else now
        busy = self.recorder.live.get("state") == "dosing"
        if name in BUSY_BLOCKS and busy:
            return self._json({"ok": False, "error": "Es wird gerade ein Drink zubereitet."}, 409)
        if name in MQTT_ACTIONS:
            topic, payload, retained = MQTT_ACTIONS[name]
            self.publish(topic, payload, retained)
            self.store.add_event(now, "admin", name)
            return self._json({"ok": True})
        if name == "led_mode":
            mode = str(args.get("mode", ""))
            if mode not in LED_MODES:
                return self._json({"ok": False, "error": "unbekannter Modus"}, 400)
            self.publish("Hector9000/LEDStrip/ambient", mode, True)
            self.store.add_event(now, "admin", "led_mode " + mode)
            return self._json({"ok": True})
        if name in ("refill", "set_bottle"):
            code = str(args.get("code", ""))[:50]
            if not code:
                return self._json({"ok": False, "error": "code fehlt"}, 400)
            try:
                capacity = int(args["capacity_ml"]) if args.get("capacity_ml") not in (None, "") else None
            except (TypeError, ValueError):
                return self._json({"ok": False, "error": "capacity_ml ungültig"}, 400)
            if capacity is not None and not 50 <= capacity <= 5000:
                return self._json({"ok": False, "error": "capacity_ml muss 50..5000 sein"}, 400)
            name_arg = args.get("name")
            if name == "refill":
                self.store.refill_bottle(code, now, capacity)
            else:
                self.store.set_bottle(code, capacity, name_arg and str(name_arg)[:100])
            self.store.add_event(now, "admin", "%s %s" % (name, code))
            return self._json({"ok": True})
        if name == "ack_errors":
            ids = args.get("ids")
            self.store.ack_errors([int(i) for i in ids][:200] if isinstance(ids, list) else None)
            return self._json({"ok": True})
        if name == "test_notification":
            return self._json({"ok": self.notifier.test_message(),
                               "error": None if self.notifier.enabled else "Matrix ist nicht konfiguriert"})
        if name == "set_time":
            return self._set_time(args, now)
        if name in ("save_notify", "reset_notify", "join_matrix"):
            return self._notify_action(name, args, now)
        return self._json({"ok": False, "error": "unbekannte Aktion"}, 404)

    # ------------------------------------------------- notification settings --
    def notify_config(self):
        """The Matrix settings for the dashboard. The token is never part of it."""
        cfg = NotifySettings.describe(
            NotifySettings.load(self.state_dir) if self.state_dir else None, self.env)
        cfg["editable"] = bool(self.state_dir)
        return cfg

    def _apply_notify(self, settings):
        """Build the backends for these settings (None = environment only) and swap them in.
        Raises ValueError if they cannot be built; nothing changes in that case."""
        merged = NotifySettings.merge_env(self.env, settings)
        backends = Notifier.backends_from_env(merged)
        self.notifier.reconfigure(backends, merged.get("HECTOR_NOTIFY_LEVEL", "warning"))

    def _notify_action(self, name, args, now):
        if not self.state_dir:
            return self._json({"ok": False, "error": "Einstellungen sind hier nicht speicherbar"}, 400)
        with self._notify_lock:
            if name == "join_matrix":
                backend = next((b for b in self.notifier.backends if hasattr(b, "join")), None)
                if backend is None:
                    return self._json({"ok": False, "error": "Matrix ist nicht eingerichtet"}, 400)
                try:
                    backend.join()
                except (Notifier.PermanentError, Notifier.TemporaryError) as e:
                    return self._json({"ok": False, "error": str(e)}, 502)
                self.store.add_event(now, "admin", "join_matrix")
                return self._json({"ok": True})
            try:
                if name == "reset_notify":
                    self._apply_notify(None)             # may refuse: then the file stays as it is
                    NotifySettings.delete(self.state_dir)
                    self.store.add_event(now, "admin", "reset_notify")
                    return self._json({"ok": True, "config": self.notify_config()})
                current = dict(NotifySettings.load(self.state_dir) or {})
                if not current.get("token"):          # set up in hector.env so far: keep that token
                    current["token"] = self.env.get("HECTOR_MATRIX_TOKEN", "")
                clean, error = NotifySettings.validate(
                    args, current, allow_http=self.env.get("HECTOR_MATRIX_ALLOW_HTTP") == "1")
                if error:
                    return self._json({"ok": False, "error": error}, 400)
                self._apply_notify(clean)            # builds first: a failure leaves the file alone
                NotifySettings.save(self.state_dir, clean)
            except ValueError as e:
                return self._json({"ok": False, "error": str(e)}, 400)
            except OSError as e:
                return self._json({"ok": False, "error": "Konnte nicht speichern: %s" % e}, 500)
            self.store.add_event(now, "admin", "save_notify")     # never the token
            return self._json({"ok": True, "config": self.notify_config()})

    def _set_time(self, args, now):
        try:
            epoch = int(args["epoch"])
        except (KeyError, TypeError, ValueError):
            return self._json({"ok": False, "error": "epoch fehlt"}, 400)
        if not SET_TIME_MIN <= epoch <= SET_TIME_MAX:
            return self._json({"ok": False, "error": "unplausible Uhrzeit"}, 400)
        cmd = [part.replace("{epoch}", str(epoch)) for part in self.set_time_cmd]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError) as e:
            return self._json({"ok": False, "error": "Uhr konnte nicht gestellt werden: %s" % e}, 500)
        if res.returncode != 0:
            return self._json({"ok": False, "error": (res.stderr or "Befehl fehlgeschlagen").strip()[:200]}, 500)
        self.store.add_event(epoch, "admin", "set_time (war %d s daneben)" % int(epoch - now))
        return self._json({"ok": True})

    def export_csv(self, table):
        try:
            rows = self.store.export_rows(table)
        except ValueError:
            return self._text("unknown table", 404)
        out = io.StringIO()
        if rows:
            w = csv.DictWriter(out, fieldnames=list(rows[0].keys()))
            w.writeheader()
            for r in rows:
                # keep spreadsheet programs from running text as formulas
                w.writerow({k: ("'" + v if isinstance(v, str) and v[:1] in "=+-@" else v)
                            for k, v in r.items()})
        return self._text(out.getvalue(), 200, "text/csv; charset=utf-8",
                          {"Content-Disposition": 'attachment; filename="hector-%s.csv"' % table})

    # ----------------------------------------------------------- routing --
    def route(self, method, raw_path, headers, body=b"", ip="-"):
        """Returns (status, headers, body bytes). Never raises."""
        try:
            status, hdrs, data = self._route(method, raw_path, headers, body, ip)
        except Exception as e:      # pragma: no cover - last line of defence
            log("request failed: %r" % e)
            status, hdrs, data = self._json({"error": "internal error"}, 500)
        hdrs = dict(hdrs)
        hdrs.setdefault("Cache-Control", "no-store")
        hdrs["X-Content-Type-Options"] = "nosniff"
        hdrs["X-Frame-Options"] = "DENY"
        hdrs["Referrer-Policy"] = "no-referrer"
        hdrs["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        return status, hdrs, data

    def _route(self, method, raw_path, headers, body, ip):
        parsed = urllib.parse.urlparse(raw_path)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)
        if method not in ("GET", "POST"):
            return self._text("method not allowed", 405)
        is_admin = path == "/admin" or path.startswith("/api/admin/")
        if is_admin:
            if not self.admin_remote and not self._is_local(ip):
                return self._text("Admin nur von diesem Gerät aus (HECTOR_ADMIN_REMOTE=0)", 403)
            auth = self.authorized(headers, ip)
            if auth is None:
                return self._text("Zu viele Versuche - bitte eine Minute warten.", 429,
                                  extra={"Retry-After": "60"})
            if not auth:
                return self._text("Anmeldung erforderlich", 401,
                                  extra={"WWW-Authenticate": 'Basic realm="Hector9000 Admin", charset="UTF-8"'})
        if method == "GET":
            if path in PAGES:
                status, hdrs, data = self._static(PAGES[path])
                return status, dict(hdrs, **{"Cache-Control": "no-store"}), data
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if path == "/api/stats":
                return self._json(self.public_stats())
            if path == "/healthz":
                return self._text("ok")
            if path == "/api/admin/overview":
                return self._json(self.overview())
            if path == "/api/admin/export":
                return self.export_csv((query.get("table") or [""])[0])
            return self._text("not found", 404)
        # POST: only the admin action endpoint
        if path != "/api/admin/action":
            return self._text("not found", 404)
        if headers.get("X-Hector-Admin") != "1" or \
                "application/json" not in headers.get("Content-Type", ""):
            return self._text("forbidden", 403)
        origin, host = headers.get("Origin"), headers.get("Host", "")
        if origin and urllib.parse.urlparse(origin).netloc != host:
            return self._text("forbidden", 403)
        if len(body) > MAX_BODY:
            return self._text("too large", 413)
        try:
            args = json.loads(body.decode("utf-8") or "{}")
            if not isinstance(args, dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError, RecursionError):
            return self._json({"ok": False, "error": "ungültiges JSON"}, 400)
        return self.action(str(args.pop("action", "")), args)

    # ----------------------------------------------------------- upkeep ---
    def sample_health(self, now):
        """Once a minute: read vitals, store them, run the watchdog."""
        h = self.health()
        self.last_health = h
        self.synced = SystemHealth.clock_synced()
        self.processes = SystemHealth.running_processes() if self.expected_processes else None
        self.store.add_sample(now, h.get("cpu_temp"), h.get("load1"), h.get("mem_pct"),
                              h.get("disk_pct"), h.get("throttled"), h.get("uptime"))
        if self.watchdog:
            self.watchdog.check(h, self.processes,
                                self.store.bottle_levels(now, self.bottle_names()), now)


class LimitedHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer with a cap on simultaneous requests (one thread each), so a
    flood of slow or idle connections from the Wi-Fi cannot eat the Pi's memory."""
    daemon_threads = True

    def __init__(self, address, handler, max_connections=MAX_CONNECTIONS):
        super().__init__(address, handler)
        self._slots = threading.BoundedSemaphore(max_connections)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nRetry-After: 5\r\n"
                                b"Connection: close\r\nContent-Length: 0\r\n\r\n")
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def handle_error(self, request, client_address):
        # a client that hangs up or is too slow is normal on Wi-Fi: no traceback per drop
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError, socket.timeout)):
            return
        log("request from %s failed: %r" % (client_address[0], exc))


class Handler(BaseHTTPRequestHandler):
    server_version = "Hector9000"
    timeout = 15              # per read; REQUEST_DEADLINE below limits the whole request

    def setup(self):
        super().setup()
        # one request per connection (HTTP/1.0), so this is the total time a client gets
        self._deadline = threading.Timer(REQUEST_DEADLINE, self._abort)
        self._deadline.daemon = True
        self._deadline.start()

    def finish(self):
        self._deadline.cancel()
        super().finish()

    def _abort(self):
        try:
            self.request.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def log_message(self, fmt, *args):       # quiet: the Pi's SD card is not a log disk
        pass

    def _handle(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:                      # read(-1) would read until the client stops
            self.send_error(400)
            return
        if length > MAX_BODY:
            self.send_error(413)
            return
        body = self.rfile.read(length) if length and self.command == "POST" else b""
        status, headers, data = self.server.app.route(
            self.command, self.path, self.headers, body, self.client_address[0])
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    do_GET = do_POST = _handle


def build_app(args, env=None):
    env = os.environ if env is None else env
    state_dir = env.get("HECTOR_STATE_DIR", ".")
    store = StatsStore(":memory:" if args.demo else os.path.join(state_dir, "stats.db"))
    demo_dir = tempfile.TemporaryDirectory(prefix="hector-demo-") if args.demo else None
    settings_dir = demo_dir.name if demo_dir else state_dir      # the demo must not write real settings
    stored = NotifySettings.load(settings_dir)
    merged = NotifySettings.merge_env(env, stored)
    try:
        backends = Notifier.backends_from_env(merged)
    except ValueError as e:
        log("WARNING: Matrix settings unusable, notifications are off: %s" % e)
        backends = []
    notifier = Notifier.Notifier(backends, min_level=merged.get("HECTOR_NOTIFY_LEVEL", "warning"))
    publisher = {"fn": lambda topic, payload, retained=False: log("demo publish %s=%s" % (topic, payload))}

    def publish(topic, payload, retained=False):
        publisher["fn"](topic, payload, retained)

    recorder = StatsRecorder(
        store,
        on_error=lambda level, source, message: notifier.notify(
            level, message, key="err:%s:%s" % (source, message), source=source, untrusted=True))
    app = StatsApp(
        store, recorder, notifier, publish, load_admin_password(state_dir, env),
        bottle_names=lambda: read_bottle_names(find_hardware_db(env, state_dir)),
        admin_remote=env.get("HECTOR_ADMIN_REMOTE", "1").strip().lower() not in ("0", "false", "no", "off"),
        state_dir=settings_dir, env=env,
        webui_dir=env.get("HECTOR_WEBUI_DIR") or next(
            (d for d in (os.path.join(os.path.dirname(REPO_DIR), "Hector9000WebUI"),
                         "/opt/hector9000/webui") if os.path.isdir(d)), None))
    default_expect = "" if args.demo else "server,controller,led"
    wanted = [n for n in env.get("HECTOR_EXPECT_PROCESSES", default_expect).split(",") if n]
    app.expected_processes = wanted
    app.watchdog = Watchdog(
        notifier.notify, notifier.resolve, wanted if wanted else None)
    app._publisher = publisher
    app._demo_dir = demo_dir                  # keeps the demo's temporary folder alive
    return app


def run_mqtt(app, host):
    import paho.mqtt.client as mqtt
    client = mqtt.Client(client_id="HectorStats")

    def on_connect(c, userdata, flags, rc):
        log("connected to MQTT (rc=%s)" % rc)
        c.subscribe("Hector9000/#")
        c.publish(Events.TOPIC_STATUS + "stats", "online", retain=True)

    def on_message(c, userdata, msg):
        app.recorder.handle(msg.topic, msg.payload, time.time(), bool(msg.retain))

    client.on_connect, client.on_message = on_connect, on_message
    client.will_set(Events.TOPIC_STATUS + "stats", "offline", retain=True)
    client.connect_async(host, 1883, 60)
    client.loop_start()
    app._publisher["fn"] = lambda topic, payload, retained=False: client.publish(
        topic, payload, retain=retained)
    return client


def main(argv=None):
    parser = argparse.ArgumentParser(description="Hector9000 statistics and admin dashboard")
    parser.add_argument("--demo", action="store_true", help="fake data, no MQTT")
    parser.add_argument("--port", type=int)
    args = parser.parse_args(argv)

    load_env_file(os.environ.get("HECTOR_ENV_FILE", "/etc/hector9000/hector.env"))
    app = build_app(args)
    now = time.time()
    if args.demo:
        from Hector9000.utils.StatsDemo import seed_demo
        seed_demo(app.store, now)
        app.last_health = SystemHealth.snapshot()
        log("DEMO mode: fake data (admin password: HECTOR_ADMIN_PASSWORD, or the file admin_password in the state dir)")
    else:
        app.store.abort_running(now)
        app.store.add_event(now, "boot", "")
        run_mqtt(app, os.environ.get("HECTOR_MQTT_HOST", "localhost"))
        app.notifier.start()
        app.notifier.notify("info", "Statistik-Dienst gestartet", key="boot", source="stats")

    port = args.port or int(os.environ.get("HECTOR_STATS_PORT", "8080"))
    server = LimitedHTTPServer((os.environ.get("HECTOR_STATS_BIND", "0.0.0.0"), port), Handler)
    server.app = app
    threading.Thread(target=server.serve_forever, name="http", daemon=True).start()
    log("listening on port %d" % port)

    last_sample = last_prune = 0.0
    while True:
        now = time.time()
        try:
            app.recorder.tick(now)
            if now - last_sample >= 60 or not last_sample:
                last_sample = now
                app.sample_health(now)
            if now - last_prune >= 3600:
                last_prune = now
                app.store.prune(now)
        except Exception as e:
            log("maintenance error: %r" % e)
        time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
