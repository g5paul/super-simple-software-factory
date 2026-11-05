# Keep the obs token out of every VM command

Decision 23 of `docs/designs/remote-sandboxes.md` says the obs server's token,
`OBS_AUTH_TOKEN`, stays on the host. Run a761dcc0 drops it for pi agents only,
in `agent_pi._in_vm`. Its bypass review found an old hole, present on `main`
too: `sandbox_remote.Remote._env` passes every host variable that starts with
`OBS_` to every VM command. Checks, `worktree_setup`, git and Claude Code
agents in the VM all get `OBS_AUTH_TOKEN` when the host has it, and the host
has it whenever the repo's `.env` sets it, since the ADW loads that file. An
agent can write a check or test script that a quality check runs in the VM,
and that script can copy the token into the clone, where it comes back to the
host branch.

What should change: `Remote._env` never passes `OBS_AUTH_TOKEN` into the VM,
whatever the command and whoever asked for it. Every other `OBS_` and `SSSF_`
variable crosses as today. `references/config.md` says the obs token never
enters a VM. A host run keeps exactly today's behaviour.
