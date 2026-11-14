# Stop the ref check stopping runs for SSSF's own specs and a stash (fourth plan)

This replaces `specs/e3997d9a_ref-check-specs-stash_v3.md` for the builder. The third plan's build is committed on the run's branch as `43db4df`. Its bypass review found two holes, and this plan closes them:

- **Root-ness came from `rev-list --parents`**, which honours `.git/shallow`. A parent listed there looked like a root and was dropped from the diff, so the swapped-parent merge passed again.
- **Every path under `specs/` was exempt**, so `specs/CLAUDE.md`, a symlink or a subfolder could land on `main`.

Everything else in `43db4df` stays: the graft line, the bound on parents, and its tests.

## Problem

`permissions.ref_moves` (merged in 06f6886) stops runs it shouldn't, and has two gaps in its reads. These are `docs/open.md`'s three P1 rows on the ref check.

- **Specs.** The shipped roster protects `specs/`, and every chain's plan phase commits a spec there on its own `sssf/*` branch. Another run's plan commit, or an engineer merge of a finished run, stops a running builder or reviewer.
- **A stash.** `git stash -u` makes a commit whose third parent is a root commit holding the untracked files. `diff-tree -m --root` diffs the stash commit against that root, which lists every tracked file.
- **Grafts.** `--no-replace-objects` doesn't turn off `info/grafts`, and a graft can hide a hijack from `rev-list`.
- **The no-fetch env isn't pinned** by any test.

## Files to touch

- `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`
- `.claude/skills/sssf/tests/test_agent_commits.py`
- `.claude/skills/sssf/references/config.md`
- `docs/open.md`

Nothing else. No ADW script, no `agents.py`, no `sandbox.py`, no config file, no prompt.

## Threat model

The agent runs as the engineer's own user, in the run's worktree, with a shell. It can write any object into the object store, move any ref with `git update-ref`, and write under the git directory: `info/grafts`, a symlinked `info`, and `shallow`, which nothing watches or restores, in the common directory when the run is a linked worktree. It can set promisor config, and forge commits, tags and trees: any mode, tree entries named `..` or `.`, and a commit with any number of parents.

This check runs after the call and only reads. It can't stop an agent that kills the chain first, moves a ref after the check, or lands content by pointing a ref at a commit that already existed. It can't tell an agent's commit from the engineer's or another run's. It judges paths and file modes, never content.

## Baseline

`main` at `9fac6b3`. Every finding is judged against that commit. Its guard code is the same as `0829cee`'s; the commits between change only the task brief and `docs/open.md`.

## Bar

Must still stop, as the baseline does, for every agent including `writes: []`:

- the `commit-tree` plus `update-ref` hijack of `main`, a tag, another branch or an `sssf/*` branch, with a new commit changing a protected path outside `specs/`;
- every shape in the brief's "Why not first-parent judging", and the swapped-parent merge whose second parent is listed in `.git/shallow`;
- a path under `specs/` that isn't a spec file as section 1 defines it, and a `specs` entry in place of the folder;
- a commit under `.scratch/` on another ref; any `refs/replace/` change;
- a stash of a protected file outside `specs/`, tracked or untracked;
- a commit with 60,000 parents, one of them changing a protected path, with no read failing on the argument limit.

The breach still names the ref, both shas, the commits and the paths.

Must newly stop: the hijack of `main` hidden by a live graft, behind a symlinked `.git/info` or not. The breach names `refs/heads/main`.

Must not stop: another run's plan commit adding, changing or deleting a spec file; an engineer merge, true or fast-forward, whose protected changes are only spec files; `git stash` and `git stash -u` of unprotected files, by the engineer or an agent; two runs each committing a plan while the other's agent works.

Must never: delete or overwrite an engineer commit, branch or file; move a ref; run agent code; fetch; run git with a `cwd` outside `run.repo_root`; pass git or `sh` an argument list whose length a commit decides; change `enforce`'s tree check or `undo_commits`; change an ADW script; add module-level mutable state. No agent file kept and no agent code run where the baseline does otherwise.

## Design, made exact

### 1. Spec files don't count on other refs

**Read modes, not only names.** Both diff commands in `_COMMIT_PATHS` print `--raw` in place of `--name-only`. With `-z` each change is two NUL-terminated fields: `:<old mode> <new mode> <old sha> <new sha> <status>`, then the path. An absent side has mode `000000`. `--raw` reads no blob. `ref_moves` parses each commit's fields in pairs. An odd count, or a first field that doesn't fully match `:[0-7]{6} [0-7]{6} [0-9a-f]+ [0-9a-f]+ [A-Z]`, returns `cannot(...)`.

**A spec-file change** is a record where all of these hold:

- the path is `REF_SPECS` plus a name with no `/` in it;
- the name ends in `.md` and doesn't start with `.`;
- the name, lowercased, is not in `REF_SPEC_NOT`;
- the old mode and the new mode are each `100644` or `000000`.

A spec-file change is dropped before `permitted` is asked. So adding, changing and deleting a regular `specs/<name>.md` are exempt. Everything else is judged as today: a subfolder, a symlink (`120000`), a gitlink (`160000`), an executable file (`100755`), a type change to or from any of those, a dotfile, a non-`.md` file, an instruction file. The name test covers `..`, `.` and empty parts, since any of them needs a `/`.

**The folder itself.** A record whose path is `specs` (`REF_SPECS` without its slash) is judged as the path `REF_SPECS`, and shown as `specs`. Without this, a commit that deletes the spec files and puts a `specs` symlink in their place would pass: the deletes are exempt, and `specs` alone matches no protected pattern.

**Constants.** Module-level, immutable, beside `REF_PATHS_SHOWN`. `REF_SPECS` exists; replace its comment. `REF_SPEC_NOT` is new:

```python
# Where plan phases commit specs/<adw_id>_<slug>.md on their own sssf/* branch.
# ref_moves() doesn't judge a regular .md file directly in it on other refs,
# or every plan commit would stop every other live run. Anything else under it
# counts. .scratch/ is not here: adw_tickets hands a ticket under it straight
# to ticket_builder.
REF_SPECS = "specs/"
# Never a spec file: a harness loads these as instructions when an agent reads
# the folder, and planners read specs/. Compared lowercased, for a
# case-insensitive disk.
REF_SPEC_NOT = ("claude.md", "claude.local.md", "agents.md")
```

The record test and the path test are functions nested in `ref_moves`. `permitted`, `enforce`'s tree check, `_roll_back` and `undo_commits` don't change, so a builder still can't write `specs/` in its own tree.

### 2. Every parent, as on `main`, but a stash's untracked root

**The rule.** Let *new* be the commits `rev-list --parents <moved tips> --not <old shas>` lists, each with the parents it lists. A parent is *skipped* for commit `c` when all four hold:

- `c` is listed with at most three parents;
- the parent is not `c`'s first;
- the parent is itself in *new*;
- the parent is a real root: its raw object has no parent line.

Every commit with no skipped parent is read exactly as on `main`: `diff-tree -r -m --root <commit>`, where git reads the parents itself. A commit with a skipped parent is diffed against each of its other parents, one pair at a time. The skipped root is new, so it is read as on `main`, and `--root` lists its whole tree.

**Root-ness from the raw object.** `rev-list --parents` shows a commit listed in `.git/shallow` with no parents though it has them. So a parent `rev-list` shows without parents is only a candidate. A new script, `_COMMIT_ROOTS`, reads each candidate's raw object and prints one character for it:

```sh
g() { env GIT_ALLOW_PROTOCOL= GIT_NO_LAZY_FETCH=1 GIT_GRAFT_FILE=/dev/null LC_ALL=C git -c core.fsmonitor= -c core.commitGraph=false -c advice.graftFileDeprecated=false --no-replace-objects "$@"; }
for c in "$@"; do
  g cat-file commit "$c" 2>/dev/null | {
    IFS= read -r t; IFS= read -r p
    case $t in
      "tree "*) case $p in "parent "*) printf p ;; *) printf r ;; esac ;;
      *) printf '?' ;;
    esac
  }
done
```

- `r`: the first line is the tree and the second isn't a parent. Git's own parser takes as parents only the `parent ` lines directly after the `tree` line, so this is what git reads without `shallow`, grafts or replace refs.
- `p`: it has a parent. `?`: the object couldn't be read as a commit.
- Only `r` makes a root. `p` and `?` leave the parent in the diff, as on `main`.
- The candidates are the commits that are a non-first parent of a new commit with at most three parents, are in *new*, and are listed without parents. They go as arguments, `_CHUNK` at a time, one sha each, as `main` sends commits. The script isn't run when there are none.
- `ref_moves` checks the output is one of `r`, `p`, `?` per candidate, else returns `cannot(...)`.

`named(c)` in `43db4df` keeps its shape; "has no parents" becomes "is in the set of real roots".

**Why each condition.**

- *Not the first.* A one-parent commit on a new root, and a merge of two new roots, would be diffed against nothing.
- *New.* Only new commits are read by their own tree. The shipped test repo's `main` is an old root.
- *At most three parents.* It keeps argument lists short. See "Departure from the brief".
- *A real root.* A commit with parents that looks like a root has only its own tree read, and what it took from its parents isn't.

**What `.git/shallow` can still do to this rule: nothing looser than `main`.** `shallow` only removes a listed commit's parents, all of them. If `c` is listed, `rev-list` shows it without parents and it is read by `main`'s command. If an old commit is listed, more commits count as new, and each is read. A skipped parent is a real root whatever `shallow` says, and `--root` lists a real root's whole tree whatever `shallow` says.

**`_COMMIT_PATHS`** keeps its words, `<commit> [<parent> [<parent>]] .`, and its bound: a call names at most 256 commits and two parents each, about 50 KB at worst. Only `--name-only` becomes `--raw`.

Checked on git 2.43 in a throwaway repo:

- with `m2` in `.git/shallow`, `rev-list --parents` prints `m2` alone, the script above prints `p` for it, and the baseline's read of the merge lists the guard file;
- the script prints `r` for a root, `p` for a commit with a parent, `?` for a missing sha and for a tree;
- `--raw -z` prints the records as described, for the one-commit form, the pair form and `--root`: `120000` for a symlink, `160000` for a gitlink, `100755` after `chmod +x`, `000000` on the absent side;
- a `shallow` file in the main git directory applies in a linked worktree.

### 3. No grafts

Unchanged from `43db4df`. `_REF_READ`, `_COMMIT_PATHS` and now `_COMMIT_ROOTS` set `GIT_GRAFT_FILE=/dev/null` and `-c advice.graftFileDeprecated=false`, beside the env and flags `main` sets.

### Departure from the brief

The brief's rule says the script gets its commits and parents on stdin. `Sandbox.run` has no stdin parameter, and adding one changes `sandbox.py` and every sandbox class for one caller. This plan passes no list a commit can lengthen: commits go as on `main`, 256 a call, and at most two parents are named per commit. A commit with 60,000 parents is read by `main`'s own command.

## Costs

- An agent can add, change or delete a spec file on another ref, `main` included, without a stop. A spec on `main` runs nothing, but a later run's planner may read it as it reads any spec.
- A spec that isn't a regular `.md` file directly in `specs/` still stops other runs: one in a subfolder, one with the executable bit, one named `X.MD`. In the hub that includes the engineer's own commit of a task brief under `specs/tasks/` during an agent call.
- A ticket run's commits under `.scratch/` still stop other runs' builders and reviewers.
- A commit with four parents or more that has a new root among them is judged against the root too, as on `main`.
- **What skipping the root gives up.** The baseline's diff against the skipped root `u` lists path `p` when commit `c` and `u` differ at `p`. If `p` is in `u`'s tree, `u`'s own listing shows it. If not, `c` holds `p`, and `c` is still diffed against its first parent `f`: a difference shows there. What is no longer listed is a path `c` carries unchanged from `f` that `u` lacks. That content is `f`'s. If `f` is new, it is judged by its own read. If `f` already existed, the shape is a two- or three-parent merge of an existing commit with a new root, holding `f`'s tree. The baseline stopped it by the accident of the root diff, but passes the same content by `git update-ref <ref> <f>` and by re-parenting, both filed in `docs/open.md`. It can't be a fast-forward the baseline refused: `c` descends from the ref's old tip only through `f`, since a real root has no ancestors and an old tip is never new. So no shape lands protected content through the skipped root that the root's tree and the other parents don't show, other than content of an existing commit.
- One more `sh` call, and one `git cat-file` per candidate, only when a new commit has a parentless new commit as a later parent: a stash.
- Either no-fetch variable alone prevents the fetch, so the tests pin the pair. `_COMMIT_ROOTS` reads only commits `rev-list` has just read, so no test can make it fetch.

## The 60,000-parent test and agentsh

agentsh refuses any command whose arguments pass 64 KB, and the refusals stop the call. For the builder, every fix round and the bypass reviewer:

- Never run `test_a_commit_with_sixty_thousand_parents_is_read_and_stopped` against a read that names all of a commit's parents. No red step, and no mutation that drops `len(ps) <= 3`, in the worktree or a copy.
- `43db4df`'s read is bounded. Keep the bound through every edit. While `ref_moves` is half-edited, leave the test out: `-k "not sixty_thousand"`.
- A harness that calls the ref check from inside an agent's call gives git a long list through a file or stdin, never as arguments.

## Steps

Start from `43db4df`, the worktree's HEAD.

1. `permissions.py`: `--raw` in `_COMMIT_PATHS` and the record parsing; the spec-file test and the `specs` path rule in place of `exempt`; `REF_SPEC_NOT`; `_COMMIT_ROOTS` and the real-root set in `named`. Update the comments above the constants and scripts, the `ref_moves` docstring and the module docstring's ref paragraph.
2. `test_agent_commits.py`: add the tests marked "new" below. Keep the rest. If an existing no-stop test uses a path that is no longer a spec file, give it a regular `specs/<name>.md`.
3. `config.md` and `docs/open.md`, as under "Docs".

The new module-level names are `REF_SPEC_NOT` and `_COMMIT_ROOTS`. Both go in this build. If the build needs another, it goes in this build too, not a fix round.

## Tests

In `.claude/skills/sssf/tests/test_agent_commits.py`, on the `shipped` fixture and the roster SSSF ships, through `go` and `stopped`. No test starts an agent or pushes. Build trees with plumbing (`update-index --cacheinfo <mode>,<sha>,<path>` on a temp index, or `mktree`). Don't make the symlinks on disk: under agentsh, `ln -s ../.env` is refused.

New in this build:

| Test | Expect | On `43db4df` | On baseline |
|---|---|---|---|
| `main` is base then fix. `m2` is a new commit on `main` with an empty tree. The merge of (base, `m2`) with base's tree goes on `main`, and `m2`'s sha is written to `.git/shallow`. As `builder` and as `reviewer` | stopped, names `R` | passes | stops |
| the same with the run in a linked worktree, writing `shallow` in the common git directory | stopped | passes | stops |
| on `main`, one commit each adding `specs/CLAUDE.md`, `specs/CLAUDE.local.md`, `specs/AGENTS.md`, `specs/claude.md`, `specs/.claude/skills/x/SKILL.md`, `specs/sub/x.md`, `specs/.hidden.md`, `specs/notes.txt` | each stopped, path named | passes | stops |
| on `main`, `specs/env.md` as a symlink to `../.env` (mode `120000`); a gitlink `specs/link.md` (`160000`); `specs/run.md` with mode `100755`; an existing spec file changed to a symlink | each stopped | passes | stops |
| on `main`, the spec files deleted and `specs` added as a symlink | stopped, names `specs` | passes | stops |
| on `main`, a commit adding `specs/<id>_x.md`, one changing it, one deleting it, as `builder` and `reviewer` | no stop | no stop | stops |

Kept from `43db4df`, all still passing: the plan-commit and two-run tests, the stash tests, the merge tests, each "Why not first-parent judging" shape, the two-new-roots and one-parent-on-a-new-root tests, `specs/../x.py` and `specs/./x.py`, the `.scratch/` commit, the failing read with no grafts hint, both graft tests, the missing-tree and missing-tagged-commit tests that pin the no-fetch env, the 60,000-parent test, the four-parent test, and every ref test of the baseline.

## Verification

Run from a copy outside the repo, with the copy command the build brief gives (the suite reads `.env` from the launch checkout, which the guard refuses). Under agentsh run only `tests/test_agent_commits.py`.

1. `pytest tests/test_agent_commits.py -q`, whole, once the read is finished and still bounded. Everything passes.
2. In the copy, with `43db4df`'s `permissions.py` (`git show 43db4df:.claude/skills/sssf/templates/adws/adw_modules/permissions.py`) and `-k "not sixty_thousand"`: the new stopped rows fail, the new no-stop row passes.
3. In the copy, with the baseline's (`git show 9fac6b3:` the same path) and `-k "not sixty_thousand"`: the new stopped rows pass, the no-stop rows fail.
4. In the copy, with `-k "not sixty_thousand"`: make `_COMMIT_ROOTS` print `r` for every commit, and both shallow tests fail. Restore it.
5. In the copy, with `-k "not sixty_thousand"`: remove `GIT_ALLOW_PROTOCOL=` and `GIT_NO_LAZY_FETCH=1` from `_REF_READ` only, and the tag test fails; from `_COMMIT_PATHS` only, and the missing-tree test fails. Remove `GIT_GRAFT_FILE=/dev/null`, and both graft tests fail. Restore each.
6. `git diff --stat 43db4df` in the worktree names only the four files above.

There is no check that drops the bound on parents. Report each numbered check in the envelope with its result. The chain's verify phase runs the whole suite outside agentsh. Never run `git push`.

## Docs

- `.claude/skills/sssf/references/config.md`, "Refs other than HEAD": what a spec file is, that only those aren't judged on other refs, and why instruction files, links and subfolders are; that a root is told from its raw object, not from `rev-list`, and why; the costs above.
- `docs/open.md`: add the row under "Out of scope" below on a tip listed in `.git/shallow`, unless the file already has one. Type Bug, priority P2, severity High, status Ready, background `config.md`.
- Comments and docstrings in `permissions.py`, as in step 1.
- The repo keeps no CHANGELOG. No `app_docs/` edit is planned; the documenter may note that the ae5ebd82 ref-check write-up describes the old judging.

## Out of scope

Old holes, to be filed as such:

- **A new commit listed in `.git/shallow` is read by its own tree only, on `main` too.** `rev-list` and `diff-tree` both take it for a root. So a tip that only deletes protected paths, such as `main` moved to a commit holding only `src/`, isn't stopped, and its new ancestors aren't listed. Nothing watches or restores `.git/shallow`. Checked on git 2.43: with the stripped tip in `shallow`, the baseline's read lists only `src/app.py`; without it, the deleted files. `GIT_SHALLOW_FILE=/dev/null` in the env made `rev-list` show the real parents; it wasn't tried on `diff-tree`, and git doesn't document it. This is the new `docs/open.md` row;
- re-parenting, a fast-forward or rewind to an existing commit, symbolic refs, the two-call form, pseudo-refs outside `refs/`, another worktree's `HEAD`, and an agent's `writes` applying on every ref;
- the merge of an existing commit with a new root, which is the re-parenting row in another spelling (see Costs);
- commits changing only unprotected paths on another ref (`uv.toml` and the like);
- a spec file's content: a spec on `main` that misleads a later planner;
- the `rev-list` calls naming every old ref sha as an argument, as on `main`;
- stdin for `Sandbox.run`;
- the hub's own `adws/adw_modules/`, which doesn't have the ref check;
- the engineer's own commits to other protected paths during a call, which still stop the run;
- the git-directory restore leaving a symlinked `.git/info` in place.

## For the engineer

No protected path waits on you: every one this plan changes is under `.claude/skills/sssf/`, which `guard_builder`'s `writes` names.

Three things to know:

- The shallow-listed tip above is a hole on `main` today, found while checking this plan. This change doesn't close it.
- The brief asks for commits and parents on stdin. This plan still names at most two parents per commit as arguments, because `Sandbox.run` takes no stdin.
- `main` has moved to `8f903b9` since the brief named `9fac6b3`; the one commit between changes only `docs/open.md`. The run's branch has its own `docs/open.md` edits, so the merge may need a hand.
