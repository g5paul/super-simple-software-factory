# Make four guard tests hold in an OpenShell VM

Run it with `--worktree` and `--sandbox none`, on the hub's own config. The change is to two test files, `tests/test_git_dir_guard.py` and `tests/test_agentsh_guard.py` under `.claude/skills/sssf`, which aren't guard code. No module under `templates/adws/adw_modules/` changes. A VM run can't pass verify yet (the pi login tests in `docs/open.md`'s VM row), so verify runs on the host, and the engineer proves the VM half after the merge.

## What broke

Five tests fail in an OpenShell VM and pass on the host. The engineer found each cause on 2026-11-21, in throwaway VMs. None is a bug in the guard code.

1. **`test_git_dir_guard.py::test_a_change_is_put_back_and_stops_the_run[chmod]`.** The case runs `sample.chmod(0o755)` on a hook sample and expects `restore_git_dir` to raise. On the host the umask is 0002, so `git init` makes the samples 775, and 755 is a change. In the VM the umask is 0022, so the samples are already 755, nothing changes, and the guard rightly reports nothing (`DID NOT RAISE PermissionBreach`).
2. **`test_real_of_an_unnameable_dir_is_marked` and `test_a_prefix_that_becomes_unnameable_has_moved`**, same file. Both use the `unnameable` fixture, a path of about 4400 characters through `A/B`. Each expects the path to be nameable while unlocked: the first asserts `_real(u.inc) == u.physical` after `u.unlock()`, the second asserts `where[f"{u.base}/A/B"][0].startswith("/")` before `u.lock()`. Past PATH_MAX, `getcwd` reads each parent folder up to `/` to find the path, and the VM's policy refuses listing `/` and `/var`. So in the VM, `sh -c 'cd -P "$1" && pwd -P'` fails with `getcwd() failed: Permission denied` even unlocked, and the guard marks the path `?`, as it should for a path it can't name. On the host the same steps name it at 4443 characters.
3. **`test_a_stalled_listing_still_restores`**, same file. It asserts the stalled listing ends within 15 s. It takes 12.2 s on the host and 12.3 s alone in the VM, and 60 s under the full VM suite. The test sets `KEYS_SECONDS` to 1 and replaces `_KEYS` with `sleep 30`, and `_sh` gets `timeout=KEYS_SECONDS + 5`; find out where the 12 s goes (most likely two listings that each wait out the 6 s `_sh` timeout, because `sleep 30` ignores the shell timeout `_KEYS` would apply) and say it in the spec.
4. **`test_agentsh_guard.py::test_a_stop_takes_a_child_with_a_non_utf8_name_and_its_own_child`.** OpenShell refuses every signal to a process whose name (`/proc/<pid>/comm`) isn't valid UTF-8: in a VM, `os.kill` and `kill -9` of a running script named `\xff` both get `EPERM`, while scripts named `ok.sh`, `café` and `a\x01b` are killed at once. SSSF's `_kill_descendants` and `rules.stop_tree` find the process and try; the kill is refused. `docs/open.md` has a row on it ("An agent's processes can outlive its call in a VM"). This run doesn't fix that.

## The change

1. **The chmod case** changes the sample's mode to one that differs from its mode at the start, whatever the umask, for example by flipping the owner's execute bit.
2. **The two unnameable-dir tests** skip, with a message naming the reason, when the unlocked path can't be named on this machine, the way `require_unnameable` skips when the locked one can. The assertions on the locked path stay.
3. **The stalled listing** gets a bound that follows from the time the listing is allowed, with room for a loaded machine, or the test is changed so the stall costs less. Whichever the spec picks, the test still fails if the listing waits for the whole `sleep 30`.
4. **The non-UTF-8 child test** skips when the machine refuses a signal to a process with a non-UTF-8 name, found by trying one, with a message that points at the `docs/open.md` row. On a machine that delivers the signal, the test runs as now.
5. **Docs.** In `docs/open.md`'s row that starts "The hub's suite still fails in an OpenShell VM", cause (2) says the four test fixes are done and the non-UTF-8 test now skips in a VM.

## The bar

- **Chmod.** With the umask set to 0022 before the test repo is made, the `[chmod]` case raises `PermissionBreach`, as it does with 0002. On `main` it doesn't raise under 0022.
- **Unnameable.** With `permissions._sh` made to fail naming the unlocked path, both tests skip with the reason; with it working, they run as now.
- **Stalled listing.** The new bound is in the test with a comment on what it's made of, and the test fails when the listing waits out `sleep 30`.
- **Non-UTF-8 child.** With `os.kill` refusing that one process, the test skips; otherwise it runs and passes as now.
- **Nothing else changes.** Every existing test passes on the host, and no file under `templates/` changes.

## After the merge, the engineer's proof

Not part of the run: in a throwaway VM, run the five tests and see the chmod case and the stalled listing pass, and the other three skip with their reasons.

## Running the tests

The planner and the builder run under agentsh. Run only `tests/test_git_dir_guard.py` and `tests/test_agentsh_guard.py` from `.claude/skills/sssf` with `-m 'not pushes and not host_only'`, and leave the whole suite to verify. Two of the five are `host_only`, so the builder can't run them under agentsh; verify runs them. A whole-suite run under agentsh makes the after-call event read take many minutes (`docs/open.md`).

## Out of scope

- Fixing the processes that outlive a VM call. That's a guard change of its own.
- The pi login tests in the same `open.md` row.
- The VM policy's paths.
