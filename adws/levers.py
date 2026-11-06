#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""The four levers of thread-based engineering, week by week, from the traces.

docs/research/thinking-in-threads.md compares this fork with disler's
article, which says to track four levers weekly and gives no way to. Each
installed repo's trace, adws/adw_data/sssf.db, holds most of what they need.
This reads every repo in the install registry, read-only, and prints one row
per ISO week:

  width      runs, and the most that ran at once
  time       median minutes per agent run, and agent tool calls per agent run
  depth      median distinct agents per agent run
  attention  touches: the engineer phases of agent runs plus merge commits on
             each repo's current branch; and agent tool calls per touch, the
             article's "tool calls per unit of your attention"

An agent run is one with at least one agent phase; a quality-only run counts
toward width and nothing else, since a session usually launches it as a check.
Merges happen outside SSSF, so they come from git, and a fast-forward leaves
no merge commit to count. Touches are a proxy: when a Claude session launches
the runs, an engineer phase is that session's prompt, and the engineer's own
messages and pushes are in no trace.

Usage, from the hub root:
    uv run adws/levers.py                  # the last 8 weeks, all repos
    uv run adws/levers.py --weeks 4 --by-repo
    uv run adws/levers.py --listed         # output safe to paste anywhere
"""

from __future__ import annotations

import argparse
import sqlite3
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_projects as cp  # noqa: E402


@dataclass
class Run:
    adw_id: str
    start: datetime
    end: datetime | None
    agents: int = 0
    tool_calls: int = 0
    engineer: int = 0


@dataclass
class Week:
    runs: list[Run] = field(default_factory=list)
    merges: int = 0


def _when(text: str | None) -> datetime | None:
    if not text:
        return None
    moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def week_of(moment: datetime) -> str:
    year, week, _ = moment.astimezone(timezone.utc).isocalendar()
    return f"{year}-W{week:02d}"


def runs(db: Path, since: datetime) -> list[Run]:
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        rows = conn.execute("select adw_id, started_at, ended_at from sessions").fetchall()
        agents = dict(conn.execute("select adw_id, count(distinct owner) from phases "
                                   "where kind = 'agent' group by adw_id"))
        engineer = dict(conn.execute("select adw_id, count(*) from phases "
                                     "where kind = 'engineer' group by adw_id"))
        calls = dict(conn.execute("select adw_id, count(*) from events where type = 'tool_call' "
                                  "and name not like 'quality%' group by adw_id"))
    out = []
    for adw_id, started, ended in rows:
        start = _when(started)
        if start and start >= since:
            out.append(Run(adw_id, start, _when(ended), agents.get(adw_id, 0),
                           calls.get(adw_id, 0), engineer.get(adw_id, 0)))
    return out


def merges(root: Path, since: datetime) -> list[datetime]:
    out = cp.git(root, "log", "--merges", "--first-parent", f"--since={since.isoformat()}",
                 "--format=%cI")
    return [_when(line) for line in out.split()]


def most_at_once(week_runs: list[Run]) -> int:
    edges = []
    for run in week_runs:
        edges.append((run.start, 1))
        edges.append((run.end or run.start, -1))
    best = now = 0
    for _, step in sorted(edges, key=lambda e: (e[0], e[1])):
        now += step
        best = max(best, now)
    return best


def row(week: Week) -> dict:
    agent_runs = [r for r in week.runs if r.agents]
    minutes = [((r.end - r.start).total_seconds() / 60) for r in agent_runs if r.end]
    calls = sum(r.tool_calls for r in agent_runs)
    touches = sum(r.engineer for r in agent_runs) + week.merges
    return {
        "runs": len(week.runs),
        "at_once": most_at_once(week.runs),
        "agent_runs": len(agent_runs),
        "minutes": statistics.median(minutes) if minutes else 0.0,
        "calls": calls / len(agent_runs) if agent_runs else 0.0,
        "agents": statistics.median([r.agents for r in agent_runs]) if agent_runs else 0,
        "touches": touches,
        "per_touch": calls / touches if touches else 0.0,
    }


HEADER = (f"{'week':9} {'runs':>5} {'at once':>7} {'agent runs':>10} {'min/run':>8} "
          f"{'calls/run':>9} {'agents':>6} {'touches':>7} {'calls/touch':>11}")


def line(label: str, r: dict) -> str:
    return (f"{label:9} {r['runs']:5} {r['at_once']:7} {r['agent_runs']:10} {r['minutes']:8.1f} "
            f"{r['calls']:9.1f} {r['agents']:6g} {r['touches']:7} {r['per_touch']:11.1f}")


def table(weeks: dict[str, Week], labels: list[str]) -> list[str]:
    return [HEADER] + [line(label, row(weeks.get(label, Week()))) for label in labels]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--weeks", type=int, default=8, help="how many ISO weeks back (default 8)")
    parser.add_argument("--by-repo", action="store_true", help="a table per repo after the total")
    parser.add_argument("--listed", action="store_true",
                        help="leave out the repos in the hub's .git/sssf-private-names")
    parser.add_argument("--hub", type=Path, default=cp.HUB, help=argparse.SUPPRESS)
    parser.add_argument("--registry", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--now", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    import json
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

    now = _when(args.now) if args.now else datetime.now(timezone.utc)
    monday = (now - timedelta(days=now.astimezone(timezone.utc).weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    since = monday - timedelta(weeks=args.weeks - 1)
    labels = [week_of(since + timedelta(weeks=i)) for i in range(args.weeks)]

    total: dict[str, Week] = defaultdict(Week)
    per_repo: dict[str, dict[str, Week]] = {}
    for name, entry in sorted(entries.items()):
        root = Path(entry["root"])
        db = root / entry.get("db", "adws/adw_data/sssf.db")
        repo_weeks: dict[str, Week] = defaultdict(Week)
        if db.is_file():
            for run in runs(db, since):
                repo_weeks[week_of(run.start)].runs.append(run)
                total[week_of(run.start)].runs.append(run)
        if (root / ".git").exists():
            for moment in merges(root, since):
                repo_weeks[week_of(moment)].merges += 1
                total[week_of(moment)].merges += 1
        per_repo[name] = repo_weeks

    print(f"{len(entries)} repos, weeks {labels[0]} to {labels[-1]}\n")
    print("\n".join(table(total, labels)))
    if args.by_repo:
        for name, repo_weeks in per_repo.items():
            print(f"\n{name}")
            print("\n".join(table(repo_weeks, labels)))
    if hidden:
        print(f"\n{hidden} unlisted repo(s) left out")
    return 0


if __name__ == "__main__":
    sys.exit(main())
