# Stop SSSF's session-folder writes from following an agent's link

Rollout step 2 of `docs/designs/agentsh-local-sandbox.md`. The chain run 1df8cc16, from `specs/tasks/guard-agentsh-wrap-5.md`, stopped after round 3 because round 2's fix added a `..` link deny. This branch holds its round-2 snapshot 0185f91, without that rule. The symlink allow is gone, the alias pattern is widened, and both passed three reviews. **Keep all of it, and make only the change below.** `specs/1df8cc16_agentsh-drop-symlink-allow_v2.md` is the spec to revise. `docs/designs/agentsh/guard-run-1df8cc16.md` is the run's report. The design doc's section "The chain run without the symlink allow, 1df8cc16" records the engineer's decisions, so don't reopen them.

## What should change

1. **SSSF's own writes under the session folder replace a link instead of following it.** After each agent call, the host writes `<session_dir>/<agent>/envelope.json` with `Path.write_text`, which follows a symlink. On a `--worktree` run the sessions folder allow lets an agent put a link at that path, made in place or moved in from its workspace. Its target can be a file in the main checkout, `/proc/<pid>/fd/<n>` or `/dev/fd/<n>` for a file the host holds open, such as `sssf.db`. The reviewer showed the envelope write cutting a held-open SQLite file to 29 bytes. On a plain run the session folder is inside the workspace, so `main` has the same hole.

   The engineer chose the host-side fix on 2026-11-09: every host write under the session folder goes through one helper that writes a temp file in the same folder and renames it over the target. A rename replaces a link and never follows it. Find every write site, including envelopes, saved prompts, raw output logs, the cost ledger, `events.jsonl` and the diff `changes.py` writes into `context_handoff/`. Name each in the spec. A site that appends, such as a log, needs the same protection: open it so that a link at the path is refused or replaced, never followed. Say how in the spec.

   This is a narrowing, one way of writing, not a list of exceptions. Don't add any policy rule for link targets.
2. **Keep the sessions folder allow as it is.** Read and write on `<main>/adws/adw_data/sessions/**`, every operation, after every deny rule.
3. **Known limits.** `config.md` gets: an agent can write `.env.example` and `.env.sample` anywhere, because `sssf-env-examples` sits ahead of every deny, as on `main`. Remove the plain-run descriptor-link limit if the fix closes it, and say so.

## The bar

The earlier bars stand. A host write under the session folder that follows a link an agent placed there, whatever the link's target, is a harm. A new file rule judging link targets is a finding, as are a `..` deny or a list of target folders. The limits above and those `config.md` already lists are accepted, not findings.

## Out of scope

Moving the handoff folder into the worktree, a fork patch so agentsh resolves link targets, pi under the wrap, running a real agent under the service, sudo, and turning this on outside sssf-sandbox.
