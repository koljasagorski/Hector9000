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


# ------------------------------------------------------------------ set clock --
def test_set_time_runs_date_with_a_checked_integer():
    runner = FakeRunner()
    assert make(runner).set_time(b"1790000000") is True
    assert runner.calls == [["sudo", "-n", "date", "-u", "-s", "@1790000000"]]


def test_set_time_refuses_everything_but_ten_digits_in_range():
    for bad in (b"", b"abc", b"1790000000; reboot", b"$(reboot)", b"1790000000\n@1", b"-1790000000",
                b"17900000000", b"179000000", b"1699999999", b"2000000000", "١٧٩٠٠٠٠٠٠٠".encode(), b"\xff\xfe"):
        runner = FakeRunner()
        assert make(runner).set_time(bad) is False, bad
        assert runner.calls == []
    runner = FakeRunner()
    assert make(runner).set_time(b" 1790000000\n") is True        # surrounding whitespace is fine
    assert runner.calls[0][-1] == "@1790000000"


def test_set_time_ignores_retained_messages_and_reports_failures():
    runner = FakeRunner()
    assert make(runner).set_time(b"1790000000", retained=True) is False
    assert runner.calls == []
    assert make(FakeRunner(fail=True)).set_time(b"1790000000") is False
    assert make(runner, commands={hs.SET_TIME: []}).set_time(b"1790000000") is False


def test_set_time_range_matches_the_sudoers_rule():
    path = os.path.join(os.path.dirname(__file__), "..", "deploy", "sudoers.d", "hector9000")
    text = open(path).read()
    assert "@1[7-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]" in text         # 10 digits, 1.7e9 .. 1.99e9
    assert hs.parse_epoch("1700000000") == 1700000000 and hs.parse_epoch("1999999999") == 1999999999
    assert hs.parse_epoch("1699999999") is None and hs.parse_epoch("2000000000") is None


def test_sudoers_gives_root_nothing_but_the_fixed_commands():
    path = os.path.join(os.path.dirname(__file__), "..", "deploy", "sudoers.d", "hector9000")
    rules = [ln.strip() for ln in open(path).read().splitlines() if ln.strip().startswith("hector ALL=")]
    as_root = [r for r in rules if r.startswith("hector ALL=(root)")]
    assert len(as_root) == 1 and "HECTOR_SHOWPW" not in as_root[0]       # reading the password is never done as root
    assert any(r.startswith("hector ALL=(hector-stats)") and "HECTOR_SHOWPW" in r for r in rules)
    assert not any("ALL=(ALL" in r or "NOPASSWD: ALL" in r for r in rules)
    maint = open(os.path.join(os.path.dirname(__file__), "..", "deploy", "hector-maint")).read()
    assert "-u hector-stats cat /var/lib/hector9000/stats/admin_password" in maint


def test_provision_never_lets_root_follow_links_in_the_hector_owned_state_dir():
    text = open(os.path.join(os.path.dirname(__file__), "..", "deploy", "provision.sh")).read()
    assert 'die "${STATE_DIR} is a symlink' in text and 'die "${d} is a symlink' in text
    assert 'install -d -m 0755 -o root -g root "$STATE_DIR"' in text           # the parent is root's, not a service user's
    assert 'chown -h "$owner" "$dst"' in text and 'mv -n -T "$src" "$dst"' in text
    assert 'runuser -u "$HECTOR_USER" -- chmod o+r' in text
