"""adw_modules/obs_relay.py: the host side of the obs relay for VM runs
(docs/designs/remote-sandboxes.md, decisions 37 to 50), against a fake obs
server, and the relay script itself, run with this machine's Node.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))


class FakeObs:
    """Obs's POST /events: records each batch with its headers. `status` sets
    the answer; `reject` names event_ids obs reports as rejected."""

    def __init__(self):
        self.batches: list[list[dict]] = []
        self.headers: list[dict] = []
        self.status = 200
        self.reject: set[str] = set()
        self.bad: set[str] = set()          # a batch holding one of these gets a 400
        self.delay = 0.0
        self.trickle = False                # answer a byte at a time, slowly
        self.redirect = ""                  # answer 302 to this URL
        self.answer = None                  # a raw answer body, in place of the usual
        self.dedupe = False                 # reject an event_id already stored, as obs does
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("content-length", 0)))
                fake.headers.append(dict(self.headers))
                if fake.delay:
                    __import__("time").sleep(fake.delay)
                if fake.redirect:
                    self.send_response(302)
                    self.send_header("location", fake.redirect)
                    self.end_headers()
                    return
                if fake.trickle:
                    self.send_response(200)
                    self.send_header("content-length", "100")
                    self.end_headers()
                    for _ in range(100):
                        self.wfile.write(b" ")
                        self.wfile.flush()
                        __import__("time").sleep(0.5)
                    return
                if fake.status != 200:
                    self.send_response(fake.status)
                    self.end_headers()
                    return
                events = json.loads(body)
                if any(e["event_id"] in fake.bad for e in events):
                    self.send_response(400)
                    self.end_headers()
                    return
                fake.batches.append(events)
                stored = {e["event_id"] for e in fake.events[:-len(events)]} if fake.dedupe else set()
                rejected = [e["event_id"] for e in events if e["event_id"] in fake.reject | stored]
                out = json.dumps({"ingested": len(events) - len(rejected), "rejected": rejected}).encode()
                if fake.answer is not None:
                    out = fake.answer
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def events(self) -> list[dict]:
        return [e for batch in self.batches for e in batch]

    def close(self):
        self.server.shutdown()


@pytest.fixture
def obs():
    fake = FakeObs()
    yield fake
    fake.close()


def _target(obs, **kw):
    from adw_modules import obs_relay
    return obs_relay.Target(server=obs.url, token="obs-secret", name=kw.get("name", "builder"),
                            pool=kw.get("pool", "sssf-abc"), tags=kw.get("tags", ["sssf", "adw-abc"]))


def _event(n: int, **kw) -> dict:
    return {"event_id": f"e{n}", "type": "tool_call", "session_id": "s1", "seq": n,
            "ts": "2026-11-05T10:00:00Z", "pool": "default", "agent_name": "x", "tags": [],
            "payload": {"n": n}, **kw}


def _line(event) -> str:
    return json.dumps(event) + "\n"


def _forwarder(obs, **kw):
    from adw_modules import obs_relay
    return obs_relay.Forwarder(_target(obs, **kw))


def _kinds(fwd) -> list[str]:
    return [kind for kind, _ in fwd.warnings()]


# --- what the host posts ------------------------------------------------------

def test_events_go_to_obs_with_the_host_token_and_the_calls_own_names(obs):
    fwd = _forwarder(obs)
    fwd.finish("".join(_line(_event(n)) for n in range(3)))
    assert [e["event_id"] for e in obs.events] == ["e0", "e1", "e2"]
    assert all(h["Authorization"] == "Bearer obs-secret" for h in obs.headers)
    for e in obs.events:
        assert (e["pool"], e["agent_name"], e["tags"]) == ("sssf-abc", "builder", ["sssf", "adw-abc"])
        assert e["session_id"] == "s1"                    # the extension's own
    assert [e["seq"] for e in obs.events] == [0, 1, 2]
    assert fwd.counts() == {"posted": 3, "rejected": 0, "dropped": 0, "unposted": 0}


def test_the_run_prefix_goes_on_every_session_id(obs):
    from adw_modules import obs_relay
    fwd = obs_relay.Forwarder(obs_relay.Target(server=obs.url, token="obs-secret", session_prefix="sssf-abc-"))
    fwd.finish(_line(_event(1)) + _line({**_event(2), "session_id": "a-host-session"}))
    assert [e["session_id"] for e in obs.events] == ["sssf-abc-s1", "sssf-abc-a-host-session"]


def test_malformed_lines_and_events_are_dropped(obs):
    fwd = _forwarder(obs)
    bad = ["not json\n", "[1, 2]\n", _line({"type": "x", "session_id": "s"}),
           _line({"event_id": "a", "session_id": "s"}), _line({"event_id": "b", "type": "x"}),
           _line(_event(1, seq="7")), _line(_event(2, event_id=5)), "\n"]
    fwd.finish("".join(bad) + _line(_event(9)))
    assert [e["event_id"] for e in obs.events] == ["e9"]
    assert fwd.counts()["dropped"] == 7                   # a blank line isn't an event


def test_an_event_seen_live_is_not_posted_again_by_the_sweep(obs):
    fwd = _forwarder(obs)
    fwd.live()
    for n in range(2):
        fwd.feed(_line(_event(n)))
    fwd.finish("".join(_line(_event(n)) for n in range(4)))
    assert sorted(e["event_id"] for e in obs.events) == ["e0", "e1", "e2", "e3"]
    assert fwd.counts()["posted"] == 4


def test_batches_hold_at_most_50_events(obs):
    fwd = _forwarder(obs)
    fwd.finish("".join(_line(_event(n)) for n in range(120)))
    assert [len(b) for b in obs.batches] == [50, 50, 20]


def test_batches_stay_under_the_byte_limit(obs, monkeypatch):
    from adw_modules import obs_relay
    monkeypatch.setattr(obs_relay, "BATCH_BYTES", 2000)
    fwd = _forwarder(obs)
    fwd.finish("".join(_line(_event(n, payload={"pad": "x" * 600})) for n in range(6)))
    assert len(obs.events) == 6 and all(len(json.dumps(b)) <= 2000 for b in obs.batches)


def test_an_event_too_big_for_a_batch_is_dropped(obs, monkeypatch):
    from adw_modules import obs_relay
    monkeypatch.setattr(obs_relay, "BATCH_BYTES", 2000)
    fwd = _forwarder(obs)
    fwd.finish(_line(_event(1, payload={"pad": "x" * 3000})) + _line(_event(2)))
    assert [e["event_id"] for e in obs.events] == ["e2"] and fwd.counts()["dropped"] == 1


def test_past_the_event_cap_the_call_stops_posting_and_warns_once(obs, monkeypatch):
    from adw_modules import obs_relay
    monkeypatch.setattr(obs_relay, "MAX_EVENTS", 5)
    fwd = _forwarder(obs)
    fwd.finish("".join(_line(_event(n)) for n in range(9)))
    assert len(obs.events) == 5 and fwd.counts()["dropped"] == 4
    assert _kinds(fwd) == ["cap"]


def test_past_the_byte_cap_the_call_stops_posting(obs, monkeypatch):
    from adw_modules import obs_relay
    one = len(json.dumps(_event(1, pool="sssf-abc", agent_name="builder", tags=["sssf", "adw-abc"])))
    monkeypatch.setattr(obs_relay, "MAX_BYTES", one * 3)        # as posted, after the rewrite
    fwd = _forwarder(obs)
    fwd.finish("".join(_line(_event(n)) for n in range(6)))
    assert len(obs.events) == 3 and _kinds(fwd) == ["cap"]


def test_obs_that_is_down_warns_once_and_never_raises(obs):
    obs.status = 500
    fwd = _forwarder(obs)
    fwd.live()
    fwd.feed(_line(_event(1)))
    fwd.finish("".join(_line(_event(n)) for n in range(3)))
    assert fwd.counts() == {"posted": 0, "rejected": 0, "dropped": 0, "unposted": 3}
    assert _kinds(fwd) == ["post"]


def test_obs_that_is_not_listening_warns_and_never_raises(obs):
    obs.close()
    fwd = _forwarder(obs)
    fwd.finish(_line(_event(1)))
    assert fwd.counts()["unposted"] == 1 and _kinds(fwd) == ["post"]


def test_events_obs_rejects_are_counted(obs):
    obs.reject = {"e1"}
    fwd = _forwarder(obs)
    fwd.finish("".join(_line(_event(n)) for n in range(3)))
    assert fwd.counts() == {"posted": 2, "rejected": 1, "dropped": 0, "unposted": 0}
    assert _kinds(fwd) == ["rejected"]


def test_live_posting_sends_events_before_the_call_ends(obs):
    fwd = _forwarder(obs)
    fwd.live()
    fwd.feed(_line(_event(1)))
    deadline = __import__("time").monotonic() + 5
    while not obs.events and __import__("time").monotonic() < deadline:
        __import__("time").sleep(0.05)
    assert [e["event_id"] for e in obs.events] == ["e1"]
    fwd.finish(_line(_event(1)))
    assert fwd.counts()["posted"] == 1


def test_the_token_never_appears_in_a_warning(obs):
    obs.status = 401
    fwd = _forwarder(obs)
    fwd.finish(_line(_event(1)))
    assert fwd.warnings() and all("obs-secret" not in why for _, why in fwd.warnings())


# --- the relay script, under this machine's Node ------------------------------

node = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


@pytest.fixture
def relay(tmp_path):
    from adw_modules import obs_relay
    events = tmp_path / "events.jsonl"
    proc = subprocess.Popen(["node", str(obs_relay.RELAY_SCRIPT), str(events)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    first = json.loads(proc.stdout.readline())
    yield f"http://127.0.0.1:{first['sssf_obs_relay']}", events, proc
    proc.kill()
    proc.wait()


def _post(url, body: bytes, path="/events", timeout=30):
    # A 5 MB body to a relay on a loaded machine can take more than a few
    # seconds. This test failed once in a full skill run, cause unrecorded,
    # and 320 posts under full CPU load didn't repeat it; a timeout is the
    # likeliest cause, so the posts get 30 seconds.
    req = urllib.request.Request(url + path, data=body, method="POST",
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as error:
        return error.code, b""


@node
def test_the_relay_answers_health_and_writes_each_event_as_a_line(relay):
    url, events, proc = relay
    with urllib.request.urlopen(url + "/health", timeout=5) as res:
        assert res.status == 200
    status, body = _post(url, json.dumps([_event(1), _event(2)]).encode())
    assert status == 200 and json.loads(body) == {"ingested": 2, "rejected": []}
    status, _ = _post(url, json.dumps(_event(3)).encode())          # one event, not in a list
    assert status == 200
    lines = events.read_text().splitlines()
    assert [json.loads(l)["event_id"] for l in lines] == ["e1", "e2", "e3"]


@node
def test_the_relay_listens_on_loopback_only(relay):
    import socket
    url, _, _ = relay
    port = int(url.rsplit(":", 1)[1])
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))                # picks this machine's own address; sends nothing
        others = {probe.getsockname()[0]} - {"0.0.0.0", "127.0.0.1"}
    except OSError:
        others = set()
    finally:
        probe.close()
    if not others:
        pytest.skip("no address but loopback here")
    for address in others:
        with pytest.raises(OSError):
            socket.create_connection((address, port), timeout=2).close()


@node
def test_the_relay_refuses_bad_json_and_oversized_bodies(relay):
    url, events, _ = relay
    assert _post(url, b"{nope")[0] == 400
    assert _post(url, b"[" + b" " * (5 * 1024 * 1024) + b"]")[0] == 413
    assert _post(url, b"[]", path="/other")[0] == 404
    assert not events.exists() or events.read_text() == ""


# --- the review's findings ----------------------------------------------------

def _wait_for(check, seconds=5):
    import time
    end = time.monotonic() + seconds
    while not check() and time.monotonic() < end:
        time.sleep(0.05)


def test_live_and_sweep_never_post_one_event_twice(obs, monkeypatch):
    from adw_modules import obs_relay
    monkeypatch.setattr(obs_relay, "POST_SECONDS", 2.0)
    obs.delay = 0.3
    fwd = _forwarder(obs)
    fwd.live()
    lines = "".join(_line(_event(n)) for n in range(300))
    for line in lines.splitlines():
        fwd.feed(line)
    fwd.finish(lines)
    ids = [e["event_id"] for e in obs.events]
    assert len(ids) == len(set(ids))
    counts = fwd.counts()
    assert counts["posted"] == len(ids) and counts["posted"] + counts["unposted"] == 300


def test_the_sweep_has_one_deadline(obs, monkeypatch):
    import time
    from adw_modules import obs_relay
    monkeypatch.setattr(obs_relay, "SWEEP_SECONDS", 1.0)
    monkeypatch.setattr(obs_relay, "POST_SECONDS", 2.0)
    obs.delay = 0.4
    fwd = _forwarder(obs)
    started = time.monotonic()
    fwd.finish("".join(_line(_event(n)) for n in range(500)))
    assert time.monotonic() - started < 4
    counts = fwd.counts()
    assert 0 < counts["posted"] < 500 and counts["posted"] + counts["unposted"] == 500


def test_a_server_answering_a_byte_at_a_time_cant_hold_a_post(obs, monkeypatch):
    import time
    from adw_modules import obs_relay
    monkeypatch.setattr(obs_relay, "POST_SECONDS", 1.0)
    monkeypatch.setattr(obs_relay, "SWEEP_SECONDS", 1.5)
    obs.trickle = True
    fwd = _forwarder(obs)
    started = time.monotonic()
    fwd.finish(_line(_event(1)))
    assert time.monotonic() - started < 3 and fwd.counts()["unposted"] == 1
    assert _kinds(fwd) == ["post"]


def test_events_obs_cant_store_are_dropped_here(obs):
    fwd = _forwarder(obs)
    no_ts = {k: v for k, v in _event(1).items() if k != "ts"}
    lines = [_line(no_ts), _line(_event(2, ts=None)), _line(_event(3, ts="")),
             '{"event_id":"e4","type":"t","session_id":"s","ts":"x","payload":NaN}\n',
             '{"event_id":"e5","type":"t","session_id":"s","ts":"x","payload":Infinity}\n',
             _line(_event(6))]
    fwd.finish("".join(lines))
    assert [e["event_id"] for e in obs.events] == ["e6"] and fwd.counts()["dropped"] == 5


def test_a_batch_obs_refuses_is_dropped_and_the_rest_still_go(obs):
    obs.bad = {"e3"}
    fwd = _forwarder(obs)
    fwd.finish("".join(_line(_event(n)) for n in range(120)))
    assert len(obs.events) == 70 and "e3" not in {e["event_id"] for e in obs.events}
    assert fwd.counts() == {"posted": 70, "rejected": 0, "dropped": 50, "unposted": 0}
    assert _kinds(fwd) == ["bad"]


def test_past_the_cap_the_host_holds_no_more_ids(obs, monkeypatch):
    from adw_modules import obs_relay
    monkeypatch.setattr(obs_relay, "MAX_EVENTS", 10)
    fwd = _forwarder(obs)
    for n in range(5000):
        fwd.feed(_line(_event(n)))
    assert len(fwd._seen) == 10 and len(fwd._pending) == 10
    fwd.finish(None)
    assert fwd.counts()["dropped"] == 4990


def test_odd_lines_and_answers_never_raise(obs):
    deep = "[" * 100_000 + "]" * 100_000 + "\n"
    obs.answer = json.dumps({"rejected": [["not", "hashable"], {"a": 1}, "e1"]}).encode()
    fwd = _forwarder(obs)
    fwd.live()
    fwd.feed(deep)
    fwd.finish(deep + _line(_event(1)) + _line(_event(2)))
    assert fwd.counts() == {"posted": 1, "rejected": 1, "dropped": 1, "unposted": 0}


def test_a_bad_answer_from_obs_never_raises(obs):
    obs.answer = b"HTTP garbage {"
    fwd = _forwarder(obs)
    fwd.finish(_line(_event(1)))
    assert fwd.counts()["unposted"] == 1 and _kinds(fwd) == ["post"]


def test_a_malformed_line_seen_live_and_swept_counts_once(obs):
    fwd = _forwarder(obs)
    fwd.feed("junk\n")
    fwd.finish("junk\n" + _line(_event(1)))
    assert fwd.counts()["dropped"] == 1


def test_a_post_that_ran_out_of_time_but_landed_isnt_called_rejected(obs, monkeypatch):
    import time
    from adw_modules import obs_relay
    monkeypatch.setattr(obs_relay, "POST_SECONDS", 0.3)
    monkeypatch.setattr(obs_relay, "RETRY_SECONDS", 0.2)
    obs.dedupe, obs.delay = True, 0.6
    fwd = _forwarder(obs)
    fwd.live()
    fwd.feed(_line(_event(1)))                         # the live post times out, and lands
    _wait_for(lambda: obs.events)
    obs.delay = 0
    time.sleep(0.5)                                    # the live retry: obs calls it a repeat
    fwd.finish(_line(_event(1)))
    assert fwd.counts() == {"posted": 1, "rejected": 0, "dropped": 0, "unposted": 0}


def test_the_token_never_follows_a_redirect(obs):
    other = FakeObs()
    try:
        obs.redirect = other.url + "/events"
        fwd = _forwarder(obs)
        fwd.finish(_line(_event(1)))
        assert not other.headers and fwd.counts()["unposted"] == 1
    finally:
        other.close()


def test_posts_skip_any_proxy(obs, monkeypatch):
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    fwd = _forwarder(obs)
    fwd.finish(_line(_event(1)))
    assert fwd.counts()["posted"] == 1


@node
def test_the_relay_answers_while_nobody_reads_its_stdout(tmp_path):
    from adw_modules import obs_relay
    events = tmp_path / "events.jsonl"
    proc = subprocess.Popen(["node", str(obs_relay.RELAY_SCRIPT), str(events)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        url = f"http://127.0.0.1:{json.loads(proc.stdout.readline())['sssf_obs_relay']}"
        big = json.dumps([_event(n, payload={"pad": "x" * 1000}) for n in range(1000)]).encode()
        for _ in range(3):                             # far past a pipe's 64 KB
            assert _post(url, big)[0] == 200
        assert len(events.read_text().splitlines()) == 3000
    finally:
        proc.kill()
        proc.wait()


def test_fields_obs_binds_as_scalars_must_be_scalars(obs):
    fwd = _forwarder(obs)
    fwd.finish(_line(_event(1, model={"id": "x"})) + _line(_event(2, provider=["a"]))
               + _line(_event(3, cwd=1)) + _line(_event(4, session_file={})) + _line(_event(5, model="m")))
    assert [e["event_id"] for e in obs.events] == ["e5"] and fwd.counts()["dropped"] == 4


def test_a_server_url_with_no_scheme_never_raises(obs):
    from adw_modules import obs_relay
    fwd = obs_relay.Forwarder(obs_relay.Target(server="nowhere", token="t"))
    fwd.live()
    fwd.feed(_line(_event(1)))
    fwd.finish(_line(_event(1)))
    assert fwd.counts()["unposted"] == 1 and _kinds(fwd) == ["post"]


def test_a_stuck_server_leaves_at_most_one_post_thread(obs, monkeypatch):
    import threading, time
    from adw_modules import obs_relay
    monkeypatch.setattr(obs_relay, "POST_SECONDS", 0.3)
    monkeypatch.setattr(obs_relay, "RETRY_SECONDS", 0.1)
    monkeypatch.setattr(obs_relay, "SWEEP_SECONDS", 0.5)
    obs.trickle = True
    before = {t for t in threading.enumerate() if t.name == "sssf-obs-post"}
    fwd = _forwarder(obs)
    fwd.live()
    fwd.feed(_line(_event(1)))
    time.sleep(2)
    fwd.finish(None)
    assert len({t for t in threading.enumerate() if t.name == "sssf-obs-post"} - before) <= 1


@node
def test_the_follower_copies_lines_and_skips_one_too_long(tmp_path, monkeypatch):
    from adw_modules import obs_relay
    events = tmp_path / "events.jsonl"
    events.write_text("")
    proc = subprocess.Popen(["node", str(obs_relay.RELAY_SCRIPT), "--follow", str(events)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with events.open("ab") as f:
            f.write(b"one\n" + b"x" * (4 * 1024 * 1024) + b"\n" + b"two\n")
            f.write(b"y" * (5 * 1024 * 1024))          # no newline: held to the cap, then skipped
            f.write(b"\nthree\n")
        assert [proc.stdout.readline() for _ in range(3)] == [b"one\n", b"two\n", b"three\n"]
    finally:
        proc.kill()
        proc.wait()
