# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""Build SSSF's OpenShell sandbox image with Podman, which `--sandbox openshell`
makes each sandbox from: `localhost/sssf-openshell:<hash>`.

The image is per machine and per repo layout. It bakes in the folder the repo
and its worktrees share, owned by the sandbox user, so the tag's hash covers
that folder. `just openshell-setup` runs this.

    uv run <skill>/scripts/openshell_image.py [--repo .] [--print]

--print shows the tag and the Containerfile and runs no Podman.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "templates" / "adws"))

from adw_modules import sandbox_openshell as openshell  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", default=".")
    parser.add_argument("--print", action="store_true", dest="show")
    args = parser.parse_args()
    try:
        folder = openshell.folder_for(Path(args.repo).resolve())
    except ValueError as error:
        print(f"openshell_image: {error}", file=sys.stderr)
        return 2
    tag, text = openshell.image_tag(folder), openshell.containerfile(folder)
    if args.show:
        print(tag)
        print(text)
        return 0
    with tempfile.TemporaryDirectory() as empty:
        code = subprocess.run(["podman", "build", "-t", tag, "-f", "-", empty],
                              input=text.encode()).returncode
    if code == 0:
        print(tag)
    return code


if __name__ == "__main__":
    sys.exit(main())
