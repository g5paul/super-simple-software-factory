#!/usr/bin/env python3
"""A Claude Code statusline that records the account's usage windows for SSSF.

Claude Code runs a statusline command after every reply and hands it JSON on
stdin, with `rate_limits.five_hour` and `rate_limits.seven_day` for a
subscription login: `used_percentage` from 0 to 100 and `resets_at` in epoch
seconds (code.claude.com/docs/en/statusline). This merges them into
rate_limits.json, where SSSF agents' readings go, so the dashboard's meters and
`rate_limit_stop` stay fresh while the engineer works interactively. It prints
one short line for the statusline itself.

Set it in ~/.claude/settings.json, pointing at the newest skill:
    "statusLine": {"type": "command", "command": "python3 <hub>/.claude/skills/sssf/scripts/statusline.py"}

It must never break the statusline: any error is swallowed, and it always
prints something and exits 0. Standard library only, so it starts fast.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "templates" / "adws" / "adw_modules"))

KEYS = ("five_hour", "seven_day")
LABELS = {"five_hour": "5h", "seven_day": "7d"}


def windows(data: dict) -> dict:
    """The rate_limits windows in unifiedWindows form: utilization 0 to 1, resetsAt epoch seconds."""
    limits = data.get("rate_limits") or {}
    out = {}
    for key in KEYS:
        w = limits.get(key) or {}
        used, reset = w.get("used_percentage"), w.get("resets_at")
        if isinstance(used, (int, float)) and isinstance(reset, (int, float)):
            out[key] = {"utilization": round(used / 100, 4), "resetsAt": int(reset)}
    return out


def line(data: dict, found: dict) -> str:
    model = ((data.get("model") or {}).get("display_name")) or "Claude"
    parts = [model]
    for key in KEYS:
        if key in found:
            parts.append(f"{LABELS[key]} {round(found[key]['utilization'] * 100)}%")
    cost = (data.get("cost") or {}).get("total_cost_usd")
    if isinstance(cost, (int, float)):
        parts.append(f"${cost:.2f} session")
    return " · ".join(parts)


def main(stdin=sys.stdin, out=sys.stdout) -> int:
    data: dict = {}
    found: dict = {}
    try:
        data = json.loads(stdin.read() or "{}")
        found = windows(data)
        if found:
            import account
            at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            account.record_windows(found, at=at, source="statusline")
    except Exception:  # noqa: BLE001 - a broken statusline helps no one
        pass
    try:
        print(line(data if isinstance(data, dict) else {}, found), file=out)
    except Exception:  # noqa: BLE001
        print("Claude", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
