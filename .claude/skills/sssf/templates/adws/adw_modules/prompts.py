"""Prompt rendering: load system/user refs from config, replace {{placeholders}}.

Rendering is one pass over the template. A value is never read for
placeholders, so text an agent or the engineer wrote cannot pull another
variable into the prompt."""

from __future__ import annotations

import re
from pathlib import Path

from . import session_files

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def render(template_path: str | Path, variables: dict[str, str]) -> str:
    text = Path(template_path).read_text()
    # A function, never a replacement string: backslashes in a value stay literal.
    return _PLACEHOLDER.sub(lambda m: variables.get(m.group(1), m.group(0)), text)


def save(directory: str | Path, name: str, content: str, root: str | Path) -> Path:
    """Save the exact prompt sent, before execution — the audit copy.

    `root` is the run's data root; the write never follows a link under it.
    """
    directory = Path(directory)
    path = directory / name
    session_files.write(path, content, root)
    return path
