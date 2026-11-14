import os

from Hector9000.utils import HectorSystem as hs


class FakeRunner:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def __call__(self, argv):
        if self.fail:
            raise OSError("boom")
        self.calls.append(argv)


def make(runner, **kw):
    return hs.HectorSystem(runner=runner, log=lambda m: None, **kw)


def test_reboot_runs_default_command():
    runner = FakeRunner()
    assert make(runner).handle(hs.REBOOT) is True
    assert runner.calls == [["sudo", "-n", "systemctl", "reboot"]]


def test_kiosk_exit_writes_flag_and_kills_kiosk_profile(tmp_path):
    (tmp_path / "kiosk").mkdir()
    flag = tmp_path / "kiosk" / "exit"
    runner = FakeRunner()
    assert make(runner, kiosk_exit_flag=str(flag)).handle(hs.KIOSK_EXIT) is True
    assert flag.exists()
    assert runner.calls == [["pkill", "-f", hs.KIOSK_PROFILE_MARKER]]


def test_kiosk_exit_without_kiosk_dir_still_kills(tmp_path):
    runner = FakeRunner()
    flag = tmp_path / "missing" / "exit"
    assert make(runner, kiosk_exit_flag=str(flag)).handle(hs.KIOSK_EXIT) is True
    assert not flag.exists()
    assert runner.calls == [["pkill", "-f", hs.KIOSK_PROFILE_MARKER]]


def test_kiosk_exit_flag_refuses_symlinks(tmp_path):
    victim = tmp_path / "victim"
    (tmp_path / "kiosk").mkdir()
    (tmp_path / "kiosk" / "exit").symlink_to(victim)
    runner = FakeRunner()
    system = make(runner, kiosk_exit_flag=str(tmp_path / "kiosk" / "exit"))
    assert system.handle(hs.KIOSK_EXIT) is False
    assert not victim.exists()
    # symlinked directory is refused as well
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "linkdir").symlink_to(real)
    system = make(runner, kiosk_exit_flag=str(tmp_path / "linkdir" / "exit"))
    assert system.handle(hs.KIOSK_EXIT) is False
    assert not (real / "exit").exists()
    assert runner.calls == []


def test_kiosk_exit_flag_existing_file_is_fine_fifo_is_refused(tmp_path):
    (tmp_path / "kiosk").mkdir()
    flag = tmp_path / "kiosk" / "exit"
    flag.write_text("")
    runner = FakeRunner()
    assert make(runner, kiosk_exit_flag=str(flag)).handle(hs.KIOSK_EXIT) is True
    flag.unlink()
    os.mkfifo(str(flag))
    # must not block on the FIFO, must refuse it
    assert make(runner, kiosk_exit_flag=str(flag)).handle(hs.KIOSK_EXIT) is False


def test_retained_message_is_ignored():
    runner = FakeRunner()
    assert make(runner).handle(hs.REBOOT, retained=True) is False
    assert runner.calls == []


def test_unknown_or_disabled_action_does_nothing():
    runner = FakeRunner()
    system = make(runner, commands={hs.REBOOT: []})
    assert system.handle(hs.REBOOT) is False
    assert system.handle("rm -rf /") is False
    assert runner.calls == []


def test_config_overrides_command():
    runner = FakeRunner()
    make(runner, commands={hs.REBOOT: ["echo", "hi"]}).handle(hs.REBOOT)
    assert runner.calls == [["echo", "hi"]]


def test_runner_error_is_swallowed():
    assert make(FakeRunner(fail=True)).handle(hs.REBOOT) is False


def test_default_runner_reports_failing_command():
    system = hs.HectorSystem(commands={hs.REBOOT: ["false"]}, log=lambda m: None)
    assert system.handle(hs.REBOOT) is False


def test_default_runner_reports_started_command():
    system = hs.HectorSystem(commands={hs.REBOOT: ["true"]}, log=lambda m: None)
    assert system.handle(hs.REBOOT) is True
