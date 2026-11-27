# Run verify in a fresh VM, away from the builder's leftovers

## Request

Under a VM sandbox (`openshell`, `vercel`, `e2b`), verify runs in the VM the builder just worked in, as the same user. Anything the builder left outside the diff can change what verify reports: a `conftest.py` in `/tmp` or `/var/tmp`, a `.pth` in the user site, uv's cache or config, an ignored file such as `.venv`, a process it left running. This is the open half of `docs/open.md`'s P2 row "In a VM, a builder can make a plan check pass that should fail".

## Decision: option 1, a fresh VM for each verify round

Each verify round takes the builder's work out of the build VM as a patch, starts a new VM from the same image, clones the run's branch into it, applies the patch, runs `worktree_setup`, runs every block and plan check there, and deletes it.

Why not option 2 (reset the build VM): it is only as good as its list. The list of writable places exists for OpenShell only (the policy's `read_write`), and SSSF writes no policy for `vercel` or `e2b`, so there the list would be a guess. A reset also runs in a VM where the builder's processes may still be alive while it runs. A fresh VM needs no list and covers a hiding place nobody has found yet, for every `Remote`.

The build VM stays up, untouched, during verify: the reviewer and any fix call go back to it, and `commit_build` still commits there. Nothing is written to it by this change (the patch is made with a temporary index, so its index is as on `main`).

### Cost, and what was not measured

The request asks the spec to measure a VM start and a `worktree_setup` for the hub. **The planner could not**: it runs under agentsh, which cannot start a VM, and it cannot read `adws/adw_data` in the main checkout, where past traces are. What the code bounds: an OpenShell create is capped at `READY_SECONDS` (180 s) in `sandbox_openshell.py`; the hub's `worktree_setup` is one command (`bun install --frozen-lockfile --backend=copyfile` in the visualizer), capped at 600 s. `adw_simple_sdlc.py` runs at most `MAX_FIX_LOOPS` (3) verify rounds plus one `reverify`, so at most four fresh VMs a run. So the change records the real numbers on every run (step 2's `sandbox_verify` event: `create_seconds`, `clone_seconds`, `setup_seconds`), and the engineer's first OpenShell run after the merge gives the figures for `docs/open.md` (see `## For the engineer`).

## Threat model

The builder is an agent in the build VM, as the VM's user (`ubuntu`, `HOME=/sandbox` on OpenShell). It can write every place that user can (on OpenShell: `/sandbox`, `/tmp`, `/var/tmp`, and the shared folder holding the run's tree), leave ignored and untracked files in the tree, change the tree's `.git` (config, hooks, index), and leave a process in its own session (`setsid`) that outlives `RemoteProcess.stop_tree`. It wants a failing check to report a pass.

What this guard can't stop at its level:
- Anything inside the patch. The patch is the diff the reviewer reads; a `conftest.py` in the tree, or a changed test, travels to the fresh VM and runs there. The reviewer reads that.
- The patch itself comes from the build VM's git, which the builder controls. It can make the patch differ from its working tree. What verify checks is then the patch, and what the reviewer reads and `commit_build` commits is the build VM's tree. See `## Out of scope`, row 1.
- Under `sandbox: none` the agent runs as the engineer's own user under agentsh, and verify runs on the host. Not changed here; see step 7.

## Baseline

`main` at `e40eba4`. Every finding is judged against that commit.

On it: `quality._run` (`quality.py`, about line 83) and `plan_checks._run` (`plan_checks.py`, about line 40) call `sandbox.current().run`, which under a VM is the build VM. `Remote.close` takes uncommitted work out with `git add -A && git diff --cached --binary HEAD`. `Remote.commits_out` judges commits with `permissions.judge_commits` from objects the host hashed.

## Bar

Must, against the baseline:
- Under every `Remote`, every quality block and every plan check of a verify round runs in a VM made for that round, which holds the run's branch plus the builder's patch and nothing else of the builder's.
- No agent file outside the patch reaches verify, and no process the builder started can reach the VM verify runs in.
- The patch is judged on the host, by `permissions.judge_commits`, before any of it is put in the fresh VM. A refused patch stops the run with `PermissionBreach`, as a refused commit does on `main`.
- If the fresh VM can't be made, cloned or patched, the run stops with `SandboxFailed`. Verify never falls back to the build VM.
- The fresh VM is deleted when the round ends, pass, fail or exception.

Must never:
- Keep an agent's file on the host that `main` doesn't keep. The patch lives in memory; a refused one is kept in the run's session folder, as `sandbox-refused.bundle` is on `main`. Objects the host hashes to judge the patch stay unreferenced in the host repo, as a refused bundle's do on `main`.
- Run agent code on the host. The host runs only `git apply --cached`, `git write-tree` and `git commit-tree` on the patch, with `GIT_NO_PROGRAMS`, in a temporary index. No checkout, no hook, no filter.
- Delete or overwrite an engineer's file. The host's worktree, index and refs are not touched by a verify round.
- Start an agent in the fresh VM, send it the Claude token or a pi token, or land a commit from it.
- Change the VM's policy, an agent prompt template, or a rule. `sandbox: none` behaves as on `main`.
- Tell an agent anything about a refusal or a VM failure. A failed check reports as on `main`.

One wording change is needed, and the reviewer should weigh it against the request's "the agents' prompts are as on main": `plan_checks.RAN_IN_VM` and `RUNS_IN_VM` say the checks run "in the run's VM, the one the agents work in" / "the one you work in". After this change that is false. Run 83c9ca03 made these two strings say where the checks run, from one source (`_in_vm`). This plan keeps them true (step 4). No prompt template file changes; only these two code-written strings do.

## Changes

All paths are from the repo root. `guard_builder` may write every one of these.

### 1. `.claude/skills/sssf/templates/adws/adw_modules/sandbox.py`

Add a context manager `verifying(run)`:
- `current() is LOCAL`: yield `None`, nothing else. The local path stays byte-for-byte as on `main`.
- Otherwise the current box must have `open_verify`; if it hasn't, raise `SandboxFailed` (fail closed, no check runs in the build VM).
- `fresh = box.open_verify()`, then `use(fresh)`, yield `fresh`, and in a `finally`: `use(box)` first, then `fresh.close_verify()`.

Using `use(fresh)` means `quality.py` and `plan_checks._run` need no edit: their `sandbox.current().run` is the fresh VM inside the scope. This matters because `quality.py` is the file each repo edits, and a stamped repo's copy would not get an edit there.

Update the module docstring's first paragraph (it says the only sandbox is `Local`).

### 2. `.claude/skills/sssf/templates/adws/adw_modules/sandbox_remote.py`

Add to `Remote`:

- A class attribute `verify_only = False`.
- `_patch_out() -> bytes`: the builder's work, without changing the build VM's index. One `_must("sh", ["-c", ...], cwd=str(self.tree))` that makes a temp index (`GIT_INDEX_FILE="$(mktemp)"`), runs `git read-tree HEAD`, `git add -A`, `git diff --cached --binary HEAD | base64 -w0`, and removes the temp index in a `trap`. Base64, as `commits_out` does, because `sandbox_openshell.run_process` decodes output with `errors="replace"`. Empty output means no uncommitted work.
- `_judge_patch(patch: bytes) -> None`: on the host, in `self.tree`, with a temp index file under `tempfile.mkdtemp()` passed as `GIT_INDEX_FILE` (never the worktree's own index): `git read-tree <self.synced>`, `git apply --cached --binary` with the patch on stdin, `git write-tree`, `git commit-tree <tree> -p <self.synced> -m "sssf verify"`. All through git with `GIT_NO_PROGRAMS` (as `_host_git`; add an `env`/`input` path to it or a small sibling). Then `permissions.judge_commits(self.tree, self.synced, commit, cfg, run.ran, session=inside, taken=run.taken)`, with the same `cfg`, `inside` and `taken` expressions `commits_out` uses, and `only=None`, as `commit_build`'s commit has. A git failure, a missing `cfg`, or a non-empty answer: write the patch to `<session_dir>/sandbox-refused-verify.patch` with `session_files.write`, log `sandbox_verify_refused` (type `error`, same payload shape as `sandbox_commits_refused`), raise `permissions.PermissionBreach` with `permissions.refusal_text(refused)`. Never call `permissions.commit_passed`. Record the tree id as `self.verified_tree`. Remove the temp folder in a `finally`. If the build VM's `HEAD` is not `self.synced`, call `self.commits_out()` first, so the patch is always on top of the last sync.
- `open_verify() -> Remote`: 
  1. `patch = self._patch_out()`; if not empty, `self._judge_patch(patch)`.
  2. `fresh = type(self)(self.run_, self.branch, self.project)`; set `fresh.verify_only = True`, `fresh.python = self.python`, `fresh.python_problem = self.python_problem`. `fresh._token` stays `""`.
  3. `fresh.sandbox = fresh._create()`, set `vm`, `_created`, `_session`, `_cpu_base` as `open` does. Then `fresh._clone()`. No `handoff_in`, no `_check_tools` (same image, already checked; and no agent's handoff file goes in).
  4. If the patch is not empty: `fresh._must("git", ["apply", "--binary", "--whitespace=nowarn", "-"], cwd=str(self.tree), input=patch)`. Worktree only, so new files are untracked and changes unstaged, as in the build VM.
  5. `worktree_setup`: load it as `worktree.set_up` does (`repo_settings.load(worktree.main_checkout())`), and run `worktree.run_setup(commands, self.tree, timeout, <context_handoff>/setup_verify, run.data_root, fresh)`. Keep its answer as `fresh.setup_failed` (`""` when it passed).
  6. Log one `sandbox_verify` event: `provider`, `vm`, `build_vm`, `create_seconds`, `clone_seconds`, `setup_seconds`, `patch_bytes`, `tree` (`verified_tree`).
  7. Any `self.errors` or `SandboxFailed` after `_create` succeeded: delete the fresh VM, then raise `SandboxFailed`. Return `fresh`.
- `close_verify()`: for a `verify_only` box only. `_cpu_add()`, `_call(self._delete)`, and a `sandbox_end` event with the fields `close` logs plus `"verify": True` and `uncommitted_patch: ""`. Never `commits_out`, never the uncommitted patch, never raises (a failed delete is logged as `sandbox_error`; the provider's `sweep` removes the VM later, since it carries the run's labels).
- In `_run`: when `self.verify_only`, skip the `git commit` sync. In `start` and `commit`: when `self.verify_only`, raise `SandboxFailed("a verify VM starts no agent and lands no commit")`. In `close`: when `self.verify_only`, call `close_verify()` and return.
- In `commits_out`, after the host has `tip` and before the merge: if `self.verified_tree` is set and `only` is falsy, compare `git rev-parse <tip>^{tree}` on the host with it. If they differ, log `sandbox_commit_not_verified` (type `error`, both tree ids) and `console.note` it for the engineer. **Do not refuse**: the reviewer or a chain may write between verify and the commit, and those cases are not mapped yet (see `## Out of scope`, row 1). Clear `verified_tree` once a commit lands.

Read `.claude/skills/sssf/templates/adws/adw_modules/sandbox_usage.py` and make sure a run with several `sandbox_end` events sums them; fix and test it if it reads only one. Check `sandbox_vercel.py`, `sandbox_e2b.py` and `sandbox_openshell.py`: each `_create` must be callable a second time for the same run (OpenShell's name ends in a random suffix, and it rewrites the same `openshell-policy.yaml`; Vercel and E2B take tags). Change them only if a second create can't work.

Update the module docstring: add a bullet on the verify VM.

### 3. `.claude/skills/sssf/templates/adws/adw_modules/plan_checks.py`

- `verify`: wrap the body in `with sandbox.verifying(run) as fresh:`. If `fresh` is not `None` and `fresh.setup_failed`: run no block and no check, and return a failed `QualityResult` with one `QualityCheckResult` (`name="worktree_setup"`, `area="setup"`, `operation="setup"`, `command="worktree_setup"`, `returncode=1`, `passed=False`, `output_tail` the text `run_setup` returned) and that text in `failures`. It is the repo's own setup output on the builder's own tree, so the builder may see it, as it sees a failed check.
- `verify` with no plan checks returns `base`'s result object itself, so a chain can call it in place of `quality.run_quality(run)` with no change in the result.
- `RAN_IN_VM = "in a fresh VM of the run's own, not the one the agents work in"` and `RUNS_IN_VM = "in a fresh VM of the run's own, not the one you work in"`. Update `_in_vm`'s docstring and the module docstring. `planned()` is unchanged: its dry run opens no VM.

### 4. Chain templates that call the blocks directly

So every verify goes through the scope: in `.claude/skills/sssf/templates/adws/adw_build_test.py` (about line 72), `.claude/skills/sssf/templates/adws/adw_tickets.py` (about line 152) and `.claude/skills/sssf/templates/adws/adw_quality.py` (about line 33), replace `quality.run_tests(run)` / `quality.run_quality(run)` with `plan_checks.verify(run, None, base=quality.run_tests)` / `plan_checks.verify(run, None)`. Passing `base=` to `builder_brief` stays as it is.

### 5. Tests, under `.claude/skills/sssf/tests`

New file `.claude/skills/sssf/tests/test_verify_fresh_vm.py`. It builds its config from `.claude/skills/sssf/templates/sssf.config.yaml` (as `jbox` in `tests/test_sandbox_remote.py` does), never a test-only one. Its stand-in is the `FakeVercel` shape from `tests/test_sandbox_remote.py`, with one difference: the fake SDK's `create_sandbox` returns a **new** stand-in with a **new** VM folder on each call, and each stand-in gets its own `HOME` and its own `TMPDIR` under that folder (standing for `/tmp` and `/var/tmp`, which a stand-in on one host can't otherwise keep apart). Reuse the helpers by import if other test files in this folder import each other; otherwise copy the minimum. Kill any process a test leaves (`_kill_left_under` shows how).

Every plant test has two halves: first it runs the check in the build box (`box.run`) and asserts exit 0, which proves the plant is live; then it calls `plan_checks.verify(run, plan)` and asserts `result.passed is False`. The second half fails on `main`.

- **Red on `main` / green after**: after a build stand-in has a marker file in its `HOME`, `plan_checks.verify` with a quality block and a plan check: every command of the round ran in a stand-in other than the build one (read each stand-in's `calls`), the marker is absent there, and the fresh stand-in is destroyed afterwards. Also: `sandbox.current()` is the build box again after the round, including when a check raises.
- **Plants**: (a) `conftest.py` with `os._exit(0)` in the build VM's temp folder, check makes a folder with `mktemp -d`, writes a failing test there and runs pytest on it with the test's own interpreter; (b) the same with a `pytest_sessionfinish` that sets `exitstatus = 0`; (c) a `.pth` in the build `HOME`'s user site, run with an interpreter whose user site is on (the base interpreter, not the venv's); (d) `$HOME/.config/uv/uv.toml` and a file in `$HOME/.cache/uv`: the check is `test -e` on the planted path, a presence check, since showing uv obey the file needs the network; (e) an ignored `.venv/bin/pytest` that exits 0, with `.venv` in a tracked `.gitignore`, and the check runs it; (f) a `setsid` process that keeps writing `0` to an ignored file in the build tree, check `exit "$(cat <file> 2>/dev/null || echo 1)"`.
- **The patch carries the builder's work**: a changed tracked file, a deleted one, a new untracked one and a binary one all reach the fresh VM; the build VM's index is unchanged by the round (`git status --porcelain=v1` before equals after).
- **Judged first**: with the shipped roster and the shipped builder in `run.ran`, a patch that writes a path the builder may not raises `PermissionBreach`, creates no second stand-in, and leaves the host's branch, worktree and index as they were.
- **Fail closed**: `_create` raising for the second VM gives `SandboxFailed` and no check runs in the build stand-in. A failing `worktree_setup` in the fresh VM gives a failed result that names it, and no block runs.
- **Verify VM limits**: `start` and `commit` on a `verify_only` box raise; a successful `git commit` argv through `run` there does not call `commits_out`; no call to a fresh stand-in carries `CLAUDE_CODE_OAUTH_TOKEN` or `OPENAI_API_KEY`.
- **Local unchanged**: under `sandbox.LOCAL`, `verifying` yields `None` and `verify` runs as on `main`.
- **Chains**: a text test that no `.claude/skills/sssf/templates/adws/adw_*.py` calls `quality.run_quality(run)` or `quality.run_tests(run)` outside a `base=` argument.
- **Tree note**: a whole-tree commit whose tree differs from `verified_tree` logs `sandbox_commit_not_verified` and still lands.

Update `.claude/skills/sssf/tests/test_plan_checks.py` where it pins `RAN_IN_VM` / `RUNS_IN_VM` wording, and `.claude/skills/sssf/tests/test_sandbox_remote.py` only if a helper has to be shared.

### 6. Docs

- `docs/open.md`, the P2 row "In a VM, a builder can make a plan check pass that should fail": say it is fixed by this run, what it covers (every block and plan check, every `Remote`, every place outside the patch, left processes during verify) and move what stays open into new rows (step 7 and `## Out of scope`).
- `.claude/skills/sssf/references/config.md`: in "A local sandbox: `--sandbox openshell`" (about line 725) and wherever `vercel` and `e2b` are described, say where verify runs, what it costs (a VM start and a `worktree_setup` per verify round), and that a check which needs a file outside the diff (an ignored file, the handoff folder) no longer finds it.
- `docs/designs/remote-sandboxes.md`: an "As built" note for the verify VM, and fix any line that says checks run in the run's VM.
- `README.md` and `docs/sssf.md`: fix any line that says where verify runs under a VM. A CHANGELOG entry if the repo keeps one (none was found at the root).
- Docstrings named in steps 1 to 3.
- For the documenter: the `app_docs/` write-up of run 83c9ca03 gives the old `RAN_IN_VM` / `RUNS_IN_VM` wording; note that this run replaced it. Do not edit `app_docs/` in the build.

### 7. `sandbox: none`, checked and not fixed

Checked by the planner on 2026-11-26, as an agent under agentsh on the host: it could create and remove a file in `/tmp` and in `/var/tmp`. Verify runs on the host without agentsh, so a builder under agentsh can leave `/tmp/conftest.py` and host verify's pytest will load it for a plan check on a path under `/tmp`. Add a row to `docs/open.md`: Bug, P2, Guard change, found by this run's planner, with the fix options (a private `TMPDIR` for host verify that no agent could write, or an agentsh rule). Not fixed here.

## Verification

Run from `.claude/skills/sssf`, only the files the change touches (the builder is under agentsh and can't start a VM; verify runs the whole suite on the host):

```
uv run --with pytest --with pydantic --with pyyaml pytest -q -m 'not pushes and not host_only' tests/test_verify_fresh_vm.py tests/test_sandbox_remote.py tests/test_sandbox_openshell.py tests/test_plan_checks.py tests/test_sandbox.py tests/test_sandbox_usage.py
```

Use the runner and flags the repo's own test block uses if they differ (`adws/adw_modules/quality.py`, about line 155).

- Before writing the fix, write the red test and the plant tests and see them fail for the right reason; say so in the build report.
- All pass after the fix. Every existing test passes on the host; the new file is inside the usual suite, so the plan adds no check of its own.
- `git diff e40eba4 -- .claude/skills/sssf/templates/adws/adw_modules/sandbox_openshell.py` shows no change to `policy`, `READ_ONLY_PATHS` or `read_write`, and no prompt template or rule file is in the diff.

## Out of scope

Holes this change leaves open; a reviewer should file them as old holes. Rows 1 and 2 get a `docs/open.md` row each in step 6.

1. **The verified tree is not bound to the committed tree.** The patch comes from the build VM's git, and the build VM's tree can change after the patch is taken (a left process, or a forged patch). The reviewer reads and `commit_build` commits the build VM's tree. On `main` the same process can rewrite a file after verify too. This change logs `sandbox_commit_not_verified` when the trees differ and does not refuse. Fix: refuse, once the legitimate writes between verify and commit are mapped.
2. **`sandbox: none`**: step 7.
3. What the builder puts inside the diff, including a `conftest.py` or a changed test in the tree.
4. A check that changes files (a formatter that rewrites in place): on `main` its changes stayed in the build tree and were committed; now they are lost with the fresh VM. Documented in `config.md`, not restored.
5. Left processes in the build VM during the review and later agent calls.
6. The VM's network policy and what agents may reach. The text guard.
7. A chain a repo wrote itself that calls `quality.run_quality(run)` directly: it still verifies in the build VM. `config.md` says to call `plan_checks.verify`.

## For the engineer

- **The hub's own install.** `adws/` is protected, so the hub keeps running `main`'s behaviour until its install is updated from the templates, as `acb30cb` did. That covers `adws/adw_modules/sandbox.py`, `sandbox_remote.py`, `plan_checks.py` and the three chains in step 4.
- **Measure.** On the first OpenShell run after that, read the `sandbox_verify` events (`create_seconds`, `clone_seconds`, `setup_seconds`) and put the figures in `docs/open.md`'s row and `config.md`. The planner could not measure them.
- **The proof**, in a real OpenShell run, as the builder would: plant `/var/tmp/conftest.py`, `/tmp/conftest.py`, a `.pth` in the user site, a uv config, a patched `pytest` in `.venv`, and a `setsid` process that rewrites a file. Each should turn a failing plan check green on `main` and leave it failing after.
- **Decide the wording** (see `## Bar`): the plan changes `RAN_IN_VM` and `RUNS_IN_VM` so they stay true. If the prompts must be byte-equal to `main`, revert those two strings and accept that they describe the old place.
