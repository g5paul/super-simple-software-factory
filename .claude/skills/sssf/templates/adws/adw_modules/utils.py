"""Small shared helpers. Anything bigger belongs in its own module."""

from __future__ import annotations

import functools
import os
import re
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def mark_agent(env: dict[str, str], agent_name: str, adw_id: str) -> dict[str, str]:
    """Tell an agent's processes they belong to an SSSF agent: SSSF_ADW_ID is
    the run, SSSF_AGENT the roster name. A repo's own script can then refuse
    a command before it starts, which an agent_rules `forbid` can't promise:
        [ -z "${SSSF_ADW_ID:-}" ] || { echo "Refusing: ..."; exit 1; }
    It's a courtesy, not a lock. An agent can unset it, so keep `forbid` too.
    """
    if adw_id:
        env["SSSF_ADW_ID"] = adw_id
    if agent_name:
        env["SSSF_AGENT"] = agent_name
    return env


def operator_env() -> dict[str, str]:
    """The engineer's own environment, as their shell would hand it over.

    Agents and quality blocks are meant to see exactly what the operator sees:
    their PATH, their toolchains, their globally installed packages. Copying
    os.environ gets almost all the way there — but ADWs launch under `uv run`,
    which prepends its ephemeral venv's bin to PATH and sets VIRTUAL_ENV. That
    venv holds the ADW's OWN dependencies (pydantic, pyyaml), not the
    operator's, so anything a subprocess resolves through it — `python3`,
    `pip`, every globally pip-installed CLI — silently becomes the wrong one.

    Stripping the venv restores parity: `python3` in an agent's bash is the
    same `python3` the engineer gets in their terminal. The ADW's own imports
    are unaffected; this env is only ever handed to child processes.
    """
    env = os.environ.copy()
    venv = env.pop("VIRTUAL_ENV", "")
    if not venv:
        return env
    venv_bin = str(Path(venv) / "bin")
    parts = [p for p in env.get("PATH", "").split(os.pathsep) if p and p != venv_bin]
    env["PATH"] = os.pathsep.join(parts)
    return env


# The same test as install.py's loads_module.
MOD_LINE = re.compile(r"^\s*mod\s+sssf(\s+['\"]sssf\.just['\"])?\s*$", re.MULTILINE)


@functools.lru_cache(maxsize=1)
def _recipe_prefix() -> str:
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    root = Path(top.stdout.strip()) if top.returncode == 0 else Path.cwd()
    justfile = root / "justfile"
    loads = justfile.is_file() and MOD_LINE.search(justfile.read_text()) is not None
    return "just sssf" if loads else "just"


def just(recipe: str) -> str:
    """An SSSF recipe as this repo runs it: `just sssf <recipe>` when the
    repo's own justfile loads SSSF's recipes as the `sssf` module, else
    `just <recipe>`. Every message that names a recipe goes through here."""
    return f"{_recipe_prefix()} {recipe}"


def new_id(length: int = 8) -> str:
    return secrets.token_hex(length // 2)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def resolve_prompt(arg: str) -> str:
    """CLI prompt arg: a file path resolves to its contents, else inline text."""
    try:
        p = Path(arg)
        if p.is_file():
            return p.read_text()
    except OSError:
        pass
    return arg


def engineer_name() -> str:
    name = os.environ.get("ENGINEER_NAME", "").strip()
    if name:
        return name
    try:
        out = subprocess.run(["git", "config", "user.name"],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except OSError:
        pass
    return os.environ.get("USER", "engineer")
