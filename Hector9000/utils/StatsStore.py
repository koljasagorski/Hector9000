#!/usr/bin/env python3
# -*- coding: utf8 -*-
##
#   StatsStore.py     SQLite store + all aggregations for the statistics pages
#
#   Own database file (stats.db) so that it never collides with h9k.db, which
#   belongs to the hardware setup (and has local edits on the Pi).
#
#   Everything that the public page shows is computed in public_stats(); the
#   admin dashboard builds on top of it (see StatsServer.py). No hardware and
#   no MQTT needed, so it is fully testable.
#

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS doses(
    id INTEGER PRIMARY KEY, ts REAL NOT NULL, ended REAL, drink_id INTEGER,
    drink TEXT NOT NULL, alcohol INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'running', cup_ml INTEGER,
    volume_ml INTEGER NOT NULL DEFAULT 0, duration_s REAL,
    planned TEXT, failed TEXT);
CREATE INDEX IF NOT EXISTS doses_ts ON doses(ts);
CREATE TABLE IF NOT EXISTS ingredient_use(
    id INTEGER PRIMARY KEY, dose_id INTEGER, ts REAL NOT NULL, code TEXT NOT NULL,
    name TEXT, ml INTEGER NOT NULL, alcohol INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS iu_code ON ingredient_use(code, ts);
CREATE TABLE IF NOT EXISTS errors(
    id INTEGER PRIMARY KEY, ts REAL NOT NULL, source TEXT, level TEXT,
    message TEXT, acked INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS errors_ts ON errors(ts);
CREATE TABLE IF NOT EXISTS events(
    id INTEGER PRIMARY KEY, ts REAL NOT NULL, kind TEXT NOT NULL, detail TEXT);
CREATE INDEX IF NOT EXISTS events_kind ON events(kind, ts);
CREATE TABLE IF NOT EXISTS samples(
    ts REAL PRIMARY KEY, cpu_temp REAL, load1 REAL, mem_pct REAL,
    disk_pct REAL, throttled INTEGER, uptime REAL);
CREATE TABLE IF NOT EXISTS bottles(
    code TEXT PRIMARY KEY, name TEXT, capacity_ml INTEGER NOT NULL DEFAULT 700,
    refilled REAL);
"""

# typical alcohol content in % vol by ingredient code / name keyword - only used
# for the "estimated alcohol" statistic, shown as an estimate everywhere
ABV_BY_KEYWORD = [
    ("vodka", 40), ("gin", 40), ("rum", 40), ("tequila", 40), ("whisk", 40),
    ("whisky", 40), ("brandy", 40), ("cognac", 40), ("korn", 32),
    ("likör", 20), ("liqueur", 20), ("aperol", 11), ("campari", 25),
    ("sekt", 11), ("prosecco", 11), ("wein", 12), ("wine", 12), ("bier", 5),
    ("beer", 5), ("vermouth", 15), ("triple", 30), ("curacao", 30),
]
DEFAULT_ALCOHOL_ABV = 35
ETHANOL_DENSITY = 0.789      # g/ml
UNIT_GRAMS = 10.0            # 1 "standard drink" (DE: 10 g pure alcohol)

SESSION_GAP_S = 2 * 3600     # a pause longer than this starts a new session
HEALTH_RETENTION_S = 30 * 86400
ERROR_RETENTION_S = 365 * 86400
# Hard caps per table (newest rows win). Years of normal use stay far below them; they only
# bite when somebody floods the (anonymous) MQTT broker, so the SD card and the RAM that
# public_stats() needs stay bounded.
MAX_DOSES = 20000
MAX_INGREDIENT_ROWS = 100000
MAX_ERRORS = 5000
MAX_EVENTS = 5000

COUNTED = ("ok", "partial")  # doses that poured something


def abv_for(code, name=""):
    text = ("%s %s" % (code, name)).lower()
    for keyword, abv in ABV_BY_KEYWORD:
        if keyword in text:
            return abv
    return DEFAULT_ALCOHOL_ABV


def percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


class StatsStore:

    def __init__(self, path=":memory:"):
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        with self.lock:
            if path != ":memory:":
                self.con.execute("PRAGMA journal_mode=WAL")
                self.con.execute("PRAGMA synchronous=NORMAL")
            self.con.executescript(SCHEMA)
            self.con.commit()

    # ------------------------------------------------------------- helpers --
    def _exec(self, sql, args=()):
        with self.lock:
            cur = self.con.execute(sql, args)
            self.con.commit()
            return cur

    def _all(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.con.execute(sql, args).fetchall()]

    def _one(self, sql, args=()):
        rows = self._all(sql, args)
        return rows[0] if rows else None

    def get_meta(self, key, default=None):
        row = self._one("SELECT value FROM meta WHERE key=?", (key,))
        return row["value"] if row else default

    def set_meta(self, key, value):
        self._exec("INSERT INTO meta(key,value) VALUES(?,?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

    def close(self):
        with self.lock:
            self.con.close()

    # --------------------------------------------------------------- write --
    def start_dose(self, ts, drink_id, drink, alcohol, cup_ml=None, planned=None):
        cur = self._exec(
            "INSERT INTO doses(ts, drink_id, drink, alcohol, status, cup_ml, planned) "
            "VALUES(?,?,?,?, 'running', ?, ?)",
            (ts, drink_id, str(drink)[:100], 1 if alcohol else 0, cup_ml,
             json.dumps(planned) if planned is not None else None))
        return cur.lastrowid

    def update_dose_plan(self, dose_id, drink, alcohol, cup_ml, planned):
        self._exec("UPDATE doses SET drink=?, alcohol=?, cup_ml=?, planned=? WHERE id=?",
                   (str(drink)[:100], 1 if alcohol else 0, cup_ml, json.dumps(planned), dose_id))

    def finish_dose(self, dose_id, ts, status, duration_s=None, failed=None):
        """Close a dose and book the ingredients that were really dosed."""
        row = self._one("SELECT * FROM doses WHERE id=?", (dose_id,))
        if not row or row["status"] != "running":
            return False
        failed = list(failed or [])
        planned = json.loads(row["planned"]) if row["planned"] else []
        volume = 0
        with self.lock:
            if status in COUNTED:
                for ing in planned:
                    if ing["code"] in failed:
                        continue
                    volume += int(ing["ml"])
                    self.con.execute(
                        "INSERT INTO ingredient_use(dose_id, ts, code, name, ml, alcohol) "
                        "VALUES(?,?,?,?,?,?)",
                        (dose_id, row["ts"], ing["code"], ing.get("name"), int(ing["ml"]),
                         1 if ing.get("alcohol") else 0))
            if duration_s is None:
                duration_s = max(0.0, ts - row["ts"])
            self.con.execute(
                "UPDATE doses SET ended=?, status=?, volume_ml=?, duration_s=?, failed=? WHERE id=?",
                (ts, status, volume, duration_s, json.dumps(failed), dose_id))
            self.con.commit()
        return True

    def add_error(self, ts, source, level, message):
        cur = self._exec("INSERT INTO errors(ts, source, level, message) VALUES(?,?,?,?)",
                         (ts, str(source)[:50], str(level)[:20], str(message)[:300]))
        return cur.lastrowid

    def ack_errors(self, ids=None):
        if ids is None:
            self._exec("UPDATE errors SET acked=1 WHERE acked=0")
        else:
            with self.lock:
                for i in ids:
                    self.con.execute("UPDATE errors SET acked=1 WHERE id=?", (int(i),))
                self.con.commit()

    def add_event(self, ts, kind, detail=""):
        self._exec("INSERT INTO events(ts, kind, detail) VALUES(?,?,?)",
                   (ts, str(kind)[:30], str(detail)[:300]))

    def add_sample(self, ts, cpu_temp=None, load1=None, mem_pct=None, disk_pct=None,
                   throttled=None, uptime=None):
        self._exec("INSERT OR REPLACE INTO samples VALUES(?,?,?,?,?,?,?)",
                   (ts, cpu_temp, load1, mem_pct, disk_pct, throttled, uptime))

    def prune(self, now):
        self._exec("DELETE FROM samples WHERE ts < ?", (now - HEALTH_RETENTION_S,))
        self._exec("DELETE FROM errors WHERE ts < ?", (now - ERROR_RETENTION_S,))
        self._exec("DELETE FROM events WHERE ts < ?", (now - ERROR_RETENTION_S,))
        self._trim("doses", MAX_DOSES)
        self._trim("ingredient_use", MAX_INGREDIENT_ROWS)
        self._trim("errors", MAX_ERRORS)
        self._trim("events", MAX_EVENTS)

    def _trim(self, table, keep):
        """Keep only the newest `keep` rows (ids grow with time). Table names are constants."""
        assert table in ("doses", "ingredient_use", "errors", "events")
        self._exec("DELETE FROM %s WHERE id <= (SELECT MAX(id) FROM %s) - ?" % (table, table), (int(keep),))

    def abort_running(self, ts, older_than=None):
        """Mark dosings that never ended (power loss, crash) as aborted."""
        sql = "SELECT id FROM doses WHERE status='running'"
        args = ()
        if older_than is not None:
            sql += " AND ts < ?"
            args = (older_than,)
        ids = [r["id"] for r in self._all(sql, args)]
        for i in ids:
            self.finish_dose(i, ts, "aborted")
        return ids

    # ------------------------------------------------------------- bottles --
    def set_bottle(self, code, capacity_ml=None, name=None):
        row = self._one("SELECT * FROM bottles WHERE code=?", (code,))
        if row is None:
            self._exec("INSERT INTO bottles(code, name, capacity_ml) VALUES(?,?,?)",
                       (code, name, int(capacity_ml or 700)))
        else:
            self._exec("UPDATE bottles SET capacity_ml=?, name=COALESCE(?, name) WHERE code=?",
                       (int(capacity_ml or row["capacity_ml"]), name, code))

    def refill_bottle(self, code, now, capacity_ml=None):
        self.set_bottle(code, capacity_ml)
        self._exec("UPDATE bottles SET refilled=? WHERE code=?", (now, code))

    def bottle_levels(self, now, known=None):
        """Estimated level per bottle. Without a recorded refill the level is unknown."""
        known = dict(known or {})                     # code -> name from the hardware DB
        used_total = {r["code"]: r for r in self._all(
            "SELECT code, MAX(name) AS name, SUM(ml) AS ml, MAX(alcohol) AS alcohol "
            "FROM ingredient_use GROUP BY code")}
        bottles = {r["code"]: r for r in self._all("SELECT * FROM bottles")}
        out = []
        for code in sorted(set(known) | set(used_total) | set(bottles)):
            bottle = bottles.get(code)
            refilled = bottle["refilled"] if bottle else None
            since = self._one(
                "SELECT COALESCE(SUM(ml),0) AS ml FROM ingredient_use WHERE code=? AND ts>=?",
                (code, refilled if refilled is not None else 0))["ml"]
            capacity = bottle["capacity_ml"] if bottle else 700
            level = None
            if refilled is not None:
                level = max(0, capacity - since)
            out.append({
                "code": code,
                "name": (bottle and bottle["name"]) or known.get(code)
                        or (used_total.get(code) or {}).get("name") or code,
                "capacity_ml": capacity,
                "used_ml": int(since),
                "total_used_ml": int((used_total.get(code) or {}).get("ml") or 0),
                "refilled": refilled,
                "remaining_ml": level,
                "remaining_pct": None if level is None else round(100.0 * level / max(capacity, 1), 1),
                "alcohol": bool((used_total.get(code) or {}).get("alcohol")),
            })
        return out

    # --------------------------------------------------------------- read ---
    def recent_errors(self, limit=50, include_acked=True):
        sql = "SELECT * FROM errors %s ORDER BY ts DESC LIMIT ?" % (
            "" if include_acked else "WHERE acked=0")
        return self._all(sql, (int(limit),))

    def recent_events(self, limit=100):
        return self._all("SELECT * FROM events ORDER BY ts DESC LIMIT ?", (int(limit),))

    def recent_doses(self, limit=50):
        return self._all("SELECT * FROM doses ORDER BY ts DESC LIMIT ?", (int(limit),))

    def samples_since(self, since):
        return self._all("SELECT * FROM samples WHERE ts>=? ORDER BY ts", (since,))

    def export_rows(self, table):
        if table not in ("doses", "ingredient_use", "errors", "events", "samples"):
            raise ValueError("unknown table")
        return self._all("SELECT * FROM %s ORDER BY ts" % table)

    # ---------------------------------------------------------- statistics --
    def public_stats(self, now, tz=None, health=None):
        """Everything the public page shows. Contains no secrets or personal data."""
        tz = tz or datetime.now().astimezone().tzinfo

        def local(ts):
            return datetime.fromtimestamp(ts, tz)

        rows = self._all("SELECT * FROM doses ORDER BY ts")
        served = [r for r in rows if r["status"] in COUNTED]
        ok_rows = [r for r in rows if r["status"] == "ok"]
        finished = [r for r in rows if r["status"] != "running"]
        failed = [r for r in rows if r["status"] == "failed"]
        aborted = [r for r in rows if r["status"] == "aborted"]
        ingredients = self._all("SELECT * FROM ingredient_use")

        volume_ml = sum(r["volume_ml"] for r in served)
        alcoholic = [r for r in served if r["alcohol"]]
        today = local(now).date()

        def day_of(r):
            return local(r["ts"]).date()

        def count_since(seconds):
            return sum(1 for r in served if r["ts"] >= now - seconds)

        # --- time distributions ------------------------------------------------
        by_hour = [0] * 24
        by_weekday = [0] * 7
        heat = [[0] * 24 for _ in range(7)]
        by_day = {}
        for r in served:
            d = local(r["ts"])
            by_hour[d.hour] += 1
            by_weekday[d.weekday()] += 1
            heat[d.weekday()][d.hour] += 1
            key = d.date()
            slot = by_day.setdefault(key, {"count": 0, "ml": 0})
            slot["count"] += 1
            slot["ml"] += r["volume_ml"]

        days60 = []
        for i in range(59, -1, -1):
            d = today - timedelta(days=i)
            slot = by_day.get(d, {"count": 0, "ml": 0})
            days60.append({"date": d.isoformat(), "count": slot["count"], "ml": slot["ml"]})

        hour_now = local(now).replace(minute=0, second=0, microsecond=0)
        last24 = []
        for i in range(23, -1, -1):
            start = hour_now - timedelta(hours=i)
            lo, hi = start.timestamp(), (start + timedelta(hours=1)).timestamp()
            last24.append({"ts": lo, "count": sum(1 for r in served if lo <= r["ts"] < hi)})

        weeks = []
        monday = today - timedelta(days=today.weekday())
        for i in range(11, -1, -1):
            start = monday - timedelta(weeks=i)
            n = sum(slot["count"] for d, slot in by_day.items()
                    if start <= d < start + timedelta(days=7))
            weeks.append({"week": start.isoformat(), "count": n})

        # --- per drink / per ingredient ----------------------------------------
        drinks = {}
        for r in served:
            e = drinks.setdefault(r["drink"], {
                "name": r["drink"], "count": 0, "volume_ml": 0, "alcohol": bool(r["alcohol"]),
                "durations": [], "last_ts": 0, "first_ts": r["ts"]})
            e["count"] += 1
            e["volume_ml"] += r["volume_ml"]
            e["last_ts"] = max(e["last_ts"], r["ts"])
            if r["status"] == "ok" and r["duration_s"]:
                e["durations"].append(r["duration_s"])
        by_drink = []
        for e in drinks.values():
            durs = e.pop("durations")
            e["avg_s"] = round(sum(durs) / len(durs), 1) if durs else None
            e["share"] = round(100.0 * e["count"] / max(len(served), 1), 1)
            by_drink.append(e)
        by_drink.sort(key=lambda e: (-e["count"], e["name"]))

        ing = {}
        pure_ml = 0.0
        for u in ingredients:
            e = ing.setdefault(u["code"], {
                "code": u["code"], "name": u["name"] or u["code"], "ml": 0, "uses": 0,
                "alcohol": bool(u["alcohol"]), "last_ts": 0})
            e["ml"] += u["ml"]
            e["uses"] += 1
            e["last_ts"] = max(e["last_ts"], u["ts"])
            if u["alcohol"]:
                pure_ml += u["ml"] * abv_for(u["code"], u["name"] or "") / 100.0
        by_ingredient = sorted(ing.values(), key=lambda e: -e["ml"])
        total_ing_ml = sum(e["ml"] for e in by_ingredient) or 1
        for e in by_ingredient:
            e["share"] = round(100.0 * e["ml"] / total_ing_ml, 1)

        # --- timing --------------------------------------------------------------
        durs = [r["duration_s"] for r in ok_rows if r["duration_s"]]
        timing = {
            "avg_s": round(sum(durs) / len(durs), 1) if durs else None,
            "median_s": round(percentile(durs, 0.5), 1) if durs else None,
            "p90_s": round(percentile(durs, 0.9), 1) if durs else None,
            "min_s": round(min(durs), 1) if durs else None,
            "max_s": round(max(durs), 1) if durs else None,
        }

        # --- sessions & streaks --------------------------------------------------
        sessions = []
        for r in served:
            if sessions and r["ts"] - sessions[-1]["end"] <= SESSION_GAP_S:
                s = sessions[-1]
                s["end"] = r["ts"]
                s["count"] += 1
                s["ml"] += r["volume_ml"]
            else:
                sessions.append({"start": r["ts"], "end": r["ts"], "count": 1,
                                 "ml": r["volume_ml"]})
        current = None
        if sessions and now - sessions[-1]["end"] <= SESSION_GAP_S:
            s = sessions[-1]
            dur = max(s["end"] - s["start"], 60)
            current = dict(s, duration_s=round(s["end"] - s["start"]),
                           per_hour=round(s["count"] * 3600.0 / dur, 1))
        biggest_session = max(sessions, key=lambda s: s["count"]) if sessions else None
        longest_session = max(sessions, key=lambda s: s["end"] - s["start"]) if sessions else None

        active_days = sorted(by_day)
        longest_streak = streak = 0
        prev = None
        for d in active_days:
            streak = streak + 1 if prev and (d - prev).days == 1 else 1
            longest_streak = max(longest_streak, streak)
            prev = d
        current_streak = 0
        if active_days and (today - active_days[-1]).days <= 1:
            current_streak = 1
            for a, b in zip(reversed(active_days[:-1]), reversed(active_days)):
                if (b - a).days == 1:
                    current_streak += 1
                else:
                    break

        # --- records ----------------------------------------------------------------
        best_day = max(by_day.items(), key=lambda kv: kv[1]["count"]) if by_day else None
        hour_buckets = {}
        for r in served:
            h = local(r["ts"]).replace(minute=0, second=0, microsecond=0)
            hour_buckets[h] = hour_buckets.get(h, 0) + 1
        best_hour = max(hour_buckets.items(), key=lambda kv: kv[1]) if hour_buckets else None
        biggest_drink = max(served, key=lambda r: r["volume_ml"]) if served else None
        busiest_hour = by_hour.index(max(by_hour)) if served else None
        busiest_weekday = by_weekday.index(max(by_weekday)) if served else None
        popular = by_drink[0] if by_drink else None
        rarest = by_drink[-1] if len(by_drink) > 1 else None

        # --- alcohol -------------------------------------------------------------------
        pure_g = pure_ml * ETHANOL_DENSITY
        alcohol = {
            "drinks": len(alcoholic),
            "share_pct": round(100.0 * len(alcoholic) / max(len(served), 1), 1),
            "est_pure_ml": round(pure_ml),
            "est_units": round(pure_g / UNIT_GRAMS, 1),
            "est_per_alcoholic_drink_ml": round(pure_ml / len(alcoholic), 1) if alcoholic else None,
        }

        # --- maintenance / system / errors / doom ----------------------------------------
        kinds = {r["kind"]: r["n"] for r in self._all(
            "SELECT kind, COUNT(*) AS n FROM events GROUP BY kind")}
        last_clean = self._one("SELECT MAX(ts) AS ts FROM events WHERE kind='clean'")["ts"]
        doom = self._one("SELECT COUNT(*) AS n, COALESCE(SUM(CAST(detail AS INTEGER)),0) AS s, "
                         "COALESCE(MAX(CAST(detail AS INTEGER)),0) AS m FROM events "
                         "WHERE kind='doom'")
        err = {
            "total": self._one("SELECT COUNT(*) AS n FROM errors")["n"],
            "last_24h": self._one("SELECT COUNT(*) AS n FROM errors WHERE ts>=?",
                                  (now - 86400,))["n"],
            "last_7d": self._one("SELECT COUNT(*) AS n FROM errors WHERE ts>=?",
                                 (now - 7 * 86400,))["n"],
            "by_source": {r["source"]: r["n"] for r in self._all(
                "SELECT source, COUNT(*) AS n FROM errors GROUP BY source")},
            "last_ts": self._one("SELECT MAX(ts) AS ts FROM errors")["ts"],
        }
        samples = self._all("SELECT * FROM samples WHERE ts>=?", (now - 86400,))
        temps = [s["cpu_temp"] for s in samples if s["cpu_temp"] is not None]
        machine = {
            "boots": kinds.get("boot", 0),
            "samples_24h": len(samples),
            "temp_avg_c": round(sum(temps) / len(temps), 1) if temps else None,
            "temp_max_c": round(max(temps), 1) if temps else None,
            "throttled_events": self._one(
                "SELECT COUNT(*) AS n FROM samples WHERE throttled>0")["n"],
        }
        machine.update(health or {})

        first_ts = served[0]["ts"] if served else None
        last_ts = served[-1]["ts"] if served else None
        liters = volume_ml / 1000.0
        span_days = max(len(active_days), 1)

        return {
            "generated_at": now,
            "totals": {
                "served": len(served), "ok": len(ok_rows),
                "partial": len([r for r in rows if r["status"] == "partial"]),
                "failed": len(failed), "aborted": len(aborted),
                "volume_ml": volume_ml, "liters": round(liters, 1),
                "alcoholic": len(alcoholic), "non_alcoholic": len(served) - len(alcoholic),
                "avg_ml": round(volume_ml / len(served)) if served else None,
                "success_pct": round(100.0 * len(ok_rows) / len(finished), 1) if finished else None,
                "different_drinks": len(by_drink),
                "different_ingredients": len(by_ingredient),
            },
            "recent": {
                "last_hour": count_since(3600), "last_24h": count_since(86400),
                "last_7d": count_since(7 * 86400),
                "today": sum(1 for r in served if day_of(r) == today),
                "yesterday": sum(1 for r in served if day_of(r) == today - timedelta(days=1)),
                "today_ml": sum(r["volume_ml"] for r in served if day_of(r) == today),
                "first_today": next((r["ts"] for r in served if day_of(r) == today), None),
                "last_ts": last_ts,
                "since_last_s": round(now - last_ts) if last_ts else None,
                "first_ts": first_ts,
            },
            "session": current,
            "sessions": {"count": len(sessions), "biggest": biggest_session,
                         "longest": longest_session},
            "records": {
                "best_day": best_day and {"date": best_day[0].isoformat(),
                                          "count": best_day[1]["count"], "ml": best_day[1]["ml"]},
                "best_hour": best_hour and {"ts": best_hour[0].timestamp(), "count": best_hour[1]},
                "busiest_hour_of_day": busiest_hour,
                "busiest_weekday": busiest_weekday,
                "biggest_drink": biggest_drink and {"name": biggest_drink["drink"],
                                                    "ml": biggest_drink["volume_ml"]},
                "most_popular": popular and {"name": popular["name"], "count": popular["count"]},
                "rarest": rarest and {"name": rarest["name"], "count": rarest["count"]},
                "streak_current_days": current_streak,
                "streak_longest_days": longest_streak,
                "active_days": len(active_days),
                "avg_per_active_day": round(len(served) / span_days, 1) if served else None,
            },
            "timing": timing,
            "by_drink": by_drink,
            "by_ingredient": by_ingredient,
            "by_hour_of_day": by_hour,
            "by_weekday": by_weekday,
            "heatmap": heat,
            "by_day": days60,
            "by_week": weeks,
            "last_24h_hourly": last24,
            "alcohol": alcohol,
            "maintenance": {
                "clean": kinds.get("clean", 0), "dry": kinds.get("dry", 0),
                "valves_open": kinds.get("valves_open", 0),
                "valves_close": kinds.get("valves_close", 0),
                "reboots": kinds.get("reboot", 0), "kiosk_exits": kinds.get("kiosk_exit", 0),
                "last_clean_ts": last_clean,
            },
            "errors": err,
            "doom": {"plays": doom["n"], "seconds": doom["s"], "longest_s": doom["m"]},
            "machine": machine,
            "fun": {
                "bathtubs": round(liters / 150.0, 2),
                "bottles_070": round(volume_ml / 700.0, 1),
                "shots_2cl": round(volume_ml / 20.0),
                "coffee_cups": round(volume_ml / 200.0),
                "per_hour_avg": round(len(served) / max(((last_ts or 0) - (first_ts or 0)) / 3600.0, 1), 1)
                                if served else None,
            },
        }
