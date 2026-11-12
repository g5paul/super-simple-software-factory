#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""Export a feature for upstream: its product changes, on a branch based on upstream, no SSSF files.

Usage:
    uv run adws/adw_export.py <branch> [--base main]   # a branch's commits since it left main
    uv run adws/adw_export.py --run <adw_id>           # the commits a run's trace records
    uv run adws/adw_export.py --feature <slug>         # the commits with SSSF-Feature: <slug>
    options: [--onto upstream/main] [--replace]

It replays each commit's product changes onto --onto, drops what's left empty,
skips merges that only join history, and strips SSSF-* trailers from messages.
Then it runs repo.yaml's export_checks in the export's own tree.

It refuses when the tree has uncommitted changes to tracked files, or when
export/<name> exists and --replace isn't given.

It builds export/<name> and changes nothing else: no push, no fetch, and the
feature's own branch is untouched. Design: docs/designs/upstream-patches.md.
"""

import argparse
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from adw_modules import agents, git_helper, repo_settings
from adw_modules.data_types import SSSFConfig

# SSSF files, by decision 7: whole folders, two files, and the records runs
# name after their adw_id. A project's own specs/ or app_docs/ files stay.
SSSF_FOLDERS = ("adws/", ".claude/skills/sssf/", ".scratch/", ".githooks/")
SSSF_FILES = ("sssf.just", "docs/sssf.md")
RECORD = re.compile(r"(specs|app_docs)/[0-9a-f]{8}_[^/]+\.md")
# SSSF's commit trailers (SSSF-Feature, SSSF-Step), which mean nothing upstream.
SSSF_TRAILER = re.compile(r"SSSF-[A-Za-z-]+: ")
# The backstop, decision 9: an added line that still names SSSF, such as a linter
# exclude in a shared config file, which no path filter can drop.
NAMES_SSSF = re.compile(r"adws/|\.claude/skills/sssf|(specs|app_docs)/[0-9a-f]{8}_|sssf\.just")


class ExportError(Exception):
    pass


def is_sssf_file(path: str) -> bool:
    return path.startswith(SSSF_FOLDERS) or path in SSSF_FILES or RECORD.fullmatch(path) is not None


def _git(*args: str, cwd: Path | None = None, input: bytes | None = None) -> str:
    done = subprocess.run(["git", "-c", "core.fsmonitor=", *args], cwd=cwd, input=input,
                          capture_output=True)
    if done.returncode != 0:
        raise ExportError(f"git {' '.join(args)}: {done.stderr.decode(errors='replace').strip()}")
    return done.stdout.decode(errors="surrogateescape")


def _changed(sha: str) -> list[str]:
    out = _git("diff-tree", "--no-commit-id", "--name-only", "-r", "--no-renames", "-z", f"{sha}^", sha)
    return [p for p in out.split("\0") if p]


def _replay(sha: str, work: Path) -> bool:
    """Apply one commit's product changes in the export worktree and commit them
    with the commit's own author and message. False when nothing is left."""
    product = [p for p in _changed(sha) if not is_sssf_file(p)]
    if not product:
        return False
    patch = subprocess.run(["git", "-c", "core.fsmonitor=", "diff-tree", "-p", "--binary", "--no-renames", f"{sha}^", sha,
                            "--", *product], capture_output=True, check=True).stdout
    try:
        _git("apply", "--3way", "--index", cwd=work, input=patch)
    except ExportError as error:
        raise ExportError(f"{_subject(sha)} won't apply onto the base; it may lean on a "
                          f"fork commit upstream lacks.\n{error}") from None
    _git("commit", "-q", "-C", sha, cwd=work)
    message = _git("log", "-1", "--format=%B", sha)
    clean = "\n".join(l for l in message.splitlines() if not SSSF_TRAILER.match(l)).rstrip() + "\n"
    if clean != message.rstrip() + "\n":
        _git("commit", "-q", "--amend", "--cleanup=whitespace", "-F", "-", cwd=work, input=clean.encode())
    return True


CHECK_TAIL = 20


def _checks(work: Path) -> tuple[list[str], bool]:
    """Run repo.yaml's export_checks in the export's worktree, in order, each one."""
    checks = repo_settings.load(Path(_git("rev-parse", "--show-toplevel").strip())).export_checks
    if not checks:
        return ["  unchecked: repo.yaml lists no export_checks"], True
    lines, ok = [], True
    for command in checks:
        done = subprocess.run(["bash", "-c", command], cwd=work, capture_output=True, text=True)
        if done.returncode == 0:
            lines.append(f"  passed: {command}")
            continue
        ok = False
        lines.append(f"  failed (exit {done.returncode}): {command}")
        tail = (done.stdout + done.stderr).strip().splitlines()[-CHECK_TAIL:]
        lines += [f"    {t}" for t in tail]
    return lines, ok


def _backstop(onto: str, work: Path) -> None:
    """Refuse when a line the export adds still names an SSSF path."""
    found, path, number, in_hunk = [], "", 0, False
    for line in _git("diff", "--no-renames", "--no-color", "-U0", onto, "HEAD", cwd=work).splitlines():
        if line.startswith("diff --git "):
            in_hunk = False
        elif not in_hunk and line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else line[4:]
        elif line.startswith("@@"):
            in_hunk, number = True, int(re.search(r"\+(\d+)", line).group(1))
        elif in_hunk and line.startswith("+"):
            if NAMES_SSSF.search(line):
                found.append(f"  {path}:{number}: {line[1:].strip()}")
            number += 1
    if found:
        raise ExportError("the export still names SSSF paths; fix these lines on the "
                          "feature branch and export again:\n" + "\n".join(found))


def run_commits(adw_id: str, config: str) -> list[str]:
    """The commits a run made, oldest first, by the shas its commit phases traced.
    A sha that no longer resolves (a discarded worktree run) stops the export."""
    cfg = agents.load_config(config) if Path(config).is_file() else SSSFConfig()
    db = Path(cfg.observability.db)
    if not db.is_file():
        raise ExportError(f"no trace at {db}, so no record of run {adw_id}")
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        rows = conn.execute("SELECT payload_json FROM events WHERE adw_id = ? AND type = 'log' "
                            "ORDER BY started_at, event_id", (adw_id,)).fetchall()
    shas = []
    for (payload,) in rows:
        sha = (json.loads(payload or "{}") or {}).get("sha")
        if not sha:
            continue
        try:
            full = _git("rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}").strip()
        except ExportError:
            raise ExportError(f"run {adw_id} committed {sha}, which isn't in this repo any more "
                              "(was the run discarded?)") from None
        # A rebased or discarded run's commits outlive it as objects, on no branch.
        if not _git("for-each-ref", "--contains", full, "--format=%(refname)", "refs/heads/").strip():
            raise ExportError(f"run {adw_id} committed {sha}, which is on no branch now: the run "
                              "was rebased or discarded. Export the branch its work is on instead.")
        shas.append(full)
    if not shas:
        return []
    # In history's order, not the trace's: two commits can share a timestamp.
    wanted = set(shas)
    return [s for s in _git("rev-list", "--reverse", "--topo-order", *wanted).split() if s in wanted]


def _is_merge(sha: str) -> bool:
    """True for a merge that only joins history. A merge that carries changes of
    its own, such as a conflict resolution, can't be replayed: it stops the export."""
    if len(_git("rev-list", "--parents", "-n", "1", sha).split()) < 3:
        return False
    if _git("diff-tree", "--cc", "--no-commit-id", "-p", sha).strip():
        raise ExportError(f"{_subject(sha)} is a merge with changes of its own, which the "
                          "export can't replay. Move those changes into a commit of their own.")
    return True


def _base_age(onto: str) -> str:
    """The base as it stands here, with how to refresh it. The export never fetches."""
    date = _git("log", "-1", "--format=%cs", onto).strip()
    remote = onto.split("/", 1)[0] if "/" in onto else ""
    refresh = (f"`git fetch {remote}`" if remote in _git("remote").split()
               else f"update {onto}")
    return f"{onto} as of {date} (to export onto a newer one, {refresh} first)"


def _subject(sha: str) -> str:
    return _git("log", "-1", "--format=%h %s", sha).strip()


def export(name: str, shas: list[str], onto: str, replace: bool = False) -> tuple[list[str], bool]:
    """Build export/<name>. Returns the report's lines, and whether every check passed."""
    branch = f"export/{name}"
    if _git("status", "--porcelain", "--untracked-files=no").strip():
        raise ExportError("the tree is dirty: commit or stash its changes first")
    if _git("branch", "--list", branch).strip() and not replace:
        raise ExportError(f"{branch} already exists: pass --replace to build it again")
    kept, dropped, merges = [], [], []
    with tempfile.TemporaryDirectory(prefix="sssf-export-") as tmp:
        work = Path(tmp) / "work"
        _git("worktree", "add", "-q", "--detach", str(work), onto)
        try:
            for sha in shas:
                if _is_merge(sha):
                    merges.append(_subject(sha))
                else:
                    (kept if _replay(sha, work) else dropped).append(_subject(sha))
            if not kept:
                raise ExportError("every commit holds only SSSF files, so there's nothing to export")
            _backstop(onto, work)
            tip = _git("rev-parse", "HEAD", cwd=work).strip()
            checked, passed = _checks(work)
        finally:
            _git("worktree", "remove", "--force", str(work))
    _git("branch", "--force", branch, tip)
    lines = [f"built {branch} on {_base_age(onto)}", "commits:"] + [f"  {c}" for c in kept]
    lines += ["files:"] + [f"  {f}" for f in _git("diff", "--stat", onto, branch).splitlines()]
    if dropped:
        lines += ["dropped, SSSF files only:"] + [f"  {c}" for c in dropped]
    if merges:
        lines += ["merges skipped, they only join history:"] + [f"  {c}" for c in merges]
    return lines + ["checks:"] + checked, passed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("branch", nargs="?", help="a feature branch: its commits since it left --base")
    which.add_argument("--feature", help="the commits on HEAD carrying `SSSF-Feature: <slug>`")
    which.add_argument("--run", help="the commits a run made, by the shas its trace records")
    parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
    parser.add_argument("--base", default="main")
    parser.add_argument("--onto", default="upstream/main")
    parser.add_argument("--replace", action="store_true", help="build export/<name> again")
    args = parser.parse_args(argv)
    try:
        if args.run:
            name, shas = args.run, run_commits(args.run, args.config)
        elif args.feature:
            name, shas = args.feature, [sha for sha, _ in git_helper.feature_commits(args.feature)]
        else:
            name, shas = args.branch, _git("rev-list", "--reverse", f"{args.base}..{args.branch}").split()
        if not shas:
            raise ExportError(f"no commits found for {name}")
        report, passed = export(name, shas, args.onto, args.replace)
    except ExportError as error:
        print(f"export: {error}", file=sys.stderr)
        return 1
    print("\n".join(report))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
