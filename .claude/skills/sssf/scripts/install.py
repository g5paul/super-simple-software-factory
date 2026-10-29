#!/usr/bin/env -S uv run
# /// script
# dependencies = []
# ///
"""/install — stamp the SSSF factory from the skill into the cwd. Idempotent.

Usage:
    uv run <skill>/scripts/install.py [--force] [--coding-agent pi|claude_code]
    uv run <skill>/scripts/install.py --update [--take PATH ...]

Stamps: adws/ (modules + starter ADWs), adws/adw_data/prompt_engineering/
(4 starter agents), adws/adw_sssf_config/sssf.config.yaml, .env.sample,
.gitignore entries.
Existing files are skipped unless --force.

Every stamped file's hash goes into adws/adw_sssf_config/stamp.json.
--update uses it to bring the repo up to this skill's version: a file that
still matches its record is replaced, a file the repo changed is kept and
listed, a missing file is added. --take PATH replaces a kept file anyway.
"""

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"

# One starter roster per harness. Pi stays the default; claude_code stamps a
# roster that runs on the `claude` CLI and the subscription login.
ROSTERS = {
    "pi": TEMPLATES / "sssf.config.yaml",
    "claude_code": TEMPLATES / "sssf.config.claude_code.yaml",
}

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
]


STAMP = Path("adws/adw_sssf_config/stamp.json")


def targets(root: Path, roster: Path) -> list[tuple[Path, Path]]:
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
    # them. Skipped like any other file if the repo already has a justfile.
    add(TEMPLATES / "justfile", root / "justfile")
    return pairs


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_sssf_code(rel: str) -> bool:
    """Code a repo is not meant to edit. quality.py is the exception: it holds
    the repo's own test, lint, and typecheck commands."""
    if rel == "adws/adw_modules/quality.py":
        return False
    parts = rel.split("/")
    return (rel.startswith("adws/adw_modules/")
            or (len(parts) == 2 and parts[0] == "adws" and parts[1].startswith("adw_")
                and parts[1].endswith(".py")))


def load_stamp(root: Path) -> dict:
    path = root / STAMP
    return json.loads(path.read_text()) if path.is_file() else {"files": {}}


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


def install(root: Path, roster: Path, force: bool) -> int:
    record = load_stamp(root)
    stamped, skipped = [], []
    for src, dest in targets(root, roster):
        rel = dest.relative_to(root).as_posix()
        entry = {"source": src.relative_to(TEMPLATES).as_posix(), "sha256": sha256(src)}
        if dest.exists() and not force:
            skipped.append(str(dest))
            if sha256(dest) == entry["sha256"]:
                record["files"][rel] = entry
            continue
        copy(src, dest)
        record["files"][rel] = entry
        stamped.append(str(dest))
    save_stamp(root, record)
    ensure_gitignore(root, stamped)

    print(f"sssf installed into {root}")
    print(f"  stamped: {len(stamped)} file(s)")
    for s in stamped:
        print(f"    + {s}")
    if skipped:
        print(f"  skipped (already exist, use --force to overwrite): {len(skipped)}")
    return 0


def update(root: Path, default_roster: Path, take: set[str]) -> int:
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

    pairs = targets(root, roster)
    unknown = take - {dest.relative_to(root).as_posix() for _, dest in pairs}
    if unknown:
        print(f"--take: not a stamped file: {', '.join(sorted(unknown))}", file=sys.stderr)
        return 1

    added, updated, kept, current = [], [], [], 0
    for src, dest in pairs:
        rel = dest.relative_to(root).as_posix()
        entry = {"source": src.relative_to(TEMPLATES).as_posix(), "sha256": sha256(src)}
        base = files.get(rel)
        if not dest.exists():
            copy(src, dest)
            added.append(rel)
        elif sha256(dest) == entry["sha256"]:
            current += 1
        elif (rel in take
              or (base and base["sha256"] == sha256(dest))
              or (base is None and is_sssf_code(rel))):
            # Unchanged since the last stamp, or SSSF's own code in a repo
            # stamped before stamp.json existed, or the user asked for it.
            copy(src, dest)
            updated.append(rel)
        else:
            # Changed in this repo, or no record to prove otherwise. Keep it,
            # and keep its old record so the next update still knows the base.
            kept.append((rel, src))
            continue
        files[rel] = entry
    save_stamp(root, record)
    gitignore: list = []
    ensure_gitignore(root, gitignore)

    sources = {src.relative_to(TEMPLATES).as_posix() for src, _ in pairs}
    gone = sorted(rel for rel, entry in files.items() if entry["source"] not in sources)

    print(f"sssf updated in {root}")
    print(f"  updated: {len(updated)}")
    for rel in updated:
        print(f"    ~ {rel}")
    print(f"  added: {len(added)}")
    for rel in added:
        print(f"    + {rel}")
    print(f"  up to date: {current}")
    print(f"  kept: {len(kept)} (changed in this repo, or never recorded)")
    for rel, src in kept:
        print(f"    ! {rel}")
        print(f"        compare: diff {src} {rel}")
    if kept:
        print("  to replace a kept file with the skill's version: --update --take <path>")
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
    parser.add_argument("--coding-agent", choices=sorted(ROSTERS), default="pi",
                        help="which starter roster to stamp (default: pi)")
    args = parser.parse_args()
    if args.take and not args.update:
        parser.error("--take needs --update")

    root = Path.cwd()
    if args.update:
        return update(root, ROSTERS[args.coding_agent],
                      {Path(p).as_posix() for p in args.take})

    code = install(root, ROSTERS[args.coding_agent], args.force)
    print("\nnext steps:")
    if args.coding_agent == "claude_code":
        print("  1. claude auth status    # must be logged in; no API key needed")
    else:
        print("  1. cp .env.sample .env   # then set the key(s) your roster needs")
    print("  2. just demo             # two cheap read-only runs, end to end")
    print("  3. just sessions         # what just happened")
    print("  4. just obs              # the trace UI, needs bun")
    print("\n  no just? the raw form of step 2 is:")
    print("     uv run adws/adw_prompt.py \"say hello\" --agent scout")
    return code


if __name__ == "__main__":
    sys.exit(main())
