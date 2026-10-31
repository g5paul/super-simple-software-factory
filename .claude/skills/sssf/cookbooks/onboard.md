# Onboard a repo

Bring SSSF into a new project, from "should we?" to the first reviewed run. This cookbook is for the Claude session that works in the project repo. [install.md](install.md) covers the install itself. This page wraps it in the steps that came up the first time SSSF went into a real repo (yt-summarizer). Each one cost a fix when it was missed.

## Two sessions

The setup that works uses two Claude sessions:

| Session | Works in | Owns |
|---|---|---|
| Hub | the SSSF skill repo | the skill: fixes, new features, update steps for every project |
| Project | the project repo | the project: its code, its runs, and its copy of the skill |

The project session never edits the skill's source. When SSSF gets something wrong, it reports to the hub with the run id and the evidence, and it waits for the fix to arrive as an update. A Claude session in the cloud cannot reply to messages yet, so for a remote project the engineer relays the report.

## Before the session starts (the engineer)

1. Clone or fork the repo. SSSF is installed and committed on `main`, so every branch and every session started in the repo has `/sssf`. ADWs run on feature branches cut from `main`, never on `main` itself. Install on a branch instead only when SSSF must stay off `main`, for example when `main` goes upstream to a repo that does not want it. To move a branch install to `main` later, see [install.md](install.md), "Move a branch install to main".
2. If the repo is public or a fork of a public repo, set a private commit email in it first: `git config user.email <login>@users.noreply.github.com`.
3. Open a session in the repo and tell it: "Read `<skill source>/cookbooks/onboard.md` and onboard this repo. Report to the SSSF hub session."

## 1. Check the fit

Read the repo before you install anything: its README, its CI workflows, its test setup, and its recent history. SSSF fits a repo when:

- it has test and lint commands, or the work can add them. The chain's verify phase is only as good as `quality.py`.
- the work comes as defined features and fixes that a short task file can state.
- a few commits per change are fine: spec, code, and write-up each get their own.

It fits badly for exploratory work, for UI changes with no automated check, and for a repo where agents must not commit. Report the result to the engineer and to the hub, with your reasons. If it does not fit, stop here.

## 2. Install

```bash
mkdir -p .claude/skills
rsync -a --exclude apps/visualizer/node_modules --exclude apps/visualizer/dist \
  <skill source>/ .claude/skills/sssf/
uv run .claude/skills/sssf/scripts/install.py --coding-agent claude_code
claude auth status            # must show a logged-in subscription account
```

The install prints `dashboard: registered as <name>`, so the repo shows up in `just obs-all`. Copy `.env.sample` to `.env` and set `SSSF_SOURCE` in it to the skill source, so `just sssf-update` works later (`just sssf sssf-update` if the repo's justfile loads SSSF with `mod sssf`, see [install.md](install.md)). Agents bill to the Claude subscription login, so `.env` needs no API key. Don't add one.

Ask the engineer one question here: should the operator session run in a container? Claude Code's auto mode blocks the command that launches a chain, so on the host the engineer runs each launch themselves with `!`, or approves it in default mode. In a Podman container, the session runs with no permission prompts and can reach only the repo. The cost is Podman, a token, and about ten minutes the first time. If they say yes, run `install.py --container` and follow [install.md](install.md), "Run the operator session in a container". It needs a Claude Code roster.

## 3. Make quality.py check what CI checks

The verify phase runs every block in `adws/adw_modules/quality.py`: test, lint, format, typecheck, and build. Each starts as a placeholder that passes, so until you fill them in, a run checks nothing.

1. Find the commands CI runs: `.github/workflows/`, a Makefile, `pyproject.toml`, or `package.json`.
2. Put each one in its block as an argv list, with bare binary names. Point lint and format at the project's own paths (for example `yt_summarizer` and `tests`), not at `.`. Point the build's output at the block's `output_dir`, for example `uv build --out-dir <output_dir>`, so a run writes its build into the session folder and not the repo.
3. Leave a block as its placeholder only when the project has no such check, and say so in `docs/sssf.md`.
   If a block needs files the project did not have, such as a `tsconfig.json` added so typecheck can run, commit them with the onboarding and list them in `docs/sssf.md`. They are part of the install from then on, though SSSF does not stamp or update them.
4. Run `uv run adws/adw_quality.py "check the tree"`. Every block must pass.
5. Prove the checks can fail: break one thing on purpose (a test, or a line's formatting), run `adw_quality` again, see that block fail, then restore the file with `git checkout`. In a Python repo, breaking something `uv run` needs, such as the build backend, fails every block, since each one syncs the project first. That proves less: pick a break that only one block notices.
6. Is `quality.py` safe to run twice at the same moment? Two worktree runs check in two folders at once, so a check that binds a fixed port, writes a fixed temp path, or uses one shared test database breaks the other run's checks. Write the answer into `adws/adw_sssf_config/repo.yaml`: `max_worktree_runs: 1` if it isn't, or 2 if it is, with a one-line comment above it saying why.
7. What does a fresh checkout need before the checks can run? A worktree run's folder is a new checkout, so it has none of the untracked dependencies yours has, such as `node_modules`. List the commands that install them under `worktree_setup` in `adws/adw_sssf_config/repo.yaml`, the way the README installs them, for example `cd web && bun install --frozen-lockfile`. They must only create gitignored files. A Python repo on uv needs none.
8. What may every live run in this repo spend together? Each run's `max_run_cost` holds for that run alone, so two runs at once can spend up to twice it. Ask the engineer, suggesting `max_run_cost` times 1.5, and write their answer as `max_live_cost` in `adws/adw_sssf_config/repo.yaml`, or leave it out to keep it off. Each live run can still overshoot it by one call.

## 4. Keep SSSF out of the project's own tools

Follow step 6 of the post-install checklist in [install.md](install.md). Exclude `adws/` and `.claude/` from repo-wide linters, formatters, type checkers, and package builds, and exclude `specs/` and `app_docs/` from formatters and linters. Then run CI's own commands on the whole repo, the way CI runs them, and confirm they pass.

## 5. Write docs/sssf.md

This page is how the next session, or the engineer, finds out how SSSF works in this repo. Keep it to what is true here:

- **Ground rules.** Branches, push rules, billing.
- **Install.** The command and the roster: each agent and its model.
- **Quality commands.** A table of the five blocks and their argv, and which CI checks they match or leave out.
- **Check the billing.** Every agent's `raw_output.jsonl` has an init event with `apiKeySource`. It must be `none`:

  ```bash
  grep -h '"subtype":"init"' adws/adw_data/sessions/*/*/raw_output.jsonl \
    | python3 -c "import json,sys; print({json.loads(l)['apiKeySource'] for l in sys.stdin})"
  ```

- **Run a chain.** `just simple-sdlc path/to/task.md`, and what each phase does.
- **Watch a run.** `just obs`, `just obs-all`, and the `just sessions` and `just phases` reads.
- **What a run leaves.** The three commits, `specs/`, `app_docs/`, and the session folder.
- **Update SSSF.** The exact command for this repo, `just sssf-update` or `just sssf sssf-update`, then `--take` or `--keep` for a file it flags.

## 5a. Optional: the ticket flow

If the engineer wants to build features from a spec and tickets, run `/setup-matt-pocock-skills` now and choose **Local markdown**, then say so in `docs/sssf.md`. [tickets.md](tickets.md) covers the flow. Skip this for repos that only take one-shot tasks.

## 6. Commit the onboarding

Commit the install, `quality.py`, the tool excludes, and `docs/sssf.md` together, on `main`, as one commit. Don't push unless the engineer says so.

Claude Code registers skills when a session starts, so this session, which started before the install, has no `/sssf` yet. Before the first run, the engineer restarts it in the repo. `claude --continue` keeps the conversation. If SSSF went on a branch instead of `main`, every operator session has to start with that branch checked out: a session started elsewhere has no `/sssf`, and switching branches does not add it. In pi-agent-observability, SSSF lived on a branch, a session started on `main` got "Unknown skill: sssf", and it fell back to reading the cookbooks by hand.

## 7. The first run

Cut a feature branch from `main` first: the run commits to the current branch. Pick a small, real change: one flag, one function, one fix. Write the task as a file with rules, tests, docs, and an out-of-scope list. [how_to_prompt_for_the_eng.md](how_to_prompt_for_the_eng.md) shows how. Then:

```bash
just simple-sdlc specs/tasks/first-task.md
```

When it ends:

1. Run the billing check from step 5.
2. Read the three commits and the review files in `adws/adw_data/sessions/<adw_id>/context_handoff/`.
3. Run CI's commands on the whole repo.
4. Report to the hub: the adw_id, each phase's status, the review rounds, the commits, and anything SSSF did that you did not expect.

## 8. Updates

When the hub sends an update, start it only while no chain is running and the tree is clean:

```bash
just sssf-update          # or: just sssf sssf-update, where the justfile has `mod sssf`
```

It refreshes the skill copy from `SSSF_SOURCE`, runs `install.py --update`, and runs it again to confirm. It refuses to start on a dirty tree or while a chain process is alive. Read what it lists. A file marked `!` changed in the repo and in the skill, so compare the two, then `--take` or `--keep` it ([install.md](install.md), "Update an installed repo"). Commit the update on its own, and report the commit and the update output to the hub.
