"""The host side of the obs relay for VM runs (docs/designs/remote-sandboxes.md,
decisions 37 to 50).

In a VM, a pi agent's obs extension posts to SSSF's relay (sssf_obs_relay.cjs)
on the VM's loopback, which writes each event as a line to a file. The host
follows that file live, reads it once more after the call, and posts the
events to the engineer's obs server with the host's token, which never
enters the VM. A Forwarder does the host's part for one call:

- It keeps only well-formed events that obs can store, and sets pool,
  agent_name and tags to the call's own (Target), since the agent can write
  anything into the file.
- It posts each event once: obs keys events by event_id, so the sweep after
  the call sends only what the live posts didn't.
- It caps a call at MAX_EVENTS and MAX_BYTES, and past the cap holds no more
  events or ids. It posts in batches of at most BATCH_EVENTS and BATCH_BYTES, under
  obs's 4 MB request limit.
- One thread posts at a time. A post takes at most POST_SECONDS of wall time
  and the sweep at most SWEEP_SECONDS; what doesn't go counts as unposted.
- It never raises, and never logs from its own threads: the tracer's SQLite
  connection belongs to the runner's thread. Warnings wait in `warnings()`,
  one of each kind, for the caller to log.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

RELAY_SCRIPT = Path(__file__).with_name("sssf_obs_relay.cjs")
DEFAULT_SERVER = "http://127.0.0.1:43190"     # where the extension posts when unset
MAX_EVENTS = 20_000                           # a call's cap: about 40 times the busiest
MAX_BYTES = 20 * 1024 * 1024                  # pi session obs held (decision 45)
BATCH_EVENTS = 50                             # as the extension batches
BATCH_BYTES = 3 * 1024 * 1024                 # under obs's 4 MB request limit
POST_SECONDS = 10.0                           # one post, wall time
SWEEP_SECONDS = 30.0                          # all of the sweep's posts
LIVE_SECONDS = 0.5                            # how often the live poster sends
RETRY_SECONDS = 5.0                           # after a failed live post
LINE_BYTES = BATCH_BYTES                      # a longer line can't fit a batch


@dataclass
class Target:
    """Where a call's events go, and the names SSSF gave the call."""
    server: str
    token: str = field(repr=False)
    name: str = ""
    pool: str = ""
    tags: list[str] = field(default_factory=list)


def _no_constant(name: str):
    raise ValueError(f"{name} isn't JSON obs can read")


def _parse(line: str):
    """The event on a line, or None. Python's json takes NaN and Infinity,
    which obs's JSON.parse refuses, so they make the line bad here too."""
    try:
        return json.loads(line, parse_constant=_no_constant)
    except Exception:           # ValueError, and RecursionError on deep nesting
        return None


def _well_formed(event) -> bool:
    """What obs needs to store an event: its ids and a timestamp. Obs keeps
    a session's first_ts NOT NULL, and a batch with an event it can't store
    fails whole."""
    if not isinstance(event, dict):
        return False
    for key in ("event_id", "type", "session_id", "ts"):
        value = event.get(key)
        if not isinstance(value, str) or not value or len(value) > 200:
            return False
    seq = event.get("seq")
    return seq is None or (isinstance(seq, int) and not isinstance(seq, bool))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None             # the token goes to the obs server named, nowhere else


# No proxy either: the obs server is the host's own, and a proxy would see the token.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


class Forwarder:
    def __init__(self, target: Target):
        self.target = target
        self._lock = threading.Lock()          # the queue and the counts
        self._sending = threading.Lock()       # one poster at a time
        self._warned: dict[str, str] = {}
        self._seen: set[str] = set()
        self._maybe: set[str] = set()          # sent by a post that ran out of time
        self._pending: list[tuple[str, str]] = []     # (event_id, json)
        self._events = 0
        self._bytes = 0
        self._full = False
        self._dropped = {"live": 0, "sweep": 0}
        self._bad = 0                          # events in batches obs refused as bad
        self._swept = False
        self.posted = self.rejected = self.unposted = 0
        self._wake = threading.Event()
        self._ending = threading.Event()
        self._poster: threading.Thread | None = None

    # --- taking events ------------------------------------------------------

    def feed(self, line: str, phase: str = "live") -> None:
        """One line from the relay's file: an event, or something to drop."""
        if not line.strip():
            return
        if len(line) > LINE_BYTES:
            with self._lock:
                self._dropped[phase] += 1
            return
        event = _parse(line)
        with self._lock:
            if not _well_formed(event):
                self._dropped[phase] += 1
                return
            if event["event_id"] in self._seen:
                return
            if self._full:                     # past the cap: counted, never held
                self._dropped[phase] += 1
                return
            event["pool"], event["agent_name"] = self.target.pool, self.target.name
            event["tags"] = list(self.target.tags)
            text = json.dumps(event)
            size = len(text.encode())
            if size + 2 > BATCH_BYTES:
                self._dropped[phase] += 1
                return
            if self._events + 1 > MAX_EVENTS or self._bytes + size > MAX_BYTES:
                self._full = True
                self._dropped[phase] += 1
                self._note("cap", f"the call passed the relay's cap of {MAX_EVENTS} events or "
                                  f"{MAX_BYTES // (1024 * 1024)} MB; later events weren't posted")
                return
            self._seen.add(event["event_id"])
            self._events += 1
            self._bytes += size
            self._pending.append((event["event_id"], text))
        self._wake.set()

    # --- posting ------------------------------------------------------------

    def live(self) -> None:
        """Post as events come, from a thread of its own, until finish()."""
        self._poster = threading.Thread(target=self._post_live, daemon=True, name="sssf-obs-relay")
        self._poster.start()

    def _post_live(self) -> None:
        while not self._ending.is_set():
            self._wake.wait(LIVE_SECONDS)
            self._wake.clear()
            if self._ending.is_set():
                return
            ok = self._send(time.monotonic() + RETRY_SECONDS * 6, live=True)
            if not ok and self._ending.wait(RETRY_SECONDS):
                return

    def finish(self, swept: str | None = None) -> None:
        """After the call: stop posting live, take the relay's file (None if
        it couldn't be read), and post what is left, once, within
        SWEEP_SECONDS. Events that don't go count as unposted."""
        self._ending.set()
        self._wake.set()
        if self._poster is not None:
            self._poster.join(POST_SECONDS + 1)    # it stops after the post in hand
        if swept is not None:
            self._swept = True
            for line in swept.splitlines():
                self.feed(line, "sweep")
        if not self._sending.acquire(timeout=POST_SECONDS + 1):
            with self._lock:
                self.unposted += len(self._pending)
                self._pending = []
            return
        try:
            self._send(time.monotonic() + SWEEP_SECONDS, live=False, locked=True)
            with self._lock:
                self.unposted += len(self._pending)
                self._pending = []
        finally:
            self._sending.release()

    def _send(self, deadline: float, live: bool, locked: bool = False) -> bool:
        """Post what is pending, a batch at a time, until `deadline`. False if
        obs looked down; the batch it was on stays pending. A batch obs refuses
        as bad (4xx but auth) is dropped, so the rest still go."""
        if not locked and not self._sending.acquire(blocking=False):
            return True
        try:
            while time.monotonic() < deadline and not (live and self._ending.is_set()):
                with self._lock:
                    batch, size = [], 2
                    for event_id, text in self._pending:
                        if len(batch) == BATCH_EVENTS or size + len(text.encode()) + 1 > BATCH_BYTES:
                            break
                        batch.append((event_id, text))
                        size += len(text.encode()) + 1
                if not batch:
                    return True
                status, rejected = self._post(batch, min(POST_SECONDS, max(0.1, deadline - time.monotonic())))
                with self._lock:
                    if status == "down":
                        return False
                    del self._pending[:len(batch)]
                    if status == "bad":
                        self._bad += len(batch)
                        continue
                    # A post that ran out of time may have landed; obs reports
                    # those ids as rejected when they come again.
                    landed = {r for r in rejected if r in self._maybe}
                    self.rejected += len(rejected) - len(landed)
                    self.posted += len(batch) - len(rejected) + len(landed)
                    if len(rejected) > len(landed):
                        self._note("rejected", f"obs rejected {len(rejected) - len(landed)} event(s) the relay sent")
            return True
        finally:
            if not locked:
                self._sending.release()

    def _post(self, batch: list[tuple[str, str]], seconds: float) -> tuple[str, list]:
        """("ok", rejected ids), ("bad", []) or ("down", []). The post runs in a
        thread of its own so a server that answers a byte at a time can't
        hold it past `seconds`; that thread is left, a daemon, if it does."""
        body = ("[" + ",".join(text for _, text in batch) + "]").encode()
        req = urllib.request.Request(
            self.target.server.rstrip("/") + "/events", data=body, method="POST",
            headers={"content-type": "application/json",
                     "authorization": f"Bearer {self.target.token}"})
        out: dict = {}

        def call() -> None:
            try:
                with _OPENER.open(req, timeout=seconds) as res:
                    out["answer"] = json.loads(res.read() or b"{}")
            except urllib.error.HTTPError as error:
                out["code"] = error.code
            except BaseException as error:
                out["error"] = type(error).__name__

        worker = threading.Thread(target=call, daemon=True, name="sssf-obs-post")
        worker.start()
        worker.join(seconds)
        if worker.is_alive():
            with self._lock:
                self._maybe.update(event_id for event_id, _ in batch)
            self._note("post", f"obs at {self.target.server} didn't answer in {seconds:.0f}s")
            return "down", []
        if "code" in out:
            code = out["code"]
            if 400 <= code < 500 and code not in (401, 403, 404, 408, 429):
                self._note("bad", f"obs at {self.target.server} refused a batch ({code}); it was dropped")
                return "bad", []
            self._note("post", f"obs at {self.target.server} answered {code}")
            return "down", []
        if "error" in out:
            self._note("post", f"obs at {self.target.server} couldn't be reached: {out['error']}")
            return "down", []
        answer = out.get("answer")
        rejected = answer.get("rejected") if isinstance(answer, dict) else None
        sent = {event_id for event_id, _ in batch}
        return "ok", [r for r in rejected if isinstance(r, str) and r in sent] if isinstance(rejected, list) else []

    # --- what happened --------------------------------------------------------

    def counts(self) -> dict[str, int]:
        """Lines dropped are counted from the sweep when it read the file,
        which holds every line, so a line seen live isn't counted twice."""
        with self._lock:
            dropped = (self._dropped["sweep"] if self._swept else self._dropped["live"]) + self._bad
            return {"posted": self.posted, "rejected": self.rejected,
                    "dropped": dropped, "unposted": self.unposted}

    def warnings(self) -> list[tuple[str, str]]:
        with self._lock:
            return list(self._warned.items())

    def _note(self, kind: str, why: str) -> None:
        """Called with or without self._lock held; dict writes are atomic."""
        self._warned.setdefault(kind, why)
