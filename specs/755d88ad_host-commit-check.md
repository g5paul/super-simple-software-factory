# Check a run's commits on the host before they land

## Goal

A VM run's commits are judged by the host's own git before the host branch moves, and the chain's own commit is judged the same way in local mode. A refused commit lands nothing. `_roll_back` never deletes inside the git directory.

All module paths below are under `.claude/skills/sssf/`, which `guard_builder`'s `writes` names.

## Threat model

- The agent runs as the engineer's user. Under `--sandbox openshell|vercel|e2b` it controls the whole VM: the clone's `.git`, its index, cache-tree, replace refs, grafts, `shallow`, hooks, filters and config, and every answer the VM gives the host, including `rev-parse HEAD` and the bundle's bytes.
- In a VM run the agent cannot reach the host's `.git`. The host check reads only objects the host's `index-pack` hashed, so an object id means its real content. This check can stop any protected path from landing through `commits_out`.
- It cannot stop: a change inside the agents' `writes` (decision 1 lets it pass for any agent that ran since the last sync); a huge bundle; what the engineer's own `post-merge` hook and checkout filters do with content that passed (decision 3).
- In local mode the agent controls the `.git` the check reads. The check turns off replace refs, grafts, hooks, external diff, textconv and submodule recursion, and reads commit objects raw, so `shallow` can't hide a parent. It can't stop: an `objects/info/alternates` or a planted pack that serves other content under an id (git does not re-hash packed objects on read); a config file the git-directory guard could not put back (the run already stops there); the chain's `git commit` running a planted hook before the check (`docs/open.md` P2 row on `commit_all`); an agent that kills the chain before the check.

## Baseline

`main` at `77b71b5`. Every finding is judged against that commit. There, `Remote.commits_out` fetches the bundle and runs `git merge --ff-only FETCH_HEAD` with no check, `commit_all` is followed by no check, and `_roll_back` deletes a staged-new path named `.git/config`.

## Bar

Against `77b71b5`: no agent file kept on the host branch, no agent code run by the check, no engineer file deleted or overwritten, where the baseline does otherwise.

1. **Each route lands nothing (VM).** Index-only staging, a forged cache-tree, a replace ref or graft hiding a change, a hook or filter that changes the chain's commit, and an agent commit `undo_commits` failed to undo: each, in `Remote.run` after a commit and in `close()` on success and failure, leaves the host branch at `synced`, raises or reports a `PermissionBreach` naming each commit and path, and keeps `sandbox-refused.bundle`.
2. **Bundle shape.** A merge commit, a second root, a prerequisite other than `synced`, more than one ref, or a head that does not descend from `synced` is refused before any `diff-tree` runs.
3. **Git internals.** A replace ref, a graft and a forged cache-tree in the VM change nothing on the host.
4. **The review's holes.** `c4_inverse_forged_tree_plan_commit`, `c2_unsorted_new_protected_then_build`, `c2_unsorted_P_replaced_then_build`: in local mode the branch is back at the commit before, through a VM stand-in at `synced`; the run stops naming the path.
5. **The git directory.** `c2_gitdir_entries_real_guard` with the real guard: `.git/config`, `.git/hooks/pre-push`, `.git/sssf-private-names` hold the engineer's bytes after the call, on the success and the failure path, and the protected write is rolled back.
6. **Local mode.** The index and cache-tree cases of `specs/tasks/guard-index-only-staged-2.md`, with `snapshot`'s in-call check stubbed to see nothing, are refused by the commit check.
7. **No false stop.** Every existing test passes. A plan commit of `specs/`, a builder's commit inside its `writes`, the documenter's, the run's session files, and `tickets.mark_done`'s ticket file pass.
8. **Nothing else changes.** When every commit passes: same host branch, same `sandbox_commits` and `sandbox_end` events, same patch bytes, same console notes.

## Decisions this plan makes (a reviewer judges them)

- **The judge lives in `permissions.py`**, beside `permitted` and `_COMMIT_PATHS`, and runs git with `subprocess.run` in a given repo, never through `sandbox.current()` (which is the VM).
- **`only` commits.** The host can't name "the planner". An `only` commit's path passes only when it is inside an `only` entry and an agent that ran since the last full commit may write it. The agent list is cleared after a full commit passes, never after an `only` commit, since the planner's other work stays in the tree.
- **Carried work in local mode.** A local tree can hold work from before this process (a resume, or the engineer's own edits without `--worktree`). When the tree was dirty at `session.ensure`, the first full commit is judged against every agent of the roster. A path no roster agent may write is refused even if the engineer wrote it: the tree is kept, and `config.md` says to commit such an edit first or use `--worktree`. This is the one deliberate difference from bar 7.
- **`close()` cannot stop a run that has ended.** It reports the refusal on the console and in the trace, and never raises.
- **A path with a `.git` component never passes**, for any agent.

## Steps

### 1. `templates/adws/adw_modules/permissions.py`: the judge

Add, near `_COMMIT_PATHS`:

- `_in_git_dir(path) -> bool`: true when any `/` component is one git treats as `.git`. Lowercase it; drop the HFS-ignored code points (U+200C–200F, U+202A–202E, U+206A–206F, U+FEFF); for NTFS cut at the first `:` and strip trailing dots and spaces; match `.git` or `git~1`.
- Module state, one run per process as `sandbox._current` is: `_LANDING = {"run": None, "agents": [], "carried": False, "only": None, "chain": set()}` with:
  - `run_started(run, carried: bool)`: sets `run`, `carried`, empties the rest.
  - `agent_ran(agent)`: appends the `AgentConfig` once.
  - `chain_wrote(path)`: adds one exact repo-relative path the chain's own code wrote.
  - `commit_scope(only)`: a context manager that sets `only` for the commit in flight and clears it in `finally`.
  - `commits_passed()`: when `only` is unset, empties `agents` and `chain` and sets `carried` False.
- `judge_commits(repo, base, tip, cfg, agents, *, only=None, session="", chain=()) -> list[tuple[str, list[str]]]`: returns `(commit sha, refused paths)` pairs, `[]` when all pass. Every git call is `subprocess.run([*_REF_READ, ...], cwd=repo, capture_output=True)`, bytes decoded `utf-8`/`surrogateescape`; a non-zero exit or unparsable output raises `GitFailed` (callers treat it as a refusal).
  1. Walk from `tip`: `cat-file commit <sha>`, parse the header lines up to the first blank line. Exactly one `parent` line, else refuse the commit with the reason (`a merge`, `a root`). Stop when the parent is `base`. More than 256 commits: refuse. Reaching a root before `base` is the "not a descendant" case. Nothing is diffed until the whole walk passes.
  2. Per commit, oldest first: `diff-tree -r --raw -z --no-renames --no-ext-diff --no-textconv <parent> <commit>`. Parse `:<modes> <shas> <status>\0<path>\0` records. Modes are not judged: a gitlink, symlink or mode change is its path.
  3. A path passes when `not _in_git_dir(path)` and (`only` is None or the path equals an `only` entry or is under one that ends with `/`, compared literally) and one of: `permitted(path, a, cfg)` for some `a` in `agents`; `session` is non-empty and the path is under it; the path is in `chain`.
- `check_commits(repo, base, tip) -> list[...]`: reads `_LANDING`; `agents` is the roster (`run.cfg.agents`) when `carried`, else the recorded list; `session` is `run.session_dir` relative to `repo` when inside it, else `""`. With no `run`, or a `run` with no `cfg`, it raises `GitFailed("no run to judge against")`.
- `refusal_text(refused, base) -> str`: one line per commit and path, each through `_said_line`, at most `REF_COMMITS_SHOWN` commits and `REF_PATHS_SHOWN` paths each, with the count of the rest.
- `check_local_commit(before: str) -> None`: does nothing when `_LANDING["run"]` is None (a bare `commit_all` in a unit test). Else `tip = rev-parse HEAD`; `refused = check_commits(repo_root, before, tip)`. On pass, `commits_passed()`. On refusal or `GitFailed`: with `off = _filters_off(...)`, `_git(["update-ref", "HEAD", before, tip])`, then `_try_git(["reset", "-q", "--no-recurse-submodules", before])` to rebuild the index from it (the work tree is kept); trace an `error` event `commit_refused` with `{"before", "refused", "commits"}`; raise `PermissionBreach` with `refusal_text`, saying the branch is back at `before`, the tree is kept, and whether the index reset failed.

Extend the module docstring with a paragraph on the commit check.

### 2. `permissions.py`: `_roll_back` never deletes in the git directory

- In `_roll_back`, after the `path in before` block and before `kind == "?"`: when `_in_git_dir(path)`, clear the two index flags, unstage with `rm -q --cached --force -- path`, and if that fails `update-index --force-remove -- path`. Return the new constant `GIT_DIR_KEPT = "unstaged; nothing on disk touched, since the path is inside the git directory"`, or `"could not unstage"`. No `delete()`, no `checkout`.
- Make `delete()` itself return `"left on disk (inside the git directory)"` when `_in_git_dir(path)`, so no later branch can reach it.
- In `enforce`, add `GIT_DIR_KEPT` to the outcomes that count as put back.
- Update `_roll_back`'s docstring.

### 3. `templates/adws/adw_modules/sandbox_remote.py`

- `commits_out`:
  1. As now up to writing `sandbox-out.bundle`.
  2. Parse the bundle header in Python (`_bundle_head(data, synced) -> str`): first line `# v2 git bundle` or `# v3 git bundle`; a `@filter` capability refuses; exactly one `-<sha>` line, equal to `self.synced`; exactly one `<sha> HEAD` line. Anything else is a shape refusal. The tip is the header's sha, never the VM's `rev-parse` answer.
  3. `_host_git("bundle", "unbundle", str(bundle))`: objects in, no ref and no `FETCH_HEAD` written.
  4. `refused = permissions.check_commits(self.tree, self.synced, tip)` (import inside the function). `GitFailed` counts as a refusal with its text.
  5. On pass: `_host_git("merge", "-q", "--ff-only", tip)`, unlink the bundle, log `sandbox_commits` with the same payload (`head` from `tip`), `self.synced = tip`, `permissions.commits_passed()`.
  6. On refusal (shape or path): write the bytes to `sandbox-refused.bundle` with `session_files.write`, unlink `sandbox-out.bundle`, log `sandbox_commits_refused` as an `error` event (`_log` gains a `type` argument, default `"log"`) with `vm`, `synced`, `head`, `refused`, `bundle`; raise `permissions.PermissionBreach`. `self.synced` is unchanged.
- `close()`: call `commits_out()` in its own `try`. A `PermissionBreach` is told to the engineer with `getattr(console, "advise", console.note)`, prefixed "the VM's commits were refused and did not land:", and close goes on to save the patch. Other exceptions are logged as `sandbox_error`, as now.
- Patch note: after saving the patch, list its paths on the host with `git apply --numstat -z <patch>` under the `_REF_READ` prefix (it applies nothing; a rename gives two paths, count both). Collect the paths that `permitted` refuses for every agent in `cfg.agents`, or that `_in_git_dir`. When there are any, add one console note naming up to 20. When the list can't be read, the note says to read the patch before applying. `sandbox_end` and the first note stay as they are. With no `cfg`, skip.
- Update the module docstring's commit bullet.

### 4. Callers

- `templates/adws/adw_modules/git_helper.py`, `commit_all`: read `before = _git("rev-parse", "HEAD")` first; run the `git commit` inside `with permissions.commit_scope(only):`; inside that block, when `sandbox.current() is sandbox.LOCAL`, call `permissions.check_local_commit(before)`. Update its docstring.
- `templates/adws/adw_modules/agents.py`: right after `git_before = permissions.guard_git_dir(run)`, call `permissions.agent_ran(agent)`.
- `templates/adws/adw_modules/session.py`, `ensure`: before `return run`, call `permissions.run_started(run, carried=...)`, where `carried` is `worktree.dirty(<repo root>)` in local mode and False with a VM.
- `templates/adws/adw_modules/tickets.py`, `mark_done`: call `permissions.chain_wrote(<ticket.path, repo-relative>)`.

### 5. Tests

A guard test builds its config from `templates/sssf.config.yaml`, as `tests/test_factory_holes.py` does. No test starts a real VM. Mark `host_only` any test that starts a command past agentsh's limits (128 KiB per argument, 2 MiB in all).

- `tests/test_sandbox_remote.py`: `_make_box` gives `run` the shipped config and calls `permissions.run_started(run, carried=False)`; the `box` fixture calls `agent_ran` with the shipped builder, so existing test bodies stay unchanged. Add tests for bar 1 (each route, in `run` and in `close()` on both paths), bar 2 (hand-made bundles), bar 3, bar 4 through the stand-in, the patch note (with a rename), and bar 8.
- `tests/test_commit_check.py` (new): `judge_commits` on real repos; `only`; session and chain paths; `.git` spellings; bar 4 and bar 6 in local mode through `commit_all`; the carried case; the normal commits of bar 7.
- `tests/test_git_dir_guard.py`: bar 5 with the real guard, through `enforce` and through `agents._roll_back_failed`; one test per `_in_git_dir` spelling.
- The probes are at `/home/gpaul/github/super-simple-software-factory/adws/adw_data/sessions/ea9dc12f/bypass_probes/` (outside the worktree; read `harness/scenarios.py` for how each candidate builds its index).

### 6. Docs

- `references/config.md`, "Write permissions, `writes` and `protected_files`": the host checks every commit a VM run brings back, and every commit the chain makes locally, against the agents' `writes`; what a refusal leaves (`sandbox-refused.bundle`, the branch, the tree); the local read's limits from the threat model; the carried-work rule; that the host merge runs the engineer's own `post-merge` hook and filters on content that passed; that the patch note names paths no agent may write.
- `docs/designs/remote-sandboxes.md`: one "As built" line on the host commit check.
- `docs/open.md`: mark the rows this closes (the VM routes; the two holes of 2026-11-17), leaving the `commit_all` hook row open.
- For the documenter: any `app_docs/` write-up that says VM commits fast-forward unchecked, or that `_roll_back` deletes every staged-new path, now describes old behaviour.

## Verification

From `.claude/skills/sssf`, with the repo's own test runner and `-m 'not pushes and not host_only'`:

- `tests/test_sandbox_remote.py`, `tests/test_commit_check.py`, `tests/test_git_dir_guard.py`
- `tests/test_agent_commits.py`, `tests/test_failed_call_rollback.py`, `tests/test_factory_holes.py`, `tests/test_tickets.py`, `tests/test_adw_commit_messages.py`, `tests/test_worktree.py`

All pass. The whole suite is verify's. `tests/test_commit_check.py` is inside the suite, so the plan adds no extra check.

## Out of scope

- Refs other than the run's branch, other worktrees and ignored files in local mode (decision 14).
- The in-VM checks, and making `sandbox: openshell` the default.
- A hook or filter the chain's own `git commit` runs before the local check.
- Local alternates and planted packs, as the threat model says.
- A local commit made by a process `session.ensure` never set up.
- A change inside the agents' `writes`, whoever made it.
- The size of a bundle, and unreachable objects a refused bundle leaves in the host's object store.

## For the engineer

- `adws/`: run `just sssf-update` so the hub's own chain gets the check.
- `adws/adw_sssf_config/sssf.config.yaml`: add `sandbox_remote.py`, `git_helper.py` and `tests/test_commit_check.py` to `protected_files` and to `guard_builder`'s `writes`, if they should be guard files.
- `CLAUDE.md`: add `sandbox_remote.py` to the guard list.
