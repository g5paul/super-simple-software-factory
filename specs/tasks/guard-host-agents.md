# A repo setting that keeps every agent in a VM

Run it with `--worktree` and `--base main`, on the hub's own config. This setting decides where agents may run, so it's guard code, and the change goes through the chain. It touches `repo_settings.py`, `session.py` and the launch paths of the chains and trials.

## What broke

A project on `sandbox: openshell` found a gap that the agent rules can't close. The rules see only the command an agent types. A builder can write a script or a `just` recipe that runs a paid command, such as a test run with a live-provider flag set, and then run it under a name no rule matches. The project's justfile has `set dotenv-load`, so on the host that recipe gets the real keys from `.env`, even though `agent_env` strips them from the agent itself.

In an OpenShell VM the same recipe gets nothing:
- the clone has no `.env`;
- a VM command gets only `SSSF_` and `OBS_` variables;
- the network policy lets only the coding agent reach its model host, with registries read-only.

The project's three OpenShell runs show this holding. So the VM closes the gap, for runs that go to the VM. These runs still put agents on the host:

1. **`--sandbox none`** on a chain that takes `--sandbox`: `adw_simple_sdlc`, `adw_build_test`, `adw_plan_build_test`, `adw_plan_build_test_quality` and `adw_tickets`. It also applies to a trial started with `adw_trial.py start --sandbox none`.
2. **The chains that take no `--sandbox`.** These always run their agents on the host: `adw_plan`, `adw_build`, `adw_plan_build`, `adw_build_review`, `adw_document`, `adw_prompt` and `adw_scout`. `adw_scout`'s agents only read, but under the text guard they can still run commands. `just demo` runs `adw_prompt` and `adw_scout`.
3. **A resumed host run.** `choose_sandbox` in `session.py` keeps an existing run where it ran (design decision 12), so a run that started on the host resumes on the host whatever `repo.yaml` says now.

Today only a ground rule in the project's own docs covers these. Other repos with `dotenv-load` justfiles have the same gap.

## The change

1. **A new `repo.yaml` setting, `host_agents`, a boolean that defaults to `true`.** With `true`, nothing changes.
   - `host_agents: false` is valid only with a VM `sandbox` (`openshell`, `vercel` or `e2b`). With `sandbox: none`, `repo_settings.load` refuses it with a message naming both settings, the way it refuses `local_guard: agentsh` with a VM sandbox today.
   - The spec says how the setting is read, so an agent's edit to `repo.yaml` in a worktree can't turn it off. Read it from the main checkout before any agent starts, as `session.ensure` already does for the guard, or say why another point is as safe.
2. **With `host_agents: false`, SSSF refuses at launch, before any run row, worktree or agent exists:**
   - `--sandbox none` on every chain and trial that takes the flag;
   - every chain that takes no `--sandbox`, the seven listed above, `adw_scout` included;
   - resuming a run whose record says it ran on the host.

   Each refusal names `host_agents: false` in `repo.yaml` and says what to do: drop the flag, use a chain that takes `--sandbox`, or set `host_agents: true` in a commit, which is the engineer's call.
   - The spec finds every place a chain or trial starts agents. It shows each one either goes through the one check or can't start an agent on the host. Prefer one check in `session.ensure`, or in the one path every chain takes, over a check in each chain script.
   - SSSF's commands that start no agent keep working: `merge`, `discard`, `rebase`, `kill`, `status`, `phases`, `quality`, the install and update scripts, and `openshell-setup`.
3. **Docs.**
   - `config.md`'s settings table and its OpenShell section describe `host_agents`: what it refuses, and that it needs a VM `sandbox`.
   - They also say the hole it leaves. `just quality`, run on a run's branch or worktree, runs code the builder wrote on the host, with whatever the justfile loads. No agent is involved, but the code is the agent's. Check a run's work in the VM, or review it before running the repo's checks on the host.
   - `run_adw.md`'s switch steps mention the setting as optional, for a repo whose justfile loads `.env`.
   - The `docs/open.md` row this run adds is for `just quality` on agent-written code under `host_agents: false`. The fix options are to run quality in a VM or to refuse it in a run's worktree. Both are new mechanisms and guard changes.

## The bar

- **Default unchanged.** Without `host_agents`, or with `true`, every chain, trial and resume behaves as on `main`. Every existing test passes.
- **Each route refuses.** With `host_agents: false` and `sandbox: openshell`, these refuse before any run row, worktree or agent call exists:
  - each of the seven no-flag chains;
  - `--sandbox none` on each chain that takes the flag, and on `adw_trial.py start`;
  - resuming a host run.

  A test drives each one and asserts no agent was started and no run row was written.
- **The VM path is untouched.** Under the same setting, a chain with no flag, or with `--sandbox openshell`, opens its VM as on `main`. Resuming a VM run works as on `main`.
- **Non-agent commands work.** `merge`, `discard`, `quality` and `status` run under `host_agents: false` as on `main`.
- **Bad pairs refuse at load.** `host_agents: false` with `sandbox: none` refuses with a message naming both.
- **An agent can't turn it off.** A run's worktree whose `repo.yaml` sets `host_agents: true`, when the main checkout's sets `false`, doesn't let a host run through. The test commits the change on the run's branch, and also leaves it uncommitted in the worktree.
- **The message is actionable.** Each refusal names the setting and the file.

## Running the tests

Run the touched test files from `.claude/skills/sssf` with `-m 'not pushes and not host_only'`, and leave the whole suite to verify. No test starts a real VM or a real agent: drive the chains through the existing fakes. Keep runs light, since the agentsh event store slows under heavy load (`docs/open.md`).

## Out of scope

- Refusing or sandboxing `just quality`. Document it and add the open.md row.
- The operator container (decision 14). Say in the spec whether it counts as the host for this setting, and leave it as on `main`.
- Changing any repo's `repo.yaml`. Each engineer turns the setting on in their own repo.
- The P2 row on committed secret files, and the other open rows.
