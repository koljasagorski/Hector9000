import json
import os
import stat

import pytest

from Hector9000.utils import NotifySettings as NS

GOOD = {"enabled": True, "homeserver": "https://matrix.example.org/", "room": "!abc:example.org",
        "token": "syt_secrettoken123", "level": "error", "msgtype": "m.notice"}


def test_validate_accepts_a_complete_form_and_trims():
    clean, err = NS.validate(dict(GOOD, homeserver="  https://matrix.example.org/  "))
    assert err is None
    assert clean["homeserver"] == "https://matrix.example.org"      # trailing slash gone
    assert clean["enabled"] is True and clean["level"] == "error" and clean["msgtype"] == "m.notice"


def test_empty_token_keeps_the_stored_one():
    clean, err = NS.validate(dict(GOOD, token=""), current={"token": "syt_stored_token"})
    assert err is None and clean["token"] == "syt_stored_token"
    clean, err = NS.validate(dict(GOOD, token="syt_new_token_1"), current={"token": "syt_stored_token"})
    assert clean["token"] == "syt_new_token_1"


@pytest.mark.parametrize("field,value", [
    ("homeserver", "http://matrix.example.org"),            # plain http
    ("homeserver", "ftp://matrix.example.org"),
    ("homeserver", "matrix.example.org"),                   # no scheme
    ("homeserver", "https://user:pw@matrix.example.org"),   # credentials in the URL
    ("homeserver", "https://matrix.example.org/?x=1"),
    ("homeserver", "https://matrix.example.org/#frag"),
    ("homeserver", "https://matrix.example .org"),
    ("homeserver", "https://" + "a" * 300 + ".org"),
    ("room", "#alias:example.org"),                          # alias instead of id
    ("room", "!abc def:example.org"),
    ("room", "!" + "a" * 300),
    ("token", "short"),
    ("token", "has a space in it"),
    ("token", "line\nbreak123"),
    ("token", "x" * 600),
    ("level", "loud"),
    ("msgtype", "m.image"),
])
def test_validate_rejects_bad_values(field, value):
    clean, err = NS.validate(dict(GOOD, **{field: value}))
    assert clean is None and err


def test_http_is_only_allowed_when_the_operator_allowed_it():
    form = dict(GOOD, homeserver="http://192.168.1.5:8008")
    assert NS.validate(form)[0] is None
    assert NS.validate(form, allow_http=True)[1] is None


def test_enabled_needs_all_three_fields_but_a_draft_does_not():
    for missing in ("homeserver", "room", "token"):
        clean, err = NS.validate(dict(GOOD, **{missing: ""}))
        assert clean is None and err
    clean, err = NS.validate(dict(GOOD, enabled=False, token="", room=""))
    assert err is None and clean["enabled"] is False


def test_save_is_private_atomic_and_round_trips(tmp_path):
    NS.save(str(tmp_path), GOOD)
    path = tmp_path / NS.FILE_NAME
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert not [p for p in os.listdir(tmp_path) if p.startswith(".notify-")]     # no temp leftovers
    assert NS.load(str(tmp_path))["token"] == "syt_secrettoken123"
    NS.delete(str(tmp_path))
    NS.delete(str(tmp_path))                                  # deleting twice is fine
    assert NS.load(str(tmp_path)) is None


def test_load_survives_broken_files(tmp_path):
    path = tmp_path / NS.FILE_NAME
    assert NS.load(str(tmp_path)) is None
    path.write_text("{not json")
    assert NS.load(str(tmp_path)) is None
    path.write_text("[1, 2]")
    assert NS.load(str(tmp_path)) is None
    path.write_text(json.dumps({"homeserver": 5, "token": None, "level": "nope", "msgtype": 7}))
    s = NS.load(str(tmp_path))
    assert s["homeserver"] == "" and s["token"] == "" and s["level"] == "warning" and s["msgtype"] == "m.text"


def test_merge_env_lets_the_dashboard_win_and_can_switch_matrix_off():
    env = {"HECTOR_MATRIX_HOMESERVER": "https://old.example", "HECTOR_MATRIX_ROOM": "!old:x",
           "HECTOR_MATRIX_TOKEN": "oldtoken1", "HECTOR_NOTIFY_LEVEL": "info", "OTHER": "kept"}
    assert NS.merge_env(env, None) == env
    merged = NS.merge_env(env, dict(GOOD))
    assert merged["HECTOR_MATRIX_HOMESERVER"] == "https://matrix.example.org/"
    assert merged["HECTOR_MATRIX_TOKEN"] == "syt_secrettoken123"
    assert merged["HECTOR_NOTIFY_LEVEL"] == "error" and merged["OTHER"] == "kept"
    off = NS.merge_env(env, dict(GOOD, enabled=False))
    assert not any(k in off for k in NS.ENV_KEYS)
    assert env["HECTOR_MATRIX_TOKEN"] == "oldtoken1"           # the input is not modified


def test_describe_never_contains_the_token():
    for settings, env in ((dict(GOOD), {}), (None, {"HECTOR_MATRIX_HOMESERVER": "https://h.example",
                                                      "HECTOR_MATRIX_ROOM": "!r:x",
                                                      "HECTOR_MATRIX_TOKEN": "envtoken123"})):
        out = NS.describe(settings, env)
        text = json.dumps(out)
        assert "syt_secrettoken123" not in text and "envtoken123" not in text
        assert out["token_set"] is True and out["enabled"] is True
    assert NS.describe(None, {})["source"] == "none"
    assert NS.describe(None, {"HECTOR_MATRIX_HOMESERVER": "https://h", "HECTOR_MATRIX_TOKEN": "t"})["source"] == "env"
    assert NS.describe(dict(GOOD), {})["source"] == "dashboard"
    off = NS.describe(dict(GOOD, enabled=False), {})
    assert off["enabled"] is False and off["token_set"] is True and off["homeserver"]
