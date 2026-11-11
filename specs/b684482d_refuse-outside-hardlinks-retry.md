# Refuse a wrapped agent whose workspace shares files with the engineer's (second attempt, from run 9497dc7f's round 1)

The hardlink leftover from run d48bd070 (`specs/tasks/guard-agentsh-pin-and-root.md`) and two findings from that run's review. Run 9497dc7f built this spec and stopped after two rounds. Its round 1 head, `0d430ab` (local branch `keep/9497dc7f-round1`), met the spec in the plain cases, and its review found no bypass. Round 1's fix (`2411669`, round 2) rewrote the walk around a stack of open folder descriptors. That was flagged as a new mechanism, and it refused a tree about 1,100 folders deep under a 1,024 open-file limit. **It is discarded. Do not take anything from `keep/9497dc7f-round2`.**

This attempt takes round 1's code as is and makes five small changes on top, from round 1's review (`adws/adw_data/sessions/9497dc7f/context_handoff/bypass_review_1.md`: FP1, BP1, BP2, BP3, OH1, OH2) and round 2's guard report (mode-000 folders).

Round 1's spec is `specs/9497dc7f_refuse-outside-hardlinks_v2.md` and its plan is `adws/adw_data/sessions/9497dc7f/context_handoff/plan.md`. Read them for the reasoning behind the seven items. Everything they asked for is already in `0d430ab`.

## Baseline

`main` at `1ca06de` ("Amend the hardlink spec to restart from run 9497dc7f's round 1"). It holds run d48bd070 merged as `7ab1149`. Every finding is judged against `1ca06de`. Between `85f71f3`, round 1's base, and `1ca06de`, only `specs/tasks/guard-agentsh-hardlinks.md` changed. So `0d430ab`'s files apply cleanly over `1ca06de`.

## Threat model

Agents run as the engineer's own Unix user. agentsh judges each file operation by its path, under a default-deny policy. That policy lets agents write `${PROJECT_ROOT}/**`, the git common dir, the sessions folder, the agent cache and temp folders.

- **What an agent can do.** It can write any file reachable through an allowed path. A hardlink gives one inode two paths. When a file has one link in the workspace and another outside it, a write through the workspace link changes the outside copy, and the engine never sees the outside path. The hub's probe on 2026-11-10 confirmed it: an append to a `~/.cache/uv` decoy was refused, and an append to its workspace link went through.
- **What an agent can't do.** It can't make a new link from the workspace to a denied path, because the engine judges both ends of `link`. Every outside link therefore comes from a tool outside the wrap: uv's and bun's default hardlink installs, a plain local `git clone`, `worktree_setup`, quality checks, plan checks, and `just rebase`'s setup.
- **What this guard can't stop at its level.**
  - It checks at each wrapped launch, not while an agent runs.
  - A process that acts during the walk can change what the walk sees. It can unlink one of two inside links between their sightings, which hides an outside link (BP1). It can also swap a folder for a link in the instant between the walk's `lstat` and its `scandir`.
  - An engine check of `st_nlink` on open would be a fork patch.
  - The guard can't stop a surviving edit to `adws/` code other than the constants the pin line names. The `writes` check puts such an edit back after each phase.

## Bar

Under `local_guard: agentsh`:

1. A wrapped agent never starts while a regular file in its workspace (`guard.root`) or the git common dir has more links than the walk found inside those two trees. That covers plain runs, worktree runs, and links a check or setup command makes between two agent starts.
2. A tree built by `worktree_setup`, a quality check, a plan check, `just rebase`'s setup or the dashboard recipe shares no inode with the engineer's uv or bun cache. bun has no environment setting, which is a named limit.
3. After the engineer's one-time steps, a plain run and a worktree run in sssf-sandbox start their agents, and so does a guard run in the hub.
4. `check_machine` refuses a link at `BINARY` or `CONFIG`. An edit to `BINARY`, `CONFIG`, `POLICY_DIR`, `SERVER`, `API` or `PINNED_SHA256` refuses as "differs".
5. Compared with the baseline, the change never keeps an agent file, runs agent code, or deletes or overwrites an engineer file. The walk only reads, with `lstat` and `scandir`. It never opens, writes or removes a file, and it follows no symlink.
6. Runs under `local_guard: text` behave exactly as on the baseline.
7. Refusing a tree with no outside link is a false positive, with two exceptions that refuse by design and name the path:
   - a path longer than PATH_MAX;
   - an unreadable folder.

   A folder that stops being a folder mid-walk also refuses by design. It only happens while another process changes the tree.
8. **The walk stays path-based.** It keeps no stack of open descriptors and adds no new skip. Its only skip is round 1's `FileNotFoundError`, for an entry that vanished.

Any change to the guard beyond round 1's seven items and the five changes below is a finding.

## Out of scope

File these as old holes:

- The fork's code. An engine check of link count on open is a fork patch.
- pi's policy, and runs under `local_guard: text`.
- Hardlinks made outside SSSF between runs. This change refuses only the next launch.
- A link made by a process outside the wrap **while** an agent runs.
- A process that changes the tree during the walk. It can unlink a file, which hides its outside link (BP1). It can also swap a folder for a link between the pop's `lstat` and its `scandir`. Both are documented, not fixed.
- Agent-writable places outside the two walked trees (OH1):
  - temp folders;
  - on a plain run with an absolute `data_dir` outside the repo, its `sessions/` and `agent-cache/`;
  - on a `--worktree` run, the main checkout's `<data_dir>/sessions/` and `agent-cache/`.
- Objects shared through `objects/info/alternates` by a `git clone --shared` or `--reference` (OH2). These aren't hardlinks, so the walk can't see them. Documented, not fixed.
- A symlinked parent of `BINARY` or `CONFIG`, such as `/etc/agentsh`. It is root-owned.
- bun has no environment setting for its install backend. A repo's own `bun install` without `--backend=copyfile` in `worktree_setup` or a check still links, and the next launch refuses. Likewise for pnpm versions that ignore `npm_config_*`.
- A surviving edit to `adws/` code other than the pinned constants.
- The walk's cost on a big workspace. It is documented, not reduced.

## Files to change

Step 0 takes all of these from `0d430ab`. Steps 1 to 5 then touch only the first three:

- `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`: `outside_links`, the `check_links` docstring
- `.claude/skills/sssf/references/config.md`: "Running local agents under agentsh, `local_guard`"
- `.claude/skills/sssf/tests/test_agentsh_guard.py`: two new tests
- Taken from `0d430ab` and not edited further:
  - `.claude/skills/sssf/templates/adws/adw_modules/data_types.py`
  - `.claude/skills/sssf/templates/adws/adw_modules/plan_checks.py`
  - `.claude/skills/sssf/templates/adws/adw_modules/quality.py`
  - `.claude/skills/sssf/templates/adws/adw_modules/utils.py`
  - `.claude/skills/sssf/templates/adws/adw_modules/worktree.py`
  - `.claude/skills/sssf/templates/justfile`
  - `justfile`
  - `docs/guide/install.md`
  - `docs/sssf.md`

Do **not** touch anything in `protected_files`: `adws/adw_modules/`, `adws/tests/`, `specs/`, `CLAUDE.md` and the rest. Round 1's `specs/9497dc7f_*` files and its edit to `specs/tasks/guard-agentsh-hardlinks.md` are **not** taken. The step 0 command names neither.

## Steps

### 0. Take round 1's code

From the repo root, run exactly:

```
git checkout 0d430ab -- .claude/skills/sssf/references/config.md .claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py .claude/skills/sssf/templates/adws/adw_modules/data_types.py .claude/skills/sssf/templates/adws/adw_modules/plan_checks.py .claude/skills/sssf/templates/adws/adw_modules/quality.py .claude/skills/sssf/templates/adws/adw_modules/utils.py .claude/skills/sssf/templates/adws/adw_modules/worktree.py .claude/skills/sssf/templates/justfile .claude/skills/sssf/tests/test_agentsh_guard.py docs/guide/install.md docs/sssf.md justfile
```

Then run the suite once, from the outside copy described under Verification, to confirm round 1 passes as taken before you edit anything. Don't commit.

### 1. A path longer than PATH_MAX refuses with its own message (`agentsh_guard.py`, `outside_links`)

Round 1 passes full path strings to `os.lstat(folder)`, `os.scandir(folder)` and `entry.stat(follow_symlinks=False)`. `DirEntry.stat` from a path-based `scandir` calls `lstat` on `entry.path`. A path of PATH_MAX or more fails with `ENAMETOOLONG`, which round 1 reports as "can't read … make it readable". That case should still refuse, since the walk can't see inside the path, but with its own message.

- Add `import errno` with the other imports.
- Add a small helper next to `outside_links`:

  ```python
  def _too_long(path: str) -> AgentshRefused:
      return AgentshRefused(
          f"a path in the workspace is too long for the link check ({len(path)} characters, over PATH_MAX), "
          f"so its files can't be checked for links outside the workspace and no agent is started. "
          f"It starts: {path[:200]}. Remove it (`rm -rf` on its top folder), then start the run again. "
          "See \"Running local agents under agentsh\" in .claude/skills/sssf/references/config.md")
  ```

  Keep the gist and adjust the wording if you like. It must say the path is too long for the check, give the first 200 characters of the path (`path[:200]`) and never the whole path, and say to remove it.
- In both `except OSError as error:` branches of `outside_links`, the folder one (`lstat`/`scandir`) and the entry one (`entry.stat`), add this before the existing "can't read" raise: `if error.errno == errno.ENAMETOOLONG: raise _too_long(<the path that failed>) from None`. The path is `folder` in the first and `entry.path` in the second. The top-tree `lstat` keeps its existing message.
- Nothing else changes. The walk still passes path strings, opens no descriptors, and skips only `FileNotFoundError`.

### 2. A folder that is no longer a folder at pop refuses (`agentsh_guard.py`, `outside_links`)

Round 1's pop runs `info = os.lstat(folder)` and then `os.scandir(folder)` without checking the mode, so a folder swapped for a symlink after its parent was listed is followed (BP2). Right after that `lstat`, and before the `seen_dirs` check and the `scandir`, add:

```python
if not stat.S_ISDIR(info.st_mode):
    raise AgentshRefused(
        f"{folder} was a folder when its parent was read and isn't one now, so it can't be checked for "
        "links outside the workspace and no agent is started. Something changed the workspace during the "
        "check: look at it with `ls -la`, then start the run again")
```

Put this inside the existing `try` so a `FileNotFoundError` from `lstat` still skips, and raise it outside any `except OSError`. Use the `lstat` already made. Add no new call.

Update the `outside_links` docstring: "A folder that is no longer a folder when the walk reaches it refuses. A path of PATH_MAX or more refuses with its own message."

### 3. The cost (`config.md`, the `check_links` docstring)

- `config.md`, in the "**Hard links outside the workspace.**" paragraph round 1 added: replace "Cost: one `lstat` per entry, 0.02 s on the hub and sssf-sandbox, and about 0.5 to 1.2 s per 100,000 files." The new text says:
  - the walk's time grows with the number of entries in the workspace and the git common dir, old session folders included, at one `lstat` per entry and about 0.5 to 1.2 s per 100,000 entries;
  - on 2026-11-11 the hub held 725,510 entries, 709,880 of them under `adws/adw_data/sessions/`, which is several seconds per launch;
  - archiving old session folders, by moving them out of the workspace, cuts it.
- `check_links` docstring: keep the 101,000-entry measurement and add "linear in entries, old session folders included: the hub's 725,510 entries on 2026-11-11 cost several seconds per launch".
- Remove any remaining "0.02 s" claim from `config.md` and `agentsh_guard.py`. Check with `grep -n "0.02" .claude/skills/sssf/references/config.md .claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`.

### 4. Known limits (`config.md`, "Limits that remain:" in the agentsh section)

The list round 1 extended sits after "a diff that only helps if it's read". Add these bullets to it, in the list's style, each ending with `;` like its neighbours, and keep the last bullet's `.`:

- a path of PATH_MAX (4,096 bytes on Linux) or more in the workspace or the git common dir. The walk can't read what's in it, so the launch refuses, names the path's first 200 characters and says to remove it;
- a process that unlinks a file during the walk can hide that file's outside link, and one that swaps a folder for a link while the walk reads it can do the same. Only a process acting during the walk can, such as an agent of another run in a worktree under the checkout;
- temp folders aren't walked, and neither are `sessions/` and `agent-cache/` under an absolute `data_dir` outside the repo on a plain run, or under the main checkout on a `--worktree` run. A link there to a cache, made outside the wrap, isn't refused;
- a clone made with `--shared` or `--reference` reads another repo's objects through `objects/info/alternates`. That isn't a hardlink, so the walk can't see it. Make the install clone with `git clone --no-local` and neither flag.

In the refusal list ("A run refuses to start, with a message, when:"), extend round 1's bullet "a regular file in the workspace or the git common dir has a hard link outside them (see below)". It should also say: "or a folder there can't be read, has a path of PATH_MAX or more, or stops being a folder during the check".

### 5. One-time steps (`config.md`, the same paragraph)

Round 1's "One-time steps for the hub and sssf-sandbox: …" sentence. Add a first step: remove the folders that earlier bypass reviews left in `adws/adw_data/sessions/` and that the walk refuses, meaning mode-000 folders and paths of PATH_MAX or more. Give the commands, run from the repo root:

- find mode-000 folders: `find adws/adw_data -type d -perm 000`;
- find paths too long for the check: ``find "$PWD/adws/adw_data" 2>/dev/null | awk 'length >= 4096 { print substr($0, 1, 200) }' | sort -u``. GNU `find` walks paths past PATH_MAX. The absolute path is what counts, because that is what the walk passes;
- remove each with `chmod -R u+rwx <folder> && rm -rf <folder>`, picking the session folder that holds it. Look before you remove.

Add one clause naming the hub's cases as of 2026-11-11: 8 mode-000 folders under runs `6bbc5ca1` and `f689a4dd`, and a 4,522-character chain under run `06ac79f1`. Keep the existing steps after it: rebuild sssf-sandbox's `.venv`, rebuild both repos' dashboard `node_modules`, and make `~/github/sssf-install` a `git clone --no-local`.

Before you write the `find`/`awk` line into `config.md`, run it read-only against a temp tree to check it. Don't run any `rm` or `chmod` on the real `adws/adw_data`.

### 6. Tests (`.claude/skills/sssf/tests/test_agentsh_guard.py`)

Round 1's tests stay as they are. Add these two, next to round 1's walk tests (`test_an_unreadable_folder_refuses` and its neighbours). Every tree goes under `tmp_path`. Never touch `~/.cache/uv`, `~/.bun`, the real `.git`, `adws/adw_data` or `~/github/sssf-install`.

1. **`test_a_path_longer_than_path_max_refuses_with_the_length_message`**
   - Build a chain under `tmp_path / "tree"` whose deepest path is past 4,096 characters. Use 22 levels of 200-character names, made with descriptors: `os.mkdir(name, dir_fd=fd)` then `os.open(name, os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)`. Close each descriptor once the next is open, and close the last one in a `finally`. Put an empty file at the bottom with `os.open(..., os.O_CREAT | os.O_WRONLY, dir_fd=fd)`.
   - `pytest.raises(agentsh_guard.AgentshRefused, match="too long for the link check")` around `outside_links([str(tree)])`.
   - Assert the message holds `str(tree)` (the start of the path) and is shorter than the failing path, so it never names the whole path.
   - Clean up in a `finally` with `shutil.rmtree(tree)`. It walks by descriptor on Linux, and that matters because bypass reviews have left such chains behind before.
   - Round 1's code fails this test ("can't read"). The change makes it pass.
2. **`test_a_folder_replaced_by_a_symlink_after_its_parent_is_listed_refuses`**
   - Layout:
     - `tree/sub/` holds a regular file `x`;
     - `tree/g` is a regular file;
     - `out/` sits outside `tree` and holds `out/f`, a hardlink of `tree/g`.
   - Monkeypatch `os.scandir`, via `monkeypatch.setattr(agentsh_guard.os, "scandir", fake)`, with a wrapper:
     - It calls the real `scandir` and makes a list of its entries.
     - When `path == str(tree)` and it hasn't swapped yet, it calls `entry.stat(follow_symlinks=False)` on each entry, which caches the folder mode the walk will read. It then replaces `tree/sub` with a symlink to `out`, using `shutil.rmtree` and `os.symlink`.
     - It returns the list.
   - So the walk pushes `sub` as a folder and finds a symlink at pop.
   - Assert `pytest.raises(agentsh_guard.AgentshRefused, match="isn't one now")`.
   - On round 1's code the walk follows the symlink, counts `out/f` as inside, and passes. That is why the test exists.
   - Keep the real `scandir` bound before patching, as `real = os.scandir`.

Also run round 1's `test_an_unreadable_folder_refuses` and `test_a_symlinked_tree_refuses` unchanged. They must still pass, since the "can't read" and "not a folder" messages stay.

## Docs this change makes out of date

- `.claude/skills/sssf/references/config.md`: steps 3 to 5. Round 1's version is out of date on cost and limits.
- Docstrings: `outside_links` and `check_links` (steps 2 and 3).
- `docs/guide/install.md` and `docs/sssf.md` are taken from round 1 and need no more change. The `--shared`/`--reference` warning lives in `config.md`, as asked.
- The repo keeps no CHANGELOG.
- `app_docs/` is the documenter's job, not the builder's. Note for the documenter: the write-up for run d48bd070, on the pin line and the glob root, describes the pin line as naming only `BINARY` and `PINNED_SHA256`, and lists the hardlink hole as open. This change supersedes both.

## Verification

Run from a copy of the repo outside the checkout, as run d48bd070's builder did, for example `rsync -a --exclude adws/adw_data --exclude .git ./ "$(mktemp -d)/repo/"`. This run is under agentsh, but the walk only reaches the runner after the merge. From the copy's root:

```
uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider
```

It must exit 0. In particular, `tests/test_agentsh_guard.py` must pass, with round 1's tests and the two new ones, and so must `tests/test_plan_checks.py`, `tests/test_quality_tally.py` and `tests/test_engineer_recipes.py`.

Also confirm, from the real checkout:

- `diff -q justfile .claude/skills/sssf/templates/justfile` exits 0 (this is also a plan check).
- `git diff 0d430ab -- .claude/skills/sssf/templates/adws/adw_modules/` touches only `agentsh_guard.py`'s `outside_links`, the `_too_long` helper, `import errno` and the two docstrings.
- `grep -n "0.02" .claude/skills/sssf/references/config.md` finds no cost claim.
- `grep -n "os.open\|dir_fd\|RLIMIT" .claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py` shows nothing new in `outside_links`. The walk stays path-based.
- `git status --porcelain` shows nothing under `protected_files`, and no `specs/9497dc7f_*` file was added.

The repo's own `test` quality check runs the same suite, so the only extra plan check is the `diff -q`.

## For the engineer

These steps come after the merge and the hub update. They aren't the builder's steps.

1. In both repos, remove the folders the walk refuses in `adws/adw_data/sessions/`, as the one-time steps in `config.md` say:
   - in the hub, 8 mode-000 folders under runs `6bbc5ca1` and `f689a4dd`, and the 4,522-character chain under `06ac79f1/context_handoff/bypass_review_1/cases/base_boundary/long/`;
   - in sssf-sandbox, run the same `find` lines.

   Archiving old session folders out of the workspace also cuts the hub's launch cost, which is several seconds at 725,510 entries.
2. Rebuild with copies:
   - sssf-sandbox: `UV_LINK_MODE=copy uv sync --reinstall`;
   - both repos: `cd .claude/skills/sssf/apps/visualizer && rm -rf node_modules && bun install --backend=copyfile`;
   - the install clone was already re-cloned with `git clone --no-local` on 2026-11-10. Keep it free of `--shared` and `--reference`.
3. Reinstall both repos' policies and config with `just agentsh-install` and the sudo lines it prints. The pin line changed, so runs refuse as "differs" until you do. Then run `sudo systemctl restart agentsh`.
4. The hub session then checks:
   - a plain run and a worktree run in sssf-sandbox start their agents;
   - a guard run in the hub starts;
   - a decoy hardlink from a workspace file to a temp file outside the workspace makes the next launch refuse, and removing it lets the launch start.
