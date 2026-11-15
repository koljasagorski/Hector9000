import io
import json
import urllib.error

import pytest

from Hector9000.utils import Notifier as N


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class Backend:
    name = "fake"

    def __init__(self, errors=()):
        self.sent = []
        self.errors = list(errors)

    def send(self, text, level, txn):
        if self.errors:
            raise self.errors.pop(0)
        self.sent.append((text, level, txn))


def make(backend=None, **kw):
    clock = Clock()
    n = N.Notifier([backend or Backend()], clock=clock, **kw)
    return n, clock


def test_below_min_level_and_without_backend_nothing_is_queued():
    n, _ = make()
    assert n.notify("info", "hi") is False
    assert N.Notifier([], clock=Clock()).notify("critical", "x") is False


def test_alert_is_sent_with_icon_and_source():
    b = Backend()
    n, clock = make(b)
    assert n.notify("error", "Flasche leer", source="dosing")
    assert n.process(clock.t) == 1
    assert b.sent[0][0] == "🔴 Hector9000 (dosing): Flasche leer"


def test_identical_alerts_are_deduplicated_within_the_window():
    b = Backend()
    n, clock = make(b, dedup_s=600)
    assert n.notify("error", "x", key="k")
    assert not n.notify("error", "x", key="k")
    clock.t += 601
    assert n.notify("error", "x", key="k")
    n.process(clock.t)
    assert len(b.sent) == 2


def test_resolve_only_after_a_sent_alert():
    b = Backend()
    n, clock = make(b)
    assert not n.resolve("temp", "wieder ok")
    n.notify("warning", "heiß", key="temp")
    assert n.resolve("temp", "wieder ok")
    n.process(clock.t)
    assert [s[0][:1] for s in b.sent] == ["🟠", "✅"]
    assert not n.resolve("temp", "wieder ok")


def test_temporary_error_retries_with_backoff_then_succeeds():
    b = Backend(errors=[N.TemporaryError("offline"), N.TemporaryError("offline")])
    n, clock = make(b)
    n.notify("error", "x")
    n.process(clock.t)
    assert b.sent == [] and n.status()["queued"] == 1 and "offline" in n.status()["last_error"]
    n.process(clock.t + 1)                     # not due yet
    assert b.sent == []
    n.process(clock.t + 12)
    n.process(clock.t + 12 + 25)
    assert len(b.sent) == 1 and n.status()["queued"] == 0 and n.status()["sent"] == 1


def test_retry_after_from_server_is_respected():
    b = Backend(errors=[N.TemporaryError("limit", retry_after=90)])
    n, clock = make(b)
    n.notify("error", "x")
    n.process(clock.t)
    n.process(clock.t + 60)
    assert b.sent == []
    n.process(clock.t + 91)
    assert len(b.sent) == 1


def test_permanent_error_is_not_retried():
    b = Backend(errors=[N.PermanentError("HTTP 403")])
    n, clock = make(b)
    n.notify("error", "x")
    n.process(clock.t)
    n.process(clock.t + 1000)
    assert b.sent == [] and n.status()["queued"] == 0 and n.status()["failed"] == 1


def test_unexpected_backend_exception_does_not_escape():
    b = Backend(errors=[RuntimeError("boom")])
    n, clock = make(b)
    n.notify("error", "x")
    n.process(clock.t)
    assert n.status()["failed"] == 1 and "boom" in n.status()["last_error"]


def test_queue_is_bounded_and_old_messages_are_dropped():
    b = Backend(errors=[N.TemporaryError("offline")] * 100)
    n, clock = make(b, max_queue=3)
    for i in range(10):
        n.notify("error", "m%d" % i, key="k%d" % i)
    assert n.status()["queued"] == 3 and n.status()["dropped"] == 7
    clock.t += N.MAX_AGE_S + 1
    n.process(clock.t)
    assert n.status()["queued"] == 0


def test_long_text_is_clipped():
    b = Backend()
    n, clock = make(b)
    n.notify("error", "x" * 10000)
    n.process(clock.t)
    assert len(b.sent[0][0]) <= N.MAX_TEXT


def test_test_message_ignores_level_and_dedup():
    b = Backend()
    n, clock = make(b, min_level="critical")
    assert n.test_message() and n.test_message()
    n.process(clock.t)
    assert len(b.sent) == 2 and "Test" in b.sent[0][0]


# -------------------------------------------------------------------- Matrix --
class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def matrix(opener, **kw):
    return N.MatrixBackend("https://matrix.example.org/", "!room:example.org", "secret-token",
                           opener=opener, **kw)


def test_matrix_request_shape():
    seen = {}

    def opener(req, timeout):
        seen.update(url=req.full_url, method=req.get_method(), headers=dict(req.header_items()),
                    body=json.loads(req.data))
        return FakeResponse(b"{}")

    matrix(opener).send("hallo", "error", "txn 1/2")
    assert seen["method"] == "PUT"
    assert seen["url"] == ("https://matrix.example.org/_matrix/client/v3/rooms/"
                           "%21room%3Aexample.org/send/m.room.message/txn%201%2F2")
    assert seen["headers"]["Authorization"] == "Bearer secret-token"
    assert seen["body"] == {"msgtype": "m.text", "body": "hallo"}


def http_error(code, body=b"{}"):
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(body))


@pytest.mark.parametrize("code,kind", [(401, N.PermanentError), (403, N.PermanentError),
                                       (404, N.PermanentError), (500, N.TemporaryError),
                                       (502, N.TemporaryError), (429, N.TemporaryError)])
def test_matrix_http_errors_map_to_retry_or_give_up(code, kind):
    def opener(req, timeout):
        raise http_error(code)
    with pytest.raises(kind):
        matrix(opener).send("x", "error", "t")


def test_matrix_429_carries_retry_after():
    def opener(req, timeout):
        raise http_error(429, b'{"retry_after_ms": 2500}')
    with pytest.raises(N.TemporaryError) as e:
        matrix(opener).send("x", "error", "t")
    assert e.value.retry_after == 2.5


def test_matrix_offline_is_temporary():
    def opener(req, timeout):
        raise urllib.error.URLError("Name or service not known")
    with pytest.raises(N.TemporaryError):
        matrix(opener).send("x", "error", "t")


def test_matrix_requires_https_unless_allowed():
    with pytest.raises(ValueError):
        N.MatrixBackend("http://matrix.lan", "!r:x", "t")
    assert N.MatrixBackend("http://matrix.lan", "!r:x", "t", allow_http=True).homeserver == "http://matrix.lan"
    with pytest.raises(ValueError):
        N.MatrixBackend("https://m.example", "", "t")


def test_backends_from_env_needs_homeserver_and_token():
    assert N.backends_from_env({}) == []
    assert N.backends_from_env({"HECTOR_MATRIX_HOMESERVER": "https://m.example"}) == []
    got = N.backends_from_env({"HECTOR_MATRIX_HOMESERVER": "https://m.example",
                               "HECTOR_MATRIX_TOKEN": "t", "HECTOR_MATRIX_ROOM": "!r:m.example"})
    assert [b.name for b in got] == ["matrix"]


def test_status_never_contains_the_token():
    n = N.Notifier(N.backends_from_env({"HECTOR_MATRIX_HOMESERVER": "https://m.example",
                                        "HECTOR_MATRIX_TOKEN": "geheim", "HECTOR_MATRIX_ROOM": "!r:m"}))
    assert "geheim" not in json.dumps(n.status())


def test_untrusted_alerts_are_capped_per_hour_with_one_summary():
    n, clock = make(max_untrusted_per_hour=5)
    sent = [n.notify("error", "spam %d" % i, source="x", untrusted=True) for i in range(20)]
    assert sent[:5] == [True] * 5 and not any(sent[5:])
    texts = [i["text"] for i in n.queue]
    assert len(texts) == 6 and "unterdrückt" in texts[-1]          # five alerts + one summary
    assert n.stats["suppressed"] == 15
    # internal checks (watchdog, temperature, ...) are not affected by the flood
    assert n.notify("critical", "CPU heiß", key="temp", source="watchdog") is True
    # and the cap opens again after an hour
    clock.t += 3601
    assert n.notify("error", "again", source="x", untrusted=True) is True


def test_untrusted_text_is_clipped_harder():
    n, _ = make()
    n.notify("error", "A" * 5000, source="x", untrusted=True)
    assert len(n.queue[0]["text"]) < 500
