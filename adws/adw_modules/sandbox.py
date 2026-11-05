"""Where a run's tree lives: every process and file read SSSF does in it.

A run's agents, quality checks, plan checks, gates, git, worktree setup, and
adw_tickets' done marks go through `current()`. The only sandbox today is `Local`: the tree is a
checkout on this machine, and each call is a subprocess here. A remote
sandbox (docs/designs/remote-sandboxes.md in the SSSF repo) keeps these calls
and runs them in a VM, so nothing above this module changes when one exists.

Work on the host doesn't come through here: making and merging worktrees,
checking sign-ins, launching trials, and killing runs.

The contract follows subprocess, so callers keep their error handling: `run`
returns a CompletedProcess and raises TimeoutExpired or OSError as
subprocess.run does, and `start` returns a Process whose stdout yields lines.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import IO, Iterable, Optional, Protocol, Union

Command = Union[list[str], str]


class Process(Protocol):
    pid: int
    stdout: Iterable[str]
    stderr: Optional[IO[str]]

    def wait(self) -> int: ...
    def kill(self) -> None: ...
    def stop_tree(self) -> None:
        """Kill the process and everything it started."""


class Sandbox(Protocol):
    name: str

    def run(self, command: Command, *, cwd=None, env=None, timeout=None,
            shell: bool = False, text: bool = True) -> subprocess.CompletedProcess: ...
    def start(self, command: list[str], *, cwd=None, env=None) -> Process: ...
    def exists(self, path) -> bool: ...
    def is_file(self, path) -> bool: ...
    def size(self, path) -> int: ...
    def read_text(self, path) -> str: ...
    def write_text(self, path, text: str) -> None: ...
    def install(self, path) -> None: ...
    def remove(self, path) -> None: ...


class LocalProcess:
    """A subprocess.Popen on this machine, with SSSF's tree stop."""

    def __init__(self, popen: subprocess.Popen):
        self._popen = popen
        self.pid = popen.pid
        self.stdout = popen.stdout
        self.stderr = popen.stderr

    def wait(self) -> int:
        return self._popen.wait()

    def kill(self) -> None:
        self._popen.kill()

    def stop_tree(self) -> None:
        from . import rules
        rules.stop_tree(self.pid)


class Local:
    """The tree is a checkout on this machine. A relative path or a missing
    cwd means this process's working directory, as with subprocess."""

    name = "local"

    def run(self, command: Command, *, cwd=None, env=None, timeout=None,
            shell: bool = False, text: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(command, cwd=cwd, env=env, timeout=timeout, shell=shell,
                              capture_output=True, text=text)

    def start(self, command: list[str], *, cwd=None, env=None) -> LocalProcess:
        # stdin is DEVNULL: an agent's prompt travels in argv, and an inherited
        # non-TTY stdin can leave the child waiting forever (see agent_pi).
        return LocalProcess(subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                             text=True, bufsize=1, cwd=cwd, env=env))

    def exists(self, path) -> bool:
        return Path(path).exists()

    def is_file(self, path) -> bool:
        return Path(path).is_file()

    def size(self, path) -> int:
        return Path(path).stat().st_size

    def read_text(self, path) -> str:
        return Path(path).read_text()

    def write_text(self, path, text: str) -> None:
        Path(path).write_text(text)

    def install(self, path) -> None:
        """This machine's file at `path`, executable, where the tree's processes
        can run it. Here it is already there; only the mode can be missing,
        since a copy of the skill may drop it."""
        mode = Path(path).stat().st_mode
        if mode & 0o111 != 0o111:
            Path(path).chmod(mode | 0o755)

    def remove(self, path) -> None:
        Path(path).unlink()


LOCAL = Local()
_current: Sandbox = LOCAL


def current() -> Sandbox:
    """The sandbox this process's run works in. One run per process, so one
    sandbox: `Local` unless a run has chosen another with `use`."""
    return _current


def use(sandbox: Sandbox) -> None:
    global _current
    _current = sandbox


# --- remote sandboxes -------------------------------------------------------

PROVIDERS = ("vercel", "e2b")


class SandboxFailed(RuntimeError):
    """A remote VM could not be made, set up, or reached, so the run stops."""


def _provider(name: str):
    import importlib
    return importlib.import_module(f".sandbox_{name}", __package__)


def prepare(provider: str, cfg) -> None:
    """Check a remote run can start, before anything is created: the provider
    is known, the roster can run there, and its SDK imports (see sandbox_remote)."""
    check_roster(provider, cfg)
    if any(a.coding_agent == "pi" for a in cfg.agents):
        from . import agent_pi
        agent_pi.vm_token()             # the host's ChatGPT sign-in can hand out a token
    _provider(provider).require_sdk()


def check_roster(provider: str, cfg) -> None:
    """The provider is known and every agent in the roster can run in its VM.
    pi extensions are checked at launch by `extensions_in_clone`. A trial checks this for every roster without importing the SDK, which
    would start the trial again under `uv run --with`."""
    if provider not in PROVIDERS:
        raise SystemExit(f"--sandbox {provider}: SSSF knows {', '.join(PROVIDERS)}")
    pi = [a for a in cfg.agents if a.coding_agent == "pi"]
    other = [a.name for a in pi if not str(a.model).startswith("openai/")]
    if other:
        raise SystemExit(f"--sandbox runs pi agents on the ChatGPT sign-in only (openai/ models), "
                         f"and {', '.join(other)} use another provider, whose key would have to "
                         "go into the VM.")


def extensions_in_clone(cfg, root: Path, commit: str, tree: Path) -> None:
    """For a VM run, before its VM opens: every pi agent's harness_engineering
    path must be a file inside the repo at `root`, committed at `commit`, the
    run's starting commit. Each is then anchored to `tree`, where the run's
    branch is cloned in the VM, so pi loads it, and what it imports, from the
    clone (design decisions 21, 22). Imports aren't followed. Anything else
    stops the run, naming the agent and the path. Call it once per process:
    `tree` is inside `root`."""
    agents = [a for a in cfg.agents if a.coding_agent == "pi" and a.harness_engineering]
    if not agents:
        return
    if not commit:
        raise SystemExit("--sandbox can't check harness_engineering extensions: "
                         "the run's starting commit isn't known.")
    found = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}"],
                           cwd=root, capture_output=True, text=True)
    sha = found.stdout.strip()
    if found.returncode != 0 or not sha:
        raise SystemExit(f"--sandbox can't check harness_engineering extensions: "
                         f"the run's starting commit {commit} can't be read.")
    for agent in agents:
        moved = []
        for p in agent.harness_engineering:
            full = Path(os.path.normpath(p if Path(p).is_absolute() else root / p))
            try:
                rel = full.relative_to(root)
            except ValueError:
                rel = Path(".")
            if rel == Path("."):
                raise SystemExit(f"--sandbox loads {agent.name}'s harness_engineering extension {p} "
                                 f"from the VM's clone of the repo, and it is outside the repo ({root}). "
                                 "Move it into the repo and commit it, or run with --sandbox none.")
            parent = rel.parent.as_posix()
            listing = subprocess.run(["git", "ls-tree", "-z", f"{sha}:{'' if parent == '.' else parent}"],
                                     cwd=root, capture_output=True, text=True)
            ok = False
            if listing.returncode == 0:
                for entry in listing.stdout.split("\0"):
                    meta, _, name = entry.partition("\t")
                    fields = meta.split()
                    if (name == rel.name and len(fields) == 3 and fields[1] == "blob"
                            and fields[0] in ("100644", "100755")):
                        ok = True
            if not ok:
                raise SystemExit(f"--sandbox loads {agent.name}'s harness_engineering extension {p} "
                                 f"from the VM's clone, and it is not a file committed at the run's "
                                 f"starting commit ({sha[:12]}). Commit it before the run starts, "
                                 "or run with --sandbox none.")
            moved.append(str(tree / rel))
        agent.harness_engineering = moved


def open_remote(provider: str, run, branch: str, project: str, live: set[str]):
    """The run's remote sandbox, not yet opened, after deleting this project's
    VMs left by runs that aren't live. Returns it and what was deleted."""
    module = _provider(provider)
    try:
        swept = module.sweep(project, live)
    except Exception as error:
        raise SandboxFailed(f"could not list the project's {provider} VMs: {error}") from None
    return module.Box(run, branch, project), swept
