#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""Writes this repo's agentsh policies and the server config, and prints the sudo steps.

Usage:
    uv run adws/adw_agentsh_policy.py [--config adws/adw_sssf_config/sssf.config.yaml]

Needed once per repo, and again after repo.yaml's secret_files, deny_exec or
network_allow change. It never runs sudo: it writes the files in a fresh temp
folder `tempfile` makes (named in the output) and prints the `sudo install` lines for you
to run. Stop every agent before running them, because sudo install follows a link.
Nothing is written under the repo.
"""

import argparse
import shlex
import sys
import tempfile
from pathlib import Path

import yaml

from adw_modules import agents, agentsh_guard, worktree


def main(config: str) -> int:
    cfg = agents.load_config(config)
    worktree.anchor(cfg, worktree.main_checkout())
    guard = agentsh_guard.read(cfg)   # a SystemExit here says why the setting is wrong
    if guard.mode != "agentsh":
        print("local_guard is not agentsh in adws/adw_sssf_config/repo.yaml; nothing to install.")
        return 1
    out = Path(tempfile.mkdtemp(prefix="sssf-agentsh-"))
    try:
        installed = yaml.safe_load(Path(agentsh_guard.CONFIG).read_text()) or {}
        listed = [a for a in (installed.get("policies") or {}).get("allowed") or [] if isinstance(a, str)]
    except (OSError, ValueError, AttributeError, TypeError, yaml.YAMLError):
        listed = []                       # no config yet: the template's list is empty
    names = [p.name for p in guard.policies.values()]
    allowed = list(dict.fromkeys([*listed, *names]))   # another repo's entry survives
    steps = []
    merged = out / "sssf.yaml"
    merged.write_text(agentsh_guard.render_config(guard.config_template, allowed), encoding="utf-8")
    steps.append(shlex.join(["sudo", "install", "-o", "root", "-g", "root", "-m", "0644", str(merged), agentsh_guard.CONFIG]))
    for policy in guard.policies.values():
        path = out / f"{policy.name}.yaml"
        path.write_text(policy.text, encoding="utf-8")
        steps.append(shlex.join(["sudo", "install", "-o", "root", "-g", "root", "-m", "0644", str(path),
                                 f"{agentsh_guard.POLICY_DIR}/{policy.name}.yaml"]))
    steps.append("sudo systemctl restart agentsh")
    print("Every agent must be stopped before you run these lines. `sudo install` follows a link, so an agent "
          "running during the install could swap a temp file for a link to a root-only file and get it copied "
          f"to {agentsh_guard.CONFIG} at mode 0644.\n")
    print("Run these, then start the chain again:\n")
    print("\n".join(steps))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
    sys.exit(main(parser.parse_args().config))
