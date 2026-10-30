# Design: run the operator session in a container

Status: built and shipped as an opt-in install option (`install.py --container`, fdbeb08). It's in its first real use, in yt-summarizer. This file tracks the experiment: what was decided, what was tested, and what's still open. Add to the log as it moves.

## Why

The operator session is the Claude Code session that writes an ADW prompt and launches `uv run adws/adw_<chain>.py`. In auto mode, the permission classifier blocks those launches. The two ways out on the host are `!` launches by hand or an allow rule, and bypass mode on the host is not recommended.

Inside a container, bypass mode is the standard advice: the session can only touch what the container mounts. So the operator runs there with no prompts, and the host stays out of reach.

The agents a run starts were never the problem. SSSF's Claude Code runner already passes `--permission-mode bypassPermissions`, and Pi agents never ask. What they lack is isolation, which the container also gives them, because they run inside it.

## What it covers, and what it doesn't

SSSF's README lists what the core deliberately leaves out: "no sandbox, no branch per run, no merge step, no cloud, and no human-in-the-loop approval phase."

The container covers the sandbox, and only the sandbox. Runs still commit to whatever branch is checked out, and nothing merges a run's work back.

[Sandcastle](https://github.com/mattpocock/sandcastle), read from the fork at `~/github/sandcastle`, does all three: a container, a git worktree and branch per run, and a merge back. It's a TypeScript library, and SSSF is Python, so using it directly would mean calling Node for every agent and rewiring session resume, the trace, and obs pool tagging. We took the idea for the operator session and left the library.

## Decisions

1. **Opt-in at setup, outside the core.** `install.py --container` stamps `container/Containerfile`, `build.sh`, and `run.sh`. Without the flag nothing is stamped, and no ADW knows about the container. `onboard.md` asks the question in step 2.
2. **Claude Code rosters only.** A Pi roster gets a parser error. Pi agents sign in through a ChatGPT login that isn't mounted in.
3. **Podman, rootless, `--userns=keep-id`.** Docker isn't installed in the WSL distro. Files the container writes are owned by the engineer on the host.
4. **The repo and `~/.config/sssf` are mounted at their host paths.** The trace db, run folders, and registry are the host's own, so the host dashboard shows container runs with no extra wiring.
5. **One image per repo, one shared home volume.** The image is `localhost/sssf-operator-<repo>`. The `sssf-operator-home` volume keeps Claude Code's settings between sessions.
6. **Subscription auth through a long-lived token.** `claude setup-token` writes `CLAUDE_CODE_OAUTH_TOKEN` to `~/.config/sssf-container/token.env`, outside every repo. No API key. The token lasts about a year, so a new repo reuses it.
7. **Kept current by updates.** `has_container(record)` keeps the stamped files on `--update` once a repo has them.

## Problems found and fixed

- **Login menu on first start.** Claude Code showed its sign-in menu even with the token set. `run.sh` now sets `hasCompletedOnboarding` in `~/.claude.json`.
- **Agents not logged in (run a03e7b00).** A Claude Code session doesn't pass its `CLAUDE_CODE_OAUTH_TOKEN` to the shell commands it runs, so the chain's agents had no login. `run.sh` exports it through `CLAUDE_ENV_FILE=/tmp/sssf-auth.sh`. The same failure led to SSSF's login check (ab2a35c): a chain now stops before its first Claude Code agent when there's no login, and `just login-check` tests it by hand.
- **Runs missing from the host dashboard.** Fixed by mounting the repo and `~/.config/sssf` at their host paths (decision 4).
- **uv's environment clashing with the host's.** `run.sh` sets `UV_PROJECT_ENVIRONMENT` per repo, and sets the `GIT_*` identity from the repo's git config.

## Log

| Date | Repo | What | Result |
|---|---|---|---|
| 2026-10-30 | sssf-sandbox `exp/container-operator` | hand-made container, run a03e7b00 | failed: agents not logged in |
| 2026-10-30 | sssf-sandbox `exp/container-operator` | run 1d56f792 after the `CLAUDE_ENV_FILE` fix | success |
| 2026-10-30 | hub | `--container` install option, fdbeb08 | shipped |
| 2026-10-30 | sssf-sandbox `exp/container-option` | reinstalled through `--container`, existing token, run 888c6d27 | success, visible in the host trace and dashboard |
| 2026-10-30 | yt-summarizer `sssf-run` | `--container` setup through the skill, aa2140b, quality 7f07c83d | set up; the engineer's first session is next |

## Open

- **First real use.** yt-summarizer is being set up now. What it gets wrong goes in the log before anything else is built on top.
- **Branch per run and a merge back.** The next step toward what sandcastle does. Not designed yet. It needs a grilling session before any code: where the worktree lives, who merges, and what happens to a run that fails.
- **Gaps yt-summarizer's setup found.** `--container` on an installed repo prints every post-install step, when only the container lines apply. The docs don't say to keep `container/` out of package builds, the way `adws/` and `.claude/` are. The docs don't say whether a container session can push: it can't, because host git credentials aren't mounted, unless the remote URL carries a token.
- **Sandbox branches.** `exp/container-operator` still holds the hand-made container and can go. The sandbox lives on `exp/container-option`, not `main`.
