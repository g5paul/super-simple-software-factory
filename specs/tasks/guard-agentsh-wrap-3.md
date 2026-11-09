# Close the last agentsh wrap findings

Rollout step 1 of `docs/designs/agentsh-local-sandbox.md`, third run. The second, 8b06e76e, stopped at three rounds with one bypass, and each round closed the one before it. Its code is already on this branch, reviewed through snapshot ed1cd23. **Keep it and make only the changes below.** The requests are `specs/tasks/guard-agentsh-wrap.md` and `specs/tasks/guard-agentsh-wrap-2.md`, and their bars and out-of-scope lists still stand except where this file changes them. `specs/8b06e76e_finish-agentsh-wrap_v2.md` is the spec to revise. `docs/designs/agentsh/guard-run-8b06e76e.md` is the last run's report. The design doc's section "The second guard run, 8b06e76e" records the engineer's decisions below, so don't reopen them.

## What should change

- **The stop walk reads bytes.** `_kill_descendants` in `agentsh_guard.py` reads `/proc/<pid>/stat` with `read_bytes()` and splits on `b")"`, so a process name that isn't valid UTF-8 can't drop a process and its subtree from the walk. Change nothing else about the walk or its order. Text mode stays as it is.
- **The push rule crosses newlines.** `sssf-no-push` uses `args_patterns: ['^\S*\s+push(\s|$)', '(?s)^\S*\s+-.*push']`, as `docs/designs/agentsh/sssf-net.yaml` now does. `git -c $'a=b\nc' push` and `git -C $'/x\ny' push` are denied. `git commit -m $'line one\npush it'` and `git log --grep $'x\npush'` run. The engineer's earlier list stands.
- **A broken config template refuses.** With a `config_template` that is only a comment, `policies: 5`, `- a` or unparseable YAML, `check_machine` raises `AgentshRefused` with a message, not `TypeError` or `ParserError`.
- **The install lines warn.** `adw_agentsh_policy.py` prints, ahead of the sudo lines, that every agent must be stopped before running them. `sudo install` follows a link, so an agent running during the install could swap a temp file for a link to a root-only file and get it copied to `/etc/agentsh/sssf.yaml` at mode 0644. `config.md`'s install step says the same.
- **Known limits.** `config.md` adds these, each with what still applies:
  - `GIT_CONFIG_COUNT` with an alias, and `git send-pack`, reach exec without the word `push` where the rule looks. The network rules refuse GitHub.
  - `mv .git .git2`, an edit and a move back, on a run without `--worktree`. The after-call guard undoes it.
  - A repo whose path holds `push` has every `git -C <path>` denied. It fails closed.
  - A stop with the chain out of file descriptors, where neither `ps` nor `/proc` can be read, kills only the wrap.
  - A signal landing inside the walk can cut it short, and `_stopped` then blocks a second attempt.
- **Docs.** Update the design doc's "What got built" only where these change it.

## The bar

The earlier bars, plus:

- A process that is under the wrap at a stop and is reachable by the walk must not survive it because of its name.
- Any spelling the engineer's list says stays denied, or runs, must do so with the pattern text the generated policy holds.

Added accepted limits, not findings, because the engineer decided them: the file descriptor case and the signal inside the walk above. Don't propose recording the agent's pid from the attach line, which is new state. The double fork, SIGKILL to the wrap, the `sudo install` link once the warning prints, and the spellings listed under Known limits are not findings either.

## Tests

- A child under a non-UTF-8 name dies at a stop, with `ps` working, under a fake wrap that forks as `wrap.go` does. So does its own child.
- The push cases above, each way, against the generated policy's pattern text, in the joined form.
- `check_machine` refuses each broken template above with `AgentshRefused`.
- The install output names the warning ahead of the first sudo line.
- The earlier tests still pass, and the hub suite passes on a machine without the service.

## Out of scope

Everything else in the walk, and anything else the earlier requests left out: the session folder allow, running a real agent under the service, patching the agentsh fork, sudo, and turning this on in any repo.
