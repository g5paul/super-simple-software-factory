# Close the pin check's holes and the engine's raw `${PROJECT_ROOT}`

Items 1, 10 and 11 of the Next list in `docs/designs/agentsh-local-sandbox.md`. The hub's probes ran on 2026-11-10, on build 05b3777f.

Revision 2. Item 4 is now a refusal in `start()` alone, and `session_files.py` is no longer touched, as spec review 1 asked. `install.md` and config.md also say that a second restart clears the rare refusal when the start and the install land in the same second.

## Baseline

`main` at `dc44140` ("Write the guard spec for the pin holes and the raw PROJECT_ROOT"). Every finding is judged against it. The hub's `adws/adw_modules/agentsh_guard.py` is byte-identical to the template at that commit.

## Threat model

An agent runs as the engineer's own user, under `agentsh wrap` with SSSF's default-deny policy. On a plain run it can write anything in its workspace, `adws/` included. That means `adws/adw_modules/agentsh_guard.py`, whose constants `BINARY` and `PINNED_SHA256` drive the pin check, and `adws/adw_data/`, where the agent cache lives. The `writes` check puts a change to `adws/adw_modules/` back after each phase, and the running process has already imported the module. So an edit acts only on a later run, and only if it survives.

What this change stops, at SSSF's level:
- a later run that trusts a `BINARY` or `PINNED_SHA256` the agent chose;
- a run against a service that still runs an older binary or config;
- a policy whose `${PROJECT_ROOT}` or `${HOME}` holds glob syntax and so matches sibling folders. The engine expands both as raw text into gobwas/glob;
- SSSF, outside the wrap, following a link an agent put at the agent cache's path.

What it can't stop: an agent can't be kept from editing its own workspace. SSSF can only refuse the *next* launch when the result differs from what the hub installed. The check runs in the checkout's own Python, so an agent that rewrites `check_machine` itself, and survives the `writes` check, still beats it. That is an old hole: config.md's line on `adws/` edits keeps it. A user can't read the service's `/proc/<pid>/exe`, so "the running binary" is inferred from the start time and the file's `st_ctime`, not seen. After SSSF checks the agent cache, an agent of another run could still swap it for a link. SSSF itself does nothing more through that path outside the wrap, and the tools inside the wrap are judged at the file they reach. Hardlinks into the engineer's uv and bun caches, made outside the wrap, are recorded here, not checked.

## Bar

Against the baseline, under the rendered policy and the new checks:

- An edit to the checkout's `PINNED_SHA256` or `BINARY`, or both together, makes `check_machine` refuse. Both together give the "differs" message.
- `check_machine` refuses when `BINARY` or `CONFIG` changed after the service started, or when the start time can't be read. The message gives `sudo systemctl restart agentsh`. It passes on this machine after a restart.
- A root or `$HOME` holding any of `*?[]{}\$` refuses before an agent starts, on a plain run and a worktree run. A root without them works as before.
- With a link or a file at the agent cache's path, the launch refuses. No SSSF code outside the wrap creates, writes or reads through the link. The link and its target are left as they were, and nothing is deleted.
- Every config.md line promises no more than the probes show.
- Never, where the baseline does otherwise: an agent file kept, agent code run, or an engineer file deleted or overwritten. The rendered policies stay byte-identical to the baseline's, and `test_file_rules_equal_the_designs_deny_policy_output` still passes. The rendered *config* changes only by its last comment line.
- Any change to the guard beyond the six items below is a finding.

## Out of scope

These stay open, so a reviewer files them as old holes:
- The hardlink check itself (uv's and bun's trees built outside the wrap share inodes with `~/.cache/uv` and `~/.bun/install/cache`). That is the next run. This run only documents it.
- The fork's code. `ExpandVariables` still doesn't escape. SSSF refuses the paths instead.
- pi's policy. pnpm's `npm_config_*` warning. `/proc/<pid>/cwd` and `cmdline`.
- An agent rewriting `check_machine` or `check_root` itself in a surviving edit (see Threat model).
- A swap of `adws/adw_data` or a folder above it for a link on a plain run, which still moves where SSSF writes (config.md already says so).
- The installed *policies'* timestamps. Only `BINARY` and `CONFIG` are compared with the start time, as asked.
- The text guard's pi pattern itself. Only the doc changes.
- `session_files.py` and how it makes the session subfolders. Unchanged.

## Files to change

All guard code is in the skill template. The hub's own `adws/` is protected and is not touched (see "For the engineer").

- `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`
- `.claude/skills/sssf/tests/test_agentsh_guard.py`
- `.claude/skills/sssf/references/config.md`
- `docs/guide/install.md`

### 1. `render_config` names the pin (`agentsh_guard.py`)

- After `yaml.safe_dump(...)`, append one comment line and return the result:
  `f"# SSSF pins agentsh: {BINARY} sha256 {PINNED_SHA256}\n"`.
  Read `BINARY` and `PINNED_SHA256` as module globals at call time, not as default arguments, so a monkeypatch or an edit to either changes the text.
- Update the docstring: the last line names the pin, so `check_machine`'s byte comparison refuses an edit to either constant as "differs". It's a comment, so agentsh loads the same YAML.
- `check_machine`'s existing `installed != rendered` comparison then does the work. Make no other change there for this item. `.claude/skills/sssf/scripts/agentsh_install.py` calls the same `render_config`, so it renders the same text. Its diff will show the new line once, on the next install. Don't edit the script.

### 2. The service must have started after its binary and config changed (`agentsh_guard.py`)

- Add `RESTART = "sudo systemctl restart agentsh"`.
- Add `_service_started() -> int`. It runs `_run(["systemctl", "show", "agentsh", "-p", "ExecMainStartTimestamp", "--timestamp=unix"])`. It returns the integer from stdout of the form `ExecMainStartTimestamp=@<digits>`, matched with `re.fullmatch(r"ExecMainStartTimestamp=@(\d+)", stdout.strip())`. A non-zero exit, an empty value, `n/a`, or any other shape raises `AgentshRefused`: `"can't read when the agentsh service started; run `{RESTART}`, then start the run again"`.
- At the **end** of `check_machine`, after the policy loop, so a content mismatch still gives its more useful "differs" or FIX message first:
  - `started = _service_started()`
  - For each `path` in `(BINARY, CONFIG)`: `int(os.stat(path).st_ctime) >= started` refuses with `AgentshRefused(f"{path} changed after the agentsh service started, so the service may still run an older build or config; run `{RESTART}` (again, if you just did, since the check counts whole seconds), then start the run again")`. An `OSError` from `os.stat` refuses with the FIX message, like the read above it.
  - Why `>=` on whole seconds: `--timestamp=unix` gives whole seconds. Comparing the truncated ctime with `>=` fails closed. The cost is a rare false refusal when the restart lands in the same second as the install, such as lines pasted as one block, and a second restart clears it. Say so in a one-line comment. Don't parse a locale-formatted `us` timestamp.
- Add a one-line docstring to `check_machine` naming its checks in order.

### 3. No glob syntax in the root or the home folder (`agentsh_guard.py`)

- Add `GLOB_CHARS = "*?[]{}\\$"`, the eight characters `* ? [ ] { } \ $`.
- Add `check_paths(root: str, env=None) -> None`. It checks:
  - `root`;
  - `HOME` from `env` (or `os.environ` when `env` is None), when set and non-empty;
  - `pwd.getpwuid(os.getuid()).pw_dir`. Import `pwd` at module top. It's not known which HOME the engine expands, so check both.

  For the first path holding any of `GLOB_CHARS`, raise `AgentshRefused`. The message names the path, says whether it is the checkout or the home folder, and names the character. It says agentsh puts this path into its rules as raw text, where these characters are glob syntax, so the rules would also match other folders. It says the fix: move the checkout, or use a home folder, without any of `* ? [ ] { } \ $`, or set `local_guard: text` in adws/adw_sssf_config/repo.yaml.
- Call it in two places, where the root is already checked:
  - `check_root(root)`: first thing, `check_paths(root)`. `session.ensure` calls `check_root` with the resolved `run.repo_root`, after `worktree.enter`, so plain runs and worktree runs are both covered. A worktree sits under the main checkout's data folder, so a character in the main checkout's path is in the worktree's path too. Update `check_root`'s docstring, or add one.
  - `start()`: right after the existing `guard.root` / cwd check, `check_paths(guard.root, env)`, before the agent cache is made and before `Popen`.
- Don't change `glob_escape` or any rule text.

### 4. The agent cache refuses a link (`agentsh_guard.py`, `start()` only)

Replace `Path(guard.agent_cache).mkdir(mode=0o700, parents=True, exist_ok=True)   # outside the wrap` with:

```python
cache = os.path.abspath(guard.agent_cache)
os.makedirs(os.path.dirname(cache), exist_ok=True)   # the data root, which already holds sessions/
try:
    os.mkdir(cache, 0o700)      # outside the wrap; mkdir never follows a link at its last component
except FileExistsError:
    pass
except OSError as error:
    raise AgentshRefused(f"can't make the agent cache {cache} ({error}); {FIX}") from None
if not stat.S_ISDIR(os.lstat(cache).st_mode):
    raise AgentshRefused(f"{cache} is a link or a file, not a folder, so no agent is started; an agent "
                         "may have put it there. Look at it with `ls -la`, remove it, and start the run again")
```

- `import stat` at module top. Wrap the `os.lstat` in the same `OSError` → `AgentshRefused` handling.
- Making the parent follows links, as before. That's the documented data-root limit.
- SSSF neither deletes nor replaces what it finds at the path. The engineer looks at it.
- `session_files.py` is not changed.
- No other SSSF code reads or writes under the agent cache. `cache_env` only builds strings. Check with `grep -rn "agent.cache" .claude/skills/sssf/templates/adws/adw_modules/` and report any other site.

### 5. `.claude/skills/sssf/references/config.md`, "Running local agents under agentsh"

Make each edit in the house style: short sentences, no promise beyond the probes.

- **Refusal list (around line 473).**
  - Change "the binary isn't the pinned build;" to: the binary isn't the pinned build, where the pin and the binary's path are named in the rendered config, so an edit to either in the checkout refuses as "differs".
  - Add a bullet: the service started before the binary or the config last changed, or its start time can't be read. The message gives `sudo systemctl restart agentsh`. The check counts whole seconds, so a restart in the same second as the install refuses once, and a second restart clears it.
  - Add a bullet: the checkout's or worktree's path, or your home folder, holds a glob character (below).
  - Add a bullet: a link or a file is at the agent cache's path.
- **Limits that remain (around line 484).** Remove "a service still running on an older config, ...".
- **The `adws/` edit line (around line 525).** Keep it accurate. An agent's edit to `agentsh_sssf.yaml`, `agentsh_guard.py`'s pin constants or other `adws/` code never reaches the install, which renders from the hub. The next run refuses with the "differs" message. The `writes` check puts an edit to `adws/adw_modules/` back after each phase, and the running process doesn't reread it. An edit that rewrites the check itself and survives would beat it.
- **Glob characters.** Add a sentence to the opening paragraph (line 457). Under agentsh, the checkout's path, a worktree's path and your home folder may not hold any of `* ? [ ] { } \ $`. agentsh puts `${PROJECT_ROOT}` and `${HOME}` into its rules as raw text, where these are glob syntax, so a run refuses before any agent starts.
- **Agent cache (line 521).** Replace "SSSF makes `adws/adw_data/agent-cache` the same way, ..." so it matches the code. Before each wrapped agent starts, SSSF makes the agent cache with a `mkdir` that doesn't follow a link at its path. If a link or a file is there, the launch refuses and SSSF leaves it in place for you to look at. On a plain run, a swap of `adws/adw_data` or a folder above it still moves the cache, as the line above says. Keep the rest of that bullet (session subfolders made by path, reads that follow links), since that code is unchanged.
- **Hardlinks (new limit bullet).** A tree built outside the wrap, by you or by `worktree_setup`, can share files with your uv and bun caches. uv and bun hardlink by default. In sssf-sandbox, 492 of the `.venv`'s 642 files did. In both repos, all 5,176 of the dashboard's `node_modules` files did. An agent's write to its copy changes the cache copy, and the next project that installs from the cache gets it. An agent can't make a new hardlink to a cache file. To rebuild a tree with copies: `UV_LINK_MODE=copy uv sync --reinstall` for a uv venv, and `bun install --backend=copyfile` for `node_modules`. In the opening paragraph (line 457), change "Agents no longer write `~/.cache/uv`, `~/.local/share/uv`, `~/.npm` or `~/.cargo`" so it doesn't claim that for `~/.cache/uv`. Say agents can't write those folders directly, and point to the hardlink limit.
- **pi pattern (text guard, near lines 428 and 433).** Add a known limit of the text guard. The example `(^|[;&|]\s*)pi\b` misses pi after a wrapper word, such as `env pi`, `timeout 60 pi` or `bash -c 'pi'`. Under `local_guard: agentsh`, a `deny_exec` entry with `command: pi` refuses the exec whatever comes before it. Run 6e904be8's review found it.

### 6. `docs/guide/install.md`, "Turn on agentsh"

- Step 5 ("Run the printed lines yourself"): add that the lines end with `sudo systemctl restart agentsh`. Run it after every install, "Nothing to install" included. A run refuses while the service started before its binary or config last changed. If the first run after a restart still refuses for that reason, restart once more: the check counts whole seconds.
- The paragraph after the steps ("Before it starts, a run checks the service, the build and the installed policy"): add "and that the service was restarted since".

### Tests (`.claude/skills/sssf/tests/test_agentsh_guard.py`)

Use the `machine` fixture and temp folders only. Never touch the real `/usr/bin/agentsh` or `/etc/agentsh`, and never write the pin's real value in a test.

**Fixture changes:**
- In `machine`, monkeypatch `BINARY` and `PINNED_SHA256` **before** writing `config`. The rendered config now names them, so today's order would make every launch "differs".
- Replace the `_run` lambda with a small function that answers `systemctl is-active` from `state["active"]` as now, and `systemctl show` with `state["show"]`, exit code `state.get("show_rc", 0)`. Default `state["show"] = f"ExecMainStartTimestamp=@{int(time.time()) + 3600}\n"`, so the existing tests pass unchanged. Other commands go to the real `_run`.
- `test_ensure_refuses_a_missing_service_with_no_traceback` fakes `_run` in a subprocess and stops at "not active". Leave it.

**New tests:**
1. `test_render_config_ends_with_the_pin_line(monkeypatch)`. Monkeypatch `BINARY="/x/agentsh"` and `PINNED_SHA256="ab"*32`, then render the shipped template (`TEMPLATE`).
   - The last line is exactly the pin comment naming both values.
   - `yaml.safe_load` of the result equals `yaml.safe_load` of the result without that line.
   - Changing either constant changes the rendered text.
2. `test_an_edited_pin_constant_refuses(machine, tmp_path, monkeypatch)`:
   - (a) Copy the fake wrap to `tmp_path/"other"` with one extra byte. Monkeypatch `BINARY` to it and `PINNED_SHA256` to its sha256. `check_machine` raises matching "differs from what this checkout renders".
   - (b) Only `BINARY` changes, to an identical copy at another path: "differs".
   - (c) Only `PINNED_SHA256` changes: raises, matching "pinned".
3. `test_a_binary_or_config_changed_after_the_service_started_refuses(machine, tmp_path)`:
   - Set `state["show"]` to `@<int(time.time()) - 3600>`: raises matching "restart agentsh".
   - Set it to `int(os.stat(CONFIG).st_ctime)`, the same second: raises. Set it to `int(max(ctime of BINARY, ctime of CONFIG)) + 1`: passes.
   - `state["show"]` set to `"ExecMainStartTimestamp=\n"`, `"ExecMainStartTimestamp=n/a\n"` and `"garbage"`, and `show_rc=1`: each raises matching "can't read when the agentsh service started".
   - A refused launch starts nothing (the marker pattern from `test_each_machine_refusal_fires_before_an_agent_starts`).
4. `test_a_root_with_glob_syntax_refuses(tmp_path, monkeypatch, char)`, parametrized over each of `* ? [ ] { } \ $`, with `HOME` monkeypatched to a clean temp folder:
   - `git_repo(tmp_path / f"re{char}po")`: `check_root` raises `AgentshRefused` naming the character.
   - A worktree: `git_repo(tmp_path/"plain")` with one commit, then `git worktree add` to `tmp_path / f"w{char}t" / "wt"`. `check_root` on the worktree path raises.
   - `check_root` on `tmp_path/"plain"` passes.
5. `test_a_home_with_glob_syntax_refuses(machine, tmp_path, monkeypatch)`:
   - Monkeypatch `HOME` to `tmp_path/"h{me"`. `check_root(machine.root)` raises, naming the home folder.
   - `launch` with `env={"HOME": ..., "FAKE_DUMP": dump}` raises, and `dump` is never written.
   - With a clean `HOME`, monkeypatch `agentsh_guard.pwd.getpwuid` to return an object whose `pw_dir` holds `*`: raises too.
6. `test_start_refuses_a_root_with_glob_syntax_before_the_wrap(machine, tmp_path)`. Copy the guard with `root` set to a git checkout named `a[b]`, and launch with that cwd. It raises before `FAKE_DUMP` is written.
7. `test_start_refuses_a_link_or_file_at_the_agent_cache(machine, tmp_path, kind)`, parametrized over four cases at `machine.guard.agent_cache`:
   - an absolute link to an existing empty folder `tmp_path/"elsewhere"`;
   - a relative link to it;
   - a link to a missing `tmp_path/"nowhere"/"x"`;
   - a regular file holding `"x"`.

   For each, `launch` with `env={"FAKE_DUMP": dump}` raises `AgentshRefused` matching "link or a file". Then:
   - `dump` is never written;
   - the link or file is still there, unchanged (`os.readlink` gives the same target, and the file holds `"x"`);
   - `elsewhere` is still empty;
   - `tmp_path/"nowhere"` doesn't exist.

Existing tests that must still pass unchanged include:
- `test_each_machine_refusal_fires_before_an_agent_starts`;
- `test_the_agents_caches_are_set_for_both_harnesses_and_beat_the_engineers`, with mode 0o700 on a fresh cache;
- `test_check_root_refuses_a_directory_that_is_not_the_checkout_top`;
- `test_file_rules_equal_the_designs_deny_policy_output`.

## Docs this change makes out of date

- `.claude/skills/sssf/references/config.md`: edited in step 5.
- `docs/guide/install.md`: edited in step 6.
- Docstrings: `render_config`, `check_machine`, `check_root`, and the new `check_paths` and `_service_started`.
- `docs/designs/agentsh-local-sandbox.md` Next items 1, 10 and 11, and `docs/open.md` line 33: the hub session updates these after its post-merge probes, not the builder.
- No CHANGELOG in the repo. README isn't affected.
- `app_docs/`: not edited. The documenter should note that earlier agentsh write-ups describing the pin check as a hash of the file alone, or the agent cache made by a link-following `mkdir`, now describe old behaviour.

## Verification

This run is under agentsh, and `adws/tests` and the skill suite read the hub's `.env` at import. So, as in run cea6fe8d, copy `.claude` and `docs/designs/agentsh` to a fresh `/tmp` folder, keeping their relative paths so `DENY_POLICY` resolves.

1. **The guard tests.** From the copy:
   `uv run --no-config --directory <copy>/.claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_agentsh_guard.py -q -p no:cacheprovider`
   Every new test passes, and so do the existing ones named above, `test_file_rules_equal_the_designs_deny_policy_output` for both harnesses included.
2. **The whole suite.** Run the whole `tests` folder the same way. Wrap denials cause known failures (run 6e904be8: 172 failed, 12 errors), so compare your count with that, and report any new failure in `test_agentsh_guard.py`. The repo's own quality check, run outside the wrap, decides:
   `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`
3. **Policies unchanged.** In the copy, extract `dc44140`'s module with `git show dc44140:.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py` into a temp module folder beside the copy's other modules.
   - Render the claude and pi policies from both versions with `policy_text`, using the test's `PATHS`, and `diff` them. They must be byte-identical.
   - Render the config from both. The only difference is the new last line.
4. **Untouched paths.** `git status` shows nothing under `app_docs/`, the repo-root `adws/`, `specs/` (apart from this plan), `docs/designs/` or `docs/open.md`. `session_files.py` is unchanged.

## For the engineer

- The hub's own copy, `adws/adw_modules/agentsh_guard.py`, is protected. Bring it up to date from the template with the usual `just sssf-update` after the merge. This run's runner keeps checking with its own unchanged copy, so the worktree's change doesn't stop this run.
- After the merge, reinstall both repos' policies with `just agentsh-install`. The config diff shows the new pin comment line. Then run the printed lines, `sudo systemctl restart agentsh` included. If the first run then refuses as "changed after the agentsh service started", restart once more.
- Then the hub session checks that `check_machine` passes in both repos after the restart, reruns the agent-cache probes, and updates the design doc's Next list and `docs/open.md`.
