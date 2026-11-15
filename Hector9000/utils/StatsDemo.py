#!/usr/bin/env python3
# -*- coding: utf8 -*-
##
#   StatsDemo.py     Believable fake data for `StatsServer.py --demo` and the tests
#

import math
import random
from datetime import datetime, timedelta

# name, alcoholic, [(code, name, ml, alcohol)], popularity
DRINKS = [
    ("Moscow Mule", True, [("vodka", "Vodka", 60, True), ("lime", "Lime Juice", 10, False),
                           ("gibe", "Ginger Beer", 180, False)], 9),
    ("Gin & Tonic", True, [("gin", "Gin", 40, True), ("tonic", "Tonic Water", 120, False)], 8),
    ("Tschunk", True, [("rum", "Rum", 40, True), ("mate", "Club Mate", 120, False)], 10),
    ("Caipirinha", True, [("rum", "Rum", 40, True), ("gga", "Gomme/Limette", 120, False)], 5),
    ("Screwdriver", True, [("vodka", "Vodka", 40, True), ("oj", "Orange Juice", 120, False)], 6),
    ("Tequila Sunrise", True, [("tequila", "Tequila", 50, True), ("oj", "Orange Juice", 150, False),
                               ("gren", "Grenadine", 15, False)], 4),
    ("Margarita", True, [("tequila", "Tequila", 50, True), ("lime", "Lime Juice", 20, False)], 3),
    ("Extra Schuss Mate", False, [("mate", "Club Mate", 250, False)], 7),
    ("Virgin Sunrise", False, [("oj", "Orange Juice", 140, False), ("gren", "Grenadine", 15, False)], 3),
    ("Shirley Temple", False, [("gibe", "Ginger Beer", 150, False), ("gren", "Grenadine", 15, False)], 2),
    ("Raspberry Zero", False, [("mate", "Club Mate", 100, False), ("gren", "Grenadine", 20, False)], 1),
    ("Horny Bull", True, [("vodka", "Vodka", 50, True), ("mate", "Club Mate", 100, False)], 1),
]


def seed_demo(store, now, days=21, seed=42):
    rng = random.Random(seed)
    weights = [d[3] for d in DRINKS]
    today = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    doses = 0
    for back in range(days, -1, -1):
        day = today - timedelta(days=back)
        weekend = day.weekday() >= 4
        if rng.random() > (0.85 if weekend else 0.45):
            continue
        start = day.replace(hour=rng.choice([17, 18, 19, 20]))
        length_h = rng.uniform(3.5, 6.5)
        count = int(rng.uniform(25, 70) if weekend else rng.uniform(6, 28))
        stamps = sorted(start.timestamp() + 3600 * length_h * min(1.0, max(0.0, rng.gauss(0.55, 0.22)))
                        for _ in range(count))
        for ts in stamps:
            if ts > now:
                continue
            name, alcohol, plan, _ = rng.choices(DRINKS, weights)[0]
            roll = rng.random()
            status = "ok" if roll < 0.93 else "partial" if roll < 0.97 else "failed" if roll < 0.99 else "aborted"
            duration = 8 + 4.5 * len(plan) + rng.uniform(0, 8)
            ingredients = [{"code": c, "name": n, "ml": ml, "alcohol": a} for c, n, ml, a in plan]
            dose_id = store.start_dose(ts, 1, name, alcohol, 400, ingredients)
            failed = [ingredients[-1]["code"]] if status == "partial" else []
            store.finish_dose(dose_id, ts + duration, status, round(duration, 1), failed)
            if status in ("partial", "failed"):
                store.add_error(ts + duration, "dosing",
                                "error", "%s: %s (%d ml) wurde nicht dosiert - Flasche leer, Ventil klemmt "
                                "oder Arm nicht in Position?" % (name, ingredients[-1]["name"],
                                                                 ingredients[-1]["ml"]))
            doses += 1
        end = stamps[-1] + 600 if stamps else start.timestamp()
        if end < now:
            store.add_event(end, "clean", "")
            store.add_event(end + 400, "dry", "")
        store.add_event(start.timestamp() - 600, "boot", "")
    for k in range(4):
        store.add_event(now - rng.uniform(3600, 14 * 86400), "doom", rng.randint(40, 600))
    store.add_error(now - 5 * 3600, "service", "error", "Dienst controller ist offline")
    store.add_error(now - 2 * 86400, "controller", "error",
                    "doseDrink: list index out of range")
    store.add_event(now - 3 * 86400, "reboot", "")
    for minute in range(24 * 60, 0, -1):
        ts = now - minute * 60
        temp = 52 + 8 * math.sin(minute / 90.0) + rng.uniform(-1.5, 1.5)
        store.add_sample(ts, round(temp, 1), round(0.3 + abs(math.sin(minute / 40.0)), 2),
                         round(38 + rng.uniform(-2, 2), 1), 41.0, 0, 86400 * 3 + (24 * 60 - minute) * 60)
    for code, capacity in (("vodka", 700), ("gin", 700), ("rum", 700), ("tequila", 700),
                           ("mate", 1000), ("oj", 1000), ("tonic", 1000), ("gibe", 700),
                           ("lime", 700), ("gren", 500), ("gga", 700)):
        store.refill_bottle(code, now - rng.uniform(5, 9) * 86400, capacity)
    return doses
