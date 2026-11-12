# Design: patches upstream without SSSF

Status: proposed, 2026-11-12. Nothing is built yet. The open questions at the end go to the engineer before work starts. The open item is in [open.md](../open.md).

SSSF installs into the repo it works on and becomes part of that repo's history. That's fine for a repo the engineer owns. For a fork of someone else's public repo, it gets in the way: a fix built with SSSF should go upstream as a patch or a PR with none of SSSF in it, and today nothing helps with that.

## How branches work today

- **The install goes on `main`.** [onboard.md](../../.claude/skills/sssf/cookbooks/onboard.md) step 1 commits SSSF to `main`, so every branch and every session started in the repo has `/sssf`. It allows a branch install "when `main` goes upstream to a repo that does not want it", but then every operator session has to start on that branch, since switching branches doesn't register the skill. pi-agent-observability hit this: a session started on `main` got "Unknown skill: sssf".
- **A plain run commits to the checked-out branch.** The engineer cuts a feature branch from `main` first. Each commit phase calls `git_helper.commit_all`, which runs `git add -A` and commits everything.
- **A `--worktree` run** gets branch `sssf/<adw_id>`, cut from the checked-out tip, in `adws/adw_data/worktrees/<adw_id>`. `just merge` lands it on the engineer's word, `just discard` drops it, and `just rebase` moves it onto the launch branch in a fast-forward-only repo. `--sandbox` implies `--worktree`, and the VM's commits come back to that branch.
- **An update** runs on a short branch, and `main` fast-forwards to it, as one commit named "Update SSSF to skill <sha>".

## What ties SSSF to the repo

Counted in sssf-sandbox on 2026-11-12:

- **The install.** 258 tracked files under `.claude/` (the skill copy) and 86 under `adws/` (workflows, modules, prompts, pi extensions, config, `stamp.json`).
- **Edits to the project's own files.** The `justfile` (starter recipes, or a `mod sssf` line), `.gitignore` entries, `.githooks/pre-push`, `.env.sample`, `docs/sssf.md`, and the linter, formatter and package-build excludes that [install.md](../../.claude/skills/sssf/cookbooks/install.md) step 6 asks for, often in `pyproject.toml` or the CI config.
- **Records in the feature commits.** A chain writes its spec to `specs/` and its docs to `app_docs/` (43 and 21 files in sssf-sandbox), and `commit_all` stages them with the code. So the product change and SSSF's record of it share every commit.

The third one matters most. Dropping the install commits from a branch is an ordinary rebase. Pulling SSSF's files out of every feature commit, and SSSF's hunks out of shared files, is the part people get wrong.

## Options

1. **A branch install plus cleanup by hand.** This works today. SSSF lives on an `sssf` branch, features are cut from it, and a PR branch comes from `git rebase --onto upstream/main sssf <feature>` plus removing `specs/` and `app_docs/` from each commit. It's fragile, it depends on care each time, and the branch install already cost one project its `/sssf`.
2. **Keep SSSF untracked**, through `.git/info/exclude`. History stays clean, but `--worktree` and `--sandbox` break. A worktree is a fresh checkout of tracked files only, the VM clones the committed repo, and pi extensions must be committed files at the run's starting commit. The tool excludes would still touch tracked config.
3. **Install SSSF outside the tree.** The skill goes at user scope and `adws/` goes in a sibling folder or under `.git/`, pointed at the repo. This is the cleanest split, but it's a redesign. Every path assumption changes, and worktree and VM runs would need the overlay copied in.
4. **Keep the install in the repo and add an export.** This is the one proposed here. Two changes:
   - **Keep records out of product commits.** Either the commit phase commits `specs/` and `app_docs/` as their own commit, marked with a trailer like the existing `SSSF-Feature` and `SSSF-Step`, or those records move under `adws/adw_data/`, which SSSF already owns.
   - **Add `just export <branch> --onto upstream/main`.** It replays only the product commits onto the upstream base, on a new branch. It refuses if the result touches any SSSF-owned path. Then it runs the repo's own checks there, not `quality.py`, which is SSSF's. The engineer reviews the branch and opens the PR. Nothing is pushed.

Option 4 leaves worktree runs, VM runs and the skill as they are. The cost is where the commits go, plus one recipe.

### What option 4 still has to settle

- **Shared config files.** An SSSF hunk in `pyproject.toml`, the `justfile` or `.gitignore` can't be dropped by path. Either the export removes known SSSF hunks, or SSSF stops writing them. For the linter excludes, `quality.py` could pass the exclude flags itself, and CI-wide tools would still need the engineer's own config.
- **Commits that mix both by hand.** A commit the engineer made that touches SSSF paths and product paths together. The export should refuse it and name it, rather than split it silently.
- **How the export picks product commits.** By trailer, by path, or both. Path is simpler and catches commits made outside a run. A trailer catches intent.

## The first repo: agentsh

The engineer's fork at `~/github/agentsh` is the first case: it holds fixes to send upstream as patches, and maybe PRs. Its state on 2026-11-12:

- **Remotes.** `origin` is the fork, `g5paul/agentsh`. `upstream` is `canyonroad/agentsh`, with push set to `DISABLED`.
- **No SSSF install.** The hub session wrote the fork's fixes by hand, so they're already free of SSSF files.
- **Branches.** `main` equals `upstream/main` at a07c7897 (fetched 2026-11-07). The build SSSF pins, `0.20.6-dev.fork+05b3777f`, is the tip of `sssf/links-listing`, 18 commits ahead. The `fix/*` branches split some of those commits into topics: `ptrace-exec-fail-closed` 1, `ptrace-thread-session` 1, `proxy-sni-and-self-allow` 2, `ptrace-wait-spin` 5, `ptrace-proc-self` 6, `ptrace-argv-limits` 7. The `sssf/*` branches are stacked builds, and the two `debug/*` branches hold instrumentation that shouldn't go upstream.
- **A draft report** for one fix is in [agentsh/upstream-report.md](agentsh/upstream-report.md).

So agentsh splits into two jobs:

- **The patches that exist.** They don't need the export at all. They need fetching upstream again, a topic branch per fix rebased onto the new `upstream/main`, each one's tests run there, and a report per fix like the draft. That's work this session can do by hand today.
- **A proving ground for the export.** Installing SSSF in agentsh and building the next fix through a chain would test option 4 on a real public fork with a Go toolchain. It has risks to probe first:
  - **agentsh would be guarding its own source.** Under `local_guard: agentsh`, the agent runs inside the installed agentsh while it edits agentsh. The installed binary is root's and isn't touched, but a test that runs the built binary runs it inside the tracer.
  - **Nested ptrace.** agentsh's ptrace tests trace child processes. Under the ptrace tracer, those children are already traced, so the tests may fail or need `local_guard: text`.
  - **Tests that need root or containers.** Many of agentsh's integration tests use Docker or root. `quality.py` would have to name the tests an agent can run.

## Open questions for the engineer

1. Option 4, as above?
2. Records: their own commit with a trailer, or moved under `adws/adw_data/`?
3. agentsh: publish the existing patches first, by hand, and install SSSF in agentsh only after the export exists? On 2026-11-10 you chose to wait on publishing until SSSF's high-priority items close, so this would change that.
4. List agentsh publicly in `docs/projects.md`? The fork is public, but unlisted is the default.
