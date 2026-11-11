# Your first run

In this tutorial you install SSSF in a repo, ask for one small change, watch the run, read its report, and then keep or drop the change. The run works on a branch and folder of its own, so nothing reaches your branch until you merge it. That makes the tutorial safe to try in a real repo. The examples in [Run SSSF](run.md) cost $0.37 and $0.63 at list price. On a Claude subscription you aren't charged them.

## Before you start

You need:

- Claude Code, signed in to a Claude subscription;
- [uv](https://docs.astral.sh/uv/), [just](https://github.com/casey/just), git and sqlite3;
- a repo with a clean tree and at least one commit.

[Before you start](install.md#before-you-start) lists the versions tested. This tutorial uses a Claude subscription. To run the agents on ChatGPT or on API keys, follow [Install SSSF in a repo](install.md#2-run-the-installer) instead.

If you don't have a repo to try it in, make a new folder and run this in it:

```bash
git init && git commit --allow-empty -m init
```

Don't run it in a repo that already has commits.

## Install SSSF

1. Clone the hub, the repo SSSF's skill comes from:

   ```bash
   git clone https://github.com/g5paul/super-simple-software-factory ~/github/super-simple-software-factory
   ```

2. From the root of your repo, copy the skill in:

   ```bash
   mkdir -p .claude/skills
   cp -r ~/github/super-simple-software-factory/.claude/skills/sssf .claude/skills/
   ```

   If you cloned the hub somewhere else, use the path of your own clone here and in the next step.
3. Run the installer, and copy the sample `.env`:

   ```bash
   export SSSF_SOURCE=~/github/super-simple-software-factory/.claude/skills/sssf
   uv run .claude/skills/sssf/scripts/install.py --coding-agent claude_code
   cp .env.sample .env
   ```

   `SSSF_SOURCE` lets the installer record which commit of the skill you installed.
4. Leave every key in `.env` empty. The agents use the account `claude` is signed in to.

## Commit, then restart Claude Code

1. Commit the install on its own:

   ```bash
   git add -A && git commit -m "Install SSSF"
   ```

   A run on its own branch starts from your last commit, so an uncommitted install isn't in it.
2. Restart any Claude Code session that's open in the repo. Claude Code registers `/sssf` only when a session starts. `claude --continue` keeps the conversation.

## Check that it works

Run the demo, then list the runs it made:

```bash
just demo        # two cheap read-only runs, end to end
just sessions    # the runs it just made
```

Both runs should end `✓ success`. Then run `just login-check`. It should print `login ok: Claude Code agents can sign in`.

If either check fails, see [Install and sign-in](troubleshooting.md#install-and-sign-in).

## Ask for one small change

In Claude Code, in the repo, ask for one small function and its test. Describe it in your own repo's terms, start the request with `/sssf`, and end it with this sentence:

```
Run it on its own branch, then tell me the adw_id, status, cost and commits.
```

Here is the request from the example in [Ask in Claude Code](run.md#ask-in-claude-code), made in a small Python text library:

```
/sssf add textkit.is_palindrome(text), which says whether a text reads the same backwards once case, spaces and punctuation are ignored, so "A man, a plan, a canal: Panama" gives True. Export it and test it. Run it on its own branch, then tell me the adw_id, status, cost and commits.
```

On the host, Claude Code asks you to approve the launch. Approve it.

The Claude Code session you asked is the operator session. It picks an ADW, short for AI developer workflow. An ADW is a script that runs agents and checks in a fixed order. The operator session then writes the ADW's prompt, launches it, and follows the trace until the run ends. The example run took 2 minutes 22 seconds.

## Watch it

While the run goes, open a second terminal in the repo and list the runs:

```bash
just sessions                 # the last 10 runs
```

The first column is each run's `adw_id`, the id that names the run everywhere. Pass yours to `just phases` to see where the run stands. In this example from [Watch a run](watch.md), put your own `adw_id` in place of `d36de4d2`:

```bash
just phases d36de4d2          # each phase: seq, name, kind, owner, status, attempt
```

If you have bun, you can follow the run in the dashboard instead. Run this, then open the address it prints:

```bash
just obs        # this repo's runs
```

If you want to stop the run, run `just kill <adw_id>`.

## Read the report

When the run ends, the operator session reports. The report from the example, trimmed:

```
Run b5b5d40a succeeded: all 11 phases passed and it cost $0.6292 (539,140 tokens).
The work is on branch sssf/b5b5d40a, and nothing is on agentsh-rollout yet.

Commits:
- 3014011 Add spec for exporting is_palindrome from textkit
- 058292e Export is_palindrome from textkit with tests and docs
- ece5bf2 Document the is_palindrome package export

One surprise. is_palindrome already existed in textkit/palindrome.py, but textkit
didn't export it. So the run didn't write a new function. It added the export,
a module docstring, and tests that cover the Panama case.

What the checks proved. Only `uv run pytest -q` actually ran, and it passed.
Lint, format, typecheck and build are still placeholder echoes in quality.py.

`just merge b5b5d40a` lands the branch on agentsh-rollout, and `just discard
b5b5d40a` drops it. I'll merge only when you tell me to.
```

Read your own report for these parts:

- **The `adw_id`.** Here it's `b5b5d40a`. You need yours for the next step.
- **The commits.** They sit on branch `sssf/<adw_id>`, in the folder `adws/adw_data/worktrees/<adw_id>`. Your own checkout hasn't changed.
- **What the checks proved.** A fresh install's `adws/adw_modules/quality.py` holds placeholder commands, and until you replace them, every test phase passes whatever the builder did. In the example only the tests were real. [Your checks](customize.md#your-checks) shows how to put in your own.

## Keep it or drop it

To keep the change, tell the operator session "merge it", or run `just merge` yourself. In this example, put your own `adw_id` in place of `b5b5d40a`:

```bash
just merge b5b5d40a
```

`just merge` lands the run's branch on the branch you launched from, then removes the run's worktree and branch. It never pushes. Check that the commits landed:

```bash
git log --oneline -3
```

To drop the change instead, run:

```bash
just discard <adw_id>    # drop the branch and folder, whatever state they're in
```

If your branch moved on while the run went, the merge can conflict. [Two runs at once](run.md#two-runs-at-once) shows how to finish a conflicted merge by hand.

## Next

- Put your real test, lint and build commands in `quality.py`, as [Your checks](customize.md#your-checks) shows. Do this first.
- Before you push, [turn on the secret scan](install.md#6-turn-on-the-secret-scan).
- [Pick an ADW](run.md#pick-an-adw) lists the other ADWs, and the request that starts each one.
- [Watch a run](watch.md) covers the terminal recipes, the dashboard and the trace in SQL.
- [How SSSF works](../concepts.md) explains how a run is built, and why.
