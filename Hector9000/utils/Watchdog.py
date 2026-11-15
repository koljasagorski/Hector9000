#!/usr/bin/env python3
# -*- coding: utf8 -*-
##
#   Watchdog.py     Decides when a measurement is a problem worth telling someone
#
#   Pure rules: check() gets a health snapshot, the process list and the bottle
#   levels and calls notify(level, message, key) / resolve(key, message).
#   Hysteresis keeps a value that wobbles around a limit from flooding the chat.
#

from Hector9000.utils import SystemHealth

TEMP_WARN, TEMP_CRIT, TEMP_OK = 75.0, 82.0, 70.0
DISK_WARN, DISK_OK = 90.0, 85.0
MEM_WARN, MEM_OK = 92.0, 85.0
PROCESS_GRACE_S = 60          # a process may be missing this long (restart)
BOTTLE_LOW_PCT = 15.0


class Watchdog:

    def __init__(self, notify, resolve, expected_processes=None):
        self.notify, self.resolve = notify, resolve
        self.expected = expected_processes      # names that must run (None = don't check)
        self.missing_since = {}
        self.bottle_alerted = set()

    def check(self, health, processes, bottles, now):
        self._temperature(health.get("cpu_temp"))
        self._level("disk", health.get("disk_pct"), DISK_WARN, DISK_OK,
                    "Speicherkarte zu %.0f %% voll", "Speicherplatz wieder in Ordnung")
        self._level("mem", health.get("mem_pct"), MEM_WARN, MEM_OK,
                    "Arbeitsspeicher zu %.0f %% belegt", "Arbeitsspeicher wieder in Ordnung")
        self._power(health.get("throttled"))
        self._processes(processes, now)
        self._bottles(bottles)

    def _temperature(self, temp):
        if temp is None:
            return
        if temp >= TEMP_CRIT:
            self.notify("critical", "CPU-Temperatur %.1f °C - der Pi drosselt gleich" % temp, "temp")
        elif temp >= TEMP_WARN:
            self.notify("warning", "CPU-Temperatur %.1f °C" % temp, "temp")
        elif temp < TEMP_OK:
            self.resolve("temp", "CPU-Temperatur wieder normal (%.1f °C)" % temp)

    def _level(self, key, value, warn, ok, text, ok_text):
        if value is None:
            return
        if value >= warn:
            self.notify("warning", text % value, key)
        elif value < ok:
            self.resolve(key, ok_text)

    def _power(self, value):
        if value is None:
            return
        now_bits = value & 0x5            # under-voltage or throttled right now
        if now_bits:
            reasons = ", ".join(SystemHealth.throttle_reasons(value & 0xF)) or "Netzteil"
            self.notify("warning", "Stromversorgung schwach: %s - Netzteil prüfen" % reasons, "power")
        else:
            self.resolve("power", "Stromversorgung wieder in Ordnung")

    def _processes(self, processes, now):
        if not processes or self.expected is None:
            return
        for name in self.expected:
            if processes.get(name, True):
                self.missing_since.pop(name, None)
                self.resolve("proc:" + name, "Prozess %s läuft wieder" % name)
                continue
            since = self.missing_since.setdefault(name, now)
            if now - since >= PROCESS_GRACE_S:
                self.notify("error", "Prozess %s läuft nicht" % name, "proc:" + name)

    def _bottles(self, bottles):
        for b in bottles or []:
            pct = b.get("remaining_pct")
            key = "bottle:" + b["code"]
            if pct is None:
                continue
            if pct <= BOTTLE_LOW_PCT:
                if self.notify("warning", "%s fast leer (%d ml von %d ml übrig)" % (
                        b["name"], b["remaining_ml"], b["capacity_ml"]), key):
                    self.bottle_alerted.add(key)
            elif pct > BOTTLE_LOW_PCT + 10:
                self.resolve(key, "%s wurde aufgefüllt" % b["name"])
