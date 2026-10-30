#!/usr/bin/env -S uv run
# /// script
# dependencies = []
# ///
"""/install — stamp the SSSF factory from the skill into the cwd. Idempotent.

Usage:
    uv run <skill>/scripts/install.py [--force] [--coding-agent pi|claude_code] [--chatgpt] [--container]
    uv run <skill>/scripts/install.py --update [--take PATH ...] [--keep PATH ...]

Stamps: adws/ (modules + starter ADWs), adws/adw_data/prompt_engineering/
(the starter agents), adws/adw_sssf_config/sssf.config.yaml, .env.sample,
.gitignore entries. --container adds container/, to run the operator
session in a Podman container (Claude Code rosters only); run it again with
--container in an installed repo to add it later.
Existing files are skipped unless --force.

Every stamped file's hash goes into adws/adw_sssf_config/stamp.json.
--update uses it to bring the repo up to this skill's version: a file that
still matches its record is replaced, a file the repo changed is kept and
listed, a missing file is added. --take PATH replaces a kept file anyway;
--keep PATH records that you reviewed it, so it stays quiet until the
skill's version of it changes again.

Install and --update also record the repo in the user's project registry
(~/.config/sssf/projects.json, or $SSSF_REGISTRY), which `just obs-all`
reads to show every repo in one dashboard.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"

# One starter roster per harness. Pi stays the default; claude_code stamps a
# roster that runs on the `claude` CLI and the subscription login.
ROSTERS = {
    "pi": TEMPLATES / "sssf.config.yaml",
    "claude_code": TEMPLATES / "sssf.config.claude_code.yaml",
}
# --chatgpt: still Pi, with every model on the ChatGPT subscription sign-in.
CHATGPT_ROSTER = TEMPLATES / "sssf.config.chatgpt.yaml"

GITIGNORE_ENTRIES = [
    "adws/adw_data/sessions/",
    "adws/adw_data/sssf.db*",
    ".env",
    # The ADWs are Python, so importing adw_modules writes bytecode next to it.
    # Chains that end in a commit phase call `git add -A`, so without this a
    # stamped repo commits its own .pyc files — 15 of them showed up in the
    # first repo that was ever installed into from scratch.
    "__pycache__/",
    "*.pyc",
    # `just obs` runs `bun install` inside the skill. The same `git add -A`
    # would then commit the visualizer's packages with the next phase.
    ".claude/skills/sssf/apps/visualizer/node_modules/",
    # `just obs` also builds the UI into dist/ there, for the same reason.
    ".claude/skills/sssf/apps/visualizer/dist/",
]


STAMP = Path("adws/adw_sssf_config/stamp.json")
DB = "adws/adw_data/sssf.db"


def targets(root: Path, roster: Path, container: bool = False) -> list[tuple[Path, Path]]:
    """Every (template, destination) pair the factory is made of."""
    pairs: list[tuple[Path, Path]] = []

    def add(src: Path, dest: Path) -> None:
        if src.is_dir():
            for child in sorted(src.iterdir()):
                if child.name != "__pycache__":
                    add(child, dest / child.name)
        else:
            pairs.append((src, dest))

    add(TEMPLATES / "adws", root / "adws")
    add(TEMPLATES / "prompt_engineering", root / "adws" / "adw_data" / "prompt_engineering")
    add(TEMPLATES / "harness_engineering", root / "adws" / "adw_data" / "harness_engineering")
    add(roster, root / "adws" / "adw_sssf_config" / "sssf.config.yaml")
    add(TEMPLATES / "env.sample", root / ".env.sample")
    # The recipes are part of the operating experience, and several cookbooks
    # plus the run banner tell you to use them, so a stamped repo has to have
    # them. A repo with its own justfile loads them as a module, and then they
    # are stamped into sssf.just. Otherwise they are the justfile, skipped like
    # any other file if the repo already has one.
    add(TEMPLATES / "justfile", root / (MODULE if loads_module(root) else "justfile"))
    if container:
        add(TEMPLATES / "container", root / "container")
    return pairs


def has_container(record: dict) -> bool:
    """A repo gets container/ only by asking for it, and keeps it current from then on."""
    return any(rel.startswith("container/") for rel in record["files"])


MODULE = "sssf.just"
MOD_LINE = re.compile(r"^\s*mod\s+sssf(\s+['\"]sssf\.just['\"])?\s*$", re.MULTILINE)


def loads_module(root: Path) -> bool:
    """True when the repo's own justfile loads SSSF's recipes with `mod sssf`."""
    justfile = root / "justfile"
    return justfile.is_file() and MOD_LINE.search(justfile.read_text()) is not None


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_sssf_code(rel: str) -> bool:
    """Code a repo is not meant to edit. quality.py is the exception: it holds
    the repo's own test, lint, and typecheck commands."""
    if rel == "adws/adw_modules/quality.py":
        return False
    if rel == MODULE:
        return True        # SSSF's recipes; the repo's own go in its justfile
    parts = rel.split("/")
    return (rel.startswith("adws/adw_modules/")
            or (len(parts) == 2 and parts[0] == "adws" and parts[1].startswith("adw_")
                and parts[1].endswith(".py")))


def load_stamp(root: Path) -> dict:
    path = root / STAMP
    return json.loads(path.read_text()) if path.is_file() else {"files": {}}


def skill_version(root: Path) -> str | None:
    """The skill commit this install or update came from, "+dirty" when its
    tree has changes. Without it, a docs-only update leaves stamp.json as it
    was, and only a commit message says which skill the repo is on.

    The source is SSSF_SOURCE when set, else the skill this script lives in.
    A copy inside the repo being stamped is not a source: its git history is
    the repo's, not the skill's.
    """
    source = Path(os.environ.get("SSSF_SOURCE") or TEMPLATES.parent).expanduser().resolve()
    if source == root.resolve() or root.resolve() in source.parents:
        return None

    def git(*args: str) -> str:
        result = subprocess.run(["git", "-C", str(source), *args],
                                capture_output=True, text=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else ""

    sha = git("rev-parse", "--short", "HEAD")
    if not sha:
        return None
    return sha + ("+dirty" if git("status", "--porcelain", "--", ".") else "")


def skill_change(root: Path, src: Path, base_sha256: str) -> str | None:
    """A git command that shows the skill's own change to `src`: from the
    template a repo was stamped with, to the template now.

    The printed `compare` diff is the repo's file against the new template,
    which mixes that change with every edit the repo made. To merge the
    change by hand, this is the diff to read. It looks back through the
    history of whichever git repo holds the template (the skill source, or
    the repo's own copy of the skill) for the version the stamp recorded.
    """
    def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, check=False)

    top = git(src.parent, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        return None
    top_dir = Path(top.stdout.decode().strip()).resolve()
    rel = src.resolve().relative_to(top_dir).as_posix()
    commits = git(top_dir, "log", "--format=%h", "-n", "200", "--", rel).stdout.decode().split()
    for commit in commits:
        blob = git(top_dir, "show", f"{commit}:{rel}")
        if blob.returncode == 0 and hashlib.sha256(blob.stdout).hexdigest() == base_sha256:
            where = "" if top_dir == root.resolve() else f"-C {top_dir} "
            return f"git {where}diff {commit} -- {rel}"
    return None


def save_stamp(root: Path, record: dict) -> None:
    path = root / STAMP
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")


def copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def ensure_gitignore(root: Path, stamped: list) -> None:
    gitignore = root / ".gitignore"
    existing = gitignore.read_text().splitlines() if gitignore.exists() else []
    missing = [e for e in GITIGNORE_ENTRIES if e not in existing]
    if missing:
        with gitignore.open("a") as f:
            f.write("\n# sssf runtime\n" + "\n".join(missing) + "\n")
        stamped.append(f"{gitignore} (+{len(missing)} entries)")


def registry_path() -> Path:
    if os.environ.get("SSSF_REGISTRY"):
        return Path(os.environ["SSSF_REGISTRY"])
    config = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(config) / "sssf" / "projects.json"


def register(root: Path) -> None:
    """Record the repo for `just obs-all`. A repo keeps its name once it has one;
    a second repo with the same folder name gets its parent folder's name too."""
    path = registry_path()
    try:
        data = json.loads(path.read_text()) if path.is_file() else {}
        projects = data.setdefault("projects", {})
        listed = [n for n, entry in projects.items() if entry.get("root") == str(root)]
        if listed:
            print(f"  dashboard: listed as {listed[0]} in {path}")
            return
        name, n = root.name, 1
        if name in projects:
            name = f"{root.parent.name}-{root.name}"
        while name in projects:
            n += 1
            name = f"{root.parent.name}-{root.name}-{n}"
        projects[name] = {"root": str(root), "db": DB}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        print(f"  dashboard: registered as {name} in {path}")
    except (OSError, ValueError) as err:
        # The dashboard is a convenience; an install must not fail over it.
        print(f"  could not record this repo in {path}: {err}")


def install(root: Path, roster: Path, force: bool, container: bool = False) -> int:
    record = load_stamp(root)
    stamped, skipped = [], []
    for src, dest in targets(root, roster, container or has_container(record)):
        rel = dest.relative_to(root).as_posix()
        entry = {"source": src.relative_to(TEMPLATES).as_posix(), "sha256": sha256(src)}
        if dest.exists() and not force:
            skipped.append(str(dest))
            if sha256(dest) == entry["sha256"]:
                record["files"][rel] = entry
            elif rel not in record["files"] and not rel.startswith("adws/"):
                # The repo's own justfile or .env.sample, there before SSSF.
                # Record it as kept, as `--update --keep` would, so the first
                # update does not flag a file SSSF never wrote. Files under
                # adws/ stay unrecorded: a leftover from an older install is
                # SSSF's, and an update still needs to replace it.
                record["files"][rel] = entry
            continue
        copy(src, dest)
        record["files"][rel] = entry
        stamped.append(str(dest))
    version = skill_version(root)
    if version:
        record["skill"] = version
    save_stamp(root, record)
    ensure_gitignore(root, stamped)

    print(f"sssf installed into {root}")
    register(root)
    print(f"  skill: {version or 'unknown (no git source)'}")
    print(f"  stamped: {len(stamped)} file(s)")
    for s in stamped:
        print(f"    + {s}")
    if skipped:
        print(f"  skipped (already exist, use --force to overwrite): {len(skipped)}")
    return 0


def update(root: Path, default_roster: Path, take: set[str], keep: set[str]) -> int:
    if not (root / "adws" / "adw_modules").is_dir():
        print(f"no SSSF install in {root}: run install.py first", file=sys.stderr)
        return 1
    record = load_stamp(root)
    files = record["files"]
    config = "adws/adw_sssf_config/sssf.config.yaml"
    config_text = (root / config).read_text() if (root / config).is_file() else ""
    if config in files:
        roster = TEMPLATES / files[config]["source"]
    elif "coding_agent: claude_code" in config_text:
        roster = ROSTERS["claude_code"]     # stamped before stamp.json recorded the roster
    else:
        roster = default_roster

    pairs = targets(root, roster, has_container(record))
    if loads_module(root) and files.get("justfile", {}).get("source") == "justfile":
        # The repo's own justfile, recorded as kept before sssf.just was
        # tracked. SSSF's recipes are in sssf.just now, so it has no claim on it.
        del files["justfile"]
    unknown = (take | keep) - {dest.relative_to(root).as_posix() for _, dest in pairs}
    if unknown:
        print(f"not a stamped file: {', '.join(sorted(unknown))}", file=sys.stderr)
        return 1
    if take & keep:
        print(f"both --take and --keep: {', '.join(sorted(take & keep))}", file=sys.stderr)
        return 1

    added, updated, review, reviewed, customized, current = [], [], [], [], [], 0
    for src, dest in pairs:
        rel = dest.relative_to(root).as_posix()
        entry = {"source": src.relative_to(TEMPLATES).as_posix(), "sha256": sha256(src)}
        base = files.get(rel)
        if not dest.exists():
            copy(src, dest)
            added.append(rel)
        elif sha256(dest) == entry["sha256"]:
            current += 1
        elif rel in take or (base and base["sha256"] == sha256(dest)):
            # Unchanged since the last stamp, or the user asked for it.
            copy(src, dest)
            updated.append(rel)
        elif base is None and is_sssf_code(rel):
            copy(src, dest)
            updated.append(f"{rel}  (SSSF code, no stamp record yet)")
        elif rel in keep:
            # Reviewed: the repo's version stays, measured against this template.
            reviewed.append(rel)
        elif base and base["sha256"] == entry["sha256"]:
            # Changed in this repo, but the skill's version has not moved since
            # the stamp, so there is nothing new to merge. quality.py lives here.
            customized.append(rel)
            continue
        else:
            # Changed in this repo, and the skill's version moved (or was never
            # recorded). Keep the old record so the next update still knows the base.
            review.append((rel, src, skill_change(root, src, base["sha256"]) if base else None))
            continue
        files[rel] = entry
    version = skill_version(root)
    if version:
        record["skill"] = version
    save_stamp(root, record)
    gitignore: list = []
    ensure_gitignore(root, gitignore)

    sources = {src.relative_to(TEMPLATES).as_posix() for src, _ in pairs}
    gone = sorted(rel for rel, entry in files.items() if entry["source"] not in sources)

    print(f"sssf updated in {root}")
    register(root)
    if version:
        print(f"  skill: {version}")
    elif record.get("skill"):
        # Run by hand from the repo's copy, e.g. for --keep: there is no skill
        # source to read, and the recorded version still stands.
        print(f"  skill: {record['skill']} (unchanged; no skill git source in this run)")
    else:
        print("  skill: unknown (no git source)")
    print(f"  updated: {len(updated)}")
    for rel in updated:
        print(f"    ~ {rel}")
    print(f"  added: {len(added)}")
    for rel in added:
        print(f"    + {rel}")
    print(f"  up to date: {current}")
    print(f"  customized: {len(customized)} (changed in this repo; nothing new in the skill's version)")
    for rel in customized:
        print(f"    = {rel}")
    if reviewed:
        print(f"  kept as reviewed: {len(reviewed)}")
        for rel in reviewed:
            print(f"    = {rel}")
    print(f"  to review: {len(review)} (changed in this repo, and the skill's version is "
          "newer or was never recorded)")
    for rel, src, change in review:
        print(f"    ! {rel}")
        if change:
            print(f"        the skill's change, to copy in: {change}")
        print(f"        yours against the skill's: diff {src} {rel}")
    if review:
        print("  for each, one of:")
        print("    uv run .claude/skills/sssf/scripts/install.py --update --take <path>"
              "   # use the skill's version")
        print("    uv run .claude/skills/sssf/scripts/install.py --update --keep <path>"
              "   # keep yours, after copying in any skill change you want")
    for line in gitignore:
        print(f"  + {line}")
    if gone:
        print(f"  no longer in the skill (left in place): {len(gone)}")
        for rel in gone:
            print(f"    - {rel}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--force", action="store_true", help="overwrite existing files")
    mode.add_argument("--update", action="store_true",
                      help="bring an installed repo up to this skill's version, "
                           "keeping files the repo changed")
    parser.add_argument("--take", action="append", default=[], metavar="PATH",
                        help="with --update: replace this kept file anyway (repeatable)")
    parser.add_argument("--keep", action="append", default=[], metavar="PATH",
                        help="with --update: keep this file and mark it reviewed (repeatable)")
    parser.add_argument("--coding-agent", choices=sorted(ROSTERS), default="pi",
                        help="which starter roster to stamp (default: pi)")
    parser.add_argument("--chatgpt", action="store_true",
                        help="stamp the Pi roster for a ChatGPT subscription sign-in")
    parser.add_argument("--container", action="store_true",
                        help="also stamp container/, to run the operator session in a "
                             "Podman container (Claude Code rosters only)")
    args = parser.parse_args()
    if (args.take or args.keep) and not args.update:
        parser.error("--take and --keep need --update")
    if args.chatgpt and args.coding_agent != "pi":
        parser.error("--chatgpt runs on Pi; leave out --coding-agent")
    roster = CHATGPT_ROSTER if args.chatgpt else ROSTERS[args.coding_agent]

    root = Path.cwd().resolve()
    if args.update:
        return update(root, roster,
                      {Path(p).as_posix() for p in args.take},
                      {Path(p).as_posix() for p in args.keep})

    if args.container and args.update:
        parser.error("--container is an install option; run install.py --container to add it")
    if args.container:
        config = root / "adws/adw_sssf_config/sssf.config.yaml"
        text = config.read_text() if config.is_file() else roster.read_text()
        if "coding_agent: claude_code" not in text:
            parser.error("--container needs a Claude Code roster: the image has no Pi. "
                         "Install with --coding-agent claude_code")

    code = install(root, roster, args.force, args.container)
    # The recipes are `just demo` when SSSF's justfile is the repo's, and
    # `just sssf demo` when the repo's own justfile loads them as a module.
    j = "just sssf" if loads_module(root) else "just"
    own = not loads_module(root) and load_stamp(root)["files"].get("justfile", {}).get(
        "sha256") != sha256(root / "justfile")
    print("\nnext steps:")
    if args.coding_agent == "claude_code":
        print("  1. claude auth status    # must be logged in; no API key needed")
    elif args.chatgpt:
        print("  1. pi, then /login, pick openai     # sign Pi into ChatGPT; no API key needed")
        print("     pi auth check --provider openai --json   # must say \"authType\": \"oauth\"")
        print("     cp .env.sample .env, then set SSSF_PI_AUTH=subscription")
    else:
        print("  1. cp .env.sample .env   # then set the key(s) your roster needs")
    if own:
        print("     this repo has its own justfile, so SSSF's recipes are not in it:")
        print("     add  mod sssf 'sssf.just'  to it and run install.py again")
    print(f"  2. {j} demo".ljust(27) + "# two cheap read-only runs, end to end")
    print(f"  3. {j} sessions".ljust(27) + "# what just happened")
    print(f"  4. {j} obs".ljust(27) + "# the trace UI, needs bun")
    print(f"     {j} obs-all".ljust(27) + "# every repo SSSF is installed in, one dashboard")
    print("  5. exclude adws/ and .claude/ from repo-wide lint, format, typecheck,")
    print("     and package builds (see cookbooks/install.md, post-install checklist)")
    if (root / "container/run.sh").is_file():
        print("  container: the operator session can run in Podman, with no permission prompts")
        print("     claude setup-token, then save it as described in cookbooks/install.md")
        print("     container/run.sh          # builds the image on first use, then starts claude")
    print("\n  no just? the raw form of step 2 is:")
    print("     uv run adws/adw_prompt.py \"say hello\" --agent scout")
    return code


if __name__ == "__main__":
    sys.exit(main())
