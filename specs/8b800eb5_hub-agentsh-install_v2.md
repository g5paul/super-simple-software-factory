# Install the agentsh policy from the hub, not from the project

## Summary

Replace the project-side `adws/adw_agentsh_policy.py` with a hub-side script, `.claude/skills/sssf/scripts/agentsh_install.py`, run as:

```
uv run --directory <hub> .claude/skills/sssf/scripts/agentsh_install.py --repo <project main checkout>
```

It renders the server config and the policies with the **hub's** `agentsh_guard` code and the **hub's** `agentsh_sssf.yaml`. From the project it reads only data:
- `adws/adw_sssf_config/repo.yaml` and `sssf.config.yaml`, through `yaml.safe_load`
- `stamp.json`'s `skill`, through `json`

It runs no git in the project and imports nothing from it. It refuses when the project's skill version differs from the hub's. Before the sudo lines, it prints a diff of each new file against `/etc/agentsh/`, or the whole file when nothing is installed.

A run's "differs" refusal now sends the engineer to `git status` / `git diff` on `adws/` first, then to the hub install. `install.py --update` removes SSSF code that left the skill, as long as the repo never changed it. That removes `adw_agentsh_policy.py` from projects.

Do not reuse the e027505a approach. That means no provenance check, no commit trailers, and no list of git or Python settings to override.

Changes from the first version of this spec, after spec review 1:
- The "Nothing to install" path still prints the restart line.
- Installed files are compared as exact bytes.
- Bar 3 now speaks of differing lines.
- `update()` skips a path that isn't a regular file.
- Out of scope names the forged-stamp deletion and the hub's own install.

## Baseline

`main` at `94af2eb` ("Record the install-check run and write the hub-install task"). Every finding is judged against it. At the baseline:
- `adws/adw_agentsh_policy.py`, in the project:
  - imports the project's `adw_modules`
  - runs git in the project (`worktree.main_checkout()`)
  - renders with the project's `agentsh_sssf.yaml`
  - prints sudo lines with no diff, always ending in `sudo systemctl restart agentsh`
- `agentsh_guard.FIX` points every refusal straight at that script.
- `install.py --update` leaves a file that left the skill in place and lists it under "no longer in the skill".

## Threat model

An agent runs as the engineer's own Unix user.

Under `local_guard: agentsh` it can read and write the project workspace. That includes:
- `adws/`, `repo.yaml`, `sssf.config.yaml` and `stamp.json`
- the project's skill copy
- the `.git` data, except `config` and `hooks`
- `/tmp`

Under `local_guard: text`, or outside the wrap, it can do anything the user can. SSSF's commit phases may commit its changes, so `git status` alone may not show them.

What this change stops: an agent's change to project code or settings changing what the engineer installs as root, **without the engineer seeing it**. Project code can't run at install time. Data changes show in the printed diff, such as:
- a new `network_allow` host
- a `deny_exec` removal
- a `data_dir` that widens `sssf-sessions`

What it can't stop at its level:
- The engineer running the project's copy of the script instead of the hub's. The script refuses when its own path is inside `--repo`, but by then that refusal is project code.
- An agent outside the wrap changing the hub itself, `~/.config/uv`, `PATH` folders, shell rc files, or a `uv.toml` in a folder above the hub. The hub and the engineer's environment are trusted.
- An agent still running between the diff and the sudo lines. It can swap the temp files in `/tmp`. This hole is the baseline's, and the printed "stop every agent" warning stays.
- The engineer approving a diff without reading it.

## Bar

Must:
1. **The install runs from the hub.**
   - It imports only hub modules, from the hub's `templates/adws/adw_modules`.
   - It runs no program that a project file or setting names.
   - It runs no git with the project as its working folder or `-C` target. It runs git only in the hub's skill folder, to read the hub's version.
2. **It reads only the project's data files.** Those are `repo.yaml` and `sssf.config.yaml` (`yaml.safe_load` through `repo_settings.load` / `agents.load_config`) and `stamp.json` (`json.loads`). Beyond that it only checks the filesystem (`is_dir`, `is_symlink`, `lexists`, `resolve`) on `<repo>/.git` and on the paths the policy names.
3. **The diff shows every change.**
   - For a file that is installed, every line of the new file that differs from the installed file appears in the printed unified diff.
   - For a file that isn't installed, or can't be read as UTF-8, every line of the new file is printed.
   - The listing comes from the same in-memory string that was written, never from re-reading the temp file.
   - Characters outside printable ASCII are shown escaped, so a terminal escape, a `\r` or a bidi character can't hide a line.
   - "Unchanged" means the installed bytes equal the new text's UTF-8 bytes exactly, the same comparison `check_machine` makes.
4. **It refuses on a version mismatch:** when the project's recorded skill version differs from the hub's, or either is dirty or unknown.
5. **A run's refusal says what to check.** When the installed config or a policy differs from what the project renders, the refusal gives `git status adws/` and `git diff adws/`, says to discard changes the engineer didn't make, and points at the hub install.
6. **`adw_agentsh_policy.py` leaves the skill's templates.** `install.py --update` deletes a file that left the skill only when the file is SSSF code (`is_sssf_code`), is a regular file, and still matches its stamp hash.
7. **Every run of the install that exits 0 prints `sudo systemctl restart agentsh`,** "Nothing to install" included. The baseline always printed it, and `check_machine` reads the files, not what the running service loaded.

Must never, where the baseline does otherwise:
- keep an agent file
- run agent code
- delete or overwrite an engineer file

`--update` never deletes a file the repo changed, a file that isn't SSSF code, or a path that isn't a regular file. A link at the path is unlinked; its target is never touched.

## Design

### Finding the main checkout and the git common dir without git

- `root = Path(args.repo).resolve()`.
- `git_dir = root / ".git"`. Refuse unless `git_dir.is_dir()` and `not git_dir.is_symlink()`. A `.git` *file* means a linked worktree or a submodule. The message says to pass the main checkout.
- Refuse if `os.path.lexists(git_dir / "commondir")`. That file makes git use another common dir, so this is not a plain main checkout.
- The common dir is `str(git_dir)` and the main checkout is `root`. For a non-bare main checkout these equal what `read()` gets at run time: `git rev-parse --path-format=absolute --git-common-dir`, with its parent resolved. If an agent changes either, the run's policy text changes and the run refuses, so the check fails closed.

### Keeping uv and Python off the project

- The documented command is `uv run --directory <hub> …`. With it, uv looks for `pyproject.toml`, `uv.toml` and `.python-version` from the hub, never from the project.
- The script has a PEP 723 header, `dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]` (the old script's list), so uv runs it as a script, not as a project.
- Python puts the script's own folder, the hub's `scripts/`, at `sys.path[0]`, not the working folder. Before it imports `adw_modules`, the script inserts the hub's `Path(__file__).resolve().parent.parent / "templates" / "adws"` at `sys.path[0]`.
- It never `chdir`s into the project and never adds a project path to `sys.path`.
- The script refuses (exit 1) when `Path(__file__).resolve()` is inside `root`, with: "run the hub's copy: uv run --directory <hub> …". As a result the hub can't install for itself. See Out of scope.

### The hub's version

`hub_version() -> tuple[str, bool]` in the script runs two commands in the skill folder (`Path(__file__).resolve().parent.parent`):
- `git -C <skill dir> log -1 --format=%H -- .`
- `git -C <skill dir> status --porcelain -- .`

It returns `(full sha, dirty)`. This mirrors `install.py`'s `skill_version`, but uses the full sha.

The project's version is `json.loads((root / "adws/adw_sssf_config/stamp.json").read_text())["skill"]`. Refuse (exit 1) when:
- the stamp is missing or unreadable, or `skill` isn't a string
- the stamp's `skill` ends in `+dirty`
- the hub is dirty or has no sha
- the hub's full sha doesn't `startswith` the stamp's short sha

The message names both versions. It says to run `just sssf-update` in the project from this hub, or to check out the project's version in the hub. It also explains why: the project's `agentsh_guard.py` checks the installed files at run time, so it must render the same text.

### One renderer for the run and the install

In `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`, move the body of `read()` that follows its git calls into a pure function:

```python
def guard_for(settings, cfg, root: Path, common: str, refuse_script: str, template: str) -> LocalGuard:
    data_dir = Path(cfg.defaults.data_dir)
    sessions = str((data_dir if data_dir.is_absolute() else root / data_dir).resolve() / "sessions")
    harnesses = sorted({"claude_code" if a.coding_agent == "claude_code" else "pi" for a in cfg.agents})
    policies = {h: Policy(name=(n := policy_name(root, h)), text=policy_text(
        settings, common, h, n, sessions=sessions, refuse_script=refuse_script)) for h in harnesses}
    return LocalGuard(mode="agentsh", policies=policies, config_template=template)
```

`read()` keeps its git calls and reads its template from `Path(__file__).parent / "agentsh_sssf.yaml"`. It passes `refuse_script = str(Path(__file__).resolve().with_name("sssf_refuse.py"))` to `guard_for`. Its behaviour is unchanged.

The install calls `guard_for` with:
- `settings = repo_settings.load(root)`
- `cfg = agents.load_config(str(root / args.config))`, with no `worktree.anchor`
- `common` from the filesystem checks above
- `refuse_script = str((root / "adws/adw_modules/agentsh_guard.py").resolve().with_name("sssf_refuse.py"))`, which is what the project's `__file__` gives at run time from the main checkout
- `template`: the hub's `templates/adws/adw_modules/agentsh_sssf.yaml`

### Refusal messages

In `agentsh_guard.py`:
- `INSTALL = "uv run --directory <hub> .claude/skills/sssf/scripts/agentsh_install.py --repo <this checkout>"`. Here `<hub>` is the SSSF source checkout on this machine, at this project's skill version.
- `FIX` becomes `` f"install from the SSSF hub: run `{INSTALL}` and the sudo lines it prints, or set `local_guard: text` in adws/adw_sssf_config/repo.yaml" ``.
- A new `DIFFERS`: `` "An agent may have changed adws/. Run `git status adws/` and `git diff adws/` (and `git log --stat -- adws/`, since a run may have committed it), discard any change you didn't make, then " + FIX ``.

In `check_machine`, three refusals use `DIFFERS` instead of `FIX`, and the others keep `FIX`:
- `{CONFIG} differs from what this checkout renders; {DIFFERS}`, which replaces "is not the file SSSF renders"
- `policy … is not in … policies.allowed`
- `installed policy {name} differs from what this checkout renders; {DIFFERS}`, which replaces "is stale"

Nothing in the skill may still name `adw_agentsh_policy.py`.

### The install script: `.claude/skills/sssf/scripts/agentsh_install.py`

`main(argv) -> int`, with `--repo` (required) and `--config` (default `adws/adw_sssf_config/sssf.config.yaml`, relative to `--repo`). In order:

1. Run the self-in-repo refusal, the main-checkout checks and the version check, as above.
2. Call `repo_settings.load(root)`. On `ValidationError`, `yaml.YAMLError`, `ValueError` or `OSError`, print the error and return 1. When `local_guard != "agentsh"`, print the baseline's "nothing to install" line and return 1.
3. Build the guard with `guard_for`. Merge `policies.allowed` with the installed config's list, as the old script did. Render with `agentsh_guard.render_config`.
4. Make a temp folder with `out = tempfile.mkdtemp(prefix="sssf-agentsh-")`. For the config and for each policy, keep `(new_text, installed_path, temp_path)` and write `new_text` to `temp_path`.
5. Read each installed file as `Path(...).read_bytes()`. `OSError` means not installed.
   - **Unchanged:** the bytes equal `new_text.encode()`. Print `unchanged: <installed_path>` and add no sudo line.
   - **Changed:** the bytes decode as UTF-8 (`bytes.decode("utf-8")`, with no newline translation, so an installed `\r` stays visible). Print `difflib.unified_diff(old.split("\n"), new.split("\n"), f"{installed} (installed)", f"{temp} (new)", lineterm="")`.
   - **Not installed or not UTF-8:** print `<installed_path>: not installed; the whole new file:`, or `…: installed file is not UTF-8; the whole new file:`, then every line of `new_text`.
   - Split on `"\n"` only. `str.splitlines` would swallow ` ` and similar separators.
   - Pass every printed line through `shown(line)`. It keeps characters where `" " <= c <= "~"` and writes any other character as `\\x..`, `\\u....` or `\\U........`. The first time it escapes something, print once: `note: characters outside printable ASCII are shown escaped; the file holds them as is.`
6. Print one line saying what the files were rendered from: the hub skill sha, `<root>/adws/adw_sssf_config/repo.yaml` and the config path.
7. Print the output for the engineer:
   - **Some files changed:** print the baseline's stop-every-agent warning. Then print a `sudo install -o root -g root -m 0644 <temp> <installed>` line (`shlex.join`) for each changed file, then `sudo systemctl restart agentsh`.
   - **Nothing changed:** print `Nothing to install: /etc/agentsh matches what this project renders. If the service hasn't been restarted since the last install, run:`, then `sudo systemctl restart agentsh`. A run checks the files, not what the running service loaded.
   - Return 0 in both cases.

The module docstring gives the usage, the run command with `--directory`, and the rule that the script reads only data. The script ends with `if __name__ == "__main__": sys.exit(main(sys.argv[1:]))`.

### `install.py --update` removes retired SSSF code

In `.claude/skills/sssf/scripts/install.py` `update()`, compute `gone` before `save_stamp`. For each `rel` in `gone`, with `path = root / rel`:
- **Not SSSF code** (`is_sssf_code(rel)` is false): leave it, as now, under "no longer in the skill (left in place)".
- **SSSF code, and `os.path.lexists(path)` is false:** drop its record.
- **SSSF code, and `path.is_file()` is false** (a folder, a link to a folder, a FIFO or a broken link): leave it and list it under "left in place". Don't hash it.
- **SSSF code, and `sha256(path) == files[rel]["sha256"]`:** call `path.unlink()`. That removes a link, never its target. Drop the record and list `- rel` under `removed: N (SSSF code the skill no longer ships)`.
- **Otherwise:** leave it, under "left in place".

Save the stamp after this step.

## Files to change

- `.claude/skills/sssf/scripts/agentsh_install.py`: new, as above.
- `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`:
  - add `guard_for`, and have `read()` call it
  - add `INSTALL`, `FIX` and `DIFFERS`
  - change the three `check_machine` messages
  - update the module docstring's mention of the install, if it has one
- `.claude/skills/sssf/templates/adws/adw_agentsh_policy.py`: delete it.
- `.claude/skills/sssf/scripts/install.py`:
  - add the removal in `update()`
  - add a docstring line: `--update` removes SSSF code the skill dropped, if the repo never changed it
- `.claude/skills/sssf/templates/repo.yaml`: the `local_guard` comment (line 87) names the hub install command instead of `adw_agentsh_policy.py`.
- `.claude/skills/sssf/references/config.md`, section "Running local agents under agentsh":
  - **Install step 1:** the new command.
    - The hub must be a clean checkout of the SSSF source on this machine, at the skill version in the project's `stamp.json`.
    - The script reads only `repo.yaml`, `sssf.config.yaml` and `stamp.json` from the project, runs nothing from it, and shows a diff against `/etc/agentsh/` before the sudo lines.
    - Read the diff. A new host or a missing `deny_exec` there is a change to look into.
    - It always prints the restart line, "Nothing to install" included.
  - **Step 2:** keep the stop-every-agent warning.
  - **The refusal paragraph:** a config or policy that differs now names `git status adws/` and `git diff adws/` first.
  - **The `agentsh_sssf.yaml` limit (line 482):** replace it with what's built. An agent's edit to the project's `agentsh_sssf.yaml` or other `adws/` code never reaches the install, which renders from the hub. The next run refuses with the "differs" message. Discard the change, and don't reinstall.
  - **Line 483:** drop "An empty template gives a traceback from `adw_agentsh_policy.py`", and keep the UTF-8 case at run time.
  - **The remaining limits** keep "a service still running on an older config (the check reads the file, and only the printed restart loads it)", and add:
    - the temp-file window
    - the diff only helps if it's read
    - the hub can't yet install its own policy
- `.claude/skills/sssf/cookbooks/install.md`, the `--update` table under "Update an installed repo": add the row "is SSSF code the skill no longer ships, and still matches its recorded hash: removes it (`-`, under `removed`); changed, or not a regular file: leaves it and lists it".
- `.claude/skills/sssf/tests/test_agentsh_guard.py`: the tests below. They replace `test_install_script_writes_nothing_under_the_repo_and_agrees_with_the_check`.
- `.claude/skills/sssf/tests/test_update.py`: the removal tests below.

## Tests

Every guard test builds its config from the shipped `.claude/skills/sssf/templates/sssf.config.yaml` (`SHIPPED`), never a test-only roster.

- Load the script in-process with `importlib.util.spec_from_file_location("agentsh_install", SKILL / "scripts/agentsh_install.py")`.
- Monkeypatch its `hub_version` to return `("abc1234" + 33*"0", False)`, so the suite doesn't depend on whether the hub is dirty.
- Use the `machine` fixture's patched `agentsh_guard.CONFIG` and `POLICY_DIR`. Patch them on the `adw_modules.agentsh_guard` the script imports. It's the same module object, since both use `templates/adws`.

A helper, `fake_project(tmp_path)`, builds the project, then `chdir`s to a folder outside it:
- runs `git init`
- copies `adws/adw_modules/agentsh_sssf.yaml` from the template
- writes an `adws/adw_modules/agentsh_guard.py` that writes a marker file if it is ever imported
- writes an empty `adws/adw_modules/sssf_refuse.py`
- writes `adws/adw_sssf_config/repo.yaml` with `local_guard: agentsh`
- copies `adws/adw_sssf_config/sssf.config.yaml` from `SHIPPED`
- writes `adws/adw_sssf_config/stamp.json` as `{"files": {}, "skill": "abc1234"}`
- plants `pyproject.toml`, `uv.toml` and `.python-version` with nonsense
- plants a root `yaml.py` and `sitecustomize.py` that also write the marker

1. **Agrees with the run.**
   - Run `main(["--repo", str(proj)])` on an empty install, and again with another repo's names already installed. Copy each printed `sudo install` source to its target.
   - Then `monkeypatch.setattr(agentsh_guard, "__file__", str(proj/"adws/adw_modules/agentsh_guard.py"))` and `chdir(proj)`.
   - `agentsh_guard.check_machine(agentsh_guard.read(agents.load_config(str(SHIPPED))))` passes.
   - The other repo's names survive, the project's `git status --porcelain` is unchanged, and no marker exists.
   - Every run's output ends with `sudo systemctl restart agentsh`.
2. **Runs nothing from the project.**
   - Wrap `subprocess.run` and `subprocess.Popen` with recorders, then call `main`.
   - No recorded call has a `cwd` inside the project or a `-C` inside it. With `hub_version` patched, there are no calls at all.
   - No marker exists, and no `sys.path` entry is inside the project.
   - `sys.modules["adw_modules.agentsh_guard"].__file__` is the hub template's.
3. **A `fail_open` in the project never reaches the install.**
   - Set `on_attach_failure: fail_open` in the project's `agentsh_sssf.yaml`, run `main` and install.
   - The output has no `fail_open`, and neither does the installed config.
   - The run's `read` plus `check_machine` (with `__file__` patched as in test 1) raises `AgentshRefused` matching `differs`, `git status adws/`, `git diff adws/` and `agentsh_install.py`.
4. **A repo.yaml change shows.**
   - Install once, then add `network_allow: [evil.example.com]` to `repo.yaml` and run `main` again.
   - The diff has `+` lines with `evil.example.com`, and there is a sudo line for that policy.
   - A rerun with no change prints `Nothing to install`, no `sudo install` line, and the `sudo systemctl restart agentsh` line.
5. **An installed `\r` is not "unchanged".**
   - Install, then rewrite one installed policy with `\r\n` line endings.
   - `main` prints a diff for it in which `\x0d` shows escaped, and prints a sudo line for it.
   - `check_machine` refuses the installed file.
6. **Hidden characters are shown.**
   - Give a `deny_exec` reason with `"\x1b[2K\r"` and `" "`, using YAML escapes.
   - The output has no raw `\x1b`, `\r` or ` `, and shows the escapes and the note.
7. **Refusals.** Each of these returns 1 and prints no `sudo` line:
   - the stamp's `skill` differs from the hub's
   - the stamp's `skill` ends in `+dirty`
   - the hub is dirty
   - there is no stamp
   - `.git` is a file
   - `.git` is a symlink to a real `.git`
   - `.git/commondir` exists
   - `local_guard: text`
   - the script's path is inside `--repo` (patch the module's `__file__` to a path under `proj`)
8. **Run messages.**
   - Move the existing assertions at `refused("not the file SSSF renders")` and `match="stale"` to the new wording.
   - Add one test asserting that `DIFFERS`'s `git status adws/` appears in the not-in-allowed case.
   - Assert that `FIX` contains `agentsh_install.py` and `--directory`.
9. **Nothing names the old script.** No file under `.claude/skills/sssf/`, outside `tests/`, contains `adw_agentsh_policy`, and `templates/adws/adw_agentsh_policy.py` doesn't exist.

`test_update.py`, using the `repo` fixture:
- **Unchanged retired code is removed.** Add a record for `adws/adw_retired.py`, with source `adws/adw_retired.py` and the sha of a file written there, then run `--update`. The file and its record are gone, and the output lists the file under `removed`.
- **Changed retired code stays.** The same, with the file changed after the record. The file stays, and is listed under "left in place".
- **Retired non-code stays.** A record for a non-code file, such as `adws/adw_data/prompt_engineering/old/user.md`. The file stays.
- **A link is unlinked, never its target.** A retired code path that is a symlink to a file outside the repo with matching content. The link is removed, and the target still exists.
- **A link to a folder stays.** A retired code path that is a symlink to a folder. It stays, and `--update` exits 0.

## Verification

- The repo's test command passes with the new tests. It runs `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`.
- `test ! -e .claude/skills/sssf/templates/adws/adw_agentsh_policy.py` succeeds.
- `grep -rn adw_agentsh_policy .claude/skills/sssf --exclude-dir=tests` finds nothing.
- Optional, by hand: on a machine with the hub and an installed project at the same skill version, run `uv run --directory <hub> .claude/skills/sssf/scripts/agentsh_install.py --repo <project>`. It prints diffs or `Nothing to install`, ends with the restart line, and runs no sudo.

## Docs this change makes out of date

- `.claude/skills/sssf/references/config.md`: the install, refusal and limits text above.
- `.claude/skills/sssf/templates/repo.yaml`: the `local_guard` comment.
- `.claude/skills/sssf/cookbooks/install.md`: the `--update` table.
- `install.py`'s docstring, and the new script's docstring.
- No README or CHANGELOG names the old script.
- For the documenter:
  - `docs/designs/agentsh-local-sandbox.md` names `uv run adws/adw_agentsh_policy.py` in its earlier records, which are history. Its "Next" item 4 is this run.
  - Any `app_docs/` write-up that says to run `adw_agentsh_policy.py` now describes old behaviour.

## Out of scope

These holes stay open. A reviewer should file them as old holes:
- **Parts of the work not in this change:** the policy rules themselves, pi under the wrap, a real agent under the service, sudo, and turning this on outside sssf-sandbox.
- **The hub's own copy under `adws/`,** including `adws/adw_agentsh_policy.py` and `adws/adw_modules/agentsh_guard.py`. They are protected, and the hub's next self-update takes them.
- **The hub can't install a policy for itself.** The self-in-repo refusal blocks `--repo <hub>`. The hub isn't on agentsh yet: that's rollout step 3, which needs its own install path.
- **The temp-file window.** Between the printed lines and the engineer's sudo, a running agent can swap the `/tmp` files. The hole is the baseline's, and the warning stays.
- **A service still running on an older config.** `check_machine` reads the files, and only the restart loads them. The install now prints the restart line on every successful run, as the baseline did, but whether it was run stays the engineer's step.
- **Run-time `read()` still runs git in the project** at session start, as at the baseline. At worst its result differs from the install's, and then the run refuses.
- **The engineer's environment and the hub are trusted:** `UV_*`, `PYTHONPATH`, `~/.config/uv/uv.toml`, a `uv.toml` above the hub, and the hub's own integrity.
- **`stamp.json` is agent-editable.** The version check only catches an honest mismatch. A project whose real code differs fails at run time with the "differs" refusal.
- **A forged `stamp.json` record can make `--update` delete an engineer's file.** A record that lists an engineer's `adws/adw_*.py` or `adws/adw_modules/*` file, with a source the skill doesn't ship and the file's current hash, makes `--update` delete it. An agent that can write `stamp.json` can delete that file directly, and git still holds a committed copy.
- **Denial of service:** a project file that is a FIFO, or a huge or alias-bomb YAML, can hang or slow the install. That isn't a bypass.
- **The engineer's own choices:** approving a diff without reading it, or running the project's copy of the script.

## For the engineer

- After this merges, update the hub's own install by running `uv run .claude/skills/sssf/scripts/install.py --update` in the hub. That removes the hub's `adws/adw_agentsh_policy.py` and takes the new `agentsh_guard.py` under `adws/adw_modules/`, both protected here. Then run `just sssf-update` in sssf-sandbox and reinstall with the new command.
- Record the run in `docs/designs/agentsh-local-sandbox.md`, as for earlier runs.
