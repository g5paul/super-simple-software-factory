#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""What remote VM runs have used of Vercel's and E2B's free allowances.

Usage:
    uv run adws/adw_sandbox_usage.py [other repos' traces ...] [--config adws/adw_sssf_config/sssf.config.yaml]

Reads this repo's trace, plus any other trace named, since an allowance is
shared by every repo using the same Vercel or E2B team. See
adw_modules/sandbox_usage.py for the allowances it counts against.
"""

import argparse
import sys
from pathlib import Path

from adw_modules import agents, sandbox_usage, worktree


def main(others: list[str], config: str) -> int:
    cfg = agents.load_config(config)
    worktree.anchor(cfg, worktree.main_checkout())
    dbs = [Path(cfg.observability.db).resolve(), *(Path(o).resolve() for o in others)]
    try:
        lines = sandbox_usage.report(list(dict.fromkeys(dbs)))
    except FileNotFoundError as error:
        print(error)
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("others", nargs="*", help="other repos' sssf.db, sharing the account")
    parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
    args = parser.parse_args()
    sys.exit(main(args.others, args.config))
