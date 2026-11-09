# Finish the agentsh wrap

Rollout step 1 of `docs/designs/agentsh-local-sandbox.md`, second run. The first, cf9d8014, stopped for the engineer after two rounds with no bypass. Its code is already on this branch, reviewed through snapshot 940f81b. **Keep it and change it. Don't rewrite it.** The request is `specs/tasks/guard-agentsh-wrap.md`, and its bar and out-of-scope list still stand except where this file changes them. `specs/cf9d8014_agentsh-local-wrap_v3.md` is the spec to revise. `docs/designs/agentsh/guard-run-cf9d8014.md` is the first run's report. The design doc's section "The first guard run, cf9d8014" records the engineer's decisions below, so don't reopen them.

## What should change

- **The push rule.** `sssf-no-push` uses `args_patterns: ['^\S*\s+push(\s|$)', '^\S*\s+-.*push']`, as `docs/designs/agentsh/sssf-net.yaml` now does. agentsh matches against all of argv joined by spaces, `argv[0]` included, which is why the pattern skips a first token. `git commit -m "fix the push path"`, `git stash push` and `git log --grep push` run. `git push`, `git -c a=b push`, `git -c "a=b c" push`, `git -C /x push`, `git --git-dir /x push` and `git -c alias.p=push p` stay denied. So does `git -c a=b commit -m push`, a false positive the engineer accepts because it fails closed.
- **Install files leave the repo.** `adw_agentsh_policy.py` writes the policies and the merged config to a fresh `tempfile.mkdtemp` folder, prints the sudo lines naming that folder, and writes nothing under the repo. `_write`, its link refusal and the `adws/adw_data/agentsh/` folder go. Nothing is added to the gitignore lists. That closes the install script harm and the untracked-files finding by removing the path, and the reviewer should confirm that.
- **The installed config is compared whole.** `check_machine` refuses unless `/etc/agentsh/sssf.yaml` equals, byte for byte, what SSSF renders for this machine, with one exception: `policies.allowed` differs by machine, so compare the rest with that list normalized, and separately require this repo's policy names in it. This replaces the single-key checks, `policies.dir` and `detect_project_root` included, so `ptrace.trace.execve: false` or `on_attach_failure: fail_open` refuses. An installed policy is compared to what's generated in memory at launch, not to a file.
- **Remove the new state.** `_finished` and its 30-second wait go. The outcome order is the spec's: reaching three named exec denials raises `RuleBreach` with `refusals=n` and keeps the call's spend, whatever a later read does. A service failure is a separate stop, and it also keeps the spend, since usage is read before the raise. A failed final read must not turn a clean call into lost spend or replace a breach. Ending a session stays best effort under SIGTERM, without a new event or wait. A session a signal leaves open is a limit to record in `config.md`.
- **The missing variable.** The wrap must set `JUST_NO_DOTENV=true`. The first request required it, the spec dropped it and no review caught it. Without it `set dotenv-load` breaks recipes under the wrap, because `.env` is denied. A test names all of `AGENTSH_CONFIG`, `AGENTSH_SERVER`, `AGENTSH_NO_AUTO`, `NODE_USE_ENV_PROXY` and `JUST_NO_DOTENV`.
- **The validators.** `network_allow` rejects what the kernel or a resolver reads as an IP, `0x7f.0.0.1` and `0x7f.1` among them. `secret_glob` escapes a backslash as well as braces, so the literal name stays protected. `deny_exec.command` must be a string and is always quoted in the YAML, so `null` and `true` can't change type. A pending text call during a service failure stays as it is at baseline, a breach, and the reasoning goes in the spec.
- **The test that reaches a real trace.** `test_non_git_directory_text_goes_on_and_agentsh_exits_plainly` sets `GIT_CEILING_DIRECTORIES` for the subprocess. Run from a temp folder inside the hub, it wrote a session to the hub's own trace. Any other test that starts a session from `tmp_path` gets the same.
- **Docs.** `config.md`'s known limits add the main checkout's `.claude/` secrets on a `--worktree` run, an `argv[0]` holding a space, and a session a signal leaves open. The design doc gets a "What got built" section and the first real run's checks. Leave the rest of the design doc as it is.

## The bar

The first request's bar, plus these:

- Any spelling the engineer's list says stays denied is a bypass if it isn't denied. Judge the push rule with the real argv-joined form. The test above must use the same pattern text the generated policy holds.
- A config with enforcement turned off anywhere in it is a bypass if `check_machine` passes it.
- A final state of the run that depends on poll timing, or that drops a recorded breach or a call's spend, is a harm.

Added accepted limit, not a finding: an `argv[0]` holding a space. `exec -a "a b" git push` gets past `sssf-no-push`, because the joined text can't tell it from an argument. Getting there takes a deliberate spoof, a copied binary already does the same with less effort, and the network rules still refuse GitHub. A text check on the command line stays on as well.

## Tests

- The push cases above, each way, against the generated policy's pattern text, with Go's join form: argv joined by spaces with `argv[0]` first.
- The install script leaves the repo's `git status` unchanged and works when `adws/adw_data` is a symlink. It writes nothing through it.
- `check_machine` refuses each enforcement setting turned off, one test per key, and passes a machine whose `policies.allowed` holds other repos' names too.
- Three denials with a failing final read raise `RuleBreach` and keep the spend. A clean call with a failing final read keeps the spend and stops.
- The earlier tests that still apply pass, and the hub suite passes on a machine without the service.

## Out of scope

- The allow on the main checkout's session folder that a `--worktree` run may need. The engineer decides that at rollout step 2, on evidence from a real run.
- Running a real agent under the service, patching the agentsh fork, sudo, and turning this on in any repo.
