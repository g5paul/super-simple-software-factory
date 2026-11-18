# Narrow the host commit check: the planner's grant and case variants

This follows `specs/tasks/guard-host-commit-check.md`, which guard run 755d88ad built and the engineer merged on 2026-11-17 as a0dd48f. Read that brief and its spec, `specs/755d88ad_host-commit-check_v2.md`; their bars carry over. Run it with `--worktree` and `--base main`, on the hub's own config.

The `bypass-reviewer` subagent found both holes reviewing 755d88ad. Their rows are in `docs/open.md` at P2 Medium. Both reach VM runs, so under decision 14 of `docs/designs/openshell-sandbox.md` they block. Its probes are in `adws/adw_data/sessions/755d88ad/bypass_probes/`: `vm_probes.py` and `local_probes.py` hold the candidates, and `results.md` their outcomes on base and head.

## The holes

### The builder's commit is judged with the planner's grant

`run.ran` lists the agents that ran since the last full commit that passed. `check_local_commit` and `Remote.commits_out` clear it only after a full commit, never after an `only` commit, because 755d88ad's spec said "the planner's other work stays in the tree". So in `adw_simple_sdlc.py` the plan commit (`only` is the planner's `writes`, `specs/`) leaves the planner in `run.ran`, and `commit_build` judges the builder's commit with `[planner, builder]`. A builder change to `specs/old.md`, a protected path only the planner may write, passes the host check (probe a1). With the builder alone in `run.ran`, the same commit is refused (a2).

The in-call check (`enforce`) still stops the builder's write. The host check is there for when the in-VM check is beaten, and there the planner's grant lets the write through. The engineer accepted the grant when merging 755d88ad, as a known cost, and asked for this run the same day.

What the grant still has to cover after a plan commit is less than the spec assumed. `commit_all` turns `only` into a pathspec and stages every change inside it, so a passing `only` commit leaves nothing inside `only` uncommitted. With every shipped roster, `only` is the planner's whole `writes`, so nothing the planner may write is left for the next commit. The grant matters only for a chain that commits less than an agent's `writes`.

### Case variants of a protected path pass

`permitted()` matches paths case-sensitively (`_matches`). A VM commit that adds `JUSTFILE` or `ADWS/adw_x.py` passes the host check (probe v07), and so would `.CLAUDE/settings.json`.
- **On a case-insensitive host** (macOS, Windows, or a folder Windows made under WSL's `/mnt/c`), the host's checkout of that commit writes over `justfile` or into `adws/`. That's the agent's code in the engineer's recipes or the chain's own modules.
- **On ext4** the file is separate, but it isn't harmless. `just` 1.59 looks for its justfile by any case. With `justfile` and `JUSTFILE` both present it refuses to run, naming both, and with `JUSTFILE` alone it runs the agent's recipes.

`_in_git_dir` already folds case, drops HFS-ignored code points and strips NTFS's `:stream` and trailing dots and spaces for `.git`. Protected paths get none of that.

## The change

1. **An `only` commit narrows the earlier agents' grant.** After an `only` commit passes, an agent that ran before it no longer passes a path inside an `only` entry. Its grant elsewhere stays, for the chain that commits less than the agent's `writes`.
   - With the shipped chains this gives the same answer as clearing `run.ran` after the plan commit: the builder's commit is judged with the builder's grant alone for `specs/`.
   - It holds in both places that clear `run.ran`, `check_local_commit` and `Remote.commits_out`, and across more than one `only` commit.
   - Narrow the existing check, with the state `run.ran` and `only` already give, or as little more as the spec shows it needs. Don't snapshot the tree: in a VM run the host can't read it without trusting the VM.
2. **Protected paths match in any spelling the host's checkout treats as theirs.** In `permitted()`, the factory-files check and the `protected_files` check compare a folded form of the path against a folded form of each entry. Fold the way `_in_git_dir` does for `.git`: case, HFS-ignored code points, and NTFS's trailing dots and spaces per component.
   - The spec lists each spelling git's checkout on macOS, Windows or a case-insensitive Linux folder writes over the real file. That includes Unicode case folding, which APFS and NTFS use, Unicode normalization (NFC and NFD), and NTFS 8.3 short names such as `JUSTFI~1`. Cite git's docs or source for each, and either fold it or say why git refuses it at checkout.
   - Fold only on the side that refuses. A path that folds to a protected entry is protected, and unlocking it still needs the agent's `writes` to name it, matched as today. The `writes` match and the always-writable match stay exact, so no path passes that `main` refuses.
   - Fold always, not only when `core.ignorecase` is true. `just` folds on every filesystem, and the host check runs before the files reach any checkout.
3. **Docs.** `config.md`'s sentence on `run.ran` says the planner's grant ends at the plan commit for the paths that commit took. "Write permissions" says a protected path is matched in any case and in the spellings the spec lists. Both `docs/open.md` rows close.

## The engineer's decisions

These are the hub session's recommendations, put to the engineer before the run as `docs/open.md`'s P2 row on guard briefs asks. A reviewer files a finding that matches one as meeting the bar once the engineer confirms it.

1. **Narrow the grant by `only`, not by a tree snapshot.** A snapshot of what the planner left uncommitted, bound to each file's blob as `carried_tree` does, would be stricter for a chain that commits less than an agent's `writes`. In a VM run the host would have to read that tree from the VM. No shipped chain needs it.
2. **Fold always, and only on the refusing side.** An agent whose `writes` names `justfile` can't write `JUSTFILE`. That's a false stop no shipped roster hits.

## The bar

- **a1 and a2.** After a plan commit of `specs/`, a builder commit that changes `specs/old.md` is refused, in local mode and through a VM stand-in from `tests/test_sandbox_remote.py`. The branch is back at the commit before (local) or at `synced` (VM), and the run stops naming the path.
- **The grant outside `only` holds.** A test chain commits `only=["specs/"]` for a planner whose `writes` are `["specs/", "docs/plans/"]`. The planner's uncommitted `docs/plans/x.md` passes in the next full commit, and a builder change to `specs/` in the same commit is refused.
- **More than one `only` commit.** Two `only` commits with different entries, then a full commit: each entry is judged without the agents that ran before its commit.
- **v07 and each listed spelling.** `JUSTFILE`, `ADWS/adw_x.py`, `.CLAUDE/settings.json`, and one path for each spelling the spec lists are refused by the host check, in local mode and through a VM stand-in. The same commit with the exact name is refused as on `main`.
- **No new pass.** For every path a test can name that `main`'s `permitted()` refuses for an agent, the head refuses it too. That covers the shipped rosters' agents, an agent with `writes: None`, and an agent whose `writes` names a protected path.
- **No false stop.** Every existing test passes, and every shipped chain's normal commits pass as on `main`: the plan commit, a builder's commit inside its `writes`, the documenter's `**/*.md`, the session files, and a ticket file `tickets.mark_done` writes.
- **Nothing else changes.** When every commit passes, the branch, the trace events and the saved patch are as on `main`.

## Running the tests

Run the touched test files from `.claude/skills/sssf` with `-m 'not pushes and not host_only'`, and leave the whole suite to verify. Mark any test that starts a command past agentsh's argument limits, 128 KiB per argument or 2 MiB in all, `host_only`, and never run those in an agent's run. No test starts a real VM.

## Out of scope

- The other rows from 755d88ad's review: the `give_up` traceback after a VM ticket run closes (P2 Low), the local-mode rows (P3), and the guard chain's own `commit_all` without `run` (P3, hub-only).
- `.justfile`, which `just` also reads. Next to `justfile` it makes `just` refuse to run, a nuisance rather than a bypass; list it in the spec's old holes.
- Making `sandbox: openshell` the default, which waits on the engineer (`docs/open.md`).
