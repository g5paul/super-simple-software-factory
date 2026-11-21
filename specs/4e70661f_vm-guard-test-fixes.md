# Make four guard tests hold in an OpenShell VM

## Read this first: the builder cannot make the test changes

The request is a change to two test files:

- `.claude/skills/sssf/tests/test_git_dir_guard.py`
- `.claude/skills/sssf/tests/test_agentsh_guard.py`

Both are in `protected_files` (`adws/adw_sssf_config/sssf.config.yaml`, lines 75 and 83). This
run is `adws/adw_simple_sdlc.py`, whose build agent is `builder`. Its `writes` are `**` and
`.claude/skills/sssf/`, and the config says that unlock "no longer reaches the shipped guard
paths". Only `guard_builder` names the two files, and only `adws/adw_guard_change.py` uses
`guard_builder`. If `builder` edits either file, SSSF stops the run.

So the plan has two parts:

1. **Builder steps**: one true sentence in `docs/open.md`. Nothing else.
2. **`## For the engineer`**, at the end: the four test changes, written out in full, to be made
   by a rerun through `adws/adw_guard_change.py` or by hand. The `docs/open.md` wording the
   request asked for ("the four test fixes are done") goes with them, because it is only true
   once they land.

**Builder: do not edit either test file, and do not add a new test file or a `conftest.py`
change that stands in for them.** The protection is deliberate.

A second thing the builder must know: under agentsh, in this worktree, the two test files
cannot even be collected. Importing `adw_modules.utils` runs `load_dotenv()`, `find_dotenv`
walks up from the worktree (`adws/adw_data/worktrees/4e70661f`) to the main checkout's `.env`,
and agentsh denies the read:

```
PermissionError: [Errno 13] Permission denied: '/home/gpaul/github/super-simple-software-factory/.env'
ERROR tests/test_git_dir_guard.py
```

The planner saw this on 2026-11-21 with
`uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest -q -p no:cacheprovider tests/test_git_dir_guard.py -k ...`
from `.claude/skills/sssf`. Do not work around it (no `.env` copy, no patched `find_dotenv`).
The builder's change is to a Markdown table row and needs no test run. Verify, which is code
on the host, runs the suite.

## What the planner found

Read from the code at `9653202`. Nothing was timed, because of the collection failure above.

### Where the stalled listing's 12 s goes

`test_a_stalled_listing_still_restores` (`test_git_dir_guard.py:1225`) rewrites the test
home's `.gitconfig`, so the file exists before and after. `permissions._shared`
(`.claude/skills/sssf/templates/adws/adw_modules/permissions.py:1830-1831`) then lists both
versions, and evaluates both before it checks either for `None`:

```python
old = Counter() if b is None else _entries(held, p)
new = Counter() if a is None else _entries("-", p)
```

Each `_entries` call runs `_sh(_KEYS, source, path, str(KEYS_SECONDS), timeout=KEYS_SECONDS + 5)`
(`permissions.py:1743`). The real `_KEYS` script has its own watchdog, `( sleep "$t"; kill "$pid" )`,
which ends the listing after `KEYS_SECONDS`. The test replaces the whole script with `sleep 30`,
which has no watchdog and ignores `$3`. So only the outer `_sh` timeout stops it, at
`KEYS_SECONDS + 5` = 1 + 5 = 6 s, and that happens twice:

- 2 listings x 6 s = **12 s**, plus about 0.2 s for the guard, the pack and the restore. That is
  the engineer's 12.2 s on the host and 12.3 s alone in the VM.

The engineer's guess in the request is right.

### Where the 60 s under the full VM suite most likely goes

Not proven, only read from the code. 60 s is 2 x 30 s: each listing lasted the whole `sleep 30`.
`sandbox.run` (`.claude/skills/sssf/templates/adws/adw_modules/sandbox.py:150-164`) on a timeout
calls `_stop_command(proc)`, waits up to 5 s for the pipes, then leaves the `with subprocess.Popen`
block, which waits for the process to exit. If the kill doesn't land, that wait lasts until
`sleep 30` ends by itself: 30 s a listing. So under the full VM suite the kill of the timed-out
listing was probably refused or lost. Alone in the VM it landed (12.3 s).

That means the test's 60 s in the full VM suite is a true failure of the thing it tests, not a
tight bound. The new bound must keep failing there, and the request says so: "the test still
fails if the listing waits for the whole `sleep 30`".

### The other three causes

The planner checked the code against the request's account and found nothing to add:

- **chmod.** `test_a_change_is_put_back_and_stops_the_run` (`test_git_dir_guard.py:90-113`) runs
  `sample.chmod(0o755)`. The `repo` fixture runs `git init` in a subprocess, which takes the
  process umask. With 0022 the samples are already 755.
- **Unnameable.** `require_unnameable` (`test_git_dir_guard.py:2137`) already has the probe:
  `permissions._sh('cd -P "$1" && pwd -P', f"{u.base}/A/B").stdout` starts with `/` when the
  path can be named. `A` alone resolves to about 2,200 characters, under PATH_MAX, so
  `where[f"{u.base}/A"][0].startswith("/")` holds in the VM too. Only `A/B` (about 4,400) needs
  `getcwd` to list every parent.
- **Non-UTF-8 child.** `test_a_stop_takes_a_child_with_a_non_utf8_name_and_its_own_child`
  (`test_agentsh_guard.py:1430-1452`) has no marker. It is not `host_only`.

## Builder steps

One file: `docs/open.md`.

In the table row that starts "The hub's suite still fails in an OpenShell VM" (line 56 at
`9653202`), cause (2) ends with this sentence, which appears once in the file:

> The test fixes are a run of their own.

Replace that one sentence with:

> The test fixes are specced and not yet made: run 4e70661f (2026-11-21) was a `simple-sdlc` run, and both test files are in `protected_files`, which only `guard_builder` in `adws/adw_guard_change.py` may write. The spec is `specs/4e70661f_vm-guard-test-fixes.md`; its "For the engineer" section has the four changes. The stalled listing's 12 s is two listings (the saved file and the live one) that each wait out the 6 s `_sh` timeout, because the test's `sleep 30` replaces the script that holds the `KEYS_SECONDS` watchdog. Its 60 s under the full VM suite is two whole `sleep 30`s, so the kill of the timed-out listing most likely didn't land there; not proven. Under agentsh in a worktree neither file can be collected: `adw_modules.utils` runs `load_dotenv()`, which finds the main checkout's `.env` above the worktree, and agentsh denies the read.

Rules for the edit:

- It is one table row on one line. Keep it one line, keep every other sentence in the row, and
  keep the row's last columns (`| Bug | P2 | Medium | Ready | 2026-11-20 | [openshell-sandbox.md](designs/openshell-sandbox.md) |`) as they are.
- The new text has no `|` character, which would break the table. Don't add one.
- Change no other row and no other file.

Other docs: none go out of date. The repo keeps no CHANGELOG. `docs/sssf.md`'s section on the
`pushes` and `host_only` markers stays true, because no marker changes. No `app_docs/` write-up
describes these tests' behaviour.

## Verification

For the builder's change, from the repo root:

1. `grep -c "The test fixes are a run of their own" docs/open.md` prints `0` (exit status 1 is
   what grep gives for no match, and is the pass here).
2. `grep -c "specs/4e70661f_vm-guard-test-fixes.md" docs/open.md` prints `1` and exits 0.
3. `git diff --stat` names `docs/open.md` only, with one line changed.
4. No file under `.claude/skills/sssf/templates/` or `.claude/skills/sssf/tests/` changed.

The repo's own verify (the skill suite, the chain tests, the typecheck) runs on the host and
must pass as it did at `9653202`. No test changes, so the count stays the same.

The request's bar (chmod under umask 0022, the two skips, the new bound, the `os.kill` skip)
is **not met by this run**. It is met when the section below is done.

## For the engineer

The four test changes need `guard_builder`. Two ways to land them:

- Rerun the brief (`specs/tasks/vm-test-fixes.md`) through
  `uv run adws/adw_guard_change.py specs/tasks/vm-test-fixes.md --worktree`. That chain plans
  again. Its planner can lift the code below.
- Or make the edits by hand from this section.

Either way the tests run on the host, outside agentsh (see the `.env` denial above). Command,
from `.claude/skills/sssf`:

```
uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest -q -p no:cacheprovider tests/test_git_dir_guard.py tests/test_agentsh_guard.py
```

No module under `.claude/skills/sssf/templates/` changes.

### 1. The chmod case: `.claude/skills/sssf/tests/test_git_dir_guard.py`, line 107

`stat` is already imported. Replace

```python
    elif plant == "chmod":
        sample.chmod(0o755)
```

with

```python
    elif plant == "chmod":
        # Flip the owner's execute bit, so the mode differs from the one git init gave the
        # sample under any umask: 775 with 0002, 755 with 0022 (an OpenShell VM's).
        sample.chmod(stat.S_IMODE(sample.stat().st_mode) ^ stat.S_IXUSR)
```

The rest of the test stays: `state(repo) == before` already compares modes, so it also proves
the old mode came back.

**Bar.** From `.claude/skills/sssf`:

```
sh -c 'umask 0022 && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest -q -p no:cacheprovider tests/test_git_dir_guard.py -k test_a_change_is_put_back_and_stops_the_run'
```

All 5 cases pass. On `main` the same command fails `[chmod]` with `DID NOT RAISE`. Run it once
more without the `umask 0022 &&` and see 5 pass again.

### 2. The two unnameable-dir tests: same file, lines 2137-2222

Add a helper beside `require_unnameable`, using the same probe:

```python
def require_nameable(u: Unnameable) -> None:
    """Skip when this machine can't name the path even unlocked. Past PATH_MAX, getcwd reads
    each parent directory up to /, and an OpenShell VM's policy refuses listing / and /var."""
    out = permissions._sh('cd -P "$1" && pwd -P', f"{u.base}/A/B").stdout
    if not out.startswith("/"):
        pytest.skip("this machine can't name the unlocked path: past PATH_MAX, getcwd must list "
                    "every parent directory up to /, and something here refuses that")
```

`test_real_of_an_unnameable_dir_is_marked`: the locked assertions stay first and still run on
every machine. Only the last, unlocked assertion gets the guard:

```python
    u.lock()
    require_unnameable(u)
    assert _real(u.inc) == "?"
    where = permissions._where([u.inc])
    assert where[f"{u.base}/A/B"][0] == ""
    assert where[f"{u.base}/A"][0].startswith("/")
    u.unlock()
    require_nameable(u)
    assert _real(u.inc) == u.physical
```

A skip raised after the locked assertions still reports a failure in them as a failure,
because an `assert` that fails raises before `require_nameable` is reached.

`test_a_prefix_that_becomes_unnameable_has_moved`: the test compares a nameable "before" with
an unnameable "after", so with no nameable "before" there is nothing to test. Skip at the top:

```python
    u = unnameable
    require_nameable(u)
    walked = [u.inc]
    guard = {"walked": walked, "where": permissions._where(walked)}
    assert guard["where"][f"{u.base}/A/B"][0].startswith("/")
    u.lock()
    require_unnameable(u)
    assert f"{u.base}/A/B" in permissions._moved(guard)
    assert f"{u.base}/A/B" in permissions._moved(guard, gone=False)
```

Both keep their `host_only` mark and its comment. The other three users of the `unnameable`
fixture don't assert on the unlocked path and don't change.

**Bar.** Both tests are `host_only`: run them on the host, outside agentsh.

- Working `_sh`: both pass, as now.
- `_sh` failing to name the unlocked path: both skip with the reason above. To show it without
  committing anything, write a throwaway plugin outside the repo, for example
  `/tmp/vmprobe/no_getcwd.py`:

  ```python
  import os, stat, subprocess
  import pytest

  @pytest.fixture(autouse=True)
  def _no_long_getcwd(monkeypatch):
      from adw_modules import permissions
      real = permissions._sh
      def fake(script, *args, **kwargs):
          done = real(script, *args, **kwargs)
          # The unlocked path only: the fixture's `long` directory is 755 when unlocked.
          if script == 'cd -P "$1" && pwd -P' and args and args[0].endswith("/A/B"):
              long_dir = args[0][:-len("/A/B")] + "/long"
              if stat.S_IMODE(os.stat(long_dir).st_mode) == 0o755:
                  return subprocess.CompletedProcess(done.args, 1, "", "getcwd() failed: Permission denied")
          return done
      monkeypatch.setattr(permissions, "_sh", fake)
  ```

  and run, from `.claude/skills/sssf`:

  ```
  PYTHONPATH=/tmp/vmprobe uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest -q -p no:cacheprovider -p no_getcwd -rs tests/test_git_dir_guard.py -k "test_real_of_an_unnameable_dir_is_marked or test_a_prefix_that_becomes_unnameable_has_moved"
  ```

  Expect `2 skipped`, each with "can't name the unlocked path". This fakes only the probe; in
  a real VM `_REAL` fails too, which is the engineer's proof after the merge.

### 3. The stalled listing: same file, lines 1225-1236

Keep the stall as it is and give the bound its parts. Replace the test body's timing lines so
the test reads:

```python
def test_a_stalled_listing_still_restores(repo, home, monkeypatch):
    import time
    run, guard = _guarded(repo)
    saved = (home / ".gitconfig").read_bytes()
    stall = 30
    monkeypatch.setattr(permissions, "_KEYS", f"sleep {stall}")
    monkeypatch.setattr(permissions, "KEYS_SECONDS", 1)
    (home / ".gitconfig").write_text("[user]\n\tname = other\n")
    # The file exists before and after, so _shared lists it twice: the saved version and the
    # live one. `sleep` has no watchdog of its own (the real _KEYS kills git after
    # KEYS_SECONDS), so each listing ends at _sh's timeout, KEYS_SECONDS + 5 = 6 s: 12 s in
    # all. 13 s more is room for a loaded machine. The bound stays under one whole stall, so
    # a listing nothing stopped still fails the test.
    allowed = 2 * (permissions.KEYS_SECONDS + 5)
    bound = allowed + 13
    assert bound < stall
    start = time.monotonic()
    msg = _breach(run, guard)
    assert time.monotonic() - start < bound
    assert "could not be listed" in msg
    assert (home / ".gitconfig").read_bytes() == saved
```

The bound goes from 15 s to 25 s: 12 s the listings are allowed, 13 s of room (2.8 s before),
and 5 s short of one `sleep 30`. Two whole stalls take 60 s and one takes at least 36 s, so
either still fails.

Why not make the stall cheaper instead: one listing instead of two needs the file deleted
rather than rewritten, which changes what the test covers (a rewritten shared config is the
common case), and it would only move the cost from 12 s to 6 s.

**Bar.** The bound and its comment are in the test. To see it fail when the listing waits out
the stall, run it once with the timeout made useless, without committing:
change `timeout=KEYS_SECONDS + 5` to `timeout=60` in a scratch copy of `permissions.py`, or
simpler, temporarily set `stall = 30` and `KEYS_SECONDS` to 40 in the test: `assert bound < stall`
fails at once (bound is 103). Put the test back afterwards.

Expect this test to keep failing under the full VM suite if the listing still takes 60 s
there. That is a real finding about the timed-out kill, not a test fault; see "Where the 60 s
... goes" above. The request's proof runs the five tests alone, where it took 12.3 s.

### 4. The non-UTF-8 child: `.claude/skills/sssf/tests/test_agentsh_guard.py`, line 1430

Add a helper above the test. It must send the signal with `os.kill`, so the bar's patch
reaches it, and it must wait for the name to change first: between fork and exec the
process is still named after Python, and OpenShell would let that signal through.

```python
OUTLIVES_ROW = "An agent's processes can outlive its call in a VM"


def require_signal_to_a_non_utf8_name(tmp_path) -> None:
    """Skip when this machine refuses a signal to a process whose name (/proc/<pid>/comm)
    isn't valid UTF-8. OpenShell does: os.kill and `kill -9` both get EPERM."""
    folder = os.fsencode(tmp_path) + b"/probe"
    os.mkdir(folder)
    script = folder + b"/\xff"
    with open(script, "wb") as f:
        f.write(b"#!/bin/sh\nread _\n")          # waits on stdin, so closing it ends the probe
    os.chmod(script, 0o755)
    probe = subprocess.Popen([script], stdin=subprocess.PIPE)
    try:
        deadline = time.time() + 5
        named = False
        while time.time() < deadline and not named:
            try:
                named = Path(f"/proc/{probe.pid}/comm").read_bytes() == b"\xff\n"
            except OSError:
                pass
            if not named:
                time.sleep(0.02)
        if not named:
            return                               # can't tell: run the test as before
        try:
            os.kill(probe.pid, signal.SIGKILL)
        except PermissionError:
            pytest.skip("this machine refuses a signal to a process whose name isn't valid UTF-8, "
                        f"as OpenShell does; see docs/open.md, \"{OUTLIVES_ROW}\"")
    finally:
        probe.stdin.close()                      # ends the probe when the kill was refused
        probe.wait(timeout=10)
```

Check the file's imports before adding it: it needs `os`, `signal`, `subprocess`, `time`,
`Path` and `pytest`. Add any that are missing (`signal` is the likely one).

Call it first in the test:

```python
def test_a_stop_takes_a_child_with_a_non_utf8_name_and_its_own_child(machine, service, tmp_path, monkeypatch):
    require_signal_to_a_non_utf8_name(tmp_path)
    bad = os.fsencode(tmp_path) + b"/\xff"
    ...
```

The rest of the test stays. The probe's script is in `tmp_path/probe`, so it doesn't collide
with the test's own `tmp_path/\xff`.

**Bar.**

- On the host, as now: the test runs and passes (not skipped). Check with `-rs` that the
  summary shows no skip for it.
- With `os.kill` refusing that one process: the test skips. A throwaway plugin outside the
  repo, for example `/tmp/vmprobe/no_kill.py`:

  ```python
  import os
  import pytest

  @pytest.fixture(autouse=True)
  def _refuse_non_utf8(monkeypatch):
      real = os.kill
      def fake(pid, sig):
          try:
              with open(f"/proc/{pid}/comm", "rb") as f:
                  name = f.read()
          except OSError:
              name = b""
          if name == b"\xff\n":
              raise PermissionError(1, "Operation not permitted")
          return real(pid, sig)
      monkeypatch.setattr(os, "kill", fake)
  ```

  ```
  PYTHONPATH=/tmp/vmprobe uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest -q -p no:cacheprovider -p no_kill -rs tests/test_agentsh_guard.py -k test_a_stop_takes_a_child_with_a_non_utf8_name_and_its_own_child
  ```

  Expect `1 skipped`, with the reason naming the `docs/open.md` row.

### 5. `docs/open.md`, once 1 to 4 have landed

In the same row, replace the builder's sentences from "The test fixes are specced and not yet
made" up to and including "not proven." with:

> The four test fixes are done (run `<adw_id>`): the `[chmod]` case flips the owner's execute bit, the two unnameable-dir tests skip when the unlocked path can't be named, the stalled listing's bound is 25 s (two listings at the 6 s `_sh` timeout, 13 s of room, under one `sleep 30`), and the non-UTF-8 child test now skips in a VM, where a probe finds the signal refused.

Keep the sentence about collection under agentsh in a worktree unless that has been fixed.
The row stays open: cause (1), the pi login tests, is untouched.

### 6. After the merge, the proof in a VM

As the request says, and not part of any run: in a throwaway VM, run the five tests alone and
see `[chmod]` and the stalled listing pass and the other three skip with their reasons.

### Out of scope, unchanged from the request

- Processes that outlive a VM call. The `docs/open.md` row stays as it is.
- The pi login tests.
- The VM policy's paths.
