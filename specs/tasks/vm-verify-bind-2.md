# Keep the verified tree bound until the chain ends it

This picks up guard run 3d9f277f, which stopped on 2026-11-27 because round 2's fix added a mechanism. Its round 1 head, 680da06, had no bypass. The engineer puts 680da06 on `guard/verify-bind-2` and launches this run from that branch with `--worktree --base guard/verify-bind-2`, on the hub's own config. So the baseline is round 1's code: dafc21f0's fresh VM per verify round, plus 3d9f277f's binding. Start from `specs/3d9f277f_commit-verified-tree_v3.md`, which is on that branch, and check it against the code there. The code is in `.claude/skills/sssf/templates/adws/adw_modules/sandbox_remote.py`, the chains under `templates/adws/` (`adw_simple_sdlc.py`, `adw_tickets.py`, `adw_build_test.py`, `adw_quality.py`), and the tests in `tests/test_verify_commit_tree.py` and `tests/test_verify_fresh_vm.py`.

## What's wrong at the baseline

`Remote._verified` holds the tree of the last green verify round. `VerifyBox.mark_green` sets it, and `commits_out` refuses any commit that comes back with a different tree, except at `wrote` paths. Two things clear it, and the bypass-reviewer found a fault with each.

1. **A false positive: only the chain's own commit ends the binding** (`commits_out`, `if must_move: self._verified = ""`). In `adw_tickets.py`, the last `tNN_commit` lands, `review_fix` runs and leaves no change, and `verify("review", …)` goes green with an empty patch. That sets the binding to the synced tree, and since nothing is left to commit, nothing ends it. The documenter's docs-only commit then differs at `app_docs/…` and is refused with `PermissionBreach`. The spec's own table says the documenter's commit is held to `judge_commits` only. The reviewer's plainest probe is a green round, no chain commit, then a docs-only commit.
2. **An old hole: a new round clears the binding before it runs** (`open_verify`, `self._verified = ""`). After a green round, a red or failed one leaves nothing bound, so a commit a process the builder left running makes with a bad tree lands at a sync or at `close()`. A left process can cause the failed round itself, by making `_patch_out`'s git fail. That run isn't accepted, but the bad commit is on `sssf/<id>`.

Round 2 fixed the first by binding nothing when the build VM's patch is empty (`VerifyBox(inner, self, tree if patch else "")`). Whether the patch is empty is the build VM's word, and a left process can forge it: with `git config diff.external true` in the build VM, `git diff` prints nothing, verify goes green on the last sync, and a bad `app.py` lands. That is why the run stopped. The reviewer showed two more forms: a left process that puts the good bytes back while `_patch_out` runs and the bad ones after, and one that commits a bad tree and then restores the good bytes.

## The change

The rule: **the host alone decides when a commit must hold the verified tree.** Nothing the build VM reports (whether its patch is empty, what its git says, what it committed) turns the check off.

1. **The binding lasts until the chain ends it.** From a chain's first verify round on, every commit that comes back must hold the tree of the last green round, except at `wrote` paths. A red or failed round doesn't clear it. A new green round replaces it with that round's tree. When the chain's own commit lands, the binding becomes that commit's tree. Before any round has gone green, nothing that comes back after the first round opens lands. Commits made before the first round, such as the plan commit, are judged as at the baseline.
2. **Each chain ends the binding where its verify ends,** with one host call of its own just before the documenter, so the documenter's commit is held to `judge_commits` only, as the spec's table says. The spec maps every chain that verifies under a VM (`simple-sdlc`, `tickets`, `build_test`, the guard chain, `quality`) and every commit each makes, and says where each ends the binding. A chain with no documenter ends it before it closes the VM, or not at all if `close()` then refuses what isn't bound.
3. **An empty patch binds the last sync's tree.** The host reads it from its own `self.synced`, never from the VM. Take out round 2's `tree if patch else ""`.
4. **Docs.** `references/config.md`'s "Where verify runs under a VM" and `docs/open.md`'s rows say which commits are held to the verified tree, which aren't (the documenter's, and any before the first verify round), and why.

## The bar

- **Red on the baseline, green after.**
  - The reviewer's round 1 false positive: a `tickets` run where the review fix changes nothing, then a docs-only commit. On the baseline it's refused; after, it lands.
  - Red after green: a green round, a red or failed one, then a left process's commit with a bad tree, at a sync and at `close()`. On the baseline it lands; after, it's refused and nothing of it lands.
- **Round 2's three plants are refused, after:** `diff.external` forging an empty patch; good bytes put back while `_patch_out` runs and bad ones after; a left-process commit with a bad tree, then the good bytes put back. Each lands the bad tree with round 2's change, so check that each test fails with round 2's line in place.
- **Round 1's plants stay refused:** the force-added ignored file, the missing blob with and without a process putting it back, skip-worktree, and a file changed between verify and the commit.
- **Ordinary runs are unchanged.** For each chain that verifies under a VM, a stand-in run with an ordinary change, a rename, a delete and a binary file passes, and each of its commits lands, the documenter's included.
- **No decision reads the VM.** A test shows that whether a commit is checked doesn't change with anything the build VM reports.
- **Nothing an agent may do changes.** The VM's policy, the agents' prompts and their rules are as on the baseline.
- **Every existing test passes on the host.**

## Running the tests

The planner and the builder run under agentsh on the host, so they can't start a VM. Use the stand-ins in `test_verify_commit_tree.py`, `test_verify_fresh_vm.py` and `test_sandbox_remote.py`. Run only the files the change touches, from `.claude/skills/sssf`, with `-m 'not pushes and not host_only'`, and leave the whole suite to verify.

## Out of scope

- The documenter's own commit, beyond `judge_commits`. It's held to the documenter's `writes`, and a left process can still change a Markdown file there; the spec names it as a known limit.
- The usage totals and dashboard (the P3 row from 3d9f277f's brief) and `sandbox: none`'s `/tmp/conftest.py` row.
- The text guard. Its patches are frozen.
