# Pin the agentsh build that refuses namespaces

Item 1 of the Next list in `docs/designs/agentsh-local-sandbox.md`. Start from `main`.

## The problem

`PINNED_SHA256` in `agentsh_guard.py` is build `41960715`'s sha256. The engineer installed `3918a619` on 2026-11-09, which fixed `tar -x`, and the pin didn't move with it. Since then `check_machine` has refused every run under `local_guard: agentsh`, the hub's and sssf-sandbox's, with `/usr/bin/agentsh is not the pinned build`.

The installed build is now the fork's `241cd393` on `sssf/confine`. It refuses namespaces, mounts and `chroot` in every tracee, which closed a hole that let an agent bind-mount any folder it can read into its workspace and read through it. The design doc's section "The namespace hole, 2026-11-09" has the evidence and the checks run on it.

| | |
|---|---|
| Commit | `241cd393`, `fix(ptrace): refuse namespaces, mounts and chroot in tracees`, on `3918a619` |
| Version | `0.20.6-dev.fork+3918a619+ns.87c6994d` |
| sha256 | `ee6cd37e03bdd1461cb1c622a15adcf9264c19bc27b5301c2d94355b27120bce` |

A rebuild from `241cd393` with the recipe in the design doc gives the same bytes.

## What should change

1. **The pin names the installed build.** `PINNED_SHA256` becomes the sha256 above, and its comment names `241cd393` on `sssf/confine`. Nothing else in `check_machine` changes.
2. **`config.md`** says which build a repo needs and why: this one, since earlier builds fail `tar -x` on files in subfolders and let an agent read any file through a namespace of its own. It adds a known limit: under the wrap, a tool that needs a namespace gets `EPERM`, such as bubblewrap, Chrome's and Electron's sandboxes and rootless podman, and `clone3` gets `ENOSYS`, which glibc answers by falling back to `clone`.

## The bar

The earlier bars stand. A pin that doesn't match the sha256 above is a broken promise. Any change to `check_machine` or the rest of the guard beyond the constant and its comment is a finding.

## Testing

The tests monkeypatch `PINNED_SHA256`, so they don't see the value. The hub session checks the real `check_machine` against `/usr/bin/agentsh` on this machine after the run.

## This run is under the text guard

`check_machine` refuses while the pin is wrong, so this run can't start under agentsh. The engineer set the hub's `local_guard: text` for this run only, and sets it back after the merge. The text checks are on, as they were for every guard run before the hub moved to agentsh.

## Out of scope

The fork's ninth patch for link targets, which waits on a review. The tool-config task. The policy rules, pi under the wrap, sudo.
