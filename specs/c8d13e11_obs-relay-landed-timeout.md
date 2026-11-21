# Count a landed post right when its own socket timeout fires first

## The request

`tests/test_obs_relay.py::test_a_post_that_ran_out_of_time_but_landed_isnt_called_rejected` (in `.claude/skills/sssf`) fails under CPU load. Find the race, fix it where it is, and prove the fix under load.

## Where the race is

**The live post's timeout loses the race, and it is in `obs_relay.py`, not the test.** A real forwarder on a slow machine can count a landed post as rejected.

`Forwarder._post` in `.claude/skills/sssf/templates/adws/adw_modules/obs_relay.py` runs each post in a worker thread and gives the same number of seconds to two clocks:

- `_OPENER.open(req, timeout=seconds)`, the socket's timeout, inside the worker;
- `worker.join(seconds)`, the forwarder's own wait.

Only the second one remembers that the post may have landed:

```python
worker.join(seconds)
if worker.is_alive():
    with self._lock:
        self._maybe.update(event_id for event_id, _ in batch)   # only here
    self._note("post", f"obs at {self.target.server} didn't answer in {seconds:.0f}s")
    return "down", []
...
if "error" in out:                                              # the socket's timeout ends here
    self._note("post", f"obs at {self.target.server} couldn't be reached: {out['error']}")
    return "down", []
```

On a quiet machine the join started first, so it ends first, the worker is still alive, and the ids go into `_maybe`. Under load the thread that called `join` is woken late, the worker's socket timeout has already raised `TimeoutError`, the worker is dead, and `_post` takes the `"error"` branch. The ids never reach `_maybe`. The request had gone, so obs stores the event. The retry gets the id back as rejected, `_send` finds it isn't in `_maybe`, and counts it rejected.

This is why waiting for `posted == 1` for 20 seconds didn't help: once the first post takes the wrong branch, `posted` never becomes 1. Neither the retry, the answer's handling, nor `finish` is at fault; each does the right thing with a wrong `_maybe`.

### Evidence

The planner ran under agentsh, which denies loopback connections (`PermissionError(13)` on `connect` to `127.0.0.1`). That is why this test is marked `host_only`, and it means **the planner could not run the real test, and has no failing counts from the real test under load.** What it has instead:

1. **A simulation under load, with no socket.** `obs_relay._OPENER` was swapped for a stub whose first `open` sleeps for the `timeout` it is given and then raises `TimeoutError`, as a socket does, after storing the event; later posts get obs's answer with the stored id rejected. `POST_SECONDS` 0.3 and `RETRY_SECONDS` 0.2 as in the real test, 32 busy loops on 16 cores. Against the module as it is on `main`, 7 of 20 runs were red, every one with the same counts and warnings:

   ```
   {'posted': 0, 'rejected': 1, 'dropped': 0, 'unposted': 0}
   [('post', "obs at http://obs.invalid couldn't be reached: TimeoutError"),
    ('rejected', 'obs rejected 1 event(s) the relay sent')]
   ```

   The 13 green runs took the other branch (`didn't answer in 0s`). The stub's sleep starts sooner than a real socket's timeout does (no connect, no send), so it loses the race more often than the real test's 2 of 12; the mechanism is the same.

2. **A deterministic test, red on `main`.** The first new test below fails on `main`'s module every time, on a quiet machine, with `{'posted': 0, 'rejected': 1, ...}` against `{'posted': 1, 'rejected': 0, ...}`.

3. **The fix, tried on a copy.** With the change below applied to a copy of the module in `/tmp`, both new tests and the simulation passed 20 of 20 under the same load.

What an engineer's red run of the real test should show, if this is right: `rejected: 1, posted: 0`, and a `post` warning that says `couldn't be reached: TimeoutError` instead of `didn't answer in 0s`. If a red run on `main` shows anything else, there is a second race and this plan hasn't found it.

## The change

Four files. The first two are under `.claude/skills/sssf/`, which the hub's builder may write; neither is on the guard list.

### 1. `.claude/skills/sssf/templates/adws/adw_modules/obs_relay.py`

In `Forwarder._post`:

- In the worker's `call()`, add a branch between the `HTTPError` one and the `BaseException` one. `HTTPError` is a subclass of `URLError`, so the order matters:

  ```python
  except urllib.error.HTTPError as error:
      out["code"] = error.code
  except urllib.error.URLError as error:      # urllib raises it only when the request didn't go
      out["error"], out["unsent"] = type(error).__name__, True
  except BaseException as error:
      out["error"] = type(error).__name__
  ```

  urllib's `do_open` wraps an `OSError` from `h.request(...)` (connect, and sending the headers and body) in `URLError`. An error from `h.getresponse()` or from `res.read()` comes out bare: `TimeoutError`, `ConnectionResetError`, `RemoteDisconnected`, `IncompleteRead`, a JSON error on the answer. Those all mean the request went, so obs may have stored it.

- In the `"error" in out` branch, before the note, remember the ids unless the request never went:

  ```python
  if "error" in out:
      if not out.get("unsent"):           # the request went, so it may have landed
          with self._lock:
              self._maybe.update(event_id for event_id, _ in batch)
      self._note("post", f"obs at {self.target.server} couldn't be reached: {out['error']}")
      return "down", []
  ```

  `_post` is called from `_send` outside `self._lock`, as the existing `_maybe.update` in the `is_alive()` branch shows, so taking the lock here is safe.

- Leave the warning texts, the `"code"` branch (obs answered, so it said what happened) and the `is_alive()` branch as they are.
- Docs in the file: change the `_maybe` field's comment from `sent by a post that ran out of time` to say it also holds posts whose answer was lost after the request went; say the same in `_post`'s docstring and in the comment above `landed = ...` in `_send`.

### 2. `.claude/skills/sssf/tests/test_obs_relay.py`

Add a socket-free stand-in for obs and two tests, placed just after the existing timed-out test. They need no `host_only` mark: they open no socket, so they run under agentsh, and the builder can run them. This is the code the planner ran; use it as it is unless the file's helpers have changed.

```python
class _Answer:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


class StubObs:
    """Obs with no socket, in place of obs_relay._OPENER: `fail` is what the
    first post raises, after obs stored its events if `lands`. Later posts get
    obs's answer, which rejects an event_id already stored."""

    def __init__(self, fail: Exception, lands: bool):
        self.fail, self.lands = fail, lands
        self.stored: list[str] = []
        self.posts = 0

    def open(self, req, timeout=None):
        self.posts += 1
        ids = [e["event_id"] for e in json.loads(req.data)]
        if self.posts == 1:
            if self.lands:
                self.stored += ids
            raise self.fail
        rejected = [i for i in ids if i in self.stored]
        self.stored += [i for i in ids if i not in rejected]
        return _Answer(json.dumps({"ingested": len(ids) - len(rejected), "rejected": rejected}).encode())


def _stub_forwarder(monkeypatch, stub):
    from adw_modules import obs_relay
    monkeypatch.setattr(obs_relay, "_OPENER", stub)
    monkeypatch.setattr(obs_relay, "RETRY_SECONDS", 0.05)
    monkeypatch.setattr(obs_relay, "LIVE_SECONDS", 0.05)
    return obs_relay.Forwarder(obs_relay.Target(server="http://obs.invalid", token="obs-secret"))


def test_a_post_whose_own_timeout_fired_first_but_landed_isnt_called_rejected(monkeypatch):
    # The socket's timeout and the forwarder's wait for the post thread get the
    # same seconds. Under load the socket's fires first, and the post ends as an
    # error instead of a thread left running.
    stub = StubObs(TimeoutError("timed out"), lands=True)
    fwd = _stub_forwarder(monkeypatch, stub)
    fwd.live()
    fwd.feed(_line(_event(1)))
    _wait_for(lambda: stub.posts >= 2 and sum(fwd.counts().values()), seconds=20)
    fwd.finish(_line(_event(1)))
    assert "post" in _kinds(fwd)
    assert fwd.counts() == {"posted": 1, "rejected": 0, "dropped": 0, "unposted": 0}
    assert "rejected" not in _kinds(fwd)


def test_a_post_that_never_left_and_obs_then_rejects_is_called_rejected(monkeypatch):
    import urllib.error
    stub = StubObs(urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")), lands=False)
    stub.stored.append("e1")                    # obs holds the id from somewhere else
    fwd = _stub_forwarder(monkeypatch, stub)
    fwd.live()
    fwd.feed(_line(_event(1)))
    _wait_for(lambda: stub.posts >= 2 and sum(fwd.counts().values()), seconds=20)
    fwd.finish(_line(_event(1)))
    assert fwd.counts() == {"posted": 0, "rejected": 1, "dropped": 0, "unposted": 0}
    assert "rejected" in _kinds(fwd)
```

Neither depends on load: the first post fails at once with the error the slow path produces, and each wait is for a state, capped at 20 seconds. The first is red before the module change and green after. The second is green both before and after; it keeps the fix from marking every failed post as maybe landed.

Write the two tests first and run them before touching the module, so the first one's red is seen (see Verification).

Then make the existing `test_a_post_that_ran_out_of_time_but_landed_isnt_called_rejected` wait for state instead of sleeping. With the module fixed, its counts are right whichever clock wins, but two fixed times remain: the 0.5 second sleep, and the 0.3 second `POST_SECONDS` that the retry and the sweep also get, which a loaded machine can miss for a post that isn't meant to time out. Replace its body from `fwd.live()` on with:

```python
    fwd.live()
    fwd.feed(_line(_event(1)))                         # the live post times out, and lands
    _wait_for(lambda: obs.events and "post" in _kinds(fwd), seconds=20)
    obs.delay = 0
    monkeypatch.setattr(obs_relay, "POST_SECONDS", 30)  # only the first post is meant to run out of time
    _wait_for(lambda: len(obs.batches) >= 2 and fwd.counts()["posted"] == 1, seconds=20)   # the live retry: obs calls it a repeat
    fwd.finish(_line(_event(1)))
    assert fwd.counts() == {"posted": 1, "rejected": 0, "dropped": 0, "unposted": 0}
```

and drop the test's `import time`, which nothing uses after that. Keep its `host_only` mark and the comment above it. If the retry starts before the test has set the delay to 0, it times out and lands as well, its ids are in `_maybe`, and the next retry still counts 1 posted, so no margin is left to lose.

**The planner could not run this rewritten test**, since it opens a socket. The builder, under agentsh, can't either. Its proof is the engineer's loop below and verify's whole-suite run.

Change no other test in the file. `_wait_for`, `_kinds`, `_line` and `_event` are used as they are.

### 3. `docs/designs/remote-sandboxes.md`

In the list of what the forwarder does (about line 222), the bullet `A post that ran out of time but landed isn't counted as rejected when it goes again.` describes old behaviour once this ships. Reword it to cover both ways a post can end unanswered: by the forwarder's wait, or by the socket's own timeout or a lost answer. Say too that a post that never left (connection refused) is still counted rejected if obs rejects it later.

### 4. `docs/open.md`

The row that starts `Three skill tests that flaked` ends with `A fourth test flakes under load: ... [its brief](../specs/tasks/flaky-obs-retry-timing.md) goes to an SSSF run`. Replace that last sentence's ending with the cause and the fix in two or three sentences: both clocks got the same seconds, the socket's fired first under load, the ids missed `_maybe`, and the retry was counted rejected; `_post` now remembers the ids whenever the request went. Say the 20-run loop under load is still the engineer's to run, unless the builder was able to run it. Leave the rest of the row, and its status, alone: the other flaky tests are out of scope.

### Not changed

- No README, CHANGELOG, help text or config reference names this behaviour. `references/config.md` and the two cookbooks mention the relay only by file name.
- `sssf_obs_relay.cjs` and the relay's 413 behaviour.
- Nothing in `app_docs/`. No write-up there describes `_maybe`; the documenter writes this change up after the build.

## Verification

All from `.claude/skills/sssf`. `RUN` stands for the repo's own runner:

```
uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest
```

1. **Red first (builder).** After adding the two stub tests and before changing `obs_relay.py`:

   ```
   RUN tests/test_obs_relay.py -q -k "whose_own_timeout_fired_first or never_left"
   ```

   Expect 1 failed, 1 passed, the failure showing `{'posted': 0, 'rejected': 1, ...}`. This is the bar's deterministic test that fails on `main`.

2. **Green after (builder, and SSSF's check).** The same command after the module change: 2 passed, exit 0.

3. **The rest of the file (builder).** Under agentsh:

   ```
   RUN tests/test_obs_relay.py -q -m "not pushes and not host_only"
   ```

   All pass. Outside agentsh, run the file with no `-m`.

4. **Under load, 20 of 20 (outside agentsh).** The builder can't run this if `python3 -c "import socket; s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen(1); socket.socket().connect(s.getsockname())"` exits non-zero; in that case say so in the report and leave it to the engineer. The loop:

   ```bash
   cd .claude/skills/sssf
   n=$(( $(nproc) * 2 )); pids=""
   for i in $(seq $n); do ( while :; do :; done ) & pids="$pids $!"; done
   red=0
   for i in $(seq 20); do
     uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich \
       pytest tests/test_obs_relay.py -q -p no:cacheprovider \
       -k "ran_out_of_time_but_landed or whose_own_timeout_fired_first or never_left" || red=$((red+1))
   done
   kill $pids; echo "$red red of 20"
   ```

   Expect `0 red of 20`. Always run the `kill $pids` line, also after an interrupted loop.

5. **The whole suite** is verify's, with every existing test passing.

## For the engineer

- **Run the load loop** in step 4 on the host, since no agent under agentsh can open the test's loopback socket. To see the real test's red as well, run the same loop on `main` with `-k ran_out_of_time_but_landed`. The plan predicts a red run there shows `rejected: 1, posted: 0`; a different red means a second race.
- **`adws/adw_modules/obs_relay.py`**, the hub's own installed copy, is protected and stays as it is until the hub's install is next updated from the skill. It is identical to the template today, so until then the hub's own VM runs can still count a landed post as rejected on a slow post. The count is the only harm: the event is in obs either way.
- **Not fixed, by choice:** a 5xx, 408 or 429 answer still doesn't put the ids in `_maybe`. Obs answered and said it failed, so the forwarder believes it.
