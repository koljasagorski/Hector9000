#!/usr/bin/env python3
# -*- coding: utf8 -*-
##
#   StatsRecorder.py     Turns MQTT messages into statistics rows
#
#   Pure logic (no MQTT client, no threads): StatsServer.py feeds handle() with
#   every message of the Hector9000/# tree and calls tick() once a second.
#   It understands the structured events of utils/Events.py and, as a fallback,
#   the plain topics of an older controller (doseDrink, doseDrink/progress).
#
#   The broker is anonymous, so everything is validated and rate limited: a
#   stranger on the network must not be able to fill the disk or break the
#   statistics page with a flood of fake messages.
#

import json
import re

from Hector9000.utils import Events

STALE_DOSE_S = 150         # a dose without any sign of life this long was aborted
RATE_WINDOW_S = 60.0
RATE_LIMITS = {"dose": 40, "error": 60, "event": 120}

ACTION_KINDS = {
    "cleanMe": "clean",
    "dryMe": "dry",
    "openAllValves": "valves_open",
    "closeAllValves": "valves_close",
    "system/reboot": "reboot",
    "system/kioskExit": "kiosk_exit",
}

SERVICE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,30}$")


class StatsRecorder:

    def __init__(self, store, on_error=None, on_notice=None):
        """on_error(level, source, message) and on_notice(kind, text) are optional hooks
        (the notifier hangs on them)."""
        self.store = store
        self.on_error = on_error or (lambda level, source, message: None)
        self.on_notice = on_notice or (lambda kind, text: None)
        self.session = None            # the dose currently running
        self.live = {"state": "idle", "drink": None, "progress": 0, "since": None}
        self.services = {}             # name -> {"state": "online", "ts": ...}
        self.drink_names = {}          # id -> name, learned from get_drinks/return
        self.messages = 0
        self._rate = {}

    # ----------------------------------------------------------- plumbing --
    def _allowed(self, bucket, now):
        stamps = [t for t in self._rate.get(bucket, []) if now - t < RATE_WINDOW_S]
        if len(stamps) >= RATE_LIMITS[bucket]:
            self._rate[bucket] = stamps
            return False
        stamps.append(now)
        self._rate[bucket] = stamps
        return True

    def _set_live(self, state, drink=None, progress=0, since=None):
        self.live = {"state": state, "drink": drink, "progress": progress, "since": since}

    # ---------------------------------------------------------- dispatcher --
    def handle(self, topic, payload, now, retained=False):
        """Process one message. Never raises: broken messages are dropped."""
        try:
            self._handle(topic, payload, now, retained)
        except Exception:
            pass

    def _handle(self, topic, payload, now, retained):
        if not topic.startswith("Hector9000/"):
            return
        self.messages += 1
        t = topic[len("Hector9000/"):]
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8", "replace")
        payload = str(payload)
        if len(payload) > 4096:
            return

        if t.startswith("status/"):
            self._status(t[len("status/"):], payload, now)
            return
        if retained:
            return            # retained messages are state, not something that just happened

        if t == "event/dose":
            if self._allowed("dose", now):
                self._dose_start(Events.parse(payload), now)
        elif t == "event/dose_done":
            if self._allowed("dose", now):
                self._dose_done(Events.parse(payload), now)
        elif t == "event/error":
            if self._allowed("error", now):
                self._error(Events.parse(payload), now)
        elif t == "event/doom":
            data = Events.parse(payload)
            if data and self._allowed("event", now):
                seconds = max(0, min(int(data.get("seconds", 0)), 6 * 3600))
                self.store.add_event(now, "doom", seconds)
        elif t in ACTION_KINDS:
            if self._allowed("event", now):
                self.store.add_event(now, ACTION_KINDS[t], "")
                self.on_notice(ACTION_KINDS[t], t)
        elif t == "doseDrink":
            self._dose_fallback_start(payload, now)
        elif t == "doseDrink/progress":
            self._progress(payload, now)
        elif t == "get_drinks/return":
            self._learn_drinks(payload)

    # -------------------------------------------------------------- doses --
    def _close_stale(self, now, reason):
        if self.session:
            self.store.finish_dose(self.session["id"], now, "aborted")
            self.on_error("warning", "dosing", "Dosierung von %s nicht beendet (%s)" % (
                self.session["drink"], reason))
            self.session = None

    def _dose_start(self, data, now):
        if not data:
            return
        drink_id = int(data.get("drink_id", 0))
        plan = []
        for ing in list(data.get("ingredients") or [])[:20]:
            ml = int(ing.get("ml", 0))
            if not 0 <= ml <= 2000:
                continue
            plan.append({"code": str(ing.get("code", "?"))[:50], "name": str(ing.get("name", ""))[:100],
                         "ml": ml, "alcohol": bool(ing.get("alcohol"))})
        name = str(data.get("drink") or self.drink_names.get(drink_id) or "Drink #%d" % drink_id)[:100]
        alcohol = bool(data.get("alcohol"))
        cup = int(data.get("cup_ml", 0)) or None

        s = self.session
        if s and s["id_drink"] == drink_id and not s["structured"] and now - s["start"] < 10:
            # the plain doseDrink request arrived first: complete that dose
            self.store.update_dose_plan(s["id"], name, alcohol, cup, plan)
            s.update(structured=True, drink=name)
        else:
            self._close_stale(now, "neue Dosierung")
            dose_id = self.store.start_dose(now, drink_id, name, alcohol, cup, plan)
            self.session = {"id": dose_id, "id_drink": drink_id, "drink": name, "start": now,
                            "last": now, "structured": True}
        self.session["last"] = now
        self._set_live("dosing", name, 0, self.session["start"])

    def _dose_fallback_start(self, payload, now):
        if not payload.strip().isdigit():
            return
        drink_id = int(payload.strip())
        if not self._allowed("dose", now):
            return
        if self.session and self.session["id_drink"] == drink_id and now - self.session["start"] < 10:
            return            # structured event follows (or already came)
        self._close_stale(now, "neue Dosierung")
        name = self.drink_names.get(drink_id, "Drink #%d" % drink_id)
        dose_id = self.store.start_dose(now, drink_id, name, False, None, [])
        self.session = {"id": dose_id, "id_drink": drink_id, "drink": name, "start": now,
                        "last": now, "structured": False}
        self._set_live("dosing", name, 0, now)

    def _progress(self, payload, now):
        if not self.session:
            return
        self.session["last"] = now
        text = payload.strip().lower()
        if text == "end":
            if not self.session["structured"]:     # an old controller sends no dose_done
                self.store.finish_dose(self.session["id"], now, "ok")
                self.session = None
                self._set_live("idle")
            return
        try:
            value = float(text)
        except ValueError:
            return
        if value == value and 0 <= value <= 100:
            self.live["progress"] = round(value)

    def _dose_done(self, data, now):
        s = self.session
        if not data or not s:
            return
        status = data.get("status")
        if status not in ("ok", "partial", "failed"):
            return
        failed = [str(f)[:50] for f in (data.get("failed") or [])][:20]
        duration = float(data.get("duration_s") or 0) or None
        self.store.finish_dose(s["id"], now, status, duration, failed)
        self.session = None
        self._set_live("idle")
        self.on_notice("dose_" + status, s["drink"])

    # ------------------------------------------------------------- errors --
    def _error(self, data, now):
        if not data:
            return
        level = data.get("level") if data.get("level") in Events.LEVELS else "error"
        source = str(data.get("source") or "unknown")[:50]
        message = str(data.get("message") or "")[:300]
        self.store.add_error(now, source, level, message)
        self.on_error(level, source, message)

    # ----------------------------------------------------------- services --
    def _status(self, name, payload, now):
        if not SERVICE_RE.match(name) or len(self.services) > 50 and name not in self.services:
            return
        state = payload.strip().lower()
        if state not in ("online", "offline"):
            return
        before = self.services.get(name, {}).get("state")
        self.services[name] = {"state": state, "ts": now}
        if before != state:
            self.store.add_event(now, "service", "%s %s" % (name, state))
            if state == "offline" and before == "online":
                self.on_error("error", "service", "Dienst %s ist offline" % name)

    def _learn_drinks(self, payload):
        try:
            data = json.loads(payload)
            for d in data.get("drinks", [])[:200]:
                self.drink_names[int(d["id"])] = str(d["name"])[:100]
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
            pass

    # --------------------------------------------------------------- tick --
    def tick(self, now):
        s = self.session
        if s and now - s["last"] > STALE_DOSE_S:
            self._close_stale(now, "keine Meldung seit %d s" % STALE_DOSE_S)
            self._set_live("idle")
