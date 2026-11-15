"""The deploy files must agree on who owns which folder below /var/lib/hector9000.

Nothing here needs hardware or a running systemd: it reads the shipped files and compares them.
A mismatch (a unit that writes where provision.sh created nothing, an env template that
overrides the unit) would only show on the device otherwise.
"""
import glob
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")
DEPLOY = os.path.join(ROOT, "deploy")


def read(*parts):
    with open(os.path.join(DEPLOY, *parts), encoding="utf-8") as f:
        return f.read()


def unit(name):
    return read("systemd", "hector-%s.service" % name)


def setting(text, key):
    return re.findall(r"^%s=(.*)$" % re.escape(key), text, re.M)


def prov_var(name):
    m = re.search(r'^%s="([^"]+)"' % name, read("provision.sh"), re.M)
    assert m, name
    return m.group(1).replace("${STATE_DIR}", prov_var("STATE_DIR")) if name != "STATE_DIR" else m.group(1)


def test_the_state_dir_is_roots_and_each_service_user_owns_only_its_own_folder():
    state, hw, stats = prov_var("STATE_DIR"), prov_var("HW_DIR"), prov_var("STATS_DIR")
    assert (state, hw, stats) == ("/var/lib/hector9000", "/var/lib/hector9000/hector", "/var/lib/hector9000/stats")
    prov = read("provision.sh")
    assert 'install -d -m 0755 -o root -g root "$STATE_DIR"' in prov
    assert 'install -d -m 0751 -o "$HECTOR_USER" -g "$HECTOR_USER" "$HW_DIR"' in prov
    assert 'install -d -m 0750 -o "$STATS_USER" -g "$STATS_USER" "$STATS_DIR"' in prov


def test_hardware_services_work_in_their_own_folder_and_wait_for_the_data_partition():
    hw = prov_var("HW_DIR")
    for name in ("server", "controller"):
        text = unit(name)
        assert setting(text, "WorkingDirectory") == [hw], name
        assert setting(text, "RequiresMountsFor") == ["/var/lib/hector9000"], name
        # StateDirectory= would hand the parent folder back to the service user at every start
        assert not setting(text, "StateDirectory"), name


def test_the_stats_service_is_confined_to_its_folder_and_reads_the_database_in_the_other():
    text = unit("stats")
    assert setting(text, "Environment") .count("HECTOR_STATE_DIR=" + prov_var("STATS_DIR")) == 1
    assert "HECTOR_DB=" + prov_var("HW_DIR") + "/h9k.db" in setting(text, "Environment")
    assert setting(text, "ReadWritePaths") == [prov_var("STATS_DIR")]
    assert setting(text, "User") == ["hector-stats"] and not setting(text, "StateDirectory")


def test_no_unit_reads_the_env_file_except_stats_and_the_template_cannot_override_the_unit():
    for path in glob.glob(os.path.join(DEPLOY, "systemd", "*.service")):
        has = bool(setting(open(path, encoding="utf-8").read(), "EnvironmentFile"))
        assert has == path.endswith("hector-stats.service"), path
    # EnvironmentFile= beats Environment=: an active HECTOR_STATE_DIR line in the template
    # would send the stats service back to the wrong folder
    template = read("hector.env.example")
    assert not re.search(r"^HECTOR_STATE_DIR=", template, re.M)
    assert not re.search(r"^HECTOR_DB=", template, re.M)


def test_migrate_data_imports_into_the_hardware_folder():
    text = read("migrate-data.sh")
    assert 'ROOT_DIR="/var/lib/hector9000"' in text
    assert 'STATE_DIR="${ROOT_DIR}/hector"' in text
    assert 'install -d -m 0755 -o root -g root "$ROOT_DIR"' in text


def test_overlay_tool_never_uses_the_recursive_default():
    text = read("hector-overlay")
    # raspi-config's plain "overlayroot=tmpfs" would also overlay the data partition (recurse=1)
    assert 'PARAM="overlayroot=tmpfs:recurse=0"' in text
    assert "enable_overlayfs" not in text.replace("raspi-config nonint enable_overlayfs", "")
    prov = read("provision.sh")
    assert "raspi-config nonint enable_overlayfs" not in prov and "/usr/local/sbin/hector-overlay on" in prov
    sudoers = read("sudoers.d", "hector9000")
    for arg in ("status", "on", "off"):
        assert "/usr/local/sbin/hector-overlay %s" % arg in sudoers
    maint = read("hector-maint")
    assert "hector-overlay on" in maint and "hector-overlay off" in maint


def test_first_boot_unit_matches_its_script_and_provisioning():
    unit_text = read("image", "hector-firstboot.service")
    script = read("image", "hector-firstboot")
    assert setting(unit_text, "ExecStart") == ["/usr/local/sbin/hector-firstboot"]
    flag = re.search(r'^FLAG="([^"]+)"', script, re.M).group(1)
    assert setting(unit_text, "ConditionPathExists") == ["!" + flag]
    assert 'MNT="/var/lib/hector9000"' in script and 'findmnt -no SOURCE "$MNT"' in script
    assert "blkid -L" not in script          # never trust "whatever carries the label"
    # the same owners/modes as provision.sh gives the folders
    assert 'install -d -m 0751 -o hector -g hector "$MNT/hector"' in script
    assert 'install -d -m 0750 -o hector-stats -g hector-stats "$MNT/stats"' in script
    build = read("image", "build-image.sh")
    assert "PARTUUID=%s  /var/lib/hector9000  ext4  defaults,noatime,nosuid,nodev,noexec,nofail" in build
    assert "LABEL=HECTORDATA  /var/lib" not in build
    prov = read("provision.sh")
    assert "image/hector-firstboot" in prov and "systemctl enable hector-firstboot.service" in prov
