"""A run's tree in an E2B sandbox. The work is in sandbox_remote; this module
makes, refreshes and deletes the VM, and puts E2B's SDK behind the calls
sandbox_remote makes, which follow Vercel's.

The VM comes from SSSF's template, `sssf` unless SSSF_E2B_TEMPLATE names
another. scripts/e2b_template.py in the skill builds it once per E2B team.

Hobby sessions last an hour. A refresh pauses the VM and resumes it, which
keeps its files and starts a new session. The VM's own timeout is the
session, so a VM whose host died is killed by E2B within the hour.

E2B bills per second on the vCPUs and memory a VM holds, so `sandbox_end`
carries an estimate from the VM's size and seconds.
"""

from __future__ import annotations

import os
import queue
import shlex
import threading
import types
import uuid

from .sandbox_remote import Remote, require_sdk as _require

REQUIREMENT = "e2b==2.62.0"
KEYS = ("E2B_API_KEY",)
TEMPLATE = "sssf"
VCPU_SECOND = 0.000014      # e2b.dev/pricing, 2026-11-02
GIB_SECOND = 0.0000045


def require_sdk() -> None:
    _require("e2b", "e2b", REQUIREMENT)


def sweep(project: str, live: set[str]) -> list[str]:
    """Kill this project's SSSF VMs whose run isn't live. Returns their IDs."""
    from e2b import Sandbox, SandboxQuery, SandboxState
    pages = Sandbox.list(query=SandboxQuery(metadata={"sssf": project},
                                            state=[SandboxState.RUNNING, SandboxState.PAUSED]))
    gone = []
    while pages.has_next:
        for info in pages.next_items():
            adw_id = (info.metadata or {}).get("adw_id")
            if adw_id not in live:
                Sandbox.kill(info.sandbox_id)
                gone.append(f"{info.sandbox_id} ({adw_id})")
    return gone


def _result(code: int, out: str, err: str):
    return types.SimpleNamespace(returncode=code, stdout=out, stderr=err)


class _Stream:
    """A background command's output as lines, as Vercel's SDK gives it.

    E2B hands output over in chunks, to callbacks, while `wait` runs; a
    thread runs it and the chunks are split into lines here."""

    def __init__(self, handle, exit_error):
        self._chunks: queue.Queue = queue.Queue()
        self._err: list[str] = []
        self._code = -1
        self._thread = threading.Thread(target=self._pump, args=(handle, exit_error), daemon=True)
        self._thread.start()
        self.stdout = self._lines()
        self.stderr = self

    def _pump(self, handle, exit_error) -> None:
        try:
            self._code = handle.wait(on_stdout=self._chunks.put, on_stderr=self._err.append).exit_code
        except exit_error as error:
            self._code = error.exit_code
        except Exception as error:      # the stream broke; the agent's call fails on the code
            self._err.append(f"\nE2B stream ended: {error}")
        finally:
            self._chunks.put(None)

    def _lines(self):
        rest = ""
        while (chunk := self._chunks.get()) is not None:
            *lines, rest = (rest + chunk).split("\n")
            for line in lines:
                yield line + "\n"
        if rest:
            yield rest

    def read(self) -> str:
        self._thread.join()
        return "".join(self._err)

    def wait(self) -> int:
        self._thread.join()
        return self._code


class _VM:
    """An E2B sandbox behind run_process, create_process, stop and destroy."""

    def __init__(self, sandbox, limit: float):
        self.sb = sandbox
        self.limit = limit
        self.name = sandbox.sandbox_id

    def run_process(self, command: str, args=(), *, cwd=None, env=None, sudo=False,
                    capture_output=True, input=None, kill_after=None):
        from e2b import CommandExitException, TimeoutException
        line = shlex.join([command, *args])
        if input is not None:
            path = f"/tmp/sssf-in-{uuid.uuid4().hex}"
            self.sb.files.write(path, input)
            line = f'{line} < {path}; status=$?; rm -f {path}; exit "$status"'
        try:
            done = self.sb.commands.run(line, envs=env or {}, cwd=cwd, user="root" if sudo else None,
                                        timeout=kill_after or 0)
            return _result(done.exit_code, done.stdout, done.stderr)
        except CommandExitException as error:
            return _result(error.exit_code, error.stdout, error.stderr)
        except TimeoutException as error:
            if not kill_after:
                raise
            return _result(124, "", str(error))     # sandbox_remote turns it into TimeoutExpired

    def create_process(self, command: str, args=(), *, cwd=None, env=None) -> _Stream:
        from e2b import CommandExitException
        handle = self.sb.commands.run(shlex.join([command, *args]), background=True,
                                      envs=env or {}, cwd=cwd, timeout=0)
        return _Stream(handle, CommandExitException)

    def stop(self) -> None:
        self.sb.pause()
        self.sb = self.sb.connect(timeout=int(self.limit))

    def destroy(self) -> None:
        self.sb.kill()


class E2B(Remote):
    name = "e2b"
    keys = KEYS
    session_minutes = 60
    owner = "user"

    def __init__(self, run, branch: str, project: str):
        from e2b import SandboxException
        super().__init__(run, branch, project)
        self.errors = (SandboxException,)
        self.vcpus, self.memory_gib = 0, 0.0

    def _create(self) -> _VM:
        from e2b import Sandbox
        sandbox = Sandbox.create(template=os.environ.get("SSSF_E2B_TEMPLATE") or TEMPLATE,
                                 timeout=int(self.limit),
                                 metadata={"sssf": self.project, "adw_id": self.run_.adw_id})
        info = sandbox.get_info()
        self.vcpus, self.memory_gib = info.cpu_count, info.memory_mb / 1024
        return _VM(sandbox, self.limit)

    def _delete(self) -> None:
        self.sandbox.destroy()

    def _end(self, seconds: float) -> dict:
        rate = self.vcpus * VCPU_SECOND + self.memory_gib * GIB_SECOND
        return {"vcpus": self.vcpus, "memory_gib": self.memory_gib,
                "vm_cost_usd": round(seconds * rate, 4)}


Box = E2B
