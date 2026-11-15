from Hector9000.utils.Watchdog import Watchdog, PROCESS_GRACE_S


class Sink:
    def __init__(self):
        self.alerts, self.resolved = [], []
        self.active = set()

    def notify(self, level, message, key=None, source=""):
        self.alerts.append((level, key, message))
        self.active.add(key)
        return True

    def resolve(self, key, message):
        if key in self.active:
            self.active.discard(key)
            self.resolved.append((key, message))


def dog(expected=None):
    s = Sink()
    return Watchdog(s.notify, s.resolve, expected), s


def test_temperature_warns_then_recovers_with_hysteresis():
    w, s = dog()
    w.check({"cpu_temp": 76.0}, None, [], 0)
    assert s.alerts[-1][:2] == ("warning", "temp")
    w.check({"cpu_temp": 72.0}, None, [], 1)          # between OK and WARN: stay silent
    assert s.resolved == []
    w.check({"cpu_temp": 65.0}, None, [], 2)
    assert s.resolved[0][0] == "temp"


def test_critical_temperature():
    w, s = dog()
    w.check({"cpu_temp": 85.0}, None, [], 0)
    assert s.alerts[-1][0] == "critical"


def test_missing_values_are_ignored():
    w, s = dog()
    w.check({"cpu_temp": None, "disk_pct": None, "mem_pct": None, "throttled": None}, None, None, 0)
    assert s.alerts == []


def test_disk_and_memory_limits():
    w, s = dog()
    w.check({"disk_pct": 95.0, "mem_pct": 50.0}, None, [], 0)
    assert [a[1] for a in s.alerts] == ["disk"]


def test_undervoltage_now_alerts_but_old_flag_does_not():
    w, s = dog()
    w.check({"throttled": 0x50000}, None, [], 0)       # happened since boot, not now
    assert s.alerts == []
    w.check({"throttled": 0x50001}, None, [], 1)
    assert s.alerts[-1][1] == "power" and "Unterspannung" in s.alerts[-1][2]


def test_process_must_be_missing_for_a_while():
    w, s = dog(expected=["controller"])
    w.check({}, {"controller": False}, [], 100)
    assert s.alerts == []
    w.check({}, {"controller": False}, [], 100 + PROCESS_GRACE_S)
    assert s.alerts[-1][:2] == ("error", "proc:controller")
    w.check({}, {"controller": True}, [], 300)
    assert s.resolved[0][0] == "proc:controller"


def test_process_check_is_off_without_a_list_or_ps():
    w, s = dog(expected=None)
    w.check({}, {"controller": False}, [], 1000)
    w2, s2 = dog(expected=["controller"])
    w2.check({}, None, [], 1000)
    assert s.alerts == [] and s2.alerts == []


def test_bottle_low_only_with_known_level_and_resolves_on_refill():
    w, s = dog()
    bottles = [{"code": "gin", "name": "Gin", "remaining_pct": None, "remaining_ml": None, "capacity_ml": 700},
               {"code": "rum", "name": "Rum", "remaining_pct": 10.0, "remaining_ml": 70, "capacity_ml": 700}]
    w.check({}, None, bottles, 0)
    assert [a[1] for a in s.alerts] == ["bottle:rum"]
    bottles[1].update(remaining_pct=100.0, remaining_ml=700)
    w.check({}, None, bottles, 1)
    assert s.resolved[0][0] == "bottle:rum"
