# When something goes wrong

Start with the trace. `just phases <adw_id>` shows which phase failed, and the run's last lines name the stop. Most stops come with a message that says what to do. This page adds why, and the fix.

## Install and sign-in

**`/sssf` doesn't show up in Claude Code.** Claude Code finds skills when a session starts. Restart the session, with `claude --continue` to keep the conversation.

**`claude has no login the agents can use`.** The agents run `claude` with your shell's environment, and `claude auth status` there found no sign-in. Sign in by running `claude`. In the operator container, or anywhere you sign in with a token, export `CLAUDE_CODE_OAUTH_TOKEN` in the shell that launches the ADW. A Claude Code session doesn't pass its own token to the shells it runs. To run the same check without starting a run, use `just login-check`.

**`claude would bill the API (apiKeySource=...) on a subscription run`.** A Claude Code agent started with an API key it found somewhere SSSF doesn't clear. SSSF kills the call at its first event, before the call does any work. Remove that key source. If you mean to bill the API, set `SSSF_CLAUDE_AUTH=api_key` instead. [install.md](install.md#4-check-that-it-works) shows how to check past runs.

**`model pattern ... is ambiguous`.** A pi roster names a model without its provider, and pi knows the model under several. Write it as `provider/model-id`, as `just rosters` shows.

**`model pattern ... not found in pi --list-models`.** The run stopped at launch, before any agent ran. pi lists a provider's models only once that provider has a key, so the cause is usually a missing key, such as `FIREWORKS_API_KEY` for the starter planner. `just demo` can pass while a larger ADW stops, because the demo uses only the scout. [install.md](install.md#2-run-the-installer) lists which model needs which key.

**A pi agent fails partway through a run.** A key that's set but wrong passes the launch check. The agent fails when it first calls the provider. Check the key for that agent's provider.

**Every test phase passes, whatever the builder wrote.** `quality.py` is still on its placeholders. `just quality "check"` reports each placeholder block as `not configured`. [customize.md](customize.md#your-checks) shows the fix.

## Stops

A stop ends the run before a call that would go over a limit. SSSF keeps the work done so far, and on a worktree run it keeps the branch too.

| Stop | What hit its limit | What to do |
|---|---|---|
| `RunBudgetStop` | the run's spend reached `defaults.max_run_cost` in the roster, $5 by default | split the task, or raise the cap for this repo |
| `CallBudgetStop` | one Claude Code call reached the agent's `max_call_cost` | split the task, because the call is doing too much |
| `LiveBudgetStop` | every live run together reached `max_live_cost` in `repo.yaml` | wait for a run to finish |
| `DailyBudgetStop` | the repo's last 24 hours reached `max_daily_cost` in `repo.yaml` | wait until the time the message gives, when enough spend ages out |
| `MachineBudgetStop` | every repo's last 24 hours reached `max_daily_cost` in `~/.config/sssf/limits.yaml` | wait, as for `DailyBudgetStop`. The message also lists each repo's share |
| `RateLimitStop` | a Claude usage window is at `defaults.rate_limit_stop`, 0.8 by default | wait for the reset the message names |
| `UsageLimitStop` | the ChatGPT subscription is out of usage | check the subscription's usage page, because OpenAI gives no reset time |
| `SilentStop` | an agent wrote nothing for `max_silence_minutes`, 15 by default | read the agent's `raw_output.jsonl` for its last step, often a server started in the foreground |

If Claude moves your account onto usage credits, which bill at API rates, SSSF stops instead of spending them. If you want runs to spend credits, set `defaults.rate_limit_stop: null`.

## A run that hangs

A hung agent writes no events, so the trace goes quiet instead of red. `SilentStop` catches the hang after 15 minutes. To look sooner:

```bash
just phases <adw_id>    # the phase still running
just procs <adw_id>     # what it's running, with pids
just kill <adw_id>      # stop the chain first, so it can't retry, then its agents
```

If `procs` shows a pi agent, but its `raw_output.jsonl` is empty and the phase has no tool calls, the agent never started. Check that its model resolves with `pi --list-models`.

Under `local_guard: agentsh`, a run that stops with "the agentsh service stopped answering event polls" can mean agentsh's event store has grown large enough to slow SSSF's poll past its 5-second limit. Check its size with `sudo ls -la /var/lib/agentsh/`. With no run going, run `sudo sqlite3 /var/lib/agentsh/events.db 'ANALYZE;'`, which can take a long time on a large store. If that's not enough, stop the service, move `events.db` and its `-wal` and `-shm` files aside, and start it again. A pruning timer is planned; [open.md](../open.md) tracks it.

## Worktree runs

**`worktree run ... is still going in this repo, and max_worktree_runs ... allows 2 at once`.** Wait for one run to finish, or stop it with `just kill`. If your checks are safe to run twice at once, you can raise `max_worktree_runs`.

**`just merge` refuses.** `just merge` changes nothing unless all of these hold:

- the run succeeded;
- the run's launch branch is checked out;
- your checkout is clean;
- the run's worktree has nothing uncommitted.

The message names the one that failed. [run.md](run.md#on-its-own-branch) shows an example.

**`just merge` hits a conflict.** SSSF aborts the merge and keeps the branch and folder. To finish by hand:

1. Run `git merge sssf/<adw_id>`.
2. Resolve the conflict, and commit.
3. Run `just discard <adw_id>`.

[run.md](run.md#two-runs-at-once) walks through one.

**A worktree run fails at its `setup` phase.** A command in `worktree_setup` failed before any agent started. Its log is in the run's session folder, which the message names.

**The checks fail in a worktree but pass in your checkout.** The worktree is a fresh checkout, so it has none of your untracked dependencies. List the commands that install them under `worktree_setup` in `repo.yaml`.

## Tickets

`just tickets` checks the folder before any agent spends, and refuses on:

- a spec with no `## Testing Decisions` section naming the test seams;
- uncommitted changes, or a feature folder that isn't committed;
- `main` or `master` without `--worktree`;
- a ticket blocked by one that doesn't exist, or a cycle.

The message names the file and what's missing. [tickets.md](../../.claude/skills/sssf/cookbooks/tickets.md) covers each.

## VM runs

**`--sandbox runs pi agents on the ChatGPT sign-in only`.** A pi agent in the roster uses a provider other than `openai/`, whose key would have to go into the VM. Use a Claude Code or ChatGPT roster for VM runs.

**`--sandbox openshell` says the CLI isn't on PATH, no gateway answers, or the sandbox image isn't built.** Run `just openshell-setup`, or `just sssf openshell-setup`. The recipe starts the gateway and builds the image. If it stops at `/dev/kvm`, run one of these:

- `sudo chgrp $USER /dev/kvm`, which lasts until WSL restarts;
- `sudo usermod -aG kvm $USER`, then `wsl --shutdown` from Windows, which stops every WSL session.

**The run stops in its `sandbox` phase.** The message comes from the provider or from SSSF. It may name a tool in the VM that's missing or too old, a VM refused past Vercel's Hobby allowance or E2B's credit, or, on E2B, a template that doesn't exist. Nothing in the VM had run yet. If the template is missing, run `uv run .claude/skills/sssf/scripts/e2b_template.py`.

**A VM run stopped with work in it.** SSSF saves the run's uncommitted work in `adws/adw_data/sessions/<adw_id>/sandbox-uncommitted.patch` and doesn't apply it. Read the patch. If you want the work, `git apply` the patch in the run's worktree.

## Updates

**`just sssf-update` refuses.** The update won't run in any of these cases:

- The tree has uncommitted changes. Commit them.
- A run is going. Wait for it to end.
- The skill source is on a branch with commits that aren't on its `main`. Check out `main` in the skill source, or set `SSSF_ALLOW_UNMERGED=1` to update from the branch anyway.

**An update flags a file.** You changed the file, and the skill changed it too. Run the `diff` the update prints. Then run `just take <path>` for the skill's version, or `just keep <path>` for yours.

**The installer prints `skill: unknown (no git source)`.** `SSSF_SOURCE` wasn't set when the installer ran, so it couldn't read the skill source's commit, and `stamp.json` has no `skill` key. Set `SSSF_SOURCE` in `.env`, then update.

## The dashboard

**`just obs` fails at once.** The dashboard needs bun. Install bun, or read the trace with the terminal recipes in [watch.md](watch.md).

**A repo is missing from `just obs-all`.** `just obs-all` reads `~/.config/sssf/projects.json`, which `install.py` writes. To record the repo, run `install.py --update` in it.
