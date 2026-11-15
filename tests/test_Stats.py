import json
from datetime import datetime, timezone

import pytest

from Hector9000.utils import Events
from Hector9000.utils.StatsRecorder import StatsRecorder, STALE_DOSE_S
from Hector9000.utils.StatsStore import StatsStore, abv_for

UTC = timezone.utc


def ts(y, mo, d, h=12, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=UTC).timestamp()


@pytest.fixture
def store():
    s = StatsStore(":memory:")
    yield s
    s.close()


@pytest.fixture
def errors():
    return []


@pytest.fixture
def rec(store, errors):
    return StatsRecorder(store, on_error=lambda *a: errors.append(a))


PLAN = [{"code": "vodka", "name": "Vodka", "ml": 60, "alcohol": True},
        {"code": "oj", "name": "Orange Juice", "ml": 120, "alcohol": False}]


def start(rec, now, drink_id=4, name="Screwdriver", alcohol=True, plan=PLAN):
    rec.handle(Events.TOPIC_DOSE, Events.dose_started(drink_id, name, alcohol, 400, plan, now), now)


def done(rec, now, status="ok", failed=None, duration=21.5, drink_id=4):
    rec.handle(Events.TOPIC_DOSE_DONE, Events.dose_finished(drink_id, status, duration, failed, now), now)


# ------------------------------------------------------------------ recorder --
def test_dose_lifecycle_books_drink_and_ingredients(rec, store):
    start(rec, 1000)
    assert rec.live["state"] == "dosing" and rec.live["drink"] == "Screwdriver"
    rec.handle("Hector9000/doseDrink/progress", "50.0", 1010)
    assert rec.live["progress"] == 50
    done(rec, 1021)
    assert rec.live["state"] == "idle"
    row = store.recent_doses(1)[0]
    assert row["status"] == "ok" and row["volume_ml"] == 180 and row["alcohol"] == 1
    assert row["duration_s"] == 21.5
    assert {r["code"]: r["ml"] for r in store.export_rows("ingredient_use")} == {"vodka": 60, "oj": 120}


def test_partial_dose_only_books_what_was_dosed(rec, store):
    start(rec, 1000)
    done(rec, 1030, status="partial", failed=["vodka"])
    row = store.recent_doses(1)[0]
    assert row["status"] == "partial" and row["volume_ml"] == 120
    assert [r["code"] for r in store.export_rows("ingredient_use")] == ["oj"]


def test_failed_dose_books_no_volume(rec, store):
    start(rec, 1000)
    done(rec, 1005, status="failed")
    assert store.recent_doses(1)[0]["volume_ml"] == 0
    assert store.export_rows("ingredient_use") == []


def test_old_controller_without_events_is_still_counted(rec, store):
    rec.handle("Hector9000/get_drinks/return", json.dumps({"drinks": [{"id": 7, "name": "Tschunk"}]}), 900)
    rec.handle("Hector9000/doseDrink", b"7", 1000)
    rec.handle("Hector9000/doseDrink/progress", "end", 1030)
    row = store.recent_doses(1)[0]
    assert row["drink"] == "Tschunk" and row["status"] == "ok"
    assert rec.live["state"] == "idle"


def test_plain_request_then_structured_event_make_one_dose(rec, store):
    rec.handle("Hector9000/doseDrink", b"4", 1000)
    start(rec, 1001)
    done(rec, 1020)
    doses = store.recent_doses(10)
    assert len(doses) == 1 and doses[0]["drink"] == "Screwdriver" and doses[0]["volume_ml"] == 180


def test_progress_end_does_not_close_a_structured_dose(rec, store):
    start(rec, 1000)
    rec.handle("Hector9000/doseDrink/progress", "end", 1015)
    assert rec.session is not None            # dose_done decides (partial / failed)
    done(rec, 1016, status="partial", failed=["oj"])
    assert store.recent_doses(1)[0]["status"] == "partial"


def test_stale_dose_is_aborted_and_reported(rec, store, errors):
    start(rec, 1000)
    rec.tick(1000 + STALE_DOSE_S - 1)
    assert rec.session is not None
    rec.tick(1000 + STALE_DOSE_S + 1)
    assert rec.session is None and store.recent_doses(1)[0]["status"] == "aborted"
    assert errors and errors[0][0] == "warning" and "Screwdriver" in errors[0][2]


def test_errors_are_stored_and_forwarded(rec, store, errors):
    rec.handle(Events.TOPIC_ERROR, Events.error_event("dosing", "Flasche leer?", now=5), 5)
    assert store.recent_errors()[0]["message"] == "Flasche leer?"
    assert errors == [("error", "dosing", "Flasche leer?")]


def test_actions_become_maintenance_events(rec, store):
    for topic in ("cleanMe", "dryMe", "system/reboot", "openAllValves"):
        rec.handle("Hector9000/" + topic, "true", 10)
    assert sorted(e["kind"] for e in store.recent_events()) == ["clean", "dry", "reboot", "valves_open"]


def test_retained_and_foreign_messages_are_ignored(rec, store):
    rec.handle("Hector9000/cleanMe", "true", 10, retained=True)
    rec.handle("Hector9000/Hardware/cleanMe", "3", 10)
    rec.handle("other/topic", "x", 10)
    assert store.recent_events() == []


def test_hostile_payloads_do_not_raise_or_store_junk(rec, store):
    for topic, payload in [(Events.TOPIC_DOSE, "{broken"), (Events.TOPIC_DOSE, "[1]"),
                           (Events.TOPIC_DOSE_DONE, '{"status": "weird"}'),
                           (Events.TOPIC_ERROR, "x" * 10000), ("Hector9000/doseDrink", "'; DROP TABLE doses;--"),
                           ("Hector9000/doseDrink/progress", "nan"), (Events.TOPIC_DOSE, b"\xff\xfe"),
                           (Events.TOPIC_DOSE, json.dumps({"drink_id": "x"}))]:
        rec.handle(topic, payload, 10)
    assert store.recent_doses() == [] and store.recent_errors() == []
    start(rec, 20, plan=[{"code": "x", "name": "x", "ml": 99999, "alcohol": False}])
    done(rec, 30)
    assert store.recent_doses(1)[0]["volume_ml"] == 0       # absurd amounts are dropped


def test_flood_is_rate_limited(rec, store):
    for i in range(500):
        rec.handle(Events.TOPIC_ERROR, Events.error_event("spam", "x%d" % i), 100)
    assert len(store.recent_errors(1000)) == 60


def test_service_going_offline_raises_an_error(rec, errors):
    rec.handle("Hector9000/status/controller", "online", 1)
    rec.handle("Hector9000/status/controller", "offline", 2, retained=True)
    assert errors == [("error", "service", "Dienst controller ist offline")]
    assert rec.services["controller"]["state"] == "offline"


def test_new_dose_closes_a_hanging_one(rec, store):
    start(rec, 1000)
    start(rec, 1100, drink_id=5, name="Virgin Sunrise")
    statuses = sorted((d["drink"], d["status"]) for d in store.recent_doses())
    assert statuses == [("Screwdriver", "aborted"), ("Virgin Sunrise", "running")]


# ------------------------------------------------------------------ statistics --
def seed(store, entries):
    """entries: (ts, drink, alcohol, ml, status, duration)"""
    for when, drink, alcohol, ml, status, dur in entries:
        i = store.start_dose(when, 1, drink, alcohol, 400, [{"code": "x", "name": "X", "ml": ml,
                                                                "alcohol": alcohol}])
        store.finish_dose(i, when + dur, status, dur)


def test_empty_database_gives_complete_structure(store):
    s = store.public_stats(ts(2026, 11, 14), tz=UTC)
    assert s["totals"]["served"] == 0 and s["records"]["best_day"] is None
    assert len(s["by_hour_of_day"]) == 24 and len(s["heatmap"]) == 7 and len(s["by_day"]) == 60
    assert s["session"] is None and s["timing"]["avg_s"] is None
    json.dumps(s)


def test_public_stats_aggregate(store):
    base = ts(2026, 11, 13, 20)
    seed(store, [
        (base, "Mate", False, 250, "ok", 10.0),
        (base + 600, "Moscow Mule", True, 300, "ok", 20.0),
        (base + 1200, "Moscow Mule", True, 300, "ok", 30.0),
        (base + 1800, "Mate", False, 250, "partial", 15.0),
        (base + 2400, "Gin", True, 100, "failed", 5.0),
        (ts(2026, 11, 14, 21), "Mate", False, 250, "ok", 12.0),
    ])
    s = store.public_stats(ts(2026, 11, 14, 22), tz=UTC)
    t = s["totals"]
    assert (t["served"], t["ok"], t["partial"], t["failed"]) == (5, 4, 1, 1)
    assert t["volume_ml"] == 1350 and t["alcoholic"] == 2 and t["non_alcoholic"] == 3
    assert [d["name"] for d in s["by_drink"]][:2] == ["Mate", "Moscow Mule"]
    assert s["by_drink"][0]["count"] == 3 and s["by_drink"][0]["share"] == 60.0
    assert s["by_hour_of_day"][20] == 4 and s["by_hour_of_day"][21] == 1
    assert s["recent"]["today"] == 1 and s["recent"]["yesterday"] == 4
    assert s["records"]["best_day"]["date"] == "2026-11-13"
    assert s["records"]["streak_longest_days"] == 2 and s["records"]["streak_current_days"] == 2
    assert s["records"]["most_popular"]["name"] == "Mate"
    assert s["timing"]["min_s"] == 10.0 and s["timing"]["max_s"] == 30.0
    assert s["sessions"]["count"] == 2 and s["session"] is not None and s["session"]["count"] == 1
    assert s["by_day"][-1]["date"] == "2026-11-14" and s["by_day"][-1]["count"] == 1
    assert s["by_weekday"][4] == 4        # Friday 2026-11-13
    json.dumps(s)


def test_alcohol_estimate_uses_known_strengths(store):
    rec = StatsRecorder(store)
    start(rec, 1000)                      # 60 ml vodka (40 %) = 24 ml pure alcohol
    done(rec, 1020)
    a = store.public_stats(2000, tz=UTC)["alcohol"]
    assert a["est_pure_ml"] == 24 and a["share_pct"] == 100.0
    assert a["est_units"] == pytest.approx(24 * 0.789 / 10, abs=0.1)
    assert abv_for("rum") == 40 and abv_for("sekt") == 11 and abv_for("zzz") == 35


def test_bottle_levels_need_a_refill_to_know_the_level(store):
    rec = StatsRecorder(store)
    start(rec, 1000)
    done(rec, 1020)
    before = {b["code"]: b for b in store.bottle_levels(2000)}
    assert before["vodka"]["remaining_ml"] is None and before["vodka"]["total_used_ml"] == 60
    store.refill_bottle("vodka", 1500, capacity_ml=700)
    start(rec, 1600)
    done(rec, 1620)
    after = {b["code"]: b for b in store.bottle_levels(2000)}
    assert after["vodka"]["remaining_ml"] == 640 and after["vodka"]["remaining_pct"] == 91.4
    assert after["oj"]["remaining_ml"] is None            # never refilled: unknown


def test_abort_running_on_restart(store):
    store.start_dose(100, 1, "A", False)
    assert store.abort_running(200) != []
    assert store.recent_doses(1)[0]["status"] == "aborted"


def test_export_rejects_unknown_tables(store):
    with pytest.raises(ValueError):
        store.export_rows("sqlite_master")
