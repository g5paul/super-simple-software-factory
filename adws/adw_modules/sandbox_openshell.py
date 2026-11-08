"""A run's tree in a local OpenShell microVM. The work is in sandbox_remote; this
module makes and deletes the sandbox and puts the `openshell` CLI behind the
calls sandbox_remote makes, which follow Vercel's.

There is no SDK: every call is an `openshell` command. There is no session cap.
The image comes from scripts/openshell_image.py (`just openshell-setup`); it
bakes in one folder, shared by the repo and its worktrees, owned by the sandbox
user. The image also holds the repo's Python (`image_python`): uv installs it at
build time, because the sandbox can't download one. The tag covers both, so a
changed Python needs `just openshell-setup`. Denials from the sandbox's network policy go in the trace as
`sandbox_denied` events.

The Claude token, like every env value, travels on stdin in a framed block and
is exported by a shell inside the sandbox. It is never on a command line, on
the host or in the sandbox (anyone can read another process's argv).

`sweep` reads the adw_id from the sandbox's name, `sssf-<adw_id>-<hex4>`, which is
kept within the CLI's 19-character limit (`OpenShell.sandbox_name_max`), so it
needs no JSON from `sandbox list`, whose shape v0.1.7 doesn't document. The
`adw_id` label is still set. The `sssf`
label is `project_label(project)`: a readable prefix and a hash of the exact
name, so no two projects share one.
"""

from __future__ import annotations

import base64
import hashlib
import math
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
import types
import uuid
from pathlib import Path

import yaml

from .sandbox import SandboxFailed
from . import sandbox_remote
from .sandbox_remote import Remote

CLI = "openshell"
SANDBOX_USER = "ubuntu"
BASE_IMAGE = "nvcr.io/nvidia/base/ubuntu:24.04"
NODE = "24.11.1"
PYTHON_DIR = "/usr/local/share/uv/python"   # under /usr, which the policy reads
PYTHON_BIN = "/usr/local/bin"               # on PATH, so uv finds python3.<minor>
CLAUDE_EXE = "/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe"
ANY_BINARY = "/**"      # untested reading: the proof run checks it
READ_ONLY_PATHS = ["/bin", "/usr", "/lib", "/proc", "/dev/urandom", "/etc", "/var/log"]
REGISTRIES = ("pypi.org", "files.pythonhosted.org", "registry.npmjs.org")
CONFIG = "adws/adw_sssf_config/sssf.config.yaml"
DATA_DIR = "adws/adw_data"
READY_SECONDS = 180
SENTINEL = "sssf-openshell-exec-started"
SETUP = "just openshell-setup"
_SETUP_HINT = f"`{SETUP}` (or `just sssf openshell-setup` where SSSF's recipes are a module)"
_TIMEOUT = 30
LABEL_MAX = 63       # a Kubernetes label value's limit; the CLI's help gives none
LABEL_HASH = 12      # hex characters of the name's SHA-256 in the project label
KILL_SLACK = 30    # seconds past kill_after the CLI gets to return on its own


class OpenShellError(RuntimeError):
    """A failure of the openshell CLI itself, not of a command run through it."""


# --- the layout, shared with scripts/openshell_image.py ----------------------

def repo_root(cwd: Path) -> Path:
    """The main checkout of the repo `cwd` is in, also from a worktree."""
    out = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                         cwd=cwd, capture_output=True, text=True, timeout=_TIMEOUT, check=True)
    return Path(out.stdout.strip()).parent


def data_dir(root: Path) -> str:
    """`defaults.data_dir` from the repo's shipped config, or the default."""
    try:
        data = yaml.safe_load((root / CONFIG).read_text())
        value = data["defaults"]["data_dir"]
    except (OSError, yaml.YAMLError, KeyError, TypeError):
        return DATA_DIR
    return value if isinstance(value, str) and value else DATA_DIR


def shared_folder(root: Path, worktree_root: Path, home: Path) -> Path:
    """The deepest folder holding both, which must be strictly below `home`."""
    root, worktree_root, home = (p.resolve() for p in (root, worktree_root, home))
    try:
        common = Path(os.path.commonpath([root, worktree_root]))
    except ValueError:
        common = Path("/")
    if common == home or home not in common.parents:
        raise ValueError(f"the repo's root ({root}) and its worktree root ({worktree_root}) share "
                         f"no folder below the home folder ({home}), so the sandbox image can't "
                         "give the sandbox user one folder for both. Move the repo, or its data "
                         "folder, under the home folder.")
    return common


def folder_for(cwd: Path) -> Path:
    root = repo_root(cwd)
    return shared_folder(root, root / data_dir(root) / "worktrees", Path.home())


def image_python(path: Path) -> str:
    """The Python, as major.minor, the image holds for the repo `path` is in:
    what the host's uv picks in its main checkout, the value a run passes as
    UV_PYTHON (sandbox_remote._host_python). "" when the host has none."""
    return sandbox_remote._host_python(repo_root(path))


def containerfile(folder: Path, python: str | None = None) -> str:
    if python is None:
        python = image_python(folder)
    if python and not re.fullmatch(r"\d+\.\d+", python, re.ASCII):
        raise ValueError(f"not a Python major.minor: {python!r}")
    q = shlex.quote(str(folder))
    install = ([f"RUN UV_PYTHON_INSTALL_DIR={PYTHON_DIR} UV_PYTHON_BIN_DIR={PYTHON_BIN}"
                f" uv python install {python}"] if python else [])
    find = f" && uv python find {python}" if python else ""
    return "\n".join([
        f"FROM {BASE_IMAGE}",
        "RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends"
        " git curl ca-certificates python3 xz-utils && rm -rf /var/lib/apt/lists/*",
        f"RUN curl -fsSL https://nodejs.org/dist/v{NODE}/node-v{NODE}-linux-x64.tar.xz"
        " | tar -xJ -C /usr/local --strip-components=1",
        "RUN curl -LsSf https://astral.sh/uv/install.sh"
        " | UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh",
        *install,
        "RUN npm install -g @anthropic-ai/claude-code",
        f"RUN mkdir -p {q} && chown -R {SANDBOX_USER}:{SANDBOX_USER} {q}",
        "RUN node --version && uv --version && claude --version && git --version" + find,
        "",
    ])


def image_tag(folder: Path, python: str | None = None) -> str:
    return "localhost/sssf-openshell:" + hashlib.sha256(containerfile(folder, python).encode()).hexdigest()[:12]


def policy(folder: Path, allow: list) -> dict:
    def rule(host: str, port: int, access: str, binary: str) -> dict:
        return {"endpoints": [{"host": host, "port": port, "protocol": "rest",
                               "enforcement": "enforce", "access": access}],
                "binaries": [{"path": binary}]}

    registries = {"endpoints": [
        {"host": host, "port": 443, "protocol": "rest", "enforcement": "enforce",
         "access": "read-only", **({"allow_encoded_slash": True} if host == "registry.npmjs.org" else {})}
        for host in REGISTRIES], "binaries": [{"path": ANY_BINARY}]}
    rules = {"anthropic_api": rule("api.anthropic.com", 443, "full", CLAUDE_EXE),
             "package_registries": registries}
    for n, entry in enumerate(allow):
        rules[f"repo_allow_{n}"] = rule(entry.host, entry.port, entry.access,
                                        ANY_BINARY if entry.binary == "*" else entry.binary)
    return {"version": 1,
            # include_workdir stays: both policies the 2026-11-07 tests ran had it, and
            # Claude Code's home is most likely the workdir. The proof run checks it.
            "filesystem_policy": {"include_workdir": True, "read_only": list(READ_ONLY_PATHS),
                                  "read_write": ["/tmp", "/dev/null", str(folder)]},
            "landlock": {"compatibility": "hard_requirement"},
            "network_policies": rules}


def policy_yaml(folder: Path, allow: list) -> str:
    return yaml.safe_dump(policy(folder, allow), sort_keys=False)


# --- require_sdk, sweep ------------------------------------------------------

def _last(text: str) -> str:
    lines = (text or "").strip().splitlines()
    return lines[-1] if lines else ""


def require_sdk() -> None:
    if shutil.which(CLI) is None:
        raise SystemExit(f"--sandbox openshell: the openshell CLI isn't on PATH. Run {_SETUP_HINT}.")
    try:
        done = subprocess.run([CLI, "status"], capture_output=True, text=True, timeout=_TIMEOUT)
        failed, why = done.returncode != 0, _last(done.stderr)
    except (OSError, subprocess.TimeoutExpired) as error:
        failed, why = True, str(error)
    if failed:
        raise SystemExit(f"--sandbox openshell: no OpenShell gateway answers ({why}). Run {_SETUP_HINT}.")
    try:
        folder = folder_for(Path.cwd())
        python = image_python(Path.cwd())
    except (ValueError, subprocess.SubprocessError, OSError) as error:
        raise SystemExit(f"--sandbox openshell: {error}. See {_SETUP_HINT}.")
    tag = image_tag(folder, python)
    try:
        have = subprocess.run(["podman", "image", "exists", tag], capture_output=True,
                              timeout=_TIMEOUT).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        have = False
    if not have:
        raise SystemExit(f"--sandbox openshell: the sandbox image {tag} isn't built"
                         f"{f' (with Python {python}, the Python uv picks in this repo)' if python else ''}. "
                         f"Run {_SETUP_HINT}.")


def label(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "-", value)[:63]


def project_label(project: str) -> str:
    """The `sssf=` label for a project: its name cleaned to [A-Za-z0-9_.-] and cut,
    then `-` and 12 hex of the SHA-256 of the exact name, so distinct names get
    distinct labels. At most 63 characters, starting and ending with a letter or
    digit."""
    digest = hashlib.sha256(project.encode("utf-8", "surrogatepass")).hexdigest()[:LABEL_HASH]
    prefix = re.sub(r"[^A-Za-z0-9_.-]", "-", project)[:LABEL_MAX - LABEL_HASH - 1]
    prefix = re.sub(r"^[^A-Za-z0-9]+", "", prefix)
    return f"{prefix}-{digest}" if prefix else digest


def sweep(project: str, live: set[str]) -> list[str]:
    """Delete this project's SSSF sandboxes whose run isn't live. Returns their names
    and ids. The id is read from the name; a name that doesn't parse is left alone."""
    listed = subprocess.run([CLI, "sandbox", "list", "--selector", f"sssf={project_label(project)}",
                             "--names", "--page-size", "1000"],
                            capture_output=True, text=True, timeout=_TIMEOUT)
    if listed.returncode != 0:
        raise OpenShellError(f"openshell sandbox list failed: {_last(listed.stderr)}")
    gone = []
    for name in listed.stdout.split():
        if not re.fullmatch(r"sssf-.+-[0-9a-f]{4}", name):
            continue
        adw_id = name[len("sssf-"):].rsplit("-", 1)[0]
        if adw_id in live:
            continue
        done = subprocess.run([CLI, "sandbox", "delete", name], capture_output=True,
                              text=True, timeout=_TIMEOUT)
        if done.returncode != 0:
            raise OpenShellError(f"openshell sandbox delete {name} failed: {_last(done.stderr)}")
        gone.append(f"{name} ({adw_id})")
    return gone


# --- exec: the env frame, the sh line, the sentinel ---------------------------

_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _frame(env: dict) -> bytes:
    """Each item as `KEY=<base64 of the value>`, then a `.` line."""
    lines = []
    for key, value in env.items():
        if not _KEY.fullmatch(key):
            raise ValueError(f"not an env name: {key!r}")
        if "\0" in value:
            raise ValueError(f"env {key} holds a NUL byte")
        lines.append(f"{key}=" + base64.b64encode(value.encode()).decode() + "\n")
    return ("".join(lines) + ".\n").encode()


_SHELL = r"""while IFS= read -r l && [ "$l" != . ]; do
  k=${l%%=*}; v=$(printf %s "${l#*=}" | base64 -d; echo x); export "$k=${v%x}"
done
printf '%s\n' SENTINEL >&2
m=$1; shift
if [ "$m" = null ]; then exec "$@" </dev/null; else exec "$@"; fi""".replace("SENTINEL", SENTINEL)


def _exec_argv(name: str, cwd, timeout) -> list[str]:
    return [CLI, "sandbox", "exec", "-n", name, "--no-tty", "--no-login-shell",
            *(["--workdir", str(cwd)] if cwd else []),
            *(["--timeout", str(math.ceil(timeout))] if timeout else []),
            "--", "sh", "-c", _SHELL, "sh"]


def _strip(err: str) -> tuple[str, bool]:
    """stderr without its first line when that is the sentinel, and whether it was."""
    first, nl, rest = err.partition("\n")
    return (rest, True) if nl and first == SENTINEL else (err, False)


def _judge(code: int, seen: bool, err: str) -> int:
    if not seen:
        raise OpenShellError(f"openshell exec failed (exit {code}, the command never started): "
                             f"{err.strip()[-500:]}")
    return code


class _Stream:
    """A background command's output as lines. stderr is drained by a thread; its
    first line is the sentinel when the command started."""

    def __init__(self, popen: subprocess.Popen):
        self._p = popen
        self._err: list[str] = []
        self.stdout = iter(popen.stdout.readline, "")
        self.stderr = self
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._thread.start()

    def _drain(self) -> None:
        self._err = list(self._p.stderr)

    def _text(self) -> tuple[str, bool]:
        self._thread.join()
        return _strip("".join(self._err))

    def read(self) -> str:
        return self._text()[0]

    def wait(self) -> int:
        code = self._p.wait()
        err, seen = self._text()
        return _judge(code, seen, err)


class _VM:
    """An OpenShell sandbox behind run_process, create_process, stop and destroy."""

    def __init__(self, name: str):
        self.name = name

    def run_process(self, command: str, args=(), *, cwd=None, env=None, sudo=False,
                    capture_output=True, input=None, kill_after=None):
        data = _frame(env or {})
        if input is not None:
            data += input.encode() if isinstance(input, str) else bytes(input)
        popen = subprocess.Popen(
            _exec_argv(self.name, cwd, kill_after) + ["input" if input is not None else "null", command, *args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            out, err = popen.communicate(data, timeout=kill_after + KILL_SLACK if kill_after else None)
        except subprocess.TimeoutExpired:
            popen.kill()
            popen.communicate()
            return types.SimpleNamespace(returncode=124, stdout="", stderr="openshell exec timed out")
        err_text, seen = _strip(err.decode("utf-8", errors="replace"))
        code = _judge(popen.returncode, seen, err_text)
        return types.SimpleNamespace(returncode=code, stdout=out.decode("utf-8", errors="replace"),
                                     stderr=err_text)

    def create_process(self, command: str, args=(), *, cwd=None, env=None) -> _Stream:
        frame = _frame(env or {})
        popen = subprocess.Popen(_exec_argv(self.name, cwd, None) + ["null", command, *args],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, bufsize=1, errors="replace")
        popen.stdin.write(frame.decode())
        popen.stdin.close()
        return _Stream(popen)

    def stop(self) -> None:
        """No session cap, so nothing to refresh."""

    def destroy(self) -> None:
        done = subprocess.run([CLI, "sandbox", "delete", self.name], capture_output=True,
                              text=True, timeout=_TIMEOUT)
        if done.returncode != 0:
            raise OpenShellError(f"openshell sandbox delete {self.name} failed: {_last(done.stderr)}")


class OpenShell(Remote):
    name = "openshell"
    keys = ()
    session_minutes = 24 * 60
    # The CLI's limit on a sandbox name; its --help gives none. OpenShell 0.1.7 said:
    # "name exceeds maximum length (20 > 19)"
    sandbox_name_max = 19
    owner = SANDBOX_USER
    errors = (OpenShellError,)

    def _create(self) -> _VM:
        name = f"sssf-{self.run_.adw_id}-{uuid.uuid4().hex[:4]}"
        if len(name) > self.sandbox_name_max:
            raise SandboxFailed(
                f"the OpenShell sandbox name {name} is {len(name)} characters, and the "
                f"openshell CLI takes at most {self.sandbox_name_max}. The run's id "
                f"{self.run_.adw_id} is too long for it.")
        from . import repo_settings
        try:
            folder = folder_for(self.tree)
            python = image_python(self.tree)
        except (ValueError, subprocess.SubprocessError, OSError) as error:
            raise OpenShellError(str(error))
        for path in (self.tree, self.handoff, self.run_.session_dir):
            if folder != Path(path).resolve() and folder not in Path(path).resolve().parents:
                raise OpenShellError(f"{path} is outside the sandbox image's folder {folder}. "
                                     f"Run {_SETUP_HINT} for this layout.")
        file = Path(self.run_.session_dir) / "openshell-policy.yaml"
        file.write_text(policy_yaml(folder, repo_settings.load(repo_root(self.tree)).sandbox_allow))
        try:
            done = subprocess.run(
                [CLI, "sandbox", "create", "--name", name, "--from", image_tag(folder, python),
                 "--policy", str(file), "--label", f"sssf={project_label(self.project)}",
                 "--label", f"adw_id={label(self.run_.adw_id)}", "--detach", "--no-tty",
                 "--no-auto-providers"],
                capture_output=True, text=True, timeout=READY_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise OpenShellError(f"openshell sandbox create: {error}")
        if done.returncode != 0:
            raise OpenShellError(f"openshell sandbox create failed: {_last(done.stderr)}")
        vm = _VM(name)
        end = time.monotonic() + READY_SECONDS
        while True:
            try:
                if vm.run_process("true", kill_after=10).returncode == 0:
                    return vm
            except OpenShellError:
                pass
            if time.monotonic() > end:
                raise OpenShellError(f"sandbox {name} wasn't ready in {READY_SECONDS}s")
            time.sleep(2)

    def _delete(self) -> None:
        self.sandbox.destroy()

    def _call_end(self, started: float) -> None:
        """Log the denials from the call's window. Never raises."""
        try:
            done = subprocess.run(
                [CLI, "logs", self.vm, "--source", "sandbox",
                 "--since", f"{math.ceil(time.time() - started) + 2}s", "-n", "10000"],
                capture_output=True, text=True, timeout=_TIMEOUT)
            if done.returncode != 0:
                raise OpenShellError(_last(done.stderr))
            for line in done.stdout.splitlines():
                if " DENIED " not in line:
                    continue
                m = re.search(r"DENIED\s+(\S+?)(?:\(\d+\))?\s+->\s+(\S+)", line)
                self._log("sandbox_denied", {"vm": self.vm, "binary": m.group(1) if m else "",
                                             "destination": m.group(2) if m else "",
                                             "line": line.strip()[-500:]})
        except Exception as error:
            try:
                self._log("sandbox_error", {"vm": self.vm,
                                            "error": f"denial log read failed: {error}"[-500:]})
            except Exception:
                pass


Box = OpenShell
