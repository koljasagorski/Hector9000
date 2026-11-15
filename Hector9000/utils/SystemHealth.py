#!/usr/bin/env python3
# -*- coding: utf8 -*-
##
#   SystemHealth.py     Reads the vital signs of the Pi (and degrades gracefully)
#
#   Every reader returns None when the information is not available (for
#   example on a laptop during development) instead of raising.
#

import os
import shutil
import socket
import subprocess
import time

# Bits of `vcgencmd get_throttled`
THROTTLE_BITS = {
    0x1: "Unterspannung (jetzt)",
    0x2: "Frequenz begrenzt (jetzt)",
    0x4: "gedrosselt (jetzt)",
    0x8: "Temperaturlimit (jetzt)",
    0x10000: "Unterspannung (seit Start)",
    0x20000: "Frequenz begrenzt (seit Start)",
    0x40000: "gedrosselt (seit Start)",
    0x80000: "Temperaturlimit (seit Start)",
}

# processes that make up a running Hector (substring of the command line)
EXPECTED_PROCESSES = {
    "server": "HectorServer.py",
    "controller": "HectorController.py",
    "led": "LEDStripServer.py",
}


def _run(args, timeout=3):
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return out.stdout.strip() if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000.0, 1)
    except (OSError, ValueError):
        pass
    out = _run(["vcgencmd", "measure_temp"])        # temp=48.3'C
    try:
        return float(out.split("=")[1].split("'")[0])
    except (AttributeError, IndexError, ValueError):
        return None


def load1():
    try:
        return round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):
        return None


def mem_pct():
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                key, _, rest = line.partition(":")
                info[key] = int(rest.split()[0])
        return round(100.0 * (1 - info["MemAvailable"] / info["MemTotal"]), 1)
    except (OSError, KeyError, ValueError, IndexError):
        return None


def disk_pct(path="/"):
    try:
        usage = shutil.disk_usage(path)
        return round(100.0 * usage.used / usage.total, 1)
    except OSError:
        return None


def uptime_s():
    try:
        with open("/proc/uptime") as f:
            return int(float(f.read().split()[0]))
    except (OSError, ValueError, IndexError):
        return None


def throttled():
    """Raw `vcgencmd get_throttled` value, or None when not on a Pi."""
    out = _run(["vcgencmd", "get_throttled"])        # throttled=0x50005
    try:
        return int(out.split("=")[1], 16)
    except (AttributeError, IndexError, ValueError):
        return None


def throttle_reasons(value):
    return [text for bit, text in THROTTLE_BITS.items() if value and value & bit]


def clock_synced():
    out = _run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
    if out is None:
        return None
    return out.lower() == "yes"


def ip_addresses():
    out = _run(["hostname", "-I"])
    if out:
        return out.split()[:4]
    try:        # works without a default route being reachable: no packet is sent
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return [ip]
    except OSError:
        return []


def running_processes(expected=None):
    """name -> True/False for every expected process, or None if `ps` is unavailable."""
    expected = EXPECTED_PROCESSES if expected is None else expected
    out = _run(["ps", "-eo", "args"], timeout=5)
    if out is None:
        return None
    return {name: any(pattern in line and "pytest" not in line for line in out.splitlines())
            for name, pattern in expected.items()}


def git_revision(path):
    if not path or not os.path.isdir(path):
        return None
    # read-only questions about a checkout that belongs to another user (e.g. root in /opt):
    # git refuses that ("dubious ownership") unless the directory is declared safe on the command line
    git = ["git", "-c", "safe.directory=" + os.path.abspath(path), "-C", path]
    rev = _run(git + ["rev-parse", "--short", "HEAD"])
    branch = _run(git + ["rev-parse", "--abbrev-ref", "HEAD"])
    return {"rev": rev, "branch": branch} if rev else None


def snapshot(disk_path="/"):
    """One consistent reading of everything that is cheap to read."""
    t = throttled()
    return {
        "ts": time.time(),
        "cpu_temp": cpu_temp(),
        "load1": load1(),
        "mem_pct": mem_pct(),
        "disk_pct": disk_pct(disk_path),
        "uptime": uptime_s(),
        "throttled": t,
    }
