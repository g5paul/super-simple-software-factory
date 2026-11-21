# Count a landed obs post when its own socket timeout fires first

## What was wrong

`test_a_post_that_ran_out_of_time_but_landed_isnt_called_rejected` (in `.claude/skills/sssf/tests/test_obs_relay.py`) failed under CPU load, at its last assert: `posted: 0, rejected: 1` instead of `posted: 1`. Waiting for the retry instead of sleeping didn't help, so the race was not in the test's timing.

The race is in `Forwarder._post` in `.claude/skills/sssf/templates/adws/adw_modules/obs_relay.py`. It gives the same number of seconds to two clocks:

- the socket's timeout, inside the worker thread (`_OPENER.open(req, timeout=seconds)`);
- the forwarder's own wait, `worker.join(seconds)`.

Only the join branch added the batch's ids to `_maybe`, the set of ids that may have landed. Under load the socket's timeout fired first. The worker died with `TimeoutError`, `_post` took the `"error"` branch, and the ids never reached `_maybe`. The request had already gone, so obs stored the events. The retry got them back as rejected, and `_send` counted them rejected.

**The step that loses the race is the live post's timeout.** The retry, the answer's handling and `finish` each behave correctly given the `_maybe` they are handed.

## What changed

- **`obs_relay.py`** (`Forwarder._post`, `_send`):
  - A new `except urllib.error.URLError` branch sets `unsent` on the result. urllib raises `URLError` only when the request didn't go, so this branch must sit before `BaseException`. `HTTPError` is a `URLError` subclass, and it is caught first.
  - The `"error"` branch adds the batch's ids to `_maybe` unless `unsent` is set. A post whose answer was lost after the request went now counts as possibly landed.
  - Comments and the `_maybe` docstring describe the new meaning. The warning texts and the `"code"` branch are unchanged.
- **`tests/test_obs_relay.py`**:
  - The existing timed-out test now waits for state (`"post" in _kinds(fwd)`, then the retry's `posted == 1`) instead of `time.sleep(0.5)`. Its `POST_SECONDS` is raised to 30 once the first post has timed out, so only the first post is meant to run out of time. Its `host_only` mark and comment are kept.
  - New socket-free `StubObs` (a fake for `obs_relay._OPENER`) and two tests that need no load and no socket:
    - `test_a_post_whose_own_timeout_fired_first_but_landed_isnt_called_rejected`: the first post raises `TimeoutError` after obs stored the ids. It is red on the old module and green on the new one.
    - `test_a_post_that_never_left_and_obs_then_rejects_is_called_rejected`: the first post raises `URLError` (connection refused), and obs then rejects. It should count rejected, both before and after the change. It guards against marking every failed post as maybe-landed.
- **`docs/designs/remote-sandboxes.md`** and **`docs/open.md`**: the bullet about posts that ran out of time, and the row about this test, now describe the new behaviour. The docs edits don't change the design or status.
- **`specs/c8d13e11_obs-relay-landed-timeout.md`**: the plan and evidence. It is the source for the reasoning above.

Supersedes: nothing in `app_docs/`. The bullet in `docs/designs/remote-sandboxes.md` (around line 222) and the flaky-tests row in `docs/open.md` describe the old behaviour, and this change replaces it.

## Hub copy not changed

`adws/adw_modules/obs_relay.py` (the hub's installed copy) is protected and is unchanged by this work. `git diff 2963d94 HEAD -- adws/adw_modules/obs_relay.py` is empty. Until the hub's install is next updated from the skill, the hub's own VM runs can still count a landed post as rejected on a slow machine. The count is the only harm; obs holds the event either way.

## How to verify

Run from `.claude/skills/sssf`:

```
uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_obs_relay.py -q -k "whose_own_timeout_fired_first or never_left"
```

Expect 2 passed. Then run the rest of the file, `-m "not pushes and not host_only"`, under agentsh, or the whole file without `-m` outside it.

The load bar is 20 of 20 runs with twice `nproc` busy loops. The spec gives the loop command (step 4 of its Verification section). It must be run outside agentsh, because the test needs a loopback socket. Stop the loops with `kill $pids` even if the loop is interrupted.

To check the red on `main`, run the same loop on `main`'s version of the test with `-k ran_out_of_time_but_landed`. A red run there should show `rejected: 1, posted: 0` and a warning of `couldn't be reached: TimeoutError`.

## What is not shown here

- **I did not run the tests or the load loop.** The results below come from the spec, which the planner recorded. They are not verified in this write-up:
  - in a simulation with no socket, 7 of 20 loaded runs were red on `main`;
  - the planner's fix passed 20 of 20 under the same load, on a copy in `/tmp`.
- The spec says the builder could not open loopback sockets under agentsh. The real test's 20-run loop is therefore still to be run by the engineer, on the host.
- The 5xx, 408 and 429 answers still don't add ids to `_maybe`. Obs answered and said it failed, so the forwarder trusts that answer. This is deliberate.
- The other flaky tests in the `docs/open.md` row, and the relay's 413 behaviour in `sssf_obs_relay.cjs`, are out of scope.
