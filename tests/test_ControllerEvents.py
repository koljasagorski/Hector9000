import json
import time

import pytest

from Hector9000 import HectorController as HC
from Hector9000.utils import Events


class FakeClient:
    def __init__(self):
        self.sent = []
        self._flip = False

    def publish(self, topic, payload=None, qos=0, retain=False):
        self.sent.append((topic, payload, retain))

    def want_write(self):
        # the controller spins on this (`while not want_write()` / `while want_write()`),
        # so it must flip instead of answering the same every time
        self._flip = not self._flip
        return self._flip

    def loop_write(self):
        pass

    def events(self, topic):
        return [json.loads(p) for t, p, _ in self.sent if t == topic]


class FakeHector:
    def __init__(self, results=(True,), fail_on=None):
        self.results = list(results)
        self.fail_on = fail_on

    def valve_dose(self, **kw):
        if self.fail_on == "dose":
            raise RuntimeError("scale exploded")
        return self.results.pop(0) if self.results else True

    def __getattr__(self, name):          # light_on, arm_out, ping, ...
        def call(*a, **k):
            if self.fail_on == name:
                raise RuntimeError(name + " failed")
        return call


class FakeDB:
    def get_Setting(self, name):
        return "400"


class Msg:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload
        self.retain = False


@pytest.fixture
def controller(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    c = HC.HectorController.__new__(HC.HectorController)
    c.client = FakeClient()
    c.db = FakeDB()
    c.TopicPrefix = "Hector9000/"
    c.LED = False
    c.hector = FakeHector()
    return c


def test_successful_dose_publishes_start_and_done(controller):
    controller.on_message(None, None, Msg("Hector9000/doseDrink", b"1"))
    start = controller.client.events(Events.TOPIC_DOSE)
    done = controller.client.events(Events.TOPIC_DOSE_DONE)
    assert len(start) == 1 and len(done) == 1
    assert start[0]["drink_id"] == 1 and start[0]["drink"] == "Extra Schuss Mate"
    ing = start[0]["ingredients"][0]
    assert ing["code"] == "mate" and ing["ml"] == 50 and ing["alcohol"] is False
    assert done[0]["status"] == "ok" and done[0]["failed"] == []
    assert controller.client.events(Events.TOPIC_ERROR) == []


def test_failed_ingredient_is_partial_and_reports_an_error(controller):
    controller.hector = FakeHector(results=[False])
    controller.on_message(None, None, Msg("Hector9000/doseDrink", b"1"))
    done = controller.client.events(Events.TOPIC_DOSE_DONE)[0]
    assert done["status"] == "partial" and done["failed"] == ["mate"]
    errors = controller.client.events(Events.TOPIC_ERROR)
    assert len(errors) == 1 and errors[0]["source"] == "dosing"
    assert "Flasche" in errors[0]["message"]


def test_exception_during_dose_is_reported_once(controller):
    controller.hector = FakeHector(fail_on="arm_out")
    controller.on_message(None, None, Msg("Hector9000/doseDrink", b"1"))
    assert controller.client.events(Events.TOPIC_DOSE_DONE)[0]["status"] == "failed"
    errors = controller.client.events(Events.TOPIC_ERROR)
    assert len(errors) == 1 and errors[0]["source"] == "controller"


def test_own_events_and_status_topics_are_ignored(controller):
    for topic in (Events.TOPIC_DOSE, Events.TOPIC_ERROR, Events.TOPIC_DOOM,
                  Events.TOPIC_STATUS + "controller"):
        controller.on_message(None, None, Msg(topic, b"{}"))
    assert controller.client.sent == []


def test_event_builders_clip_text_and_validate_status():
    long = "x" * 5000
    err = json.loads(Events.error_event("src", long, level="nonsense"))
    assert len(err["message"]) <= Events.MAX_TEXT and err["level"] == "error"
    with pytest.raises(ValueError):
        Events.dose_finished(1, "weird", 1.0)
    assert Events.parse(b"[1,2]") is None
    assert Events.parse("{" + '"a":' * 100000) is None
    assert Events.parse('{"a": 1}') == {"a": 1}


# ------------------------------------------------------------------ set clock --
class Runner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv):
        self.calls.append(argv)


def test_set_time_topic_sets_the_clock_and_answers_ok(controller):
    from Hector9000.utils import HectorSystem
    runner = Runner()
    controller.system = HectorSystem.HectorSystem(runner=runner, log=lambda m: None)
    controller.on_message(None, None, Msg("Hector9000/system/setTime", b"1790000000"))
    assert runner.calls == [["sudo", "-n", "date", "-u", "-s", "@1790000000"]]
    assert ("Hector9000/system/setTime/return", "ok", False) in controller.client.sent
    assert controller.client.events(Events.TOPIC_ERROR) == []


def test_set_time_with_a_bad_payload_runs_nothing_and_reports_a_warning(controller):
    from Hector9000.utils import HectorSystem
    runner = Runner()
    controller.system = HectorSystem.HectorSystem(runner=runner, log=lambda m: None)
    controller.on_message(None, None, Msg("Hector9000/system/setTime", b"1790000000; reboot"))
    assert runner.calls == []
    assert ("Hector9000/system/setTime/return", "error", False) in controller.client.sent
    err = controller.client.events(Events.TOPIC_ERROR)
    assert len(err) == 1 and err[0]["source"] == "system" and err[0]["level"] == "warning"


def test_retained_set_time_is_ignored_silently(controller):
    from Hector9000.utils import HectorSystem
    runner = Runner()
    controller.system = HectorSystem.HectorSystem(runner=runner, log=lambda m: None)
    msg = Msg("Hector9000/system/setTime", b"1790000000")
    msg.retain = True
    controller.on_message(None, None, msg)
    assert runner.calls == [] and controller.client.events(Events.TOPIC_ERROR) == []
