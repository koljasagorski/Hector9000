#!/usr/bin/env python3
# -*- coding: utf8 -*-
##
#   LEDAnimations.py      Hardware-free LED animation engine for the WS2812 strip
#
#   LEDEngine keeps the state (theme colours, ambient mode, current event)
#   and computes one frame (a list of (r, g, b) tuples) for any point in time.
#   LEDStripServer.py feeds it MQTT messages and writes frames to the strip,
#   so everything here can be tested without hardware.
#
#   Layout (see HectorConfig "ws2812"): the first `base` pixels sit under the
#   glass, the remaining pixels form the column (bottom -> top, unless
#   column_reversed is set).
#

import json
import math
import random

# 40C3 "Model Citizens" palette (styleguide, CC0)
CONGRESS = {
    "0": "#75B54B", "1": "#13AEA3", "2": "#999FCD", "3": "#B78FBF",
    "4": "#EB5D9D", "5": "#EB6F83", "6": "#F28931", "7": "#FAB93E",
    "8": "#7EAE83", "9": "#3B5831", "10": "#B69D93", "11": "#584744",
    "A": "#DFEED5", "B": "#031202",
}

# LED colours per WebUI theme: accent, secondary, highlight (no dark
# backgrounds - a nearly black colour is just "off" on an LED)
THEME_PALETTES = {
    "dark": ["0", "9", "A"],
    "light": ["0", "8", "A"],
    "pink": ["4", "A", "0"],
    "green": ["0", "9", "A"],
    "teal": ["1", "A", "8"],
    "autumn": ["6", "10", "7"],
    "yellow": ["7", "6", "A"],
    "lilac": ["2", "3", "A"],
}

AMBIENT_MODES = ("theme", "random", "idle", "off")

# how long one-shot events stay before falling back to the ambient mode
EVENT_SECONDS = {
    "select": 60.0,
    "dosing": 300.0,     # ended by progress "end" / drinkfinish
    "finish": 4.0,
    "clean": 900.0,      # no end message from the controller
    "valves": 8.0,
    "shutdown": 1e9,
}


def hex_to_rgb(value):
    value = str(value).strip().lstrip("#")
    if len(value) != 6:
        raise ValueError("bad colour %r" % value)
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def palette_from_keys(keys):
    return [hex_to_rgb(CONGRESS[k]) for k in keys]


def scale(color, f):
    f = max(0.0, min(1.0, f))
    return tuple(int(round(c * f)) for c in color)


def mix(a, b, t):
    t = max(0.0, min(1.0, t))
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def palette_at(palette, x):
    """Smoothly interpolate the palette as a loop, x in [0, 1)."""
    n = len(palette)
    pos = (x % 1.0) * n
    i = int(pos)
    return mix(palette[i % n], palette[(i + 1) % n], pos - i)


def _hash01(*parts):
    """Deterministic pseudo random number in [0, 1) without state."""
    h = 2166136261
    for p in parts:
        for ch in str(p):
            h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return h / 4294967296.0


# ---------------------------------------------------------------- effects --
# Every effect: (t seconds, n pixels, palette) -> list of n colours.

def fx_wave(t, n, palette, speed=0.08):
    return [palette_at(palette, i / max(n, 1) * 0.6 + t * speed) for i in range(n)]


def fx_breathe(t, n, palette, period=6.0, low=0.15, high=1.0):
    level = low + (high - low) * (0.5 - 0.5 * math.cos(2 * math.pi * t / period))
    color = palette_at(palette, t / (period * len(palette)))
    return [scale(color, level)] * n


def fx_chase(t, n, palette, speed=8.0, tail=4):
    head = (t * speed) % n
    out = []
    for i in range(n):
        d = (head - i) % n
        level = max(0.0, 1.0 - d / tail) if d < tail else 0.0
        color = palette[int(t * speed / n) % len(palette)]
        out.append(scale(color, level))
    return out


def fx_sparkle(t, n, palette, rate=6.0):
    slot = int(t * rate)
    frac = t * rate - slot
    out = []
    for i in range(n):
        r = _hash01("s", i, slot)
        if r < 0.35:
            color = palette[int(_hash01("c", i, slot) * len(palette)) % len(palette)]
            out.append(scale(color, 1.0 - frac))
        else:
            out.append(scale(palette[0], 0.08))
    return out


def fx_palette_cycle(t, n, palette, speed=0.25):
    return [palette_at(palette, t * speed + i / max(n, 1)) for i in range(n)]


RANDOM_EFFECTS = {
    "wave": fx_wave,
    "breathe": fx_breathe,
    "chase": fx_chase,
    "sparkle": fx_sparkle,
    "cycle": fx_palette_cycle,
}


class LEDEngine:

    def __init__(self, count=15, base=5, brightness=0.5, idle_after=300.0,
                 column_reversed=False, rng=None, now=0.0):
        self.count = int(count)
        self.base = max(0, min(int(base), self.count))
        self.brightness = max(0.0, min(1.0, float(brightness)))
        self.idle_after = float(idle_after)
        self.column_reversed = bool(column_reversed)
        self.rng = rng or random.Random()
        self.theme = "dark"
        self.palette = palette_from_keys(THEME_PALETTES["dark"])
        self.ambient = "theme"
        self.last_activity = now
        self.event = None          # dict(name, start, until, ...)
        self._random_until = -1.0
        self._random_fx = "wave"
        self._random_palette = self.palette

    # ------------------------------------------------------------ input --
    def activity(self, now):
        self.last_activity = now

    def set_theme(self, payload, now):
        """payload: theme id ("pink") or JSON {"id": .., "colors": [hex, ..]}."""
        theme, colors = str(payload).strip(), None
        if theme.startswith("{"):
            data = json.loads(theme)
            theme = str(data.get("id", ""))
            colors = data.get("colors")
        if colors:
            self.palette = [hex_to_rgb(c) for c in colors][:6]
        elif theme in THEME_PALETTES:
            self.palette = palette_from_keys(THEME_PALETTES[theme])
        else:
            raise ValueError("unknown theme %r" % theme)
        self.theme = theme
        self.activity(now)

    def set_ambient(self, payload, now):
        mode = str(payload).strip().lower()
        if mode not in AMBIENT_MODES:
            raise ValueError("unknown ambient mode %r" % mode)
        self.ambient = mode
        self._random_until = -1.0
        self.activity(now)

    def start_event(self, name, now, **data):
        self.event = dict(name=name, start=now, until=now + EVENT_SECONDS[name], **data)
        self.activity(now)

    def clear_event(self, now):
        if self.event and self.event["name"] != "shutdown":
            self.event = None
        self.activity(now)

    def handle(self, topic, payload, now):
        """Apply one MQTT message. Unknown or broken messages are ignored."""
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8", "replace")
        payload = str(payload)
        t = topic.split("Hector9000/", 1)[-1]
        try:
            if t == "LEDStrip/theme":
                self.set_theme(payload, now)
            elif t == "LEDStrip/ambient":
                self.set_ambient(payload, now)
            elif t == "LEDStrip/activity":
                self.activity(now)
            elif t == "LEDStrip/select":
                self.start_event("select", now)
            elif t in ("LEDStrip/deselect", "LEDStrip/standart"):
                self.clear_event(now)
            elif t == "LEDStrip/standby":
                self.last_activity = now - self.idle_after - 1  # go idle now
                self.event = None
            elif t == "LEDStrip/dosedrink":
                parts = payload.split(",")
                color = None
                if len(parts) >= 3:
                    color = tuple(max(0, min(255, int(float(p)))) for p in parts[:3])
                self.start_event("dosing", now, progress=0.0, color=color)
            elif t == "doseDrink/progress":
                if payload.strip().lower() == "end":
                    self.start_event("finish", now)
                    return
                progress = float(payload.split()[0]) / 100.0
                if not self.event or self.event["name"] != "dosing":
                    self.start_event("dosing", now, progress=0.0, color=None)
                self.event["progress"] = max(0.0, min(1.0, progress))
                self.event["until"] = now + EVENT_SECONDS["dosing"]
                self.activity(now)
            elif t == "LEDStrip/drinkfinish":
                self.start_event("finish", now)
            elif t in ("cleanMe", "dryMe"):
                self.start_event("clean", now)
            elif t in ("openAllValves", "closeAllValves"):
                self.start_event("valves", now)
            elif t == "system/reboot":
                self.start_event("shutdown", now)
        except (ValueError, TypeError, KeyError, IndexError):
            pass

    # ----------------------------------------------------------- output --
    def is_idle(self, now):
        return self.ambient == "idle" or (
            self.idle_after > 0 and now - self.last_activity > self.idle_after)

    def _column(self):
        idx = list(range(self.base, self.count))
        return idx[::-1] if self.column_reversed else idx

    def _ambient_frame(self, now):
        n = self.count
        if self.ambient == "off":
            return [(0, 0, 0)] * n
        if self.is_idle(now):
            return fx_breathe(now, n, self.palette[:1], period=8.0, low=0.04, high=0.25)
        if self.ambient == "random":
            if now >= self._random_until:
                self._random_fx = self.rng.choice(sorted(RANDOM_EFFECTS))
                keys = [k for k in CONGRESS if k != "B"]
                self._random_palette = palette_from_keys(self.rng.sample(keys, 3))
                self._random_until = now + self.rng.uniform(20.0, 40.0)
            return RANDOM_EFFECTS[self._random_fx](now, n, self._random_palette)
        return fx_wave(now, n, self.palette, speed=0.05)

    def _event_frame(self, now):
        ev, n = self.event, self.count
        dt = now - ev["start"]
        accent = self.palette[0]
        if ev["name"] == "select":
            return fx_breathe(dt, n, [accent], period=1.6, low=0.35, high=1.0)
        if ev["name"] == "dosing":
            color = ev.get("color") or accent
            frame = [(0, 0, 0)] * n
            pulse = 0.4 + 0.6 * (0.5 + 0.5 * math.sin(dt * 6.0))
            for i in range(self.base):
                frame[i] = scale(color, pulse)
            col = self._column()
            level = ev["progress"] * len(col)
            for k, i in enumerate(col):
                frame[i] = scale(color, max(0.0, min(1.0, level - k)))
            return frame
        if ev["name"] == "finish":
            frame = fx_sparkle(dt, n, palette_from_keys(["0", "1", "4", "7", "A"]), rate=12.0)
            if int(dt * 4) % 2 == 0:
                for i in range(self.base):
                    frame[i] = hex_to_rgb(CONGRESS["A"])
            return frame
        if ev["name"] == "clean":
            return fx_wave(dt, n, palette_from_keys(["1", "A", "2"]), speed=0.4)
        if ev["name"] == "valves":
            return fx_chase(dt, n, palette_from_keys(["7", "6"]), speed=12.0)
        if ev["name"] == "shutdown":
            # fade out before the reboot
            return [scale(accent, max(0.0, 1.0 - dt / 3.0))] * n
        return self._ambient_frame(now)

    def frame(self, now):
        if self.event and now > self.event["until"]:
            self.event = None
        raw = self._event_frame(now) if self.event else self._ambient_frame(now)
        return [scale(c, self.brightness) for c in raw]
