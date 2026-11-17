# Check a run's commits on the host before they land (v2)

This replaces `specs/755d88ad_host-commit-check.md` after spec review 1. What changed: the agents that ran live on `run`, not in a module global; `only` and the chain's own paths are arguments; the carried-work mode is gone, and work already in a local tree passes by matching its blob; `close()` raises on the success path; `config.md`'s known-limit bullets are part of the change.

## Goal

A VM run's commits are judged by the host's own git before the host branch moves, and the chain's own commit is judged the same way in local mode. A refused commit lands nothing. `_roll_back` never deletes inside the git directory.

All module paths below are under `.claude/skills/sssf/`, which `guard_builder`'s `writes` names.

## Threat model

- The agent runs as the engineer's user. Under `--sandbox openshell|vercel|e2b` it controls the whole VM: the clone's `.git`, its index, cache-tree, replace refs, grafts, `shallow`, hooks, filters and config, and every answer the VM gives the host, including `rev-parse HEAD` and the bundle's bytes.
- In a VM run the agent cannot reach the host's `.git`. The host reads only objects its own `index-pack` hashed while unbundling, so an object id means its real content. This check can stop any protected path from landing through `commits_out`.
- It cannot stop, in a VM run: a change inside the agents' `writes` (decision 1); a secret an agent writes into a permitted path; a huge bundle; what the engineer's own `post-merge` hook and checkout filters do with content that passed (decision 3).
- In local mode the agent controls the `.git` the check reads. The check turns off replace refs, grafts, hooks, external diff, textconv and submodule recursion, and reads commit objects raw, so `shallow` can't hide a parent. It cannot stop: an object the agent wrote in the object store, loose or packed or through `objects/info/alternates`, that serves other content under an id (git does not re-hash an object on read); a config file the git-directory guard could not put back (the run already stops there); a planted hook the chain's own `git commit` runs before the check (`docs/open.md` P2 row on `commit_all`); an agent that kills the chain before the check.

## Baseline

`main` at `77b71b5`. Every finding is judged against that commit. There, `Remote.commits_out` fetches the bundle and runs `git merge --ff-only FETCH_HEAD` with no check, `commit_all` is followed by no check, and `_roll_back` deletes a staged-new path named `.git/config`.

## Bar

Against `77b71b5`: no agent file kept on the host branch, no agent code run by the check, no engineer file deleted or overwritten, where the baseline does otherwise.

1. **Each route lands nothing (VM).** Index-only staging, a forged cache-tree, a replace ref or graft hiding a change, a hook or filter that changes the chain's commit, and an agent commit `undo_commits` failed to undo: each leaves the host branch at `synced` and keeps `sandbox-refused.bundle`. After a chain commit, and at the run's end on the success path, the run stops with a `PermissionBreach` naming each commit and path and exits 1. On the failure path the refusal is told to the engineer and the original exception is the one raised.
2. **Bundle shape.** A merge commit, a second root, a prerequisite other than `synced`, more than one ref, or a head that does not descend from `synced` is refused before any `diff-tree` runs.
3. **Git internals.** A replace ref, a graft and a forged cache-tree in the VM change nothing on the host.
4. **The review's holes.** `c4_inverse_forged_tree_plan_commit`, `c2_unsorted_new_protected_then_build`, `c2_unsorted_P_replaced_then_build`: in local mode the branch is back at the commit before, through a VM stand-in at `synced`; the run stops naming the path.
5. **The git directory.** `c2_gitdir_entries_real_guard` with the real guard: `.git/config`, `.git/hooks/pre-push`, `.git/sssf-private-names` hold the engineer's bytes after the call, on the success and the failure path, and the protected write is rolled back.
6. **Local mode.** The index and cache-tree cases of `specs/tasks/guard-index-only-staged-2.md`, with `snapshot`'s in-call check stubbed to see nothing, are refused by the commit check.
7. **No false stop.** Every existing test passes. These commits pass as on `main`: a plan commit of `specs/`, a builder's commit inside its `writes`, the documenter's, the run's session files, the ticket file `tickets.mark_done` writes, and, in local mode, work that was already in the tree when the run's process started (the engineer's uncommitted edits, or a resumed run's earlier work), committed byte for byte.
8. **Nothing else changes.** When every commit passes: same host branch, same `sandbox_commits` and `sandbox_end` payloads, same patch bytes, same console notes. One order differs: `sandbox_end` now comes before the session's end record, because `Run.finish` closes the VM.

## Design notes for the reviewer

- **The judge is one function in `permissions.py`**, beside `permitted` and `_COMMIT_PATHS`. It runs git with `subprocess.run` in a given repo, never through `sandbox.current()`, which is the VM.
- **No module state.** `run.ran` holds the agents that ran since the last full commit that passed. `only` and `wrote` are arguments from `commit_all`.
- **`only` stands in for "only the planner".** The host can't name the planner. A path in an `only` commit passes only when it is inside an `only` entry and an agent in `run.ran` may write it. With decision 1 that is the planner in every shipped chain. `run.ran` is cleared after a full commit passes, never after an `only` commit, since the planner's other work stays in the tree. `config.md` says so.
- **Work already in a local tree.** `session.ensure` records one tree id, `run.carried`: what `git add -A` would commit at that moment, built in a fresh index of SSSF's own. A path the agents may not write still passes when the commit holds exactly the mode and blob that tree holds. No roster fallback. A VM clone starts clean, so a VM run has none.
- **A path with a `.git` component never passes**, for any agent.

## Steps

### 1. `templates/adws/adw_modules/permissions.py`: the judge

Add, near `_COMMIT_PATHS`:

- `_in_git_dir(path) -> bool`: true when any `/` component is one git treats as `.git`. Lowercase it; drop the HFS-ignored code points (U+200C–200F, U+202A–202E, U+206A–206F, U+FEFF); for NTFS cut at the first `:` and strip trailing dots and spaces; match `.git` or `git~1`.
- `carried_tree(repo) -> str`: with a new temp index file outside the repo (`GIT_INDEX_FILE`, removed afterwards), run `read-tree HEAD`, `add -A`, `write-tree` in `repo`, with `-c core.fsmonitor=` and `-c core.hooksPath=/dev/null`, and filters left on so the blobs equal what `commit_all`'s own `git add` makes. Returns the tree id, or `""` when any step fails. It never reads `.git/index`, so a forged cache-tree or an unsorted index from an earlier process can't put a path in it.
- `judge_commits(repo, base, tip, cfg, agents, *, only=None, session="", wrote=(), carried="") -> list[tuple[str, list[str]]]`: `(commit sha, refused paths or one shape reason)` pairs, `[]` when all pass. Every git call is `subprocess.run([*_REF_READ, ...], cwd=repo, capture_output=True)`, bytes decoded `utf-8`/`surrogateescape`. A non-zero exit or unparsable output raises `GitFailed`, which callers treat as a refusal.
  1. Walk from `tip`: `cat-file commit <sha>`, parse the header lines up to the first blank line. Exactly one `parent` line, else refuse the commit with the reason (`a merge`, `a root`). Stop when the parent is `base`. More than 256 commits: refuse. Reaching a root before `base` is the "not a descendant" case. Nothing is diffed until the whole walk passes.
  2. Per commit, oldest first: `diff-tree -r --raw -z --no-renames --no-ext-diff --no-textconv <parent> <commit>`. Parse `:<old mode> <new mode> <old sha> <new sha> <status>\0<path>\0`. Modes are not judged on their own: a gitlink, symlink or mode change is its path.
  3. A path passes when `not _in_git_dir(path)`, and `only` is None or the path equals an `only` entry or is under one ending in `/` (compared literally), and one of:
     - `permitted(path, a, cfg)` for some `a` in `agents`;
     - `session` is non-empty and the path is under it;
     - the path is in `wrote`;
     - `carried` is set and `ls-tree -z <carried> -- <path>` (with `--literal-pathspecs`) gives the same mode and sha as the commit's new side, or the status is `D` and that tree has no such path.
- `refusal_text(refused) -> str`: one line per commit and path, each through `_said_line`, at most `REF_COMMITS_SHOWN` commits and `REF_PATHS_SHOWN` paths each, with the count of the rest.
- `check_local_commit(run, before, *, only=None, wrote=()) -> None`: `tip` is `rev-parse HEAD`; `session` is `run.session_dir` relative to `run.repo_root` when inside it, else `""`. Calls `judge_commits(run.repo_root, before, tip, run.cfg, run.ran, only=only, session=session, wrote=wrote, carried=run.carried)`. On pass with `only` None: `run.ran.clear()`. On refusal or `GitFailed`: `_git(["update-ref", "HEAD", tip_before := before, tip])` in the argument order `update-ref HEAD <before> <tip>`, then `_try_git(["reset", "-q", "--no-recurse-submodules", before])` with `_filters_off`, which rebuilds the index and keeps the work tree; trace an `error` event `commit_refused` with `before`, `head` and `refused`; raise `PermissionBreach` with `refusal_text`, saying the branch is back at `before`, the tree is kept, and whether the index reset failed.

Extend the module docstring with a paragraph on the commit check.

### 2. `permissions.py`: `_roll_back` never deletes in the git directory

- In `_roll_back`, after the `path in before` block and before `kind == "?"`: when `_in_git_dir(path)`, clear the two index flags, unstage with `rm -q --cached --force -- path`, and if that fails `update-index --force-remove -- path`. Return the new constant `GIT_DIR_KEPT = "unstaged; nothing on disk touched, since the path is inside the git directory"`, or `"could not unstage"`. No `delete()`, no `checkout`.
- Make `delete()` return `"left on disk (inside the git directory)"` when `_in_git_dir(path)`, so no later branch can reach a delete.
- In `enforce`, add `GIT_DIR_KEPT` to the outcomes that count as put back.
- Update `_roll_back`'s docstring.

### 3. `templates/adws/adw_modules/runner.py`

- `Run.__init__`: `self.ran: list = []` and `self.carried = ""`.
- `Run.finish`, first thing: when `sandbox.current()` is not `sandbox.LOCAL` and has `close`, call `close(stop=True)`. Catch `permissions.PermissionBreach`: `self.console.advise` its text, and go on with `accepted=False` and the text as `reason`, so the db, the banner and exit code 1 agree.

### 4. `templates/adws/adw_modules/sandbox_remote.py`

- `commits_out(self, only=None, wrote=())`:
  1. As now up to writing `sandbox-out.bundle`.
  2. `_bundle_head(data, synced) -> str` parses the header in Python: first line `# v2 git bundle` or `# v3 git bundle`; a `@filter` capability refuses; exactly one `-<sha>` line, equal to `self.synced`; exactly one `<sha> HEAD` line. Anything else is a shape refusal. The tip is the header's sha, never the VM's `rev-parse` answer.
  3. `_host_git("bundle", "unbundle", str(bundle))`: objects in, no ref and no `FETCH_HEAD` written.
  4. `permissions.judge_commits(self.tree, self.synced, tip, cfg, ran, only=only, session=<run.session_dir relative to the tree, when inside>, wrote=wrote)`, imported inside the function. `cfg` and `ran` come from `self.run_` with `getattr`; a run with no `cfg` is a refusal. `GitFailed` is a refusal with its text.
  5. On pass: `_host_git("merge", "-q", "--ff-only", tip)`, unlink the bundle, log `sandbox_commits` with the same payload (`head` from `tip`), `self.synced = tip`, and with `only` None clear `run.ran`.
  6. On refusal: write the bytes to `sandbox-refused.bundle` with `session_files.write`, unlink `sandbox-out.bundle`, log `sandbox_commits_refused` as an `error` event (`_log` gains a `type` argument, default `"log"`) with `vm`, `synced`, `head`, `refused`, `bundle`; raise `permissions.PermissionBreach` with `refusal_text` and the bundle's path. `self.synced` is unchanged.
- `commit(self, argv, *, only=None, wrote=())`: `run`'s body with the automatic sync replaced by `self.commits_out(only, wrote)` after exit 0. Factor the shared body into `_run(..., sync: bool)`. `run` keeps its automatic `commits_out()` for any other `git commit`.
- `close(self, stop: bool = False)`: call `commits_out()` in its own `try`. Keep a `PermissionBreach` aside and go on: save the patch, delete the VM, log `sandbox_end`, set `self.sandbox = None`. Then, with a breach kept: `stop=True` raises it; otherwise (the `atexit` call, after a stop or a failure) it is told with `getattr(console, "advise", console.note)`, starting "The VM's commits were refused and did not land:", and nothing is raised. Other exceptions are logged as `sandbox_error`, as now.
- Patch note: after saving the patch, list its paths on the host with `git apply --numstat -z <patch>` under the `_REF_READ` prefix (it applies nothing; a rename gives two paths, count both). Collect the paths `permitted` refuses for every agent in `cfg.agents`, and those `_in_git_dir`. When there are any, add one console note naming up to 20. When the list can't be read, the note says to read the patch before applying. `sandbox_end` and the first note stay as they are. With no `cfg`, skip.
- Update the module docstring's commit bullet.

### 5. Callers

- `templates/adws/adw_modules/git_helper.py`, `commit_all(message, only=None, *, run=None, wrote=())`:
  - Local (`sandbox.current() is sandbox.LOCAL`): read `before = _git("rev-parse", "HEAD")` first, commit as now, then with a `run`, `permissions.check_local_commit(run, before, only=only, wrote=wrote)`. With `run=None` nothing is checked, which is what the existing unit tests call.
  - A sandbox with `commit`: call `box.commit([...the same git commit argv...], only=only, wrote=wrote)` and raise `RuntimeError` on a non-zero exit as `_git` does.
  - Update its docstring.
- Every `commit_all(` call in `templates/adws/adw_*.py` (six) passes `run=run`. In `templates/adws/adw_tickets.py`, the local `commit` helper takes `wrote=()` and the ticket's commit passes `wrote=[<ticket.path, repo-relative>]`.
- `templates/adws/adw_modules/agents.py`, `execute`: right after `git_before = permissions.guard_git_dir(run)`, add `agent` to `run.ran` if it isn't there (make the list with `getattr` when a test's stand-in run has none).
- `templates/adws/adw_modules/session.py`, `ensure`: before `return run`, in local mode only and when `worktree.dirty(<the tree the run works in>)`, set `run.carried = permissions.carried_tree(<that tree>)`.

### 6. Tests

A guard test builds its config from `templates/sssf.config.yaml`, as `tests/test_factory_holes.py` does. No test starts a real VM. Mark `host_only` any test that starts a command past agentsh's limits (128 KiB per argument, 2 MiB in all).

- `tests/test_sandbox_remote.py`: `_make_box` gives `run` the shipped config when none is passed, and `ran=[<the shipped builder>]`, so existing test bodies stay unchanged. Add tests for bar 1 (each route: after `commit`, in `close(stop=True)`, and in `close()`), bar 2 (hand-made bundles), bar 3, bar 4 through the stand-in, `only` and `wrote` through `commit`, the patch note (with a rename), and bar 8.
- `tests/test_commit_check.py` (new): `judge_commits` on real repos; `only`; session and `wrote` paths; `.git` spellings; bar 4 and bar 6 in local mode through `commit_all(run=...)`; `carried_tree` (the engineer's dirty protected file passes byte for byte, a different blob at that path is refused, a forged cache-tree in `.git/index` is not in the tree); `Run.finish` returning 1 on a refusal; the normal commits of bar 7; a test that every `commit_all(` call in `templates/adws/adw_*.py` passes `run=`.
- `tests/test_git_dir_guard.py`: bar 5 with the real guard, through `enforce` and through `agents._roll_back_failed`; one test per `_in_git_dir` spelling.
- The probes are at `/home/gpaul/github/super-simple-software-factory/adws/adw_data/sessions/ea9dc12f/bypass_probes/` (outside the worktree; read `harness/scenarios.py` for how each candidate builds its index).

### 7. Docs

- `references/config.md`, "Write permissions, `writes` and `protected_files`":
  - the host checks every commit a VM run brings back, and every commit the chain makes locally, against the agents' `writes`; what a refusal leaves (`sandbox-refused.bundle`, the branch, the tree);
  - the `only` rule and that it stands in for "only the planner"; the carried-tree rule;
  - the local read's limits from the threat model;
  - the host merge runs the engineer's own `post-merge` hook and filters on content that passed;
  - the patch note names paths no agent may write.
- `references/config.md`, known limits: rewrite the forged cache-tree bullet and the unsorted-index bullet (the commit check refuses the commit that takes the path; what stays is the local read's limits), remove the `.git/` entries bullet, and fix the sentence near "cache-tree is the exception: see the known limits below". Leave "Known limits on secrets in a VM" as it is.
- `docs/designs/remote-sandboxes.md`: one "As built" line on the host commit check.
- `docs/open.md`: mark the rows this closes, leaving the `commit_all` hook row open.
- For the documenter: any `app_docs/` write-up that says VM commits fast-forward unchecked, or that `_roll_back` deletes every staged-new path, now describes old behaviour.

## Verification

From `.claude/skills/sssf`, with the repo's own test runner and `-m 'not pushes and not host_only'`:

- `tests/test_sandbox_remote.py`, `tests/test_commit_check.py`, `tests/test_git_dir_guard.py`
- `tests/test_agent_commits.py`, `tests/test_failed_call_rollback.py`, `tests/test_factory_holes.py`, `tests/test_tickets.py`, `tests/test_adw_tickets.py`, `tests/test_adw_commit_messages.py`, `tests/test_worktree.py`

All pass. The whole suite is verify's. `tests/test_commit_check.py` is inside the suite, so the plan adds no extra check.

## Out of scope

- Secrets: nothing scans the commits or the handoff folder that come back from a VM for secrets (`config.md`, "Known limits on secrets in a VM"). This check judges paths, not content.
- Refs other than the run's branch, other worktrees and ignored files in local mode (decision 14).
- The in-VM checks, and making `sandbox: openshell` the default.
- A hook or filter the chain's own `git commit` runs before the local check.
- An object the agent wrote in the local object store, as the threat model says.
- Work an agent left in a local tree in an earlier process that died before its rollback: on a resume it is in `run.carried`, as the baseline's "already dirty" rule treats it.
- The content of a path the chain wrote itself in that commit (`wrote`), and of the run's session files.
- A `commit_all` call with no `run` (none in the shipped chains; a test pins that).
- A change inside the agents' `writes`, whoever made it.
- The size of a bundle, and unreachable objects a refused bundle leaves in the host's object store.

## For the engineer

- `adws/`: run `just sssf-update` so the hub's own chain gets the check.
- `adws/adw_sssf_config/sssf.config.yaml`: add `sandbox_remote.py`, `git_helper.py`, `runner.py` and `tests/test_commit_check.py` to `protected_files` and to `guard_builder`'s `writes`, if they should be guard files.
- `CLAUDE.md`: add `sandbox_remote.py` to the guard list.
