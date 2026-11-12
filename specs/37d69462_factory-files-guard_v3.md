# Keep agents off the factory files that judge their work

The open item is "Agents can edit factory files that `protected_files` doesn't cover", in `docs/open.md`. ADR 0001, Consequences, says why it matters.

All code changes go in `.claude/skills/sssf/`. **Never edit anything under `adws/`**, because that is the hub's live copy. The hub's roster and `adws/` get updated by hand after the merge (see "For the engineer").

This is revision 3.

Revision 2, after spec review 1:
- `permitted` keeps the baseline roster check exactly as it is. A separate fixed-list check is added, and it must pass too.
- The extension known limit at `references/config.md:602` is named and rewritten. The part still open is listed in Out of scope.
- The db grant comes from `cfg.observability.db`.
- The git helpers are now one `git show` call.

Revision 3, after spec review 2:
- The trace db grant applies only when `cfg.observability.db` is inside `<data_dir>/`, so the new grant really is a subset of the old one. A regression test covers a db path outside `data_dir`.
- `changed_in` takes `-m --first-parent`, so a merge commit lists every file it changed against its first parent.

## Baseline

`main` at `d620acb` ("Add the brief for the factory-files guard run"). Every finding is judged against it.

At the baseline, `permissions.permitted` (`.claude/skills/sssf/templates/adws/adw_modules/permissions.py:888-900`) is:

```python
if any(_matches(path, p) for p in always_writable(cfg)):      # all of <data_dir>/
    return True
writes = agent.writes or []
guards = [p for p in cfg.defaults.protected_files if _matches(path, p)]
if guards:
    return any(_matches(path, w) and _names(w, g) for w in writes for g in guards)
if any(_matches(path, w) for w in writes):
    return True
return agent.writes is None
```

What that allows today:
- Every agent, `writes: []` ones included, may change `<data_dir>/prompt_engineering/` and `<data_dir>/harness_engineering/`.
- The builder may change `.claude/skills/sssf/`, `justfile`, `sssf.just`, `.githooks/` and `.claude/settings*.json`.
- The documenter's `**/*.md` reaches the skill's Markdown.

`prompts.render` reads the prompts at each call. The ticket chain's review diff (`adw_tickets.py:195-197`) leaves out `git_helper.SSSF_PATHS`. So a builder edit to a reviewer prompt changes what the reviewer is told, and the reviewer never sees the edit. `references/config.md:602` records the extension case as a known limit: "A host run has the same exposure, since an extension isn't a protected file. Nothing checks it."

## Threat model

An agent runs as the engineer's own user, with `bash`, `write` and `edit`. It can write any file the user can, run any program, and commit.

This guard acts **after each agent call**:
- `snapshot` records the work tree's change-set before the call, and again after it.
- `undo_commits` undoes any commits the agent made.
- `enforce` rolls back each path the agent wasn't allowed to change.

It decides what's allowed from four things: the repo-relative path, the agent's `writes`, the roster's `protected_files`, and, after this change, SSSF's fixed list. Under `local_guard: agentsh` and in a VM, the same `enforce` runs after every call, and git runs through `sandbox.current()` in the tree the agent worked in.

After this change, the guard stops one thing it doesn't stop today: a change to a factory file by an agent that doesn't name it in `writes`. That change is rolled back, and the run stops before any later agent call reads the file. So the next `prompts.render`, or the next pi call's extension load, gets the committed file again. That closes `config.md:602`'s exposure across calls, on the host and in a VM.

At its level, it can't stop:
- A protected file used **within** the same call that edited it. For example, an agent edits `justfile` and runs `just` in that call. Or it edits a pi extension, and a pi process started later in the same call loads it, such as a subagent from `subagent_create`.
- Writes outside the work tree, and writes to gitignored paths. Neither ever shows up in a snapshot. A worktree run pins its prompt and extension paths to absolute paths in the launch checkout (`worktree.py:88-93`), so the files its agents load are outside the work tree that run's check covers.
- An agent that kills SSSF before the after-call check runs.
- A VM agent overwriting `sssf_refuse.py` within one call. That has its own open item.

## Bar

Must:
1. **Every refusal at the baseline stays a refusal.** The new `permitted` is (a narrower runtime grant) OR (the baseline's roster-and-`writes` logic, unchanged), AND (the fixed-list check).
   - Every path in the new grant is under `<data_dir>/`, so the new grant is a subset of the old one. That includes the trace db paths, which are granted only when the db is inside `<data_dir>/`.
   - The fixed-list check can only refuse.
   - So any path the baseline refused is still refused.
   - A roster entry inside a fixed folder stays refused, because the roster check runs unchanged. Example: the roster protects `.claude/skills/sssf/SKILL.md`, and the agent has `writes: [".claude/skills/sssf/"]`.
   - A db path outside `data_dir`, such as `sssf.db` at the root, stays refused to `writes: []` agents.
2. Every path in the fixed list is refused after every call, to every agent of the starter rosters, unless that agent names it in its `writes`. The rosters are `sssf.config.yaml` (pi), `sssf.config.claude_code.yaml` and `sssf.config.chatgpt.yaml`. Naming means the same entry, or a path inside a protected folder. This covers the `writes: []` agents and the documenter with `**/*.md`.
3. Every agent may still write `<data_dir>/sessions/` and `<data_dir>/agent-cache/`, `writes: []` included. The planner still writes `specs/`. The documenter still writes `app_docs/`, `docs/` (except `docs/adr/`, which the roster protects) and the project's Markdown outside the fixed list.
4. A refused change is rolled back as today, and the breach message names it.
5. A builder edit to the project's own `justfile` is now refused. That's intended and documented. Any other new refusal of a starter chain's normal work is a regression.
6. The ticket review's new section shows only SSSF-file changes from commits that carry the feature's `SSSF-Feature` trailer. It never blocks the run by itself.
7. No new module-level mutable state. The fixed list and the runtime list are tuple constants, like `SSSF_PATHS`.

Must never, where the baseline does otherwise: keep an agent's file, run agent code, or delete or overwrite an engineer file.

agentsh's policy doesn't change.

## Findings: what agents write under `data_dir`

Searched: every chain under `.claude/skills/sssf/templates/adws/`, plus `adw_modules/`, the starter prompts in `templates/prompt_engineering/` and the extensions in `templates/harness_engineering/`.

- `<data_dir>/sessions/<adw_id>/...` holds handoff files, envelopes, saved prompts, raw output, the Claude Code config dir (`agent_cc.py:344`) and `events.jsonl`. Agents and SSSF both write there. **Runtime, granted.**
- `<data_dir>/agent-cache/` holds the agents' tool caches under agentsh (`agentsh_guard.py:348,452`). **Runtime, granted.**
- `<data_dir>/worktrees/<adw_id>/` is created by SSSF (`worktree.py:66`), never by an agent. In a worktree run, the agent's `repo_root` is the worktree, so its writes show up as worktree-relative paths.
- The trace db is `cfg.observability.db` (default `adws/adw_data/sssf.db`), with its `-wal`, `-shm` and `-journal` files. The tracer in the SSSF process writes it, not an agent.
- `harness_engineering/subagents.ts` writes under `~/.pi/agent/sessions/subagents`, outside the repo.
- No starter prompt tells an agent to write under `data_dir` outside `sessions/`.

**No agent has to write anything under `data_dir` that isn't runtime.**

One decision to flag: `worktrees/` and the trace db are SSSF's own runtime, and SSSF writes them while an agent runs. `install.py` gitignores both (`GITIGNORE_ENTRIES`), so normally neither shows up in a snapshot. But if a repo's `.gitignore` lacks those lines, a narrowed grant would make `enforce` treat them as the agent's new untracked files and **delete** them. That would lose the trace db, or another run's worktree, and the bar forbids that new deletion. So `always_writable` also grants `<data_dir>/worktrees/`, and the trace db's own paths when the db sits inside `<data_dir>/`. Neither holds factory files. A db path outside `data_dir` gets no grant, the same as at the baseline. Neither does a db path that falls inside a fixed entry.

## The fixed list

```
<data_dir>/prompt_engineering/
<data_dir>/harness_engineering/
.claude/skills/sssf/
justfile
sssf.just
.githooks/
.claude/settings.json
.claude/settings.local.json
adws/adw_modules/
adws/adw_sssf_config/
adws/adw_*.py
```

## Changes

### 1. `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`

- Add these tuple constants near `always_writable`:

  ```python
  # SSSF's own files: the prompts, the extensions, the skill, the recipes, the
  # hooks, Claude Code's settings (their hooks run on the host) and the ADW
  # code. Protected whatever the roster says. Only an agent's own `writes`,
  # naming one, unlocks it. FACTORY_DATA is relative to data_dir.
  FACTORY_DATA = ("prompt_engineering/", "harness_engineering/")
  FACTORY_FILES = (".claude/skills/sssf/", "justfile", "sssf.just", ".githooks/",
                   ".claude/settings.json", ".claude/settings.local.json",
                   "adws/adw_modules/", "adws/adw_sssf_config/", "adws/adw_*.py")
  # The runtime under data_dir: the agents' own (sessions, tool caches) and
  # SSSF's worktrees. The trace db is added from cfg.observability.db when it
  # is inside data_dir.
  RUNTIME = ("sessions/", "agent-cache/", "worktrees/")
  _DB_SIBLINGS = ("", "-wal", "-shm", "-journal")
  ```
- Add `def factory_files(cfg) -> list[str]`. It sets `data = cfg.defaults.data_dir.rstrip("/")` and returns `[f"{data}/{p}" for p in FACTORY_DATA] + list(FACTORY_FILES)`.
- Add `def protected(cfg) -> list[str]`. It returns the fixed list, then the roster's `protected_files`, each entry once (`list(dict.fromkeys(...))`). It is **only for display**: prompts, messages, the trace and the ticket prompts. Never use it in the decision.
- `always_writable(cfg)`:

  ```python
  data = cfg.defaults.data_dir.rstrip("/")
  grants = [f"{data}/{p}" for p in RUNTIME]
  db = cfg.observability.db
  # Only inside data_dir, so the grant stays within the baseline's (all of
  # data_dir), and never on a factory path. A worktree run pins db absolute,
  # outside the worktree; an absolute path never matches a snapshot path.
  if _matches(db, data + "/") and not any(_matches(db, f) for f in factory_files(cfg)):
      grants += [db + s for s in _DB_SIBLINGS]
  return grants
  ```
  Rewrite the docstring to say:
  - The session runtime and the tool caches are always writable.
  - SSSF's worktrees and trace db are granted, inside `data_dir` only, so a missing gitignore line can't get them deleted by a rollback.
  - The rest of `data_dir`, the prompts and the extensions, is factory and protected.
- `permitted`: keep the baseline body **exactly**. Add one new check after the runtime grant and before `writes = ...`:

  ```python
  if any(_matches(path, p) for p in always_writable(cfg)):
      return True
  writes = agent.writes or []
  # SSSF's own files, whatever the roster says. A separate check: it can only
  # refuse, so the roster check below still decides everything it did before.
  fixed = [p for p in factory_files(cfg) if _matches(path, p)]
  if fixed and not any(_matches(path, w) and _names(w, g) for w in writes for g in fixed):
      return False
  guards = [p for p in cfg.defaults.protected_files if _matches(path, p)]
  ...  # unchanged from the baseline
  ```
  Take a path inside a fixed entry that the agent names, with no roster entry matching it. It falls through to `any(_matches(path, w) for w in writes)`, which is true because the naming entry matches the path.
- `enforce`: the scope message `barred from {...}` uses `protected(run.cfg)`.
- Module docstring (lines 34-36): say SSSF's fixed `FACTORY_FILES` are checked on top of `protected_files`, and a roster can't drop them.
- `snapshot` docstring (lines 76-77): say the session runtime is normally gitignored. Remove the claim that `data_dir` as a whole "needs no special case".

### 2. `.claude/skills/sssf/templates/adws/adw_modules/agents.py`

- `prompt_variables` (line ~128): render `permissions.protected(run.cfg)` instead of `run.cfg.defaults.protected_files`. That way planners and builders are told the fixed list too. `agents.py` already uses `permissions` at line ~405. Check it's imported at module level, and import it if not.
- In the `permission_breach` trace event payload (line ~417), set `"protected_files": permissions.protected(run.cfg)`.

### 3. `.claude/skills/sssf/templates/adws/adw_modules/git_helper.py`

- Add `def changed_in(sha: str, paths: tuple[str, ...]) -> tuple[str, list[str]]`.
  - It makes one call through `_git`: `git show -m --first-parent --format=%s --name-only <sha> -- <paths...>`.
  - `-m --first-parent` makes a merge commit list everything it changed against its first parent. Without them, git shows a combined diff, which leaves files out.
  - The first line of the output is the subject, and the remaining non-empty lines are the paths. Return `(subject, paths)`.
- Update the `SSSF_PATHS` comment: the ticket review shows the feature's own changes to these paths in a separate section.

### 4. `.claude/skills/sssf/templates/adws/adw_tickets.py`

- Add a module-level function `feature_sssf_changes(feature_slug: str) -> str`. Its docstring: it returns the SSSF-file changes in the feature's own commits (the ones with the `SSSF-Feature` trailer), for the reviewers to report to the engineer, or `""` when there are none. An "Update SSSF" commit between runs carries no trailer, so it never shows.
  - It walks `git_helper.feature_commits(feature_slug)`.
  - For each sha, it calls `changed_in(sha, git_helper.SSSF_PATHS)`. When the paths aren't empty, it adds the line `- <git_helper.short_sha(sha)> <subject>: <path>, <path>`.
  - A non-empty result starts with this header:
    `SSSF files this feature's own commits changed. These are for the engineer to judge: report each as a non-blocking finding naming its path, never as blocking. Inspect with git show <sha> -- adws/ .claude/skills/sssf/`
- In `build_feature`, after `commits = ...`, set `sssf = feature_sssf_changes(feature.slug)`. When it isn't empty, append `f"\n{sssf}"` after `Commits:` in both the standards reviewer's prompt and the spec reviewer's prompt.
- Set `protected = ", ".join(permissions.protected(run.cfg))`. Both the reviewers' `hands_off` and the fixer prompt use it.
- In the split between blocking and for-the-engineer findings (lines ~219-221), send any finding whose path starts with one of `git_helper.SSSF_PATHS` to `for_engineer`, even when `permitted` would allow it:
  `fixable = not path or (permissions.permitted(path, builder, run.cfg) and not path.startswith(git_helper.SSSF_PATHS))`
  So for every finding that names a path, the guarantee is in code. For a finding with an empty `where`, it rests on the header's instruction. Such a finding costs at most the one fix pass the chain already allows. Say this in the code comment.
- Update the comment above `outside = ...`, and the review paragraph of the module docstring, to mention the new section.

### 5. The other chains' review inputs: checked, no change

- `adw_build_review.py` and `adw_simple_sdlc.py` give the `reviewer` the builder's envelope and the code on disk. The reviewer prompt says to use `git diff`. Neither chain leaves SSSF paths out.
- The `adw_plan_build*.py` chains reuse those two.
- The documenter's `changes.capture` leaves out `ChangeCapture.exclude`, which is the SSSF paths. But the documenter isn't a reviewer, so leave it.

Record this in the build report.

### 6. `.claude/skills/sssf/templates/adws/adw_modules/data_types.py`

Comments only:
- `AgentConfig.writes` comment (line ~419): "except the roster-wide `protected_files` and SSSF's fixed factory files (`permissions.FACTORY_FILES`)".
- `ConfigDefaults.protected_files` comment: the fixed list is always checked on top, and can't be dropped here.

Keep the default list's values as they are.

### 7. Starter rosters

In `.claude/skills/sssf/templates/sssf.config.yaml`, `sssf.config.claude_code.yaml` and `sssf.config.chatgpt.yaml`, change comments only. Keep their `protected_files` entries identical, because `test_rosters.py` checks that.

- Above `protected_files`, add this line: "SSSF always protects its own factory files too (prompts, extensions, the skill, justfile, sssf.just, .githooks/, .claude/settings*.json, adws/ code). A roster can't drop them; only an agent's own `writes` naming one unlocks it. A repo whose justfile holds product recipes names `justfile` in the builder's `writes`."
- `sssf.config.yaml` lines 26-29: change "It never restricts the session runtime under data_dir" to "It never restricts the session runtime under data_dir/sessions/ and data_dir/agent-cache/".
- The builder's comment: change "It still cannot touch defaults.protected_files" to "...defaults.protected_files or SSSF's factory files".

### 8. Docs

`.claude/skills/sssf/references/config.md`:
- In "Write permissions, `writes` and `protected_files`", add a paragraph that covers:
  - The fixed list, named exactly as above.
  - SSSF checks it on top of the roster's `protected_files`, and a roster can't drop an entry.
  - An agent's own `writes` unlocks an entry by naming it: the same entry, or a path inside a protected folder. A broad glob doesn't unlock it.
  - Unlocking a fixed folder doesn't unlock a roster entry inside it.
  - Why: prompts are read at each call and extensions are loaded by agents, so an agent that could edit them would change what judges it. Claude Code settings hooks run on the host in the engineer's next session.
- Say that a repo whose own `justfile` holds product recipes unlocks it by naming `justfile` in the builder's `writes`. Otherwise a builder edit to it is refused.
- Rewrite the paragraph at line ~338 ("The session runtime under `data_dir` is always writable…"):
  - Only `sessions/` and `agent-cache/` are always writable.
  - SSSF's own `worktrees/` and the trace db are writable too, when the db is inside `data_dir`, so a rollback never deletes them.
  - `prompt_engineering/` and `harness_engineering/` are protected.
- In the table row for `writes` (line ~76), make the same correction to "The session runtime under `data_dir`", and change the protection wording to "barred from `protected_files` and SSSF's factory files".
- **Line 602**, the known limit on extensions. Rewrite it to say:
  - An agent's edit to a committed extension under `<data_dir>/harness_engineering/` is now refused after the call, on the host and in a VM, and rolled back before the next agent call loads it.
  - Still open: an extension edited and loaded within one call, for example by a subagent the agent starts.
  - A worktree run loads extensions from the launch checkout, which is outside the tree its check covers.

`.claude/skills/sssf/SKILL.md` line 80: change "The session runtime under `data_dir` is always writable" to "The session runtime (`data_dir/sessions/`, `agent-cache/`) is always writable, and SSSF's own factory files are always protected".

There's no CHANGELOG in the skill or the repo. There's no `app_docs/` in this repo, so there's no older write-up for the documenter to note.

### 9. Tests (`.claude/skills/sssf/tests/`)

Build configs with `agents.load_config(str(SKILL / "templates" / roster))`, as `test_rosters.py` does, for all three starter rosters. Never start an agent.

New `.claude/skills/sssf/tests/test_factory_files.py`:
- `FIXED_SAMPLES`: `adws/adw_data/prompt_engineering/reviewer/system.md`, `adws/adw_data/harness_engineering/subagents.ts`, `.claude/skills/sssf/cookbooks/run_adw.md`, `justfile`, `sssf.just`, `.githooks/pre-push`, `.claude/settings.json`, `.claude/settings.local.json`, `adws/adw_modules/rules.py`, `adws/adw_sssf_config/sssf.config.yaml` and `adws/adw_tickets.py`.
  - Assert that every `permissions.factory_files(cfg)` entry is matched by at least one sample (`permissions._matches`), so a new entry can't go untested.
- Parametrize over roster × agent × sample, and assert `not permissions.permitted(sample, agent, cfg)`.
- An agent that names an entry unlocks it (use `model_copy(update={"writes": [...]})`):
  - `["justfile"]` permits `justfile`, but not `sssf.just`.
  - `[".claude/skills/sssf/cookbooks/"]` permits a cookbook file, but not `.claude/skills/sssf/SKILL.md`.
  - `["adws/adw_data/prompt_engineering/"]` permits the reviewer prompt.
- The overlap case from review 1. Take a cfg with `.claude/skills/sssf/SKILL.md` added to `defaults.protected_files`, and an agent with `writes: [".claude/skills/sssf/"]`. `.claude/skills/sssf/SKILL.md` is refused, and `.claude/skills/sssf/cookbooks/run_adw.md` is allowed.
- Runtime. For every starter agent, the `writes: []` ones included (reviewer, scout, standards_reviewer, spec_reviewer), these are permitted:
  - `adws/adw_data/sessions/abc/context_handoff/review.md`
  - `adws/adw_data/agent-cache/uv/x`
  - `adws/adw_data/sssf.db-wal`
- **A db outside `data_dir`, from review 2.** Take a starter cfg with `observability.db` set to `"sssf.db"` (`cfg.model_copy(update={"observability": cfg.observability.model_copy(update={"db": "sssf.db"})})`).
  - The reviewer (`writes: []`) is refused `sssf.db` and `sssf.db-wal`, as at the baseline.
  - `permissions.always_writable(cfg)` holds no entry outside `adws/adw_data/`.
  - With the default db, every `always_writable` entry starts with `adws/adw_data/`.
  - With an absolute db such as `/tmp/x/sssf.db`, no db entry is granted.
- Normal work is still allowed:
  - The planner may write `specs/abc_x.md`.
  - The documenter may write `README.md`, `docs/guide.md`, `app_docs/abc_x.md` and `src/pkg/NOTES.md`.
  - The builder may write `src/app.py` and `tests/test_app.py`.
- A roster that leaves out the factory paths. Use `cfg.model_copy` with `defaults.protected_files=["specs/"]`, and again with `[]`.
  - The builder is still refused every sample.
  - With the starter roster, `permissions.protected(cfg)` starts with `factory_files(cfg)` and holds each entry once.
- **The confirmed repro, as a regression.**
  - Set up a temp git repo with `tmp_path` and `monkeypatch.chdir`. Its `.gitignore` has `adws/adw_data/sessions/`, and it has `adws/adw_data/prompt_engineering/reviewer/system.md` committed.
  - Use the starter pi roster, with `run = SimpleNamespace(repo_root=tmp_path, cfg=cfg)`. Follow how `test_agent_commits.py` builds its run for `enforce`.
  - Take `before = permissions.snapshot(run)`, then overwrite the file.
  - `pytest.raises(permissions.PermissionBreach)` around `permissions.enforce(run, None, builder, before)`.
  - Assert that the message names the path, and that the file has its committed content again.
  - Do the same for a new untracked `.claude/skills/sssf/x.md`, and assert it's deleted.
- The reviewer writes `adws/adw_data/sessions/abc/x.md` in a repo whose `.gitignore` doesn't ignore it. `enforce` still returns the path and doesn't raise.

`.claude/skills/sssf/tests/test_protected_paths.py::test_every_prompt_can_name_the_protected_paths`:
- The expected value becomes `"\n".join(f"- `{p}`" for p in permissions.protected(config()))`.
- Also assert on the literal text: it contains "- `justfile`", ends with "- `specs/`", and lists `adws/adw_modules/` only once.

`.claude/skills/sssf/tests/test_adw_tickets.py`:
- Unit test, in a fresh temp repo (chdir):
  - Commit `init`.
  - Commit a change to `adws/adw_data/prompt_engineering/reviewer/system.md` and `src/a.py`, with the message `"t\n\nSSSF-Feature: summaries"`.
  - Commit a change to `adws/adw_modules/new.py`, with the message `"Update SSSF to skill abc1234"` and no trailer.
  - `adw_tickets.feature_sssf_changes("summaries")` contains `reviewer/system.md`, the first commit's short sha and its subject `t`. It contains neither `new.py` nor `Update SSSF`.
  - `feature_sssf_changes("other")` is `""`.
- End to end, next to `test_the_reviewers_never_see_an_sssf_update`. Follow the same flow, but between the runs commit `.claude/skills/sssf/SKILL.md` with an `SSSF-Feature: summaries` trailer. The slug is the folder name (`tickets.py:58`).
  - `run() == 0`.
  - Both reviewers' last prompts contain the section header and `.claude/skills/sssf/SKILL.md`.
  - In the existing test, which only commits "Update SSSF", assert the header is absent.
- Never blocks. A reviewer finding that blocks on `.claude/skills/sssf/SKILL.md` goes to the engineer. No fix phase runs, and `run() == 0`. Reuse the pattern of `test_a_blocking_finding_in_a_protected_file_goes_to_the_engineer`.

A grep found no existing test where an enforced agent writes prompts or extensions under `data_dir`. Run the whole suite anyway, and update a test only if it relied on the old wide grant for such a write.

## Verification

Run the skill suite the way `quality.py` does. Run it from the copy outside the repo that the build brief gives, from the skill root:

`uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`

All tests must pass. That includes:
- the new `tests/test_factory_files.py`, with the overlap case, the db-outside-`data_dir` case and the regression;
- the updated `tests/test_protected_paths.py` and `tests/test_adw_tickets.py`;
- the unchanged `tests/test_rosters.py`, `tests/test_git_dir_guard.py` and `tests/test_agent_commits.py`.

Then:
- `git diff d620acb -- adws/` and `git status --short adws/` are both empty. Nothing in the hub's live copy changed.
- `FACTORY_DATA`, `FACTORY_FILES`, `RUNTIME` and `_DB_SIBLINGS` in `permissions.py` are tuples, and no module-level list, dict or set was added.
- `grep -n "Nothing checks it" .claude/skills/sssf/references/config.md` finds nothing, which means line 602 was rewritten.

## Out of scope

These are the holes this change leaves open, for a reviewer to file as old holes:
- agentsh write rules for the factory paths, as a second layer. agentsh's policy is unchanged.
- Installing SSSF outside the tree (ADR 0001).
- A VM agent overwriting `sssf_refuse.py` within one call.
- Edits a protected file sees before the after-call check runs. For example, an agent edits `justfile` and runs `just` in the same call. Or it edits a pi extension, and a pi process started within that call loads it, such as a subagent from `subagent_create`. This is what's left of `config.md:602`'s known limit.
- A worktree run loads prompts and extensions from the launch checkout (`worktree.py:88-93`). That's outside the work tree its check covers, so an agent's write there isn't seen.
- A roster that points an agent's `prompt_engineering` or `harness_engineering` at files outside `<data_dir>/prompt_engineering/` and `<data_dir>/harness_engineering/` gets no fixed protection for them.
- A trace db configured outside `data_dir` gets no grant, as at the baseline. If it isn't gitignored, a rollback can still delete it, as at the baseline.
- A blocking review finding about an SSSF file that names no path relies on the reviewer prompt, and costs at most one fix pass.
- The documenter's change capture still leaves out SSSF paths. It isn't a review input.
- The export design in `docs/designs/upstream-patches.md`.

## For the engineer

These are post-merge steps, or need files that are off-limits to this run's builder:
- After the merge, update the hub by hand.
  - Update `adws/` from the skill. The hub runs `main`'s `adws/`, which has the gap until then.
  - Decide whether the hub's roster in `adws/adw_sssf_config/` should name `justfile` in the builder's `writes`. This repo's `justfile` holds SSSF's own recipes, so leaving it refused is probably right.
- After the run, check that `git diff <base>..HEAD -- adws/` and `git status --short adws/` are both empty. Then run the `bypass-reviewer` subagent on the result.
- Once the change is merged and the hub is updated, mark the open item in `docs/open.md` done.
