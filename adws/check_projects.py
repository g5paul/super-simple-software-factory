#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Check each installed repo's SSSF update against the hub, read-only.

After a skill change the hub asks every project session to update, and each
reports its skill commit, its commit and its quality run. This checks those
claims against the repos themselves, so the hub session no longer checks them
by hand. It reads git and each repo's trace; it never writes to a project.

For each repo in the install registry, except the hub, it checks:
  stamp     adws/adw_sssf_config/stamp.json names the expected skill commit
  commit    the last commit to touch stamp.json is "Update SSSF to skill <h>"
  clean     `git status --porcelain` is empty
  skill     the committed .claude/skills/sssf matches the hub's at that commit:
            no file missing, changed, or left over, such as a stray file an
            update should have removed
  quality   an adw_quality run started after the update commit and succeeded

Usage, from the hub root:
    uv run adws/check_projects.py                 # expect the hub's current skill
    uv run adws/check_projects.py --skill 33a1ed2
    uv run adws/check_projects.py --repo fusion-harness
    uv run adws/check_projects.py --listed        # output safe to paste anywhere

--listed leaves out every repo the hub's .git/sssf-private-names lists, and
says only how many it left out. That file is the one the pre-push hook reads,
one name per line, case ignored; a repo is unlisted when its registry name or
path contains one. Without the file, --listed refuses rather than print
everything.

Exit 0 when every check passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SKILL_DIR = ".claude/skills/sssf"
STAMP = "adws/adw_sssf_config/stamp.json"
HUB = Path(__file__).resolve().parents[1]


@dataclass
class Check:
    name: str
    ok: bool
    note: str


@dataclass
class Report:
    repo: str
    branch: str = ""
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def add(self, name: str, ok: bool, note: str) -> None:
        self.checks.append(Check(name, ok, note))


def git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError(f"git {' '.join(args)} in {root}: {done.stderr.strip()}")
    return done.stdout


def private_names(hub: Path) -> list[str] | None:
    """The names in the hub's .git/sssf-private-names, as the pre-push hook
    reads them; None when the file doesn't exist."""
    path = Path(git(hub, "rev-parse", "--path-format=absolute", "--git-common-dir").strip())
    path = path / "sssf-private-names"
    if not path.is_file():
        return None
    return [line.strip().lower() for line in path.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")]


def unlisted(name: str, entry: dict, names: list[str]) -> bool:
    text = f"{name}\n{entry.get('root', '')}".lower()
    return any(n in text for n in names)


def registry_path() -> Path:
    if os.environ.get("SSSF_REGISTRY"):
        return Path(os.environ["SSSF_REGISTRY"])
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "sssf" / "projects.json"


def projects(registry: Path, hub: Path) -> dict[str, dict]:
    entries = json.loads(registry.read_text()).get("projects", {})
    return {name: entry for name, entry in sorted(entries.items())
            if Path(entry["root"]).resolve() != hub.resolve()}


def skill_tree(root: Path, rev: str | None) -> dict[str, str]:
    """path -> blob sha of every file under the skill, at rev, or in the index."""
    if rev:
        out = git(root, "ls-tree", "-r", rev, "--", SKILL_DIR)
        rows = (line.split(None, 3) for line in out.splitlines())
        return {path: sha for _mode, _kind, sha, path in rows}
    out = git(root, "ls-files", "-s", "--", SKILL_DIR)
    rows = (line.split(None, 3) for line in out.splitlines())
    return {path: sha for _mode, sha, _stage, path in rows}


def _same(a: str, b: str) -> bool:
    n = min(len(a), len(b))
    return n >= 7 and a[:n] == b[:n]


def _when(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def check(name: str, entry: dict, hub: Path, skill: str) -> Report:
    root = Path(entry["root"])
    report = Report(name)
    if not (root / ".git").exists():
        report.add("repo", False, f"{root} is not a git repo")
        return report
    report.branch = git(root, "branch", "--show-current").strip() or "(detached)"

    stamp_file = root / STAMP
    stamped = json.loads(stamp_file.read_text()).get("skill", "") if stamp_file.is_file() else ""
    report.add("stamp", _same(stamped, skill), f"skill {stamped or 'none'}")

    line = git(root, "log", "-1", "--format=%h%x09%cI%x09%s", "--", STAMP).strip()
    sha, committed, subject = (line.split("\t", 2) + ["", "", ""])[:3]
    report.add("commit", subject.startswith(f"Update SSSF to skill {skill[:7]}"),
               f"{sha} {subject}" if sha else "no commit touches stamp.json")

    dirty = [l for l in git(root, "status", "--porcelain").splitlines() if l.strip()]
    report.add("clean", not dirty, "clean" if not dirty else f"{len(dirty)} changed: {dirty[0][3:]}"
               + (" ..." if len(dirty) > 1 else ""))

    want, have = skill_tree(hub, skill), skill_tree(root, None)
    extra = sorted(set(have) - set(want))
    missing = sorted(set(want) - set(have))
    changed = sorted(p for p in set(want) & set(have) if want[p] != have[p])
    problems = ([f"extra {p}" for p in extra] + [f"missing {p}" for p in missing]
                + [f"changed {p}" for p in changed])
    report.add("skill", not problems,
               f"{len(have)} files match" if not problems else "; ".join(problems[:3])
               + (f" and {len(problems) - 3} more" if len(problems) > 3 else ""))

    db = root / entry.get("db", "adws/adw_data/sssf.db")
    if not db.is_file() or not committed:
        report.add("quality", False, "no trace" if not db.is_file() else "no update commit")
        return report
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        rows = conn.execute("select adw_id, status, started_at from sessions "
                            "where adw_name like '%adw_quality%' order by started_at").fetchall()
    after = [r for r in rows if r[2] and _when(r[2]) >= _when(committed)]
    if not after:
        report.add("quality", False, f"no adw_quality run since {sha}")
    else:
        adw_id, status, _ = after[-1]
        report.add("quality", status == "success", f"{adw_id} {status}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--skill", help="the skill commit to expect (default: the hub's current one)")
    parser.add_argument("--repo", action="append", help="check only this repo; repeatable")
    parser.add_argument("--listed", action="store_true",
                        help="leave out the repos in the hub's .git/sssf-private-names")
    parser.add_argument("--hub", type=Path, default=HUB, help=argparse.SUPPRESS)
    parser.add_argument("--registry", type=Path, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    skill = args.skill or git(args.hub, "log", "-1", "--format=%h", "--", SKILL_DIR).strip()
    skill = git(args.hub, "rev-parse", "--short", f"{skill}^{{commit}}").strip()
    repos = projects(args.registry or registry_path(), args.hub)
    hidden = 0
    if args.listed:
        names = private_names(args.hub)
        if names is None:
            print("--listed needs the hub's .git/sssf-private-names, and it doesn't exist; "
                  "refusing rather than print every repo", file=sys.stderr)
            return 1
        kept = {n: e for n, e in repos.items() if not unlisted(n, e, names)}
        hidden, repos = len(repos) - len(kept), kept
    if args.repo:
        unknown = set(args.repo) - set(repos)
        if unknown:
            print(f"not in the registry: {', '.join(sorted(unknown))}", file=sys.stderr)
            return 1
        repos = {n: e for n, e in repos.items() if n in args.repo}

    print(f"expecting skill {skill}\n")
    reports = [check(name, entry, args.hub, skill) for name, entry in repos.items()]
    for report in reports:
        print(f"{'ok  ' if report.ok else 'FAIL'} {report.repo} ({report.branch})")
        for c in report.checks:
            print(f"     {'ok  ' if c.ok else 'FAIL'} {c.name:8} {c.note}")
    bad = [r.repo for r in reports if not r.ok]
    print(f"\n{len(reports) - len(bad)} of {len(reports)} repos pass"
          + (f"; failing: {', '.join(bad)}" if bad else ""))
    if hidden:
        print(f"{hidden} unlisted repo(s) left out")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
