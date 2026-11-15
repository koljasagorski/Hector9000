#!/usr/bin/env python3
# -*- coding: utf8 -*-
##
#   Notifier.py     Sends alerts out of the machine (Matrix chat bot, prepared)
#
#   Hector tells people when something breaks: an empty bottle, a service that
#   died, an overheating Pi. The Notifier is the only place that talks to the
#   outside; everything else just calls notify().
#
#     - notify() never blocks and never raises (a broken network must not stop
#       a cocktail),
#     - identical alerts are sent at most once per dedup window,
#     - offline? messages wait in a small queue and are retried with backoff,
#     - resolve() sends a "wieder in Ordnung" note, but only for alerts that
#       were really sent.
#
#   Matrix is switched on purely through the environment (secrets never go
#   into the repository, see docs/matrix.md):
#
#     HECTOR_MATRIX_HOMESERVER   https://matrix.example.org
#     HECTOR_MATRIX_ROOM         !roomid:example.org   (or the room's internal id)
#     HECTOR_MATRIX_TOKEN        access token of the bot account
#     HECTOR_NOTIFY_LEVEL        info | warning | error | critical   (default warning)
#
#   More backends (e-mail, ntfy, ...) only need a send(text, level) method.
#

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

LEVELS = {"info": 0, "warning": 1, "error": 2, "critical": 3}
ICONS = {"info": "ℹ️", "warning": "🟠", "error": "🔴", "critical": "🚨"}

MAX_QUEUE = 50
MAX_UNTRUSTED_PER_HOUR = 20   # alerts that were derived from MQTT payloads (anyone on the broker)
MAX_UNTRUSTED_TEXT = 300
MAX_AGE_S = 6 * 3600          # older messages are no longer worth sending
MAX_BACKOFF_S = 300
MAX_TEXT = 1500


class PermanentError(Exception):
    """The backend rejected the message for good (wrong token, unknown room, ...)."""


class TemporaryError(Exception):
    """Try again later (no network, server busy)."""

    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


class LogBackend:
    name = "log"

    def send(self, text, level, txn):
        print("Notifier[%s]: %s" % (level, text), flush=True)


class MatrixBackend:
    """Posts into one Matrix room via the client-server API (no extra libraries).

    Works for unencrypted rooms. End-to-end encrypted rooms need an Olm-capable
    client library - see docs/matrix.md for the way forward.
    """

    name = "matrix"

    def __init__(self, homeserver, room, token, timeout=10, msgtype="m.text",
                 opener=None, allow_http=False):
        homeserver = homeserver.rstrip("/")
        scheme = urllib.parse.urlparse(homeserver).scheme
        if scheme != "https" and not (scheme == "http" and allow_http):
            raise ValueError("HECTOR_MATRIX_HOMESERVER must be an https:// URL "
                             "(HECTOR_MATRIX_ALLOW_HTTP=1 allows http for a LAN server)")
        if not room or not token:
            raise ValueError("room and token are required")
        self.homeserver, self.room, self.token = homeserver, room, token
        self.timeout, self.msgtype = timeout, msgtype
        self._open = opener or urllib.request.urlopen

    def url(self, txn):
        return "%s/_matrix/client/v3/rooms/%s/send/m.room.message/%s" % (
            self.homeserver, urllib.parse.quote(self.room, safe=""),
            urllib.parse.quote(txn, safe=""))

    def send(self, text, level, txn):
        body = json.dumps({"msgtype": self.msgtype, "body": text}).encode("utf-8")
        req = urllib.request.Request(self.url(txn), data=body, method="PUT", headers={
            "Authorization": "Bearer " + self.token,
            "Content-Type": "application/json",
            "User-Agent": "Hector9000-Notifier",
        })
        try:
            with self._open(req, timeout=self.timeout) as resp:
                resp.read(65536)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry = None
                try:
                    retry = json.loads(e.read(4096)).get("retry_after_ms", 0) / 1000.0
                except Exception:
                    pass
                raise TemporaryError("Matrix: zu viele Anfragen", retry)
            if 500 <= e.code < 600:
                raise TemporaryError("Matrix: Serverfehler %d" % e.code)
            raise PermanentError("Matrix lehnt ab (HTTP %d) - Token/Raum prüfen" % e.code)
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise TemporaryError("Matrix nicht erreichbar: %s" % getattr(e, "reason", e))


def backends_from_env(env=None):
    """Build the backends that are configured. Nothing configured -> empty list."""
    env = os.environ if env is None else env
    out = []
    if env.get("HECTOR_MATRIX_HOMESERVER") and env.get("HECTOR_MATRIX_TOKEN"):
        out.append(MatrixBackend(
            env["HECTOR_MATRIX_HOMESERVER"], env.get("HECTOR_MATRIX_ROOM", ""),
            env["HECTOR_MATRIX_TOKEN"], msgtype=env.get("HECTOR_MATRIX_MSGTYPE", "m.text"),
            allow_http=env.get("HECTOR_MATRIX_ALLOW_HTTP") == "1"))
    return out


class Notifier:

    def __init__(self, backends=None, min_level="warning", dedup_s=3600, clock=time.time,
                 max_queue=MAX_QUEUE, host="Hector9000", max_untrusted_per_hour=MAX_UNTRUSTED_PER_HOUR):
        self.backends = list(backends or [])
        self.min_level = LEVELS.get(min_level, 1)
        self.dedup_s = dedup_s
        self.clock = clock
        self.max_queue = max_queue
        self.host = host
        self.max_untrusted = max_untrusted_per_hour
        self._untrusted_times = []
        self._flood_reported = False
        self._suppressed = {}              # source -> how many alerts were held back
        self.queue = []
        self.sent_keys = {}          # key -> time of the last alert that was queued
        self.active = set()          # keys with an unresolved alert
        self.stats = {"sent": 0, "dropped": 0, "failed": 0, "suppressed": 0,
                      "last_ok": None, "last_error": None}
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._thread = None
        self._counter = 0

    # ----------------------------------------------------------------- api --
    @property
    def enabled(self):
        return bool(self.backends)

    def notify(self, level, message, key=None, source="", untrusted=False):
        """Queue an alert. Returns True if it will be sent.

        untrusted=True marks alerts whose text came from an MQTT message (the broker may be
        anonymous): they are clipped harder and limited to a few per hour, so nobody can
        use the chat room as a spam channel. Checks made by the service itself are not limited."""
        try:
            level = level if level in LEVELS else "error"
            if LEVELS[level] < self.min_level or not self.backends:
                return False
            now = self.clock()
            if untrusted:
                message = str(message)[:MAX_UNTRUSTED_TEXT]
            key = key or "%s:%s" % (source, message)
            with self._lock:
                last = self.sent_keys.get(key)
                if last is not None and now - last < self.dedup_s:
                    return False
                if untrusted and not self._untrusted_allowed(now, source):
                    return False
                self.sent_keys[key] = now
                self.active.add(key)
                if len(self.sent_keys) > 500:       # forget old keys
                    for k in sorted(self.sent_keys, key=self.sent_keys.get)[:250]:
                        self.sent_keys.pop(k, None)
                self._enqueue(level, message, source, now)
            self._wake.set()
            return True
        except Exception:
            return False

    def _untrusted_allowed(self, now, source=""):
        """Caller holds the lock. At most max_untrusted alerts per hour; one summary says so
        when it starts and one recap (count and sources) when alerts flow again."""
        self._untrusted_times = [t for t in self._untrusted_times if now - t < 3600]
        if len(self._untrusted_times) >= self.max_untrusted:
            self.stats["suppressed"] += 1
            name = str(source or "?")[:40]
            if name in self._suppressed or len(self._suppressed) < 20:
                self._suppressed[name] = self._suppressed.get(name, 0) + 1
            if not self._flood_reported:
                self._flood_reported = True
                self._enqueue("warning", "Sehr viele Fehlermeldungen in kurzer Zeit - weitere werden "
                              "bis zu einer Stunde lang unterdrückt (Details im Admin-Dashboard)",
                              "notifier", now)
                self._wake.set()
            return False
        if self._flood_reported:
            top = sorted(self._suppressed.items(), key=lambda kv: -kv[1])[:5]
            self._enqueue("info", "%d Meldungen wurden unterdrückt (Quellen: %s). Alle Fehler stehen im "
                          "Admin-Dashboard." % (sum(self._suppressed.values()),
                                                ", ".join("%s %d×" % kv for kv in top)),
                          "notifier", now, recovery=False)
            self._wake.set()
        self._flood_reported = False
        self._suppressed = {}
        self._untrusted_times.append(now)
        return True

    def resolve(self, key, message):
        """Tell that a problem is gone - only if its alert was sent."""
        with self._lock:
            if key not in self.active:
                return False
            self.active.discard(key)
            self.sent_keys.pop(key, None)
            self._enqueue("info", message, "", self.clock(), recovery=True)
        self._wake.set()
        return True

    def _enqueue(self, level, message, source, now, recovery=False):
        icon = "✅" if recovery else ICONS[level]
        where = " (%s)" % source if source else ""
        text = ("%s %s%s: %s" % (icon, self.host, where, message))[:MAX_TEXT]
        self._counter += 1
        item = {"text": text, "level": level, "created": now, "attempts": 0, "next": now,
                "txn": "h9k-%d-%d" % (int(now * 1000), self._counter),
                "pending": [b.name for b in self.backends]}
        self.queue.append(item)
        while len(self.queue) > self.max_queue:
            self.queue.pop(0)
            self.stats["dropped"] += 1

    # -------------------------------------------------------------- worker --
    def process(self, now=None):
        """Send everything that is due. Returns the number of finished messages."""
        now = self.clock() if now is None else now
        done = 0
        with self._lock:
            due = [i for i in self.queue if i["next"] <= now]
        for item in due:
            if now - item["created"] > MAX_AGE_S:
                self._finish(item, dropped=True)
                continue
            for backend in [b for b in self.backends if b.name in item["pending"]]:
                try:
                    backend.send(item["text"], item["level"], item["txn"])
                    item["pending"].remove(backend.name)
                    self.stats["sent"] += 1
                    self.stats["last_ok"] = now
                except PermanentError as e:
                    item["pending"].remove(backend.name)
                    self.stats["failed"] += 1
                    self.stats["last_error"] = str(e)
                except TemporaryError as e:
                    item["attempts"] += 1
                    wait = e.retry_after or min(MAX_BACKOFF_S, 5 * 2 ** min(item["attempts"], 6))
                    item["next"] = now + wait
                    self.stats["last_error"] = str(e)
                except Exception as e:           # a broken backend must not kill the worker
                    item["pending"].remove(backend.name)
                    self.stats["failed"] += 1
                    self.stats["last_error"] = "%s: %s" % (backend.name, e)
            if not item["pending"]:
                self._finish(item)
                done += 1
        return done

    def _finish(self, item, dropped=False):
        with self._lock:
            if item in self.queue:
                self.queue.remove(item)
            if dropped:
                self.stats["dropped"] += 1

    def start(self):
        if self._thread or not self.backends:
            return
        self._thread = threading.Thread(target=self._run, name="notifier", daemon=True)
        self._thread.start()

    def _run(self):
        while True:
            self._wake.wait(timeout=5)
            self._wake.clear()
            try:
                self.process()
            except Exception:
                pass

    def status(self):
        with self._lock:
            return {
                "enabled": self.enabled,
                "backends": [b.name for b in self.backends],
                "min_level": [k for k, v in LEVELS.items() if v == self.min_level][0],
                "queued": len(self.queue),
                "active_alerts": sorted(self.active),
                **self.stats,
            }

    def test_message(self):
        """For the admin dashboard: a message that bypasses level and dedup."""
        if not self.backends:
            return False
        with self._lock:
            self._enqueue("info", "Testnachricht - die Benachrichtigung funktioniert.", "", self.clock())
        self._wake.set()
        return True
