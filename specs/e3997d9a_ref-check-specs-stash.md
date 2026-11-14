# Stop the ref check stopping runs for SSSF's own specs and a stash

## Problem

`permissions.ref_moves` (merged in 06f6886) stops runs it shouldn't, and has two gaps in its reads. These are `docs/open.md`'s three P1 rows on the ref check.

- **Specs.** The shipped roster protects `specs/`, and every chain's plan phase commits a spec under `specs/` on its own `sssf/*` branch. Another run's plan commit, or an engineer merge of a finished run, stops a running builder or reviewer. `just trial` attempts stop each other.
- **A stash.** `git stash -u` makes a commit whose third parent is a root commit holding the untracked files. `diff-tree -m --root` diffs the stash commit against that root, which lists every tracked file. Git 2.43 makes that parent even with nothing untracked.
- **Grafts.** `--no-replace-objects` doesn't turn off `info/grafts`. A graft line `<old main> <hijack>` makes the hijack reachable from the old tip, so `rev-list <new> --not <old>` lists nothing.
- **The no-fetch env isn't pinned.** No test fails when `GIT_ALLOW_PROTOCOL=` and `GIT_NO_LAZY_FETCH=1` are removed.

## Files to touch

- `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`
- `.claude/skills/sssf/tests/test_agent_commits.py`
- `.claude/skills/sssf/references/config.md`
- `docs/open.md`

Nothing else. No ADW script, no `agents.py`, no config file, no prompt.

## Threat model

The agent runs as the engineer's own user, in the run's worktree, with a shell. It can write any object into the object store, move any ref with `git update-ref`, write under the git directory (`info/grafts`, `shallow`, a symlinked `info`), set promisor config, and forge commits, tags and trees with `hash-object --literally` or `mktree`, including tree entries named `..` or `.`.

This check runs after the call and only reads. It can't stop an agent that kills the chain first, moves a ref after the check, or lands content by pointing a ref at a commit that already existed. It can't tell an agent's commit from the engineer's or another run's. It judges paths, never content.

## Baseline

`main` at `0829cee`. Every finding is judged against that commit.

## Bar

Must still stop, as the baseline does, for every agent including `writes: []`:

- the `commit-tree` plus `update-ref` hijack of `main`, a tag, another branch or an `sssf/*` branch, with a new commit changing a protected path outside `specs/`;
- every shape in "Why not first-parent judging": the swapped-parent merge reverting a guard fix, its octopus form, the run's own branch as first parent, a new root holding only `src/` as first parent, the forged stash whose third parent is an existing commit;
- a path under `specs/` with a `..`, `.` or empty part; a commit under `.scratch/` on another ref; any `refs/replace/` change;
- a stash of a protected file outside `specs/`, tracked or untracked.

The breach still names the ref, both shas, the commits and the paths.

Must newly stop: the hijack of `main` hidden by a live graft, behind a symlinked `.git/info` or not. The breach names `refs/heads/main`.

Must not stop: another run's plan commit under `specs/`; an engineer merge, true or fast-forward, whose protected changes are only under `specs/`; `git stash` and `git stash -u` of unprotected files, by the engineer or an agent; two runs each committing a plan while the other's agent works.

Must never: delete or overwrite an engineer commit, branch or file; move a ref; run agent code; fetch; run git with a `cwd` outside `run.repo_root`; change `enforce`'s tree check or `undo_commits`; change an ADW script; add module-level mutable state. No agent file kept and no agent code run where the baseline does otherwise.

## Design, made exact

### 1. Specs don't count on other refs

One new module-level constant in `permissions.py`, beside `REF_PATHS_SHOWN`. It is the only new module-level name in the module, and it goes in the first build:

```python
# The one folder ref_moves() doesn't judge on other refs. Every chain's plan
# phase commits its spec here on its own sssf/* branch, so judging it stops
# every other live run. A spec runs nothing. .scratch/ is not here:
# adw_tickets hands a ticket under it straight to ticket_builder.
REF_SPECS = "specs/"
```

In `ref_moves` only, a path is exempt when both hold:

- `path.startswith(REF_SPECS)`;
- no part of `path.split("/")` is `""`, `"."` or `".."`.

An exempt path is dropped before `permitted` is asked. Everything else is judged as today (`not permitted(p, agent, cfg) and not permitted(p, open_, cfg)`). Write the test as a nested function inside `ref_moves`, not a module-level one. `permitted`, `enforce`'s tree check, `_roll_back` and `undo_commits` don't change, so a builder still can't write `specs/` in its own tree. `specs` itself (a file or link of that name) and `Specs/` are not exempt.

### 2. Every parent but a new root that isn't the first

The brief's rule is "every parent, except a parent that is a root commit". Taken literally it opens three holes the baseline stops, so the exact rule is narrower:

- A one-parent commit on a root would be diffed against nothing. The shipped test repo's `main` is a root, so the plain hijack would pass.
- A merge of two roots would be diffed against nothing.
- An old root is never judged by its own tree, since only new commits are listed. A forged stash with the repo's first commit as third parent would pass, and `git stash pop` restores from it.

**The rule.** Let *new* be the commits `rev-list --parents <moved tips> --not <old shas>` lists, each with its parents in order.

- A new commit with no parents is listed by its whole tree.
- A new commit with parents is diffed against each parent, except a parent that is all three of: not the first parent; itself in *new*; listed with no parents. The first parent is always diffed.

A real `git stash -u` commit has parents `HEAD`, the index commit and the untracked root. It is diffed against the first two. The root is new, so its tree (the untracked files only) is listed.

**Implementation.**

- In `ref_moves`, the first `rev-list` call gains `--parents`. Parse each line as `<sha> [<parent>...]`. Every token must match the sha pattern `parse` already uses; otherwise return `cannot(...)`. Keep `commits` in the listed order and build `parents: dict[str, list[str]]`.
- For each commit, the parents to diff against are `parents[c]` minus the skipped ones above. It is empty only when `parents[c]` is empty.
- `_COMMIT_PATHS` takes, per commit, the words `<commit> <parent>... .` (a literal `.` ends the group). For a group with no parents it lists the tree; otherwise it diffs each pair. It prints the same output as today: NUL-terminated paths, then one empty field per commit. Reference script, with the `_REF_READ` line of section 3 inside `g`:

```sh
g() { env GIT_ALLOW_PROTOCOL= GIT_NO_LAZY_FETCH=1 GIT_GRAFT_FILE=/dev/null LC_ALL=C git -c core.fsmonitor= -c core.commitGraph=false -c advice.graftFileDeprecated=false --no-replace-objects "$@"; }
while [ $# -gt 0 ]; do
  c=$1; shift
  if [ "$1" = . ]; then
    g ls-tree -r -z --name-only --full-tree "$c" || exit 1
  fi
  while [ $# -gt 0 ] && [ "$1" != . ]; do
    g diff-tree -r --name-only -z --no-renames --no-ext-diff --no-textconv "$1" "$c" || exit 1
    shift
  done
  [ $# -gt 0 ] || exit 1
  shift
  printf '\0'
done
```

- `ls-tree`, not `diff-tree --root`: it lists the commit's own tree whatever git takes its parents to be (a `shallow` file can hide them).
- Chunk by commit as today (`_CHUNK`). The `fields.count("") != len(chunk)` check stays. Any failure is still `cannot(err)`.
- The second `rev-list`, which builds the message, is unchanged. Update the comment above `_COMMIT_PATHS` and the `ref_moves` docstring to state the rule.
- All calls keep `cwd=run.repo_root`.

Checked in a throwaway repo on git 2.43: `rev-list --parents` prints excluded parents too; the pair form of `diff-tree` lists the same paths `-m` does for that parent; a stash with nothing untracked has an empty-tree root as third parent.

### 3. No grafts

`_REF_READ` and the git line in `_COMMIT_PATHS` add `GIT_GRAFT_FILE=/dev/null` to the env and `-c advice.graftFileDeprecated=false` to the flags, keeping everything they set today. Without the advice flag, git prints its three-line grafts hint on stderr whenever the graft file opens, `/dev/null` included, and `cannot()` would quote it. Update the comment above `_REF_READ`.

Checked: with the graft `<old main> <hijack>`, the baseline's `rev-list` lists nothing; with `GIT_GRAFT_FILE=/dev/null` it lists the hijack; with the advice flag a failing read prints only its `fatal:` line.

## Costs

- An agent can add, change or delete anything under `specs/` on another ref, `main` included, without a stop. A spec on `main` runs nothing, but a later run's planner may read it. A project roster entry that protects a file under `specs/` by another pattern is exempt on other refs too.
- A ticket run's commits under `.scratch/` still stop other runs' builders and reviewers.
- **What skipping a new root parent gives up.** The baseline's diff against the skipped root `u` lists path `p` when the commit `c` and `u` differ at `p`. If `p` is in `u`'s tree, `u`'s own listing shows it. If not, `c` holds `p`, and `c` is still diffed against its first parent `f`: a difference shows there. What is no longer listed is a path `c` carries unchanged from `f` that `u` lacks. That content is `f`'s. If `f` is new, it is judged by its own diffs. If `f` already existed, this is a merge of an existing commit with a new root: `c` has `f`'s tree, and the ref moves to `c`. The baseline stopped that by the accident of the root diff, but it already passes the same content by `git update-ref <ref> <f>` and by re-parenting, both filed in `docs/open.md`. It can't be a fast-forward the baseline refused: `c` descends from the ref's old tip only through `f`, since a new root is never an old tip. So no shape lands protected content through a root parent that the root's tree and the other parents don't show, other than content of an existing commit, which is the open re-parenting row.
- Either no-fetch variable alone prevents the fetch, so the test pins the pair, not each one.

## Steps

1. Edit `permissions.py` as in sections 1 to 3. Also correct the module docstring's ref paragraph if it no longer holds.
2. Add the tests below to `test_agent_commits.py`, in the "refs other than HEAD" part, on the `shipped` fixture and the roster SSSF ships. Use `go`, `stopped`, `plant`, `hijack`, `roster`, `promisor`. Add helpers as needed (a second worktree with a commit; a commit from an arbitrary tree built with `read-tree --empty` on a temp index or `git mktree`). No test starts an agent or pushes.
3. Update `test_a_roster_protected_path_is_stopped`: it plants `specs/note.md` on another ref and expects a stop. Change it to plant under `.scratch/` (assert `.scratch/` is in `protected_files`), which is the bar's `.scratch/` case.
4. Update `config.md` and `docs/open.md` as under Docs.

### Tests to add

The engineer's actions happen in a second worktree on `main` (`git worktree add <tmp>/eng main`), never in the run's tree.

| Test | Expect | On baseline |
|---|---|---|
| another worktree commits `specs/plan.md` on `sssf/other` during a `builder` and a `reviewer` call | no stop | stops |
| two runs, two worktrees: A's builder call spans B's plan commit, then B's builder call (run object with `repo_root` at B's worktree) spans a plan commit on A's branch | no stop for either | stops |
| engineer `git stash -u`: tracked unprotected change with nothing untracked; with an untracked file too | no stop | stops |
| engineer plain `git stash`, and a `reviewer`'s own `git stash -u` of `src/app.py` in the run's tree | no stop | first passes |
| stash of tracked `R`; `stash -u` of a new untracked file beside `R` | stopped, path named | stops |
| true merge into `main` (`merge --no-ff`, `main` moved on first) of a branch changing `specs/` and `src/` only; the fast-forward form | no stop | true merge stops |
| the same true merge with `R` changed on the branch | stopped, names `R` | stops |
| swapped-parent merge: `main` is base then fix; merge of (base, fix) with base's tree | stopped | stops |
| octopus form: (base, a side commit, fix) with base's tree | stopped | stops |
| own branch first: own branch holds a committed `R` change; merge of (own, main) with own's tree on `main` | stopped | stops |
| new root with only `src/`: merge of (root, main) with the root's tree, with `main` at a root and at a later commit; and the order (main, root) | stopped | stops |
| forged stash on `refs/stash`: (main, index commit, third parent) where the third parent is an existing non-root commit with another `R`; and where it is the repo's first commit | stopped | stops |
| a merge of two new roots whose tree adds `R`; a one-parent commit adding `R` on a new root, on a new branch | stopped | stops |
| tree entries `specs/../x.py` and `specs/./x.py` on `main`, built with `git mktree` | stopped | stops |
| a `.scratch/` commit on another ref | stopped | stops |
| a failing read (the missing-tree case) in a repo with a `.git/info/grafts` file | "could not compare", and no `grafts` or `hint:` in the breach | n/a |
| graft `<old main> <hijack>` written to `.git/info/grafts`; and behind `.git/info` replaced by a symlink, through `guard_git_dir`, `restore_git_dir` (breach caught), `undo_commits`, `enforce`, with `HOME` set to a temp dir | breach names `refs/heads/main` and the hijack sha | passes without it |
| promisor repo (`promisor()`), a commit on `refs/heads/other` whose tree is missing | "could not compare", marker absent | passes, pins nothing yet |
| promisor repo, an annotated tag object (`hash-object -t tag --literally`) naming a missing commit, at `refs/tags/t` | "could not compare", marker absent | same |

The missing-tree case pins `_COMMIT_PATHS` (`rev-list` reads no tree). The tag case pins `_REF_READ` (`rev-list` must read the tagged commit). Checked on git 2.43: without the env each one runs the `uploadpack` script.

Two things were not checked while planning; confirm them and report in the envelope, don't bend a test to fit:

- that `git mktree` accepts entries named `..` and `.`. If it refuses, write the tree with `hash-object -t tree --literally`.
- that the symlinked-`info` form fails on the baseline, as the brief says. The plain graft form does.

## Verification

Run from a copy outside the repo, with the copy command the build brief gives (the suite reads `.env` from the launch checkout, which the guard refuses). Under agentsh run only:

```
pytest tests/test_agent_commits.py -q
```

- Every existing ref test passes, with only the edit in step 3.
- Each "stops" or "passes without it" row above: run the new test against the baseline `permissions.py` (`git show 0829cee:.claude/skills/sssf/templates/adws/adw_modules/permissions.py` into the copy) and confirm the no-stop rows and the graft rows fail there, and the stopped rows pass on both.
- In the copy, remove `GIT_ALLOW_PROTOCOL=` and `GIT_NO_LAZY_FETCH=1` from `_REF_READ` only: the tag test fails. From `_COMMIT_PATHS` only: the missing-tree test fails. Restore both.
- In the copy, remove `GIT_GRAFT_FILE=/dev/null`: both graft tests fail.
- `git diff --stat` in the worktree names only the four files above.

The chain's verify phase runs the whole suite outside agentsh, `tests/test_export.py` and `tests/test_install_hook.py` included. Never run `git push`.

## Docs

- `.claude/skills/sssf/references/config.md`, "Refs other than HEAD": say paths under `specs/` aren't judged on other refs and why, and that `.scratch/` is; replace "A merge is judged against each parent" with the rule in section 2; say grafts are off for the check; rewrite the costs list (drop `specs/` from concurrent runs, keep `.scratch/` and the other protected paths; a stash stops only when it holds a protected file outside `specs/`); add the three costs above.
- `docs/open.md`: delete the row "`git stash -u` during any agent call stops the run" and the row "Two gaps in the ref check's own reads". Rewrite the row "The after-call ref check stops a run when another run commits a protected path" to what remains: `.scratch/` and the other protected paths, and the engineer's own protected commits.
- Docstrings and comments in `permissions.py`, as in the steps.
- The repo keeps no CHANGELOG. No `app_docs/` write-up is planned here; the documenter may note that the ae5ebd82 ref-check write-up describes the old `-m --root` judging.

## Out of scope

Old holes, to be filed as such:

- re-parenting, a fast-forward or rewind to an existing commit, symbolic refs, the two-call form, pseudo-refs outside `refs/`, another worktree's `HEAD`, and an agent's `writes` applying on every ref;
- the merge of an existing commit with a new root, which is the re-parenting row in another spelling (see Costs);
- commits changing only unprotected paths on another ref (`uv.toml` and the like);
- the hub's own `adws/adw_modules/`, which doesn't have the ref check;
- the engineer's own commits to other protected paths during a call, which still stop the run;
- a `.git/shallow` file, which can only make the check list more;
- the git-directory restore leaving a symlinked `.git/info` in place.

## For the engineer

Nothing. Every protected path this plan changes is under `.claude/skills/sssf/`, which `guard_builder`'s `writes` names.
