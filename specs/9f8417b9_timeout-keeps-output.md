# Keep a timed-out command's output, and stop all of it

## Goal

`sandbox.LOCAL.run` with a timeout must, when the timeout fires:

1. raise `TimeoutExpired` holding what the command printed, even when the 5 s second wait also times out;
2. kill the command's descendants by pid (`rules.stop_tree`) before the group kill, so a child that called `setsid` dies too;
3. when `os.killpg` is refused with `PermissionError`, kill each member of the command's process group by its own pid.

The call with no timeout stays a plain `subprocess.run`. Nothing else in `sandbox.py` changes.

## What I confirmed on `main` (2026-11-21, host)

- `LOCAL.run(["sh", "-c", "echo started; (setsid sleep 20 &); sleep 300"], timeout=1)` raises after 6.0 s with `stdout` and `stderr` both `None`.
- A `Popen(..., text=True)` whose first `communicate(timeout=1)` times out, and whose second `communicate(timeout=2)` times out too, raises a `TimeoutExpired` whose `stdout` is `b'hi\n'`: bytes, and everything read across both waits. `stderr` is `None` when nothing was read. `proc.stdout.encoding` is `UTF-8` and `proc.stdout.errors` is `strict`.
- `rules.py` imports only the standard library and pydantic, so the existing lazy `from . import rules` inside a function is enough. Keep it lazy, as `LocalProcess.stop_tree` does.
- `sandbox.py` isn't in `test_sandbox.py`'s `IN_TREE` list, so a `subprocess.run(["ps", ...])` inside it breaks no test.

## Files to change

| File | Change |
|---|---|
| `.claude/skills/sssf/templates/adws/adw_modules/sandbox.py` | The three behaviour changes and the comment. |
| `.claude/skills/sssf/tests/test_sandbox.py` | New tests for the bar. |
| `docs/open.md` | Drop cause (2) from the OpenShell VM row and renumber. |

Do not touch `.claude/skills/sssf/templates/adws/adw_modules/rules.py` (protected; call `rules.stop_tree` as it is), `adws/adw_modules/sandbox.py` (the hub's installed copy, protected; see "For the engineer"), `quality.py`, `plan_checks.py`, or `app_docs/`.

## Step 1: `.claude/skills/sssf/templates/adws/adw_modules/sandbox.py`

Everything below sits between `LocalProcess` and `Local.start`. Leave the module docstring, the protocols, `LocalProcess`, `Local.start` and all file methods as they are.

### 1a. `_kill_group`: a refused group kill falls back to one pid at a time

Replace the current function, which swallows `PermissionError`:

```python
def _group_pids(pgid: int) -> list[int]:
    """Every process whose process group is `pgid`, from one `ps` listing."""
    try:
        listing = subprocess.run(["ps", "-A", "-o", "pid=", "-o", "pgid="],
                                 capture_output=True, text=True).stdout
    except OSError:
        return []
    pids = []
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1] == str(pgid):
            pids.append(int(parts[0]))
    return pids


def _kill_group(proc: subprocess.Popen) -> None:
    """Kill the process group `proc` leads, and so everything it started that
    stayed in it. An OpenShell VM's seccomp filter refuses every kill with a
    negative pid (EPERM), so a refused group kill kills each member by its own
    pid, which the VM allows."""
    import signal
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        for pid in _group_pids(proc.pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
```

- `ProcessLookupError` from `os.killpg` stays ignored.
- In the per-pid loop both errors are ignored, as `rules.stop_tree` does: one process that can't be killed must not stop the rest, or replace the `TimeoutExpired`.
- Use `os.kill`, never `os.killpg`, in the fallback. The tests patch `os.killpg` to raise.

### 1b. `_stop`: descendants by pid first, then the group

```python
def _stop(proc: subprocess.Popen) -> None:
    """Kill the command and everything it started. The tree first, by pid,
    while each parent is still there to be found through: a child that called
    setsid has left the group, and is reachable only through its parent. Then
    the group, for anything started since the listing."""
    from . import rules
    rules.stop_tree(proc.pid)
    _kill_group(proc)
```

Call it as `rules.stop_tree(...)`, through the module attribute, so a test can patch `rules.stop_tree`. The order is fixed: `stop_tree` before `_kill_group`.

### 1c. `_read`: what a timed-out wait had read, in the caller's type

```python
def _read(data, stream, text: bool):
    """What a timed-out communicate had read from `stream`: bytes, even with
    text=True. As str for text=True, decoded and with newlines translated as
    Popen does; as bytes otherwise. Nothing read is empty, not None."""
    if not text:
        return data or b""
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    encoding = getattr(stream, "encoding", None) or "utf-8"
    errors = getattr(stream, "errors", None) or "strict"
    try:
        decoded = data.decode(encoding, errors)
    except UnicodeDecodeError:      # a read cut off inside a character
        decoded = data.decode(encoding, "replace")
    return decoded.replace("\r\n", "\n").replace("\r", "\n")
```

Two decisions here that the brief leaves open:

- **Nothing read is `""` or `b""`, not `None`.** That matches what the second `communicate` returns when it succeeds, and the brief says "not `None`".
- **A decode error falls back to `errors="replace"`.** The bytes can end inside a multi-byte character, and a `UnicodeDecodeError` must not replace the `TimeoutExpired`.

Don't use `Popen._translate_newlines`; it is private.

### 1d. `Local.run`

Replace the two `except` bodies and the comment. The `Popen(...)` call's arguments don't change.

```python
        # With a timeout, the command gets its own process group, and a timeout
        # or an interrupt kills everything it started: its descendants by pid,
        # so a child that left the group with setsid dies too, then the group,
        # one pid at a time where a group kill is refused (an OpenShell VM).
        # subprocess.run kills only the process it started, so a check run as
        # `uv run pytest` left pytest running: one hung for 4.5 hours after
        # guard run 0ece22a5's verify. The TimeoutExpired holds what the
        # command printed, str with text=True, even when something that got
        # away keeps the pipes open past the 5 s second wait.
        # stdin is DEVNULL, since a process outside the terminal's session
        # can't read it anyway.
        with subprocess.Popen(...) as proc:
            try:
                out, err = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                _stop(proc)
                try:
                    out, err = proc.communicate(timeout=5)
                except subprocess.TimeoutExpired as late:   # something got away with the pipes
                    out = _read(late.output, proc.stdout, text)
                    err = _read(late.stderr, proc.stderr, text)
                raise subprocess.TimeoutExpired(command, timeout, output=out, stderr=err) from None
            except BaseException:
                _stop(proc)
                raise
        return subprocess.CompletedProcess(command, proc.returncode, out, err)
```

- The second `communicate`'s `TimeoutExpired` holds everything read across both waits, so read `late`, not the first exception.
- The raised exception keeps `timeout`, the caller's value, not 5.
- `_read` runs inside the `with` block, before the pipes close.

## Step 2: `.claude/skills/sssf/tests/test_sandbox.py`

Add the tests below after `test_a_timed_out_command_leaves_nothing_it_started_running`. Leave that test and every other existing test as it is. Reuse the file's `_alive` helper. Add `import shutil` and `import signal` at the top if they aren't there, and `from adw_modules import rules` beside the existing `adw_modules` import.

Add two small helpers so no test leaves a `sleep` behind:

```python
def _dead_within(pid: int, seconds: float) -> bool:
    for _ in range(int(seconds * 10)):
        if not _alive(pid):
            return True
        time.sleep(0.1)
    return not _alive(pid)


def _reap(pidfile: Path) -> None:
    """Kill what a test's command left, pass or fail."""
    try:
        os.kill(int(pidfile.read_text()), signal.SIGKILL)
    except (OSError, ValueError):
        pass
```

Each test wraps its body in `try: ... finally: _reap(pidfile)`. A test that uses `setsid` starts with `if shutil.which("setsid") is None: pytest.skip("no setsid here")`.

1. **`test_a_timed_out_command_stops_a_child_that_left_its_group`** (the bar's first case, word for word)
   - Command: `["sh", "-c", f"echo started; setsid sleep 300 & echo $! > {pidfile}; wait"]`, `timeout=1`.
   - Time the call with `time.monotonic()`. Assert `TimeoutExpired` is raised and the call took under 7 s.
   - Assert `"started" in raised.value.stdout`, with `stdout` a `str`.
   - Assert `_dead_within(child, 5)`.
   - On `main` this fails: `stdout` is `None` and the child lives.

2. **`test_a_refused_group_kill_still_stops_the_command_and_keeps_its_output`** (the bar's second case)
   - `monkeypatch.setattr(os, "killpg", refuse)` where `refuse` raises `PermissionError(1, "Operation not permitted")`.
   - Command: `["sh", "-c", f"echo started; sleep 300 & echo $! > {pidfile}; wait"]`, `timeout=1`.
   - Assert `"started" in raised.value.stdout` and `_dead_within(child, 5)`.
   - The shell is dead too: `Popen.__exit__` waits for it, so the call returning at all proves it. Assert the call took under 7 s to pin that.
   - On `main` the output is `None`.

3. **`test_a_refused_group_kill_kills_each_group_member_by_its_own_pid`**
   - Case 2 passes through `stop_tree` alone, so this test pins change 3 by itself. Patch `os.killpg` as in case 2, and also `monkeypatch.setattr(rules, "stop_tree", lambda pid: None)`.
   - Same command and asserts as case 2. Only `_kill_group`'s fallback can kill the shell and the `sleep` here.

4. **`test_output_survives_a_child_that_got_away_with_the_pipes`**, parametrized over `text` in `(True, False)`
   - This pins change 1 by itself: the second wait really times out. Needs `setsid`.
   - Command: `["sh", "-c", f"echo started; (setsid sleep 30 & echo $! > {pidfile}); sleep 300"]`, `timeout=1`, `text=text`. The subshell exits at once, so the `sleep 30` belongs to no parent in the command's tree and to another session: neither kill reaches it, and it holds the pipes.
   - Assert `raised.value.stdout == ("started\n" if text else b"started\n")`, and that `raised.value.stderr` is `""` or `b""` of the same type, not `None`.
   - Each case takes about 6 s (1 s, then the 5 s second wait). `_reap` kills the `sleep 30`.
   - On `main` `stdout` is `None`.

5. **`test_a_timed_out_command's_output_is_its_caller's_type`** (name it `test_timed_out_output_is_str_for_text_and_bytes_otherwise`), parametrized over `text` in `(True, False)`
   - Command: `["sh", "-c", "echo started; sleep 300"]`, `timeout=1`, `text=text`. No pidfile needed; the group kill ends it.
   - Assert `isinstance(raised.value.stdout, str if text else bytes)` and that it holds `started`.

Give each test a one- or two-line docstring saying what broke, in the style of the tests around it.

## Step 3: `docs/open.md`

One row, the one that starts "The hub's suite still fails in an OpenShell VM". Keep it one table row on one line. Change only these four things in it:

1. Delete the whole cause (2) passage, from `(2) **A timed-out command's output is lost: 3 tests.**` through `[Its brief](../specs/tasks/timeout-keeps-output.md) goes to an SSSF run.` inclusive.
2. In its place put one sentence, so the row's counts still add up to 16: `The 3 tests in `test_plan_checks.py`, `test_quality_tally.py` and `test_sandbox.py` that lost a timed-out command's output were fixed in `sandbox.py` by run 9f8417b9, not yet rerun in a VM.`
3. Renumber: `(3) **`test_sandbox_remote.py`'s` becomes `(2)`, and `(4) **Five tests that need a closer look.**` becomes `(3)`.
4. The earlier sentence "Run 4a74c2b9 never reached the files behind causes (2) and (3), and reached only one of cause (1)'s." refers to the old numbers. Old (2) is gone and old (3) is now (2), so it becomes "Run 4a74c2b9 never reached the files behind cause (2), and reached only one of cause (1)'s."

Leave every other row and every other sentence in this row alone.

## Docs this change makes out of date

- The comment above the `Popen` in `Local.run`: rewritten in step 1d.
- `docs/open.md`: step 3.
- No README, help text or reference page describes `Local.run`'s timeout. The repo keeps no CHANGELOG.
- `quality.py` and `plan_checks.py` each carry a comment that a timed-out process's output can be bytes even with `text=True`. Both stay true for the remote runners and both still handle `str`, so leave them.
- For the documenter: no write-up in `app_docs/` describes the group kill, so none goes out of date.

## Verification

Run from the repo root. The repo's own test command runs the whole skill suite, so the new tests need no extra check.

1. The three files the brief names, with the suite's own runner and flags:

   ```
   uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_sandbox.py tests/test_quality_tally.py tests/test_plan_checks.py -q -p no:cacheprovider -m 'not pushes and not host_only'
   ```

   Judge it by its exit status. Do not run the whole suite; verify does that.

   **This may not start for you.** When I ran it under agentsh in this worktree it exited 2 at collection: `utils.py`'s `load_dotenv()` walks up out of the worktree to the hub checkout's `.env`, and agentsh denies the open (`PermissionError: [Errno 13] ... /home/gpaul/github/super-simple-software-factory/.env`). That deny is the secret-file guard working. If you get the same error, do not work around it: don't create a `.env` in the worktree, don't change `utils.py` or `conftest.py`, don't set variables to redirect dotenv. Use check 2 and leave the three files to verify, which SSSF runs outside agentsh.

2. A probe that needs no `.env`, since `sandbox.py` and `rules.py` don't import `utils`. Put it in a temp file outside the repo with your file tool and run it with `uv run --with pydantic python <file>`, with `.claude/skills/sssf/templates/adws` on `sys.path`. It should cover the bar:
   - the `setsid` command from test 1: raises within 7 s, `stdout` holds `started`, the child is dead within 5 s;
   - `os.killpg` patched to raise `PermissionError`: `started` in `stdout`, the `sleep` dead;
   - the got-away command from test 4 with `text=True` and `text=False`: `stdout` is `"started\n"` and `b"started\n"`.
   Kill any `sleep` it leaves, and delete the probe file afterwards.

3. `git diff --stat` shows exactly three files: `sandbox.py` under the templates, `tests/test_sandbox.py`, and `docs/open.md`.

4. Verify then runs the repo's checks: the whole skill suite, `chain_test`, the visualizer tests and the typecheck. All pass, including `test_sandbox.py::test_a_timed_out_command_leaves_nothing_it_started_running`, `test_quality_tally.py::test_a_check_that_times_out_reports_exit_124_with_its_output` and `test_plan_checks.py::test_a_check_that_times_out_is_reported_with_its_output`.

## Out of scope

- The other VM-only failures in the same `open.md` row.
- `trial.py`'s own `start_new_session` process.
- The remote runners' timeouts.
- `Local.start` and `LocalProcess`.

## For the engineer

- **The hub's installed copy.** `adws/adw_modules/sandbox.py` holds the same `_kill_group` and is protected, so the builder leaves it. It gets the fix when you update the hub's install after the merge, as 34f2f23 did for the last one.
- **The three test files don't collect under agentsh in a nested worktree.** The command in Verification check 1 exits 2 at collection for the planner, because `load_dotenv()` in `utils.py` finds the hub checkout's `.env` above `adws/adw_data/worktrees/9f8417b9` and agentsh denies the read. The brief expects the builder to run those three files; it most likely can't, and the plan tells it not to work around the deny. Verify still runs them. If you want agents to run skill tests in a worktree, the fix is yours to choose (for example an agentsh rule, or `utils.py` not walking above the repo root), and it isn't part of this change.
- **Not rerun in a VM.** The plan's tests patch `os.killpg` to stand in for the VM's seccomp refusal. The `open.md` sentence says so. A VM rerun of the three tests would confirm that `ps -A -o pid= -o pgid=` and a kill by pid both work there.
- **Two choices the brief left open**, both in step 1c: nothing read comes back as `""` or `b""`, not `None`, and bytes that end inside a character decode with `errors="replace"` so the `TimeoutExpired` is never replaced by a `UnicodeDecodeError`.
