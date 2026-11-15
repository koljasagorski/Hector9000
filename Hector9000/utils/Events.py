#!/usr/bin/env python3
# -*- coding: utf8 -*-
##
#   Events.py     Structured events that Hector publishes on MQTT
#
#   The statistics service (StatsServer.py) and the notifier (Matrix bot)
#   listen to these topics. Payloads are small JSON objects so that they can
#   be read by anything (mosquitto_sub, a dashboard, a bot).
#
#     Hector9000/event/dose        a drink starts (id, name, ingredients in ml)
#     Hector9000/event/dose_done   a drink ended (ok / partial / failed)
#     Hector9000/event/error       something went wrong (source, message)
#     Hector9000/event/doom        the Doom easter egg was closed (seconds)
#     Hector9000/status/<service>  retained "online" / "offline" (last will)
#

import json
import time

PREFIX = "Hector9000/"

TOPIC_DOSE = PREFIX + "event/dose"
TOPIC_DOSE_DONE = PREFIX + "event/dose_done"
TOPIC_ERROR = PREFIX + "event/error"
TOPIC_DOOM = PREFIX + "event/doom"
TOPIC_STATUS = PREFIX + "status/"

LEVELS = ("info", "warning", "error", "critical")

# nobody should be able to flood the database through MQTT
MAX_TEXT = 300


def _text(value, limit=MAX_TEXT):
    return str(value)[:limit]


def dose_started(drink_id, name, alcohol, cup_ml, plan, now=None):
    """plan: list of dicts (code, name, ml, alcohol) in dosing order."""
    return json.dumps({
        "drink_id": int(drink_id),
        "drink": _text(name),
        "alcohol": bool(alcohol),
        "cup_ml": int(cup_ml),
        "ingredients": [
            {"code": _text(p["code"], 50), "name": _text(p["name"], 100),
             "ml": int(p["ml"]), "alcohol": bool(p.get("alcohol", False))}
            for p in plan],
        "ts": time.time() if now is None else now,
    })


def dose_finished(drink_id, status, duration_s, failed=None, now=None):
    """status: ok (everything dosed), partial (some ingredients failed), failed."""
    if status not in ("ok", "partial", "failed"):
        raise ValueError("unknown dose status %r" % status)
    return json.dumps({
        "drink_id": int(drink_id),
        "status": status,
        "duration_s": round(float(duration_s), 2),
        "failed": [_text(f, 50) for f in (failed or [])],
        "ts": time.time() if now is None else now,
    })


def error_event(source, message, level="error", now=None):
    if level not in LEVELS:
        level = "error"
    return json.dumps({
        "source": _text(source, 50),
        "level": level,
        "message": _text(message),
        "ts": time.time() if now is None else now,
    })


def doom_event(seconds, now=None):
    return json.dumps({
        "seconds": max(0, int(seconds)),
        "ts": time.time() if now is None else now,
    })


def parse(payload):
    """Decode an event payload; returns a dict or None for anything else."""
    try:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8", "replace")
        if len(payload) > 4096:
            return None
        data = json.loads(payload)
    except (ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None
