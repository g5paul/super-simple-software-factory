"""Prompt rendering: load system/user refs from config, replace {{placeholders}}."""

from __future__ import annotations

from pathlib import Path

from . import session_files


def render(template_path: str | Path, variables: dict[str, str]) -> str:
    text = Path(template_path).read_text()
    for key, value in variables.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def save(directory: str | Path, name: str, content: str, root: str | Path) -> Path:
    """Save the exact prompt sent, before execution — the audit copy.

    `root` is the run's data root; the write never follows a link under it.
    """
    directory = Path(directory)
    path = directory / name
    session_files.write(path, content, root)
    return path
