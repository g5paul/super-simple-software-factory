# Make four guard tests hold in an OpenShell VM

Five tests fail in an OpenShell VM and pass on the host. None is a guard bug. This plan changes
two protected test files and one docs row. No guard module changes.

Files to touch, all by `guard_builder`:

- `.claude/skills/sssf/tests/test_git_dir_guard.py` (protected; `guard_builder`'s `writes` names it)
- `.claude/skills/sssf/tests/test_agentsh_guard.py` (protected; `guard_builder`'s `writes` names it)
- `docs/open.md` (not protected)

Nothing under `.claude/skills/sssf/templates/` changes. No new file is added.

This plan starts from `specs/4e70661f_vm-guard-test-fixes.md`, whose "For the engineer" section
wrote the four changes out. The planner checked each against the code at `5ab8867`. The line
numbers and the code it quotes still match. What this plan adds: two small tests that prove the
two new skip helpers skip, an `import signal` the earlier spec only guessed at, and two check
commands.

## Baseline

`main` at `5ab8867` ("Send the VM guard test fixes through the guard chain"). Every finding is
judged against that commit. At the baseline:

- `test_a_change_is_put_back_and_stops_the_run[chmod]` (`test_git_dir_guard.py:106-107`) runs
  `sample.chmod(0o755)`. Under umask 0022 `git init` already made the sample 755, so nothing
  changes and the test fails with `DID NOT RAISE PermissionBreach`.
- `test_real_of_an_unnameable_dir_is_marked` (`:2162`) and
  `test_a_prefix_that_becomes_unnameable_has_moved` (`:2214`) each assert the unlocked
  4400-character path can be named. Where `getcwd` can't list every parent up to `/`, they fail.
- `test_a_stalled_listing_still_restores` (`:1225`) asserts under 15 s and takes 12.2 s on the
  host. The 12 s is two listings (`permissions._shared`, `permissions.py:1830-1831`, lists the
  saved and the live `.gitconfig`), each ending at `_sh`'s timeout of `KEYS_SECONDS + 5` = 6 s
  (`permissions.py:1743`), because the test's `sleep 30` replaces the `_KEYS` script that holds
  the `KEYS_SECONDS` watchdog.
- `test_a_stop_takes_a_child_with_a_non_utf8_name_and_its_own_child`
  (`test_agentsh_guard.py:1430`) has no marker and no skip. Where the machine refuses a signal
  to a process named `\xff`, it fails.

## Threat model

This change is to tests, so it gives an agent nothing new at run time. What matters is what the
tests keep proving:

- An agent runs as the engineer's own user. It can change a file's mode in `.git`, put an
  include behind a path nobody can name, stall a config listing, and start a process with a
  non-UTF-8 name. The four tests cover the guard's answer to each. Each must keep failing when
  the guard stops answering.
- A skip is a hole in coverage on the machine where it fires. Each new skip fires only on a
  probe of the machine (can this path be named unlocked; is this signal delivered), never on a
  name, an environment variable or a "we're in a VM" flag an agent could set. On the host,
  where verify runs, no probe fires, and a check command below fails if one does.
- What the guard can't stop at its level stays as it is: OpenShell refuses every signal to a
  process whose `comm` isn't valid UTF-8, so in a VM `_kill_descendants` and `rules.stop_tree`
  find such a process and can't kill it. The test that would show this skips there with a
  pointer to the `docs/open.md` row. The hole stays open and stays written down.

## Bar

Against `5ab8867`:

- **Chmod.** With umask 0022 set before the test repo is made, the `[chmod]` case raises
  `PermissionBreach`, as it does with 0002. At the baseline it doesn't raise under 0022.
- **Unnameable.** With `permissions._sh` made to fail naming the unlocked path, the helper both
  tests call skips with the reason. With it working, both tests run and pass as now. Every
  assertion on the locked path stays and still runs before any skip.
- **Stalled listing.** The new bound is in the test with a comment on what it's made of. The
  bound is under one whole `sleep 30`, and the test asserts that, so a listing nothing stopped
  still fails the test.
- **Non-UTF-8 child.** With `os.kill` refusing that one process, the helper the test calls
  skips, with a reason that names the `docs/open.md` row. Otherwise the test runs and passes as
  now.
- **Must never.** No change to any file under `.claude/skills/sssf/templates/`. No test
  deleted, no assertion on guard behaviour removed or weakened, no marker changed. No skip keyed
  on anything but a probe of the machine. No existing test starts failing or skipping on the
  host.
- The usual guard bar (no agent file kept, no agent code run, no engineer file deleted or
  overwritten where the baseline does otherwise) holds trivially: no guard code changes.

## Builder steps

Make the edits exactly as written. Don't refactor nearby tests.

### 1. The chmod case: `.claude/skills/sssf/tests/test_git_dir_guard.py`, lines 106-107

`stat` is already imported (line 30). Replace

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

The rest of the test stays. `state(repo) == before` compares modes, so it also proves the old
mode came back.

### 2. The stalled listing: same file, lines 1225-1236

Replace the whole test with:

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
5 s short of one `sleep 30`. One listing that waits out the stall takes at least 36 s and two
take 60 s, so either still fails.

Why the bound and not a cheaper stall: one listing instead of two needs the file deleted rather
than rewritten, which changes what the test covers, and the `+ 5` in `_sh`'s timeout is in
`permissions.py`, which this change must not touch.

### 3. The two unnameable-dir tests: same file, lines 2137-2222

Add this helper directly after `require_unnameable` (after line 2140):

```python
def require_nameable(u) -> None:
    """Skip when this machine can't name the path even unlocked. Past PATH_MAX, getcwd reads
    each parent directory up to /, and an OpenShell VM's policy refuses listing / and /var."""
    out = permissions._sh('cd -P "$1" && pwd -P', f"{u.base}/A/B").stdout
    if not out.startswith("/"):
        pytest.skip("this machine can't name the unlocked path: past PATH_MAX, getcwd must list "
                    "every parent directory up to /, and something here refuses that")
```

Directly after the helper, add a test of it. It needs no deep directory, so it is not
`host_only`:

```python
@pytest.mark.parametrize("named", [True, False])
def test_require_nameable_skips_only_when_the_path_has_no_name(tmp_path, monkeypatch, named):
    asked = []

    def fake(script, *args, **kwargs):
        asked.append((script, args))
        return subprocess.CompletedProcess([], 0 if named else 1, f"{tmp_path}/long\n" if named else "",
                                           "" if named else "sh: 1: pwd: getcwd() failed: Permission denied\n")
    monkeypatch.setattr(permissions, "_sh", fake)
    u = SimpleNamespace(base=tmp_path)
    if named:
        require_nameable(u)
    else:
        with pytest.raises(pytest.skip.Exception, match="can't name the unlocked path"):
            require_nameable(u)
    assert asked == [('cd -P "$1" && pwd -P', (f"{tmp_path}/A/B",))]
```

`subprocess` and `SimpleNamespace` are already imported (lines 31 and 34).

In `test_real_of_an_unnameable_dir_is_marked`, add one line, after `u.unlock()`:

```python
    u.unlock()
    require_nameable(u)
    assert _real(u.inc) == u.physical
```

The four locked assertions above it don't change and run first on every machine. A failure in
one of them is still a failure, because the `assert` raises before `require_nameable` is reached.

In `test_a_prefix_that_becomes_unnameable_has_moved`, add one line, after `u = unnameable`:

```python
    u = unnameable
    require_nameable(u)
    walked = [u.inc]
```

That test compares a nameable "before" with an unnameable "after", so with no nameable "before"
there is nothing to test. The rest of it doesn't change.

Both tests keep their `host_only` mark and its comment. The other three users of the
`unnameable` fixture don't assert on the unlocked path and don't change.

### 4. The non-UTF-8 child: `.claude/skills/sssf/tests/test_agentsh_guard.py`

Add `import signal` to the imports, after `import shutil` (line 14). The file doesn't import it
at the baseline. `os`, `subprocess`, `time`, `Path` and `pytest` are there.

Add this helper directly above
`test_a_stop_takes_a_child_with_a_non_utf8_name_and_its_own_child` (line 1430):

```python
OUTLIVES_ROW = "An agent's processes can outlive its call in a VM"


def require_signal_to_a_non_utf8_name(tmp_path) -> None:
    """Skip when this machine refuses a signal to a process whose name (/proc/<pid>/comm)
    isn't valid UTF-8. OpenShell does: os.kill and `kill -9` both get EPERM. Found by trying
    one. The probe waits for the name first: between fork and exec the process is still named
    after Python, and that signal would be delivered."""
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

Directly after the helper, add a test of it:

```python
def test_the_non_utf8_probe_skips_when_the_signal_is_refused(tmp_path, monkeypatch):
    real, refused = os.kill, []

    def fake(pid, sig):
        try:
            name = Path(f"/proc/{pid}/comm").read_bytes()
        except OSError:
            name = b""
        if name == b"\xff\n":
            refused.append(pid)
            raise PermissionError(1, "Operation not permitted")
        return real(pid, sig)
    monkeypatch.setattr(os, "kill", fake)
    with pytest.raises(pytest.skip.Exception, match=OUTLIVES_ROW):
        require_signal_to_a_non_utf8_name(tmp_path)
    assert len(refused) == 1
    assert not Path(f"/proc/{refused[0]}").exists()      # the probe ended and was reaped
```

Then call the helper first in the existing test. Only this one line is added to it:

```python
def test_a_stop_takes_a_child_with_a_non_utf8_name_and_its_own_child(machine, service, tmp_path, monkeypatch):
    require_signal_to_a_non_utf8_name(tmp_path)
    bad = os.fsencode(tmp_path) + b"/\xff"
```

The probe's script is in `tmp_path/probe`, so it doesn't collide with the test's own
`tmp_path/\xff`.

`OUTLIVES_ROW` must equal the opening words of the row in `docs/open.md` (line 43 at the
baseline). Don't change that row.

### 5. Docs: `docs/open.md`

In the table row that starts "The hub's suite still fails in an OpenShell VM" (line 56), cause
(2) has this sentence, which appears once in the file:

> The test fixes are a run of their own.

Replace that one sentence with:

> The four test fixes are done (run cd521f37, 2026-11-21): the `[chmod]` case flips the owner's execute bit, the two unnameable-dir tests skip when the unlocked path can't be named, the stalled listing's bound is 25 s (two listings at the 6 s `_sh` timeout, 13 s of room, under one `sleep 30`), and the non-UTF-8 child test now skips in a VM, where a probe finds the signal refused. Not yet rerun in a VM.

Rules for the edit:

- It is one table row on one line. Keep it one line, keep every other sentence in the row, and
  keep the row's last columns as they are.
- The new text has no `|` character, which would break the table. Don't add one.
- Change no other row. The row on processes that outlive a VM call (line 43) stays as it is.

Other docs: none go out of date. The repo keeps no CHANGELOG. `docs/sssf.md`'s section on the
`pushes` and `host_only` markers stays true, because no marker changes. No `app_docs/` write-up
describes these tests' behaviour.

## Running the tests

The planner tried on 2026-11-21, in this worktree, under agentsh. Neither file can be collected:
importing `adw_modules.utils` runs `load_dotenv()`, which walks up to the main checkout's
`.env`, and agentsh denies the read:

```
PermissionError: [Errno 13] Permission denied: '/home/gpaul/github/super-simple-software-factory/.env'
```

Builder: try once, from the repo root:

```
uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_git_dir_guard.py tests/test_agentsh_guard.py -q -p no:cacheprovider -m 'not pushes and not host_only'
```

If it fails with that `PermissionError`, don't work around it: no `.env` copy, no patched
`find_dotenv`, no changed environment. Say so in the build's notes. Verify runs on the host and
judges the tests. Don't run the whole suite under agentsh. So nothing in this plan was timed or
run by the planner; it was read from the code.

## Verification

Verify runs on the host with no `-m`, so it runs every test, the `host_only` ones included.

1. The repo's own test command passes, with every test that passed at `5ab8867` still passing
   and three more (the two `test_require_nameable_...` cases and
   `test_the_non_utf8_probe_skips_when_the_signal_is_refused`):
   `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`
2. **Chmod bar.** From the repo root, all 5 cases pass under umask 0022. At the baseline
   `[chmod]` fails this with `DID NOT RAISE`:
   `sh -c 'umask 0022 && uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_git_dir_guard.py -q -p no:cacheprovider -k test_a_change_is_put_back_and_stops_the_run'`
3. **No new skip fires on the host.** The two unnameable-dir tests and the non-UTF-8 child test
   pass and none is skipped. The command exits 1 if pytest fails or if its `-rs` summary has a
   `SKIPPED` line:
   `sh -c 'out=$(uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_git_dir_guard.py tests/test_agentsh_guard.py -q -p no:cacheprovider -rs -k "test_real_of_an_unnameable_dir_is_marked or test_a_prefix_that_becomes_unnameable_has_moved or test_a_stop_takes_a_child_with_a_non_utf8_name_and_its_own_child" 2>&1); s=$?; printf "%s\n" "$out"; [ "$s" -eq 0 ] || exit 1; case "$out" in *SKIPPED*) exit 1;; esac'`
4. **Unnameable and non-UTF-8 bars.** The two helper tests in step 1's suite prove each helper
   skips with its reason when the probe fails. Each real test calls its helper on one added
   line, which a reviewer reads in the diff.
5. **Stalled listing bar.** The diff shows the bound, its comment and `assert bound < stall`.
   The test passes in step 1 in about 12 s.
6. **Nothing else changes.** The diff names three files only:
   `.claude/skills/sssf/tests/test_git_dir_guard.py`,
   `.claude/skills/sssf/tests/test_agentsh_guard.py` and `docs/open.md`. No file under
   `.claude/skills/sssf/templates/` is in it. In `docs/open.md` one line changed, and
   "The test fixes are a run of their own" is gone from it.

Steps 2 and 3 are this plan's `checks`. Both need nothing the repo doesn't already use. The
planner ran both from the repo root: each starts, and each stops at the `.env` denial above
under agentsh, as the repo's own test command does there.

## Out of scope

Holes this change leaves open, so a reviewer files them as old holes:

- **Processes that outlive a VM call.** OpenShell still refuses signals to a process with a
  non-UTF-8 name, and SSSF still can't kill one in a VM. The test now skips there; the
  `docs/open.md` row stays open. That fix is a guard change of its own.
- **The stalled listing under the full VM suite.** It took 60 s there, which is two whole
  `sleep 30`s. Read from the code, not proven: `sandbox.run` waits for the process after a
  timeout, so if the kill of the timed-out listing doesn't land, each listing lasts the whole
  stall. If so, the test will still fail under the full VM suite, and it should, because that
  is the thing it tests. Alone in the VM it took 12.3 s and will pass.
- **The pi login tests** in the same `docs/open.md` row, and **the VM policy's paths** (listing
  `/` and `/var`).
- **Collection under agentsh in a worktree** (the `.env` denial). Not fixed here.
- **A skipped test covers nothing in a VM.** In a VM the unlocked-path assertions and the
  non-UTF-8 stop aren't tested. The host's verify still tests them.

## For the engineer

Nothing in this plan waits on a protected path outside `guard_builder`'s `writes`. Two things
are yours after the merge:

- **The VM proof.** In a throwaway VM, run the five tests alone and see `[chmod]` and the
  stalled listing pass, and the other three skip with their reasons (`-rs` shows them). Then
  drop "Not yet rerun in a VM." from the `docs/open.md` sentence.
- **The `.env` denial** isn't in `docs/open.md`. Two planners have now hit it (4e70661f and
  this run). It may deserve a row of its own.
