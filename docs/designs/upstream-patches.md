# Design: patches upstream without SSSF

Status: being designed, 2026-11-12. Nothing is built yet. The decisions so far are at the end. The open item is in [open.md](../open.md).

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
3. **Install SSSF outside the tree.** The skill goes at user scope and `adws/` goes in a sibling folder or under `.git/`, pointed at the repo. This is the cleanest split, but it's a redesign. Every path assumption changes, and worktree and VM runs would need the overlay copied in. It's also the only option that takes the factory out of the agents' reach. With SSSF in the tree, an agent's file tools can edit the files that decide whether its work passes, and `protected_files` doesn't cover all of them: see the ADR's consequences.
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

- **The patches that exist wait.** The engineer decided on 2026-11-12 not to publish them yet. The setup comes first: SSSF installed in agentsh, the export built and working there. Whether and how to send the fixes upstream is decided after that. Publishing them wouldn't need the export, since they hold no SSSF files: it would mean fetching upstream again, a topic branch per fix rebased onto the new `upstream/main`, each one's tests run there, and a report per fix like the draft.
- **A proving ground for the export.** This is the work now. Installing SSSF in agentsh and building the next fix through a chain would test option 4 on a real public fork with a Go toolchain. It has risks to probe first:
  - **agentsh would be guarding its own source.** Under `local_guard: agentsh`, the agent runs inside the installed agentsh while it edits agentsh. The installed binary is root's and isn't touched, but a test that runs the built binary runs it inside the tracer.
  - **Nested ptrace.** agentsh's ptrace tests trace child processes. Under the ptrace tracer, those children are already traced, so the tests may fail or need `local_guard: text`.
  - **Tests that need root or containers.** Many of agentsh's integration tests use Docker or root. `quality.py` would have to name the tests an agent can run.

## Decisions

Settled with the engineer in a grilling session on 2026-11-12. Terms are in [GLOSSARY.md](../../GLOSSARY.md).

1. **Option 4.** SSSF stays installed in the repo, and an export builds the upstream branch. [ADR 0001](../adr/0001-keep-sssf-in-the-repo.md) records why, and what it costs: the agents work in the same tree as the factory that judges them.
2. **The export ends at a local branch.** It never pushes and never opens a PR. The engineer reviews the branch, pushes it to the fork and opens the PR, which fits the deny rules on `git push` and `gh pr create`.
3. **Prove it before agentsh.** The export is built and proven against a fake upstream first, then agentsh is the first real repo, so a broken export can't tangle the fork commits SSSF's pinned build comes from.
4. **agentsh is listed.** It goes into `docs/projects.md` at its install. The fork is public, and the hub already names it throughout.
5. **An agentsh project session owns agentsh after the install.** This session stops writing to it then. The agentsh fixes still open, loopback and bind in [open.md](../open.md), reach that session as the engineer's requests, and are candidates for the first features to build and export.
6. **Terms.** Product change, SSSF file, record and export, as the glossary defines them. "Upstream" keeps its git meaning, the repo a fork came from.
7. **Records leave by path filter, at export.** Commits stay as runs make them. The export removes SSSF files from each commit it replays, and drops a commit left empty. Records match by the names runs give them, `specs/<adw_id>_<slug>.md` and `app_docs/<adw_id>_<slug>.md`, so a project's own `specs/` or `app_docs/` survives. Whole folders go only for `adws/`, `.claude/skills/sssf/`, `.scratch/` and `.githooks/`, plus the files `sssf.just` and `docs/sssf.md`. This replaces both of option 4's first ideas, a record commit with a trailer and moving records under `adws/adw_data/`. The second would have meant a guard change to `gates.py`.
8. **The export takes the feature's own commits.** That means the commits since the feature branch left the branch it was cut from, usually `main`, not everything since upstream. So the install, SSSF updates and the onboarding commit stay behind. A commit that won't apply onto upstream, because it leans on a fork commit upstream lacks, stops the export, which names it.
9. **SSSF lines in shared files.** A feature's edit to a file both share, such as `pyproject.toml`, is copied as it is. As the backstop, the export refuses when the final diff against upstream holds an SSSF path name (`adws/`, `.claude/skills/sssf`, `specs/<adw_id>`, `sssf.just`), naming the file and line, for the engineer to fix by hand.
10. **`export_checks` in `repo.yaml`.** It lists the repo's own check commands, since `quality.py` lives under `adws/` and isn't on the exported branch. The export runs them on the branch in a temporary worktree. The list starts empty, and an export with none says "unchecked" instead of passing.
11. **`just export <branch>`,** with `--onto <ref>` defaulting to `upstream/main`. It builds `export/<branch>`, and refuses when that branch exists (unless `--replace`) or the tree is dirty. It prints the branch, its commits, the files changed and the check results, and changes nothing else: no push, and the feature branch is untouched.
12. **Where it's proven.** First in the export's own tests, in the skill's pytest suite, with a throwaway upstream and fork in temp folders. Then in a new fixture pair outside `/tmp`: a bare `~/github/sssf-export-upstream.git` standing in for the original, and a clone at `~/github/sssf-export-fork` with SSSF installed on its `main`, a feature built by a real chain, then exported. sssf-sandbox stays as it is. This narrows decision 3: it was to be sssf-sandbox, which has no history to share with an upstream.
13. **agentsh's `main` is `sssf/links-listing` plus the SSSF install.** Features are cut from `main`, so they're built and tested with the fork's fixes in, and SSSF's next pinned build comes from `main`. `upstream/main` stays the clean base for exports, and by decision 8 an export takes only a feature's own commits. Pushing `main` to the fork would publish the fixes, so `main` stays local until the engineer decides on publishing.
14. **The fork's existing fixes are out of scope.** They hold no SSSF files, so publishing them needs no export. The export's first users are features a chain builds.
15. **The export lives in the skill and is built by hand, test-first.** That means `templates/adws/adw_export.py`, a `just export` recipe in the template justfile, and a "Send a feature upstream" section in a cookbook, reaching every repo through the usual update. None of it is guard code. This session builds it with the `tdd` skill rather than through the hub's ticket chain, which keeps `git_helper`, the commit path of every chain, under close watch.
16. **agentsh runs with `local_guard: text`.** Under the agentsh guard, its own ptrace tests would most likely fail, since the test process is already a tracee, and its loopback tests fail on the policy's loopback denial. The text guard still refuses push, `gh` writes and secret reads, and an agent can't change the installed binary, which is root's. `--sandbox openshell` can come later: it needs Go in the image, the Go module proxy allowed, and ptrace proven in the microVM.
17. **agentsh's checks build with `CGO_ENABLED=0`.** A cgo build needs `libseccomp-dev`, `libfuse-dev` and `libfuse3-dev`, which only the engineer can install. Until then, the 42 `linux && cgo` test files that upstream's CI runs aren't compiled.
18. **The first feature to export is the `go vet` lock-copy fix** in `internal/db/proxy/postgres/cancelmap.go`, upstream's own code and independent of the fork's fixes, so it tests the whole path from chain to export to review. Bind (open.md) is second: its code is ptrace and network code the fork changed heavily, so it will test decision 8's stop.
19. **The fork's `ocsf` bug is the agentsh session's first job,** as a fork-only commit that registers `ptrace_signal`. It's never exported, since upstream has no `ptrace_signal`. Done on 2026-11-12 by run cda7b802, merged to local `main` as d5e14cb1.
20. **agentsh's check set, in both `quality.py` and `export_checks`:** `CGO_ENABLED=0 go build ./...`, then `CGO_ENABLED=0 go test -short -p 4 -timeout 8m` on every package except `cmd/agentsh-shell-shim`, `internal/api`, `internal/shim/kernelinstall` and `internal/db/proxy/postgres`, which fail here because of the environment, and `internal/ocsf`, until decision 19's fix adds it back. Both run with Go's `~/.local/go/bin` on `PATH`. `go vet ./...` joins with the vet fix. `gofmt` stays out, since the tree already fails it and upstream's CI doesn't check it.
21. **The order of the work.** The design is committed. This session builds the export test-first in the skill, proves it on the fixture pair, and updates the hub and sssf-sandbox. Then it installs SSSF in agentsh: `main` fast-forwards to `sssf/links-listing`, the install goes on top, and agentsh is listed. Last, it hands agentsh to its project session for the `ocsf` fix, the vet fix and its export, then bind. Nothing is pushed unless the engineer says so.
22. **The fixture pair is a tiny Python package.** It has one module, pytest tests, a `pyproject.toml`, and its own `specs/` folder with one file that isn't a record, to test decision 7's edge case for real. A real chain on a Claude roster with a cheap model builds one small function, and the engineer reviews its export. The pair has no GitHub remote, and it's listed in `docs/projects.md` as a test bed.
23. **This session owns the fixture pair,** as it owns sssf-sandbox. It installs SSSF there, launches the chain and runs the export.
24. **Three ways to name a feature.** `just merge` deletes a worktree run's branch, so decision 11's `<branch>` isn't enough. The export takes a branch (its commits since it left `main`, or `--base`), `--run <adw_id>` (the commits that run made, by the shas the trace records), or `--feature <slug>` (the commits carrying `SSSF-Feature: <slug>`). The output branch is named after the form used, such as `export/<adw_id>`.
25. **No fetch.** The export uses the local `upstream/main` as it stands, and prints that ref's date and the command to refresh it. That keeps decision 11's "changes nothing else" exact, and a machine with no network still exports.
26. **Merge commits.** The export replays only the non-merge commits, in order. A merge that only joins history is skipped. A merge that carries changes of its own, such as a conflict resolution, stops the export, which names it.
27. **agentsh's local `main` can't be pushed.** After the install, `main` holds the SSSF install and still tracks `origin/main`, and `push.default` is unset, so a plain `git push` on `main` would publish SSSF to the fork. The install sets `git config branch.main.pushRemote no-push`, a remote that doesn't exist, so a plain `git push` on `main` fails, and pushes of `export/*` and `sssf/links-listing` still work. Checked in a scratch clone with `git rev-parse main@{push}`. It doesn't stop `git push origin main`, which names the remote and skips `pushRemote`. A stronger guard would be the pre-push hook refusing a push whose commits hold SSSF files; that's a later skill change, not part of the install. Claude sessions can't push on this machine anyway; this covers a push the engineer types.
28. **agentsh commits as the noreply address.** The install sets the clone's `user.email` to `g5paul@users.noreply.github.com`. An export keeps each commit's author, so a chain commit made with the global identity would publish that email upstream. The fixture fork found this.

### Facts found along the way

- The planner writes its spec to `specs/`, and the documenter writes to `app_docs/`. The plan gate, `gates.plan_well_formed`, requires the spec path to start with `specs/`, and `gates.py` is guard code, so moving specs means a guard-change run.
- A builder edits the project's README, CHANGELOG and docstrings as part of a change, and those are product changes. The documenter may not edit them.
- `install.py` never touches `pyproject.toml`, CI, `CLAUDE.md` or `AGENTS.md`. The operator session writes the linter and build excludes, `docs/sssf.md`, and the justfile's `mod sssf` line, following the cookbooks, usually in the onboarding commit.
- `git_helper.SSSF_PATHS` (`adws/`, `.claude/skills/sssf/`) already keeps SSSF files out of review diffs, but not out of `git add -A`.
- sssf-sandbox has no history from before SSSF, and no remote: its first commit is "init: stamp sssf factory".
- agentsh's checks, as the engineer's user, on `sssf/links-listing`, 2026-11-12 (a read and an unprivileged run, not under agentsh):
  - Go is at `~/.local/go/bin`, not on `PATH`. The cgo build needs `libseccomp` and fuse headers, which aren't installed, so only `CGO_ENABLED=0` builds. CI has no lint or gofmt gate, and `gofmt -l` already lists files.
  - `CGO_ENABLED=0 go test -short -p 4 ./...` takes 122 s. 120 packages pass and 5 fail. Three of the failures come from the environment: the shim against a loopback server, `internal/api`'s peer-uid checks, and `kernelinstall`, which refuses an inherited seccomp filter. `internal/db/proxy/postgres` fails on a dial timeout. `internal/ocsf` fails for real: `ptrace_signal`, the event the fork's signal commit added, isn't registered.
  - `internal/ptrace`'s 314 untagged tests pass here, but they start tracees with `PTRACE_TRACEME`. Under agentsh's tracer the test process is already a tracee, so they would most likely fail hard rather than skip. That's read from the code, not run. The integration-tagged ones need root, FUSE or Docker.

## The workflow for agentsh, once installed

How work reaches GitHub after the install, as explained to the engineer on 2026-11-12. In the clone that day, `origin/main` equals `upstream/main` (a07c7897), so none of the fork's fixes is on GitHub yet. All 18 fixes sit on `sssf/links-listing`, and `git cherry` finds every commit of the `fix/*` and other `sssf/*` branches already there. None of the 18 touches an SSSF file.

| Branch | Holds | Goes to GitHub |
|---|---|---|
| `upstream/main` | canyonroad's code | it's theirs |
| `sssf/links-listing` | upstream plus the 18 fixes | yes, when the engineer decides to publish them |
| local `main` | upstream, the 18 fixes and the SSSF install | never (decision 27) |
| `export/<name>` | upstream plus one feature's product changes | yes: the branch the engineer pushes and opens a PR from |

SSSF lives only on local `main` and the feature branches cut from it. Everything pushed comes from a branch that never held SSSF.

**The fixes that exist.** They need no export (decision 14). Publishing them to the fork is `git push origin sssf/links-listing`, run by the engineer. A PR per fix to canyonroad is ordinary git work: branches cut from `upstream/main`, the fix cherry-picked onto each, stacked where one fix leans on another.

**New work a chain builds, such as the vet fix.**

1. The engineer asks the agentsh project session for the change. It runs a chain on a branch cut from local `main`, or as a worktree run.
2. The chain commits the spec (`specs/<adw_id>_*.md`), the code, and the write-up (`app_docs/<adw_id>_*.md`).
3. `just export --run <adw_id>` builds `export/<adw_id>` on `upstream/main`. It replays only the code, drops the spec and write-up commits, strips the `SSSF-*` trailers, and runs `export_checks` (the `CGO_ENABLED=0` build and tests) on the clean branch.
4. The engineer reviews the branch, pushes it with `git push origin export/<adw_id>`, and opens the PR.

**Work that leans on the unpublished fixes, such as bind.** Replayed onto bare `upstream/main`, it won't apply, and the export stops and names the commit (decision 8). The engineer then either gets the fixes upstream first and exports once `upstream/main` has them, or exports onto the fixes with `just export --run <adw_id> --onto sssf/links-listing`, which still has no SSSF files, and opens a PR stacked on the fixes' branch. Fork-only work such as the `ocsf` fix (decision 19) works the same way: exported `--onto sssf/links-listing` to put it on the fork, or left on local `main`.

**Who does what.** This session installs SSSF in agentsh and then stops writing there (decision 5). The agentsh project session, started after the install so it loads `/sssf`, runs the chains and the exports. The engineer runs every push.

### Status on 2026-11-12

The setup works inside the fork. Going upstream is untried on agentsh.

Proven:

- The install (b165f676). `quality.py` and `export_checks` run the `CGO_ENABLED=0` build and tests, and since 77d3a9e0 they test `internal/ocsf` too.
- Scout run 9ab6e3ba.
- The first chain run, cda7b802 (`adw_simple_sdlc`, worktree, $1.03, 6.6 minutes), fixed the `ocsf` test (decision 19). Review 1 blocked, the builder revised, and review 2 approved. `just merge` fast-forwarded local `main` to d5e14cb1. Every commit carries the noreply email (decision 28), and nothing was pushed.
- The project session ran it through `/sssf` and reported, as the hub's handoff rules expect, and its report matched the trace.

To do and test, in order:

1. The export on a real repo: the vet fix, through `just export --run`, with `export_checks` on a clean `upstream/main` branch. So far the export has run only on the test pair.
2. `--onto sssf/links-listing`, for bind and for putting the `ocsf` fix on `sssf/links-listing` before that branch is published. Without the fix, the published branch fails its own `internal/ocsf` test.
3. Decision 27's guard against a push the engineer types: a plain `git push` from `main` should fail. `git push origin main` still goes through until the pre-push hook refuses SSSF files (a row in open.md).
4. agentsh as the guard in this checkout. It runs `local_guard: text`, and agentsh has run as the guard only in sssf-sandbox and the hub. That waits for the rollout.
5. A PR to canyonroad, when the engineer decides to publish.

A note from the first run: when a fix should turn a skipped check back on, no agent can do it, since `quality.py` is a factory file. The project session edited it before launch, with the engineer's OK. The run cookbook should say so (a row in open.md).

## Open questions

None. The grilling session's design tree was empty after decision 26. Decisions 27 and 28 came from the export's fixture run and the engineer's review of the agentsh workflow on 2026-11-12.
