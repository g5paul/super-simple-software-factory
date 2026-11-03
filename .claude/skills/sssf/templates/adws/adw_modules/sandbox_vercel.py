"""A run's tree in a Vercel Sandbox VM. The work is in sandbox_remote; this
module makes, refreshes and deletes the VM.

Hobby sessions last 45 minutes. A refresh stops the VM, and the next call
resumes it lazily in a new session with its files kept.
"""

from __future__ import annotations

from datetime import timedelta

from .sandbox_remote import Remote, require_sdk as _require

REQUIREMENT = "vercel-sandbox==0.10.0"
KEYS = ("VERCEL_TOKEN", "VERCEL_TEAM_ID", "VERCEL_PROJECT_ID")


def require_sdk() -> None:
    _require("vercel", "vercel.sandbox.sync", REQUIREMENT)


def sweep(project: str, live: set[str]) -> list[str]:
    """Delete this project's SSSF VMs whose run isn't live. Returns their names."""
    from vercel.sandbox.sync import query_sandboxes
    gone = []
    for box in query_sandboxes():
        tags = box.tags or {}
        if tags.get("sssf") == project and tags.get("adw_id") not in live:
            box.destroy(delete_orphan_snapshots=True)
            gone.append(f"{box.name} ({tags.get('adw_id')})")
    return gone


class Vercel(Remote):
    name = "vercel"
    keys = KEYS
    session_minutes = 45
    owner = "ubuntu"

    def __init__(self, run, branch: str, project: str):
        from vercel.sandbox.sync import SandboxError
        super().__init__(run, branch, project)
        self.errors = (SandboxError,)

    def _create(self):
        from vercel.sandbox.sync import create_sandbox
        return create_sandbox(tags={"sssf": self.project, "adw_id": self.run_.adw_id},
                              execution_time_limit=timedelta(seconds=self.limit), persistent=True)

    def _end(self, seconds: float) -> dict:
        """The VM's size and the GB-hours of memory it held, which Hobby's
        monthly allowance counts busy or idle. Its active CPU is cpu_seconds."""
        vcpus = getattr(self.sandbox, "vcpus", None) or 2
        memory_gib = (getattr(self.sandbox, "memory", None) or 2048 * vcpus) / 1024
        return {"vcpus": vcpus, "memory_gib": memory_gib,
                "memory_gb_hours": round(memory_gib * seconds / 3600, 3)}

    def _delete(self) -> None:
        # Each stop saved a snapshot of the VM, which Vercel keeps unless
        # asked. Hobby caps snapshot storage, and at about 800MB each, a
        # few dozen refreshes used it up and blocked every new VM.
        self.sandbox.destroy(delete_orphan_snapshots=True)


Box = Vercel
