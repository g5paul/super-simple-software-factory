"""The repo files agents must not read: .env and its kin, plus repo.yaml's
`secret_files`.

The bar is accidental exposure: an agent doing ordinary work runs `cat .env`
or opens it with a Read tool, and the secret lands in a commit or a log. This
is not a boundary. An agent set on a secret can build the path at runtime or
read /proc/<pid>/environ. The container and VMs are the boundary.
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import Iterable, Optional

from .rules import AgentRule

DEFAULT_ALLOWED = {".env.sample", ".env.example"}
PRUNE = {".git", "node_modules", ".venv", "venv", "__pycache__"}

RULE_TEXT = ("Don't read, print or source this repo's secret files (.env and the like). "
             "SSSF keeps them from agents.")


def is_default(name: str) -> bool:
    return name == ".env" or (name.startswith(".env.") and name not in DEFAULT_ALLOWED)


def _matches(name: str, rel: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if "/" in pattern:
            if fnmatch.fnmatchcase(rel, pattern.removeprefix("./")):
                return True
        elif fnmatch.fnmatchcase(name, pattern):
            return True
    return False


def find(roots: Iterable[Path], patterns: list[str], skip: Iterable[Path] = ()) -> list[str]:
    """Absolute paths of the protected files under each root. A directory walk
    only: nothing is opened. Prunes PRUNE names and any dir in `skip`."""
    skipped = {Path(s).resolve() for s in skip}
    found: set[str] = set()
    for root in roots:
        root = Path(root)
        for top, dirs, files in os.walk(root, followlinks=False, onerror=lambda e: None):
            dirs[:] = [d for d in dirs
                       if d not in PRUNE and (Path(top) / d).resolve() not in skipped]
            for name in files:
                path = Path(top) / name
                rel = path.relative_to(root).as_posix()
                if is_default(name) or _matches(name, rel, patterns):
                    found.add(os.path.abspath(path))
    return sorted(found)


def shell_rule(files: list[str]) -> Optional[AgentRule]:
    """A refuse rule for shell commands that name one of `files` by basename or
    path. `.env.sample` doesn't match `.env`: a `.` follows."""
    if not files:
        return None
    names = sorted({os.path.basename(f) for f in files})
    alt = "|".join(re.escape(n) for n in names)
    return AgentRule(rule=RULE_TEXT, on_match="refuse",
                     forbid=[rf"(?<![\w.-])(?:{alt})(?![\w.-])"])
