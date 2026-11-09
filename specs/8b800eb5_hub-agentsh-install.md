# Install the agentsh policy from the hub, not from the project

## Summary

Replace the project-side `adws/adw_agentsh_policy.py` with a hub-side script, `.claude/skills/sssf/scripts/agentsh_install.py`, run as:

```
uv run --directory <hub> .claude/skills/sssf/scripts/agentsh_install.py --repo <project main checkout>
```

It renders the server config and policies with the **hub's** `agentsh_guard` code and the **hub's** `agentsh_sssf.yaml`. From the project it reads only data: `adws/adw_sssf_config/repo.yaml` and `sssf.config.yaml` with `yaml.safe_load`, and `stamp.json`'s `skill` with `json`. It runs no git in the project and imports nothing from it. It refuses when the project's skill version differs from the hub's. Before the sudo lines, it prints a diff of each new file against `/etc/agentsh/`, or the whole file when nothing is installed. A run's "differs" refusal now sends the engineer to `git status` / `git diff` on `adws/` first, then to the hub install. `install.py --update` removes SSSF code that left the skill, as long as the repo never changed it. This removes `adw_agentsh_policy.py` from projects.

Do not reuse the e027505a approach: no provenance check, no commit trailers, no list of git or Python settings to override.

## Baseline

`main` at `94af2eb` ("Record the install-check run and write the hub-install task"). Every finding is judged against it. At the baseline:
- `adws/adw_agentsh_policy.py`, in the project, imports the project's `adw_modules`, runs git in the project (`worktree.main_checkout()`), renders with the project's `agentsh_sssf.yaml`, and prints sudo lines with no diff.
- `agentsh_guard.FIX` points every refusal straight at that script.
- `install.py --update` leaves a file that left the skill in place and lists it under "no longer in the skill".

## Threat model

An agent runs as the engineer's own Unix user. Under `local_guard: agentsh` it can read and write the project workspace, including `adws/`, `repo.yaml`, `sssf.config.yaml`, `stamp.json`, the project's skill copy, the `.git` data except `config` and `hooks`, and `/tmp`. Under `local_guard: text`, or outside the wrap, it can do anything the user can. SSSF's commit phases may commit its changes, so `git status` alone may not show them.

What this change stops: an agent's change to project code or settings changing what the engineer installs as root **without the engineer seeing it**. Project code can't run at install time. Data changes, such as a new `network_allow` host, a `deny_exec` removal or a `data_dir` that widens `sssf-sessions`, appear in the printed diff.

What it can't stop at its level:
- The engineer running the project's copy of the script instead of the hub's. The script refuses when its own path is inside `--repo`, but that refusal is project code by then.
- An agent outside the wrap changing the hub itself, `~/.config/uv`, `PATH` directories, shell rc files, or a `uv.toml` in a folder above the hub. The hub and the engineer's environment are trusted.
- An agent still running between the diff and the sudo lines. It can swap the temp files in `/tmp`. This is the baseline's hole, and the printed "stop every agent" warning stays.
- The engineer approving a diff without reading it.

## Bar

Must:
1. The install runs from the hub. It imports only hub modules: the hub's `templates/adws/adw_modules`. It runs no program named by a project file or setting, and runs no git with the project as its working directory or `-C` target. It runs git only in the hub's skill folder, to read the hub's version.
2. It reads only `repo.yaml`, `sssf.config.yaml` (`yaml.safe_load` via `repo_settings.load` / `agents.load_config`) and `stamp.json` (`json.loads`). It also does filesystem checks (`is_dir`, `is_symlink`, `exists`, `resolve`) on `<repo>/.git` and on the paths the policy names.
3. Every byte that reaches a file named in a printed `sudo install` line appears in the printed diff or whole-file listing. The listing comes from the same in-memory string that was written, never from re-reading the temp file. Characters outside printable ASCII are shown escaped, so a terminal escape, `\r` or bidi character can't hide a line.
4. It refuses when the project's recorded skill version differs from the hub's, or either is dirty or unknown.
5. A run's refusal for a config or policy that differs from what the project renders gives `git status adws/` and `git diff adws/`, says to discard changes the engineer didn't make, and points at the hub install.
6. `adw_agentsh_policy.py` leaves the skill's templates. `install.py --update` deletes a file that left the skill only when it's SSSF code (`is_sssf_code`) and still matches its stamp hash.

Must never, where the baseline does otherwise: keep an agent file, run agent code, or delete or overwrite an engineer file. `--update` never deletes a file the repo changed, or a file that isn't SSSF code. A link at the path is unlinked, never its target.

## Design

### Finding the main checkout and the git common dir without git

- `root = Path(args.repo).resolve()`.
- `git_dir = root / ".git"`. Refuse unless `git_dir.is_dir()` and `not git_dir.is_symlink()`. A `.git` *file* means a linked worktree or submodule, so the message says to pass the main checkout.
- Refuse if `os.path.lexists(git_dir / "commondir")`. That file would make git use another common dir, so this is not a plain main checkout.
- The common dir is `str(git_dir)`, and the main checkout is `root`. For a non-bare main checkout these equal what `read()` gets at run time from `git rev-parse --path-format=absolute --git-common-dir`, its parent resolved. If an agent changes either, the run's policy text changes and the run refuses. That fails closed.

### Keeping uv and Python off the project

- The documented command is `uv run --directory <hub> …`. uv then looks for `pyproject.toml`, `uv.toml` and `.python-version` from the hub, never the project. The script has a PEP 723 header (`dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]`, the old script's list), so uv runs it as a script, not as a project.
- Python puts the script's own folder (the hub's `scripts/`) on `sys.path[0]`, not the cwd. The script inserts `Path(__file__).resolve().parent.parent / "templates" / "adws"` (the hub's) at `sys.path[0]` before importing `adw_modules`. It never `chdir`s into the project and never adds a project path to `sys.path`.
- The script refuses, with exit 1, when `Path(__file__).resolve()` is inside `root`: "run the hub's copy: uv run --directory <hub> …".

### The hub's version

`hub_version() -> tuple[str, bool]` in the script runs `git -C <skill dir> log -1 --format=%H -- .` and `git -C <skill dir> status --porcelain -- .` (skill dir = `Path(__file__).resolve().parent.parent`). It returns the full sha and whether the skill folder is dirty. This mirrors `install.py`'s `skill_version`, but uses the full sha.

The project's version is `json.loads((root / "adws/adw_sssf_config/stamp.json").read_text())["skill"]`. Refuse (exit 1) when any of these holds:
- the stamp is missing or unreadable, or `skill` isn't a string
- the stamp's `skill` ends in `+dirty`
- the hub is dirty, or has no sha
- the hub's full sha doesn't `startswith` the stamp's short sha

The message names both versions. It says to run `just sssf-update` in the project from this hub, or to check out the project's version in the hub. It also says that the project's `agentsh_guard.py` checks the installed files at run time and must render the same text.

### One renderer for the run and the install

In `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`, move the body of `read()` after its git calls into a pure function:

```python
def guard_for(settings, cfg, root: Path, common: str, refuse_script: str, template: str) -> LocalGuard:
    data_dir = Path(cfg.defaults.data_dir)
    sessions = str((data_dir if data_dir.is_absolute() else root / data_dir).resolve() / "sessions")
    harnesses = sorted({"claude_code" if a.coding_agent == "claude_code" else "pi" for a in cfg.agents})
    policies = {h: Policy(name=(n := policy_name(root, h)), text=policy_text(
        settings, common, h, n, sessions=sessions, refuse_script=refuse_script)) for h in harnesses}
    return LocalGuard(mode="agentsh", policies=policies, config_template=template)
```

`read()` keeps its git calls and its template read from `Path(__file__).parent / "agentsh_sssf.yaml"`. It passes `refuse_script = str(Path(__file__).resolve().with_name("sssf_refuse.py"))` and calls `guard_for`. Its behaviour doesn't change.

The install passes:
- `settings = repo_settings.load(root)`
- `cfg = agents.load_config(str(root / args.config))`, with no `worktree.anchor`
- `common` as above
- `refuse_script = str((root / "adws/adw_modules/agentsh_guard.py").resolve().with_name("sssf_refuse.py"))`, which is what the project's `__file__` gives at run time from the main checkout
- `template` = the hub's `templates/adws/adw_modules/agentsh_sssf.yaml`

### Refusal messages

In `agentsh_guard.py`:

- `INSTALL = "uv run --directory <hub> .claude/skills/sssf/scripts/agentsh_install.py --repo <this checkout>"`, where `<hub>` is the SSSF source checkout on this machine, at this project's skill version.
- `FIX` becomes: `f"install from the SSSF hub: run `{INSTALL}` and the sudo lines it prints, or set `local_guard: text` in adws/adw_sssf_config/repo.yaml"`.
- A new `DIFFERS`: `"An agent may have changed adws/. Run `git status adws/` and `git diff adws/` (and `git log --stat -- adws/`, since a run may have committed it), discard any change you didn't make, then " + FIX`.
- In `check_machine`, these three refusals use `DIFFERS` instead of `FIX`:
  - `{CONFIG} is not the file SSSF renders` becomes `{CONFIG} differs from what this checkout renders; {DIFFERS}`
  - `policy … is not in … policies.allowed`
  - `installed policy … is stale` becomes `installed policy {name} differs from what this checkout renders; {DIFFERS}`

  Keep the words "differs" in both. The others keep `FIX`.
- Nothing in the skill may still name `adw_agentsh_policy.py`.

### The install script: `.claude/skills/sssf/scripts/agentsh_install.py`

`main(argv) -> int`. Arguments: `--repo` (required) and `--config` (default `adws/adw_sssf_config/sssf.config.yaml`, relative to `--repo`). The steps, in order:

1. The self-in-repo refusal, the main-checkout checks and the version check, as above.
2. `repo_settings.load(root)`: on `ValidationError`, `yaml.YAMLError`, `ValueError` or `OSError`, print the error and return 1. If `local_guard != "agentsh"`, print the baseline's "nothing to install" line and return 1.
3. Build the guard with `guard_for`. Merge `policies.allowed` with the installed config's list, as the old script did. Render with `agentsh_guard.render_config`.
4. `out = tempfile.mkdtemp(prefix="sssf-agentsh-")`. For the config and each policy, keep `(new_text, installed_path, temp_path)`, and write `new_text` to `temp_path`.
5. Read each installed file with `Path(...).read_text()`. `OSError` or `UnicodeDecodeError` counts as none.
   - When it equals `new_text`, print `unchanged: <installed_path>`, with no sudo line.
   - Otherwise, when installed text exists, print `difflib.unified_diff(old.split("\n"), new.split("\n"), f"{installed} (installed)", f"{temp} (new)", lineterm="")`.
   - Otherwise, print `<installed_path>: not installed; the whole new file:` and every line of `new_text`.

   Split on `"\n"` only: `str.splitlines` would eat ` ` and friends. Pass every printed line through `shown(line)`, which keeps `" " <= c <= "~"` and writes anything else as `\\x..` / `\\u....` / `\\U........`. When any escape was made, print once: `note: characters outside printable ASCII are shown escaped; the file holds them as is.`
6. Print the baseline's stop-every-agent warning, then a `sudo install -o root -g root -m 0644 <temp> <installed>` line (via `shlex.join`) for each changed file, then `sudo systemctl restart agentsh`. When nothing changed, print `Nothing to install: /etc/agentsh matches what this project renders.` and return 0.
7. Print one line saying what the files were rendered from: the hub skill sha, plus `<root>/adws/adw_sssf_config/repo.yaml` and the config path.

The module docstring gives usage, the run command with `--directory`, and the "reads only data" rule. `if __name__ == "__main__": sys.exit(main(sys.argv[1:]))`.

### `install.py --update` removes retired SSSF code

In `.claude/skills/sssf/scripts/install.py` `update()`, compute `gone` before `save_stamp`. For each `rel` in `gone`:
- If `is_sssf_code(rel)` and the path (`root / rel`) is missing, drop the record.
- Else, if `is_sssf_code(rel)` and `sha256(path) == files[rel]["sha256"]`, call `path.unlink()`, which removes a link and never its target. Then drop the record and list it under `removed: N (SSSF code the skill no longer ships)` as `- rel`.
- Otherwise, leave it, as now, under "no longer in the skill (left in place)".

Save the stamp after this. A changed file is never deleted.

## Files to change

- `.claude/skills/sssf/scripts/agentsh_install.py`: new, as above.
- `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`:
  - `guard_for`, and `read()` calling it
  - `INSTALL`, `FIX` and `DIFFERS`
  - the three `check_machine` messages
  - the module docstring's mention of the install, if any
- `.claude/skills/sssf/templates/adws/adw_agentsh_policy.py`: delete it.
- `.claude/skills/sssf/scripts/install.py`: the removal in `update()`, and a docstring line saying `--update` removes SSSF code the skill dropped when the repo never changed it.
- `.claude/skills/sssf/templates/repo.yaml`: line 87's comment names the hub install command instead of `adw_agentsh_policy.py`.
- `.claude/skills/sssf/references/config.md`, section "Running local agents under agentsh":
  - Install step 1: the new command. The hub must be a checkout of the SSSF source on this machine, clean, at the skill version in the project's `stamp.json`. The script reads only `repo.yaml`, `sssf.config.yaml` and `stamp.json` from the project, runs nothing from it, and shows a diff against `/etc/agentsh/` before the sudo lines. Read it. A new host or a missing `deny_exec` there is a change to look into.
  - Step 2: keep the stop-every-agent warning.
  - The refusal paragraph: a config or policy that differs now names `git status adws/` / `git diff adws/` first.
  - Replace the `agentsh_sssf.yaml` limit (line 482) with what's built. An agent's edit to the project's `agentsh_sssf.yaml` or other `adws/` code never reaches the install, which renders from the hub. The next run refuses with the "differs" message. Discard the change, and don't reinstall.
  - Line 483: drop "An empty template gives a traceback from `adw_agentsh_policy.py`" and keep the UTF-8 run-time case.
  - Add the limits that remain: the temp-file window, and that the diff only helps if read.
- `.claude/skills/sssf/cookbooks/install.md`, section "Update an installed repo": add a table row: "is SSSF code the skill no longer ships, and still matches its recorded hash: removes it (`-`, under `removed`); changed: leaves it and lists it".
- `.claude/skills/sssf/tests/test_agentsh_guard.py`: tests below. Replace `test_install_script_writes_nothing_under_the_repo_and_agrees_with_the_check`.
- `.claude/skills/sssf/tests/test_update.py`: the removal tests below.

## Tests

All guard tests build config from the shipped `.claude/skills/sssf/templates/sssf.config.yaml` (`SHIPPED`), never a test-only roster. Load the script in-process with `importlib.util.spec_from_file_location("agentsh_install", SKILL / "scripts/agentsh_install.py")`. Monkeypatch its `hub_version` to return a fixed `("abc1234" + 33*"0", False)`, so the suite doesn't depend on the hub's dirty state. Use the `machine` fixture's patched `agentsh_guard.CONFIG` / `POLICY_DIR`: patch them on the `adw_modules.agentsh_guard` the script imports, which is the same module object since both use `templates/adws`.

A helper `fake_project(tmp_path)` builds the project:
- `git init`
- `adws/adw_modules/agentsh_sssf.yaml` copied from the template
- an `adws/adw_modules/agentsh_guard.py` that, if imported, writes a marker file
- `adws/adw_modules/sssf_refuse.py` (empty)
- `adws/adw_sssf_config/repo.yaml` with `local_guard: agentsh`
- `adws/adw_sssf_config/sssf.config.yaml` copied from `SHIPPED`
- `adws/adw_sssf_config/stamp.json` = `{"files": {}, "skill": "abc1234"}`
- planted `pyproject.toml`, `uv.toml` and `.python-version` with nonsense
- a `yaml.py` and a `sitecustomize.py` at the root that write the marker

`chdir` to a folder outside the project.

1. **Agrees with the run.** Run `main(["--repo", str(proj)])` against an empty install, then against one where another repo's names are installed. Copy each printed `sudo install` source to its target. Then `monkeypatch.setattr(agentsh_guard, "__file__", str(proj/"adws/adw_modules/agentsh_guard.py"))`, `chdir(proj)`, and `agentsh_guard.check_machine(agentsh_guard.read(agents.load_config(str(SHIPPED))))` passes. The other repo's names survive, the project's `git status --porcelain` is unchanged, and no marker exists.
2. **Runs nothing from the project.** Wrap `subprocess.run` and `subprocess.Popen` with recorders. After `main`, no recorded call has a `cwd` inside the project or a `-C` argument inside it, and with `hub_version` patched there are none at all. No marker file exists. No `sys.path` entry is inside the project. `sys.modules["adw_modules.agentsh_guard"].__file__` is the hub template's.
3. **fail_open in the project never reaches the install.** Set `on_attach_failure: fail_open` in the project's `agentsh_sssf.yaml`, run `main` and install the files. The printed output has no `fail_open`, and the installed config has none. The run's `read`+`check_machine`, with `__file__` patched as in test 1, raises `AgentshRefused` matching `differs`, `git status adws/`, `git diff adws/` and `agentsh_install.py`.
4. **A repo.yaml change shows in the diff.** Install once, then add `network_allow: [evil.example.com]` to `repo.yaml` and run `main` again. The diff holds `+` lines with `evil.example.com`, and a sudo line exists for that policy. A rerun with no changes prints `Nothing to install` and no `sudo` line.
5. **Hidden characters are shown.** Give a `deny_exec` reason with `"\x1b[2K\r"` and `" "` via YAML escapes. The output holds no raw `\x1b`, `\r` or ` `, and the escapes and the note appear.
6. **Refusals.** Each of these returns 1 and prints no `sudo` line:
   - stamp `skill` differs from the hub's
   - stamp `+dirty`
   - hub dirty
   - no stamp
   - `.git` is a file
   - `.git` is a symlink to a real `.git`
   - `.git/commondir` exists
   - `local_guard: text`
   - the script path inside `--repo` (load a copy of the script placed under `proj/.claude/skills/sssf/scripts/`, or patch `__file__`)
7. **Run messages.** Extend the existing tests at `refused("not the file SSSF renders")` and `match="stale"` to the new wording. Add one asserting `DIFFERS`'s `git status adws/` on the not-in-allowed case. Assert `FIX` holds `agentsh_install.py` and `--directory`.
8. **Nothing names the old script.** No file under `.claude/skills/sssf/` except `tests/` contains `adw_agentsh_policy`, and `templates/adws/adw_agentsh_policy.py` doesn't exist.

`test_update.py`, with the `repo` fixture:
- Add a record for `adws/adw_retired.py`, with source `adws/adw_retired.py` and the sha of a file written there. Run `--update`. The file is gone, the record is gone, and the output lists it under `removed`.
- The same with the file changed after the record: it stays, and is listed under "left in place".
- A retired non-code file, such as a record for `adws/adw_data/prompt_engineering/old/user.md`: it stays.
- A retired code path that is a symlink to a file outside the repo with the matching content: the link is removed and the target still exists.

## Verification

- The repo's test command, which runs `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`, passes with the new tests.
- `test ! -e .claude/skills/sssf/templates/adws/adw_agentsh_policy.py`
- `grep -rn adw_agentsh_policy .claude/skills/sssf --exclude-dir=tests` finds nothing.
- By hand, optional: on a machine with the hub and an installed project at the same skill version, `uv run --directory <hub> .claude/skills/sssf/scripts/agentsh_install.py --repo <project>` prints diffs or `Nothing to install`, and runs no sudo.

## Docs this change makes out of date

- `.claude/skills/sssf/references/config.md`: the install, refusal and limits text above.
- `.claude/skills/sssf/templates/repo.yaml`: the comment on `local_guard`.
- `.claude/skills/sssf/cookbooks/install.md`: the `--update` table.
- `install.py`'s docstring, and the new script's own docstring.
- No README or CHANGELOG names the old script. For the documenter: `docs/designs/agentsh-local-sandbox.md` names `uv run adws/adw_agentsh_policy.py` in its earlier records, and those are history. Its "Next" item 4 describes this run. Any `app_docs/` write-up that says to run `adw_agentsh_policy.py` now describes old behaviour, and the documenter should note that.

## Out of scope

Old holes a reviewer should file as such:
- The policy rules themselves, pi under the wrap, a real agent under the service, sudo, and turning this on outside sssf-sandbox.
- The hub's own copy under `adws/`, including `adws/adw_agentsh_policy.py` and `adws/adw_modules/agentsh_guard.py`. They are protected. The hub's next self-update takes them.
- The window between the printed lines and the engineer's sudo, where a running agent can swap the `/tmp` files. This is the baseline's hole, and the warning stays.
- Run-time `read()` still runs git in the project at session start, as at the baseline. Its result can at worst differ from the install's, and then the run refuses.
- The engineer's own environment (`UV_*`, `PYTHONPATH`, `~/.config/uv/uv.toml`, a `uv.toml` in a folder above the hub) and the hub's integrity are trusted.
- `stamp.json` can be edited by an agent. The version check only catches an honest mismatch. A project whose real code differs fails at run time with the "differs" refusal.
- A project file that is a FIFO, or a huge or alias-bomb YAML, can hang or slow the install. That's denial of service, not a bypass.
- The engineer approving a diff they didn't read, or running the project's copy of the script.

## For the engineer

- After this merges, update the hub's own install (`uv run .claude/skills/sssf/scripts/install.py --update` in the hub). That removes the hub's `adws/adw_agentsh_policy.py` and takes the new `agentsh_guard.py` under `adws/adw_modules/`, both protected here. Then run `just sssf-update` in sssf-sandbox and reinstall with the new command.
- Record the run in `docs/designs/agentsh-local-sandbox.md`, as for earlier runs.
