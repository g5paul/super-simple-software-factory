#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""How long each chain's phases usually take, and the runs that went well past it.

Reads every repo in the install registry, read-only. For each chain (a
session's adw_name) and phase, it takes the durations of that phase's
successful attempts in the last --history runs of the chain, and prints the
count, the median and the 90th percentile; --all adds the phases whose 90th
percentile is under a minute. Numbered phases share a row:
bypass_review_1 and bypass_review_2 are bypass_review_N.

It then lists every phase in the last --days days that ran longer than
--factor times its 90th percentile, a phase still running included, and every
run whose whole time or cost did. A row needs at least --min-runs samples
before it judges anything, and nothing under one minute is called slow, so a
git step that usually takes a second doesn't flag at three.

A slow phase isn't a failure. SilentStop already stops an agent that writes
nothing for 15 minutes; this finds the ones that kept writing and ran long,
so someone can look at why.

Usage, from the hub root:
    uv run adws/phase_times.py                    # every repo
    uv run adws/phase_times.py --repo sssf-sandbox
    uv run adws/phase_times.py --days 30 --factor 1.5
    uv run adws/phase_times.py --listed           # output safe to paste anywhere
    uv run adws/phase_times.py --json             # for the factory page

--json prints one object: per repo, each chain's rows (median and p90 in
seconds), its cost norms, and every phase, run or cost past --factor times
its p90 in the window, with its ratio, so a reader can apply its own,
higher bar. Rows whose p90 is under a minute are always included there.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_projects as cp  # noqa: E402

FLOOR = 60.0     # seconds; nothing shorter is called slow


@dataclass
class Phase:
    adw_id: str
    chain: str
    name: str
    status: str
    start: datetime
    seconds: float      # to now, for a phase still running


@dataclass
class RunTime:
    adw_id: str
    chain: str
    status: str
    start: datetime
    seconds: float
    cost: float


def _when(text: str | None) -> datetime | None:
    if not text:
        return None
    moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def family(name: str) -> str:
    """bypass_review_2 and fix_round_1_verify_3 share a row with the other rounds."""
    return re.sub(r"_\d+", "_N", name)


def read(db: Path, now: datetime) -> tuple[list[Phase], list[RunTime]]:
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        sessions = conn.execute("select adw_id, adw_name, status, started_at, ended_at, total_cost "
                                "from sessions").fetchall()
        phases = conn.execute("select adw_id, name, status, started_at, ended_at from phases").fetchall()
    chain = {}
    live = {adw_id for adw_id, _, status, *_ in sessions if status == "running"}
    runs = []
    for adw_id, name, status, started, ended, cost in sessions:
        chain[adw_id] = name or "?"
        start = _when(started)
        if not start:
            continue
        end = _when(ended) or (now if status == "running" else None)
        if end:
            runs.append(RunTime(adw_id, chain[adw_id], status or "", start, (end - start).total_seconds(),
                                cost or 0.0))
    out = []
    for adw_id, name, status, started, ended in phases:
        start = _when(started)
        if not start:
            continue
        if status == "running" and adw_id not in live:
            status = "left running"      # its run ended without closing it, as a restart can leave it
        end = _when(ended) or (now if status == "running" else None)
        if end or status == "left running":
            end = end or start
            out.append(Phase(adw_id, chain.get(adw_id, "?"), name, status or "", start,
                             (end - start).total_seconds()))
    return out, runs


def p90(values: list[float]) -> float:
    values = sorted(values)
    return values[min(len(values) - 1, int(0.9 * len(values)))]


def norms(phases: list[Phase], runs: list[RunTime], history: int) -> dict:
    """{(chain, family): [seconds]} from successful attempts in each chain's last `history` runs,
    plus {(chain, '<run>'): ...} and {(chain, '<cost>'): ...} from its successful runs."""
    recent: dict[str, set[str]] = defaultdict(set)
    for run in sorted(runs, key=lambda r: r.start, reverse=True):
        if len(recent[run.chain]) < history:
            recent[run.chain].add(run.adw_id)
    out: dict[tuple[str, str], list[float]] = defaultdict(list)
    for phase in phases:
        if phase.status == "success" and phase.adw_id in recent[phase.chain]:
            out[(phase.chain, family(phase.name))].append(phase.seconds)
    for run in runs:
        if run.status == "success" and run.adw_id in recent[run.chain]:
            out[(run.chain, "<run>")].append(run.seconds)
            out[(run.chain, "<cost>")].append(run.cost)
    return out


def minutes(seconds: float) -> str:
    return f"{seconds / 60:.1f}"


def analyse(phases: list[Phase], runs: list[RunTime], *, history: int, min_runs: int,
            days: int, factor: float, now: datetime) -> dict:
    """The norms per chain, and what in the last `days` days went past `factor` times its p90."""
    seen = norms(phases, runs, history)
    chains = []
    for chain in sorted({chain for chain, _ in seen}):
        rows = sorted(((fam, v) for (c, fam), v in seen.items() if c == chain and not fam.startswith("<")),
                      key=lambda kv: -statistics.median(kv[1]))
        costs = seen.get((chain, "<cost>"), [])
        chains.append({
            "chain": chain,
            "judged": len(seen.get((chain, "<run>"), [])) >= min_runs,
            "rows": [{"phase": fam, "n": len(values), "median_s": statistics.median(values),
                      "p90_s": p90(values)}
                     for fam, values in [("(whole run)", seen.get((chain, "<run>"), []))] + rows if values],
            "cost": ({"n": len(costs), "median": statistics.median(costs), "p90": p90(costs)}
                     if costs else None)})

    since = now - timedelta(days=days)
    slow = []
    for phase in phases:
        if phase.status == "left running":
            continue
        values = seen.get((phase.chain, family(phase.name)), [])
        if phase.start < since or len(values) < min_runs:
            continue
        if phase.seconds > max(FLOOR, factor * p90(values)):
            slow.append({"kind": "phase", "adw_id": phase.adw_id, "chain": phase.chain, "name": phase.name,
                         "status": phase.status, "running": phase.status == "running", "start": phase.start,
                         "seconds": phase.seconds, "p90_s": p90(values), "ratio": phase.seconds / p90(values)})
    for run in runs:
        if run.start < since:
            continue
        values = seen.get((run.chain, "<run>"), [])
        if len(values) >= min_runs and run.seconds > max(FLOOR, factor * p90(values)):
            slow.append({"kind": "run", "adw_id": run.adw_id, "chain": run.chain, "status": run.status,
                         "start": run.start, "seconds": run.seconds, "p90_s": p90(values),
                         "ratio": run.seconds / p90(values)})
        costs = seen.get((run.chain, "<cost>"), [])
        if len(costs) >= min_runs and p90(costs) > 0 and run.cost > factor * p90(costs):
            slow.append({"kind": "cost", "adw_id": run.adw_id, "chain": run.chain, "status": run.status,
                         "start": run.start, "cost": run.cost, "p90": p90(costs), "ratio": run.cost / p90(costs)})
    slow.sort(key=lambda item: item["start"])
    left = [{"adw_id": p.adw_id, "chain": p.chain, "name": p.name, "start": p.start}
            for p in phases if p.status == "left running" and p.start >= since]
    return {"chains": chains, "slow": slow, "left_running": left}


def report(name: str, phases: list[Phase], runs: list[RunTime], *, history: int, min_runs: int,
           days: int, factor: float, now: datetime, every: bool = False) -> list[str]:
    found = analyse(phases, runs, history=history, min_runs=min_runs, days=days, factor=factor, now=now)
    lines = [name]
    for chain in found["chains"]:
        lines.append(f"  {chain['chain']}" + ("" if chain["judged"] else
                                              f"  (fewer than {min_runs} successful runs, so nothing is judged)"))
        lines.append(f"    {'phase':28} {'n':>4} {'median':>7} {'p90':>7}   minutes")
        for row in chain["rows"]:
            if every or row["phase"] == "(whole run)" or row["p90_s"] >= FLOOR:
                lines.append(f"    {row['phase']:28} {row['n']:>4} {minutes(row['median_s']):>7} "
                             f"{minutes(row['p90_s']):>7}")
        if chain["cost"]:
            cost = chain["cost"]
            lines.append(f"    {'(cost, $)':28} {cost['n']:>4} {cost['median']:>7.2f} {cost['p90']:>7.2f}")

    lines.append(f"  past {factor:g}x the p90, last {days} days: {len(found['slow'])}")
    for item in found["slow"]:
        if item["kind"] == "phase":
            lines.append(f"    {item['adw_id']}  {item['chain']}  {item['name']}: {minutes(item['seconds'])} min"
                         f"{' so far' if item['running'] else ''}, "
                         f"{item['ratio']:.1f}x its p90 of {minutes(item['p90_s'])} ({item['status']})")
        elif item["kind"] == "run":
            lines.append(f"    {item['adw_id']}  {item['chain']}  whole run: {minutes(item['seconds'])} min, "
                         f"{item['ratio']:.1f}x its p90 of {minutes(item['p90_s'])} ({item['status']})")
        else:
            lines.append(f"    {item['adw_id']}  {item['chain']}  cost: ${item['cost']:.2f}, "
                         f"{item['ratio']:.1f}x its p90 of ${item['p90']:.2f} ({item['status']})")
    if found["left_running"]:
        lines.append(f"  phases still marked running in a run that ended: {len(found['left_running'])}")
        lines += [f"    {p['adw_id']}  {p['chain']}  {p['name']}, started {p['start']:%Y-%m-%d %H:%M}Z"
                  for p in found["left_running"]]
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", default=None, help="one repo, by its registry name")
    parser.add_argument("--days", type=int, default=14, help="how far back to look for slow runs (default 14)")
    parser.add_argument("--history", type=int, default=50, help="runs per chain the norms come from (default 50)")
    parser.add_argument("--min-runs", type=int, default=10, help="samples a row needs before it judges (default 10)")
    parser.add_argument("--factor", type=float, default=2.0, help="how far past the p90 is slow (default 2)")
    parser.add_argument("--all", action="store_true", help="also list phases whose p90 is under a minute")
    parser.add_argument("--json", action="store_true", help="print one JSON object instead of text")
    parser.add_argument("--listed", action="store_true",
                        help="leave out the repos in the hub's .git/sssf-private-names")
    parser.add_argument("--hub", type=Path, default=cp.HUB, help=argparse.SUPPRESS)
    parser.add_argument("--registry", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--now", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    entries = json.loads((args.registry or cp.registry_path()).read_text()).get("projects", {})
    hidden = 0
    if args.listed:
        names = cp.private_names(args.hub)
        if names is None:
            print("--listed needs the hub's .git/sssf-private-names, and it doesn't exist; "
                  "refusing rather than print every repo", file=sys.stderr)
            return 1
        kept = {n: e for n, e in entries.items() if not cp.unlisted(n, e, names)}
        hidden, entries = len(entries) - len(kept), kept
    if args.repo:
        if args.repo not in entries:
            print(f"no repo named {args.repo} in the registry", file=sys.stderr)
            return 1
        entries = {args.repo: entries[args.repo]}

    now = _when(args.now) if args.now else datetime.now(timezone.utc)
    if args.json:
        out = []
        for name, entry in sorted(entries.items()):
            db = Path(entry["root"]) / entry.get("db", "adws/adw_data/sssf.db")
            if db.is_file():
                phases, runs = read(db, now)
                out.append({"repo": name} | analyse(phases, runs, history=args.history, min_runs=args.min_runs,
                                                    days=args.days, factor=args.factor, now=now))
        print(json.dumps({"now": now, "days": args.days, "factor": args.factor, "hidden": hidden, "repos": out},
                         indent=1, default=lambda v: v.isoformat()))
        return 0
    blocks = []
    for name, entry in sorted(entries.items()):
        db = Path(entry["root"]) / entry.get("db", "adws/adw_data/sssf.db")
        if db.is_file():
            phases, runs = read(db, now)
            if runs:
                blocks.append("\n".join(report(name, phases, runs, history=args.history, min_runs=args.min_runs,
                                               days=args.days, factor=args.factor, now=now, every=args.all)))
    print("\n\n".join(blocks) if blocks else "no traces with runs")
    if hidden:
        print(f"\n{hidden} unlisted repo(s) left out")
    return 0


if __name__ == "__main__":
    sys.exit(main())
