"""A run's tree in a remote VM (docs/designs/remote-sandboxes.md).

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

A provider (sandbox_vercel, sandbox_e2b) makes the VM and hands back an
object with Vercel's SDK calls: run_process, create_process, stop, destroy.
Sessions are capped, so before a command that might not fit in what is left,
the VM is stopped and resumed with a fresh session and its files kept.
SSSF_SANDBOX_SESSION_MINUTES shortens the session, for tests.
"""

from __future__ import annotations

import base64
import importlib
import io
import os
import socket
import subprocess
import sys
import tarfile
import time
import uuid
from pathlib import Path

from .sandbox import SandboxFailed

TOKEN_FILE = Path.home() / ".config" / "sssf-sandbox" / "token.env"
PASSED = ("SSSF_", "OBS_")          # the host variables a VM command gets, with UV_PYTHON
AGENT_SECONDS = 30 * 60             # what an agent call may need of a session
COMMAND_SECONDS = 60                # what a command with no timeout may need: git, a read
MARGIN_SECONDS = 30
CPU_READ = ("awk -v hz=\"$(getconf CLK_TCK)\" "
            "'/^cpu /{printf \"%.2f\\n\", ($2+$3+$4+$7+$8+$9)/hz; exit}' /proc/stat")


def require_sdk(provider: str, module: str, requirement: str) -> None:
    """Make sure the provider's SDK can be imported, before the run starts.

    The chains don't declare it, so a run without --sandbox never fetches it.
    A sandbox run without it starts again once, under `uv run --with`."""
    try:
        importlib.import_module(module)
    except ImportError:
        if os.environ.get("SSSF_SANDBOX_SDK"):
            raise SystemExit(f"--sandbox {provider} needs {requirement}, and uv could not add it")
        os.environ["SSSF_SANDBOX_SDK"] = "1"
        os.execvp("uv", ["uv", "run", "--quiet", "--with", requirement, sys.argv[0], *sys.argv[1:]])


def _token() -> str:
    if not TOKEN_FILE.is_file():
        raise SandboxFailed(f"no Claude token at {TOKEN_FILE}. Run `claude setup-token` in a "
                            "terminal and save CLAUDE_CODE_OAUTH_TOKEN=<token> there, mode 600.")
    for line in TOKEN_FILE.read_text().splitlines():
        key, _, value = line.strip().partition("=")
        if key == "CLAUDE_CODE_OAUTH_TOKEN" and value.strip():
            return value.strip().strip('"').strip("'")
    raise SandboxFailed(f"{TOKEN_FILE} has no CLAUDE_CODE_OAUTH_TOKEN line")


def credentials(keys: tuple[str, ...]) -> None:
    missing = [k for k in keys if not os.environ.get(k)]
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


def _host_python(tree: Path) -> str:
    """The Python, as major.minor, that the host's uv picks in the tree.

    The VM's uv would pick its own: a `.python-version` is often untracked,
    so the clone doesn't have it, and the VM's image has another Python. A
    repo's tests can depend on the version, as argparse's help text does.
    "" when the host has no uv or no Python."""
    try:
        found = subprocess.run(["uv", "python", "find"], cwd=tree, capture_output=True,
                               text=True, timeout=60)
        if found.returncode != 0:
            return ""
        version = subprocess.run([found.stdout.strip(), "-c",
                                  "import sys; print('%d.%d' % sys.version_info[:2])"],
                                 capture_output=True, text=True, timeout=60)
        return version.stdout.strip() if version.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _tar(folder: Path) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        tar.add(folder, arcname=".")
    return buffer.getvalue()


class RemoteProcess:
    """An agent in the VM. Its pid is a negative stand-in, unique in the run:
    the trace records it, and `just kill` knows not to signal it on the host."""

    def __init__(self, box: "Remote", process, sid_file: str, pid: int):
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


class Remote:
    """What every provider shares. A provider sets the class attributes and
    writes `_create` and `_delete`."""

    name = ""
    keys: tuple[str, ...] = ()
    session_minutes = 45
    owner = ""                  # the VM's own user, who gets the host paths
    errors: tuple[type[BaseException], ...] = ()

    def __init__(self, run, branch: str, project: str):
        self.run_ = run
        self.tree = Path.cwd()
        self.handoff = Path(run.context_handoff_dir)
        self.branch = branch
        self.project = project
        self.limit = float(os.environ.get("SSSF_SANDBOX_SESSION_MINUTES") or self.session_minutes) * 60
        self.sandbox = None
        self.vm = ""
        self.refreshes = 0
        self.processes = 0
        self.installed: set[str] = set()    # paths install() has copied in
        self.synced = ""
        self.python = ""
        self._token = ""
        self._created = 0.0
        self._session = 0.0
        self.cpu_seconds: float | None = 0.0
        self._cpu_base: float | None = None

    # --- what a provider writes ---------------------------------------------

    def _create(self):
        """Make the VM, tagged with the project and adw_id. Returns the handle."""
        raise NotImplementedError

    def _delete(self) -> None:
        raise NotImplementedError

    def _end(self, seconds: float) -> dict:
        """What the provider adds to the run's `sandbox_end` event."""
        return {}

    # --- lifecycle -------------------------------------------------------

    def open(self) -> None:
        """Make the VM and put the run's branch in it. Raises SandboxFailed."""
        credentials(self.keys)
        self._token = _token()
        self.python = _host_python(self.tree)
        try:
            self.sandbox = self._create()
            self.vm = self.sandbox.name
            self._created = self._session = time.monotonic()
            self._cpu_base = self._cpu_now()
            self._log("sandbox", {"provider": self.name, "vm": self.vm,
                                  "session_minutes": self.limit / 60, "python": self.python})
            self._clone()
            self.handoff_in()
        except self.errors as error:
            raise SandboxFailed(f"{self.name} would not make or set up the VM: {error}") from None

    def _clone(self) -> None:
        bundle = self.run_.session_dir / "sandbox-in.bundle"
        _host_git("bundle", "create", "-q", str(bundle), self.branch, cwd=self.tree)
        name = _host_git("config", "user.name", cwd=self.tree)
        email = _host_git("config", "user.email", cwd=self.tree)
        self._must("sh", ["-c", f'mkdir -p "$1" "$2" && chown -R {self.owner} "$1" "$2"',
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
        self._cpu_add()
        seconds = round(time.monotonic() - self._created, 1)
        try:
            self._delete()
            deleted = True
        except Exception as error:
            deleted = False
            self._log("sandbox_error", {"vm": self.vm, "error": f"delete failed: {error}"[-500:]})
        self._log("sandbox_end", {"provider": self.name, "vm": self.vm, "vm_seconds": seconds,
                                  "refreshes": self.refreshes, "deleted": deleted,
                                  "uncommitted_patch": patch,
                                  "cpu_seconds": None if self.cpu_seconds is None else round(self.cpu_seconds, 1),
                                  **self._end(seconds)})
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

    def start(self, command: list[str], *, cwd=None, env=None) -> RemoteProcess:
        argv = list(command)
        if Path(argv[0]).is_absolute():
            argv[0] = Path(argv[0]).name        # the host's install path means nothing in the VM
        self._refresh(AGENT_SECONDS)
        self.handoff_in()
        self.processes += 1
        sid_file = f"/tmp/sssf-{uuid.uuid4().hex}.sid"
        agent_env = {**self._env(env), "CLAUDE_CODE_OAUTH_TOKEN": self._token}
        process = self._call(self.sandbox.create_process, "setsid",
                             ["sh", "-c", 'echo $$ > "$0"; exec "$@"', sid_file, *argv],
                             cwd=self._cwd(cwd), env=agent_env)
        return RemoteProcess(self, process, sid_file, -self.processes)

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

    def write_text(self, path, text: str) -> None:
        self._must("sh", ["-c", 'cat > "$0"', self._path(path)], input=text.encode())

    def install(self, path) -> None:
        """Copy one of SSSF's own host files into the VM at the same path,
        executable, once per VM. The VM's clone holds the repo's committed
        files, which can lag the skill the host is running."""
        where = self._path(path)
        if where in self.installed:
            return
        self._must("sh", ["-c", 'mkdir -p "$(dirname "$0")" && cat > "$0" && chmod 755 "$0"', where],
                   input=Path(path).read_bytes())
        self.installed.add(where)

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
        """Stop the VM when `need` seconds won't fit in this session, so it
        resumes in a new session, with its files.

        No command may ask for more than half a session, so a fresh one always
        has room for it. Otherwise a short session would stop the VM before
        every command, and Vercel limits how fast VMs resume (40 vCPUs a
        minute on Hobby)."""
        need = min(need, self.limit / 2)
        if time.monotonic() - self._session + need <= self.limit - MARGIN_SECONDS:
            return
        self._cpu_add()
        self._call(self.sandbox.stop)
        self._session = time.monotonic()
        self._cpu_base = self._cpu_now()
        self.refreshes += 1
        self._log("sandbox_refresh", {"vm": self.vm, "refreshes": self.refreshes})

    def _cpu_now(self) -> float | None:
        """The VM's busy CPU seconds since it booted, from /proc/stat: user,
        nice, system, irq, softirq and steal, summed over its vCPUs. Waiting
        is left out, as Vercel leaves it out of active CPU. None if unread."""
        try:
            done = self.sandbox.run_process("sh", ["-c", CPU_READ], capture_output=True)
            return float(done.stdout.strip()) if done.returncode == 0 else None
        except Exception:
            return None

    def _cpu_add(self) -> None:
        """Add this session's CPU to the run's. A reading that fails makes the
        run's total unknown, never wrong. A count lower than its base means
        the VM booted again, and all of it is this session's."""
        now = self._cpu_now() if self.cpu_seconds is not None else None
        if now is None or self._cpu_base is None:
            self.cpu_seconds = None
        else:
            self.cpu_seconds += now - self._cpu_base if now >= self._cpu_base else now
        self._cpu_base = None

    def _env(self, env) -> dict[str, str]:
        source = os.environ if env is None else env
        passed = {k: v for k, v in source.items() if k.startswith(PASSED)}
        # SSSF's refusing shell (sssf_refuse.py), which agent_cc installs in the
        # VM. An operator's own prefix names a host path, so it stays behind.
        if "SSSF_REFUSE_RULES" in passed and source.get("CLAUDE_CODE_SHELL_PREFIX"):
            passed["CLAUDE_CODE_SHELL_PREFIX"] = source["CLAUDE_CODE_SHELL_PREFIX"]
        # A pi agent's ChatGPT token (agent_pi.vm_token), under the name pi reads.
        # Only this name crosses: an OPENAI_API_KEY on the host stays there.
        if "SSSF_PI_TOKEN" in passed:
            passed["OPENAI_API_KEY"] = passed.pop("SSSF_PI_TOKEN")
        return {"UV_PYTHON": self.python, **passed} if self.python else passed

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
        try:
            return method(*args, **kwargs)
        except self.errors as error:
            raise OSError(f"{self.name} sandbox {self.vm}: {error}") from error

    def _log(self, name: str, payload: dict) -> None:
        from .data_types import EventRecord
        self.run_.tracer.event(EventRecord(adw_id=self.run_.adw_id, phase_id="", type="log",
                                           name=name, payload={**payload, "host": socket.gethostname()}))
