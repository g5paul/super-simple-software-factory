# See an agent's change staged only in the index, second run

This finishes `specs/tasks/guard-index-only-staged.md`. Read it; its hole, change and bar carry over, except where this brief changes them. Run it with `--worktree` and `--base main`, on the hub's own config.

## Where run 64bd47ec got to

Guard run 64bd47ec built the first brief and stopped on 2026-11-17 after round 1's fix, because the reviewer found the fix added a mechanism. Neither review round found a bypass. Its spec is `specs/64bd47ec_index-staged-check_v2.md` on branch `sssf/64bd47ec`, and its report is `adws/adw_data/sessions/64bd47ec/context_handoff/guard_report.md`, with the two reviews beside it.

The code is uncommitted in `adws/adw_data/worktrees/64bd47ec`. `git -C adws/adw_data/worktrees/64bd47ec diff` is the starting point. Start from that spec and that code, and read both reviews before relying on either. The reviewers' probes and raw results are in `bypass_review_1/` and `bypass_review_2/` in that folder; the cases this brief names by result name are there.

## The engineer accepted round 1's three changes

On 2026-11-17 the engineer accepted the three changes the reviewer called a mechanism. They stay, and a reviewer files a finding that matches one of them, as narrowed here, as meeting the bar.

1. **The staged mode from before the call.** The after-call check reads the staged mode from the before snapshot's value for the same path (`kept`), so a mode the engineer staged before the call isn't read as hidden. That fixed the `core.fileMode=false` false stop. Round 2 showed an agent can't use it to hide its own blob; keep those cases as tests.
2. **The link exception, narrowed.** An entry whose `HEAD` mode is `120000` and whose index holds the plain file's bytes counts as shown, only when `core.symlinks` is false. With `core.symlinks` true, round 2's `link_T_engineer_agent_stages_link` case is named, as round 1 named it. Read `core.symlinks` from the config the snapshot's other commands read, after the git directory is put back.
3. **The rollback leaves a staged-new file under an untracked folder.** A path the agent staged under a `before` key ending in `/`, such as the engineer's untracked nested repo, is unstaged and left on disk, never deleted. The run still stops and names it. That keeps the agent's own new file there, round 2's one harm. The engineer accepted it: an unstaged write inside such a folder passes on every version, so the agent gains nothing. `config.md` says so in one line.

## What this run adds

1. **The plan commit keeps both halves of a rename.** Round 2's broken promise 1: the diff that lists the staged names under `only` runs with rename detection on, so a `git mv` under `specs/` commits only the new name and the deletion lands in the builder's commit. Add `--no-renames` to that diff, and to any other diff `commit_all` reads names from.
2. **A forged cache-tree can't hide a staged blob.** Round 1's old hole 1. The agent runs `git read-tree HEAD` and `git update-index -q --refresh`, then rewrites one entry's object id in `.git/index` to its own blob, keeping that entry's stat data and the TREE extension, and fixes the trailing checksum. `git diff --cached HEAD` takes the `HEAD` side from the cache-tree and prints nothing; `git diff HEAD` trusts the stat data and prints nothing. On `main` and on the head, the blob passes and lands in SSSF's own build commit once the builder edits a file in that folder.
   - The engineer put this in scope on 2026-11-17 for the fix the reviewer suggested: compare `HEAD` with a copy of the index that has no TREE extension, read through `GIT_INDEX_FILE`, so git compares every entry. A narrower fix is fine. A reviewer files the index copy as part of this fix, not as a new mechanism.
   - The copy lives outside the work tree and the git directory, is removed after the command, and is made with the snapshot's settings: filters, hooks and programs off, fail closed on a git failure. Reading it reads no git object `main`'s snapshot doesn't, or the spec says why it must.
   - Stat data in the index is still trusted for the work-tree compare where `main` trusts it. Say in the spec whether the forged stat data hides anything once the index compare is fixed, and if it does, what.
   - `config.md`'s sentence "with no index flag or stat data trusted" is made true, or rewritten to say exactly what is trusted.
3. **Name the old holes left.** `config.md`'s known limits gain two lines:
   - Once an agent stages anything inside an engineer's untracked nested repo, git lists the engineer's other files there as untracked, and the rollback deletes them. That's on `main` too.
   - A link whose target ends in a newline: the snapshot reads the target without it, so an agent's staged link to the same target without the newline isn't seen. It needs such a link in `HEAD`.
   
   `docs/open.md` gets a P2 row for the nested-repo deletion.

## The bar

- The first brief's bar holds, with the three accepted changes above.
- **Rename.** `git mv specs/old.md specs/new.md`, then `commit_all("plan", ["specs/"])`, commits both the add and the delete. So does deleting a spec and writing a similar one.
- **Links.** With `core.symlinks=false`, the engineer's dirty link-file and the agent's `git add -A` pass. With `core.symlinks=true`, the agent's `--cacheinfo 120000` over the engineer's plain file is named.
- **Cache-tree.** Round 1's `aged_cachetree_*` cases, with the files older than git's racy window: for the reviewer on `PROMPT`, the planner on a path outside `specs/`, and the builder on `.claude/settings.json`, on the success and the failure path, the run stops naming the path, and afterwards the index entry and the work tree match `HEAD`. The build commit never holds the forged blob.
- **No false stop.** A fresh `--worktree` checkout, a checkout with a valid cache-tree, and an index with no TREE extension give the same result as on `main` when nothing was staged during the call.
- **Nothing else changes.** With nothing staged during the call, the tree, commits, trace events and exception are as on `main`.

## Running the tests

Run the touched test files from `.claude/skills/sssf` with `-m 'not pushes and not host_only'`, and leave the whole suite to verify. Mark any test that starts a command past agentsh's argument limits, 128 KiB per argument or 2 MiB in all, `host_only`, and never run those in an agent's run. A cache-tree test that needs files older than the racy window sets their times with `os.utime` rather than sleeping.

## Out of scope

- A staged change inside a submodule, the decided limit in `config.md`.
- Fixing the nested-repo deletion and the newline link target; this run names them.
- A plan commit during a merge, which fails closed (round 1's broken promise 1).
- The other P2 rows on `permissions.py`, and the gitignore row.
