# Stop SSSF's session-folder writes from following an agent's link

Rollout step 2 of `docs/designs/agentsh-local-sandbox.md`. This revises `specs/1df8cc16_agentsh-drop-symlink-allow_v2.md`. The chain run 1df8cc16 (`docs/designs/agentsh/guard-run-1df8cc16.md`) stopped after round 3 because round 2's fix added a `..` link deny. This branch holds the round-2 snapshot `0185f91` without that rule. The engineer's decisions are in the design doc's section "The chain run without the symlink allow, 1df8cc16". They're settled, so don't reopen them.

**Keep everything on the branch.** That includes the dropped symlink allow, the widened alias pattern, the sessions folder allow and every test. This plan changes how SSSF itself writes under a run's session folder. It adds no policy rule, and `agentsh_guard.py` doesn't change.

## Threat model

Each local Claude Code or pi agent runs as the engineer's own user under `agentsh wrap`, with a default-deny policy SSSF generates. On a `--worktree` run, `sssf-sessions` allows every operation on `<main>/adws/adw_data/sessions` and everything under it, after every deny. On a plain run the session folder sits inside the workspace, which `sssf-workspace` allows. So in both cases the agent can:
- make an absolute symlink anywhere under the sessions folder, or move one in from its workspace,
- make a hard link there to any file it owns on the same filesystem, or a FIFO,
- replace a folder there, such as `<session>/<agent>`, `<session>/context_handoff`, the run's `<session>` folder, or on a `--worktree` run the `sessions` folder itself, with a link,
- find the host's pid and its open descriptors through `/proc/<pid>/fd`.

The host, SSSF, then writes into those folders by path. `Path.write_text` and `open("a")` follow a link at the last component and at every folder above it. With a target of `/proc/<pid>/fd/<n>` or `/dev/fd/<n>`, the write reaches a file the host holds open, such as `sssf.db`. With a target in the main checkout, it overwrites an engineer file. The reviewer showed the envelope write cutting a held-open SQLite file to 29 bytes.

A rule on link targets misses the next spelling, so the fix is on the writer's side. At this level SSSF can't stop:
- a process that leaves the wrap,
- what other host tools do with links the agent leaves, such as git, or Claude Code and pi writing their own session files,
- on a plain run, a link at the data folder or above it (`adws/adw_data`, `adws`), which is the root this fix trusts. See Out of scope.

The text guard (`rules.py`, `sssf_refuse.py`) stays on and is unchanged.

## Baseline

`main` at `81ca839`. Every finding is judged against that commit. On `main`:
- the file rules have no `sssf-sessions`, so on a `--worktree` run the sessions folder in the main checkout is default-denied,
- every host write under the session folder goes by path and follows a link: `Path.write_text`, `Path.write_bytes`, `open("a")` and `open("w")`,
- on a plain run the session folder is in the workspace, so an agent can already place a link there and the host follows it.

## Bar

Must do, against the baseline:
1. **One way of writing.** Every host write under `<data_root>/sessions/` goes through `adw_modules/session_files.py`. The sites are listed under "Files and changes", each one named.
2. **A whole write replaces, never follows.** `session_files.write` writes a temp file in the target's own folder and renames it over the target. A link, hard link, FIFO or file at the target is replaced. Its target, or the other names of a hard-linked file, are left as they were.
3. **An append never follows.** `session_files.open_append` opens the last component with `O_NOFOLLOW` relative to its folder's descriptor. It keeps the file only if `fstat` shows a regular file with one link. Anything else at that name, whether a symlink, a hard link, a FIFO or a device, is unlinked and made again with `O_CREAT | O_EXCL | O_NOFOLLOW`. So an append never reaches another file.
4. **No folder link is followed either.** Both helpers walk from the data root to the target's folder one component at a time. Each `os.open(name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW, dir_fd=parent)` is relative to the folder before it. A missing folder is made with `os.mkdir(name, dir_fd=parent)`. A link or a non-folder in the way is unlinked and made again as a folder. Only the data root itself is opened by path, following links. It is the engineer's configured folder, and on a `--worktree` run no agent can write it.
5. **Both run kinds.** This closes the hole on plain runs too, where `main` has it.
6. **The policy doesn't change.** `agentsh_guard.file_rules` output is the same as on the branch head, and `sssf-sessions` stays `rule("sssf-sessions", [sessions, f"{sessions}/**"], ALL, "allow")`, after every deny. `test_file_rules_equal_the_designs_deny_policy_output` still passes with `docs/designs/agentsh/deny-policy.py` unchanged.
7. **The branch's earlier work stays.** The relative-link deny via `default-deny-files`, the three-pattern `sssf-no-push`, the refuse-script read, the `null` page, the argv-named command and their tests are unchanged.

Must never do, against the baseline:
- keep an agent file,
- run agent code,
- delete or overwrite an engineer file. Unlinking a link, FIFO or hard link that an agent placed under the session folder isn't an engineer file,
- allow a path the baseline denies, beyond the sessions folder allow already on the branch,
- let a host write under the session folder follow a link an agent placed there, whatever the link's target.

Any of these is a finding:
- a new file rule that judges link targets,
- a `..` deny,
- a list of target folders,
- a check of where a link points before writing. The helper never reads a link. It replaces it.

Accepted, not findings:
- the limits under Out of scope, and every limit `config.md` already lists,
- an agent can write `.env.example` and `.env.sample` anywhere, because `sssf-env-examples` sits ahead of every deny, as on `main`,
- a link, FIFO or hard link an agent placed under the session folder being removed silently. Its content is the agent's own.

## Out of scope

These stay open. File them as old holes:
- **A link at the data root or above it on a plain run.** On a plain run `adws/adw_data` is in the workspace. An agent can swap it, or `adws`, for a link. The helper opens the data root by path, so its writes then land in `<target>/sessions/<adw_id>/…`, under file names SSSF picks. The tracer's open `sssf.db` connection is unaffected. This is as on `main`. On a `--worktree` run no agent can write the data root.
- **Host reads under the session folder follow links.** These include the claude ledger read in `agent_cc.py`, `agent_map.json` in `runner.py`, and `plan.md` and the other handoff files the gates and later agents read. A read changes no file. The bar is about writes.
- **Folders made by path.** `Run.__init__`'s `ensure_dir` calls, `agent_dir.mkdir` in `agents.py`, and the `mkdir` calls in `quality.py`, `plan_checks.py`, `worktree.run_setup` and `trial.py` can follow a folder link and make an empty folder at its target. They write no file, and they delete or overwrite nothing. Leave them.
- **Writes by tools other than SSSF's Python.** Claude Code's and pi's own session files under `<agent>/<harness>_sessions`, and `git bundle create` writing `sandbox-in.bundle` in `sandbox_remote.py`. git writes the bundle through a lock file and a rename.
- **A host file left in a folder the agent moved.** The helper holds each folder's descriptor. If an agent renames `<session>/<agent>` into its workspace mid-write, SSSF's file lands in the moved folder. SSSF wrote that content, and the agent can write there anyway.
- Moving the handoff folder into the worktree.
- A fork patch so agentsh resolves link targets.
- pi under the wrap.
- Running a real agent under the service.
- sudo.
- Turning this on outside sssf-sandbox.
- Every out-of-scope item and known limit in `specs/1df8cc16_agentsh-drop-symlink-allow_v2.md`, such as the workspace-root swap and absolute targets holding `..`.

## Files and changes

Paths are under `.claude/skills/sssf/templates/adws/` unless they're given in full. Read each module whole before you edit it. Don't touch the repo's own `adws/` copy, which is protected.

### `.claude/skills/sssf/templates/adws/adw_modules/session_files.py` (new)

A small module with no SSSF imports. Its docstring says why it exists: an agent can place links under the session folder, a rename replaces a link and never follows it, and every host write there must come through here.

- `class LinkedPath(OSError)`: raised when the helper can't make the path its own after a few tries, for example because an agent keeps racing it. The message names the path.
- `_walk(root: Path, path: Path) -> tuple[int, str]`. Make both paths absolute with `os.path.abspath`, which is lexical: never call `resolve()`. Raise `ValueError` if `path` isn't under `root`. Open `root` with `O_RDONLY | O_DIRECTORY | O_CLOEXEC`, following links. For each folder component between `root` and the file name, try `os.open(part, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC, dir_fd=fd)`:
  - On `FileNotFoundError`, run `os.mkdir(part, dir_fd=fd)`, ignoring `FileExistsError`, then retry.
  - On `ELOOP` or `ENOTDIR` (a link or a non-folder), run `os.unlink(part, dir_fd=fd)`, then `mkdir` and retry.
  - Give up after 3 tries per component with `LinkedPath`.

  Close each parent fd once the child is open. Return the last folder's fd and the file name. The caller closes the fd in a `finally`.
- `write(path, data: str | bytes, root) -> None`. Walk to the folder. Create a temp file named `.{name}.{os.getpid()}.{secrets.token_hex(4)}.tmp` with `os.open(tmp, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0o666, dir_fd=dfd)`. The umask applies, as it does for `write_text`. Write all bytes, encoding `str` as UTF-8, and close. Then `os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)`. On any error, unlink the temp file (`dir_fd=dfd`, ignoring errors) and re-raise.
- `open_append(path, root) -> TextIO`. Walk to the folder. Up to 3 times:
  1. `fd = os.open(name, O_WRONLY | O_APPEND | O_CREAT | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC, 0o666, dir_fd=dfd)`.
  2. On `ELOOP` (a symlink) or `ENXIO` (a FIFO with no reader), `os.unlink(name, dir_fd=dfd)` and try again with `O_EXCL` added.
  3. After a successful open, `os.fstat(fd)`. If it isn't `S_ISREG`, or `st_nlink != 1`, close it, unlink the name and try again with `O_EXCL`.
  4. Once kept, clear `O_NONBLOCK` with `fcntl` and return `os.fdopen(fd, "a", encoding="utf-8")`.

  After 3 tries, raise `LinkedPath`. It's used for logs a host keeps open and for per-event appends.
- `root_of(session_dir) -> Path`: returns `Path(session_dir).parent.parent`, the data root above `sessions/<adw_id>`, for callers that hold only a session folder. Use it only where noted below.

Linux and macOS both support `dir_fd` for `os.open`, `os.mkdir`, `os.unlink` and `os.replace`, and `O_NOFOLLOW`. SSSF doesn't run on Windows. Add no fallback.

### The write sites

Each one changes to the helper. The root is always the run's data root, given explicitly.

1. **`adw_modules/runner.py`**: `Run.__init__` stores `self.data_root = Path(data_root)`, the value it already computes. `save_agent_map` writes `agent_map.json` with `session_files.write(self._agent_map_path, …, self.data_root)`.
2. **`adw_modules/agents.py`**:
   - `_persist_envelope` writes `<agent>/envelope.json` with `session_files.write(…, run.data_root)`.
   - The `prompts.save` calls for `system.md` and `user.md` pass `root=run.data_root`.
   - The `AgentRequest` build: `session_dir` and `raw_output_path` use `os.path.abspath(...)` instead of `.resolve()`, because `resolve()` follows a link an agent placed. Add `data_root=str(run.data_root)`.
3. **`adw_modules/prompts.py`**: `save(directory, name, content, root)` writes with `session_files.write`. Update the docstring. `save` has no other callers. Confirm with a grep.
4. **`adw_modules/data_types.py`**: `AgentRequest` gets `data_root: str = ""`, commented "the folder above `sessions/`; SSSF's own writes under it never follow a link". When it's empty, as in tests that build a request by hand, `agent_cc` and `agent_pi` use `Path(raw_output_path).parent` as the root.
5. **`adw_modules/agent_cc.py`**:
   - The claude cost ledger `<agent>/claude_sessions/<session_id>.json` is written in three places: `keep_spend`, the first `system` event, and after the call. All three use `session_files.write(ledger, …, root)`.
   - `raw_output.jsonl` is opened with `session_files.open_append(raw_path, root)` instead of `raw_path.open("a")`.
   - Work out `root` once, near `_ledger(request)`.
6. **`adw_modules/agent_pi.py`**: `raw_output.jsonl` is opened with `session_files.open_append(raw_path, root)`, with the root worked out the same way.
7. **`adw_modules/tracer.py`**:
   - `Tracer.__init__(db_path, events_jsonl, data_root=None)`.
   - `event()` appends to `events.jsonl` with `session_files.open_append(self.events_jsonl, self.data_root)`.
   - When `events_jsonl` is `os.devnull`, as in `kill.py`, `trial.py:175` and `worktree.py`'s three throwaway tracers, `event()` writes no line. Keep the SQLite insert.
   - A real path with no `data_root` raises `ValueError` in `__init__`.
   - Update the class docstring.
8. **The three tracers with a real events file** pass `data_root=cfg.defaults.data_root`: `adw_modules/session.py` (`Tracer(cfg.observability.db, …events.jsonl)`), `adw_modules/trial.py:373` and `adw_modules/worktree.py:359`.
9. **`adw_modules/changes.py`**: the diff in `context_handoff/` (`DIFF_FILENAME`) is written with `session_files.write(diff_path, …, run.data_root)`.
10. **`adw_modules/quality.py`**: `context_handoff/quality/NN_name/command.log` (`output_artifact`) uses `session_files.write(…, run.data_root)`.
11. **`adw_modules/plan_checks.py`**: `context_handoff/quality/NN_name/command.log` uses `session_files.write(…, run.data_root)`.
12. **`adw_modules/worktree.py`**:
    - `run_setup(commands, cwd, timeout, log_dir, box=None, root=None)` writes each `NN.log` with `session_files.write(log, …, root)`. `root` becomes required in practice: both callers pass it.
    - The setup call passes `run.data_root`, for `context_handoff/setup`.
    - The rebase call passes `Path(cfg.defaults.data_root)`, for `context_handoff/rebase_setup`.
    - Update the docstring.
13. **`adw_modules/trial.py`**:
    - Each attempt's `sessions/<adw_id>/trial.log` is opened with `session_files.open_append(log, root / cfg.defaults.data_dir)` instead of `log.open("w")`. The folder is new, so append and write give the same file.
    - The referee's `context_handoff/panel_<fam>/<label>.diff` uses `session_files.write(…, run.data_root)`.
14. **`adw_modules/sandbox_remote.py`**:
    - `sandbox-uncommitted.patch` uses `session_files.write(path, done.stdout, self.run_.data_root)`.
    - `sandbox-out.bundle` uses `session_files.write(bundle, base64.b64decode(packed), self.run_.data_root)`.
    - `handoff_out` stops calling `tar.extractall(self.handoff, …)`. Extract into a fresh `tempfile.mkdtemp()` with `filter="data"`, as now. Then, for every regular file in the temp tree, `session_files.write(self.handoff / rel, bytes, self.run_.data_root)`. Skip link members. Delete the temp folder in a `finally`.
    - Leave `git bundle create` for `sandbox-in.bundle` alone. See Out of scope.
    - If `self.run_` can lack `data_root`, as with test doubles, use `session_files.root_of(self.run_.session_dir)`.
15. **`adw_modules/sandbox_openshell.py`**: `openshell-policy.yaml` uses `session_files.write(file, policy_yaml(...), root)`, with `root` taken the same way as in item 14.

Before you finish, grep `adw_modules/` and `adw_*.py` again for `write_text`, `write_bytes`, `.open("a"`, `.open("w"` and `extractall`. Confirm that no other write lands under the session folder. If you find one, route it through the helper and name it in your report. `account.py`'s writes go to the account folder, `adw_agentsh_policy.py`'s to a temp folder, `tickets.py`'s to the repo, and `permissions.py`'s to `mktemp`. They aren't under the session folder.

### `.claude/skills/sssf/tests/test_session_files.py` (new)

Use `tmp_path` as the data root, with `sessions/abcd1234/planner/` under it. Tests:
1. `write` over a symlink to an outside file leaves the outside file's bytes unchanged. The path is then a regular file with the new content.
2. `write` over `/dev/fd/<n>` and over `/proc/self/fd/<n>` for a file the test holds open leaves that file's size and bytes unchanged. Skip when `/proc/self/fd` doesn't exist. Also build a SQLite db, hold a connection open, link `envelope.json` to its descriptor, write, and check the db still opens and holds its row.
3. `write` over a hard link to an outside file leaves the outside file unchanged.
4. `open_append` over a symlink, a hard link and a FIFO (`os.mkfifo`): none blocks, each outside file is unchanged, and the line lands in a fresh regular file. Opening an existing regular file appends to it.
5. A folder link on the way: `planner` → an outside folder, and separately `sessions` → an outside folder. `write` creates nothing in the outside folder, and the link is replaced by a real folder.
6. A path outside the root raises `ValueError`.
7. A failed `write` leaves no `.tmp` file. Force it with a folder at the target name.

### Site tests, in existing test files

Each plants a symlink to an outside file at the site's path and checks the outside file is unchanged:
- **`.claude/skills/sssf/tests/test_changes.py`**: the diff in `context_handoff/`. Use the file's `repo` fixture, with a run carrying `data_root`.
- **`.claude/skills/sssf/tests/test_agent_cc.py`**:
  - the ledger and `raw_output.jsonl`, using the file's fake-claude setup with a request whose `data_root` is set,
  - `prompts.save`,
  - `Tracer.event` with a real `events.jsonl`,
  - `Run.save_agent_map`.

  If any of these fits better beside an existing test of that module, put it there.
- **`.claude/skills/sssf/tests/test_git_dir_guard.py`**: add `_persist_envelope` next to the file's `SimpleNamespace(cfg=agents.load_config(str(SKILL / "templates" / "sssf.config.yaml")), …)` helper. It uses the shipped config, never a test-only one. Use an envelope from `data_types`, such as a `PlanOutput`, and a run with `data_root`, `session_dir` and a tracer stub.
- Update every existing test that calls `prompts.save`, `worktree.run_setup`, `Tracer(...)` with a real events path, or `sandbox_remote`'s `handoff_out`, for the new parameters. Grep the tests for each.

### `.claude/skills/sssf/references/config.md`

In "Running local agents under agentsh", under "More known limits":
- **Replace** the sessions-folder line, "On a `--worktree` run, agents may read and write the main checkout's `<data_dir>/sessions/`…", with:

  "On a `--worktree` run, agents may read and write the main checkout's `<data_dir>/sessions/`, which holds every run's handoff folder, and Claude Code agents may read `adws/adw_modules/sssf_refuse.py`. Secret files and git config stay denied inside the sessions folder. SSSF's own writes there never follow a link. It writes each file to a temp file in the same folder and renames it over the target, opens logs so a link, hard link or FIFO at the path is replaced, and replaces a link in place of a folder. This holds on plain runs too. On a plain run the data folder is in the workspace, so an agent that swaps `adws/adw_data` or a folder above it for a link still moves where SSSF writes, as before."
- **Add**: "An agent can write `.env.example` and `.env.sample` anywhere, outside its workspace too, because their allow sits ahead of every deny, as before."
- There is no plain-run descriptor-link line to remove. `config.md` names no such limit. This fix closes that hole for SSSF's writes, and the replaced sessions line says so. Report this in your summary.

### `docs/designs/agentsh-local-sandbox.md`

- **Don't edit** the section "The chain run without the symlink allow, 1df8cc16".
- **In "What got built"**, add a bullet: a symlink at `<session>/<agent>/envelope.json` to `/proc/<pid>/fd/<n>` or to a main-checkout file is replaced by SSSF's next envelope write, and the target is unchanged. This holds on a `--worktree` run and a plain one.
- **Add a paragraph** in "The first real runs", after the "The refuse script and the sessions folder (built)" paragraph, titled "**SSSF's session-folder writes (built).**":

  "Every host write under `<data_root>/sessions/` goes through `adw_modules/session_files.py`. It walks from the data root with `O_NOFOLLOW` at every folder, writes a temp file and renames it over the target, and opens appended logs so that anything but SSSF's own single-link regular file is replaced. No file rule judges link targets."

### Other docs

- `.claude/skills/sssf/` has no CHANGELOG, so add none.
- Update these docstrings and comments: `prompts.save`, `Tracer`, `worktree.run_setup`, the `AgentRequest.data_root` field comment, and the `raw_output_path` comment in `agents.py`, which says "absolute". Change no other docstrings.
- Don't edit `app_docs/`. The documenter should note that any write-up saying SSSF writes `envelope.json`, `events.jsonl`, prompts, raw logs, the ledger or the handoff diff with a plain path write now describes old behaviour.

## Verification

- **The repo's own test command passes.** It runs `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`, which collects the new `tests/test_session_files.py` and the site tests. With no service on the machine, `test_live_service_is_active` skips as before.
- **The policy is unchanged.** `test_file_rules_equal_the_designs_deny_policy_output` and the `sssf-no-push` equality test pass, and `docs/designs/agentsh/deny-policy.py` isn't edited.
- **No session-folder site writes by path any more.** This command exits 0:

  `bash -c '! grep -nE "\"envelope.json\"\)\.write_text|ledger\.write_text|raw_path\.open|events_jsonl\.open|diff_path\.write_text|output_artifact\.write_text|log\.write_text|log\.open\(|path\.write_text\(content\)|_agent_map_path\.write_text|\.diff\"\)\.write_text|bundle\.write_bytes|path\.write_text\(done\.stdout\)|file\.write_text\(policy_yaml|extractall\(self\.handoff" .claude/skills/sssf/templates/adws/adw_modules/*.py'`
- **No new link rule.** This command exits 0:

  `bash -c '! grep -nE "sssf-link-dotdot|\*\*/\.\./\*\*|sssf-relative-links|sssf-link-targets|LINK_FOLDERS" .claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py docs/designs/agentsh/deny-policy.py'`
- **Edit files with the file tools, not shell one-liners.** The repo's text guard searches every shell command's text.
- **Nothing may change under `adws/adw_modules/`, `adws/tests/`, `specs/` or `app_docs/`.** The builder reads `git status --porcelain` to confirm this. It isn't a check.

## For the engineer

1. **The repo's own `adws/` copy is protected.** After this merges, sync `adws/adw_modules/` from the template the way you usually do, so this repo's runs get `session_files.py`.
2. **Guard module lists.** Add `session_files.py`, and `agentsh_guard.py`, still open from earlier specs, to `GUARD_MODULES` in `adws/adw_guard_change.py` and to `CLAUDE.md`'s list of guard code. Both paths are protected.
3. **Run under the service.** In the `--worktree` and plain chains in sssf-sandbox, have an agent link `<session>/<agent>/envelope.json` to `/proc/<sssf pid>/fd/<n>` for `sssf.db`. Confirm the db is intact after the envelope write and the run carries on.
