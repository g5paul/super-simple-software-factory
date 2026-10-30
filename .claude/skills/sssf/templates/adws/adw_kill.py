#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""Stop a run by adw_id: the chain first, then its coding agents.

Usage:
    uv run adws/adw_kill.py <adw_id> [--config adws/adw_sssf_config/sssf.config.yaml]

Each pid is checked against what the trace recorded before it is signalled,
because pids get reused. The run ends `fail`. A worktree run keeps its branch
and folder; `just discard <adw_id>` removes them.
"""

import argparse
import sys

from adw_modules import agents, kill, worktree


def main(adw_id: str, config: str) -> int:
    cfg = agents.load_config(config)
    worktree.anchor(cfg, worktree.main_checkout())
    report = kill.kill(cfg.observability.db, adw_id)
    for line in report:
        print(line)
    return 1 if any("NOT stopped" in line or "still running" in line for line in report) else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("adw_id")
    parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
    args = parser.parse_args()
    sys.exit(main(args.adw_id, args.config))
