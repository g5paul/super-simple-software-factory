# /// script
# requires-python = ">=3.11"
# dependencies = ["e2b==2.62.0", "python-dotenv"]
# ///
"""Build SSSF's E2B template, which `--sandbox e2b` makes each VM from.

E2B's base image is Debian with git, Python and Node 20, 2 vCPUs and 512MB.
The template adds Node 24 (pi needs 22 or later), uv, bun, `claude` and `pi`,
and gives the VM 2 vCPUs and 4GB, the size of a Vercel VM. Build it once per
E2B team, and again to take newer tools. Node and pi are pinned, so a
rebuild gives the same pi the last proof ran: raise PI here, and rebuild,
to take a newer one. `claude` installs the latest each build.

Run from a repo whose .env has E2B_API_KEY:
    uv run <skill>/scripts/e2b_template.py [--name sssf]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

NODE = "24.11.1"
PI = "1.5.0"      # the pi the 2026-11-03 ChatGPT live check ran


def template():
    from e2b import Template
    return (Template()
            .from_base_image()
            # Node 20's npm goes first: unpacked over it, npm 11 keeps stale
            # files and fails with "Class extends value undefined".
            .run_cmd("rm -rf /usr/local/lib/node_modules/npm && curl -fsSL"
                     f" https://nodejs.org/dist/v{NODE}/node-v{NODE}-linux-x64.tar.xz"
                     " | tar -xJ -C /usr/local --strip-components=1", user="root")
            .run_cmd(f"npm install -g @earendil-works/pi-coding-agent@{PI}", user="root")
            .run_cmd("curl -LsSf https://astral.sh/uv/install.sh"
                     " | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh", user="root")
            .run_cmd("curl -fsSL https://bun.sh/install | BUN_INSTALL=/usr/local bash", user="root")
            .run_cmd("curl -fsSL https://claude.ai/install.sh | bash")
            .run_cmd("ln -sf /home/user/.local/bin/claude /usr/local/bin/claude", user="root")
            .run_cmd("node --version && uv --version && bun --version"
                     " && claude --version && pi --version"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--name", default="sssf", help="the template's name; SSSF_E2B_TEMPLATE picks another")
    args = parser.parse_args()
    load_dotenv(Path.cwd() / ".env")
    from e2b import Template
    info = Template.build(template(), args.name, cpu_count=2, memory_mb=4096,
                          on_build_logs=lambda entry: print(entry, flush=True))
    print(f"built E2B template {args.name}: {info}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
