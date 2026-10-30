#!/usr/bin/env -S uv run
# /// script
# dependencies = []
# ///
"""make_config — generate adws/adw_sssf_config/sssf.config.yaml with great defaults.

Usage:
    uv run <skill>/scripts/make_config.py [--force] [--coding-agent pi|claude_code] [--chatgpt]
"""

import argparse
import shutil
import sys
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
ROSTERS = {
    "pi": TEMPLATES / "sssf.config.yaml",
    "claude_code": TEMPLATES / "sssf.config.claude_code.yaml",
}
CHATGPT_ROSTER = TEMPLATES / "sssf.config.chatgpt.yaml"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--coding-agent", choices=sorted(ROSTERS), default="pi")
    parser.add_argument("--chatgpt", action="store_true",
                        help="the Pi roster for a ChatGPT subscription sign-in")
    args = parser.parse_args()
    if args.chatgpt and args.coding_agent != "pi":
        parser.error("--chatgpt runs on Pi; leave out --coding-agent")

    dest = Path.cwd() / "adws" / "adw_sssf_config" / "sssf.config.yaml"
    if dest.exists() and not args.force:
        print(f"{dest} already exists — use --force to overwrite")
        return 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(CHATGPT_ROSTER if args.chatgpt else ROSTERS[args.coding_agent], dest)
    print(f"wrote {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
