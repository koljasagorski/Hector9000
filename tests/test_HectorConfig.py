import json

from Hector9000.conf import HectorConfig as HC


def base():
    return {"hx711": {"CLK": 29, "ref": 2145}, "mqtt": {"SERVER": "localhost"}, "list": [1, 2]}


def test_override_merges_nested_values(tmp_path):
    f = tmp_path / "config.json"
    f.write_text(json.dumps({"hx711": {"ref": 2200}, "list": [9]}))
    cfg = HC.load_override(str(f), base())
    assert cfg["hx711"] == {"CLK": 29, "ref": 2200} and cfg["list"] == [9]
    assert cfg["mqtt"] == {"SERVER": "localhost"}


def test_missing_file_changes_nothing(tmp_path):
    assert HC.load_override(str(tmp_path / "nope.json"), base()) == base()


def test_broken_files_are_ignored(tmp_path, capsys):
    for text in ("{broken", "[1, 2]", "", "{" * 100000):
        f = tmp_path / "bad.json"
        f.write_text(text)
        assert HC.load_override(str(f), base()) == base()
    assert "ignoring" in capsys.readouterr().out


def test_real_config_still_has_its_defaults():
    assert HC.config["hx711"]["ref"] and HC.config["ws2812"]["count"] == 15
