#!/usr/bin/env python3
# -*- coding: utf8 -*-
##
#   HectorSystem.py       System actions (reboot, leave kiosk fullscreen)
#                         triggered from the WebUI via MQTT.
#
#   The commands are fixed argv lists from the config. The MQTT payload is
#   never passed to a command, and retained messages are ignored so a
#   leftover retained "reboot" on the broker cannot cause a reboot loop.
#

import os
import subprocess

REBOOT = "reboot"
KIOSK_EXIT = "kiosk_exit"

# Contract with Hector9000WebUI/kiosk.sh: the kiosk launcher stops relaunching
# Chromium once this flag file exists, and every kiosk Chromium process carries
# the profile marker in its command line. The flag lives in a directory created
# by kiosk.sh (owned by the kiosk user), so the kiosk can delete it again even
# when the backend runs as root (sticky /tmp would prevent that otherwise).
KIOSK_EXIT_FLAG = "/tmp/hector9000-kiosk/exit"
KIOSK_PROFILE_MARKER = "hector9000-kiosk-profile"

DEFAULT_COMMANDS = {
    # Needs passwordless sudo for the Hector user (default on Raspberry Pi OS).
    REBOOT: ["sudo", "-n", "systemctl", "reboot"],
    KIOSK_EXIT: [
        "sh", "-c",
        # "[p]" keeps the pattern from matching this sh process itself
        "touch %s; pkill -f '%s'" % (
            KIOSK_EXIT_FLAG,
            KIOSK_PROFILE_MARKER.replace("profile", "[p]rofile")),
    ],
}


def _default_runner(argv, grace=2.0):
    """Start ``argv`` detached. Raises if it fails right away.

    Waits up to ``grace`` seconds so a refused ``sudo -n`` or a pkill that
    found nothing is reported as an error instead of "ok". A command still
    running after that (e.g. the reboot in progress) counts as started.
    """
    env = dict(os.environ)
    # Started via nohup/rc.local the runtime dir may be missing.
    env.setdefault("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid())
    proc = subprocess.Popen(
        argv,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        code = proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        return
    if code != 0:
        raise RuntimeError("exit code %d" % code)


class HectorSystem:

    def __init__(self, commands=None, runner=None, log=print):
        self.commands = dict(DEFAULT_COMMANDS)
        if commands:
            self.commands.update(commands)
        self.runner = runner or _default_runner
        self.log = log

    def handle(self, action, retained=False):
        """Run the command for ``action``. Returns True if it was started."""
        if retained:
            self.log("System: ignoring retained '%s' message" % action)
            return False
        argv = self.commands.get(action)
        if not argv:
            self.log("System: no command configured for '%s'" % action)
            return False
        try:
            self.log("System: running '%s'" % action)
            self.runner(list(argv))
            return True
        except Exception as e:  # never take the controller down
            self.log("System: '%s' failed: %s" % (action, e))
            return False
