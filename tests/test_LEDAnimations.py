import random

from Hector9000.utils import LEDAnimations as la


def engine(**kw):
    kw.setdefault("rng", random.Random(1))
    kw.setdefault("brightness", 1.0)
    return la.LEDEngine(count=15, base=5, **kw)


def lit(frame, idx):
    return [i for i in idx if frame[i] != (0, 0, 0)]


def test_frame_has_one_colour_per_pixel_and_valid_values():
    e = engine()
    for t in (0, 1.5, 33.3):
        f = e.frame(t)
        assert len(f) == 15
        assert all(len(c) == 3 and all(0 <= v <= 255 for v in c) for c in f)


def test_brightness_caps_output():
    e = la.LEDEngine(count=15, base=5, brightness=0.25, rng=random.Random(1))
    assert max(max(c) for c in e.frame(1.0)) <= 64


def test_theme_by_id_and_by_json():
    e = engine()
    e.handle("Hector9000/LEDStrip/theme", "pink", 1)
    assert e.theme == "pink" and e.palette[0] == la.hex_to_rgb("#EB5D9D")
    e.handle("Hector9000/LEDStrip/theme", '{"id": "x", "colors": ["#010203"]}', 2)
    assert e.palette == [(1, 2, 3)]


def test_broken_messages_are_ignored():
    e = engine()
    for topic, payload in [("Hector9000/LEDStrip/theme", "nope"),
                           ("Hector9000/LEDStrip/theme", "{broken"),
                           ("Hector9000/LEDStrip/ambient", "disco"),
                           ("Hector9000/doseDrink/progress", "abc"),
                           ("Hector9000/LEDStrip/dosedrink", "a,b,c"),
                           ("Hector9000/whatever", b"\xff"),
                           ("Hector9000/LEDStrip/dosedrink", "inf,1e999,nan,0"),
                           ("Hector9000/doseDrink/progress", "inf"),
                           ("Hector9000/LEDStrip/theme", "[" * 100000 + "]" * 100000),
                           ("Hector9000/LEDStrip/theme", '{"id": "x", "colors": "#010203"}'),
                           ("Hector9000/LEDStrip/theme", '{"id": "x", "colors": [1, 2]}'),
                           ("Hector9000/LEDStrip/theme", '["pink"]'),
                           ("Hector9000/LEDStrip/theme", "{" + '"a":' * 200 + "1" + "}" * 200),
                           ("Hector9000/LEDStrip/theme", "{" + "[" * 1000),
                           ("Hector9000/LEDStrip/theme", "{" + '"a":{' * 5000 + "}" * 5001)]:
        e.handle(topic, payload, 1)
    assert e.theme == "dark" and e.ambient == "theme"
    assert len(e.frame(2)) == 15


def test_dosing_fills_column_bottom_up_with_drink_colour():
    e = engine()
    e.handle("Hector9000/LEDStrip/dosedrink", "255,0,0,0", 10)
    e.handle("Hector9000/doseDrink/progress", "50", 11)
    f = e.frame(11)
    column = list(range(5, 15))
    assert lit(f, column) == column[:5]          # lower half lit
    assert f[5] == (255, 0, 0)
    e.handle("Hector9000/doseDrink/progress", "100", 12)
    assert lit(e.frame(12), column) == column


def test_column_reversed_fills_from_the_other_end():
    e = engine(column_reversed=True)
    e.handle("Hector9000/doseDrink/progress", "30", 1)
    assert lit(e.frame(1), range(5, 15)) == [12, 13, 14]


def test_progress_end_plays_finish_then_returns_to_ambient():
    e = engine()
    e.handle("Hector9000/doseDrink/progress", "40", 1)
    e.handle("Hector9000/doseDrink/progress", "end", 2)
    assert e.event["name"] == "finish"
    e.frame(2 + la.EVENT_SECONDS["finish"] + 0.1)
    assert e.event is None


def test_select_and_deselect():
    e = engine()
    e.handle("Hector9000/LEDStrip/select", "", 1)
    assert e.event["name"] == "select"
    e.handle("Hector9000/LEDStrip/deselect", "", 2)
    assert e.event is None


def test_clean_valves_and_reboot_events():
    e = engine()
    e.handle("Hector9000/cleanMe", "true", 1)
    assert e.event["name"] == "clean"
    e.handle("Hector9000/openAllValves", "true", 2)
    assert e.event["name"] == "valves"
    e.handle("Hector9000/system/reboot", "true", 3)
    assert e.event["name"] == "shutdown"
    assert all(c == (0, 0, 0) for c in e.frame(10))   # faded out
    e.handle("Hector9000/LEDStrip/deselect", "", 11)  # cannot cancel shutdown
    assert e.event["name"] == "shutdown"


def test_idle_after_inactivity_dims_and_activity_wakes():
    e = engine(idle_after=60)
    bright = max(max(c) for c in e.frame(10))
    assert not e.is_idle(10)
    assert e.is_idle(100)
    dim = max(max(c) for t in range(100, 110) for c in e.frame(t))
    assert dim < bright
    e.handle("Hector9000/LEDStrip/activity", "", 120)
    assert not e.is_idle(121)


def test_ambient_modes():
    e = engine()
    e.handle("Hector9000/LEDStrip/ambient", "off", 1)
    assert all(c == (0, 0, 0) for c in e.frame(2))
    e.handle("Hector9000/LEDStrip/ambient", "idle", 3)
    assert e.is_idle(3)
    e.handle("Hector9000/LEDStrip/ambient", "random", 4)
    e.frame(5)
    assert 25 <= e._random_until <= 45                # next change in 20-40 s
    e.frame(46)
    assert 66 <= e._random_until <= 86                # a new pick was made
    assert e._random_fx in la.RANDOM_EFFECTS


def test_events_override_ambient_off():
    e = engine()
    e.handle("Hector9000/LEDStrip/ambient", "off", 1)
    e.handle("Hector9000/doseDrink/progress", "100", 2)
    assert any(c != (0, 0, 0) for c in e.frame(2))


def test_legacy_controller_messages():
    e = engine()
    e.handle("Hector9000/LEDStrip/standart", "80,80,30,3", 1)   # sent on connect
    e.handle("Hector9000/LEDStrip/drinkfinish", "true", 2)
    assert e.event["name"] == "finish"
    e.handle("Hector9000/LEDStrip/standby", "80,80,30,0", 3)
    assert e.event is None and e.is_idle(3)
