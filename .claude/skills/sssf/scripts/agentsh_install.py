#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""Writes a project's agentsh policies and server config from the HUB, and prints the sudo steps.

Usage, from anywhere:
    uv run --directory <hub> .claude/skills/sssf/scripts/agentsh_install.py --repo <project main checkout>
        [--config adws/adw_sssf_config/sssf.config.yaml]

The hub is the SSSF source checkout on this machine, clean and at the skill
version in the project's stamp.json. The script renders with the hub's code and
the hub's agentsh_sssf.yaml. From the project it reads only data: repo.yaml and
sssf.config.yaml through yaml.safe_load, and stamp.json's `skill` through json.
It imports nothing from the project and runs no git there.

It never runs sudo. It prints a diff of each new file against /etc/agentsh/
(the whole file when nothing is installed), then the `sudo install` lines and
`sudo systemctl restart agentsh`. Read the diff. Stop every agent before the
sudo lines, because sudo install follows a link.
"""

import argparse
import difflib
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

import yaml  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from adw_modules import agents, agentsh_guard, repo_settings  # noqa: E402

STAMP = "adws/adw_sssf_config/stamp.json"
_noted = False


def hub_version() -> tuple[str, bool]:
    """The hub skill's full sha and whether its folder has changes."""
    skill = Path(__file__).resolve().parent.parent
    sha = subprocess.run(["git", "-C", str(skill), "log", "-1", "--format=%H", "--", "."],
                         capture_output=True, text=True, check=False).stdout.strip()
    status = subprocess.run(["git", "-C", str(skill), "status", "--porcelain", "--", "."],
                            capture_output=True, text=True, check=False).stdout.strip()
    return sha, bool(status)


def shown(line: str) -> str:
    """The line with everything outside printable ASCII escaped."""
    global _noted
    out = []
    for c in line:
        if " " <= c <= "~":
            out.append(c)
            continue
        if not _noted:
            _noted = True
            print("note: characters outside printable ASCII are shown escaped; the file holds them as is.")
        n = ord(c)
        out.append(f"\\x{n:02x}" if n < 0x100 else f"\\u{n:04x}" if n < 0x10000 else f"\\U{n:08x}")
    return "".join(out)


def refuse(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", required=True, help="the project's main checkout")
    parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml",
                        help="the roster, relative to --repo")
    args = parser.parse_args(argv)
    global _noted
    _noted = False

    root = Path(args.repo).resolve()
    if Path(__file__).resolve().is_relative_to(root):
        return refuse(f"this script is inside {root}; run the hub's copy: "
                      "uv run --directory <hub> .claude/skills/sssf/scripts/agentsh_install.py --repo <project>")
    git_dir = root / ".git"
    if git_dir.is_symlink() or not git_dir.is_dir():
        return refuse(f"{git_dir} is not a plain .git folder; pass the project's main checkout, "
                      "not a linked worktree or a link")
    if os.path.lexists(git_dir / "commondir"):
        return refuse(f"{git_dir / 'commondir'} exists, so this is not a plain main checkout; "
                      "pass the project's main checkout")

    hub_sha, dirty = hub_version()
    try:
        recorded = json.loads((root / STAMP).read_text()).get("skill")
    except (OSError, ValueError, AttributeError):
        recorded = None
    if not isinstance(recorded, str) or not recorded:
        return refuse(f"can't read the skill version from {root / STAMP}; run `just sssf-update` in the project")
    if recorded.endswith("+dirty") or dirty or not hub_sha or not hub_sha.startswith(recorded):
        return refuse(f"the project is on skill {recorded}; the hub is on {hub_sha[:7] or 'unknown'}"
                      f"{' (dirty)' if dirty else ''}. Run `just sssf-update` in the project from this hub, "
                      "or check out the project's version in a clean hub. The project's agentsh_guard.py "
                      "checks the installed files at run time, so it must render the same text.")

    try:
        settings = repo_settings.load(root)
    except (ValidationError, yaml.YAMLError, ValueError, OSError) as error:
        return refuse(f"{repo_settings.PATH} is invalid: {error}")
    if settings.local_guard != "agentsh":
        print("local_guard is not agentsh in adws/adw_sssf_config/repo.yaml; nothing to install.")
        return 1
    config_path = root / args.config
    try:
        cfg = agents.load_config(str(config_path))
        template = (SKILL / "templates/adws/adw_modules/agentsh_sssf.yaml").read_text()
    except (ValidationError, yaml.YAMLError, ValueError, OSError) as error:
        return refuse(f"can't read {config_path}: {error}")

    refuse_script = str((root / "adws/adw_modules/agentsh_guard.py").resolve().with_name("sssf_refuse.py"))
    guard = agentsh_guard.guard_for(settings, cfg, root, str(git_dir), refuse_script, template)

    try:
        installed = yaml.safe_load(Path(agentsh_guard.CONFIG).read_text()) or {}
        listed = [a for a in (installed.get("policies") or {}).get("allowed") or [] if isinstance(a, str)]
    except (OSError, ValueError, AttributeError, TypeError, yaml.YAMLError):
        listed = []
    allowed = list(dict.fromkeys([*listed, *(p.name for p in guard.policies.values())]))

    out = Path(tempfile.mkdtemp(prefix="sssf-agentsh-"))
    items = [(agentsh_guard.render_config(guard.config_template, allowed), agentsh_guard.CONFIG, out / "sssf.yaml")]
    for policy in guard.policies.values():
        items.append((policy.text, f"{agentsh_guard.POLICY_DIR}/{policy.name}.yaml", out / f"{policy.name}.yaml"))

    steps = []
    for new_text, target, temp in items:
        temp.write_text(new_text, encoding="utf-8", newline="")
        try:
            old_bytes = Path(target).read_bytes()
        except OSError:
            old_bytes = None
        if old_bytes == new_text.encode():
            print(f"unchanged: {target}")
            continue
        old = None
        if old_bytes is not None:
            try:
                old = old_bytes.decode("utf-8")
            except UnicodeDecodeError:
                print(f"{target}: installed file is not UTF-8; the whole new file:")
        else:
            print(f"{target}: not installed; the whole new file:")
        if old is None:
            lines = new_text.split("\n")
        else:
            lines = difflib.unified_diff(old.split("\n"), new_text.split("\n"),
                                         f"{target} (installed)", f"{temp} (new)", lineterm="")
        for line in lines:
            print(shown(line))
        steps.append(shlex.join(["sudo", "install", "-o", "root", "-g", "root", "-m", "0644", str(temp), target]))

    print(f"Rendered from hub skill {hub_sha[:12]}, {root / repo_settings.PATH} and {config_path}.")
    if steps:
        print("Every agent must be stopped before you run these lines. `sudo install` follows a link, so an agent "
              "running during the install could swap a temp file for a link to a root-only file and get it copied "
              f"to {agentsh_guard.CONFIG} at mode 0644.\n")
        print("Run these, then start the chain again:\n")
        print("\n".join(steps))
    else:
        print("Nothing to install: /etc/agentsh matches what this project renders. "
              "If the service hasn't been restarted since the last install, run:")
    print("sudo systemctl restart agentsh")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
