import base64
import io
import json
import os
import socket
import threading
import time

import pytest

from Hector9000 import StatsServer as SS
from Hector9000.utils import Notifier as N
from Hector9000.utils.StatsDemo import seed_demo
from Hector9000.utils.StatsRecorder import StatsRecorder
from Hector9000.utils.StatsStore import StatsStore
from Hector9000.utils.Watchdog import Watchdog


class Clock:
    t = 1_790_000_000.0

    def __call__(self):
        return self.t


class SpyBackend:
    name = "spy"

    def __init__(self):
        self.sent = []

    def send(self, text, level, txn):
        self.sent.append(text)


@pytest.fixture
def web(tmp_path):
    d = tmp_path / "web"
    (d / "fonts").mkdir(parents=True)
    (d / "public.html").write_text("<html>public</html>")
    (d / "admin.html").write_text("<html>admin</html>")
    (d / "app.css").write_text("body{}")
    (d / "fonts" / "a.woff2").write_bytes(b"font")
    (d / ".secret").write_text("nope")
    (tmp_path / "outside.txt").write_text("outside")
    return str(d)


@pytest.fixture
def app(web):
    clock = Clock()
    store = StatsStore(":memory:")
    backend = SpyBackend()
    notifier = N.Notifier([backend], clock=clock)
    recorder = StatsRecorder(store, on_error=lambda l, s, m: notifier.notify(l, m, source=s))
    published = []
    a = SS.StatsApp(store, recorder, notifier, lambda t, p, r=False: published.append((t, p, r)),
                    "s3cret", health=lambda: {"cpu_temp": 50.0}, clock=clock, web_dir=web,
                    set_time_cmd=["true"])
    a.published, a.backend, a.clock_obj = published, backend, clock
    a.watchdog = Watchdog(notifier.notify, notifier.resolve, None)
    return a


def basic(user="admin", pw="s3cret"):
    return {"Authorization": "Basic " + base64.b64encode(("%s:%s" % (user, pw)).encode()).decode()}


def post(app, body, headers=None, ip="10.0.0.5"):
    h = {"Content-Type": "application/json", "X-Hector-Admin": "1", "Host": "hector:8080"}
    h.update(basic())
    h.update(headers or {})
    return app.route("POST", "/api/admin/action", h, json.dumps(body).encode(), ip)


def body_json(res):
    return json.loads(res[2])


# --------------------------------------------------------------------- public --
def test_public_pages_need_no_login(app):
    assert app.route("GET", "/", {})[0] == 200
    assert b"public" in app.route("GET", "/", {})[2]
    assert app.route("GET", "/healthz", {})[2] == b"ok"
    status, headers, data = app.route("GET", "/api/stats", {})
    assert status == 200 and json.loads(data)["totals"]["served"] == 0
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    assert headers["X-Frame-Options"] == "DENY"


def test_public_api_contains_no_admin_data(app):
    seed_demo(app.store, app.clock(), days=5)
    text = app.route("GET", "/api/stats", {})[2].decode()
    for forbidden in ("s3cret", "ips", "\"errors\":[", "message"):
        assert forbidden not in text
    assert "by_drink" in text and "heatmap" in text


def test_live_state_is_part_of_the_public_data(app):
    app.recorder.live = {"state": "dosing", "drink": "Mule", "progress": 40, "since": 1}
    assert body_json(app.route("GET", "/api/stats", {}))["live"]["drink"] == "Mule"


def test_static_whitelist_blocks_traversal_and_dotfiles(app):
    assert app.route("GET", "/static/app.css", {})[0] == 200
    assert app.route("GET", "/static/fonts/a.woff2", {})[0] == 200
    for bad in ("/static/../outside.txt", "/static/%2e%2e/outside.txt", "/static/.secret",
                "/static/fonts/../../outside.txt", "/static/a/b/c.css", "/static/",
                "/static/app.css%00.png", "/static/..%5coutside.txt", "/static/admin.html.bak"):
        assert app.route("GET", bad, {})[0] == 404, bad


def test_unknown_routes_and_methods(app):
    assert app.route("GET", "/nope", {})[0] == 404
    assert app.route("DELETE", "/", {})[0] == 405
    assert app.route("POST", "/api/stats", {})[0] == 404


# ----------------------------------------------------------------------- auth --
def test_admin_requires_login(app):
    for path in ("/admin", "/api/admin/overview", "/api/admin/export?table=doses"):
        status, headers, _ = app.route("GET", path, {})
        assert status == 401 and "Basic" in headers["WWW-Authenticate"]
    assert app.route("GET", "/admin", basic())[0] == 200
    assert app.route("GET", "/api/admin/overview", basic())[0] == 200


@pytest.mark.parametrize("headers", [basic(pw="wrong"), basic(user="root"), {"Authorization": "Basic !!!"},
                                     {"Authorization": "Bearer s3cret"}, basic(pw="")])
def test_wrong_credentials_are_rejected(app, headers):
    assert app.route("GET", "/api/admin/overview", headers)[0] == 401


def test_brute_force_gets_locked_out_per_address(app):
    for _ in range(SS.LOGIN_FAILS):
        assert app.route("GET", "/admin", basic(pw="x"), ip="6.6.6.6")[0] == 401
    assert app.route("GET", "/admin", basic(), ip="6.6.6.6")[0] == 429     # even the right one
    assert app.route("GET", "/admin", basic(), ip="10.1.1.1")[0] == 200   # others unaffected
    app.clock_obj.t += 61
    assert app.route("GET", "/admin", basic(), ip="6.6.6.6")[0] == 200


def test_missing_header_does_not_count_as_failed_attempt(app):
    for _ in range(20):
        assert app.route("GET", "/admin", {}, ip="7.7.7.7")[0] == 401
    assert app.route("GET", "/admin", basic(), ip="7.7.7.7")[0] == 200


# ------------------------------------------------------------------- actions --
def test_post_needs_csrf_header_json_and_same_origin(app):
    assert post(app, {"action": "clean"}, {"X-Hector-Admin": "0"})[0] == 403
    assert post(app, {"action": "clean"}, {"Content-Type": "text/plain"})[0] == 403
    assert post(app, {"action": "clean"}, {"Origin": "http://evil.example"})[0] == 403
    assert post(app, {"action": "clean"}, {"Origin": "http://hector:8080"})[0] == 200
    assert app.published == [("Hector9000/cleanMe", "true", False)]


def test_post_without_login_is_rejected(app):
    h = {"Content-Type": "application/json", "X-Hector-Admin": "1"}
    assert app.route("POST", "/api/admin/action", h, b'{"action":"reboot"}')[0] == 401
    assert app.published == []


def test_bad_bodies(app):
    for raw in (b"{", b"[1]", b"\xff", b"{" * 50000):
        h = dict(basic(), **{"Content-Type": "application/json", "X-Hector-Admin": "1"})
        status = app.route("POST", "/api/admin/action", h, raw)[0]
        assert status in (400, 413)
    assert body_json(post(app, {"action": "format_disk"}))["ok"] is False


def test_machine_actions_publish_on_mqtt_and_are_logged(app):
    for action, topic in (("reboot", "Hector9000/system/reboot"), ("kiosk_exit", "Hector9000/system/kioskExit"),
                          ("dry", "Hector9000/dryMe"), ("open_valves", "Hector9000/openAllValves"),
                          ("close_valves", "Hector9000/closeAllValves")):
        assert post(app, {"action": action})[0] == 200
        assert app.published[-1][0] == topic
    assert [e["kind"] for e in app.store.recent_events(1)] == ["admin"]


def test_dangerous_actions_wait_while_a_drink_is_made(app):
    app.recorder.live = {"state": "dosing", "drink": "x", "progress": 1, "since": 1}
    for action in ("clean", "dry", "reboot", "open_valves"):
        assert post(app, {"action": action})[0] == 409
    assert app.published == []
    assert post(app, {"action": "close_valves"})[0] == 200      # closing is always fine


def test_led_mode_validates(app):
    assert post(app, {"action": "led_mode", "mode": "random"})[0] == 200
    assert app.published[-1] == ("Hector9000/LEDStrip/ambient", "random", True)
    assert post(app, {"action": "led_mode", "mode": "disco"})[0] == 400


def test_refill_and_bottle_settings(app):
    assert post(app, {"action": "refill", "code": "gin", "capacity_ml": 1000})[0] == 200
    b = {x["code"]: x for x in app.store.bottle_levels(app.clock())}
    assert b["gin"]["capacity_ml"] == 1000 and b["gin"]["remaining_ml"] == 1000
    assert post(app, {"action": "refill", "code": "gin", "capacity_ml": 99999})[0] == 400
    assert post(app, {"action": "refill", "code": "gin", "capacity_ml": "abc"})[0] == 400
    assert post(app, {"action": "set_bottle"})[0] == 400


def test_ack_errors(app):
    app.store.add_error(1, "x", "error", "boom")
    post(app, {"action": "ack_errors"})
    assert app.store.recent_errors()[0]["acked"] == 1


def test_test_notification_goes_through_the_notifier(app):
    assert post(app, {"action": "test_notification"})[0] == 200
    app.notifier.process(app.clock())
    assert "Test" in app.backend.sent[0]


def test_set_time_validates_range_and_runs_the_command(app):
    assert post(app, {"action": "set_time", "epoch": 1})[0] == 400
    assert post(app, {"action": "set_time", "epoch": "x"})[0] == 400
    assert post(app, {"action": "set_time", "epoch": int(app.clock())})[0] == 200
    app.set_time_cmd = ["false"]
    assert post(app, {"action": "set_time", "epoch": int(app.clock())})[0] == 500


def test_error_events_reach_the_chat_once(app):
    for _ in range(3):
        app.recorder.handle("Hector9000/event/error",
                            json.dumps({"source": "dosing", "message": "Gin leer", "level": "error"}),
                            app.clock())
        app.clock_obj.t += 1
    app.notifier.process(app.clock())
    assert len(app.backend.sent) == 1 and "Gin leer" in app.backend.sent[0]


# --------------------------------------------------------------------- admin ---
def test_overview_has_everything_the_dashboard_needs(app):
    seed_demo(app.store, app.clock(), days=7)
    app.sample_health(app.clock())
    data = body_json(app.route("GET", "/api/admin/overview", basic()))
    for key in ("stats", "live", "health", "errors", "events", "doses", "bottles", "samples",
                "notifier", "versions", "ips", "actions", "clock"):
        assert key in data, key
    assert data["health"]["cpu_temp"] == 50.0 and len(data["bottles"]) >= 5
    assert "token" not in json.dumps(data["notifier"]).lower()


def test_csv_export_escapes_formulas_and_rejects_unknown_tables(app):
    app.store.add_error(1, "x", "error", "=HYPERLINK(\"http://evil\")")
    status, headers, data = app.route("GET", "/api/admin/export?table=errors", basic())
    assert status == 200 and "'=HYPERLINK" in data.decode()
    assert headers["Content-Disposition"].startswith("attachment")
    assert app.route("GET", "/api/admin/export?table=sqlite_master", basic())[0] == 404


def test_admin_password_is_generated_once_and_private(tmp_path):
    pw = SS.load_admin_password(str(tmp_path), env={})
    assert len(pw) >= 12 and SS.load_admin_password(str(tmp_path), env={}) == pw
    assert oct(os.stat(tmp_path / "admin_password").st_mode & 0o777) == "0o600"
    assert SS.load_admin_password(str(tmp_path), env={"HECTOR_ADMIN_PASSWORD": "fromenv"}) == "fromenv"


def test_demo_data_makes_a_rich_public_page_quickly(app):
    t0 = time.time()
    seed_demo(app.store, app.clock(), days=21)
    stats = body_json(app.route("GET", "/api/stats", {}))
    assert stats["totals"]["served"] > 100 and len(stats["by_drink"]) >= 8
    assert stats["records"]["best_day"] and stats["alcohol"]["est_units"] > 0
    assert time.time() - t0 < 5


def test_sample_health_stores_and_checks(app):
    app.sample_health(app.clock())
    assert app.store.samples_since(0)[0]["cpu_temp"] == 50.0


# ---- hardening found by the security review --------------------------------

def test_global_login_limit_stops_guessing_from_many_addresses(app):
    for i in range(SS.GLOBAL_LOGIN_FAILS):
        assert app.route("GET", "/admin", basic(pw="x"), ip="10.9.0.%d" % i)[0] == 401
    # a fresh address is now refused as well, even with the right password ...
    assert app.route("GET", "/admin", basic(), ip="10.9.9.9")[0] == 429
    # ... until the minute is over
    app.clock_obj.t += 61
    assert app.route("GET", "/admin", basic(), ip="10.9.9.9")[0] == 200


def test_failed_login_bookkeeping_does_not_grow_forever(app):
    for i in range(50):
        app.route("GET", "/admin", basic(pw="x"), ip="10.8.0.%d" % i)
    app.clock_obj.t += 61
    app.route("GET", "/admin", basic(), ip="10.8.1.1")
    assert len(app._fails) <= 1


@pytest.mark.parametrize("ip,allowed", [("127.0.0.1", True), ("::1", True), ("::ffff:127.0.0.1", True),
                                        ("192.168.1.20", False), ("10.0.0.5", False), ("-", False)])
def test_admin_can_be_limited_to_this_machine(app, ip, allowed):
    app.admin_remote = False
    assert (app.route("GET", "/admin", basic(), ip=ip)[0] == 200) == allowed
    assert (post(app, {"action": "ack_errors"}, ip=ip)[0] == 200) == allowed
    if not allowed:
        assert app.route("GET", "/admin", basic(), ip=ip)[0] == 403
    # the public page is never affected
    assert app.route("GET", "/api/stats", {}, ip=ip)[0] == 200


def test_set_time_stays_inside_what_sudoers_allows(app):
    assert post(app, {"action": "set_time", "epoch": 1699999999})[0] == 400
    assert post(app, {"action": "set_time", "epoch": 2000000000})[0] == 400
    assert post(app, {"action": "set_time", "epoch": 1700000000})[0] == 200
    assert post(app, {"action": "set_time", "epoch": 1999999999})[0] == 200


def test_sudoers_clock_rule_matches_the_python_range():
    path = os.path.join(os.path.dirname(__file__), "..", "deploy", "sudoers.d", "hector9000")
    with open(path) as f:
        text = f.read()
    assert "@1[7-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]" in text
    assert SS.SET_TIME_MIN == 1700000000 and SS.SET_TIME_MAX == 1999999999


def test_short_configured_admin_password_is_warned_about(tmp_path, capsys):
    assert SS.load_admin_password(str(tmp_path), env={"HECTOR_ADMIN_PASSWORD": "short"}) == "short"
    assert "WARNING" in capsys.readouterr().out
    SS.load_admin_password(str(tmp_path), env={"HECTOR_ADMIN_PASSWORD": "a-long-enough-password"})
    assert "WARNING" not in capsys.readouterr().out


def _raw_request(port, payload, read=True):
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    sock.sendall(payload)
    data = b""
    if read:
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
        except socket.timeout:
            pass
    sock.close()
    return data


@pytest.fixture
def live(app):
    """A real socket server around the app (loopback, random port)."""
    server = SS.LimitedHTTPServer(("127.0.0.1", 0), SS.Handler, max_connections=3)
    server.app = app
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.mark.parametrize("length", ["-1", "-5", "abc", "99999"])
def test_bad_content_length_is_rejected_before_anything_is_read(live, length):
    port = live.server_address[1]
    reply = _raw_request(port, ("GET /healthz HTTP/1.0\r\nContent-Length: %s\r\n\r\n" % length).encode())
    assert reply.startswith(b"HTTP/1.0 400") or reply.startswith(b"HTTP/1.0 413")


def test_healthz_works_over_a_real_socket(live):
    reply = _raw_request(live.server_address[1], b"GET /healthz HTTP/1.0\r\n\r\n")
    assert reply.startswith(b"HTTP/1.0 200") and reply.endswith(b"ok")


def test_connection_limit_answers_503_instead_of_piling_up_threads(live):
    port = live.server_address[1]
    holders = [socket.create_connection(("127.0.0.1", port), timeout=5) for _ in range(3)]  # send nothing
    try:
        time.sleep(0.3)
        reply = _raw_request(port, b"GET /healthz HTTP/1.0\r\n\r\n")
        assert reply.startswith(b"HTTP/1.1 503")
    finally:
        for h in holders:
            h.close()
    time.sleep(0.5)       # the slots are free again once the holders are gone
    assert _raw_request(port, b"GET /healthz HTTP/1.0\r\n\r\n").startswith(b"HTTP/1.0 200")


def test_slow_client_is_cut_off_after_the_total_deadline(live, monkeypatch):
    monkeypatch.setattr(SS, "REQUEST_DEADLINE", 0.5)
    port = live.server_address[1]
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    sock.sendall(b"GET /healthz HTTP/1.0\r\n")          # never finishes the request
    time.sleep(0.2)
    sock.sendall(b"X-A: 1\r\n")                          # a byte now and then does not help
    t0 = time.time()
    try:
        data = sock.recv(100)       # b"" once the server closed the connection
    except OSError:
        data = b""
    assert data == b"" and time.time() - t0 < 3
    sock.close()


def test_global_login_limit_does_not_lock_out_the_admin_who_is_already_in(app):
    # the admin's browser has logged in successfully a moment ago (it polls every 5 s)
    assert app.route("GET", "/admin", basic(), ip="10.5.5.5")[0] == 200
    for i in range(SS.GLOBAL_LOGIN_FAILS):                    # an attacker uses up the global budget
        app.route("GET", "/admin", basic(pw="x"), ip="10.7.0.%d" % i)
    assert app.route("GET", "/admin", basic(), ip="10.7.9.9")[0] == 429     # a stranger is locked out
    assert app.route("GET", "/admin", basic(), ip="10.5.5.5")[0] == 200     # the known device is not
    # the trust only lasts a while
    app.clock_obj.t += SS.TRUST_S + 1
    for i in range(SS.GLOBAL_LOGIN_FAILS):
        app.route("GET", "/admin", basic(pw="x"), ip="10.7.1.%d" % i)
    assert app.route("GET", "/admin", basic(), ip="10.5.5.5")[0] == 429


def test_this_machine_is_never_locked_out_by_the_global_limit(app):
    for i in range(SS.GLOBAL_LOGIN_FAILS):
        app.route("GET", "/admin", basic(pw="x"), ip="10.7.0.%d" % i)
    assert app.route("GET", "/admin", basic(), ip="127.0.0.1")[0] == 200


def test_a_wrong_password_never_earns_trust(app):
    assert app.route("GET", "/admin", basic(pw="x"), ip="10.6.6.6")[0] == 401
    for i in range(SS.GLOBAL_LOGIN_FAILS):
        app.route("GET", "/admin", basic(pw="x"), ip="10.7.0.%d" % i)
    assert app.route("GET", "/admin", basic(), ip="10.6.6.6")[0] == 429


def test_a_client_that_hangs_up_does_not_spam_the_log(live, capsys):
    port = live.server_address[1]
    for _ in range(5):
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        sock.sendall(b"GET /api/stats HTTP/1.0\r\n\r\n")
        sock.close()                       # gone before the answer is written
    time.sleep(0.5)
    assert "Traceback" not in capsys.readouterr().err
