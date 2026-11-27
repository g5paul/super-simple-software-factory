# Commit only the tree verify passed

This builds on guard run dafc21f0, which isn't merged. The engineer makes `guard/verify-fresh-vm` from `main`, merges `sssf/dafc21f0` into it, and launches this run from that branch with `--worktree --base guard/verify-fresh-vm`. So the baseline below is dafc21f0's code, and `main` is the code before it. The hub's own config. This is guard work and goes through the guard chain. The code is in `.claude/skills/sssf/templates/adws/adw_modules/`, mostly `sandbox_remote.py`, `plan_checks.py` and `git_helper.py`, with tests under `.claude/skills/sssf/tests`, beside `test_verify_fresh_vm.py`.

## What's wrong

dafc21f0 runs each verify round under a VM sandbox in a fresh VM: `Remote.open_verify` takes the builder's work out of the build VM as one patch (`_patch_out`), the host judges it (`_judge_patch`), and a new VM applies it and runs the checks. The review and `commit_build` then use the build VM's tree. Nothing checks that the commit holds the tree verify passed. The bypass-reviewer found two ways through on 2026-11-26, and the engineer reran both. Each one gets past every agent guard. On `main` verify fails each time. On the baseline it passes, and the bad tree lands on the run's branch.

1. **A force-added ignored file (High).** `_patch_out` builds its index from `git read-tree HEAD` plus `git add -A`, and `git add -A` skips ignored files. `commit_all` (`git_helper.py`, about line 73) runs `git add -A` on the real index, which still holds a file the builder staged with `git add -f`. The plant: `.gitignore` has `local_settings.py`, and the tracked `app.py` is `OK = 1` then `try: from local_settings import *` / `except ImportError: pass`. The builder runs `printf "OK = 0\n" > local_settings.py && git add -f local_settings.py`. The check `python3 -c 'import app,sys; sys.exit(0 if app.OK == 1 else 1)'` passes in the fresh VM, which never got the file, and the commit holds it.
2. **A `git diff` that fails turns into an empty patch (Medium; High with a process left running).** `_patch_out`'s script ends in `git diff --cached --binary "$1" | base64 -w0`, so the script exits with base64's status. If `git diff` dies, SSSF gets empty output, `open_verify` skips both the judge and the apply, and the fresh VM checks the last sync alone. The plant: unpack the build VM's pack into loose objects, delete the loose object of a tracked file's blob at the sync, delete that file and break `app.py`. The guards pass, because `snapshot`'s `git diff HEAD --raw` doesn't read the blob. Verify passes with `patch_bytes: 0`. The commit then fails ("unable to read <oid>"), but `close()` → `commits_out` still lands it on the run's branch. If a process the builder left running puts the object back after the patch is taken, the commit succeeds and the whole run passes.

Two older holes have the same root, and are on `main` too:
- **skip-worktree.** The builder stages bad bytes for a file, runs `git update-index --skip-worktree` on it, and writes good bytes to disk. Verify checks the disk, and the commit takes the index.
- **A process left running changes the tree after verify.** `docs/open.md`'s P2 row that starts "The verified tree is not bound to the committed tree" has it.

And two harms the baseline brought:
- **`VerifyBox` has only `run` and `close`.** A repo's own `quality.py` block that calls `sandbox.current().read_text`, `exists`, `is_file` or `size` crashes with `AttributeError` under a VM. On `main` it works.
- **`plan_checks.verify` returns a `run_quality` result as it is when the plan has no checks** (about line 198, `if not extra: return result`). `main` always rebuilt it with `passed=not failures`, so a custom `run_quality` that says passed while listing failures now passes verify, under `sandbox: none` too.

## The change

1. **Verify checks what the commit will hold.** In the build VM, `_patch_out` starts its temporary index from a copy of the real index, not from `HEAD`, then runs `git add -A`, so staged ignored files and skip-worktree entries are in it as they will be in the commit. No git output goes through a pipe that hides its exit status. Any git failure there stops the round with `SandboxFailed`, and no check runs.
2. **The commit must be the verified tree.** The host's tree from `_judge_patch` (`write-tree` after `apply --cached`) is what verify passed. SSSF keeps it for the round that went green. When the build VM's commits come back (`commits_out`, at a sync or at `close`), the host refuses, as a `PermissionBreach`, any commit made after a green verify whose tree isn't that tree, and lands nothing of it. The spec maps which commits land under each VM chain (`simple-sdlc`, `tickets`, `build_test`, the guard chain, `quality`), which must equal a verified tree, and which are rightly made before any verify, such as the plan commit. It says what the documenter's commit is held to. This closes the P2 row named above, and the spec says so in it.
3. **The two harms.** `VerifyBox` answers the same read calls as the build VM's sandbox, against the fresh VM. `plan_checks.verify` gives `passed` as `not failures` again on every path.
4. **Docs.** `references/config.md`'s "Where verify runs under a VM" says the commit must equal the verified tree, and stops saying that a process left running can't reach the result. `docs/open.md`'s rows say what this run closes. Add a P3 row for two smaller findings this run doesn't fix: `sandbox_usage.Totals` counts each verify VM's `sandbox_end` as a run, and the dashboard's `openSandboxes` (`db.ts`) never lists a verify VM as open, so a leaked one doesn't show (the provider sweep still deletes it).

## The bar

- **Red on the baseline, green after.** One test per plant above, each through the guard sequence in `agents.py` and a stand-in `Remote`, as `test_verify_fresh_vm.py` does: the force-added ignored file, the missing blob with no process left running, the missing blob put back after the patch, skip-worktree, and a file changed between verify and the commit. On the baseline each lands the bad tree on the run's branch. After, each is refused, nothing of it lands, and the branch stays at the last sync.
- **A real run is unchanged.** A builder's ordinary change, an ordinary staged rename, a deleted file and a binary file each pass verify and land, as on the baseline.
- **The harms.** A test calls each read method on `sandbox.current()` inside a verify round under a stand-in VM, and passes. A test gives a `run_quality` that says passed with a failure listed, and verify fails. Both fail on the baseline.
- **Nothing an agent may do changes.** The VM's policy, the agents' prompts and their rules are as on the baseline.
- **Every existing test passes on the host.**

## Running the tests

The planner and the builder run under agentsh on the host, so they can't start a VM. Use the stand-ins in `test_verify_fresh_vm.py` and `test_sandbox_remote.py`. Run only the files the change touches, from `.claude/skills/sssf`, with `-m 'not pushes and not host_only'`, and leave the whole suite to verify.

## Out of scope

- Fixing the usage totals and the dashboard (the P3 row above).
- `sandbox: none`'s `/tmp/conftest.py` row. The verify-pass fix in (3) applies there, and nothing else does.
- The text guard. Its patches are frozen.
