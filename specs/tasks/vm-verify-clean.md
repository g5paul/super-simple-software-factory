# Keep a builder's leftovers in a VM out of verify

Run it with `--worktree` and `--base main`, on the hub's own config. Verify's results are what a run trusts before it commits, so this is guard work and goes through the guard chain. The code is likely in `.claude/skills/sssf/templates/adws/adw_modules/`: `sandbox_remote.py` (the `Remote` base every VM sandbox shares), `sandbox_openshell.py`, `quality.py` and `plan_checks.py`, with their tests under `.claude/skills/sssf/tests`.

This is `docs/open.md`'s P2 row that starts "In a VM, a builder can make a plan check pass that should fail". Run 83c9ca03 (bd5f52e) fixed its wording half. This run is the other half.

## What's wrong

Under a VM sandbox (`openshell`, `vercel`, `e2b`), verify runs in the same VM the builder just worked in, as the same user. `quality.py` (about line 83) runs each block with `sandbox.current().run`, and `plan_checks._run` (about line 40) runs each plan check the same way. The reviewer sees the diff, which shows tracked files and nothing else. So anything the builder leaves outside the diff can change what verify reports, and no one sees it.

The bypass-reviewer showed one case on guard run 2ebbe80a, on a Landlock mock of the VM policy. pytest loads a `conftest.py` from each parent of a test path outside its rootdir. A builder that writes `/var/tmp/conftest.py` or `/tmp/conftest.py` with `os._exit(0)`, or with a `pytest_sessionfinish` that sets `exitstatus = 0`, turns a failing plan check into a pass. A check that makes its folder with `mktemp -d` and runs pytest on a file there is exactly what plans write (run 12ec0782).

That is one hiding place of several. The engineer probed a throwaway OpenShell VM with SSSF's policy on 2026-11-26. An agent there runs as `ubuntu` with `HOME=/sandbox`. It can write `/sandbox`, `/tmp`, `/var/tmp` and the shared folder (the main checkout's path, which holds the run's worktree). Nothing else it tried was writable. In those places:
- **Python's user site** is on: `/sandbox/.local/lib/python3.12/site-packages`. A `.pth` file there runs code at the start of every `python3` that doesn't use a venv.
- **uv's cache** is `/sandbox/.cache/uv`, and its user config would be `/sandbox/.config/uv/uv.toml`. Either can change what `uv run --with pytest` installs.
- **Ignored and untracked files in the run's tree**, such as `.venv` from `worktree_setup`, never show in the diff. A patched `pytest` in `.venv` would.
- **A process the builder leaves running.** After each agent call, `RemoteProcess.stop_tree` kills the agent's session (`pkill -s`). A process that started its own session with `setsid` isn't in it, and could rewrite files during verify.

## The change

Verify, every block and plan check, under every VM sandbox, runs where nothing the builder did outside the commit it's checking can reach it. The spec picks the way and says why. Two ways:

1. **A fresh VM for each verify** (preferred). Take the changes the reviewer's diff shows out of the build VM, with the checks `commits_out` applies to commits today. `close` already takes uncommitted work out with `git add -A && git diff --cached --binary HEAD`, which leaves ignored files behind. Start a new VM from the same image, clone that state in, run `worktree_setup`, and run verify there. Then the review, and `commit_build` lands the same tree. This covers every hiding place above, and any the spec doesn't find, for every `Remote`. The cost is a VM start and a `worktree_setup` per verify round. The spec measures both for the hub and says what they add to a run. It says what happens to the build VM meanwhile and after.
2. **Reset the build VM before verify.** Kill every process the sandbox user owns, remove everything it can write outside the tracked tree, `git clean -ffdx` the tree, and run `worktree_setup` again. This is cheaper, but it's only as good as its list. If the spec picks it, it takes the writable places from the policy rather than writing a list by hand, shows the list is complete for OpenShell, and says what it does for `vercel` and `e2b`, whose writable places no policy SSSF writes names.

Either way:
- An agent learns nothing new from this. A refused or failed verify reports as it does today.
- `docs/open.md`'s P2 row says what the fix covers and leaves open, and `references/config.md`'s section on each VM sandbox says where verify runs.
- `sandbox: none` is out of the change, but the spec checks it. There, verify runs on the host without agentsh, and the builder runs under agentsh. If agentsh lets the builder write a place host verify reads, such as `/tmp/conftest.py` for a check under `/tmp`, the spec says so and adds a row to `docs/open.md`. It doesn't fix it in this run.

## The bar

- **Red on `main`.** A test with a stand-in `Remote` shows that verify's commands run in the sandbox the builder used, with its files. On `main` it fails.
- **Green after.** The same test shows verify runs where the builder's files outside the commit are absent: a fresh sandbox under option 1, or one where each planted file is gone and each left process is dead under option 2.
- **The plants, in tests.** For each hiding place above, a test plants it in the stand-in's build sandbox, then shows verify's failing check still fails.
- **Nothing an agent may do changes.** The VM's policy, the agents' prompts and their rules are as on `main`.
- **Every existing test passes on the host.**

## After the merge, the engineer's proof

Not part of the run. In a real OpenShell run, as the builder would, plant each of these: `/var/tmp/conftest.py`, `/tmp/conftest.py`, a `.pth` in the user site, a uv config, a patched `pytest` in `.venv`, and a `setsid` process that rewrites a file. Each should make a failing plan check pass on `main` and leave it failing after.

## Running the tests

The planner and the builder run under agentsh on the host, so they can't start a VM. Use stand-in sandboxes, as `tests/test_sandbox_openshell.py` and `tests/test_sandbox_remote.py` do. Run only the files the change touches, from `.claude/skills/sssf`, with `-m 'not pushes and not host_only'`, and leave the whole suite to verify, which runs on the host.

## Out of scope

- What the builder can do inside the diff. The reviewer reads that.
- The VM's network policy and what the agents may reach.
- The text guard. Its patches are frozen.
