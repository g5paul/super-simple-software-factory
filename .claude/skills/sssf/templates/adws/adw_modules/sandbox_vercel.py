"""A run's tree in a Vercel Sandbox VM (docs/designs/remote-sandboxes.md).

The ADW stays on the host. The VM holds a clone of the run's branch at the
same absolute path as the host worktree, and the run's context_handoff folder
at its host path too, so every path the run passes means the same thing on
both sides. Each command goes through `run` or `start` below:

- `run` is a check, git, or setup. It gets SSSF's own variables, never the
  Claude token.
- `start` is an agent. It gets the token from ~/.config/sssf-sandbox/token.env,
  and the handoff folder is copied in before it and back out after it.
- After each `git commit`, the new commits come back to the host branch as a
  git bundle, so the host keeps them if the VM dies.
- At the end, uncommitted work is saved as a patch in the run's folder, not
  applied, and the VM is deleted.

Hobby sessions last 45 minutes. Before a command that might not fit in what
is left, the VM is stopped; the next call resumes it with a fresh session and
the files kept. SSSF_SANDBOX_SESSION_MINUTES shortens the session, for tests.
"""

from __future__ import annotations

import base64
import io
import os
import socket
import subprocess
import sys
import tarfile
import time
import uuid
from datetime import timedelta
from pathlib import Path

REQUIREMENT = "vercel-sandbox==0.10.0"
TOKEN_FILE = Path.home() / ".config" / "sssf-sandbox" / "token.env"
KEYS = ("VERCEL_TOKEN", "VERCEL_TEAM_ID", "VERCEL_PROJECT_ID")
PASSED = ("SSSF_", "OBS_")          # the only host variables a VM command gets
AGENT_SECONDS = 30 * 60             # what an agent call may need of a session
COMMAND_SECONDS = 10 * 60           # what a command with no timeout may need
MARGIN_SECONDS = 30


from .sandbox import SandboxFailed


def require_sdk() -> None:
    """Make sure `vercel-sandbox` can be imported, before the run starts.

    The chains don't declare it, so a run without --sandbox never fetches it.
    A sandbox run without it starts again once, under `uv run --with`."""
    try:
        import vercel.sandbox.sync  # noqa: F401
    except ImportError:
        if os.environ.get("SSSF_SANDBOX_SDK"):
            raise SystemExit(f"--sandbox vercel needs {REQUIREMENT}, and uv could not add it")
        os.environ["SSSF_SANDBOX_SDK"] = "1"
        os.execvp("uv", ["uv", "run", "--quiet", "--with", REQUIREMENT, sys.argv[0], *sys.argv[1:]])


def _token() -> str:
    if not TOKEN_FILE.is_file():
        raise SandboxFailed(f"no Claude token at {TOKEN_FILE}. Run `claude setup-token` in a "
                            "terminal and save CLAUDE_CODE_OAUTH_TOKEN=<token> there, mode 600.")
    for line in TOKEN_FILE.read_text().splitlines():
        key, _, value = line.strip().partition("=")
        if key == "CLAUDE_CODE_OAUTH_TOKEN" and value.strip():
            return value.strip().strip('"').strip("'")
    raise SandboxFailed(f"{TOKEN_FILE} has no CLAUDE_CODE_OAUTH_TOKEN line")


def _credentials() -> None:
    missing = [k for k in KEYS if not os.environ.get(k)]
    if missing:
        raise SandboxFailed(f"{', '.join(missing)} not set. Put them in the repo's .env; "
                            "see docs/designs/remote-sandboxes.md, decision 19.")
    if os.environ.get("ANTHROPIC_API_KEY"):
        raise SandboxFailed("ANTHROPIC_API_KEY is set, and in the VM it would win over the "
                            "subscription token. Unset it before a sandbox run.")


def _host_git(*args: str, cwd: Path) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if done.returncode != 0:
        raise SandboxFailed(f"git {' '.join(args)} failed on the host: {done.stderr.strip()}")
    return done.stdout.strip()


def _tar(folder: Path) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        tar.add(folder, arcname=".")
    return buffer.getvalue()


def sweep(project: str, live: set[str]) -> list[str]:
    """Delete this project's SSSF VMs whose run isn't live. Returns their names."""
    from vercel.sandbox.sync import query_sandboxes
    gone = []
    for box in query_sandboxes():
        tags = box.tags or {}
        if tags.get("sssf") == project and tags.get("adw_id") not in live:
            box.destroy()
            gone.append(f"{box.name} ({tags.get('adw_id')})")
    return gone


class VercelProcess:
    """An agent in the VM. Its pid is a negative stand-in, unique in the run:
    the trace records it, and `just kill` knows not to signal it on the host."""

    def __init__(self, box: "Vercel", process, sid_file: str, pid: int):
        self._box, self._process, self._sid_file = box, process, sid_file
        self.pid = pid
        self.stdout = process.stdout
        self.stderr = process.stderr
        self._done = False

    def wait(self) -> int:
        code = self._process.wait()
        if not self._done:
            self._done = True
            self._box.handoff_out()
        return code

    def kill(self) -> None:
        self.stop_tree()

    def stop_tree(self) -> None:
        # Started under setsid, so the agent and everything it ran share its session.
        self._box.sandbox.run_process(
            "sh", ["-c", f'pkill -KILL -s "$(cat {self._sid_file})"'], capture_output=True)


class Vercel:
    name = "vercel"

    def __init__(self, run, branch: str, project: str):
        self.run_ = run
        self.tree = Path.cwd()
        self.handoff = Path(run.context_handoff_dir)
        self.branch = branch
        self.project = project
        self.limit = float(os.environ.get("SSSF_SANDBOX_SESSION_MINUTES") or 45) * 60
        self.sandbox = None
        self.vm = ""
        self.refreshes = 0
        self.processes = 0
        self.synced = ""
        self._token = ""
        self._created = 0.0
        self._session = 0.0

    # --- lifecycle -------------------------------------------------------

    def open(self) -> None:
        """Make the VM and put the run's branch in it. Raises SandboxFailed."""
        from vercel.sandbox.sync import SandboxError, create_sandbox
        _credentials()
        self._token = _token()
        try:
            self.sandbox = create_sandbox(
                tags={"sssf": self.project, "adw_id": self.run_.adw_id},
                execution_time_limit=timedelta(seconds=self.limit), persistent=True)
            self.vm = self.sandbox.name
            self._created = self._session = time.monotonic()
            self._log("sandbox", {"provider": self.name, "vm": self.vm,
                                  "session_minutes": self.limit / 60})
            self._clone()
            self.handoff_in()
        except SandboxError as error:
            raise SandboxFailed(f"Vercel would not make or set up the VM: {error}") from None

    def _clone(self) -> None:
        bundle = self.run_.session_dir / "sandbox-in.bundle"
        _host_git("bundle", "create", "-q", str(bundle), self.branch, cwd=self.tree)
        name = _host_git("config", "user.name", cwd=self.tree)
        email = _host_git("config", "user.email", cwd=self.tree)
        self._must("sh", ["-c", 'mkdir -p "$1" "$2" && chown -R ubuntu "$1" "$2"',
                          "sh", str(self.tree), str(self.handoff)], sudo=True)
        self._must("sh", ["-c", "cat > /tmp/sssf-in.bundle"], input=bundle.read_bytes())
        self._must("git", ["clone", "-q", "--branch", self.branch, "/tmp/sssf-in.bundle", str(self.tree)])
        for args in (["remote", "remove", "origin"], ["config", "user.name", name],
                     ["config", "user.email", email]):
            self._must("git", args, cwd=str(self.tree))
        self.synced = self._must("git", ["rev-parse", "HEAD"], cwd=str(self.tree)).strip()
        bundle.unlink()

    def close(self) -> None:
        """Bring back what the run made, then delete the VM. Never raises."""
        if self.sandbox is None:
            return
        patch = ""
        try:
            self.commits_out()
            done = self.sandbox.run_process(
                "sh", ["-c", "git add -A && git diff --cached --binary HEAD"],
                cwd=str(self.tree), capture_output=True)
            if done.returncode == 0 and (done.stdout or "").strip():
                path = self.run_.session_dir / "sandbox-uncommitted.patch"
                path.write_text(done.stdout)
                patch = str(path)
        except Exception as error:   # the VM may already be gone; deleting it still matters
            self._log("sandbox_error", {"vm": self.vm, "error": str(error)[-500:]})
        seconds = round(time.monotonic() - self._created, 1)
        try:
            self.sandbox.destroy()
            deleted = True
        except Exception as error:
            deleted = False
            self._log("sandbox_error", {"vm": self.vm, "error": f"delete failed: {error}"[-500:]})
        self._log("sandbox_end", {"provider": self.name, "vm": self.vm, "vm_seconds": seconds,
                                  "refreshes": self.refreshes, "deleted": deleted,
                                  "uncommitted_patch": patch})
        if patch:
            self.run_.console.note(f"uncommitted work from the VM is saved, not applied: {patch}")
        self.sandbox = None

    # --- the Sandbox protocol -----------------------------------------------

    def run(self, command, *, cwd=None, env=None, timeout=None,
            shell: bool = False, text: bool = True) -> subprocess.CompletedProcess:
        argv = ["sh", "-c", command] if shell else ([command] if isinstance(command, str) else list(command))
        self._refresh(timeout or COMMAND_SECONDS)
        started = time.monotonic()
        done = self._call(self.sandbox.run_process, argv[0], argv[1:], cwd=self._cwd(cwd),
                          env=self._env(env), capture_output=True, kill_after=timeout)
        out, err = done.stdout or "", done.stderr or ""
        if timeout and done.returncode != 0 and time.monotonic() - started >= timeout - 1:
            raise subprocess.TimeoutExpired(command, timeout, output=out, stderr=err)
        if argv[:2] == ["git", "commit"] and done.returncode == 0:
            self.commits_out()
        if not text:
            out, err = out.encode(), err.encode()
        return subprocess.CompletedProcess(command, done.returncode, out, err)

    def start(self, command: list[str], *, cwd=None, env=None) -> VercelProcess:
        argv = list(command)
        if Path(argv[0]).is_absolute():
            argv[0] = Path(argv[0]).name        # the host's install path means nothing in the VM
        self._refresh(min(AGENT_SECONDS, self.limit - 2 * MARGIN_SECONDS))
        self.handoff_in()
        self.processes += 1
        sid_file = f"/tmp/sssf-{uuid.uuid4().hex}.sid"
        agent_env = {**self._env(env), "CLAUDE_CODE_OAUTH_TOKEN": self._token}
        process = self._call(self.sandbox.create_process, "setsid",
                             ["sh", "-c", 'echo $$ > "$0"; exec "$@"', sid_file, *argv],
                             cwd=self._cwd(cwd), env=agent_env)
        return VercelProcess(self, process, sid_file, -self.processes)

    def exists(self, path) -> bool:
        return self._test("-e", path)

    def is_file(self, path) -> bool:
        return self._test("-f", path)

    def size(self, path) -> int:
        done = self._call(self.sandbox.run_process, "stat", ["-c", "%s", self._path(path)],
                          capture_output=True)
        if done.returncode != 0:
            raise FileNotFoundError(path)
        return int(done.stdout.strip())

    def read_text(self, path) -> str:
        done = self._call(self.sandbox.run_process, "cat", [self._path(path)], capture_output=True)
        if done.returncode != 0:
            raise FileNotFoundError(path)
        return done.stdout or ""

    def remove(self, path) -> None:
        self._call(self.sandbox.run_process, "rm", ["-f", self._path(path)], capture_output=True)

    # --- moving work between the VM and the host -----------------------------

    def handoff_in(self) -> None:
        self._must("tar", ["-xzf", "-", "-C", str(self.handoff)], input=_tar(self.handoff))

    def handoff_out(self) -> None:
        packed = self._must("sh", ["-c", 'tar -czf - -C "$0" . | base64 -w0', str(self.handoff)])
        with tarfile.open(fileobj=io.BytesIO(base64.b64decode(packed)), mode="r:gz") as tar:
            tar.extractall(self.handoff, filter="data")

    def commits_out(self) -> None:
        """Fast-forward the host branch to the VM's, through a bundle of the new commits."""
        head = self._must("git", ["rev-parse", "HEAD"], cwd=str(self.tree)).strip()
        if head == self.synced:
            return
        packed = self._must("sh", ["-c", f"git bundle create -q - {self.synced}..HEAD | base64 -w0"],
                            cwd=str(self.tree))
        bundle = self.run_.session_dir / "sandbox-out.bundle"
        bundle.write_bytes(base64.b64decode(packed))
        _host_git("fetch", "-q", str(bundle), "HEAD", cwd=self.tree)
        _host_git("merge", "-q", "--ff-only", "FETCH_HEAD", cwd=self.tree)
        bundle.unlink()
        count = _host_git("rev-list", "--count", f"{self.synced}..{head}", cwd=self.tree)
        self._log("sandbox_commits", {"vm": self.vm, "count": int(count), "head": head[:12]})
        self.synced = head

    # --- helpers ------------------------------------------------------------

    def _refresh(self, need: float) -> None:
        """Stop the VM when `need` seconds won't fit in this session. The next
        call resumes it in a new session, with its files."""
        if time.monotonic() - self._session + need <= self.limit - MARGIN_SECONDS:
            return
        self._call(self.sandbox.stop)
        self._session = time.monotonic()
        self.refreshes += 1
        self._log("sandbox_refresh", {"vm": self.vm, "refreshes": self.refreshes})

    def _env(self, env) -> dict[str, str]:
        source = os.environ if env is None else env
        return {k: v for k, v in source.items() if k.startswith(PASSED)}

    def _cwd(self, cwd) -> str:
        return str(Path(cwd).resolve()) if cwd else os.getcwd()

    def _path(self, path) -> str:
        return str(Path(os.getcwd(), path))

    def _test(self, flag: str, path) -> bool:
        return self._call(self.sandbox.run_process, "test", [flag, self._path(path)],
                          capture_output=True).returncode == 0

    def _must(self, command: str, args: list[str], **kwargs) -> str:
        done = self._call(self.sandbox.run_process, command, args, capture_output=True, **kwargs)
        if done.returncode != 0:
            raise SandboxFailed(f"{command} {' '.join(args)[:200]} failed in VM {self.vm}: "
                                f"{(done.stderr or done.stdout or '').strip()[-500:]}")
        return done.stdout or ""

    def _call(self, method, *args, **kwargs):
        """An SDK call, with its errors as OSError, as a subprocess's would be."""
        from vercel.sandbox.sync import SandboxError
        try:
            return method(*args, **kwargs)
        except SandboxError as error:
            raise OSError(f"Vercel sandbox {self.vm}: {error}") from error

    def _log(self, name: str, payload: dict) -> None:
        from .data_types import EventRecord
        self.run_.tracer.event(EventRecord(adw_id=self.run_.adw_id, phase_id="", type="log",
                                           name=name, payload={**payload, "host": socket.gethostname()}))
