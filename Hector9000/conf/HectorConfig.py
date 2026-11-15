import json
import os

# hardware configuration
# all pin numbers are corresponding to physical P1 connector!

config = {
    "hx711": {
        "CLK": 29,
        "DAT": 31,
        "ref": 2145  # calibration yields 100 g <-> readout 214500
    },
    "pca9685": {
        "freq": 60,
        "valvechannels": range(12),  # 0..11
        "valvepositions": [  # (open, closed)
            (375, 535),  # ch 0
            (375, 510),  # ch 1
            (375, 515),  # ch 2
            (375, 515),  # ch 3
            (375, 520),  # ch 4
            (375, 520),  # ch 5
            (375, 235),  # ch 6
            (375, 250),  # ch 7
            (375, 250),  # ch 8
            (375, 240),  # ch 9
            (375, 235),  # ch 10
            (375, 250)  # ch 11
        ],
        "fingerchannel": 12,
        "fingerpositions": (280, 430, 450),  # retracted, above bell, bell
        "lightpin": 22,
        "lightpwmchannel": 13,
        "lightpositions": (0, 500)
    },
    "a4988": {
        "ENABLE": 11,
        "MS1": 13,
        "MS2": 15,
        "MS3": 19,
        "RESET": 21,
        "SLEEP": 23,
        "STEP": 37,
        "DIR": 33,
        "numSteps": 261  # number of steps between IN and OUT positions
    },
    "arm": {
        "SENSE": 16
    },
    "pump": {
        "MOTOR": 18
    },
    "ws2812": {
        # D18
        "DIN": 12,
        "output": "ws2812",       # "ws2812" (strip on the Pi) or "console" (test)
        "count": 15,              # LEDs in total
        "base": 5,                # first LEDs: under the glass
        "column_reversed": False,  # True if the column's first LED is at the top
        "brightness": 0.5,        # 0..1, caps power draw and glare
        "idle_minutes": 5         # dim idle animation after no touch (0 = never)
    },
    "mqtt": {
        "SERVER": "localhost",
        "TOPICPREFIX": "Hector9000/Main/"
    },
    "system": {
        # Commands triggered from the WebUI settings (argv lists, no shell
        # expansion of MQTT payloads). Leave out a key to keep the default
        # from Hector9000/utils/HectorSystem.py, set it to [] to disable it.
        # "reboot": ["sudo", "-n", "systemctl", "reboot"],
        # "kiosk_exit": ["pkill", "-f", "hector9000-kiosk-profile"],
        #   (the exit flag /tmp/hector9000-kiosk/exit is always written first)
    }
}


def merge_config(base, extra):
    """Recursively copy the values of `extra` into `base` (dicts are merged, the rest replaced)."""
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge_config(base[key], value)
        else:
            base[key] = value
    return base


def load_override(path, target=None):
    """Apply a JSON file on top of `config`, so that a machine's calibration and
    valve positions live outside the git-tracked file (no update conflicts).

    Missing file: nothing happens. A broken file is reported and ignored - the
    machine must still start with the defaults above.
    """
    target = config if target is None else target
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("top level must be an object")
    except FileNotFoundError:
        return target
    except (OSError, ValueError, RecursionError) as e:
        print("HectorConfig WARNING: ignoring %s: %s" % (path, e))
        return target
    return merge_config(target, data)


# /etc/hector9000/config.json (or $HECTOR_CONFIG), e.g. {"hx711": {"ref": 2200}}
load_override(os.environ.get("HECTOR_CONFIG", "/etc/hector9000/config.json"))
