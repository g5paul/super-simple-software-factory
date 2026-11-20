# Let the build gate pass a file the builder renamed or deleted

Run it with `--worktree` and `--base main`, on the hub's own config. The gate lives in `gates.py`, which is guard code, so this goes through the chain.

## What broke

On 2026-11-19 an unlisted project's OpenShell run ended at build for $1.40. The builder moved three docs with `git mv`, then listed both the old and the new paths in `changed_files`. The list was accurate. `diff_matches_claims` (`gates.py`, about line 63) checks only that each claimed path exists, through `sandbox.current().exists`, so each old path failed with "claimed changed file does not exist". A file the builder deletes fails the same way.

Since 411114a, plan and build get one gate retry. So the builder now gets a correction, but the only way it can pass is to drop true entries from its list.

The gate runs after every builder and ticket-builder call in every chain: build, fix, revise, the ticket build and the review fix. `grep -n diff_matches_claims` in `templates/adws/` lists each call site. No test covers the gate today.

## The first run, set aside

Guard run caf45b46 (branch `sssf/caf45b46`, not merged) passed the chain. Its gate reads `git ls-tree -r HEAD` when the gate runs. The bypass-reviewer then found a bypass:

- The agent creates `fake.txt`, commits it, removes it, and claims `["fake.txt"]`. On `main` the gate fails it. On caf45b46 it passes as "deleted in the working tree". The hub session reproduced this.
- It works because `agents.py` runs the gates before `permissions.undo_commits`, so at gate time `HEAD` is whatever the agent left. `undo_commits` then resets the commit, but nothing judges the claims again, so the false claim stays in the envelope. The run's spec said the later commit checks find this. They only note it.
- `git commit-tree` and `git update-ref HEAD` do the same without `git commit`.

The same review found that caf45b46 still fails a true claim. A file the agent removed with `git rm` and then committed is no longer in `HEAD`, so its claim fails.

The chain's own review found that a forged loose tree object, written over `HEAD:<dir>`'s id in `.git/objects`, makes `ls-tree` list a file that was never there. Git checks the root tree's and the commit's hashes, not a subtree's. Objects served through `objects/info/alternates` do the same. The gate isn't a boundary, since on `main` an agent passes any claim by creating the file, so this needs a threat-model line, not code.

Read caf45b46's spec, `specs/caf45b46_gate-renamed-deleted-files_v2.md` on that branch, and its tests, `tests/test_claims_gate.py`. Reuse what holds, such as the command's isolation options and the odd-name cases. The rule below replaces the one it used.

## The change

1. **A claimed path passes if it exists, as today, or if the call deleted it.**
   - "Deleted" means a file in the tree of the commit the call started from, and gone from the working tree, whether staged or not, committed or not. That covers `git rm`, `git mv`'s source, a plain `rm`, a plain `mv`'s source, and any of those followed by a commit.
   - The start commit is the one SSSF recorded before the agent ran: `head_before` in `agents.py`, from `permissions.head(run)`. The gate gets it from SSSF, never from the repo at gate time, since the agent can move `HEAD`, a branch or a ref before the gate runs. The spec says how the gate receives it with no new module-level state or `global` in a guard module, and what the gate does when there is no start commit, such as a repo with no commits yet.
   - Read the start commit's tree where the agent's tree is, through `sandbox.current().run`, so a VM run asks the VM. Use the start commit's full id, never a ref name. Keep caf45b46's isolation: `--no-replace-objects`, the `GIT_NO_PROGRAMS` options from `utils.py`, no commit-graph, and no claim as an argument.
   - The spec picks the git command and says how it reads a name with odd bytes.
   - Run the git read only when some claimed path doesn't exist. A call whose claims all exist sends the same commands as on `main`.
   - Match a claim against git's names exactly, with no new normalization. The spec says how the gate judges `./x`, `src//x`, `x/../y`, an absolute path, and a path outside the repo. None of them passes as a deletion unless git prints that exact name.
   - A path that neither exists nor shows as deleted fails as today, with today's message.
   - The check's note says which held: "exists, <size>" as today, or "deleted in the working tree".
2. **Nothing else in the gate changes.** It doesn't start checking that the diff's files are all claimed. Widening the gate is a separate item.
3. **The builders' prompts say what to list.** `builder/user.md` and `ticket_builder/user.md` each get one line saying that `changed_files` lists every path the change touched, including a deleted file and both sides of a rename. This line is about what the agent reports, not what it may do.
4. **Docs.**
   - `create_adw.md`'s gate table, or the line that lists the gates, says what `diff_matches_claims` accepts.
   - Delete the `docs/open.md` row this run closes.
   - The spec's threat model says that a forged loose or alternate object can make the gate pass a made-up claim, on the host as in a VM, and why that's accepted: the gate isn't a boundary.

## The bar

- **Renames pass.** In a temp repo, a builder that `git mv`s a tracked file and claims both paths passes. The same goes for a plain `mv`, which leaves an unstaged deletion and an untracked file.
- **Deletes pass.** A claimed file removed with `git rm` passes, and so does one removed with plain `rm`.
- **Made-up paths still fail, with today's message:**
  - a path that never existed;
  - an untracked file that's gone, which git shows no deletion for;
  - a file deleted in an earlier commit, so absent from `HEAD`;
  - a path outside the repo, as `../x` or an absolute path;
  - a spelling of a real deleted file that git doesn't print, such as `./x` or `src//x`.
- **Commit-then-remove fails.** A file the agent creates, commits and removes, and then claims, fails with today's message. So does the same file made with `git commit-tree` and `git update-ref HEAD` instead of `git commit`. The test asserts the gate's verdict, before `undo_commits` runs.
- **Delete-then-commit passes.** A tracked file the agent removes with `git rm` and then commits passes when claimed.
- **A moved HEAD changes nothing.** With `HEAD`, the branch or a replace ref moved by the agent before the gate runs, the verdicts match those for the same tree with `HEAD` left alone.
- **Odd names.** A deleted file whose name holds a space, a non-ASCII letter or a newline passes when claimed by its real name.
- **The VM is asked, not the host.** Through `tests/test_sandbox_openshell.py`'s fake CLI or the sandbox test double, the gate's git read goes to the sandbox. The test asserts which command the sandbox received, with the start commit's full id in it.
- **Nothing else changes.** Every existing test passes. A call whose claims all exist sends no git command from the gate.

## Running the tests

Run the touched test files from `.claude/skills/sssf` with `-m 'not pushes and not host_only'`, and leave the whole suite to verify. No test starts a real VM or a real agent. Keep runs light, since the agentsh event store slows under heavy load (`docs/open.md`).

## Out of scope

- Checking that every changed file is claimed, or that claims and the diff match both ways.
- `git_helper.changed_files`, which slices each `git status --porcelain` line at 3, so a staged rename reads as `old -> new`. Add the `open.md` row caf45b46 wrote for it, and leave the code alone.
- `permissions.enforce` and the forged-object hole. It has its own `open.md` row.
- Judging the claims again after `undo_commits`.
- The other open rows.
