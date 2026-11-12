# Keep agents off the factory files that judge their work

Open item: "Agents can edit factory files that `protected_files` doesn't cover" (`docs/open.md`). Why it matters: ADR 0001, Consequences.

All code changes live in `.claude/skills/sssf/`. **Never edit anything under `adws/`** (the hub's live copy). The hub's roster and `adws/` are updated by hand after the merge (see "For the engineer").

## Baseline

`main` at `d620acb` ("Add the brief for the factory-files guard run"). Every finding is judged against it.

At the baseline, `permissions.permitted` (`.claude/skills/sssf/templates/adws/adw_modules/permissions.py:888`) grants all of `always_writable(cfg)` = `<data_dir>/` first, then checks `cfg.defaults.protected_files`, then `writes`. So every agent, `writes: []` included, may change `<data_dir>/prompt_engineering/` and `<data_dir>/harness_engineering/`; the builder may change `.claude/skills/sssf/`, `justfile`, `sssf.just`, `.githooks/`, `.claude/settings*.json`; the documenter's `**/*.md` reaches the skill's Markdown. `prompts.render` reads prompts at each call, and the ticket chain's review diff (`adw_tickets.py:195-197`) excludes `git_helper.SSSF_PATHS`, so a builder edit to a reviewer prompt changes the reviewer and the reviewer never sees it.

## Threat model

An agent runs as the engineer's own user, with `bash`, `write` and `edit`. It can write any file the user can, run any program, and commit. This guard works **after each agent call**: it compares the work tree's change-set (`snapshot`) before and after, undoes the agent's commits (`undo_commits`), and rolls back each path the agent was not allowed to change (`enforce`). It decides "allowed" purely from the repo-relative path, the agent's `writes`, and the protected list.

What it cannot stop at this level:
- What a protected file does *during* the call that edited it (e.g. an agent edits `justfile` and runs `just` in the same call; a pi extension edited and loaded within the call). Out of scope.
- Writes outside the work tree, or to gitignored paths (they never show in a snapshot). The git directory has its own check, unchanged.
- An agent that kills SSSF before the after-call check runs.
- A VM agent overwriting `sssf_refuse.py` within one call (own open item).

What this change makes it stop: a factory file edited by an agent that doesn't name it in `writes` is rolled back and stops the run *before any later agent call reads it*, so the next `prompts.render` or extension load sees the committed file again.

## Bar

Must:
1. Every refusal at the baseline stays a refusal (the new protected set is a superset; `always_writable` only shrinks).
2. Every path in the fixed list (below) is refused, after every call, to every agent of the starter rosters (`sssf.config.yaml` pi, `sssf.config.claude_code.yaml`, `sssf.config.chatgpt.yaml`), unless that agent names it in its `writes` (same entry, or a path inside a protected folder). `writes: []` agents and the documenter with `**/*.md` included.
3. Every agent may still write `<data_dir>/sessions/` and `<data_dir>/agent-cache/`, `writes: []` included. The planner still writes `specs/`; the documenter still writes `app_docs/`, `docs/` (except roster-protected `docs/adr/`) and project Markdown outside the fixed list.
4. A refused change is rolled back as today and the breach message names it.
5. A builder edit to the project's own `justfile` is now refused — intended and documented. No other new refusal of a starter chain's normal work.
6. The ticket review's new section shows only SSSF-file changes from commits carrying the feature's `SSSF-Feature` trailer, and never blocks the run by itself.
7. No new module-level mutable state: the fixed list is a tuple constant, like `SSSF_PATHS`.

Must never, where the baseline does otherwise: keep an agent's file, run agent code, or delete/overwrite an engineer file.

Runs under `local_guard: agentsh` and in VMs go through the same `enforce` after every call; nothing in agentsh's policy changes.

## Findings: what agents write under `data_dir`

Searched every chain under `.claude/skills/sssf/templates/adws/`, `adw_modules/`, the starter prompts under `templates/prompt_engineering/` and the extensions under `templates/harness_engineering/`:

- `<data_dir>/sessions/<adw_id>/...` — handoff files, envelopes, saved prompts, raw output, the Claude Code config dir (`agent_cc.py:344`), events.jsonl. Agents and SSSF. **Runtime, kept.**
- `<data_dir>/agent-cache/` — agents' tool caches under agentsh (`agentsh_guard.py:348,452`). **Runtime, kept.**
- `<data_dir>/worktrees/<adw_id>/` — created by SSSF (`worktree.py:66`), not by an agent. Inside a worktree run, the agent's `repo_root` is the worktree, so its writes show worktree-relative, never under `worktrees/`.
- `<data_dir>/sssf.db*` — written by the tracer in the SSSF process (`cfg.observability.db`), not by an agent.
- `harness_engineering/subagents.ts` writes under `~/.pi/agent/sessions/subagents`, outside the repo.
- No starter prompt tells an agent to write under `data_dir` outside `sessions/`.

**No agent must write anything under `data_dir` that isn't runtime.** One decision to flag for the reviewer: `worktrees/` and `sssf.db*` are SSSF's own runtime, written while an agent runs. They are gitignored by `install.py` (`GITIGNORE_ENTRIES`), so they normally never appear in a snapshot. But if a repo's `.gitignore` lacks those lines, a narrowed grant would make `enforce` treat them as the agent's untracked files and *delete* them (the trace db, or another run's worktree) — a new deletion the bar forbids. So `always_writable` also grants `<data_dir>/worktrees/` and `<data_dir>/sssf.db*` as SSSF-owned runtime. Neither holds factory files. If the reviewer reads the request as "exactly two entries", this is the deliberate deviation and why.

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

- Add, near `always_writable`, two tuple constants and two functions:

  ```python
  # SSSF's own files: the prompts, the extensions, the skill, the recipes, the
  # hooks, Claude Code's settings (their hooks run on the host) and the ADW
  # code. Protected whatever the roster says; only an agent's own `writes`,
  # naming one, unlocks it. data_dir-relative entries come first.
  FACTORY_DATA = ("prompt_engineering/", "harness_engineering/")
  FACTORY_FILES = (".claude/skills/sssf/", "justfile", "sssf.just", ".githooks/",
                   ".claude/settings.json", ".claude/settings.local.json",
                   "adws/adw_modules/", "adws/adw_sssf_config/", "adws/adw_*.py")
  RUNTIME = ("sessions/", "agent-cache/", "worktrees/", "sssf.db*")

  def factory_files(cfg) -> list[str]:
      data = cfg.defaults.data_dir.rstrip("/")
      return [f"{data}/{p}" for p in FACTORY_DATA] + list(FACTORY_FILES)

  def protected(cfg) -> list[str]:
      """The fixed list, then the roster's protected_files, each once."""
      return list(dict.fromkeys([*factory_files(cfg), *cfg.defaults.protected_files]))
  ```
- `always_writable(cfg)` returns `[f"{data}/{p}" for p in RUNTIME]` (with `data = cfg.defaults.data_dir.rstrip("/")`). Rewrite its docstring: the session runtime and the agents' tool caches are always writable; SSSF's own `worktrees/` and trace db are granted too so a missing gitignore line can't get them deleted by a rollback; the rest of `data_dir` (prompts, extensions) is factory and protected.
- `permitted`: replace `cfg.defaults.protected_files` with `protected(cfg)`. Logic otherwise unchanged (`_names` keeps "same entry or inside a protected folder"; `**/*.md` still doesn't name).
- `enforce`: the scope message's `barred from {...}` uses `protected(run.cfg)`.
- Module docstring (lines 34-36): say `protected_files` joins SSSF's fixed `FACTORY_FILES`, which a roster can't drop.
- `snapshot` docstring line 76-77: say the session runtime is normally gitignored; drop the claim that `data_dir` as a whole "needs no special case".

### 2. `.claude/skills/sssf/templates/adws/adw_modules/agents.py`

- `prompt_variables` (line ~128): render `permissions.protected(run.cfg)` instead of `run.cfg.defaults.protected_files`, so planners and builders are told the fixed list too. (`permissions` is already imported there; if not at module level, import it as the file does elsewhere.)
- The `permission_breach` trace event payload (line ~417): `"protected_files": permissions.protected(run.cfg)`.

### 3. `.claude/skills/sssf/templates/adws/adw_modules/git_helper.py`

- Add `def touched(sha: str, paths: tuple[str, ...]) -> list[str]`: `git diff-tree --no-commit-id --name-only -r --root <sha> -- <paths...>` via `_git`, split into lines, empty list when none.
- Add `def subject(sha: str) -> str`: `git log -1 --format=%s <sha>`.
- Update the `SSSF_PATHS` comment: the ticket review shows the feature's own changes to these in a separate section.

### 4. `.claude/skills/sssf/templates/adws/adw_tickets.py`

- New module-level function:

  ```python
  def feature_sssf_changes(feature_slug: str) -> str:
      """SSSF-file changes in the feature's own commits (SSSF-Feature trailer),
      for the reviewers to report to the engineer. "" when there are none.
      An "Update SSSF" commit between runs carries no trailer, so it never shows."""
  ```
  It walks `git_helper.feature_commits(feature_slug)`, and for each sha with `git_helper.touched(sha, git_helper.SSSF_PATHS)` non-empty adds a line `- <short sha> <subject>: <path>, <path>` (short sha via `git_helper.short_sha`). Non-empty result starts with a header line:
  `SSSF files this feature's own commits changed. These are for the engineer to judge: report each as a non-blocking finding, never as blocking. Inspect with git show <sha> -- adws/ .claude/skills/sssf/`
- In `build_feature`, after `commits = ...`: `sssf = feature_sssf_changes(feature.slug)`, and append `f"\n{sssf}"` (only when non-empty) to both the standards and spec reviewer prompts, after `Commits:`.
- `protected = ", ".join(permissions.protected(run.cfg))` (both the reviewers' `hands_off` and the fixer prompt use it).
- The blocking/for-engineer split (line ~219-221): a finding whose path starts with any of `git_helper.SSSF_PATHS` goes to `for_engineer` even if `permitted` would allow it, so the new section can never block the run or send the fixer into SSSF files:
  `fixable = not path or (permissions.permitted(path, builder, run.cfg) and not path.startswith(git_helper.SSSF_PATHS))`
- Update the comment above `outside = ...` and the module docstring's review paragraph to mention the section.

### 5. Other chains' review inputs — checked, no change

`adw_build_review.py` and `adw_simple_sdlc.py` give the `reviewer` the builder's envelope and the code on disk (the reviewer prompt says to use `git diff`); neither excludes SSSF paths. `adw_plan_build*.py` reuse these. The documenter's `changes.capture` excludes `ChangeCapture.exclude` (SSSF paths), but the documenter is not a reviewer; leave it. Record this in the final report.

### 6. `.claude/skills/sssf/templates/adws/adw_modules/data_types.py`

- Comments only: `AgentConfig.writes` comment (line ~419) "except the roster-wide `protected_files` and SSSF's fixed factory files (permissions.FACTORY_FILES)"; `ConfigDefaults.protected_files` comment: the fixed list is always added on top and can't be dropped here. Keep the default list's values as they are (duplicates are dropped by `protected()`).

### 7. Starter rosters

`.claude/skills/sssf/templates/sssf.config.yaml`, `sssf.config.claude_code.yaml`, `sssf.config.chatgpt.yaml` — comments only, keep `protected_files` entries identical across the three (`test_rosters.py` checks):
- Above `protected_files`, one line: "SSSF always adds its own factory files (prompts, extensions, the skill, justfile, sssf.just, .githooks/, .claude/settings*.json, adws/ code); a roster can't drop them, only an agent's own `writes` naming one unlocks it — a repo whose justfile holds product recipes names `justfile` in the builder's `writes`."
- `sssf.config.yaml` lines 26-29: "It never restricts the session runtime under data_dir" → "under data_dir/sessions/ and data_dir/agent-cache/".
- Builder comment ("It still cannot touch defaults.protected_files") → "…defaults.protected_files or SSSF's factory files".

### 8. Docs

- `.claude/skills/sssf/references/config.md`, section "Write permissions, `writes` and `protected_files"`:
  - Add a paragraph naming the fixed list exactly as above; a roster's `protected_files` adds to it and can't drop an entry; an agent's own `writes` naming an entry (same entry, or a path inside a protected folder) unlocks it; a broad glob doesn't. Why: prompts are read at each call and extensions loaded by agents, so an agent that could edit them changes what judges it; Claude Code settings hooks run on the host in the engineer's next session.
  - Say a repo whose own `justfile` holds product recipes unlocks it by naming `justfile` in the builder's `writes`; otherwise a builder edit to it is refused.
  - Rewrite the paragraph at line ~338 ("The session runtime under `data_dir` is always writable…"): only `sessions/` and `agent-cache/` (plus SSSF's own `worktrees/` and `sssf.db*`) are always writable; `prompt_engineering/` and `harness_engineering/` are protected.
  - Table row for `writes` (line ~76): same correction to "The session runtime under `data_dir`", and "barred from `protected_files` and SSSF's factory files".
- `.claude/skills/sssf/SKILL.md` line 80: "The session runtime under `data_dir` is always writable" → "The session runtime (`data_dir/sessions/`, `agent-cache/`) is always writable, and SSSF's own factory files are always protected".
- No CHANGELOG in the skill or repo. `app_docs/` doesn't exist in this repo; for the documenter: no older write-up to note.

### 9. Tests (`.claude/skills/sssf/tests/`)

Build configs with `agents.load_config(str(SKILL / "templates" / roster))`, as `test_rosters.py` does, for all three starter rosters. Never start an agent.

- New `.claude/skills/sssf/tests/test_factory_files.py`:
  - `FIXED_SAMPLES`: `adws/adw_data/prompt_engineering/reviewer/system.md`, `adws/adw_data/harness_engineering/subagents.ts`, `.claude/skills/sssf/cookbooks/run_adw.md`, `justfile`, `sssf.just`, `.githooks/pre-push`, `.claude/settings.json`, `.claude/settings.local.json`, `adws/adw_modules/rules.py`, `adws/adw_sssf_config/sssf.config.yaml`, `adws/adw_tickets.py`. Assert the sample set covers every entry of `permissions.factory_files(cfg)` (one sample `_matches` each entry), so a new entry can't go untested.
  - Parametrized over roster × agent × sample: `not permissions.permitted(sample, agent, cfg)`.
  - An agent that names it unlocks it: `model_copy(update={"writes": ["justfile"]})` permits `justfile`, not `sssf.just`; `writes: [".claude/skills/sssf/cookbooks/"]` permits a cookbook file, not `.claude/skills/sssf/SKILL.md`; `writes: ["adws/adw_data/prompt_engineering/"]` permits the reviewer prompt.
  - Runtime: for every starter agent (including `writes: []` reviewer, scout, standards_reviewer, spec_reviewer), `permitted("adws/adw_data/sessions/abc/context_handoff/review.md")` and `permitted("adws/adw_data/agent-cache/uv/x")` are true.
  - Normal work still allowed: planner `specs/abc_x.md`; documenter `README.md`, `docs/guide.md`, `app_docs/abc_x.md`, `src/pkg/NOTES.md`; builder `src/app.py`, `tests/test_app.py`.
  - Roster without factory paths: `cfg.model_copy` with `defaults.protected_files=["specs/"]`, and with `[]`: builder still refused every sample; `permissions.protected(cfg)` starts with `factory_files(cfg)` and holds each entry once with the starter roster.
  - Regression (the confirmed repro): temp git repo (`tmp_path`, `monkeypatch.chdir`), `.gitignore` with `adws/adw_data/sessions/`, tracked `adws/adw_data/prompt_engineering/reviewer/system.md` committed; starter pi roster; `run = SimpleNamespace(repo_root=tmp_path, cfg=cfg)` (follow how `test_agent_commits.py` builds its run for `enforce`); `before = permissions.snapshot(run)`; overwrite the file; `pytest.raises(permissions.PermissionBreach)` around `permissions.enforce(run, None, builder, before)`; assert the path is in the message and the file has its committed content again. Same for an untracked new `.claude/skills/sssf/x.md` (deleted).
  - A write to `adws/adw_data/sessions/abc/x.md` with `.gitignore` *not* ignoring it is still allowed by `enforce` for the reviewer (returns the path, no raise).
- `.claude/skills/sssf/tests/test_protected_paths.py::test_every_prompt_can_name_the_protected_paths`: expected value becomes the fixed list for `data_dir="adws/adw_data"` followed by `specs/` (`adws/adw_modules/` is already in the fixed list, so it appears once, in the fixed position). Build it from `permissions.protected(config())` and also assert a literal: it contains "- `justfile`" and ends with "- `specs/`".
- `.claude/skills/sssf/tests/test_adw_tickets.py`:
  - Unit: in a fresh temp repo (chdir), commit `init`; commit touching `adws/adw_data/prompt_engineering/reviewer/system.md` and `src/a.py` with message `"t\n\nSSSF-Feature: summaries"`; commit touching `adws/adw_modules/new.py` with message `"Update SSSF to skill abc1234"` (no trailer). `adw_tickets.feature_sssf_changes("summaries")` contains `reviewer/system.md` and the first commit's short sha, and not `new.py` nor `Update SSSF`; `feature_sssf_changes("other")` is `""`.
  - End to end, next to `test_the_reviewers_never_see_an_sssf_update`: same flow, but commit `.claude/skills/sssf/SKILL.md` with an `SSSF-Feature: summaries` trailer (the slug is the folder name, `tickets.py:58`) between the runs. `run() == 0`; both reviewers' last prompts contain the section header and `.claude/skills/sssf/SKILL.md`. In the existing test (Update SSSF only), assert the header is absent.
  - Never-blocks: a reviewer finding blocking on `.claude/skills/sssf/SKILL.md` goes to the engineer (reuse the pattern of `test_a_blocking_finding_in_a_protected_file_goes_to_the_engineer`), no fix phase runs, `run() == 0`.
- Existing tests that rely on the old wide grant must be updated only if they write prompts/extensions under `data_dir` *as an agent being enforced*; none were found by grep, but run the whole suite.

## Verification

Run the skill suite the way `quality.py` does, from a copy outside the repo per the build brief (from the skill root):

`uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`

All green, including the new `tests/test_factory_files.py`, updated `tests/test_protected_paths.py`, `tests/test_adw_tickets.py` and the unchanged `tests/test_rosters.py`, `tests/test_git_dir_guard.py`, `tests/test_agent_commits.py`.

Then:
- `git diff d620acb -- adws/` and `git status --short adws/` are both empty (no edit to the hub's live copy).
- `grep -n "FACTORY_FILES" .claude/skills/sssf/templates/adws/adw_modules/permissions.py` shows a tuple, and no new module-level list/dict/set was added.

## Out of scope

Holes this change leaves open, to be filed as old holes:
- agentsh write rules for the factory paths as a second layer (agentsh's policy is unchanged).
- Installing SSSF outside the tree (ADR 0001).
- A VM agent overwriting `sssf_refuse.py` within one call.
- Edits a protected file sees before the after-call check runs (e.g. edit `justfile` and run `just` in one call; edit an extension that loads in the same call).
- A roster that points an agent's `prompt_engineering`/`harness_engineering` at files outside `<data_dir>/prompt_engineering/` and `harness_engineering/` gets no fixed protection for them.
- The documenter's change capture still excludes SSSF paths (not a review input).
- The export design in `docs/designs/upstream-patches.md`.

## For the engineer

These are protected for this run's builder, or are post-merge steps:
- After the merge, update the hub by hand: `adws/` from the skill (the hub runs `main`'s `adws/`, which has the gap until then), and the hub's roster in `adws/adw_sssf_config/` if it should name `justfile` in the builder's `writes` (this repo's `justfile` holds SSSF's own recipes, so leaving it refused is probably right).
- After the run: `git diff <base>..HEAD -- adws/` and `git status --short adws/` should be empty; then run the `bypass-reviewer` subagent on the result.
- Once merged and the hub updated, mark the open item in `docs/open.md` done.
