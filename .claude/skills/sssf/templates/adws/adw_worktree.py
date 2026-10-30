#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""Land or drop a worktree run: the merge step for chains started with --worktree.

Usage:
    uv run adws/adw_worktree.py merge <adw_id> [--ff-only] [--config adws/adw_sssf_config/sssf.config.yaml]
    uv run adws/adw_worktree.py discard <adw_id> [--config ...]

merge: only a successful run. Fast-forward when it can, a merge commit when the
launch branch moved on, never a rebase. A conflict aborts and keeps the branch.
--ff-only, or SSSF_MERGE_FF_ONLY=1 in the repo's .env, refuses a merge commit:
for a repo that keeps a linear history.
discard: removes the run's worktree and branch, whatever state they are in.

Neither pushes. Run them in the main checkout, on the run's launch branch.
"""

import argparse
import os
import sys

from adw_modules import agents, worktree


def main(action: str, adw_id: str, config: str, ff_only: bool = False) -> int:
    cfg = agents.load_config(config)
    try:
        done = (worktree.merge(cfg, adw_id, ff_only) if action == "merge"
                else worktree.discard(cfg, adw_id))
    except worktree.WorktreeError as error:
        print(f"{action} {adw_id}: {error}", file=sys.stderr)
        return 1
    print(done)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["merge", "discard"])
    parser.add_argument("adw_id")
    parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
    parser.add_argument("--ff-only", action="store_true",
                        help="refuse a merge commit; also on when SSSF_MERGE_FF_ONLY=1")
    args = parser.parse_args()
    ff_only = args.ff_only or os.environ.get("SSSF_MERGE_FF_ONLY", "") not in ("", "0")
    sys.exit(main(args.action, args.adw_id, args.config, ff_only))
