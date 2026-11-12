# Close the holes left in the factory-files guard

The brief is `specs/tasks/guard-factory-files-2.md`. This run follows run 37d69462 (`specs/37d69462_factory-files-guard_v3.md`), and that spec's bar still stands.

All code changes go in `.claude/skills/sssf/`. **Never edit anything under `adws/`.** That's the hub's live copy, and it runs `main`'s guard, which has every hole below. Run the suite from the copy outside the repo, using the copy command the build brief gives.

This is revision 2. After spec review 1:
- An untracked record no longer replaces a diff status. It merges into it, so `git rm --cached justfile` followed by a rewrite gives `D,?`. `_roll_back` deletes only a path that is untracked alone (`?`). A `D,?` path is restored from `HEAD`. Two tests cover this.
- A staged-new path is unstaged. Then it's deleted when git says it isn't ignored, the same as an untracked new file today. It's left on disk only when it's ignored.
- A comment explains what the `?` fallback for a failed content chunk can miss.
- The plan says how a sparse checkout's cost grows, and what "strictly inside" means for the db grant when `data_dir` is the repo root.

## Baseline

`main` at `d620acb` ("Add the brief for the factory-files guard run"). Every finding is judged against it. This run starts from `6472edf`, run 37d69462's commit, which already refuses a fixed list of factory paths. Any refusal at either commit must stay a refusal.

At both commits, `permissions.snapshot` (`.claude/skills/sssf/templates/adws/adw_modules/permissions.py:73-91`) reads `git diff HEAD --numstat` and `git ls-files --others --exclude-standard`. It fingerprints a tracked change by its `+,-` counts and an untracked file by the word `untracked`. `_roll_back` (`:947-968`) restores a path from the index with `git checkout -- <path>`.

Checked with git 2.43 in a scratch repo while planning:
- With renames on, the reviewer-prompt swap shows as `0 0 {p => f}/a.md`. With `--no-renames --name-status -z` it shows as `A f/a.md` and `D p/a.md`, even when the old path is gitignored and on disk.
- `git checkout HEAD -- p/a.md` brings back a file that `git rm --cached` took out.
- After `--skip-worktree j` plus an edit, and `--assume-unchanged k` plus an edit, `git diff HEAD` shows neither. `git ls-files -v -z` prints `S j` and `h k`.
- `git checkout HEAD -- k` on a skip-worktree path fails with "pathspec did not match", so the flag has to be cleared first.
- `--fsmonitor-valid` is ignored under `-c core.fsmonitor=`, which `_SAFE` already sets. The diff saw that edit.
- `ls-files --others` prints `"\303\251.md"`, quoted. With `-z` it prints the raw bytes.

## Threat model

An agent runs as the engineer's own user, with `bash`, `write` and `edit`. It can write any file the user can, run any git command (`update-index`, `rm --cached`, `add`), edit `.gitignore`, and commit.

This guard acts after each agent call:
- `snapshot` records the work tree's change-set before the call and again after it.
- `undo_commits` undoes any commits the agent made.
- `enforce` refuses and rolls back each path the agent wasn't allowed to change.

After this change, the snapshot sees what the agent did to every non-ignored path:
- a rename, as a delete and an add;
- a non-ASCII name;
- a change hidden behind an index flag;
- a rewrite of a file that was already dirty or untracked.

The fixed list also covers what `just` and `uv run adws/adw_*.py` load from the tree before the guard code loads.

At its level, it can't stop:
- A change used within the same call that made it, such as editing `local.just` and then running `just`.
- Writes to gitignored paths, and writes outside the work tree.
- A hand-forged `.git/index`. Example: stat data that makes git treat a changed file as clean, or a forged untracked cache. Git itself trusts the index, and hashing every tracked file would make the snapshot grow with the tree.
- An agent that kills SSSF before the after-call check runs.
- Edits by the engineer during the call, which can't be told apart from the agent's.

## Bar

Must:
1. Every refusal at `d620acb` and at `6472edf` stays a refusal.
   - `permitted` keeps its shape: the runtime grant, then the fixed-list check, then the roster check.
   - The fixed list only grows. `adws/` covers `adws/adw_modules/`, `adws/adw_sssf_config/` and `adws/adw_*.py`, and `.claude/` covers `.claude/skills/sssf/` and `.claude/settings*.json`.
   - The runtime grant never widens. Normalising `data_dir` changes only how a path is written. The db grant only narrows.
2. Each case 1-10 in the brief is refused, or for case 5 restored, for every agent of the pi roster (`sssf.config.yaml`) and the Claude roster (`sssf.config.claude_code.yaml`) that doesn't name the path in its `writes`. The tests also run the ChatGPT roster wherever that's cheap. This holds on a tree where `.git/info/exclude` lists none of the paths.
3. No new refusal of a starter chain's normal work, beyond edits to the newly listed paths, which take an unlock. Normal work includes:
   - the builder's product code and tests;
   - the planner's `specs/`;
   - the documenter's `docs/`, `README.md` and `app_docs/`;
   - `__pycache__/` and `node_modules/` that test runs write, which are gitignored and never seen.
4. The snapshot's per-path work (hashing, Python parsing beyond git's own output) grows only with the number of changed, untracked and index-flagged paths. The git calls themselves walk the index once each, as `git diff HEAD` already does. `git ls-files -v` is one more such walk. Nothing is hashed for a clean, unflagged path. One exception is accepted and named in Out of scope: in a sparse checkout, every `S`-flagged entry is fingerprinted, so the cost grows with the number of entries outside the sparse cone. Each of those is a `-` (missing) from one `[ -L ] / [ -f ]` test in the content script, so no hash runs for it.
5. No new module-level mutable state. New constants are tuples or strings.
6. Must never, where the baseline does otherwise: keep an agent's file, run agent code, or delete or overwrite an engineer file. Concretely:
   - A path that was dirty, untracked or flagged before the call is never written by the rollback, as today.
   - A path that is staged-new (`A`) after the call is unstaged first. It is then deleted only when `git check-ignore` says it isn't ignored. That leaves it in exactly the state of a new untracked file, which the baseline deletes too. A file that is ignored may be an engineer's gitignored file that the agent force-added, so it's left on disk and named. At the baseline, the index checkout left every staged-new file on disk. The deletion only covers files the baseline would delete in their untracked form, so it adds no new way to lose an engineer file.
   - No hashing step runs a git filter or textconv (`--no-filters`, `--no-textconv`, `--no-ext-diff`).

agentsh's policy doesn't change.

## Findings: what the launch loads from the tree

`just <recipe>` with the starter `.claude/skills/sssf/templates/justfile` reads the following:
- `justfile`. This is fixed already.
- `local.just`, through `import? 'local.just'`. A `set shell` there runs every recipe through the agent's shell. **SSSF's: newly fixed.**
- `sssf.just`, when a repo loads it with `mod sssf`. This is fixed already.
- `.env`, through `set dotenv-load`. It can set `UV_*`, `PYTHONPATH` and the rest. **The project's own: left unprotected.**
- `.justfile` or `Justfile` next to `justfile`. just refuses to run when there's more than one candidate, so another one stops the run but runs nothing.

`uv run adws/adw_x.py` runs every ADW. Each one is a PEP 723 script (`# /// script`), and it reads:
- `adws/adw_x.py` and its lock file `adws/adw_x.py.lock`, if one exists.
- `adws/` first on `sys.path`, so `adws/yaml.py` or `adws/yaml/` shadows PyYAML. **SSSF's: newly fixed as `adws/` whole.**
- `uv.toml`, and `pyproject.toml`'s `[tool.uv]`, from the cwd upward. Either can set the package index. **The project's own Python tooling: left unprotected.**
- `.python-version` in the cwd, which uv reads for a script. This was checked: `3.1` there made `uv run` fail with "Invalid version request". **The project's own: left unprotected.**
- `.venv/`, which is normally gitignored and so out of scope.

SSSF's agent harnesses read:
- Claude Code reads all of `.claude/`: skills, agents, hooks and settings. **Newly fixed as `.claude/` whole.**
- pi reads `.pi/`: extensions and `settings.json`. **Newly fixed.**
- Both read `.mcp.json`. **Newly fixed.**

`CLAUDE.md`, `AGENTS.md` and other context files are out of scope, as the brief says.

Each unprotected launch input is listed in Out of scope, so a reviewer files it as an old hole.

## Changes

### 1. `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`

**Fixed list.** Replace `FACTORY_FILES` with this:

```python
FACTORY_FILES = (".claude/", ".pi/", ".mcp.json", "justfile", "sssf.just", "local.just",
                 ".githooks/", "adws/")
```

Rewrite the comment above it. It names what each entry covers:
- the skill, agents, hooks and settings under `.claude/`;
- pi's extensions and settings under `.pi/`;
- the MCP servers in `.mcp.json`;
- the recipes and their `local.just` import;
- the hooks;
- the ADW code, and modules beside it that would shadow an import.

It also says that the runtime under `data_dir` stays writable, because `always_writable` is checked first. Keep `FACTORY_DATA` as it is: `adws/` covers it under the default `data_dir`, but a `data_dir` outside `adws/` needs it.

**Clean config paths.** Add a helper:

```python
def _clean(path: str) -> str:
    """A config path as a repo-relative path: no leading ./, no doubled or
    trailing slash. "" for the repo root. Absolute paths stay absolute."""
```

Use `posixpath.normpath(path.strip())`, map `"."` to `""`, and keep a leading `/`. Then:
- `factory_files(cfg)` uses `data = _clean(cfg.defaults.data_dir)`, and builds each entry as `f"{data}/{p}"`, or just `p` when `data == ""`.
- `always_writable(cfg)` does the same for `RUNTIME`.

**Db grant.** In `always_writable`, grant the db only when all of these hold:
- the raw `cfg.observability.db` doesn't end in `/` and holds none of `*`, `?` or `[`;
- `db = _clean(raw)` is strictly inside `data_dir`:
  - When `data != ""`, it starts with `data + "/"` and isn't equal to `data`.
  - When `data == ""`, `data_dir` is the repo root. Then it's any non-empty relative path: not starting with `/`, and with no `..` component.
  - The baseline granted all of `data_dir` either way, so this stays inside that grant.
- `db` matches no `f"{data}/{p}"` entry for `p in FACTORY_DATA`.

That last check must **not** use the whole `factory_files(cfg)`. `adws/` now matches the default `adws/adw_data/sssf.db`, and would drop its grant. Grant `db + s for s in _DB_SIBLINGS`, as today. Update the comment.

**`snapshot`.** Rewrite it to make three git reads, each through `_git`, with `cwd=run.repo_root`:
1. `git diff HEAD --no-renames --no-ext-diff --no-textconv --name-status -z`. Its records are `<status>\0<path>\0`, and each one gives `kind = status letter` (M, A, D, T, …).
2. `git ls-files --others --exclude-standard -z`. Each untracked record **merges** into the path's kind: `kind = existing + ",?"` when the diff already gave the path a status, else `kind = "?"`. This matters because `git rm --cached justfile` followed by a rewrite makes git report `justfile` both as `D` in the diff and as untracked. The merged kind `D,?` is restored from `HEAD`, never deleted (see `_roll_back`). It's the same with no `.gitignore` line, as in case 1 without the ignore step.
3. `git ls-files -v -z`. Its records are `<tag> <path>`. Keep only those whose tag isn't `H`. Lowercase means assume-unchanged, and `S` or `s` means skip-worktree. For each one, add `"flag-" + tag` to its kind, keeping the diff status when the path is already there. Example: `M,flag-S`.

Then fingerprint every collected path by content, in batches. Add a script constant `_CONTENT`. For each path argument, it prints one NUL-terminated field:
- `L` plus the `readlink` output for a symlink;
- `F` plus `git -c core.fsmonitor= hash-object --no-filters -- "$p"` for a regular file;
- `D` for a directory (a gitlink, or an untracked nested repo);
- `-` when the path is missing.

Run it with `sandbox.current().run(["sh", "-c", _CONTENT, "sh", *chunk], cwd=run.repo_root, text=False)`. Use chunks of at most 256 paths, and decode with `surrogateescape`. If the field count doesn't match, fall back to `"?"` for that chunk's content. Never raise, since a snapshot that fails must not stop the run differently from today. Put a comment at the fallback saying what it can miss. If both the before and the after content reads fail for a path, a rewrite of an already-dirty or untracked file (case 4) gives the same fingerprint on both sides and isn't seen. Appearing, vanishing and a status change are still seen. The fallback is for a broken sandbox, not a path the agent controls.

The fingerprint is `f"{kind}:{content}"`. For example: `"M:Fe69de29…"`, `"?:Fabc…"`, `"D:-"`, `"D:Fdef…"` (the rename case), `"H"` never appears.

Keep the return type `dict[str, str]`. Rewrite the docstring to say:
- Renames show as a delete and an add.
- Names are never quoted.
- Flagged paths show by content.
- Every rewrite of a dirty or untracked file registers.
- Gitignored paths never appear.

Add a small helper `_fields(text)`, which splits NUL output and drops the trailing empty field, if it helps.

**`_roll_back(run, path, before, after)`.** Keep the order of cases:
1. **`path in before`.** Unchanged logic. If it's gone from `after`, return `"REVERTED-BY-AGENT (uncommitted work lost, cannot restore)"`. Otherwise return `"left as-is (it was already changed, untracked or flagged before the call, so it may hold your work)"`.
2. **The kind is exactly `?`**, meaning untracked alone and not in `before`. Parse the kind as the text before the first `:`, and compare the whole kind, never a prefix. Delete it, as today. A kind such as `D,?` doesn't match here. It's a path in `HEAD` that the agent took out of the index, and it falls through to case 4.
3. **The kind's diff status is `A` and it has no `flag`** (staged-new, not in `HEAD`).
   - Run `git rm -q --cached -- <path>` with `writes=True`. If that fails, return `"could not unstage"`.
   - Then run `git check-ignore -q --no-index -- <path>`.
     - Exit 1 (not ignored): delete the file as in case 2, and return `"unstaged and deleted"`.
     - Exit 0 (ignored): leave it, and return `"unstaged; left on disk because it is gitignored and may be yours, so check it by hand"`.
     - Any other exit: leave it, and say the same.
4. **Otherwise** (in `HEAD`, clean before). Run these in order:
   - `git update-index -q --no-skip-worktree --no-assume-unchanged -- <path>`, with `writes=True`. Ignore its exit status: the path may no longer be in the index.
   - `git checkout HEAD -- <path>`, with `writes=True`. This restores both the index and the work tree, so a `git rm`'d file comes back.

   Return `"rolled back"` or `"could not roll back"`.

Update the `_roll_back` docstring, and the `undo_commits` docstring line that says "_roll_back restores a path from the index". `HEAD` is now the source for clean paths. Keep the point that a reset keeps the changes unstaged.

**Module docstring.** Add one paragraph saying how the change-set is read: no renames, `-z`, flagged paths and content hashes. Also say that gitignored paths are never seen.

No other function changes. `permitted`, `_names`, `_matches`, `enforce` and the git-directory guard stay as they are.

### 2. `.claude/skills/sssf/templates/adws/adw_modules/data_types.py`

Comments only. Where the comments name the fixed list's contents (`AgentConfig.writes`, `ConfigDefaults.protected_files`), say `adws/` and `.claude/` in place of the old entries. Leave the default values unchanged.

### 3. Starter rosters

Change comments only in these three files, and keep the `protected_files` entries identical, because `tests/test_rosters.py` checks them:
- `.claude/skills/sssf/templates/sssf.config.yaml`
- `.claude/skills/sssf/templates/sssf.config.claude_code.yaml`
- `.claude/skills/sssf/templates/sssf.config.chatgpt.yaml`

Replace the comment above `protected_files` (`sssf.config.yaml:30-33` and its counterparts) with this:

```
# SSSF always protects its own factory files too: all of adws/ (except the runtime under
# data_dir), all of .claude/, .pi/, .mcp.json, justfile, sssf.just, local.just and .githooks/.
# A roster can't drop them; only an agent's own `writes` naming one unlocks it. Gitignored
# files are never checked.
```

Keep the existing "A repo whose justfile holds product recipes…" line after it.

### 4. Docs

`.claude/skills/sssf/references/config.md`, in "Write permissions, `writes` and `protected_files"`:
- The **SSSF's fixed factory files** paragraph (`:347-360`) gets the new list:
  - `<data_dir>/prompt_engineering/`
  - `<data_dir>/harness_engineering/`
  - `.claude/`
  - `.pi/`
  - `.mcp.json`
  - `justfile`
  - `sssf.just`
  - `local.just`
  - `.githooks/`
  - `adws/`

  Its "Why" part gains these:
  - `local.just` is imported by the justfile, so its `set shell` runs every recipe.
  - `uv run` puts `adws/` first on `sys.path`, so a module there shadows a dependency.
  - Claude Code and pi load `.claude/`, `.pi/` and `.mcp.json`.
- Add one line for each project file that now needs an unlock to edit:
  - other skills under `.claude/skills/`, `.claude/agents/` and `.claude/hooks/`;
  - `.pi/`;
  - `.mcp.json`;
  - `local.just`.

  Each is unlocked by naming it in the builder's `writes`, for example `.claude/skills/myskill/`.
- Add one line saying the check never sees gitignored files. Python's `__pycache__/` and the skill's `node_modules/` are written during normal work, so a gitignored file in a fixed path isn't checked.
- Add one line on how the check sees a change:
  - renames count as a delete and an add;
  - index flags (`skip-worktree`, `assume-unchanged`) don't hide a change;
  - a rewrite of an already-dirty file counts;
  - a refused change to a file that was clean is restored from `HEAD`, while one that was already dirty is left as it is.
- Data paths are normalised. `./adws/adw_data` and `adws//adw_data/` mean `adws/adw_data`. The trace db is granted only as a file path inside `data_dir`, never one ending in `/` or holding a glob. Put this in the runtime paragraph (`:338-345`).
- The example at `:318` keeps the roster's own entries. Its comment at `:321` stays.

`.claude/skills/sssf/SKILL.md:80`: make sure the sentence naming the always-protected factory files says `adws/` and `.claude/` as wholes. If it doesn't list entries, leave it.

`.claude/skills/sssf/references/handoff.md:104`: `{{protected_files}}` renders `permissions.protected(cfg)`, which is SSSF's fixed list first and then the roster's. Correct the row if it says "the roster's" only.

`.claude/skills/sssf/cookbooks/update_modules.md:28`, the `permissions.py` row: add "and SSSF's fixed factory files, reading renames, index flags and content hashes".

Agent prompts. Add one sentence after the `protected_files` rule in each of these:
- `.claude/skills/sssf/templates/prompt_engineering/builder/system.md:11`
- `.claude/skills/sssf/templates/prompt_engineering/ticket_builder/system.md:12`
- `.claude/skills/sssf/templates/prompt_engineering/planner/system.md:14`

The sentence: "That list includes all of `adws/` (except the session runtime under it) and all of `.claude/`, `.pi/` and `.mcp.json`, so a new module beside the ADW scripts or a project skill edit is refused too unless your `writes` names it."

The `user.md` files render the list, so they need no change.

There's no CHANGELOG, and no `app_docs/` in this repo.

The other grep hits (`run_adw.md`, `install.md`, `how_to_prompt_for_the_eng.md`, `create_adw.md`, `sssf_overview.md`, `update_adw.md`, `SKILL.md:16`) mention `adw_*.py` as the ADW menu, not as a protected path. Leave them.

### 5. Tests

These go in `.claude/skills/sssf/tests/`. Configs always come from `agents.load_config(str(SKILL / "templates" / roster))`. Never start an agent. Use temp git repos with `tmp_path` and `monkeypatch.chdir`, built like the `tree` fixture in `tests/test_factory_files.py`, and run through `snapshot` and `enforce`.

Update `.claude/skills/sssf/tests/test_factory_files.py`:
- Add samples to `FIXED_SAMPLES`:
  - `local.just`
  - `adws/yaml.py`
  - `adws/yaml/__init__.py`
  - `.claude/agents/bypass-reviewer.md`
  - `.claude/hooks/x.sh`
  - `.claude/skills/other/SKILL.md`
  - `.pi/extensions/x.ts`
  - `.pi/settings.json`
  - `.mcp.json`

  `test_every_fixed_entry_has_a_sample` then covers the new entries. The existing parametrized refusal test runs every sample against every agent of all three rosters.
- `test_naming_an_entry_unlocks_it`: a builder with `writes` naming each new entry is permitted a path under it:
  - `["local.just"]`
  - `[".pi/"]`
  - `[".mcp.json"]`
  - `[".claude/skills/other/"]`, which still doesn't permit `.claude/skills/sssf/SKILL.md`
  - `["adws/yaml.py"]`
- `test_the_constants_are_tuples` stays.

Add a new file, `.claude/skills/sssf/tests/test_factory_holes.py`. Each test below is a regression that fails on `6472edf` and passes after.

Parametrize the per-agent tests over the pi and Claude rosters, and over every starter agent whose `writes` doesn't name the path. A helper picks those agents with `permissions.permitted(path, agent, cfg)` being false on a clean config. Write the repo's `.git/info/exclude` empty in the fixture.

- **Case 1, rename.** Commit `adws/adw_data/prompt_engineering/reviewer/system.md` and a copy at `src/fixtures/system.md`. Then, after `snapshot`:
  - `git rm -q --cached` the prompt;
  - `git add` a fresh copy at `src/fixtures/system2.md`;
  - write `evil` at the prompt path;
  - append the prompt path to `.gitignore`.

  `enforce` raises, the message names the prompt path, and the prompt reads its committed text again and is back in the index (`git ls-files --error-unmatch`). Also add a `.claude/settings.json` variant moved from `src/fixtures/settings.json`. Add a **no-ignore variant** too: the same steps without the `.gitignore` line. The snapshot then has the prompt as `D,?`, and `enforce` restores it from `HEAD` rather than deleting it. Assert the committed text is back, and the path is back in the index.
- **Case 2, quoted names.** A new untracked `.claude/skills/sssf/é.md` is refused and deleted. Assert that `snapshot` has the key `".claude/skills/sssf/é.md"` with no quotes.
- **Case 3, index flags.** On a committed `justfile`, run `git update-index --skip-worktree justfile`, then edit it. `enforce` raises, and `justfile` has its committed content with the flag cleared (`git ls-files -v justfile` starts with `H`). The same goes for `--assume-unchanged`. A reviewer (`writes: []`) that flags and edits its own prompt is refused and restored.
- **Case 4, coarse fingerprints.** Before `snapshot`, the engineer edits a committed `justfile` (one line changed). The agent then rewrites the same line differently, so the `+/-` counts stay the same. `enforce` raises, and the message says "left as-is". The same goes for an untracked `.claude/skills/sssf/new.md` that exists before the call and is rewritten during it. Assert that the engineer's file isn't deleted: it still exists with the agent's content, and the message says so.
- **Case 5, rollback from HEAD.** The agent runs `git rm -q justfile`, which removes it from both the index and disk. `enforce` raises, and `justfile` is back on disk and in the index.
  - A path that was dirty before the call and then `git rm`'d is left alone, and the message says "REVERTED-BY-AGENT" or "left as-is".
  - **`git rm -q --cached justfile` and a rewrite of `justfile`**, with no `.gitignore` line. Assert that `snapshot` gives `justfile` a kind of `D,?`. Assert that `enforce` raises, and that `justfile` is on disk with its committed content and back in the index, not deleted.
  - A staged-new fixed path (`git add adws/yaml.py`) is unstaged and deleted, and the message says "unstaged and deleted".
  - A staged-new path that is gitignored stays on disk, and the message says so. To set it up, list `adws/local.cfg` in `.gitignore`, write it before `snapshot`, and have the agent `git add -f adws/local.cfg`.
- **Case 6.** A new `local.just` holding `set shell := ["sh", "-c"]` is refused and deleted.
- **Case 7.** New `adws/yaml.py` and `adws/yaml/__init__.py` are refused and deleted, while `adws/adw_data/sessions/x/y.md` is still allowed to the reviewer.
- **Case 8.** New `.claude/agents/x.md`, `.claude/hooks/x.sh`, `.claude/skills/other/SKILL.md`, `.pi/extensions/x.ts`, `.pi/settings.json` and `.mcp.json` are each refused.
- **Case 9.** Take a cfg with `defaults.data_dir = "./adws/adw_data"` through `model_copy`, with the ADW samples moved out to a `data_dir` outside `adws/`. Also take one with `"data//"`. Then:
  - `factory_files(cfg)` holds `data/prompt_engineering/`;
  - `data/prompt_engineering/reviewer/system.md` is refused to the reviewer;
  - `data/sessions/x.md` is permitted.
- **Case 10.** With `data_dir: adws/adw_data`:
  - `observability.db` set to `adws/adw_data/`, `adws/adw_data/**`, `adws/adw_data/*.db` or `adws/adw_data` adds no db grant, and the reviewer is refused `adws/adw_data/prompt_engineering/reviewer/system.md`;
  - set to `./adws/adw_data/sssf.db`, it grants `adws/adw_data/sssf.db` and `adws/adw_data/sssf.db-wal`;
  - the default db is still granted, which guards the `adws/` overlap.
- **Normal work.** In one temp repo, through `enforce`, each of these returns the touched paths and doesn't raise:
  - the builder edits `src/app.py` and adds `tests/test_app.py`;
  - the planner adds `specs/abc_x.md`;
  - the documenter edits `docs/guide.md`;
  - all of them alongside `adws/adw_modules/__pycache__/x.pyc` and `src/__pycache__/app.cpython-312.pyc`, written with `.gitignore` holding `__pycache__/`.
- **Snapshot cost.** Commit 200 files, change 2, add 1 untracked, and flag none. Monkeypatch `permissions._CONTENT` runs, or count `sandbox.current().run` calls, to show that only 3 paths are fingerprinted. That is, the paths passed to the content script number 3.

Existing tests to adapt:
- `.claude/skills/sssf/tests/test_sandbox.py:89-90` asserts `{"new.txt": "untracked"}`. Change it to assert the key set is `{"new.txt"}` and that the value starts with `"?:"`. Pass that value to `_roll_back`. Keep its fake sandbox working: if the fake can't run `sh`, extend the fake rather than the code.
- Run the whole suite. Adapt any test that compared fingerprint values exactly. Never weaken one that checks a refusal.

## Verification

Run from the copy outside the repo, at the skill root, the way `quality.py` runs the skill suite:

`uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`

Everything must pass, including:
- `tests/test_factory_holes.py`, which has all ten cases, normal work and snapshot cost;
- the updated `tests/test_factory_files.py` and `tests/test_sandbox.py`;
- `tests/test_rosters.py`, `tests/test_git_dir_guard.py`, `tests/test_agent_commits.py`, `tests/test_protected_paths.py` and `tests/test_adw_tickets.py`, unchanged.

Show that each new regression fails on `6472edf`. Do it in a scratch copy of the skill: check out `6472edf`'s `templates/adws/adw_modules/permissions.py`, and run `tests/test_factory_holes.py` against it. It should fail. Report the counts.

Then:
- `git diff 6472edf -- adws/` and `git status --short adws/` are both empty.
- `grep -n "adws/adw_\*\.py" .claude/skills/sssf/templates/adws/adw_modules/permissions.py` finds no `FACTORY_FILES` entry.
- `FACTORY_FILES`, `FACTORY_DATA`, `RUNTIME` and `_DB_SIBLINGS` are tuples, and no module-level list, dict or set was added.

## Out of scope

These holes stay open, for a reviewer to file as old holes:
- **Gitignored files in fixed paths.** Examples: `.claude/settings.local.json` where `.git/info/exclude` lists it, a `.pyc` under `adws/**/__pycache__/`, and the skill's `node_modules/`. An agentsh write rule on the fixed paths is the likely fix, and it needs its own design. Engineer note below.
- An agent gitignoring its own new file in a fixed folder, through a permitted `.gitignore` edit.
- **Launch inputs that are the project's own:**
  - `.env`, which reaches every recipe through `dotenv-load`;
  - `pyproject.toml`'s `[tool.uv]` and `uv.toml`, which set the package index;
  - `.python-version`, which uv reads for each script.

  An agent with write access to them can change the next launch.
- A hand-forged `.git/index`, such as stat data or a forged untracked cache, that makes git report a changed file as clean.
- A staged-new fixed path that is gitignored is unstaged but left on disk, because it may be an engineer's gitignored file the agent force-added.
- In a sparse checkout, every `S`-flagged entry is fingerprinted, so the snapshot's cost grows with the entries outside the sparse cone. A missing entry is never hashed.
- If the content read fails for a chunk both before and after the call, a same-status rewrite in that chunk isn't seen (the `?` fallback).
- An absolute `data_dir` never matches a snapshot path. That's unchanged.
- Any agent writing another run's worktree under `<data_dir>/worktrees/`.
- `CLAUDE.md`, `AGENTS.md` and other context files.
- Telling the engineer's edits during a call apart from the agent's.
- agentsh policy changes, installing SSSF outside the tree (ADR 0001), and the export design.
- A change used within the same call, before the check runs.

## For the engineer

These are steps outside the builder's reach:
- After the run, check that `git diff main..HEAD -- adws/` and `git status --short adws/` are both empty. Then run the `bypass-reviewer` subagent on `main..HEAD`.
- After the merge, update the hub's `adws/` from the skill by hand. The hub runs `main`'s guard, which has every hole until then.
- The hub's own roster in `adws/adw_sssf_config/` keeps its `protected_files` entries. `adws/` and `.claude/` now cover them in the fixed list. The hub edits `.claude/agents/` and `local.just` by hand, so decide whether any of its agents should name them in `writes`. Probably none should.
- Add an item to `docs/open.md`: "An agentsh write rule for the fixed factory paths, to cover gitignored files there (`__pycache__/`, `node_modules/`, `.claude/settings.local.json`)". `docs/` isn't protected, but the item belongs to the engineer's design queue, so leave it to them.
