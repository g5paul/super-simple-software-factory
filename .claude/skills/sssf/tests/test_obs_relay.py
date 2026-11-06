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
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("content-length", 0)))
                if fake.status != 200:
                    self.send_response(fake.status)
                    self.end_headers()
                    return
                events = json.loads(body)
                fake.batches.append(events)
                fake.headers.append(dict(self.headers))
                rejected = [e["event_id"] for e in events if e["event_id"] in fake.reject]
                out = json.dumps({"ingested": len(events) - len(rejected), "rejected": rejected}).encode()
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


def _forwarder(obs, warnings=None, **kw):
    from adw_modules import obs_relay
    warns = [] if warnings is None else warnings
    return obs_relay.Forwarder(_target(obs, **kw), warn=lambda kind, why: warns.append((kind, why)))


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
    warnings = []
    fwd = _forwarder(obs, warnings)
    fwd.finish("".join(_line(_event(n)) for n in range(9)))
    assert len(obs.events) == 5 and fwd.counts()["dropped"] == 4
    assert [k for k, _ in warnings] == ["cap"]


def test_past_the_byte_cap_the_call_stops_posting(obs, monkeypatch):
    from adw_modules import obs_relay
    one = len(json.dumps(_event(1, pool="sssf-abc", agent_name="builder", tags=["sssf", "adw-abc"])))
    monkeypatch.setattr(obs_relay, "MAX_BYTES", one * 3)        # as posted, after the rewrite
    warnings = []
    fwd = _forwarder(obs, warnings)
    fwd.finish("".join(_line(_event(n)) for n in range(6)))
    assert len(obs.events) == 3 and [k for k, _ in warnings] == ["cap"]


def test_obs_that_is_down_warns_once_and_never_raises(obs):
    obs.status = 500
    warnings = []
    fwd = _forwarder(obs, warnings)
    fwd.live()
    fwd.feed(_line(_event(1)))
    fwd.finish("".join(_line(_event(n)) for n in range(3)))
    assert fwd.counts() == {"posted": 0, "rejected": 0, "dropped": 0, "unposted": 3}
    assert [k for k, _ in warnings] == ["post"]


def test_obs_that_is_not_listening_warns_and_never_raises(obs):
    obs.close()
    warnings = []
    fwd = _forwarder(obs, warnings)
    fwd.finish(_line(_event(1)))
    assert fwd.counts()["unposted"] == 1 and [k for k, _ in warnings] == ["post"]


def test_events_obs_rejects_are_counted(obs):
    obs.reject = {"e1"}
    warnings = []
    fwd = _forwarder(obs, warnings)
    fwd.finish("".join(_line(_event(n)) for n in range(3)))
    assert fwd.counts() == {"posted": 2, "rejected": 1, "dropped": 0, "unposted": 0}
    assert [k for k, _ in warnings] == ["rejected"]


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
    warnings = []
    _forwarder(obs, warnings).finish(_line(_event(1)))
    assert warnings and all("obs-secret" not in why for _, why in warnings)


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


def _post(url, body: bytes, path="/events"):
    req = urllib.request.Request(url + path, data=body, method="POST",
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as res:
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
    assert [json.loads(proc.stdout.readline())["event_id"] for _ in range(3)] == ["e1", "e2", "e3"]


@node
def test_the_relay_listens_on_loopback_only(relay):
    url, _, _ = relay
    assert url.startswith("http://127.0.0.1:")


@node
def test_the_relay_refuses_bad_json_and_oversized_bodies(relay):
    url, events, _ = relay
    assert _post(url, b"{nope")[0] == 400
    assert _post(url, b"[" + b" " * (5 * 1024 * 1024) + b"]")[0] == 413
    assert _post(url, b"[]", path="/other")[0] == 404
    assert not events.exists() or events.read_text() == ""
