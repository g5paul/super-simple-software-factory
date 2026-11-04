"""What remote VM runs have used of each provider's free allowance, from the
`sandbox_end` events in one or more traces.

An allowance is per Vercel team or E2B team, not per repo, and each repo keeps
its own trace. So the report takes every trace that shares the account and
adds them up. VMs made outside SSSF don't show here, and the provider's own
dashboard is the bill: this is SSSF's share of it.

Vercel Hobby (vercel.com/docs/sandbox/pricing, 2026-09-10) counts each month:
5 hours of active CPU, which leaves out time spent waiting, 420 GB-hours of
memory, busy or idle, and 5,000 VM creations. Past any of them, new VMs are
blocked until 30 days after the first use. E2B Hobby (e2b.dev/pricing) gives
$100 once, spent per second on the vCPUs and memory a VM holds.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERCEL_CPU_HOURS = 5.0
VERCEL_GB_HOURS = 420.0
VERCEL_CREATIONS = 5000
E2B_CREDIT_USD = 100.0
E2B_USD_PER_SECOND = 2 * 0.000014 + 4 * 0.0000045   # the sssf template's 2 vCPUs and 4 GiB
WINDOW_DAYS = 30


@dataclass
class Totals:
    runs: int = 0
    vm_seconds: float = 0.0
    cpu_seconds: float = 0.0
    cpu_unknown: int = 0        # runs from before cpu_seconds, or whose reading failed
    gb_hours: float = 0.0
    cost_usd: float = 0.0
    estimated: int = 0          # runs whose GB-hours or cost came from vm_seconds alone
    vms: set[str] = field(default_factory=set)

    def add(self, end: dict) -> None:
        if end.get("vm") in self.vms:     # one trace read twice, or a resume's repeat
            return
        self.vms.add(end.get("vm"))
        seconds = float(end.get("vm_seconds") or 0)
        self.runs += 1
        self.vm_seconds += seconds
        if end.get("cpu_seconds") is None:
            self.cpu_unknown += 1
        else:
            self.cpu_seconds += float(end["cpu_seconds"])
        if end.get("provider") == "vercel":
            if "memory_gb_hours" in end:
                self.gb_hours += float(end["memory_gb_hours"])
            else:
                self.gb_hours += 4 * seconds / 3600
                self.estimated += 1
        elif "vm_cost_usd" in end:
            self.cost_usd += float(end["vm_cost_usd"])
        else:
            self.cost_usd += seconds * E2B_USD_PER_SECOND
            self.estimated += 1


def ends(db: Path) -> list[tuple[datetime, dict]]:
    """Every `sandbox_end` in one trace, oldest first."""
    if not db.is_file():
        raise FileNotFoundError(f"no trace at {db}")
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        rows = conn.execute("select started_at, payload_json from events "
                            "where name='sandbox_end' order by started_at").fetchall()
    return [(datetime.fromisoformat(at), json.loads(payload)) for at, payload in rows]


def totals(dbs: list[Path], now: datetime | None = None) -> dict[str, dict[str, Totals]]:
    """Per provider, what the last WINDOW_DAYS used and what all time used."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=WINDOW_DAYS)
    out: dict[str, dict[str, Totals]] = {}
    for db in dbs:
        for at, end in ends(db):
            provider = out.setdefault(end.get("provider", "?"), {"window": Totals(), "all": Totals()})
            provider["all"].add(end)
            if at >= since:
                provider["window"].add(end)
    return out


def _share(used: float, allowed: float) -> str:
    return f"{used / allowed:.1%} of {allowed:g}"


def _cpu_hours(t: Totals) -> str:
    """CPU hours, or "unknown" when no run in `t` has a reading: a zero
    would read as no CPU used."""
    return "unknown" if t.runs and t.cpu_unknown == t.runs else f"{t.cpu_seconds / 3600:.3f} h"


def report(dbs: list[Path], now: datetime | None = None) -> list[str]:
    found = totals(dbs, now)
    lines = [f"traces: {', '.join(str(db) for db in dbs)}"]
    if not found:
        return lines + ["no remote VM runs in these traces"]
    for name in sorted(found):
        window, ever = found[name]["window"], found[name]["all"]
        lines.append("")
        if name == "vercel":
            cpu_h = window.cpu_seconds / 3600
            lines += [f"vercel, last {WINDOW_DAYS} days: {window.runs} runs, "
                      f"{window.vm_seconds / 3600:.2f} VM hours",
                      f"  active CPU   {_cpu_hours(window)}     {_share(cpu_h, VERCEL_CPU_HOURS)} h",
                      f"  memory       {window.gb_hours:.2f} GB-h   {_share(window.gb_hours, VERCEL_GB_HOURS)} GB-h",
                      f"  creations    {window.runs}           {_share(window.runs, VERCEL_CREATIONS)}"]
            if window.runs:
                per = window.cpu_seconds / max(window.runs - window.cpu_unknown, 1)
                if window.runs > window.cpu_unknown and per:
                    lines.append(f"  at {per:.0f} CPU seconds a run, 5 hours covers about "
                                 f"{VERCEL_CPU_HOURS * 3600 / per:.0f} runs a month")
            unknown, estimated = window.cpu_unknown, window.estimated
        else:
            lines += [f"{name}, all time: {ever.runs} runs, {ever.vm_seconds / 3600:.2f} VM hours, "
                      f"CPU {_cpu_hours(ever)}",
                      f"  credit       ${ever.cost_usd:.4f}   {_share(ever.cost_usd, E2B_CREDIT_USD)} dollars,"
                      " Hobby's one-time credit",
                      f"  last {WINDOW_DAYS} days ${window.cost_usd:.4f} over {window.runs} runs"]
            if ever.runs:
                lines.append(f"  at ${ever.cost_usd / ever.runs:.4f} a run, what's left covers about "
                             f"{(E2B_CREDIT_USD - ever.cost_usd) / (ever.cost_usd / ever.runs or 1):.0f} runs")
            unknown, estimated = ever.cpu_unknown, ever.estimated
        if unknown:
            lines.append(f"  {unknown} runs have no CPU reading: from before SSSF measured it, "
                         "or the reading failed")
        if estimated:
            lines.append(f"  {estimated} runs estimated from VM seconds at 2 vCPUs and 4 GiB")
    lines += ["", "SSSF's runs only, from these traces, against each provider's Hobby allowance",
              "as its pricing page gives it (built into sandbox_usage.py, not a setting).",
              "The provider's dashboard is the bill."]
    return lines
