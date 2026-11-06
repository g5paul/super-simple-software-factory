"""The host side of the obs relay for VM runs (docs/designs/remote-sandboxes.md,
decisions 37 to 50).

In a VM, a pi agent's obs extension posts to SSSF's relay (sssf_obs_relay.cjs)
on the VM's loopback, which writes each event as a line. The host reads those
lines live from the relay's stdout, and once more from its file after the
call, and posts them to the engineer's obs server with the host's token,
which never enters the VM. A Forwarder does the host's part for one call:

- It keeps only well-formed events, and sets pool, agent_name and tags to the
  call's own (Target), since the agent can write anything into the file.
- It posts each event once: obs keys events by event_id, so the sweep after
  the call sends only what the live stream didn't.
- It caps a call at MAX_EVENTS and MAX_BYTES, and posts in batches of at most
  BATCH_EVENTS and BATCH_BYTES, under obs's 4 MB request limit.
- It never raises. A failure is one warning of its kind per call (`warn`),
  and `counts()` says what happened to each event.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

RELAY_SCRIPT = Path(__file__).with_name("sssf_obs_relay.cjs")
DEFAULT_SERVER = "http://127.0.0.1:43190"     # where the extension posts when unset
MAX_EVENTS = 20_000                           # a call's cap: about 40 times the busiest
MAX_BYTES = 20 * 1024 * 1024                  # pi session obs held (decision 45)
BATCH_EVENTS = 50                             # as the extension batches
BATCH_BYTES = 3 * 1024 * 1024                 # under obs's 4 MB request limit
POST_SECONDS = 10                             # one post's timeout
LIVE_SECONDS = 0.5                            # how often the live poster sends
RETRY_SECONDS = 5.0                           # after a failed live post


@dataclass
class Target:
    """Where a call's events go, and the names SSSF gave the call."""
    server: str
    token: str = field(repr=False)
    name: str = ""
    pool: str = ""
    tags: list[str] = field(default_factory=list)


def _well_formed(event) -> bool:
    if not isinstance(event, dict):
        return False
    for key in ("event_id", "type", "session_id"):
        value = event.get(key)
        if not isinstance(value, str) or not value or len(value) > 200:
            return False
    seq = event.get("seq")
    if seq is not None and (isinstance(seq, bool) or not isinstance(seq, int)):
        return False
    ts = event.get("ts")
    return ts is None or isinstance(ts, str)


class Forwarder:
    def __init__(self, target: Target, warn: Callable[[str, str], None]):
        self.target = target
        self._warn = warn
        self._warned: set[str] = set()
        self._lock = threading.Lock()
        self._seen: set[str] = set()
        self._pending: list[tuple[str, str]] = []     # (event_id, json)
        self._events = 0
        self._bytes = 0
        self._full = False
        self.posted = self.rejected = self.dropped = self.unposted = 0
        self._wake = threading.Event()
        self._ending = threading.Event()
        self._poster: threading.Thread | None = None

    # --- taking events ------------------------------------------------------

    def feed(self, line: str) -> None:
        """One line from the relay: an event, or something to drop."""
        line = line.strip()
        if not line:
            return
        try:
            event = json.loads(line)
        except ValueError:
            event = None
        with self._lock:
            if not _well_formed(event):
                self.dropped += 1
                return
            if event["event_id"] in self._seen:
                return
            self._seen.add(event["event_id"])
            event["pool"], event["agent_name"] = self.target.pool, self.target.name
            event["tags"] = list(self.target.tags)
            text = json.dumps(event)
            size = len(text.encode())
            if self._full or self._events + 1 > MAX_EVENTS or self._bytes + size > MAX_BYTES:
                self._full = True
                self.dropped += 1
                full = True
            elif size + 2 > BATCH_BYTES:
                self.dropped += 1
                return
            else:
                full = False
                self._events += 1
                self._bytes += size
                self._pending.append((event["event_id"], text))
        if full:
            self._once("cap", f"the call passed the relay's cap of {MAX_EVENTS} events or "
                              f"{MAX_BYTES // (1024 * 1024)} MB; later events weren't posted")
            return
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
            if not self._send() and self._ending.wait(RETRY_SECONDS):
                return

    def finish(self, swept: str = "") -> None:
        """After the call: stop posting live, take the relay's file, and post
        what is left, once. Events that still can't go count as unposted."""
        self._ending.set()
        self._wake.set()
        if self._poster is not None:
            self._poster.join(POST_SECONDS + 1)
        for line in swept.splitlines():
            self.feed(line)
        if not self._send():
            with self._lock:
                self.unposted += len(self._pending)
                self._pending = []

    def _send(self) -> bool:
        """Post what is pending, a batch at a time. False if a post failed;
        the failed batch and the rest stay pending."""
        while True:
            with self._lock:
                batch, size = [], 2
                for event_id, text in self._pending:
                    if len(batch) == BATCH_EVENTS or size + len(text.encode()) + 1 > BATCH_BYTES:
                        break
                    batch.append((event_id, text))
                    size += len(text.encode()) + 1
            if not batch:
                return True
            rejected = self._post(batch)
            if rejected is None:
                return False
            with self._lock:
                del self._pending[:len(batch)]
                self.rejected += len(rejected)
                self.posted += len(batch) - len(rejected)
            if rejected:
                self._once("rejected", f"obs rejected {len(rejected)} event(s) the relay sent")

    def _post(self, batch: list[tuple[str, str]]) -> list | None:
        body = ("[" + ",".join(text for _, text in batch) + "]").encode()
        req = urllib.request.Request(
            self.target.server.rstrip("/") + "/events", data=body, method="POST",
            headers={"content-type": "application/json",
                     "authorization": f"Bearer {self.target.token}"})
        try:
            with urllib.request.urlopen(req, timeout=POST_SECONDS) as res:
                answer = json.loads(res.read() or b"{}")
        except urllib.error.HTTPError as error:
            self._once("post", f"obs at {self.target.server} answered {error.code}")
            return None
        except (OSError, ValueError) as error:
            self._once("post", f"obs at {self.target.server} couldn't be reached: {type(error).__name__}")
            return None
        rejected = answer.get("rejected") if isinstance(answer, dict) else None
        sent = {event_id for event_id, _ in batch}
        return [r for r in rejected if r in sent] if isinstance(rejected, list) else []

    # --- what happened --------------------------------------------------------

    def counts(self) -> dict[str, int]:
        with self._lock:
            return {"posted": self.posted, "rejected": self.rejected,
                    "dropped": self.dropped, "unposted": self.unposted}

    def _once(self, kind: str, why: str) -> None:
        with self._lock:
            if kind in self._warned:
                return
            self._warned.add(kind)
        try:
            self._warn(kind, why)
        except Exception:
            pass
