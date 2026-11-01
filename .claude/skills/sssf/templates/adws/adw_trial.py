#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Trial — one request, several rosters, the best one merged.

Usage:
    uv run adws/adw_trial.py start "<prompt or path/to/prompt.md>" <roster> <roster> [...]
    uv run adws/adw_trial.py review <trial>
    uv run adws/adw_trial.py show <trial>
    uv run adws/adw_trial.py merge <trial> <adw_id> [--ff-only]
    uv run adws/adw_trial.py discard <trial>

start: one adw_simple_sdlc worktree run per roster, from the same commit. A
roster is `main` (adws/adw_sssf_config/sssf.config.yaml) or the <name> of
adws/adw_sssf_config/sssf.config.<name>.yaml; the same one twice is allowed.
Every roster must be signed in and every attempt must have a worktree slot,
or nothing starts. Once all have ended, the panel reviews them and the
scorecard prints.
review: run the panel again, for a trial whose `start` died or whose panel failed.
show: the scorecard, at any point.
merge: land the engineer's pick like `just merge`, then discard the other attempts.
discard: drop every attempt's branch. The traces stay.

Design: docs/designs/trials.md in the SSSF repo.
"""

import argparse
import sys

from adw_modules import repo_settings, trial, utils, worktree


def main(args: argparse.Namespace) -> int:
    # `just trial ... > trial.log` runs for minutes; each line should reach the log as it prints.
    sys.stdout.reconfigure(line_buffering=True)
    try:
        if args.action == "start":
            trial.start(utils.resolve_prompt(args.prompt), args.rosters)
            return 0
        if args.action == "review":
            return trial.review(args.trial)
        if args.action == "show":
            print(trial.show(args.trial))
            return 0
        if args.action == "merge":
            ff_only = args.ff_only or repo_settings.merge_ff_only(worktree.main_checkout())
            print(trial.merge(args.trial, args.adw_id, ff_only))
            return 0
        print(trial.discard(args.trial))
        return 0
    except (trial.TrialError, worktree.WorktreeError) as error:
        print(f"trial {args.action}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    actions = parser.add_subparsers(dest="action", required=True)
    start = actions.add_parser("start", help="launch one attempt per roster")
    start.add_argument("prompt", help="inline text or a path to a prompt file")
    start.add_argument("rosters", nargs="+", help="main, or the <name> of sssf.config.<name>.yaml")
    for name in ("review", "show", "discard"):
        actions.add_parser(name).add_argument("trial")
    land = actions.add_parser("merge", help="land one attempt and drop the rest")
    land.add_argument("trial")
    land.add_argument("adw_id")
    land.add_argument("--ff-only", action="store_true",
                      help="refuse a merge commit; repo.yaml's `merge: ff_only` turns it on too")
    sys.exit(main(parser.parse_args()))
