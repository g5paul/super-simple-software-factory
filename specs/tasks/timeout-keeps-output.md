# Keep a timed-out command's output, and stop all of it

Run it with `--worktree` and `--sandbox none`, on the hub's own config. The change is in `templates/adws/adw_modules/sandbox.py`, which isn't guard code.

## What broke

When a command run through `sandbox.LOCAL.run` with a timeout prints and then hangs, the timeout can come back with none of what it printed. `quality._run` then reports only "Timed out after 1s.", and the fix builder has nothing to go on.

`Local.run` starts the command in its own process group. On a timeout it calls `_kill_group`, then waits up to 5 s more in a second `proc.communicate(timeout=5)`. If that second wait times out too, it sets `out, err = None, None` and raises `TimeoutExpired` with no output. Two things make the second wait time out:

1. **A process that left the group keeps the pipes open.** On the host, `sh -c "echo started; setsid sleep 20"` with `timeout=1` comes back after 6.0 s with `stdout` `None`, and the `setsid` child keeps running after the call returns. The engineer checked this on 2026-11-21.
2. **A group kill that fails.** In an OpenShell VM the seccomp filter (`Seccomp: 2`) makes every `kill` with a negative pid fail with `EPERM`, signal 0 included, and `_kill_group` swallows the `PermissionError`. Nothing dies, the shell's child keeps the pipes, and the output is lost. Killing each process by its own pid works in the VM. Three skill tests fail this way in a VM and pass on the host: `test_sandbox.py::test_a_timed_out_command_leaves_nothing_it_started_running`, `test_quality_tally.py::test_a_check_that_times_out_reports_exit_124_with_its_output` and `test_plan_checks.py::test_a_check_that_times_out_is_reported_with_its_output`.

The output was there all along. After the first `communicate` times out, the `TimeoutExpired` it raises already holds what was read (bytes, even with `text=True`), and a second `communicate` that times out raises one holding everything read across both waits.

In a real OpenShell run, quality and plan checks go through the remote runner, which applies the timeout from the host, so the VM half only breaks the hub's suite in a VM today. The lost output breaks any local run.

## The change

1. **Keep the output.** When the second wait times out, raise `TimeoutExpired` with the output and stderr read so far, not `None`. With `text=True` they are `str`, decoded the way `Popen` decodes; with `text=False`, bytes.
2. **Stop what left the group.** On a timeout or an interrupt, kill the command's descendants by pid with `rules.stop_tree`, before the group kill, so a child that called `setsid` is still found through its parent. Then kill the group.
3. **A group kill that's refused.** When `os.killpg` raises `PermissionError`, kill each process whose process group is the command's, one pid at a time. `ProcessLookupError` stays ignored.
4. **Docs.** The comment above the `Popen` in `Local.run` says what a timeout now does. In `docs/open.md`'s row that starts "The hub's suite still fails in an OpenShell VM", drop cause (2), the timed-out output, and renumber the rest.

Nothing else in `sandbox.py` changes, and the call with no timeout stays a plain `subprocess.run`.

## The bar

- **A child that left the group.** `LOCAL.run(["sh", "-c", f"echo started; setsid sleep 300 & echo $! > {pidfile}; wait"], timeout=1)` raises `TimeoutExpired` within 7 s. Its `stdout` holds `started`, and the `setsid` child is dead within 5 s of the raise. On `main` the same test fails.
- **A refused group kill.** With `os.killpg` patched to raise `PermissionError`, a timed-out `sh -c "echo started; sleep 300 & ...; wait"` still raises with `started` in its `stdout`, and the shell and its `sleep` are dead. On `main` the output is `None`.
- **Types.** With `text=True` the timed-out output is `str`, and with `text=False` it is `bytes`.
- **Nothing else changes.** Every existing test passes, the three named above among them.

## Running the tests

The builder and the planner run under agentsh. Run only `tests/test_sandbox.py`, `tests/test_quality_tally.py` and `tests/test_plan_checks.py` from `.claude/skills/sssf` with `-m 'not pushes and not host_only'`, and leave the whole suite to verify. A whole-suite run under agentsh makes the after-call event read take many minutes (`docs/open.md`).

## Out of scope

- The other VM-only failures in the same `open.md` row.
- `trial.py`'s own `start_new_session` process.
- The remote runners' timeouts.
