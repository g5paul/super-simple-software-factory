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

A tool call counts as quiet while it runs without output: Claude Code streams
nothing during a Bash call, and pi streams only what the command prints. A
Claude Code Bash call ends within 10 minutes on its own, so the default of 15
leaves room for one. A repo whose agents run longer quiet commands raises it.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Iterator, Optional


class SilentStop(RuntimeError):
    """The agent wrote nothing for max_silence_minutes and was stopped."""

    def __init__(self, agent: str, minutes: float, adw_id: str = ""):
        resume = f" Resume with --adw-id {adw_id} once that's fixed." if adw_id else ""
        super().__init__(
            f"{agent or 'the agent'} wrote nothing for {_minutes(minutes)} and was stopped "
            f"(max_silence_minutes in adws/adw_sssf_config/repo.yaml). Its raw_output.jsonl "
            f"shows its last step: often a command that never returns, such as a server "
            f"started in the foreground.{resume}")
        self.agent = agent
        self.minutes = minutes
        self.partial = None       # the call's result so far, priced, set by the runner


def _minutes(value: float) -> str:
    return f"{value:g} minute" + ("" if value == 1 else "s")


class Watchdog:
    def __init__(self, process, limit: Optional[float], poll: float):
        self.process = process
        self.limit = limit
        self.poll = poll
        self.fired = False
        self._last = time.monotonic()
        self._done = threading.Event()

    def touch(self) -> None:
        self._last = time.monotonic()

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
