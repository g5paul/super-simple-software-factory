# Let the hub's suite pass in an OpenShell VM

Run it with `--worktree` and `--base main`, on the hub's own config. One part is the VM image's package list in `sandbox_openshell.py`, which is guard code, so the change goes through the chain. The other two parts are a test fixture and the hub's quality cap, which aren't guard code but ship in the same change, because until they land no `--sandbox openshell` run of the hub can pass verify.

## What broke

Run 4a74c2b9 (2026-11-20) was a `simple-sdlc --worktree --sandbox openshell` run on the hub, in a VM with 4 vCPUs, 4 GiB of RAM, a 16 GiB disk and `TMPDIR=/var/tmp` (guard run 2656ff74). Verify passed the visualizer tests, the typecheck and both plan checks in the VM. It failed two checks:

- **`test`**, the skill suite, timed out at 1200 s with 3208 of 3858 tests done and 46 failed.
- **`chain_test`** had 1 failure.

The same suite passed on the host an hour earlier (quality run e3fc5c57, 4 of 4). A rerun of the failing files in a throwaway VM, made the same way a run makes one, with no agents, reproduced 43 of the 46 and gave these causes.

1. **No global git identity: 37 tests.** These tests make their own temp repos and commit in them. On the host, `~/.gitconfig` gives git a name and email. In the VM, `sandbox_remote._clone` sets `user.name` and `user.email` only in the run's clone, so every commit in a temp repo fails with `Author identity unknown` (`fatal: unable to auto-detect email address (got 'ubuntu@<vm>.(none)')`). The 37 are all 22 of `tests/test_adw_tickets.py`'s failures, 8 in `tests/test_host_agents.py`, 6 in `tests/test_failed_call_rollback.py` and 1 in `tests/test_agent_commits.py`.
2. **No `rsync` in the image: 1 chain test.** `adws/tests/test_guard_change.py::test_the_build_brief_copy_takes_what_git_sees_and_leaves_ignored_files_out` runs `git ls-files -z ... | rsync -a --inplace --from0 ...` and gets exit 127. The image's apt list is in `containerfile()` in `templates/adws/adw_modules/sandbox_openshell.py`.
3. **The cap is too short for the VM.** The VM ran the suite at about 2.7 tests a second against the host's 5.2. The whole suite needs about 1450 s there. `test()` in the hub's `adws/adw_modules/quality.py` caps it at 1200 s.

Other failures from that run are out of scope; see the end.

## The change

1. **A test identity for every test.** The skill suite's `tests/conftest.py` gives every test a git identity that doesn't come from the machine, so a temp repo can commit on a host with no `~/.gitconfig`. Pointing `GIT_CONFIG_GLOBAL` at a temp file that sets `user.name` and `user.email` is one way; the spec picks a way and says why.
   - That also hides the engineer's own `~/.gitconfig` from the suite. Find any test that relies on a setting from it, and say in the spec what each needs.
   - A repo's own `user.name` or `user.email` (as `_clone` sets, and as tests that check authorship set) still wins over the test identity.
   - The hub's `adws/tests` suite (the `chain_test` check) gets the same, if any of its tests commit without setting an identity. Say in the spec whether it needs it.
2. **`rsync` in the image.** Add it to the apt line in `containerfile()`. That changes `image_tag`, so every repo builds a new image at its next `just openshell-setup`; say so in the OpenShell section of `references/config.md`.
3. **The cap.** Raise `test()`'s `timeout_seconds` in the hub's `adws/adw_modules/quality.py` so a VM run fits with room, and update the comment above it with the VM's numbers. Host runs take about 740 s; the cap only has to catch a hang. `quality.py` is the hub's own file, and the template's isn't touched.
4. **Docs.** Delete the `docs/open.md` row this closes in part only if all of its causes are fixed; otherwise edit the row to drop causes (1) to (3) and keep the rest.

## The bar

- **Identity, no global config.** A test that runs `git commit` in a fresh temp repo, with `HOME` pointed at an empty folder so no `~/.gitconfig` is found, commits. On `main` the same test fails with `Author identity unknown`.
- **Identity, a repo's own wins.** In a temp repo with its own `user.email`, a commit's author is that email, not the test identity.
- **`rsync`.** A test on `containerfile()` sees `rsync` on the apt line, and the policy, network rules and every other line of the image are as before.
- **Cap.** `test()`'s cap is the new number.
- **Nothing else changes.** Every existing test passes on the host.

## After the merge, the engineer's proof

Not part of the run, since no test starts a real VM:

1. Update the hub's own install (`install.py --update`), rerun `just openshell-setup` to build the new image, and commit the update.
2. Rerun `specs/tasks/flaky-obs-retry-timing.md` with `just simple-sdlc --worktree --sandbox openshell`, and see `test` finish inside the cap with only the out-of-scope failures below, and `chain_test` pass.

## Running the tests

Run the touched test files from `.claude/skills/sssf` with `-m 'not pushes and not host_only'`, plus `adws/tests/test_guard_change.py` from the repo root, and leave the whole suite to verify. No test starts a real VM, a real gateway or a real agent.

## Out of scope

Each is in the same `docs/open.md` row:

- `test_rosters.py`'s ChatGPT roster tests, which need a pi login in the VM.
- `test_git_dir_guard.py`'s `[chmod]` case, its two unnameable-dir tests and its stalled-listing timing test, and `test_agentsh_guard.py`'s non-UTF-8 child test, which fail in the VM for causes not yet found.
- Making the suite faster, such as with `pytest-xdist`.
