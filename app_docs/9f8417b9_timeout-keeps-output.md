# Keep a timed-out command's output, and stop everything it started

## What changed and why

`sandbox.Local.run` used to lose a timed-out command's output in two cases, so `quality._run` reported only "Timed out after 1s." and the fix builder had nothing to act on:

1. **A child that left the process group.** The command runs in its own group, and a child that calls `setsid` leaves it. That child keeps the stdout/stderr pipes open, so the second `communicate(timeout=5)` times out and the code set `out, err = None, None`.
2. **A refused group kill.** In an OpenShell VM the seccomp filter refuses every `kill` with a negative pid, and `_kill_group` swallowed the `PermissionError`. Nothing died, the pipes stayed open, and the output was lost again.

The output was read in both cases. The `TimeoutExpired` raised by `communicate` already holds it, so the fix keeps it and stops the processes.

After this change, a timeout or interrupt:

- Keeps the output and stderr read so far. If the second wait also times out, the raised `TimeoutExpired` carries what was read across both waits, not `None`. With `text=True` they are `str`, decoded the way `Popen` decodes them (`\r\n` and `\r` become `\n`). With `text=False` they are `bytes`.
- Stops the command's descendants by pid through `rules.stop_tree(proc.pid)` before the group kill. A `setsid` child is still found through its parent. `rules.py` is unchanged; `stop_tree` is called from here only.
- Kills the group with `os.killpg`. If that raises `PermissionError`, it runs `ps -A -o pid= -o pgid=` and kills each process whose pgid is the command's, one pid at a time. `ProcessLookupError` is still ignored.

The call with no timeout is still a plain `subprocess.run`, and nothing else in `sandbox.py` changed.

## Files

- `.claude/skills/sssf/templates/adws/adw_modules/sandbox.py`: the change itself. New helpers `_stop_command`, `_decode`, and a fallback branch in `_kill_group`. `Local.run` calls `_stop_command` on a timeout and on any other exception, and raises `TimeoutExpired` with the output. The comment above `Popen` now describes what a timeout does.
- `.claude/skills/sssf/tests/test_sandbox.py`: new tests for each behaviour, listed below.
- `docs/open.md`: the row "The hub's suite still fails in an OpenShell VM" drops the timed-out-output cause and renumbers the rest. The row now says the three affected tests were fixed in `sandbox.py` by run 9f8417b9 and have not been rerun in a VM.
- `specs/9f8417b9_timeout-keeps-output.md`: the spec for this change (committed separately in `6aff4f3`).

The brief is `specs/tasks/timeout-keeps-output.md`, which the `open.md` row links to.

## Verify it

From `.claude/skills/sssf`:

```
pytest tests/test_sandbox.py tests/test_quality_tally.py tests/test_plan_checks.py -m 'not pushes and not host_only'
```

The tests that cover this change are in `tests/test_sandbox.py`:

- `test_a_timed_out_command_leaves_nothing_it_started_running`: the command and its children are dead after the timeout.
- `test_a_timed_out_command_stops_a_child_that_left_its_group`: a `setsid` child is dead within 5 s of the raise, and `stdout` holds `started`.
- `test_a_refused_group_kill_still_stops_the_command_and_keeps_its_output`: with `os.killpg` patched to raise `PermissionError`, the output is kept and the shell and its `sleep` are dead.
- `test_a_refused_group_kill_kills_each_group_member_by_its_own_pid`: the fallback kills each group member by pid.
- `test_output_survives_a_child_that_got_away_with_the_pipes`: the output survives a child holding the pipes.
- `test_timed_out_output_is_str_for_text_and_bytes_otherwise`: `str` with `text=True`, `bytes` otherwise.

The two `quality_tally` and `plan_checks` tests named in the brief are existing tests that the fix should make pass. They are not new.

A whole-suite run under agentsh takes many minutes, per `docs/open.md`, so run the whole suite only when verifying the full change.

## Not verified here

No test run is recorded for this write-up. The VM case (seccomp refusing negative-pid kills) is the one the brief names. The `open.md` row still says those three tests have not been rerun in a VM.

## Out of scope

- The other VM-only failures in the same `open.md` row (pi ChatGPT login, `test_sandbox_remote.py`, and the five tests that need a closer look).
- `trial.py`'s own `start_new_session` process.
- The remote runners' timeouts (`sandbox_remote.py`), which apply the timeout from the host.

## Notes for the next engineer

- The capture that produced this write-up's diff listed only `docs/open.md` and the spec. It excluded `.claude/skills/sssf/`, so the code change in `d4a8961` was not in it. This write-up was built from `git show d4a8961`.
- `_kill_group`'s fallback runs `ps`. If `ps` is missing or fails, the `OSError` path leaves the member list empty, and the output is still kept but the processes may survive.
- `_stop_command` swallows any exception from `rules.stop_tree`, so a failure there falls through to the group kill silently.
