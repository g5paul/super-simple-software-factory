"""Stop an agent that has gone quiet.

A coding agent streams a line for every message, token batch and tool update,
so a live one is rarely quiet for long. One that hangs, on a dev server left
in the foreground, a prompt waiting for input or a stalled request, writes
nothing, spends nothing, and holds its run and its worktree slot until
someone notices. The cost caps can't see it.

`watch` runs a timer beside the runner's read loop. Each line the agent
writes resets it. When nothing has come for `limit` seconds, it stops the
agent's whole process tree, which ends the read loop, and the runner raises
SilentStop. The limit is repo.yaml's `max_silence_minutes`; None turns it off.

Claude Code sends a heartbeat every 30 seconds or so while a tool runs, and
its Bash calls end within 10 minutes on their own, so on Claude Code a stop
means the CLI itself stalled. pi streams only what a command prints and has
no time limit on one, so a pi agent stuck on a command that prints nothing
for the limit is stopped. A repo whose agents run longer quiet commands
raises it.

Once the agent has sent its final result it has nothing left to say, but its
process can stay alive. The runner calls `finished()`, and after GRACE quiet
seconds the watchdog kills what's left and the call keeps its result.

Claude Code needs care here. With a background command still running, its
result isn't final: the CLI waits for the command, then starts a new turn and
sends another result (checked by hand against claude 2.1.317). So a result
sent while background tasks run is `finished(waiting=True)`: the full limit
still applies, since the agent may yet have work to do, and a stop then
keeps the latest result instead of failing the call. A new turn `reopen()`s.

In a sandbox VM the agent's output comes over a connection that can drop
with no error. The VM's process object (sandbox_remote.RemoteProcess) is told
about the watchdog, and when it judges the stream dead it calls `lose()`: the
read ends and the runner raises SilentStop as for silence, with `lost` set,
its own message, and the call priced from what arrived. agents.py records it
as `agent_stream_lost` (docs/designs/remote-sandboxes.md, decisions 28 to 35).
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Iterator, Optional


# Seconds of quiet after the final result before what's left is killed.
GRACE = 30.0

# The watchdog most recently started in this thread: SilentStop, raised by the
# runner just after its watch ends, reads from it whether the stream was lost.
_recent = threading.local()


class SilentStop(RuntimeError):
    """The agent wrote nothing for max_silence_minutes and was stopped."""

    def __init__(self, agent: str, minutes: float, adw_id: str = ""):
        dog = getattr(_recent, "dog", None)
        self.lost = bool(dog and dog.lost)
        if self.lost:
            resume = f" Resume with --adw-id {adw_id}." if adw_id else ""
            text = (f"{agent or 'the agent'}'s output stream from the sandbox VM went dead: "
                    f"nothing came for a while, and the VM {dog.lost_why}. The call is priced "
                    f"from what its raw_output.jsonl got before that; the agent may have done "
                    f"more after it.{resume}")
        else:
            resume = f" Resume with --adw-id {adw_id} once that's fixed." if adw_id else ""
            text = (f"{agent or 'the agent'} wrote nothing for {_minutes(minutes)} and was stopped "
                    f"(max_silence_minutes in adws/adw_sssf_config/repo.yaml). Its raw_output.jsonl "
                    f"shows its last step: often a command that never returns, such as a server "
                    f"started in the foreground.{resume}")
        super().__init__(text)
        self.agent = agent
        self.minutes = minutes
        self.partial = None       # the call's result so far, priced, set by the runner
        self.running: dict = {}   # the tool call still open, set by agents.py

    def __str__(self) -> str:
        text = super().__str__()
        label = self.running.get("label")
        if not label:
            return text
        since = self.running.get("started_at", "")
        return f"{text} It was waiting on `{label}`" + (f", started {since}." if since else ".")


def _minutes(value: float) -> str:
    return f"{value:g} minute" + ("" if value == 1 else "s")


class Watchdog:
    def __init__(self, process, limit: Optional[float], poll: float):
        self.process = process
        self.limit = limit
        self._full = limit
        self.poll = poll
        self.fired = False
        self.done = False
        self.lost = False         # the VM's stream went dead (lose)
        self.lost_why = ""
        self._last = time.monotonic()
        self._done = threading.Event()

    def touch(self) -> None:
        self._last = time.monotonic()

    def finished(self, waiting: bool = False) -> None:
        """The agent sent a result: quiet from here on is it done, not hung.
        Only leftovers keep it alive, so they get GRACE, unless it's
        `waiting` on background tasks that may wake it for another turn."""
        self.done = True
        if self.limit and not waiting:
            self.limit = min(self._full, GRACE)

    def reopen(self) -> None:
        """A new turn began after a result: the agent is working again."""
        self.done = False
        self.limit = self._full

    def lose(self, why: str) -> None:
        """The agent's stream from a VM went dead. Like a silence stop: before
        the agent's result the call fails, after it the result stands."""
        self.lost, self.lost_why = True, why
        self.fired = True

    @property
    def hung(self) -> bool:
        """Stopped for silence before it had finished."""
        return self.fired and not self.done

    def _watch(self) -> None:
        while not self._done.wait(self.poll):
            if time.monotonic() - self._last >= self.limit:
                self.fired = True
                try:
                    self.process.stop_tree()
                except Exception:
                    pass      # already gone; the read loop ends either way
                return


@contextmanager
def watch(process, limit_seconds: Optional[float], poll: float = 5.0) -> Iterator[Watchdog]:
    """A Watchdog over `process` while the block runs. With no limit it never fires."""
    dog = Watchdog(process, limit_seconds, min(poll, limit_seconds or poll))
    _recent.dog = dog
    watched = getattr(process, "watched", None)
    if callable(watched):
        watched(dog)          # a VM's process ends the read through it (lose)
    thread = None
    if limit_seconds:
        thread = threading.Thread(target=dog._watch, name="sssf-silence", daemon=True)
        thread.start()
    try:
        yield dog
    finally:
        dog._done.set()
        if thread:
            thread.join(timeout=1)
