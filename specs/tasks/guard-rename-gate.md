# Let the build gate pass a file the builder renamed or deleted

Run it with `--worktree` and `--base main`, on the hub's own config. The gate lives in `gates.py`, which is guard code, so this goes through the chain.

## What broke

On 2026-11-19 an unlisted project's OpenShell run ended at build for $1.40. The builder moved three docs with `git mv`, then listed both the old and the new paths in `changed_files`. The list was accurate. `diff_matches_claims` (`gates.py`, about line 63) checks only that each claimed path exists, through `sandbox.current().exists`, so each old path failed with "claimed changed file does not exist". A file the builder deletes fails the same way.

Since 411114a, plan and build get one gate retry. So the builder now gets a correction, but the only way it can pass is to drop true entries from its list.

The gate runs after every builder and ticket-builder call in every chain: build, fix, revise, the ticket build and the review fix. `grep -n diff_matches_claims` in `templates/adws/` lists each call site. No test covers the gate today.

## The change

1. **A claimed path passes if it exists, as today, or if git shows the change deleted it.**
   - "Deleted" means tracked in `HEAD` and gone from the working tree, whether staged or not. That covers `git rm`, `git mv`'s source, a plain `rm`, and a plain `mv`'s source.
   - Read git's view where the agent's tree is, through `sandbox.current().run`, so a VM run asks the VM. Pass the same `-c` options as `GIT_NO_PROGRAMS` in `utils.py`, so the read runs no hook or fsmonitor.
   - The spec picks the git command, such as `git status --porcelain=v1 -z` or `git diff --name-status --no-renames -z HEAD`. It says why that command lists both a staged rename's source and an unstaged deletion, and how it reads a quoted name.
   - Run the git read only when some claimed path doesn't exist. A call whose claims all exist sends the same commands as on `main`.
   - Match a claim against git's names exactly, with no new normalization. The spec says how the gate judges `./x`, `src//x`, `x/../y`, an absolute path, and a path outside the repo. None of them passes as a deletion unless git prints that exact name.
   - A path that neither exists nor shows as deleted fails as today, with today's message.
   - The check's note says which held: "exists, <size>" as today, or "deleted in the working tree".
2. **Nothing else in the gate changes.** It doesn't start checking that the diff's files are all claimed. Widening the gate is a separate item.
3. **The builders' prompts say what to list.** `builder/user.md` and `ticket_builder/user.md` each get one line saying that `changed_files` lists every path the change touched, including a deleted file and both sides of a rename. This line is about what the agent reports, not what it may do.
4. **Docs.**
   - `create_adw.md`'s gate table, or the line that lists the gates, says what `diff_matches_claims` accepts.
   - Delete the `docs/open.md` row this run closes.

## The bar

- **Renames pass.** In a temp repo, a builder that `git mv`s a tracked file and claims both paths passes. The same goes for a plain `mv`, which leaves an unstaged deletion and an untracked file.
- **Deletes pass.** A claimed file removed with `git rm` passes, and so does one removed with plain `rm`.
- **Made-up paths still fail, with today's message:**
  - a path that never existed;
  - an untracked file that's gone, which git shows no deletion for;
  - a file deleted in an earlier commit, so absent from `HEAD`;
  - a path outside the repo, as `../x` or an absolute path;
  - a spelling of a real deleted file that git doesn't print, such as `./x` or `src//x`.
- **Odd names.** A deleted file whose name holds a space, a non-ASCII letter or a newline passes when claimed by its real name.
- **The VM is asked, not the host.** Through `tests/test_sandbox_openshell.py`'s fake CLI or the sandbox test double, the gate's git read goes to the sandbox. The test asserts which command the sandbox received.
- **Nothing else changes.** Every existing test passes. A call whose claims all exist sends no git command from the gate.

## Running the tests

Run the touched test files from `.claude/skills/sssf` with `-m 'not pushes and not host_only'`, and leave the whole suite to verify. No test starts a real VM or a real agent. Keep runs light, since the agentsh event store slows under heavy load (`docs/open.md`).

## Out of scope

- Checking that every changed file is claimed, or that claims and the diff match both ways.
- `git_helper.changed_files`, which slices each `git status --porcelain` line at 3, so a staged rename reads as `old -> new`. If the spec confirms that's a bug, it adds an `open.md` row and leaves the code alone.
- The other open rows.
