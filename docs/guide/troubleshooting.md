# When something goes wrong

Start with the trace. `just phases <adw_id>` shows which phase failed, and the run's last lines name the stop. Most of the stops below come with a message that says what to do, and this page adds why.

## Install and sign-in

**`/sssf` doesn't show up in Claude Code.** Claude Code finds skills when a session starts. Restart the session, with `claude --continue` to keep the conversation.

**`claude has no login the agents can use`.** The agents run `claude` with your shell's environment, and `claude auth status` there found no login. Log in with `claude`. In the container, or anywhere you sign in with a token, export `CLAUDE_CODE_OAUTH_TOKEN` in the shell that launches the ADW: a Claude Code session doesn't pass its own token to the shells it runs. `just login-check` runs the same check without starting a run.

**`claude would bill the API (apiKeySource=...) on a subscription run`.** A Claude Code agent started with an API key it found, from somewhere SSSF doesn't clear. SSSF kills the call at its first event, before it does any work. Remove that key source, or set `SSSF_CLAUDE_AUTH=api_key` if you mean to bill the API. [install.md](install.md#4-check-that-it-works) shows how to check past runs.

**`model pattern ... is ambiguous`.** A pi roster names a model without its provider, and pi knows it under several. Write it as `provider/model-id`, as `just rosters` shows.

**`model pattern ... not found in pi --list-models`.** The chain stopped at launch, before any agent ran. pi lists a provider's models only once that provider has a key, so this usually means a missing key, such as `FIREWORKS_API_KEY` for the starter planner. `just demo` can pass while a larger chain stops, since the demo uses only the scout. [install.md](install.md#2-run-the-installer) lists which model needs which key.

**A pi agent fails partway through a chain.** A key that's set but wrong passes the launch check and fails when its agent first calls the provider.

**Every test phase passes, whatever the builder wrote.** `quality.py` is still on its placeholders. `uv run adws/adw_quality.py "check"` reports each placeholder block as `not configured`. [customize.md](customize.md#your-checks) shows the fix.

## Stops

A stop ends the run before a call that would go over a limit. The work done so far is kept, and on a worktree run its branch is too.

| Stop | What hit its limit | What to do |
|---|---|---|
| `RunBudgetStop` | the run's spend reached `defaults.max_run_cost` in the roster, $5 by default | split the task, or raise the cap for this repo |
| `CallBudgetStop` | one Claude Code call reached the agent's `max_call_cost` | the call is doing too much: split the task |
| `LiveBudgetStop` | every live run together reached `max_live_cost` in `repo.yaml` | wait for a run to finish |
| `DailyBudgetStop` | the repo's last 24 hours reached `max_daily_cost` in `repo.yaml` | the message says when enough spend ages out |
| `MachineBudgetStop` | every repo's last 24 hours reached `max_daily_cost` in `~/.config/sssf/limits.yaml` | the same, and it lists each repo's share |
| `RateLimitStop` | a Claude usage window is at `defaults.rate_limit_stop`, 0.8 by default | wait for the reset the message names |
| `UsageLimitStop` | the ChatGPT plan is out of usage | OpenAI gives no reset time. Check the plan's usage page |
| `SilentStop` | an agent wrote nothing for `max_silence_minutes`, 15 by default | read its `raw_output.jsonl` for the last step, often a server started in the foreground |

If Claude moves your account onto usage credits, which bill at API rates, SSSF stops rather than spend them. Set `defaults.rate_limit_stop: null` only if you want runs to spend credits.

## A run that hangs

A hung agent writes no events, so the trace goes quiet rather than red. `SilentStop` catches it after 15 minutes. To look sooner:

```bash
just phases <adw_id>    # the phase still running
just procs <adw_id>     # what it's running, with pids
just kill <adw_id>      # stop the chain first, so it can't retry, then its agents
```

If `procs` shows a pi agent but its `raw_output.jsonl` is empty and the phase has no tool calls, the agent never started. Check that its model resolves with `pi --list-models`.

## Worktree runs

**`worktree run ... is still going in this repo, and max_worktree_runs ... allows 2 at once`.** Wait for one to finish, or stop it with `just kill`. Raise `max_worktree_runs` only if your checks are safe to run twice at once.

**`just merge` refuses.** It changes nothing until the run succeeded, its launch branch is checked out, your checkout is clean, and the run's worktree has nothing uncommitted. The message names which. [run.md](run.md#on-its-own-branch) shows one.

**`just merge` hits a conflict.** SSSF aborts the merge and keeps the branch and folder. Merge by hand with `git merge sssf/<adw_id>`, resolve, commit, then `just discard <adw_id>`. [run.md](run.md#two-runs-at-once) walks through one.

**A worktree run fails at its `setup` phase.** A command in `worktree_setup` failed before any agent started. Its log is in the run's session folder, named in the message.

**The checks fail in a worktree but pass in your checkout.** The worktree is a fresh checkout, so it has none of your untracked dependencies. List what installs them under `worktree_setup` in `repo.yaml`.

## Tickets

`just tickets` checks the folder before any agent spends, and refuses on:

- a spec with no `## Testing Decisions` section naming the test seams;
- uncommitted changes, or a feature folder that isn't committed;
- `main` or `master` without `--worktree`;
- a ticket blocked by one that doesn't exist, or a cycle.

The message names the file and what's missing. [tickets.md](../../.claude/skills/sssf/cookbooks/tickets.md) covers each.

## VM runs

**`--sandbox runs pi agents on the ChatGPT sign-in only`.** A pi agent in the roster uses a provider other than `openai/`, whose key would have to go into the VM. Use a Claude Code or ChatGPT roster for VM runs.

**`--sandbox openshell` says the CLI isn't on PATH, no gateway answers, or the sandbox image isn't built.** Run `just openshell-setup` (or `just sssf openshell-setup`); it starts the gateway and builds the image. If it stops at `/dev/kvm`, either `sudo chgrp $USER /dev/kvm` (lasts until WSL restarts) or `sudo usermod -aG kvm $USER` and then `wsl --shutdown` from Windows, which stops every WSL session.

**The run stops in its `sandbox` phase.** The message is the provider's or SSSF's. It may name a tool in the VM that's missing or too old, a refused VM past Vercel's Hobby allowance or E2B's credit, or, on E2B, a template that doesn't exist. For the template, run `uv run .claude/skills/sssf/scripts/e2b_template.py`. Nothing in the VM had run yet.

**A VM run stopped with work in it.** Its uncommitted work is in `adws/adw_data/sessions/<adw_id>/sandbox-uncommitted.patch`. SSSF doesn't apply it. Read it, then `git apply` it in the run's worktree if you want it.

## Updates

**`just sssf-update` refuses.** It won't run while the tree has uncommitted changes or a chain is running, or when the skill source is on a branch with commits that aren't on its `main`. Commit, wait for the chain, or check out `main` in the source. `SSSF_ALLOW_UNMERGED=1` updates from the branch anyway.

**An update flags a file.** You changed it and the skill changed it too. Run the `diff` the update prints, then `--take <path>` for the skill's version or `--keep <path>` for yours.

**The installer prints `skill: unknown (no git source)`.** `SSSF_SOURCE` wasn't set when it ran, so it couldn't read the source's commit, and `stamp.json` has no `skill` key. Set it in `.env` and update.

## The dashboard

**`just obs` fails at once.** It needs bun. Install it, or read the trace with the terminal recipes in [watch.md](watch.md).

**A repo is missing from `just obs-all`.** It reads `~/.config/sssf/projects.json`, which `install.py` writes. Run `install.py --update` in that repo to record it.
