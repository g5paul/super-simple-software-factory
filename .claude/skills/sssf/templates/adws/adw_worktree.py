#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""Land or drop a worktree run: the merge step for chains started with --worktree.

Usage:
    uv run adws/adw_worktree.py merge <adw_id> [--config adws/adw_sssf_config/sssf.config.yaml]
    uv run adws/adw_worktree.py discard <adw_id> [--config ...]

merge: only a successful run. Fast-forward when it can, a merge commit when the
launch branch moved on, never a rebase. A conflict aborts and keeps the branch.
discard: removes the run's worktree and branch, whatever state they are in.

Neither pushes. Run them in the main checkout, on the run's launch branch.
"""

import argparse
import sys

from adw_modules import agents, worktree


def main(action: str, adw_id: str, config: str) -> int:
    cfg = agents.load_config(config)
    try:
        done = (worktree.merge if action == "merge" else worktree.discard)(cfg, adw_id)
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
    args = parser.parse_args()
    sys.exit(main(args.action, args.adw_id, args.config))
