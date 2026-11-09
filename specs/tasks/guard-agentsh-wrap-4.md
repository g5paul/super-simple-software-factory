# Fix what the first real runs under agentsh found

Rollout step 2 of `docs/designs/agentsh-local-sandbox.md`. The wrap merged as b5d0b82, and the first chains under the real service ran in sssf-sandbox on 2026-11-09. The design doc's section "The first real runs" records what they found and what the engineer decided, so don't reopen those decisions. Everything here comes from the real service, the real Claude Code and real events. The requests before this one, `specs/tasks/guard-agentsh-wrap.md` to `-3.md`, still stand except where this file changes them.

A throwaway patch in sssf-sandbox proved fixes 1 to 3: with it, a full SDLC chain passed without `--worktree`, 13 phases, every agent wrapped. Build them here properly, with tests. The patch doesn't ship.

## What should change

1. **The push rule matched Claude Code's own git calls.** Under `--safe-mode`, Claude Code runs `git config user.name`, `git remote`, `git status` and `git log` with about 40 `-c` options, among them `hook.pre-push.enabled=false` and `hook.push-to-checkout.enabled=false`. `(?s)^\S*\s+-.*push` matched them, so five calls ended the planner in 5 seconds. Use `args_patterns: ['^\S*\s+push(\s|$)', '(?s)^\S*\s+-.*\spush(\s|$)', '(?s)^\S*\s+-.*\salias\.[^=\s]*=\S*push']`, tested on the fork's engine against the five real argv lists and every earlier case, and as `docs/designs/agentsh/sssf-net.yaml` now holds. `git -c alias.p=push p`, `git -c 'alias.p=!git push' p`, `git -c $'a=b\nc' push` and all of the engineer's earlier denied list stay denied. `git -C /x/push-tools status` now runs.
2. **A worktree agent couldn't read the refuse script.** Every Bash call goes through `adws/adw_modules/sssf_refuse.py` in the main checkout, which default-deny refused, so every call failed. Allow reading that one file.
3. **The events API returns `null` for a page past the end.** `_get` raised `TypeError`, which counted as a failed poll, so an agent that ran no command for 15 seconds was stopped as "the agentsh service stopped answering event polls". Treat `null` as no rows. A real API failure must still count.
4. **The refusal message names no command.** It read "last ``", because SSSF reads `command` and a `ptrace_execve` event carries `fields.argv`. Name the command from `argv`, shortened, in the message and the trace.
5. **The handoff folder on a `--worktree` run.** It sits in the main checkout at `adws/adw_data/sessions/<id>/`, outside the workspace, so the planner couldn't write `plan.md` and the run failed its gates. The engineer chose to allow read and write on `<main>/adws/adw_data/sessions` and everything under it, for every run. That lets an agent reach other runs' handoff files, as text mode already does. Put the rule after every deny rule, so secrets and git config stay denied inside it. Find any other path in the main checkout a worktree run needs, such as the `REFUSE_*` files, and allow only those. Name each in the spec.
6. **A relative symlink fell to agentsh's own fallback deny.** A `symlink` whose target was relative (`path2: testing`) was denied by `default-deny-files`, not by any SSSF rule. Find out whether the engine checks the target as a path of its own. If an agent making a relative symlink inside its workspace is refused, that's a false positive: fix it in the policy, without allowing a link that points at a denied file. If it can't be fixed in the policy, say so in the spec and in `config.md`.
7. **Tests from real data.** The fakes passed 1, 3 and 4. Add fixtures taken from the real runs:
   - the five real argv lists from item 1, checked against the generated pattern text in the joined form;
   - an events API that returns `null` past the end;
   - a `ptrace_execve` denial event with `fields.argv` and no `command`.

   `docs/designs/agentsh/first-real-runs/` holds them.

## The bar

The earlier bars stand, plus these:

- A real Claude Code call that the policy denies, with no agent action behind it, is a false positive.
- Item 5's allow must not reach anything an earlier rule denies: `.env` and the other secret files, credentials, `.git/hooks`, `.git/config`.
- A run that stops because of what the API returns when it works normally is a harm.

Accepted, not findings: the item 5 allow on every run's handoff folder, a relative symlink if the spec shows it can't be fixed in the policy, and every limit `config.md` already lists.

## Out of scope

- Moving the handoff folder into the worktree. That's the stronger fix, recorded for later.
- pi under the wrap, which needs its own policy and a real run first.
- Running a real agent under the service, sudo, patching the agentsh fork, and turning this on in a repo other than sssf-sandbox.
