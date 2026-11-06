#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""What each feature cost: SSSF runs joined to the commits that landed.

docs/research/thinking-in-threads.md, "by run type and by feature", has the
reasoning. A feature is a change that reached a repo's main line. Every commit
phase logs its sha in the trace, so for each agent run in the window this
takes the run's commits and finds where each landed on the main line, walking
it by first parent: a merge commit, or after a fast-forward the commit itself.
Runs that landed at the same point are one feature: its cost is all of them,
its run count is the rework, and its lead time runs from the first run's start
to the landing commit.

A rebase rewrites shas, so a commit not found by sha is matched by
`git patch-id --stable`, then by subject. A run whose commits never landed is
pending when a branch still holds one of them, and gone otherwise: discarded
work, the waste the levers can't see. A run with no commits, such as a scout
or a run that failed before committing, is counted apart.

It only reads git and each trace. The main line is each repo's current branch
unless --branch says otherwise.

Usage, from the hub root:
    uv run adws/features.py                      # the last 2 weeks, all repos
    uv run adws/features.py --weeks 4 --repo fusion-harness
    uv run adws/features.py --branch yt=main     # a repo's main line
    uv run adws/features.py --listed --real      # no private repos, no test beds
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_projects as cp  # noqa: E402
import levers  # noqa: E402


@dataclass
class Run:
    adw_id: str
    kind: str
    start: datetime
    status: str
    cost: float
    commits: list[tuple[str, str]] = field(default_factory=list)   # (sha, message)


@dataclass
class Landing:
    sha: str
    when: datetime
    subject: str


@dataclass
class Feature:
    landing: Landing
    runs: list[Run] = field(default_factory=list)
    matched_by: set[str] = field(default_factory=set)

    @property
    def cost(self) -> float:
        return sum(r.cost for r in self.runs)

    @property
    def lead_hours(self) -> float:
        return (self.landing.when - min(r.start for r in self.runs)).total_seconds() / 3600


def agent_runs(db: Path, since: datetime) -> list[Run]:
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        rows = conn.execute("select s.adw_id, s.adw_name, s.started_at, s.status, s.total_cost "
                            "from sessions s where exists (select 1 from phases p where "
                            "p.adw_id = s.adw_id and p.kind = 'agent')").fetchall()
        logged = conn.execute("select adw_id, payload_json from events "
                              "where payload_json like '%\"sha\"%' order by started_at").fetchall()
    commits: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for adw_id, payload in logged:
        try:
            data = json.loads(payload)
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("sha"), str) and data["sha"]:
            commits[adw_id].append((data["sha"], str(data.get("message") or "")))
    out = []
    for adw_id, kind, started, status, cost in rows:
        start = levers._when(started)
        if start and start >= since:
            out.append(Run(adw_id, kind or "?", start, status or "", cost or 0.0, commits[adw_id]))
    return sorted(out, key=lambda r: r.start)


def git(root: Path, *args: str, stdin: str | None = None) -> str:
    done = subprocess.run(["git", "-C", str(root), *args], input=stdin,
                          capture_output=True, text=True)
    return done.stdout if done.returncode == 0 else ""


def main_line(root: Path, branch: str, since: datetime) -> tuple[dict[str, Landing], dict[str, Landing]]:
    """full sha -> its landing, and subject -> landing, for what landed since."""
    by_sha: dict[str, Landing] = {}
    by_subject: dict[str, Landing] = {}
    out = git(root, "log", "--first-parent", f"--since={since.isoformat()}",
              "--format=%H%x09%P%x09%cI%x09%s", branch)
    for line in out.splitlines():
        sha, parents, when, subject = (line.split("\t", 3) + ["", "", "", ""])[:4]
        landing = Landing(sha, levers._when(when), subject)
        parent = parents.split()
        introduced = (git(root, "rev-list", f"{parent[0]}..{sha}").split()
                      if len(parent) > 1 else [sha])
        for commit in introduced:
            by_sha.setdefault(commit, landing)
    if by_sha:
        subjects = git(root, "log", "--no-walk=unsorted", "--format=%H%x09%s", *by_sha)
        for line in subjects.splitlines():
            sha, _, subject = line.partition("\t")
            by_subject.setdefault(subject, by_sha[sha])
    return by_sha, by_subject


def patch_ids(root: Path, shas: list[str]) -> dict[str, str]:
    """commit -> its stable patch-id; a commit with no diff has none."""
    if not shas:
        return {}
    patches = git(root, "log", "--no-walk=unsorted", "-p", "--format=commit %H", *shas)
    out = git(root, "patch-id", "--stable", stdin=patches)
    return {commit: pid for pid, commit in (line.split() for line in out.splitlines())}


def resolve(root: Path, sha: str) -> str:
    return git(root, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}").strip()


def on_a_branch(root: Path, full: str) -> bool:
    return bool(git(root, "for-each-ref", "--contains", full, "--format=%(refname)",
                    "refs/heads", "refs/remotes").strip())


def join(root: Path, branch: str, runs: list[Run], since: datetime):
    by_sha, by_subject = main_line(root, branch, since)
    landed_pids = {pid: by_sha[c] for c, pid in patch_ids(root, list(by_sha)).items()}
    features: dict[str, Feature] = {}
    pending: list[Run] = []
    gone: list[Run] = []
    none: list[Run] = []
    for run in runs:
        if not run.commits:
            none.append(run)
            continue
        hits: dict[str, tuple[Landing, str]] = {}
        alive = False
        for sha, message in run.commits:
            full = resolve(root, sha)
            landing, how = None, ""
            if full and full in by_sha:
                landing, how = by_sha[full], "sha"
            if landing is None and full:
                pid = patch_ids(root, [full]).get(full)
                if pid and pid in landed_pids:
                    landing, how = landed_pids[pid], "patch-id"
            subject = message.splitlines()[0] if message else ""
            if landing is None and subject in by_subject:
                landing, how = by_subject[subject], "subject"
            if landing is not None:
                hits[landing.sha] = (landing, how)
            elif full and on_a_branch(root, full):
                alive = True
        if hits:
            # The run's work is in once its last commit is: after a fast-forward
            # each commit lands alone, and the spec commit lands first.
            landing, _ = max(hits.values(), key=lambda h: h[0].when)
            feature = features.setdefault(landing.sha, Feature(landing))
            feature.runs.append(run)
            feature.matched_by.update(how for _, how in hits.values())
        elif alive:
            pending.append(run)
        else:
            gone.append(run)
    return sorted(features.values(), key=lambda f: f.landing.when), pending, gone, none


def bucket(label: str, runs: list[Run]) -> str:
    return f"  {label:11} {len(runs):3} run(s)  ${sum(r.cost for r in runs):8.2f}"


def report(name: str, branch: str, features: list[Feature], pending, gone, none) -> list[str]:
    out = [f"{name} (main line: {branch})"]
    if features:
        out.append(f"  {'landed':16} {'sha':7} {'runs':>4} {'ok':>3} {'cost':>8} {'lead h':>7}  feature")
        for f in features:
            ok = sum(r.status == "success" for r in f.runs)
            how = "" if f.matched_by == {"sha"} else f"  [{', '.join(sorted(f.matched_by - {'sha'}))}]"
            out.append(f"  {f.landing.when.astimezone(timezone.utc):%Y-%m-%d %H:%M} "
                       f"{f.landing.sha[:7]} {len(f.runs):4} {ok:3} {f.cost:8.2f} "
                       f"{f.lead_hours:7.1f}  {f.landing.subject[:60]}{how}")
    landed_cost = sum(f.cost for f in features)
    out.append(f"  {'landed':11} {len(features):3} feature(s) from "
               f"{sum(len(f.runs) for f in features)} run(s)  ${landed_cost:8.2f}"
               + (f", ${landed_cost / len(features):.2f} per feature" if features else ""))
    out += [bucket("pending", pending), bucket("gone", gone), bucket("no commits", none)]
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--weeks", type=int, default=2, help="how many weeks back (default 2)")
    parser.add_argument("--repo", action="append", help="only this repo; repeatable")
    parser.add_argument("--branch", action="append", default=[], metavar="REPO=BRANCH",
                        help="a repo's main line, if not its current branch; repeatable")
    parser.add_argument("--listed", action="store_true",
                        help="leave out the repos in the hub's .git/sssf-private-names")
    parser.add_argument("--real", action="store_true",
                        help="leave out the repos in the hub's .git/sssf-test-beds")
    parser.add_argument("--hub", type=Path, default=cp.HUB, help=argparse.SUPPRESS)
    parser.add_argument("--registry", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--now", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    entries = json.loads((args.registry or cp.registry_path()).read_text()).get("projects", {})
    hidden = beds = 0
    if args.listed:
        names = cp.private_names(args.hub)
        if names is None:
            print("--listed needs the hub's .git/sssf-private-names, and it doesn't exist; "
                  "refusing rather than print every repo", file=sys.stderr)
            return 1
        kept = {n: e for n, e in entries.items() if not cp.unlisted(n, e, names)}
        hidden, entries = len(entries) - len(kept), kept
    if args.real:
        names = levers.test_beds(args.hub)
        kept = {n: e for n, e in entries.items() if not cp.unlisted(n, e, names)}
        beds, entries = len(entries) - len(kept), kept
    if args.repo:
        unknown = set(args.repo) - set(entries)
        if unknown:
            print(f"not in the registry, or left out: {', '.join(sorted(unknown))}", file=sys.stderr)
            return 1
        entries = {n: e for n, e in entries.items() if n in args.repo}
    branches = dict(b.split("=", 1) for b in args.branch if "=" in b)

    now = levers._when(args.now) if args.now else datetime.now(timezone.utc)
    since = now - timedelta(weeks=args.weeks)
    blocks = []
    for name, entry in sorted(entries.items()):
        root = Path(entry["root"])
        db = root / entry.get("db", "adws/adw_data/sssf.db")
        if not db.is_file() or not (root / ".git").exists():
            continue
        runs = agent_runs(db, since)
        if not runs:
            continue
        branch = branches.get(name) or git(root, "branch", "--show-current").strip() or "HEAD"
        blocks.append("\n".join(report(name, branch, *join(root, branch, runs, since))))
    print(f"agent runs since {since:%Y-%m-%d}, {len(blocks)} repo(s) with any\n")
    print("\n\n".join(blocks))
    if beds:
        print(f"\n{beds} test bed(s) left out")
    if hidden:
        print(f"\n{hidden} unlisted repo(s) left out")
    return 0


if __name__ == "__main__":
    sys.exit(main())
