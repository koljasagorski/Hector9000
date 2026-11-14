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
import stat
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
    # The exit flag is written by write_kiosk_exit_flag() before this runs.
    KIOSK_EXIT: ["pkill", "-f", KIOSK_PROFILE_MARKER],
}


def write_kiosk_exit_flag(path=KIOSK_EXIT_FLAG):
    """Create the kiosk exit flag without following symlinks.

    The directory belongs to the kiosk user and the backend may run as root,
    so neither the directory nor the flag may be a symlink (otherwise a
    local user could make root create files elsewhere). Returns False if
    the directory does not exist (kiosk.sh not running).
    """
    parent, name = os.path.split(path)
    try:
        dfd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return False
    try:
        # O_EXCL: never open an existing object (symlink, FIFO, device ...)
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                     | os.O_CLOEXEC | os.O_NONBLOCK, 0o644, dir_fd=dfd)
        os.close(fd)
    except FileExistsError:
        st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
        if not stat.S_ISREG(st.st_mode):
            raise OSError("%s exists and is not a regular file" % path)
    finally:
        os.close(dfd)
    return True


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

    def __init__(self, commands=None, runner=None, log=print,
                 kiosk_exit_flag=KIOSK_EXIT_FLAG):
        self.commands = dict(DEFAULT_COMMANDS)
        if commands:
            self.commands.update(commands)
        self.runner = runner or _default_runner
        self.log = log
        self.kiosk_exit_flag = kiosk_exit_flag

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
            if action == KIOSK_EXIT and not write_kiosk_exit_flag(self.kiosk_exit_flag):
                self.log("System: %s missing, kiosk.sh not running?" % self.kiosk_exit_flag)
            self.log("System: running '%s'" % action)
            self.runner(list(argv))
            return True
        except Exception as e:  # never take the controller down
            self.log("System: '%s' failed: %s" % (action, e))
            return False
