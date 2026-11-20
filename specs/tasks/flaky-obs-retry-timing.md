# Make the obs relay's timed-out-post test pass under load

Run it with `--worktree`, on the hub's own config. The test and the module it tests are skill code, not guard code.

## What broke

`tests/test_obs_relay.py::test_a_post_that_ran_out_of_time_but_landed_isnt_called_rejected` failed once in a run of four test files on 2026-11-20 (`test_agent_pi.py`, `test_agentsh_guard.py`, `test_obs_relay.py`, `test_trial.py`), at its last line:

```
assert fwd.counts() == {"posted": 1, "rejected": 0, "dropped": 0, "unposted": 0}
```

Alone it passed 3 of 3. Under CPU load, with twice `nproc` busy loops running, it failed 2 of 12 runs.

The test sets `POST_SECONDS` to 0.3 and `RETRY_SECONDS` to 0.2, and makes the fake obs hold each post 0.6 seconds, so the live post times out but still lands. It then sets the delay to 0, sleeps a fixed 0.5 seconds for the live retry, which obs calls a repeat, and calls `finish`.

One fix has been tried and didn't work. Replacing the sleep with a wait for `len(obs.batches) >= 2 and fwd.counts()["posted"] == 1`, up to 20 seconds, still failed 2 of 20 runs under the same load. So the race isn't only the retry arriving late. Find where it is.

## The change

1. Find the race. Keep the failing counts and the forwarder's warnings from a run that goes red under load, and say in the spec which step loses the race: the live post's timeout, the retry, the answer's handling, or `finish`.
2. If the race is in the test, make the test wait for the state it needs instead of a fixed time.
3. If it's in `obs_relay.py`, so a real forwarder can count a landed post wrongly on a slow machine, fix it there and add a test that drives the slow path without depending on load.
4. Leave the other tests in the file alone, apart from shared helpers the fix needs.

## The bar

- **Under load.** The test passes 20 of 20 runs with twice `nproc` busy loops running. The spec gives the loop command, so the engineer can run it again.
- **The red is real.** The same loop on `main`'s version of the test fails at least once in 20 runs, or the spec shows a deterministic test that fails on `main`.
- **Nothing else changes.** Every existing test passes.

## Running the tests

Run `tests/test_obs_relay.py` from `.claude/skills/sssf`, and leave the whole suite to verify. The test is marked `host_only`. Stop the busy loops when the load runs end.

## Out of scope

- The other flaky tests in the `docs/open.md` row.
- The relay's 413 behaviour (`sssf_obs_relay.cjs`).
