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


def test_kiosk_exit_sets_flag_and_kills_kiosk_profile():
    runner = FakeRunner()
    assert make(runner).handle(hs.KIOSK_EXIT) is True
    script = runner.calls[0][-1]
    assert hs.KIOSK_EXIT_FLAG in script
    assert hs.KIOSK_PROFILE_MARKER in script


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
