# Let pytest walk a temp folder in an OpenShell VM

Run it with `--worktree` and `--base main`, on the hub's own config. The change is to the VM's filesystem policy in `.claude/skills/sssf/templates/adws/adw_modules/sandbox_openshell.py`, which is guard code, so it goes through the guard chain. Its tests are in `tests/test_sandbox_openshell.py`.

This is the last cause in `docs/open.md`'s P1 row that starts "A hub run in an OpenShell VM stopped at verify". Guard run 7e8490c2 (46bee4c) fixed the first, the six pi tests.

## What broke

Run 12ec0782 (2026-11-25) failed its plan check in an OpenShell VM. The log is `adws/adw_data/sessions/12ec0782/context_handoff/quality/09_plan_check_1/command.log`. The planner's check made a folder with `mktemp -d`, wrote a test file there and ran `pytest -c pytest.ini <folder>/src/test_kept.py` from the repo. Every VM exec gets `TMPDIR=/var/tmp`, so the folder was `/var/tmp/tmp.<x>`, and pytest stopped with `PermissionError: [Errno 13] Permission denied: '/var'` and `found no collectors`.

The cause is how pytest collects a path outside its rootdir. In pytest 9.2.0 (`_pytest/main.py`, `Session.collect`), it adds each parent of the path, stopping at the first one above the confcutdir, which defaults to the ini file's folder. It then lists each of those folders with `os.scandir`. For `/var/tmp/tmp.<x>/src/test_kept.py` that means `/var`, `/var/tmp` and so on. The policy's read-write list has `/tmp` and `/var/tmp`, and its read-only list has `/var/log` (`READ_ONLY_PATHS`), but nothing covers `/var` itself, so `/var` can't be listed. A test file under `/tmp` would get through, because pytest's walk starts at `/tmp`, which is covered.

The engineer reproduced it on the host on 2026-11-26. With a folder of mode 0311 standing in for `/var`, the same command fails with the same two errors. With `--confcutdir` set to the temp folder, it passes.

Any tool that lists a parent of `TMPDIR` fails the same way, so the fix belongs in the VM, not in each plan's checks.

## The change

Make every parent of `TMPDIR` below `/` listable under the VM's policy. Two ways; the spec picks one and says why.

1. **Add `/var` to `READ_ONLY_PATHS`** (preferred). It's one line, and every exec keeps `TMPDIR=/var/tmp` on the VM's disk. `/var/tmp` stays read-write. It lets an agent in the VM read all of `/var`. The image is built from `BASE_IMAGE` (Ubuntu 24.04) by `containerfile()`, and `/etc` is already read-only. The spec lists what `/var` holds in that image, says that SSSF writes nothing to `/var` outside `/var/tmp`, and says why reading the rest gives an agent nothing it shouldn't have. `/var/run` and `/var/lock` are links to `/run` and `/run/lock` on Ubuntu. The spec says whether Landlock follows them and whether `/run` then becomes readable, and checks for any other link under `/var` that leaves it.
2. **Move `TMPDIR`** to a folder on the VM's disk whose every parent below `/` the policy already covers. That keeps the policy's reads as they are, but moves every VM tool's temp, including the skill suite's 2.5 GB. The new folder must not be `/tmp`, which is a tmpfs from the VM's RAM and filled up in run 00d8e1a8. It must also not be in the repo's tree, where a suite run from the root would collect pytest's own temp test files.

Either way:
- The module docstring's line on `TMPDIR` says what the policy allows and why `/var` (or the new folder's parents) has to be listable, citing 12ec0782.
- `docs/open.md`'s row that starts "A hub run in an OpenShell VM stopped at verify" says the fix is in, and that the engineer's VM proof below closes the row.

## The bar

- **Red on `main`.** A test in `test_sandbox_openshell.py` checks that every parent of `TMPDIR` below `/` is equal to or under a path in the policy's `read_only` or `read_write`. On `main` it fails, naming `/var`.
- **Green after.** The same test passes on the branch, for every roster kind the policy tests already cover.
- **Nothing else in the policy changes.** The tests that pin `READ_ONLY_PATHS` and the read-write list (`test_the_policy_differs_from_the_baseline_only_by_the_two_devices` and the ones near lines 206 and 382) change only by the one entry. `/var/tmp` stays read-write, no other path is added, and the network rules don't change.
- **Every existing test passes on the host.**

## After the merge, the engineer's proof

Not part of the run. In a throwaway OpenShell VM, check these:
- 12ec0782's plan check command passes.
- `ls /var` works, and writing a file directly in `/var` is refused.
- Under option 1, reading through `/var/run` doesn't reach anything in `/run` that wasn't readable before.

Then a hub `simple-sdlc --sandbox openshell` run can pass verify, which closes the P1 row.

## Running the tests

The planner and the builder run under agentsh on the host, so they can't start a VM. Run only `tests/test_sandbox_openshell.py` from `.claude/skills/sssf` with `-m 'not pushes and not host_only'`, and leave the whole suite to verify, which runs on the host. What `/var` holds in the image comes from reading `containerfile()` and what Ubuntu 24.04 ships, not from a VM.

## Out of scope

- The planner's prompt and `plan_checks.py`. A check that writes to a temp folder is fine once the VM can list its parents.
- `/tmp`'s size, and how much temp the skill suite writes (`docs/open.md`'s row on pytest temp).
- Listing `/`. pytest's walk stops below it whenever the repo's folder and `TMPDIR` share no parent but `/`. If the spec finds a case where it doesn't, it says so and stops there rather than widening `/`.
