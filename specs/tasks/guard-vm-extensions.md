# Let a pi roster with extensions run in a VM

Build decisions 20 to 27 of `docs/designs/remote-sandboxes.md`, settled with
the engineer on 2026-11-05. Read them first; they are the bar's source.

Today `sandbox.check_roster` refuses any pi roster with `harness_engineering`
extensions under `--sandbox vercel|e2b`. Extension paths are made absolute
against the launch checkout (`worktree.anchor`), and the only file copied into
the VM is the refuse extension, `sssf_rules.ts`, at its host path. A VM run's
branch is already cloned in the VM at the host worktree's path.

What should change:

- In a VM run, each extension path is anchored to the run's worktree path, so
  pi loads it from the VM's clone, with the files it imports.
- Before the VM opens, each `harness_engineering` path must be inside the repo
  and committed at the run's starting commit. One that isn't stops the run
  with a message naming the agent and the path. This replaces the refusal.
  Imports aren't followed.
- In the VM, pi agents get `OBS_DISABLE=1`, and `OBS_AUTH_TOKEN` doesn't cross
  in. Other `OBS_` variables cross as today.
- The refuse extension still loads last, after every user extension, and a
  test pins that order.
- `references/config.md` says what a VM run does with extensions, and lists
  the known limit: an agent can edit a committed extension in the VM, and the
  edited one loads for the next agent call, as on the host.

A host run, a Claude Code roster, and a pi roster without extensions keep
exactly today's behaviour. The proof on real VMs (decision 27) runs after the
merge, by hand; this change needs tests that stand in for it without a VM.
