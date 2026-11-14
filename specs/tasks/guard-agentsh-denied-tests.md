# Let a run pass when agentsh denies only a test's own fixture

This is open.md's P2 High row "agentsh's secret-file rule denies a test its own `.env` fixture" and the P4 row on the builder's `blocked` status. The engineer chose the prompt fix on 2026-11-14: the builder and the reviewer take the host's verify run as the suite's result when a test fails only because the sandbox denied it. Run it with `--worktree` and `--base main`, on the hub's own config.

## The problem

pi-agent-observability's run ef480069, `adw_simple_sdlc` on the pi roster under `local_guard: agentsh`, failed with its code done:

- The repo's extension test writes a decoy `.env` into a `mkdtemp` folder under `/tmp`. agentsh's `sssf-secret-files` rule denies every `**/.env`, so inside the agent the test fails with `EACCES`. That's the rule working as designed.
- The builder's run of `bun test apps extension shared` inside agentsh exited 1, with 19 passing and those 3 failing. It reported this honestly in `notes_for_next_agent`.
- `verify_1` ran the same command on the host, outside agentsh, and passed 22 of 22.
- The reviewer approved 10 of 11 requirements and blocked on the 11th: the plan's acceptance command "has not passed". It doesn't run tests, and its brief carries only the builder's envelope (`previous=build` in `adw_simple_sdlc.py`), so it took the builder's in-sandbox result and never saw verify's.
- The revise builder couldn't make the denied tests pass, which is right, since it must not get around a denial. It first returned `status: "blocked"`, which `BuildOutput` refuses (`success` or `fail`), and then `fail` on SSSF's retry. The run ended.

Any repo whose tests write a `.env`, `.envrc` or other secret-named fixture hits this on its first agentsh run.

## The design

This brief settles the design. Spend the spec on making it exact.

1. **The builder names a denied test and moves on.** In `prompt_engineering/builder/system.md` and `prompt_engineering/ticket_builder/system.md`, add a line: a test that fails only on a permission error the sandbox gave (`EACCES`, `EPERM`, "Permission denied", "Operation not permitted") on a path the agent's code didn't make unreadable is not the builder's to fix. It must not skip, weaken, mock out or delete the test, and must not reach the path another way. It reports `success` when everything else passes, and names each such test and its error in `notes_for_next_agent`. SSSF's verify runs the plan's checks on the host, outside the sandbox, after the builder. A failure with any other cause is still the builder's to fix. Keep the existing line "A refused or denied probe is an answer, never a reason to try the real file another way", and make the new line agree with it.
2. **The builder's two statuses.** `prompt_engineering/builder/user.md` says `status` is `success` or `fail`, and that work it can't finish is `fail` with the reason in `summary`, as `ticket_builder/user.md` already says.
3. **The reviewer sees verify's result.** In `adw_simple_sdlc.py`, the code passes the latest verify's outcome into each review's brief: that verify passed on the host, and the checks it ran. `review_N` runs only after a green verify, so the brief can say so. `adw_tickets.py` does the same for its axis reviewers if they can block on a suite result; check its order before adding anything. `adw_build_review.py` has no verify, so its reviewer gets no such line.
4. **The reviewer rules on it.** In `prompt_engineering/reviewer/system.md`, and `standards_reviewer` and `spec_reviewer` if they can block on a command result: a requirement that a command or suite passes is met when the brief says verify passed it on the host. A failure the builder saw inside the sandbox on a permission error is then a note, not a finding. With no verify line in the brief, the requirement stays not met, with the reason that nothing ran it outside the sandbox, as today.

Decided by the engineer on 2026-11-14, so not open here: allowing `.env` under `/tmp`, `/var/tmp` or `/dev/shm` in the agentsh policy was turned down. agentsh judges by path, so a hard link or symlink in `/tmp` to the repo's real `.env` would pass. The policy, `agentsh_sssf.yaml`, `agentsh_guard.py` and `scripts/agentsh_install.py` don't change in this run. Neither do the rows on `/mnt/wsl/resolv.conf` and on hard links at install.

## The bar

- **No gate loosens.** Every code commit in `adw_simple_sdlc` and `adw_tickets` still needs a green verify on the host first, and `reverify` still runs after a revision. A builder that calls a real failure a denial gets caught there, and the fix loop hands it the host's failures.
- **No prompt invites a way around a denial.** No new line may suggest reaching a denied path by another name, a link, a copy, an env var or a different tool, or turning the sandbox off. The bypass reviewer judges the new prompt text for that the same way it judges code.
- **The reviewer's line comes from code, not from the builder.** The verify line in the brief is written by the chain from the verify phase's result. Nothing in the builder's envelope can make the reviewer believe verify passed.
- **Tests.** One pins that `adw_simple_sdlc`'s review brief carries verify's result and the checks. One pins that `adw_build_review`'s doesn't. Tests that pin the prompts' secret-file lines keep passing.

## Out of scope

The agentsh policy and its install, the obs extension, and making any denied test pass under agentsh.
