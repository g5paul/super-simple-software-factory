# Finish the agentsh wrap

Revises `specs/cf9d8014_agentsh-local-wrap_v3.md`. Everything in v3 not changed here still binds. **Keep the code on this branch and change it. Don't rewrite it.** Request: `specs/tasks/guard-agentsh-wrap-2.md`. The engineer's decisions in `docs/designs/agentsh-local-sandbox.md` aren't reopened. This version answers `spec_review_1.md`.

## Threat model

As v3: the agent runs as the engineer's user, can spell anything any way, and can change what the policy lets it write, including `repo.yaml`, the worktree's `.git` file and files in `/tmp`. A running process never re-reads `local_guard`, the git common dir or the config template.

New here: install files wait in a temp folder an agent could edit before the sudo step. A tampered policy, or a tampered config outside `policies.allowed`, differs from what SSSF generates once sudo installs it, so the launch refuses. **`policies.allowed` is the exception:** the check renders from the installed list, so an edit that adds a name or reorders the list passes `check_machine`. Reordering also changes `policies.default`, which is derived from the first entry. What that gains is small: every wrap passes `--policy` with SSSF's own name, a session may only load a policy from the root-owned `policies.dir`, and that policy's bytes are compared.

What this guard can't stop at its level: v3's list, an `argv[0]` holding a space, and a service still running on an older config. `check_machine` reads the file, not the service's memory, so a config installed and restarted, then replaced by the right file with no restart, passes.

## Baseline

`main` at `0e38cb5`: the text guard alone. Every finding is judged against it.

## Bar

v3's Bar and `specs/tasks/guard-agentsh-wrap.md`'s bar stand, with these changes:

- **Push.** Each spelling in "Push cases" below behaves as listed, judged on argv joined by spaces with `argv[0]` first, using the pattern text in the generated policy.
- **Config.** A config with enforcement off anywhere in it is a bypass if `check_machine` passes it.
- **Outcome order.** Three named exec denials raise `RuleBreach` with `refusals=n`, with spend kept, whatever a later read does. A service failure, including a failed final read on a clean call, is a separate stop that also keeps spend. A final state that depends on poll timing, or that drops a recorded breach or a call's spend, is a harm.
- **A breach replaces any other stop the call leaves by.** When a call exits through a non-breach `Exception`, such as `account.RateLimitStop` on Claude Code or a tracer failure on either harness, and `close` shows three denials reached, the call raises `RuleBreach` with `refusals=n`, spend kept, and the other exception as its cause. Below three, the original stop stands, as at baseline. The run stops either way, and `account.record` has already kept the rate-limit sample. A text `RuleBreach` is already a breach and stays as raised. A signal isn't an `Exception` and stays best effort.
- **Pending text call during a service failure stays a breach,** as at baseline. `ended()` raises because a refuse-rule call with no result may have run, and a dead service gives no evidence that it didn't. Calling it a plain failure would claim more than SSSF knows. v3's "three failed polls end as a failure" applies when no such call is pending. The breach carries the call's spend.
- **Stopping, replacing v3's Stopping text.** `stop_tree`, a silence stop, `just kill` and a SIGTERM end the wrap and everything under it. Ending the session is best effort under a signal, with no new event or wait. On every path that isn't killed by a signal, the session's DELETE is sent once.
- **Default bar.** No agent file kept, no agent code run, and no engineer file deleted or overwritten, where the baseline does otherwise. The install script writes nothing under the repo.

## Out of scope

- Everything in v3's Out of scope, and in the request's.
- The allow on the main checkout's session folder for a `--worktree` run. The engineer decides at rollout step 2.
- Accepted limits, not findings: an `argv[0]` holding a space, and `git -c a=b commit -m push` being denied.
- The main checkout's `.claude/` secrets on a `--worktree` run, and a session a signal leaves open. Both go in `config.md`.
- **Extra or reordered names in `policies.allowed`.** Machines differ on this list by design, so only this repo's names are required. See Threat model.
- **A service still running on an older config.** The check reads the file. The install step prints the restart. It goes in `config.md`.
- **An exception leaving pi's read loop outside `on_event`,** such as a failed raw log write or a JSON line that isn't an object. `agent_pi.run` has no cleanup there at baseline. The call still fails rather than passes, and `atexit` ends the wrap at exit, but no `close` runs, so denials aren't read and spend isn't kept, as at baseline.
- A newline inside a git option value. See "For the engineer".
- `tempfile` honouring `TMPDIR`. A `TMPDIR` inside the repo is the engineer's own setting.

## Files and changes

Modules are under `.claude/skills/sssf/templates/adws/adw_modules/`. Don't edit the stamped `adws/adw_modules/`.

### `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`

**Push rule.** In `COMMAND_HEAD`, `sssf-no-push` gets `args_patterns: ['^\S*\s+push(\s|$)', '^\S*\s+-.*push']` (double the backslashes in the Python string) and the comment from `docs/designs/agentsh/sssf-net.yaml`.

**`deny_exec` output.** `commands: ['{_q(deny.command)}']`, always quoted. Quote `name` as well, `- name: '{deny.name}'`: the slug check allows `null` and `true`, and an unquoted one changes type the same way.

**`secret_glob`.** Escape backslashes first, then braces: `a\b` becomes `**/a\\b`.

**`render_config(template: str, allowed: list[str]) -> str`** (new, one place for the script and the check):

```python
config = yaml.safe_load(template)
config["policies"]["allowed"] = list(allowed)
config["policies"]["default"] = allowed[0] if allowed else ""
return yaml.safe_dump(config, sort_keys=False)
```

`policies.default` is derived from the list, so it needs no second exception. Every wrap passes `--policy`, so `default` picks nothing.

**`read`.** In the `agentsh` branch only, read `agentsh_sssf.yaml` beside this module once into the new `LocalGuard.config_template`. An `OSError` becomes `SystemExit`. `text` reads nothing new.

**`check_machine`.** Replace step 3:

1. `installed = Path(CONFIG).read_bytes()`, then `yaml.safe_load` it. Parse or read errors refuse, as today.
2. `allowed = config["policies"]["allowed"]` must be a list of strings, or refuse.
3. Refuse unless `installed == render_config(guard.config_template, allowed).encode()`. The message says the file isn't what SSSF renders, and ends with `FIX`. An empty `config_template` refuses.
4. Refuse if a policy's name isn't in `allowed`.
5. Compare each policy with `read_bytes()` against `policy.text.encode()`. `read_text` would hide a CRLF. The text stays the one `read` generated in memory; no file is the reference.

Remove `project_root_off`, `listed_dir` and their refusals.

**Remove `_finished`** and its wait. Only the main thread ends a session, so close's return is the final state:

- `stop_tree()` only kills the tree, once. It no longer calls `_end_session`. The watcher and the silence dog can call it without doing a read on their own thread.
- `_at_exit()` (new method): `stop_tree()`, then `_end_session()`. `atexit` registers and unregisters this instead of `stop_tree`.
- `_end_session()`: under `_lock`, take the once-only flag and set `_done`. Then final read, `_take` under `_lock`, with the DELETE in a `finally`, so a `SystemExit` during the read still sends it. An `Exception` from the read sets `failure` to `the final read of agentsh events failed (<type>)`, which also covers a malformed event, and adds the trace note. A second caller returns at once.
- `_poll`: fetch outside the lock, then under `_lock` return without taking if `_done` is set, or else advance the offset and `_take`.
- `_watch`: on the third failed poll, under `_lock`, set `failure` only if `_done` isn't set, so a late poll can't change the outcome after close. Then `stop_tree()`. A trip calls `stop_tree()` and returns. The watcher never does the final read.
- `wait_attached`'s failure path: `stop_tree()`, `_end_session()`, `atexit.unregister(self._at_exit)`, then raise, because no `close` follows a failed `start`.
- `close` calls `_end_session()` as now.
- `breach(process, agent="") -> Optional[rules.RuleBreach]` (new): returns the three-refusal stop if this process reached it, or `None`, including for any other process type. Its test is `check_stopped`'s current one: `process.counted and (process.tripped or total >= rules.MAX_REFUSALS)`.
- `check_stopped`: raise `breach(...)` **first**, then test `failure`.
- `class AgentshRefused`: add `partial = None`.

**The wrap env.** Add `"JUST_NO_DOTENV": "true"`. `SSSF_AGENTSH` stays.

### `.claude/skills/sssf/templates/adws/adw_modules/data_types.py`

`LocalGuard` gets `config_template: str = ""`.

### `.claude/skills/sssf/templates/adws/adw_modules/repo_settings.py`

- `_bare_host`: replace the `0x...|[0-9.]+` test. Reject when the last label is all digits or `0x` plus hex digits, case-insensitive, or is `localhost`. That covers `0x7f.0.0.1`, `0x7f.1`, `127.1`, `2130706433`, `0177.0.0.1` and `a.localhost`, and leaves `example.com` and `*.example.com` alone.
- `DenyExec.command` stays `str`, so a YAML `null` or `true` fails validation. Fix the `args` comment: the regex runs over all of argv joined by spaces, `argv[0]` included.

### `.claude/skills/sssf/templates/adws/adw_modules/agent_cc.py`

- Add a nested `keep_spend(stop)` holding the current `except` body around `check_stopped`: it writes the ledger and uses `_partial_from_final` when there's a `final`, or else `_partial(turns, output)`.
- Widen the `except` around `check_stopped` to `(rules.RuleBreach, agentsh_guard.AgentshRefused)`, calling `keep_spend(stop)`.
- Set `left_by = None` before the `try`, and `left_by = error` in the existing `except BaseException as error`.
- At the end of the existing `finally`, after `on_exit`:

```python
if isinstance(left_by, Exception) and not isinstance(left_by, rules.RuleBreach):
    stop = agentsh_guard.breach(process, request.agent_name)
    if stop:
        keep_spend(stop)
        raise stop from left_by
```

A text run's process isn't an `AgentshProcess`, so `breach` returns `None` and it behaves as at baseline.

### `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py`

- Widen the same `except`, with `stop.partial = result`.
- In the `on_event` failure branch, after `on_exit` and before the `raise`:

```python
if not isinstance(error, rules.RuleBreach):
    stop = agentsh_guard.breach(process, request.agent_name)
    if stop:
        stop.partial = result
        raise stop from error
```

### `.claude/skills/sssf/templates/adws/adw_modules/agents.py`

- In `send`'s inner `except Exception`: `except Exception as error: forward.ended(getattr(error, "partial", None)); raise`. Nothing on a text path has a `partial` here, so text stays as baseline.
- Add to the outer handlers: `except agentsh_guard.AgentshRefused as stop: count_partial(stop); raise`.

### `.claude/skills/sssf/templates/adws/adw_agentsh_policy.py`

Remove `_write`, `os`, the link refusals and `cfg.defaults.data_root`. Use `out = Path(tempfile.mkdtemp(prefix="sssf-agentsh-"))`. Write with `Path.write_text(..., encoding="utf-8")`, and `render_config(template_text, merged_allowed)` for the config. Print sudo lines with `shlex.join`. Reading the installed list also tolerates `ValueError`, `AttributeError` and non-string entries, dropping those. Update the docstring. Add nothing to any gitignore list.

### `.claude/skills/sssf/references/config.md`

- Install step: a fresh temp folder, printed by the command.
- Refusal sentence: the installed config must equal what SSSF renders, apart from `policies.allowed`.
- `deny_exec` table row: `args` runs over all of argv joined by spaces, `argv[0]` included.
- Say the wrap sets `JUST_NO_DOTENV=true`, and why.
- Say three denials end the call as a breach even when it was leaving by another stop.
- Known limits: the main checkout's `.claude/` secrets on a `--worktree` run, an `argv[0]` holding a space, a session a signal leaves open, and a service still running on an older config, since the check reads the file and only the printed restart loads it.

### `.claude/skills/sssf/templates/repo.yaml`

The `deny_exec` example's comment says what `args` matches. Its pattern `(^|\s)-\w*r\w*f` still works on the joined form.

### `docs/designs/agentsh-local-sandbox.md`

Add a `## What got built` section before `## Running the checks`, and under it the first real run's checks: the two already listed, plus the push cases under the real service, the service accepting the rendered config, and `set dotenv-load` recipes under the wrap. Leave the rest of the doc as it is.

No `app_docs/` edit, and no older write-up there covers this. There is no CHANGELOG.

## Tests

All in `.claude/skills/sssf/tests/test_agentsh_guard.py`, config from the shipped `sssf.config.yaml`, hub suite passing with no service.

**Fixture.** `machine` installs `render_config(<real agentsh_sssf.yaml>, names)` and its guard carries `config_template`.

**Remove or change.**

- Remove `test_the_policy_command_refuses_a_link_and_replaces_files`.
- Rewrite `test_check_machine_matches_whole_names_and_the_policy_dir`, and the last lines of `test_each_machine_refusal_fires_before_an_agent_starts`, for the new message.
- `test_stop_tree_kills_the_child_and_deletes_the_session_once`: `stop_tree` twice sends no DELETE, and `close` then sends one.
- `test_a_third_denial_in_the_final_read_counts_even_when_another_thread_is_ending`: fix the comment, keep the assert.
- Add `JUST_NO_DOTENV` to `test_each_harness_gets_its_policy_the_env_and_a_null_stdin`, and assert all of `AGENTSH_CONFIG`, `AGENTSH_SERVER`, `AGENTSH_NO_AUTO`, `NODE_USE_ENV_PROXY` and `JUST_NO_DOTENV` by name, with `JUST_NO_DOTENV == "true"`.

**Push cases.** Take the patterns from `yaml.safe_load(policy_text(...))`'s `sssf-no-push`, join argv with `" ".join`, and use `re.search(p, joined, re.ASCII)`. Parametrized on argv lists:

- Run: `git commit -m "fix the push path"`, `git commit -m push`, `git stash push`, `git log --grep push`.
- Denied: `git push`, `git -c a=b push`, `git -c "a=b c" push`, `git -C /x push`, `git --git-dir /x push`, `git -c alias.p=push p`, `git -c a=b commit -m push`, and an empty `argv[0]` with `push`.

**Install script.** In a throwaway repo with `local_guard: agentsh` and `adws/adw_data` a symlink to an empty outside folder, and `agentsh_guard.CONFIG` monkeypatched:

- `git status --porcelain` is the same before and after, and the outside folder stays empty.
- Every printed source path exists, outside the repo.
- Installing the printed config and policies into the fake machine makes `check_machine` pass, so the script and the check agree. Repeat with another repo's names already in the installed list.

**`check_machine`.** One parametrized case per key, each refusing: `sandbox.ptrace.enabled`, `.trace.execve`, `.trace.file`, `.trace.network`, `.on_attach_failure: fail_open`, `.attach_mode`, `sandbox.network.enabled`, `.require_sni_match`, `policies.detect_project_root: true`, `policies.dir`, `policies.default`, `audit.enabled`, `server.unix_socket.path`, an extra top-level key. Raw forms also refuse:

- a trailing comment
- a CRLF file
- a duplicate key
- `allowed` as a string, with a non-string entry, or missing this repo's name
- a CRLF installed policy

It passes when `allowed` holds other repos' names, first and last, with the config rendered for that list.

**Outcomes**, on both harnesses through `agent_cc.run` and `agent_pi.run`, under the fake wrap with a fake agent that reports cost, using the helpers in `.claude/skills/sssf/tests/test_agent_cc.py` and `.claude/skills/sssf/tests/test_agent_pi.py`:

- Three denials with a failing final read raise `RuleBreach`, `refusals=3`, `partial` set.
- A clean call with a failing final read raises `AgentshRefused`, `partial` holding the cost, and on Claude Code the ledger written.
- Three failed polls plus a final read that succeeds with three denials is a breach.
- A late failed poll after `close` doesn't set `failure`.
- An `on_event` that raises a non-breach exception, with three denials in the final read, raises `RuleBreach`, `refusals=3`, `partial` set, `__cause__` the original. With two denials the original exception stands.
- A text `RuleBreach` from `on_event` with three denials counted stays that breach.

On Claude Code only, the credits stop as `.claude/skills/sssf/tests/test_rate_limits.py` triggers it: with three denials it raises `RuleBreach`, `refusals=3`, `partial` set, and with two it stays `RateLimitStop`. Pi has no such stop in its read loop.

Through `agents.execute` with a faked interface, as `.claude/skills/sssf/tests/test_budget.py` does:

- `AgentshRefused` with `partial` adds the cost to the run.
- The same with a refuse-rule call pending raises `RuleBreach` with `refusals=0`, and the cost is kept.

**Session end.**

- `_at_exit` kills the tree and sends one DELETE.
- A `SystemExit` raised during the final read still sends the DELETE and returns at once.
- A failed attach sends the DELETE when a session id was seen.
- `_finished` is gone: `not hasattr(process, "_finished")`.

**Validators.**

- `network_allow` refuses every form under `repo_settings.py` above, and each dotted numeric one also passes `socket.inet_aton`, which shows the kernel form is real.
- `a\b` gives `**/a\\b`.
- `command: null` and `true` as YAML types fail validation.
- `command: "null"` and `"true"`, and a rule named `null`, parse back from the policy as strings.

**The trace.** `test_non_git_directory_text_goes_on_and_agentsh_exits_plainly` and `test_read_exits_plainly_on_a_broken_repo_yaml` use `monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.resolve().parent))`, which subprocesses inherit. Add a case running the same code from a folder inside a throwaway repo, asserting the plain exit and that nothing appears in that repo. Search `.claude/skills/sssf/tests` for `session.ensure` and `chdir(tmp_path)`, and give the same to any other test starting from a `tmp_path` that isn't its own repo. `test_budget.py` and `test_trial.py` use their own repos, and `test_sandbox_extensions.py` stops before a session.

## Verification

- The repo's own test command passes on a machine without the service. It runs `pytest tests` in `.claude/skills/sssf`, which collects `.claude/skills/sssf/tests/test_agentsh_guard.py`, so no extra check is needed.
- A search of `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py` and `.claude/skills/sssf/templates/adws/adw_agentsh_policy.py` finds no `_finished`, `_write` or `adw_data/agentsh`. Other modules have their own `_write`.
- The live checks under `AGENTSH_LIVE` aren't run. Rollout step 2 runs them.
- The reviewer confirms that removing the data-dir path closes the install script harm and the untracked-files finding.

## For the engineer

These are protected, so the builder must not touch them. They're still open from v3:

1. `adws/adw_guard_change.py`: add `agentsh_guard.py` to `GUARD_MODULES`.
2. `CLAUDE.md`: add `agentsh_guard.py` to the guard code list.
3. `adws/tests/test_guard_change.py`: add a matching assertion if you want one pinned.

**A gap in the decided push rule, for you to decide.** `.` doesn't match a newline, so `git -c $'a=b\nc' push` joins to text neither pattern matches. I confirmed that with Python's `re`, and Go's RE2 reads `.` the same way, though that part wasn't run. It isn't on your denied list, so the build uses your text verbatim and a reviewer should file this here. The network rules still refuse GitHub, and the text check stays on. `(?s)` on the second pattern would close it, but the fork tests would need rerunning against the changed pattern.
