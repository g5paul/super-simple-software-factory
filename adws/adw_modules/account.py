"""The Claude account's usage limits, as Claude Code last reported them.

A subscription login has a 5-hour and a 7-day usage window, shared by every
Claude Code session on the account: interactive ones and SSSF agents alike.
A verified Ultra account has no 5-hour window, and reports only the 7-day one.
Claude Code reports how full each window is in a `rate_limit_event` on every
`claude -p` send. agent_cc hands each report to `record`, which keeps the
newest one in `rate_limits.json` beside the project registry, so every repo on
the machine reads the same account. `just obs-all` shows it.

`check` runs before each Claude Code agent starts. It stops the run when a
window is at or above `defaults.rate_limit_stop`, which beats a run that dies
halfway through a phase with a rate-limit error. Interactive sessions add
readings too once the engineer sets SSSF's statusline (scripts/statusline.py):
Claude Code hands a statusline command the same windows after every reply,
and `record_windows` merges them in.

Each kept reading also goes into `rate_limits_history.jsonl` beside it, when
a window moved or the last line is an hour old, so the dashboard can tell how
fast each window fills in percent per hour. It keeps about 8 days.
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
    partial = None                  # what a call killed mid-turn had spent, set by agent_cc


# Usage credits let a Pro, Max or Ultra plan run past its limit at API rates,
# billed as real money. Claude Code switches to them on its own once the
# account has them on, and says so with isUsingOverage.
CREDITS = ("Claude moved this account onto usage credits: its plan's limit is used up, and "
           "further calls bill at API rates. SSSF stopped rather than spend real money. "
           "Rerun when the limit resets, or set defaults.rate_limit_stop to null in "
           "sssf.config.yaml to let runs spend credits.")


def on_credits(info: dict) -> bool:
    return bool(info.get("isUsingOverage"))


def path() -> Path:
    """Beside the registry install.py writes, so tests and $SSSF_REGISTRY move both."""
    if os.environ.get("SSSF_REGISTRY"):
        return Path(os.environ["SSSF_REGISTRY"]).parent / "rate_limits.json"
    config = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(config) / "sssf" / "rate_limits.json"


HISTORY_EVERY_S = 3600             # an unchanged reading still gets a line this often
HISTORY_KEEP_S = 8 * 86400
HISTORY_PRUNE_AT = 3000            # lines; past this, older ones go


def history_path() -> Path:
    return path().with_name("rate_limits_history.jsonl")


def _epoch(at: str) -> float:
    return datetime.fromisoformat(at.replace("Z", "+00:00")).timestamp()


def _append_history(info: dict, at: str) -> None:
    """One line per change of a window, and one an hour while nothing moves.
    A gap longer than that means nothing was recording, and the dashboard
    measures only between lines."""
    windows = {k: [w.get("utilization"), w.get("resetsAt")]
               for k, w in (info.get("unifiedWindows") or {}).items()
               if k in WINDOWS and isinstance(w, dict) and isinstance(w.get("utilization"), (int, float))}
    if not windows:
        return
    target = history_path()
    try:
        lines = target.read_text().splitlines() if target.exists() else []
    except OSError:
        lines = []
    try:
        last = json.loads(lines[-1]) if lines else None
    except ValueError:
        last = None
    if last and last.get("w") == windows and _epoch(at) - _epoch(str(last.get("at"))) < HISTORY_EVERY_S:
        return
    line = json.dumps({"at": at, "w": windows}, separators=(",", ":"))
    if len(lines) >= HISTORY_PRUNE_AT:
        cutoff = _epoch(at) - HISTORY_KEEP_S
        kept = []
        for old in lines:
            try:
                if _epoch(str(json.loads(old).get("at"))) >= cutoff:
                    kept.append(old)
            except (ValueError, TypeError):
                continue
        tmp = target.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text("".join(f"{x}\n" for x in [*kept, line]))
        tmp.replace(target)
        return
    with target.open("a") as f:
        f.write(f"{line}\n")


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
    try:
        _append_history(info, at)
    except (OSError, ValueError, TypeError):
        pass                        # the history is for the dashboard; a run never waits on it


def record_windows(windows: dict, at: Optional[str] = None, source: str = "statusline") -> None:
    """Merge windows read elsewhere, keyed like unifiedWindows, into the newest
    sample. The sample's other fields stay: a statusline reports no status or
    overage, and `check` still needs the last ones an agent call reported."""
    if not windows:
        return
    current = latest() or {}
    info = dict(current.get("info") or {})
    info["unifiedWindows"] = {**(info.get("unifiedWindows") or {}), **windows}
    record(info, at=at, source=source)


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
    if on_credits(info) and reset > now:
        raise RateLimitStop(f"{CREDITS} The limit resets at {_clock(reset)}.")
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
