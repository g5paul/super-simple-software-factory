# Let SSSF's refuse files reach an E2B VM

`sandbox_remote.Remote.install` copies one of SSSF's own host files into the
VM at its host path: `sssf_rules.ts` for pi agents (`agent_pi._in_vm`) and
`sssf_refuse.py` for Claude Code agents (`agent_cc`), whenever the repo has
refuse rules. It runs `mkdir -p "$(dirname "$0")"` as the VM's user. That path
is the launch checkout's `adws/adw_modules/`, outside the run's worktree, and
on E2B the VM's user can't create it:

    mkdir: cannot create directory '/home/gpaul/github/sssf-sandbox/adws/adw_modules': Permission denied

So on E2B every agent call in a repo with refuse rules fails before the agent
starts. The proof of decision 27 in `docs/designs/remote-sandboxes.md` found
it on 2026-11-05, in runs 516d1ffd and 0fd75f1c. It is present on `main`.
Earlier E2B proofs passed only because their repo had no refuse rules. On
Vercel the same copy works. The run fails closed: no agent runs without the
refuse file.

What should change: `install` makes the file's directory the way `_clone`
makes the clone's directories, with `sudo` and handed to the VM's user, then
writes the file as that user and makes it executable, as now. The copy still
happens once per VM, at the host path, with the host file's bytes. Nothing
else the VM gets changes, and a host run keeps exactly today's behaviour. A
failed copy still stops the agent call before it starts.
