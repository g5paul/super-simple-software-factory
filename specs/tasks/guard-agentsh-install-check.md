# Keep an agent's edit out of the agentsh install

Rollout step 2 of `docs/designs/agentsh-local-sandbox.md` is done, and this is item 4 of its Next list, held since the third guard run. Start from `main`.

## The problem

`uv run adws/adw_agentsh_policy.py` builds the server config and the policies from files in the repo, and prints `sudo install` lines that the engineer runs. An agent can change those files:

- on a plain run, every file in the repo is in its workspace;
- whatever an agent changes there, SSSF's commit phases may commit onto the branch.

The known case, now listed in `config.md`: an agent edits `adws/adw_modules/agentsh_sssf.yaml`, for example to set `on_attach_failure: fail_open`, and the run stops. The next run refuses because the installed config no longer matches what SSSF renders, and the refusal points at the install step. The engineer runs it and installs the agent's config as root. `check_machine` then passes it. The same goes for any other file the install script reads to build what it installs: `agentsh_guard.py`, `repo.yaml`, `sssf.config.yaml`, the script itself and the modules it imports.

## What should change

1. **The install script refuses to write anything when a file it builds from differs from what the engineer last reviewed.** Find every file it reads or imports to build the config and the policies, and name each in the spec. An uncommitted change is the plain case. A commit an SSSF run made is the harder one: decide how the script tells a change the engineer made, such as a new `deny_exec` entry in `repo.yaml`, from one an agent made. Say in the spec what it compares against and why an agent can't move that too.
2. **The refusal says what to do.** It names each changed file and gives the command to see the change. It tells the engineer to discard the change if they didn't make it, or to commit it if they did, and then to run the script again.
3. **A run's refusal no longer sends the engineer straight to install** when the installed config differs from what SSSF renders. It first says to check the files above, in the same terms as item 2.
4. **`config.md`** replaces the `agentsh_sssf.yaml` limit with what's built, and says that an engineer's own edit to `repo.yaml` now needs a commit before the install.

## The bar

The earlier bars stand. An agent's change, committed by a run or not, to any file the install script builds from, that reaches the printed `sudo install` lines without a refusal naming it, is a bypass. A refusal of the engineer's own committed change is a false positive. A new list of exceptions or a new mode is a finding.

## Out of scope

The policy rules themselves, pi under the wrap, running a real agent under the service, sudo, and turning this on outside sssf-sandbox.
