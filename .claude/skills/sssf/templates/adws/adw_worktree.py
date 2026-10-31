#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""Land or drop a worktree run: the merge step for chains started with --worktree.

Usage:
    uv run adws/adw_worktree.py merge <adw_id> [--ff-only] [--config adws/adw_sssf_config/sssf.config.yaml]
    uv run adws/adw_worktree.py discard <adw_id> [--config ...]
    uv run adws/adw_worktree.py rebase <adw_id> [--config ...]

merge: only a successful run. Fast-forward when it can, a merge commit when the
launch branch moved on, never a rebase. A conflict aborts and keeps the branch.
--ff-only refuses a merge commit, for a repo that keeps a linear history. So
does `merge: ff_only` in adws/adw_sssf_config/repo.yaml, unless this machine's
.env sets SSSF_MERGE_FF_ONLY=0.
discard: removes the run's worktree and branch, whatever state they are in.
rebase: only when the engineer asks, for a fast-forward-only repo. Moves a
successful run's branch onto the launch branch's tip and reruns adw_quality in
its worktree. A conflict or a failed check puts the branch back as it was.

None of them pushes. Run them in the main checkout, on the run's launch branch.
"""

import argparse
import sys

from adw_modules import agents, repo_settings, worktree


def main(action: str, adw_id: str, config: str, ff_only: bool = False) -> int:
    cfg = agents.load_config(config)
    try:
        done = (worktree.merge(cfg, adw_id, ff_only) if action == "merge"
                else worktree.rebase(cfg, adw_id) if action == "rebase"
                else worktree.discard(cfg, adw_id))
    except worktree.WorktreeError as error:
        print(f"{action} {adw_id}: {error}", file=sys.stderr)
        return 1
    print(done)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["merge", "discard", "rebase"])
    parser.add_argument("adw_id")
    parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
    parser.add_argument("--ff-only", action="store_true",
                        help="refuse a merge commit; repo.yaml's `merge: ff_only` turns it on too")
    args = parser.parse_args()
    ff_only = args.ff_only or repo_settings.merge_ff_only(worktree.main_checkout())
    sys.exit(main(args.action, args.adw_id, args.config, ff_only))
