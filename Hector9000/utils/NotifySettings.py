#!/usr/bin/env python3
# -*- coding: utf8 -*-
##
#   NotifySettings.py     Matrix settings that are edited in the admin dashboard
#
#   The settings live in $HECTOR_STATE_DIR/notify.json (mode 0600, never in the
#   repository, listed in .gitignore). When that file exists it replaces the
#   HECTOR_MATRIX_* / HECTOR_NOTIFY_LEVEL values from the environment; without it
#   the environment (/etc/hector9000/hector.env) is used as before.
#
#   The token is write-only: describe() tells that one is stored, but no function
#   here hands it back out to the web.
#

import json
import os
import re
import tempfile
import urllib.parse

FILE_NAME = "notify.json"
LEVELS = ("info", "warning", "error", "critical")
MSGTYPES = ("m.text", "m.notice")
DEFAULT_LEVEL, DEFAULT_MSGTYPE = "warning", "m.text"

MAX_URL, MAX_ROOM, MAX_TOKEN, MIN_TOKEN = 200, 255, 512, 8
ROOM_RE = re.compile(r"^![^\s?#/\\]{1,%d}$" % (MAX_ROOM - 1))
TOKEN_RE = re.compile(r"^[\x21-\x7e]+$")          # printable ASCII, no spaces/control characters

ENV_KEYS = ("HECTOR_MATRIX_HOMESERVER", "HECTOR_MATRIX_ROOM", "HECTOR_MATRIX_TOKEN")


def path(state_dir):
    return os.path.join(state_dir, FILE_NAME)


def _clean(raw):
    """Keep only known keys with the right types (a hand-edited file must not crash the service)."""
    if not isinstance(raw, dict):
        return None
    out = {"enabled": bool(raw.get("enabled", True))}
    for key in ("homeserver", "room", "token"):
        value = raw.get(key, "")
        out[key] = value if isinstance(value, str) else ""
    out["level"] = raw.get("level") if raw.get("level") in LEVELS else DEFAULT_LEVEL
    out["msgtype"] = raw.get("msgtype") if raw.get("msgtype") in MSGTYPES else DEFAULT_MSGTYPE
    return out


def load(state_dir):
    """The stored settings, or None if there are none (or the file is unreadable)."""
    try:
        with open(path(state_dir), encoding="utf-8") as f:
            return _clean(json.load(f))
    except (OSError, ValueError):
        return None


def save(state_dir, settings):
    """Write atomically with mode 0600 (the file holds the bot's access token)."""
    os.makedirs(state_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".notify-", dir=state_dir)      # mkstemp creates it 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(_clean(settings), f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path(state_dir))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def delete(state_dir):
    try:
        os.unlink(path(state_dir))
    except FileNotFoundError:
        pass


def validate(form, current=None, allow_http=False):
    """Check what the dashboard sent. Returns (settings, None) or (None, "German error message").

    An empty or missing token keeps the stored one; that is how the form can be saved
    without ever showing the token again."""
    current = current or {}
    enabled = form.get("enabled") in (True, "true", "1", 1)
    homeserver = str(form.get("homeserver") or "").strip().rstrip("/")
    room = str(form.get("room") or "").strip()
    token = str(form.get("token") or "").strip() or current.get("token", "")
    level = str(form.get("level") or DEFAULT_LEVEL)
    msgtype = str(form.get("msgtype") or DEFAULT_MSGTYPE)

    if level not in LEVELS:
        return None, "Unbekannte Mindest-Stufe"
    if msgtype not in MSGTYPES:
        return None, "Unbekannter Nachrichtentyp"
    if homeserver:
        err = _check_homeserver(homeserver, allow_http)
        if err:
            return None, err
    if room and not ROOM_RE.match(room):
        return None, "Die Raum-ID beginnt mit ! (z. B. !abc123:example.org), nicht mit # (Alias)"
    if token and (len(token) < MIN_TOKEN or len(token) > MAX_TOKEN or not TOKEN_RE.match(token)):
        return None, "Der Token sieht ungültig aus (Leerzeichen oder Zeilenumbrüche enthalten?)"
    if enabled:
        for value, name in ((homeserver, "Homeserver"), (room, "Raum-ID"), (token, "Zugangstoken")):
            if not value:
                return None, "%s fehlt" % name
    return {"enabled": enabled, "homeserver": homeserver, "room": room, "token": token,
            "level": level, "msgtype": msgtype}, None


def _check_homeserver(url, allow_http):
    if len(url) > MAX_URL or re.search(r"[\s\x00-\x1f\x7f]", url):
        return "Die Homeserver-Adresse ist ungültig"
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname
    except ValueError:
        return "Die Homeserver-Adresse ist ungültig"
    if parts.scheme not in ("https", "http") or not host:
        return "Die Homeserver-Adresse muss mit https:// beginnen"
    if parts.scheme == "http" and not allow_http:
        return "Nur https:// ist erlaubt (http nur mit HECTOR_MATRIX_ALLOW_HTTP=1 in hector.env)"
    if parts.username or parts.password or parts.query or parts.fragment:
        return "Die Homeserver-Adresse darf keine Zugangsdaten, Parameter oder # enthalten"
    return None


def merge_env(env, settings):
    """The environment with the dashboard settings applied (they win over hector.env)."""
    out = dict(env)
    if settings is None:
        return out
    for key in ENV_KEYS:
        out.pop(key, None)
    if settings.get("enabled"):
        out["HECTOR_MATRIX_HOMESERVER"] = settings["homeserver"]
        out["HECTOR_MATRIX_ROOM"] = settings["room"]
        out["HECTOR_MATRIX_TOKEN"] = settings["token"]
    out["HECTOR_NOTIFY_LEVEL"] = settings["level"]
    out["HECTOR_MATRIX_MSGTYPE"] = settings["msgtype"]
    return out


def describe(settings, env):
    """What the dashboard may show. Never contains the token itself."""
    eff = merge_env(env, settings)
    env_on = bool(env.get("HECTOR_MATRIX_HOMESERVER") and env.get("HECTOR_MATRIX_TOKEN"))
    stored = settings or {}
    return {
        "source": "dashboard" if settings is not None else ("env" if env_on else "none"),
        "enabled": bool(eff.get("HECTOR_MATRIX_HOMESERVER") and eff.get("HECTOR_MATRIX_TOKEN")),
        "env_configured": env_on,
        "homeserver": stored.get("homeserver") if settings is not None else eff.get("HECTOR_MATRIX_HOMESERVER", ""),
        "room": stored.get("room") if settings is not None else eff.get("HECTOR_MATRIX_ROOM", ""),
        "token_set": bool(stored.get("token") if settings is not None else eff.get("HECTOR_MATRIX_TOKEN")),
        "level": eff.get("HECTOR_NOTIFY_LEVEL") if eff.get("HECTOR_NOTIFY_LEVEL") in LEVELS else DEFAULT_LEVEL,
        "msgtype": eff.get("HECTOR_MATRIX_MSGTYPE") if eff.get("HECTOR_MATRIX_MSGTYPE") in MSGTYPES else DEFAULT_MSGTYPE,
        "stored_enabled": stored.get("enabled") if settings is not None else None,
    }
