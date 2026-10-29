"""The Claude account's usage limits, as Claude Code last reported them.

A subscription login has a 5-hour and a 7-day usage window, shared by every
Claude Code session on the account: interactive ones and SSSF agents alike.
Claude Code reports how full each window is in a `rate_limit_event` on every
`claude -p` send. agent_cc hands each report to `record`, which keeps the
newest one in `rate_limits.json` beside the project registry, so every repo on
the machine reads the same account. `just obs-all` shows it.

`check` runs before each Claude Code agent starts. It stops the run when a
window is at or above `defaults.rate_limit_stop`, which beats a run that dies
halfway through a phase with a rate-limit error. The sample is only as fresh
as the last SSSF send; interactive sessions do not write one.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

WINDOWS = {"five_hour": "5-hour", "seven_day": "7-day"}


class RateLimitStop(RuntimeError):
    pass


def path() -> Path:
    """Beside the registry install.py writes, so tests and $SSSF_REGISTRY move both."""
    if os.environ.get("SSSF_REGISTRY"):
        return Path(os.environ["SSSF_REGISTRY"]).parent / "rate_limits.json"
    config = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(config) / "sssf" / "rate_limits.json"


def latest() -> Optional[dict]:
    try:
        return json.loads(path().read_text())
    except (OSError, ValueError):
        return None


def record(info: dict, at: Optional[str] = None, source: str = "") -> None:
    """Keep `info` unless a newer sample is already on file. Agents in two repos
    can finish out of order; the file must not go back in time."""
    at = at or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    current = latest()
    if current and str(current.get("recorded_at", "")) > at:
        return
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"recorded_at": at, "source": source, "info": info}, indent=2))
    tmp.replace(target)


def _clock(epoch: int) -> str:
    return datetime.fromtimestamp(epoch).strftime("%a %H:%M")


def check(stop_at: Optional[float], now: Optional[float] = None) -> None:
    """Raise RateLimitStop when the last sample puts a live window at or over
    `stop_at`, or Claude already refused the account. None turns this off."""
    if stop_at is None:
        return
    sample = latest()
    if not sample:
        return
    now = time.time() if now is None else now
    info = sample.get("info") or {}
    reset = int(info.get("resetsAt") or 0)
    if info.get("status") == "rejected" and reset > now:
        raise RateLimitStop(f"Claude rejected this account's last request (usage limit); "
                            f"it resets at {_clock(reset)}. Rerun then.")
    for key, label in WINDOWS.items():
        window = (info.get("unifiedWindows") or {}).get(key) or {}
        used, reset = window.get("utilization"), int(window.get("resetsAt") or 0)
        if used is None or reset <= now:
            continue
        if used >= stop_at:
            raise RateLimitStop(
                f"Claude's {label} window is at {used:.0%}, at or over rate_limit_stop "
                f"({stop_at:.0%}); it resets at {_clock(reset)}. Rerun then, or raise "
                f"defaults.rate_limit_stop in sssf.config.yaml.")
