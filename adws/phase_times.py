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


def report(name: str, phases: list[Phase], runs: list[RunTime], *, history: int, min_runs: int,
           days: int, factor: float, now: datetime, every: bool = False) -> list[str]:
    seen = norms(phases, runs, history)
    lines = [name]
    chains = sorted({chain for chain, _ in seen})
    for chain in chains:
        rows = sorted(((fam, v) for (c, fam), v in seen.items() if c == chain and not fam.startswith("<")),
                      key=lambda kv: -statistics.median(kv[1]))
        judged = len(seen.get((chain, "<run>"), [])) >= min_runs
        lines.append(f"  {chain}" + ("" if judged else f"  (fewer than {min_runs} successful runs, so nothing is judged)"))
        lines.append(f"    {'phase':28} {'n':>4} {'median':>7} {'p90':>7}   minutes")
        for fam, values in [(f"(whole run)", seen.get((chain, '<run>'), []))] + rows:
            if values and (every or fam == "(whole run)" or p90(values) >= FLOOR):
                lines.append(f"    {fam:28} {len(values):>4} {minutes(statistics.median(values)):>7} "
                             f"{minutes(p90(values)):>7}")
        costs = seen.get((chain, "<cost>"), [])
        if costs:
            lines.append(f"    {'(cost, $)':28} {len(costs):>4} {statistics.median(costs):>7.2f} {p90(costs):>7.2f}")

    since = now - timedelta(days=days)
    slow = []
    left = [p for p in phases if p.status == "left running" and p.start >= since]
    for phase in phases:
        if phase.status == "left running":
            continue
        values = seen.get((phase.chain, family(phase.name)), [])
        if phase.start < since or len(values) < min_runs:
            continue
        limit = max(FLOOR, factor * p90(values))
        if phase.seconds > limit:
            slow.append((phase.start, f"    {phase.adw_id}  {phase.chain}  {phase.name}: {minutes(phase.seconds)} min"
                                      f"{' so far' if phase.status == 'running' else ''}, "
                                      f"{phase.seconds / p90(values):.1f}x its p90 of {minutes(p90(values))} "
                                      f"({phase.status})"))
    for run in runs:
        if run.start < since:
            continue
        values = seen.get((run.chain, "<run>"), [])
        if len(values) >= min_runs and run.seconds > max(FLOOR, factor * p90(values)):
            slow.append((run.start, f"    {run.adw_id}  {run.chain}  whole run: {minutes(run.seconds)} min, "
                                    f"{run.seconds / p90(values):.1f}x its p90 of {minutes(p90(values))} ({run.status})"))
        costs = seen.get((run.chain, "<cost>"), [])
        if len(costs) >= min_runs and p90(costs) > 0 and run.cost > factor * p90(costs):
            slow.append((run.start, f"    {run.adw_id}  {run.chain}  cost: ${run.cost:.2f}, "
                                    f"{run.cost / p90(costs):.1f}x its p90 of ${p90(costs):.2f} ({run.status})"))
    lines.append(f"  past {factor:g}x the p90, last {days} days: {len(slow)}")
    lines += [text for _, text in sorted(slow)]
    if left:
        lines.append(f"  phases still marked running in a run that ended: {len(left)}")
        lines += [f"    {p.adw_id}  {p.chain}  {p.name}, started {p.start:%Y-%m-%d %H:%M}Z" for p in left]
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", default=None, help="one repo, by its registry name")
    parser.add_argument("--days", type=int, default=14, help="how far back to look for slow runs (default 14)")
    parser.add_argument("--history", type=int, default=50, help="runs per chain the norms come from (default 50)")
    parser.add_argument("--min-runs", type=int, default=10, help="samples a row needs before it judges (default 10)")
    parser.add_argument("--factor", type=float, default=2.0, help="how far past the p90 is slow (default 2)")
    parser.add_argument("--all", action="store_true", help="also list phases whose p90 is under a minute")
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
