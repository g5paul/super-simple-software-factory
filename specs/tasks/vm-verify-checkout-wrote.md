# Verify a checkout of the verified tree, and bind each `wrote` path to its blob

Run it with `--worktree --base main`, on the hub's own config. Both faults are in what a commit may hold after verify, so this is guard work and goes through the guard chain. The baseline is `main` at the merge of guard runs dafc21f0, 3d9f277f and 1befc519 (4a9eec4): each verify round under a VM runs in a fresh VM, and from the first round until `sandbox.verify_done()` every commit that comes back must hold the last green tree, except at `wrote` paths. Start from `specs/1befc519_verified-tree-binding.md` and check it against the code. The code is in `.claude/skills/sssf/templates/adws/adw_modules/`: `sandbox_remote.py` (`_judge_patch`, `open_verify`, `commits_out`), `permissions.py` (`judge_commits`), `git_helper.py` (`commit_all`), `tickets.py` (`mark_done`), and `templates/adws/adw_tickets.py`. The tests are in `.claude/skills/sssf/tests/`: `test_verify_commit_tree.py`, `test_verify_fresh_vm.py`, `test_tickets.py` and `test_adw_tickets.py`.

These are `docs/open.md`'s two P2 rows that start "A verify VM checks bytes a checkout of the committed tree doesn't have" and "A file the chain writes itself is passed by its path alone". The bypass-reviewer found both on 2026-11-27, reviewing 1befc519.

## What's wrong

1. **The verify VM checks bytes a checkout of the committed tree doesn't have (H1).** `_judge_patch` applies the build VM's patch to the last sync in a temporary index and writes the tree the commit must hold. `open_verify` then clones the last sync into a fresh VM and runs `git apply --binary` on the same patch there. When the patch adds or changes `.gitattributes`, the clone still has the old attributes, so `git apply` writes the files to disk under those. The checks then read bytes that differ from what a clean checkout of the verified tree gives. The reviewer's plant: the patch adds `app.py ident` to `.gitattributes` and a `$Id$` line to `app.py`, and the plan check compares that line. It passes in the verify VM and fails in a checkout of the landed tree. `*.sh eol=crlf` shows the same with line endings. On `main` before 4a9eec4, verify ran on the builder's own disk and the plant passed there too, but it breaks the claim 4a9eec4 makes: the commit holds what verify checked.
2. **A `wrote` path lets any content through (O1).** `commits_out` skips each `wrote` path when it compares a commit's tree with the verified one (`n not in wrote`), and `judge_commits` passes any change at a `wrote` path (`or path in wrote`). Only `adw_tickets.py` passes `wrote`: each ticket's `tNN_commit` names the ticket file, after `tickets.mark_done` sets its Status line. `mark_done` reads the ticket from the build VM's disk and writes it back there. After a green round, a process the builder left running can rewrite the ticket (its "Accept:" line, say), before `mark_done` reads it or after it writes, and the ticket's commit lands the rewrite. This is on `main` before 4a9eec4 too, through `judge_commits`.

## The change

1. **Verify runs on a checkout of the verified tree.** In the verify VM, the working files are what a clean checkout of the tree `_judge_patch` returned gives, under that tree's own `.gitattributes`. One way is to bring the commit the host built into the VM and check it out; another is `git rm --cached -r -q . && git reset --hard` after the apply. The spec picks one and says why, and says how it knows the result equals the host's tree (for example, `git write-tree` in the VM against the host's tree id, failing the round on a mismatch). An empty patch still checks the last sync.
2. **Each `wrote` path carries the blob the chain wrote.** `wrote` becomes a map from path to blob id, through `commit_all`, `Remote.commit`, `commits_out`, `check_local_commit` and `judge_commits`. A path passes only when the commit's blob there is that blob. Any other content at it, or a deletion, is judged like any other path, and `commits_out` refuses it when it differs from the verified tree.
3. **The chain chooses the ticket's bytes, not the VM's disk.** Under a VM, `mark_done`'s new text starts from the ticket's bytes in the last green tree on the host, not from what the build VM's disk holds when it runs. The chain hashes the text it writes and passes that blob with the path. Under `sandbox: none` it can keep reading the local file, since no binding applies there. The spec says what it does there and why.
4. **Docs.** `references/config.md`'s "Where verify runs under a VM" says the verify VM checks a checkout of the verified tree and that a `wrote` path is bound to its blob. `docs/open.md`'s two rows say what this run closes.

## The bar

- **Red on `main`, green after.** One test per plant, each through the guard sequence in `agents.py` and a stand-in `Remote`, as `test_verify_commit_tree.py` and `test_verify_fresh_vm.py` do:
  - `.gitattributes` gains `app.py ident`, `app.py` gains a `$Id$` line, and a plan check compares that line with what a clean checkout of the patched tree gives. On `main` the check passes in the verify VM. After, it fails there, as it does in the clean checkout.
  - The same with `*.sh eol=crlf` and a check on a `.sh` file's bytes.
  - In a tickets chain, after a ticket's green round, a left process rewrites the ticket's "Accept:" line before `mark_done` reads it. On `main` the rewrite lands in the ticket's commit. After, the commit holds the green tree's ticket with only its Status line changed.
  - The same, with the rewrite after `mark_done` writes and before the commit. On `main` it lands. After, the commit is refused as a `PermissionBreach`, nothing of it lands, and the branch stays at the last sync.
  - A local commit (`check_local_commit`) with a `wrote` path whose blob isn't the one passed is judged like any other path.
- **A real run is unchanged.** A builder's ordinary change, a `.gitattributes` change whose attributes don't change any file's bytes, a deleted file and a binary file each pass verify and land as on `main`. A tickets chain marks each ticket resolved and lands it.
- **Nothing an agent may do changes.** The VM's policy, the agents' prompts and their rules are as on `main`.
- **Every existing test passes on the host.**

## Running the tests

The planner and the builder run under agentsh on the host, so they can't start a VM. Use the stand-ins in `test_verify_fresh_vm.py`, `test_verify_commit_tree.py` and `test_sandbox_remote.py`. Run only the files the change touches, from `.claude/skills/sssf`, with `-m 'not pushes and not host_only'`, and leave the whole suite to verify.

## Out of scope

- `docs/open.md`'s P3 rows on a left process's commits at `close()` (O2, O4) and on the verify VM's error paths (H2, H3).
- `sandbox: none`'s `/tmp/conftest.py` row.
- The text guard. Its patches are frozen.
