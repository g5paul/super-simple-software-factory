# Run local agents under agentsh when a repo asks for it

Rollout step 1 of `docs/designs/agentsh-local-sandbox.md`. Read its Decisions table first: those choices are settled, so build them and don't reopen them. Its sections on the proof say why each requirement below exists.

The text guard has let one shell spelling after another through, and its patches are frozen. agentsh checks the real program and arguments at exec, and real file opens and connects, so a spelling doesn't matter. The proof ran by hand, with `docs/designs/agentsh/` scripts, on a machine where the engineer installed the service. This change makes it a repo setting.

## What should change

- **A setting.** `local_guard: text | agentsh` in `repo.yaml`, `text` by default. It only applies under `sandbox: none`. Asking for `agentsh` with any other `sandbox` is a config error. Nothing else about `text` changes, and the hub's own `repo.yaml` stays on `text`.
- **The wrap.** Under `agentsh`, every agent process starts as `/usr/bin/agentsh wrap --policy <policy> --root <checkout top> --report=false -- <agent command>`, with stdin `/dev/null` and these set: `AGENTSH_CONFIG=/etc/agentsh/sssf.yaml`, `AGENTSH_SERVER=unix:///run/agentsh/agentsh.sock`, `AGENTSH_NO_AUTO=1`, `NODE_USE_ENV_PROXY=1`, `JUST_NO_DOTENV=true`. Use the absolute binary path, not `PATH`. Quality checks, gates and SSSF's own git stay outside the wrap. The text checks stay on as well: they leave later, in separate runs.
- **The policies.** One policy per agent type per repo, generated from `repo.yaml`. `docs/designs/agentsh/deny-policy.py` and `sssf-net.yaml` are the tested source: keep their rule order, their rule sets and their reasons, because first match wins and a reordered list fails quietly. The generated file adds what they hard-code today:
  - the repo's git common dir, which a worktree needs and `${GIT_ROOT}` doesn't cover
  - `secret_files.py`'s patterns, with `.env.sample` and `.env.example` allowed as the guard allows them
  - `deny_exec` from `repo.yaml`, an argv form that becomes `command_rules`, beside `forbid`, which keeps its text uses
  - `network_allow` from `repo.yaml`, added to the base host list
- **Install stays manual.** A command prints the policies and the sudo lines for the engineer to run, including the `policies.allowed` entry and the `sssf.yaml` that holds it. That file ships in the skill, since `sssf-update` copies nothing else, and it sets `policies.detect_project_root: false`. SSSF itself never runs sudo.
- **No fallback.** Under `agentsh`, a run refuses to start, before any agent, with a message saying which sudo step fixes it, when any of these is true:
  - the service isn't active
  - `/usr/bin/agentsh`'s checksum isn't the pinned one
  - an installed policy differs from what `repo.yaml` generates today
  - `detect_project_root` isn't `false` in the installed config
  - `--root` isn't the checkout's top
  - the wrap fails to attach. This one may only show after launch, and it must end the run rather than go on unguarded
- **Counting denials.** Only a denied `ptrace_execve` with a named rule counts toward the three-refusal stop, and it's never a breach. Read these from the server's event API with the session id the wrap prints. The API caps a page at 5000, and a larger limit silently returns 200, so page at 5000. File and network denials go to the trace with their paths. They don't count, because agents tolerate many of them.
- **Stopping.** A run's timeout and `stop_tree` must end the agent and everything under it through the wrap, and leave no session running.
- **Agent output.** `agentsh:` lines from the wrap mustn't reach the stream parsers as agent output.
- **The guard list.** A new module holding this code joins `GUARD_MODULES` in `adws/adw_guard_change.py` and the list in the hub's `CLAUDE.md`.
- **Docs.** `config.md` gets the setting and the install step. The design doc says what got built and what the first real run must check.

## The bar

The baseline is `main`, the text guard alone.

- With `local_guard: text` or no setting, every command, env var and refusal is as on `main`.
- With `agentsh`, nothing the text guard stops gets through because of this change, and no agent process runs outside the wrap. A path through the code where `local_guard: agentsh` starts an agent unwrapped, or with a policy other than the generated one, is a bypass. So is a state where the run goes on after the service or the attach has failed.
- A generated policy whose order or content lets an agent read or change what the third run's probes showed held is a bypass. `docs/designs/agentsh/deny-probe.sh` lists those cases.
- A wrong setting, a stale policy or a missing service stops the run with a clear message. It must not hang, and it must not count as a breach.

Accepted limits, which aren't findings: a copied binary runs under a new name, an agent can read its own sign-in, Claude Code can write `~/.claude.json` and read other projects' transcripts, `/tmp` is shared, the server's API stays open to unwrapped local processes, and quality checks run outside the wrap. agentsh's own bugs and the open pi hang are out of scope too: those are the fork's and the design doc's.

## Tests

- Hub tests must pass on a machine without the service, so they use a fake `agentsh` and fake server responses. A test that needs the real service skips itself when `AGENTSH_LIVE` isn't set.
- `text`: the agent command and env are byte for byte what they were.
- `agentsh`: each refusal above fires before any agent starts, and each agent type gets its own policy and all five env vars.
- Generated policies: the last file rule denies `/`, `/**`, the secret and credential rules come before the workspace allow, writes to `.git/hooks` and `.git/config` are denied before the git data allow, and `secret_files` patterns and `deny_exec` entries appear. With no repo settings, the file rules match what `deny-policy.py` writes for the same paths.
- Denial counting: a named `ptrace_execve` denial counts and a file denial doesn't, across more than one page of events.
- A wrap that exits at once without attaching ends the run as a failure.

## Out of scope

- Turning it on anywhere. Rollout steps 2 to 5 do that, each from its own repo's session.
- Removing or patching a text checker. A new spelling goes in `docs/designs/agentsh/corpus.sh`.
- `--sandbox openshell`, Vercel and E2B, none of which change.
- Patching the agentsh fork.
- Installing anything with sudo, or changing what's installed on this machine.
- Running a real agent under the service as part of this run. Rollout step 2 is that test.
