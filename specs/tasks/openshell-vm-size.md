# Give OpenShell VMs room for a real test suite

Run it with `--worktree` and `--base main`, on the hub's own config. Part of the change is the VM's filesystem policy in `sandbox_openshell.py`, which is guard code, so it goes through the chain. The setup recipe is not guard code, but it ships in the same change.

## What broke

Run 00d8e1a8 (2026-11-20) was the hub's first `simple-sdlc` run under `--sandbox openshell`. Its fix passed the plan's own check, and verify failed all four quality checks on the VM, not on the change:

- the visualizer tests hit `ENOSPC: no space left on device` writing to `/tmp`;
- the Python suite timed out at 1200 s (740 s on the host), with errors piling up once `/tmp` filled;
- `vue-tsc` was killed, most likely out of memory;
- 7 chain tests failed in SSSF's index rebuild.

Inside the VM: 2 CPUs, 2 GiB of RAM with no swap, `/tmp` a 1 GiB tmpfs taken from that RAM, and a 3.9 GB overlay root disk. Mid-suite, `/tmp/pytest-of-1000` alone held 222 MB. On the host, one run of the skill's suite writes about 2.5 GB of temp (`docs/open.md` has a row on that; shrinking it is out of scope here).

## What sets the size

The size is gateway-wide, not per sandbox. The engineer checked on 2026-11-20:

- `openshell sandbox create --cpu 6 --memory 8Gi` is accepted and changes nothing under the VM driver: the VM still had 2 CPUs, 2 GiB and a 1 GiB `/tmp`.
- The gateway starts `openshell-driver-vm` with `--vcpus 2 --mem-mib 2048 --overlay-disk-mib 4096`, the driver's defaults (`openshell-driver-vm --help` lists them, with env vars `OPENSHELL_VM_DRIVER_VCPUS`, `OPENSHELL_VM_DRIVER_MEM_MIB`, `OPENSHELL_VM_OVERLAY_DISK_MIB`).
- The gateway reads per-driver tables from its config file. The gateway binary names an `[openshell.drivers.vm]` table with keys including `vcpus`, `mem_mib` and `overlay_disk_mib`. Confirm the exact keys before relying on them, from the binary's strings or OpenShell's docs, and say in the spec how you confirmed them.
- `just openshell-setup` (the `openshell-setup` recipe in `templates/justfile`) writes `~/.config/openshell/gateway.toml` only when it's missing, with no driver table, and rewrites the systemd unit each time. `systemctl --user enable --now` doesn't restart a gateway that's already running.
- Restarting the gateway stops every running sandbox, including other projects' live runs.

The host is 16 CPUs and 15 GiB of RAM, and several projects' runs can hold a VM at once.

## The change

1. **Setup sizes the VM.** `openshell-setup` makes sure `gateway.toml` has an `[openshell.drivers.vm]` table with `vcpus`, `mem_mib` and `overlay_disk_mib`. Defaults: 4 vCPUs, 4096 MiB and 16384 MiB. The spec says why those fit a host running two or three VMs at once, or picks better ones and says why. The engineer can override each one when running setup; the spec says how.
   - An existing `gateway.toml` keeps every other key and table it has. Setup changes only the three sizes and says when it changed one.
   - Setup restarts the gateway only when a size changed and no sandbox is running. With a sandbox running, it changes the file, restarts nothing, and tells the engineer to rerun setup once the runs end.
   - Say in the spec whether the overlay disk file is sparse on the host, since 16 GiB a VM times several VMs matters if it isn't.
2. **Temp goes on the disk.** Agents and quality commands in the VM get a `TMPDIR` on the overlay disk, outside the repo's tree, which the filesystem policy (`policy()` in `sandbox_openshell.py`) allows read-write. `/tmp` stays as it is for tools that ignore `TMPDIR`. The spec says which commands get the variable and how: every `sandbox exec` the run makes, or only some.
3. **Docs.** The OpenShell section of `references/config.md` says what size a VM gets, how to change it, and that a change needs the gateway restarted with no runs live. Delete the `docs/open.md` row this closes, the one on the hub's suite not fitting the OpenShell VM.

## The bar

- **Size, fresh.** With no `gateway.toml`, setup writes one with the three sizes at their defaults, and the gateway table as today.
- **Size, existing.** With a `gateway.toml` that has other keys and tables, setup keeps them all, byte for byte apart from the sizes, and adds or updates the three sizes. A second run changes nothing and restarts nothing.
- **Overrides.** Each size can be set by the engineer, and a value that isn't a positive whole number is refused with the setting's name.
- **No restart under a live run.** With a sandbox listed as running, setup restarts nothing and says what to do. The test uses a fake `openshell` and a fake `systemctl`; nothing real is restarted.
- **TMPDIR.** Through `tests/test_sandbox_openshell.py`'s fake CLI, the commands the spec names carry the new `TMPDIR`, and the policy lists its folder read-write. The folder is outside the repo's tree.
- **Nothing else changes.** Every existing test passes, and the policy's other paths and network rules are as before.

## After the merge, the engineer's proof

Not part of the run, since no test starts a real VM:

1. Rerun `just openshell-setup` with no runs live, then make a throwaway sandbox and check `nproc`, `free` and `df`, and that `TMPDIR` is set in it.
2. Rerun `specs/tasks/flaky-obs-retry-timing.md` with `just simple-sdlc --worktree --sandbox openshell` and see verify pass in the VM.

## Running the tests

Run the touched test files from `.claude/skills/sssf` with `-m 'not pushes and not host_only'`, and leave the whole suite to verify. No test starts a real VM, a real gateway or a real agent.

## Out of scope

- Shrinking the suite's temp footprint, and SSSF's cleanup of images, VM disks and sessions. Each has its own `docs/open.md` row.
- Per-repo VM sizes. The driver's size is gateway-wide.
- The remote VM modes (`vercel`, `e2b`).
