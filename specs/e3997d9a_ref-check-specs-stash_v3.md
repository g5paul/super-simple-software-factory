# Stop the ref check stopping runs for SSSF's own specs and a stash (third plan)

This replaces `specs/e3997d9a_ref-check-specs-stash_v2.md` for the builder. The design is the second plan's. What changes is the order of work and the verification: the second build ran the 60,000-parent test against the first build's read, which names every parent as an argument, and agentsh refused it 51 times.

The second build is uncommitted in the worktree. Its `permissions.py` already has the read section 2 specifies, and its test file has the tests in the table. "Steps" says how to finish from there.

## Problem

`permissions.ref_moves` (merged in 06f6886) stops runs it shouldn't, and has two gaps in its reads. These are `docs/open.md`'s three P1 rows on the ref check.

- **Specs.** The shipped roster protects `specs/`, and every chain's plan phase commits a spec there on its own `sssf/*` branch. Another run's plan commit, or an engineer merge of a finished run, stops a running builder or reviewer.
- **A stash.** `git stash -u` makes a commit whose third parent is a root commit holding the untracked files. `diff-tree -m --root` diffs the stash commit against that root, which lists every tracked file. Git 2.43 makes that parent even with nothing untracked.
- **Grafts.** `--no-replace-objects` doesn't turn off `info/grafts`. A graft line `<old main> <hijack>` makes the hijack reachable from the old tip, so `rev-list <new> --not <old>` lists nothing.
- **The no-fetch env isn't pinned.** No test fails when `GIT_ALLOW_PROTOCOL=` and `GIT_NO_LAZY_FETCH=1` are removed.

## Files to touch

- `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`
- `.claude/skills/sssf/tests/test_agent_commits.py`
- `.claude/skills/sssf/references/config.md`
- `docs/open.md`

Nothing else. No ADW script, no `agents.py`, no `sandbox.py`, no config file, no prompt.

## Threat model

The agent runs as the engineer's own user, in the run's worktree, with a shell. It can write any object into the object store, move any ref with `git update-ref`, write under the git directory (`info/grafts`, `shallow`, a symlinked `info`), set promisor config, and forge commits, tags and trees with `hash-object` or `mktree`: tree entries named `..` or `.`, and a commit with any number of parents.

This check runs after the call and only reads. It can't stop an agent that kills the chain first, moves a ref after the check, or lands content by pointing a ref at a commit that already existed. It can't tell an agent's commit from the engineer's or another run's. It judges paths, never content.

## Baseline

`main` at `a79ff1b`. Every finding is judged against that commit. Its guard code is the same as `0829cee`'s; the commits between change only the task brief.

## Bar

Must still stop, as the baseline does, for every agent including `writes: []`:

- the `commit-tree` plus `update-ref` hijack of `main`, a tag, another branch or an `sssf/*` branch, with a new commit changing a protected path outside `specs/`;
- every shape in the brief's "Why not first-parent judging": the swapped-parent merge reverting a guard fix, its octopus form, the run's own branch as first parent, a new root holding only `src/` as first parent, the forged stash whose third parent is an existing commit;
- a path under `specs/` with a `..`, `.` or empty part; a commit under `.scratch/` on another ref; any `refs/replace/` change;
- a stash of a protected file outside `specs/`, tracked or untracked;
- a commit with 60,000 parents, one of them changing a protected path, with no read failing on the argument limit.

The breach still names the ref, both shas, the commits and the paths.

Must newly stop: the hijack of `main` hidden by a live graft, behind a symlinked `.git/info` or not. The breach names `refs/heads/main`.

Must not stop: another run's plan commit under `specs/`; an engineer merge, true or fast-forward, whose protected changes are only under `specs/`; `git stash` and `git stash -u` of unprotected files, by the engineer or an agent; two runs each committing a plan while the other's agent works.

Must never: delete or overwrite an engineer commit, branch or file; move a ref; run agent code; fetch; run git with a `cwd` outside `run.repo_root`; pass git or `sh` an argument list whose length a commit decides; change `enforce`'s tree check or `undo_commits`; change an ADW script; add module-level mutable state. No agent file kept and no agent code run where the baseline does otherwise.

## Design, made exact

### 1. Specs don't count on other refs

One new module-level constant in `permissions.py`, beside `REF_PATHS_SHOWN`. It is the only new module-level name in the module, and it is already in the tree:

```python
# The one folder ref_moves() doesn't judge on other refs. Every chain's plan
# phase commits its spec here on its own sssf/* branch, so judging it stops
# every other live run. A spec runs nothing. .scratch/ is not here:
# adw_tickets hands a ticket under it straight to ticket_builder.
REF_SPECS = "specs/"
```

In `ref_moves` only, a path is exempt when `path.startswith(REF_SPECS)` and no part of `path.split("/")` is `""`, `"."` or `".."`. An exempt path is dropped before `permitted` is asked. Everything else is judged as today. The test is a function nested in `ref_moves`. `permitted`, `enforce`'s tree check, `_roll_back` and `undo_commits` don't change. `specs` itself (a file or link of that name) and `Specs/` are not exempt.

### 2. Every parent, as on `main`, but a stash's untracked root

**The rule.** Let *new* be the commits `rev-list --parents <moved tips> --not <old shas>` lists, each with its parents in order. A parent is *skipped* for commit `c` when all four hold:

- `c` has at most three parents;
- the parent is not `c`'s first;
- the parent is itself in *new*;
- the parent is listed with no parents.

Every commit with no skipped parent is read exactly as on `main`: `diff-tree -r -m --root <commit>`, where git reads the parents itself. That is every root, every one-parent commit, every merge of existing or non-root commits, and every commit with four parents or more. A commit with a skipped parent is diffed against each of its other parents, one pair at a time.

A real `git stash -u` commit has three parents: `HEAD`, the index commit and the untracked root. It is diffed against the first two. The root is new, so it is read as on `main` and `--root` lists its tree, the untracked files only.

**Why each condition.** The brief says "except a parent that is a root commit". Taken literally that opens holes the baseline stops:

- *Not the first.* A one-parent commit on a new root, and a merge of two new roots, would be diffed against nothing.
- *New.* An old root is never read by its own tree, since only new commits are listed. The shipped test repo's `main` is a root, so the plain hijack would pass, and so would a forged stash with the repo's first commit as third parent.
- *At most three parents.* This is what keeps argument lists short. See "Departure from the brief".

**Implementation.**

- The first `rev-list` call in `ref_moves` has `--parents`. Each line is `<sha> [<parent>...]`. Every word must match the sha pattern `parse` uses; otherwise return `cannot(...)`. Keep `commits` in listed order and `parents: dict[str, list[str]]`.
- A nested function returns the parents to name for a commit: the non-skipped parents when the commit has a skipped one, else an empty list.

```python
def named(c: str) -> list[str]:
    # A stash's untracked-files root: not the first parent, a new commit,
    # no parents of its own. Only for a commit with at most three parents,
    # so no more than two shas are ever named. Empty: git reads the parents.
    ps = parents[c]
    kept = [p for i, p in enumerate(ps) if i == 0 or p not in parents or parents[p]]
    return kept if len(ps) <= 3 and len(kept) < len(ps) else []
```

- `_COMMIT_PATHS` takes, per commit, the words `<commit> [<parent> [<parent>]] .`. A group with no parent runs `main`'s command. A group with parents diffs each pair. Output is as today: NUL-terminated paths, then one empty field per commit.

```sh
g() { env GIT_ALLOW_PROTOCOL= GIT_NO_LAZY_FETCH=1 GIT_GRAFT_FILE=/dev/null LC_ALL=C git -c core.fsmonitor= -c core.commitGraph=false -c advice.graftFileDeprecated=false --no-replace-objects diff-tree -r --name-only -z --no-renames --no-ext-diff --no-textconv "$@"; }
while [ $# -gt 0 ]; do
  c=$1; shift
  if [ "$1" = . ]; then
    g -m --root --no-commit-id "$c" || exit 1
  fi
  while [ $# -gt 0 ] && [ "$1" != . ]; do
    g "$1" "$c" || exit 1
    shift
  done
  [ $# -gt 0 ] || exit 1
  shift
  printf '\0'
done
```

- Chunk by commit as on `main` (`_CHUNK`, 256). The `fields.count("") != len(chunk)` check stays. Any failure is still `cannot(err)`. All calls keep `cwd=run.repo_root`. The second `rev-list`, which builds the message, is unchanged.
- **The bound.** A call names at most 256 commits, each with at most two parents and a dot: 256 × (3 × 65 + 2) = 50,432 bytes with SHA-256 names, 32,000 with SHA-1, plus the script, about 1 KB. That is under agentsh's 64 KB and the kernel's 2 MB, whatever the commits hold. The comment above `_COMMIT_PATHS` says so.

Checked on git 2.43 in a throwaway repo:

- a stash with nothing untracked has an empty-tree root as third parent, and the pair form lists what `-m` lists for that parent;
- `hash-object -t commit -w --stdin` accepts a commit with 60,000 `parent` lines naming one sha, `rev-list --parents` prints all 60,000 on one 2.4 MB line, and `diff-tree -m` on it exits 0 after about 30 seconds, the same on the baseline's flags;
- `diff-tree -m` prints the bare commit id before each parent's paths, never the parent, and prints nothing for a parent with an empty diff even with `--always`. So its output can't be split by parent, which rules out reading every commit with `-m` and dropping the root's part in Python.

### 3. No grafts

`_REF_READ` and the git line in `_COMMIT_PATHS` set `GIT_GRAFT_FILE=/dev/null` and `-c advice.graftFileDeprecated=false`, beside what they set today. Without the advice flag git prints its grafts hint on stderr whenever the graft file opens, `/dev/null` included, and `cannot()` would quote it.

### Departure from the brief

The brief's rule says the script gets its commits and parents on stdin. `Sandbox.run` has no stdin parameter: `LocalSandbox.run` calls `subprocess.run` without `input`, and each remote sandbox would need its own. Adding one changes `sandbox.py` and every sandbox class, two of them guard files, for one caller.

This plan meets the rule's purpose another way: no list a commit can lengthen is passed at all. Commits go as on `main`, 256 a call, which the brief says never hits the limit. Parents are named only for the stash shape, two at most. A commit with 60,000 parents is read by `main`'s own command. If the engineer wants stdin anyway, it is a separate change to the sandbox interface.

## Costs

- An agent can add, change or delete anything under `specs/` on another ref, `main` included, without a stop. A spec on `main` runs nothing, but a later run's planner may read it. A roster entry that protects a file under `specs/` by another pattern is exempt on other refs too.
- A ticket run's commits under `.scratch/` still stop other runs' builders and reviewers.
- A commit with four parents or more that has a new root among them is judged against the root too, as on `main`. An engineer's octopus merge of an unrelated new history during a call stops the run and names every file the root lacks.
- **What skipping the root gives up.** The baseline's diff against the skipped root `u` lists path `p` when commit `c` and `u` differ at `p`. If `p` is in `u`'s tree, `u`'s own listing shows it. If not, `c` holds `p`, and `c` is still diffed against its first parent `f`: a difference shows there. What is no longer listed is a path `c` carries unchanged from `f` that `u` lacks. That content is `f`'s. If `f` is new, it is judged by its own diffs. If `f` already existed, the shape is a two- or three-parent merge of an existing commit with a new root, holding `f`'s tree. The baseline stopped it by the accident of the root diff, but passes the same content by `git update-ref <ref> <f>` and by re-parenting, both filed in `docs/open.md`. It can't be a fast-forward the baseline refused: `c` descends from the ref's old tip only through `f`, since a new root is never an old tip. So no shape lands protected content through the skipped root that the root's tree and the other parents don't show, other than content of an existing commit, which is the open re-parenting row.
- Either no-fetch variable alone prevents the fetch, so the tests pin the pair, not each one.
- The 60,000-parent test takes about 30 seconds of git's own time. It takes the same on `main`.
- No agent shows the 60,000-parent test red. It pins the bound only in the chain's verify phase, outside agentsh.

## The 60,000-parent test and agentsh

agentsh refuses any command whose arguments pass 64 KB, and the refusals stop the call. A read that names a commit's parents as arguments puts 2.4 MB on a command line when it meets this test's commit. So, for the builder, every fix round and the bypass reviewer:

- **Never run `test_a_commit_with_sixty_thousand_parents_is_read_and_stopped` against a read that names all of a commit's parents.** That is the first build's read, and the current read with `len(ps) <= 3` removed.
- **No red step for it.** Don't run it before the read is right to watch it fail.
- **No mutation that drops the bound.** Don't remove `len(ps) <= 3` to see the test fail, in the worktree or in a copy. The chain's verify phase runs the test outside agentsh, and that is where a dropped bound shows.
- Until the read matches section 2, leave the test out: `pytest tests/test_agent_commits.py -q -k "not sixty_thousand"`.
- A harness that calls the ref check from inside an agent's call follows the same rule. It gives git a long list through a file or stdin, never as arguments.

## Steps

The worktree holds the second build, uncommitted. Keep it.

1. **Read before running anything.** Open `permissions.py` in the worktree and compare `_COMMIT_PATHS`, `named` and the words built from it with section 2. Check by reading that `named` returns an empty list for a commit with more than three parents, and that no other call in `ref_moves` puts a commit's parents on a command line. Run no test first.
2. If the read doesn't match, fix it, running only `-k "not sixty_thousand"` until it does.
3. Once it matches, run the whole test file, the 60,000-parent test included.
4. Compare the test file with the table below and add what is missing. Keep the tests that are there.
5. Check `config.md` and `docs/open.md` against "Docs" and finish them.

No new module-level name is needed in `permissions.py`. If the build needs one, it goes in this build, not a fix round.

## Tests

In `.claude/skills/sssf/tests/test_agent_commits.py`, in the "refs other than HEAD" part, on the `shipped` fixture and the roster SSSF ships, through `go` and `stopped`. The engineer's actions happen in a second worktree on `main`, never in the run's tree. No test starts an agent or pushes. No test passes a long list as arguments: a commit body goes to `git hash-object` through `input=`.

| Test | Expect | On baseline |
|---|---|---|
| another worktree commits `specs/plan.md` on `sssf/other` during a `builder` and a `reviewer` call | no stop | stops |
| two runs, two worktrees: A's builder call spans B's plan commit, then B's builder call spans a plan commit on A's branch | no stop for either | stops |
| engineer `git stash -u`: a tracked unprotected change with nothing untracked; with an untracked file too | no stop | stops |
| engineer plain `git stash`; a `reviewer`'s own `git stash -u` of `src/app.py` in the run's tree | no stop | first passes |
| stash of tracked `R`; `stash -u` of a new untracked file beside `R` | stopped, path named | stops |
| true merge into `main` of a branch changing `specs/` and `src/` only; the fast-forward form | no stop | true merge stops |
| the same true merge with `R` changed on the branch | stopped, names `R` | stops |
| swapped-parent merge: `main` is base then fix; merge of (base, fix) with base's tree | stopped | stops |
| octopus form: (base, a side commit, fix) with base's tree | stopped | stops |
| own branch first: merge of (own, main) with own's tree, own holding a committed `R` change | stopped | stops |
| new root with only `src/`: merge of (root, main) with the root's tree, with `main` at a root and at a later commit; and the order (main, root) | stopped | stops |
| forged stash on `refs/stash`: third parent an existing non-root commit with another `R`; third parent the repo's first commit | stopped | stops |
| a merge of two new roots whose tree adds `R`; a one-parent commit adding `R` on a new root | stopped | stops |
| tree entries `specs/../x.py` and `specs/./x.py` on `main` | stopped | stops |
| a `.scratch/` commit on another ref | stopped | stops |
| a failing read in a repo with a `.git/info/grafts` file | "could not compare", no `grafts` or `hint:` in the breach | n/a |
| graft `<old main> <hijack>` in `.git/info/grafts`; and behind a symlinked `.git/info`, through `guard_git_dir`, `restore_git_dir`, `undo_commits`, `enforce` | breach names `refs/heads/main` and the hijack | passes without it |
| promisor repo, a commit on `refs/heads/other` whose tree is missing | "could not compare", marker absent | pins `_COMMIT_PATHS` |
| promisor repo, an annotated tag object naming a missing commit, at `refs/tags/t` | "could not compare", marker absent | pins `_REF_READ` |
| `main` moved to a commit with 60,000 parents, 59,999 naming old `main` and the last a new empty-tree root, whose tree changes `R` | stopped, names `R` and the commit, no "could not compare" | stops |
| a four-parent commit on another branch: (main, two existing commits, a new empty-tree root) with `main`'s tree | stopped, as on `main` | stops |

The 60,000-parent test is one test, not parametrized by agent. Its commit has a new root among its parents, so a read without the bound would name all 59,999 others and fail to start. That is why no agent runs it against such a read.

## Verification

Run from a copy outside the repo, with the copy command the build brief gives (the suite reads `.env` from the launch checkout, which the guard refuses). Under agentsh run only `tests/test_agent_commits.py`.

1. After step 1 confirms the read: `pytest tests/test_agent_commits.py -q`. Everything passes, the 60,000-parent test included. Run it whole once; later runs may leave that test out to save its 30 seconds.
2. Every ref test that exists on the baseline passes. One changes: `test_a_roster_protected_path_is_stopped` plants under `.scratch/`, not `specs/`.
3. Against the baseline's `permissions.py` in the copy (`git show a79ff1b:.claude/skills/sssf/templates/adws/adw_modules/permissions.py`), with `-k "not sixty_thousand"`: the no-stop rows and the graft rows fail, the stopped rows pass.
4. In the copy, with `-k "not sixty_thousand"`: remove `GIT_ALLOW_PROTOCOL=` and `GIT_NO_LAZY_FETCH=1` from `_REF_READ` only, and the tag test fails; from `_COMMIT_PATHS` only, and the missing-tree test fails. Restore both.
5. In the copy, with `-k "not sixty_thousand"`: remove `GIT_GRAFT_FILE=/dev/null`, and both graft tests fail. Restore it.
6. `git diff --stat a79ff1b` in the worktree names only the four files above and the specs.

There is no check that drops the bound on parents. See "The 60,000-parent test and agentsh".

Report each numbered check in the envelope with its result. The chain's verify phase runs the whole suite outside agentsh. Never run `git push`.

## Docs

- `.claude/skills/sssf/references/config.md`, "Refs other than HEAD": paths under `specs/` aren't judged on other refs and why, and `.scratch/` is; the rule in section 2 in place of "A merge is judged against each parent"; grafts are off for the check; the costs above, with `specs/` gone from the concurrent-runs cost and a stash stopping only when it holds a protected file outside `specs/`; under "Size", that a call names at most 256 commits and two parents each.
- `docs/open.md`: the stash row and the "Two gaps" row are deleted, and the first ref-check row is rewritten to what remains (`.scratch/`, the other protected paths, the engineer's own protected commits).
- Docstrings and comments in `permissions.py`: the rule and the bound.
- The repo keeps no CHANGELOG. No `app_docs/` edit is planned; the documenter may note that the ae5ebd82 ref-check write-up describes the old `-m --root` judging of every parent.

## Out of scope

Old holes, to be filed as such:

- re-parenting, a fast-forward or rewind to an existing commit, symbolic refs, the two-call form, pseudo-refs outside `refs/`, another worktree's `HEAD`, and an agent's `writes` applying on every ref;
- the merge of an existing commit with a new root, which is the re-parenting row in another spelling (see Costs);
- commits changing only unprotected paths on another ref (`uv.toml` and the like);
- the `rev-list` calls naming every old ref sha as an argument, as on `main`: a repo with more refs than fit on a command line stops with "could not compare", and under agentsh that is about 1,500 distinct ref shas;
- stdin for `Sandbox.run`;
- the hub's own `adws/adw_modules/`, which doesn't have the ref check;
- the engineer's own commits to other protected paths during a call, which still stop the run;
- a `.git/shallow` file, which can only make the check list more;
- the git-directory restore leaving a symlinked `.git/info` in place.

## For the engineer

No protected path waits on you: every one this plan changes is under `.claude/skills/sssf/`, which `guard_builder`'s `writes` names.

One decision is yours to overturn. The brief asks for commits and parents on stdin; this plan names at most two parents per commit as arguments instead, because `Sandbox.run` takes no stdin. See "Departure from the brief".
