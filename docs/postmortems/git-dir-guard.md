# Post-mortem: the git-directory guard took 17 review rounds

Date: 2026-11-03. Branch `git-dir-guard`, merged to `main` at 4d139e1.

## What happened

The guard saves the git directory and git config before each agent, puts back
any change after it, and stops the run. It was built and reviewed in one day,
in one hub session. The standing rule says to run the bypass reviewer before a
guard change merges, and to merge once a run comes back clean. Seventeen runs
later, none had come back clean. The branch merged on a weaker bar that the
engineer chose: nothing the branch does is worse than `main`. The last round
met that bar with one exception the engineer accepted (a file name that isn't
valid UTF-8, where `main` keeps the engineer's files only because it crashes).

## Timeline

| Rounds | Commits | What the rounds found |
|---|---|---|
| 1 to 5 | 36a88fb to 7ffbef4 | Real gaps in coverage: a git breach skipped the commit undo and rollback, the global config was unwatched, the copy sat in /tmp, credentials were copied, includes and `core.hooksPath` were missed, a guard error dropped the breach, a toggle race, `..` in a path. |
| 6 to 10 | 57d84da to e670bfc | Symlinks. Each fix for links caused a new harm: the restore deleted `~/.gitconfig` under a symlinked HOME, deleted nested links, then `ln -s` wrote into a folder the agent swapped in. The engineer narrowed the promise: a change through a link is reported, never written back. |
| 11 | 7ec44bc | A config pin for SSSF's own git, added to close a round-10 finding, made the rollback delete the engineer's `.env` and `node_modules`. |
| 12 to 16 | 04e0fc5 to 21c9429 | A "keep new files" mode, then an ignored-file shelter in three shapes. Every round found a bypass in the newest shape. |
| 17 | 73403b4, 4d139e1 | The shelter removed and the rollback back to `main`'s. No bypass. |

Round 7's first run also died after three seconds and nobody saw it. Since
then each review writes a progress log that a stall watch reads.

## Why it took 17 rounds

**The bar moved.** The promise grew with the findings: put back everything,
then report links instead of writing through them, then keep SSSF's own git
from being steered, then never delete the engineer's files. Each review was
briefed with the newest promise and tested that. "Nothing worse than `main`"
arrived at round 11. With it in place from the start, rounds 6 to 16 would
have been judged against `main` rather than against a promise still being
written.

**Fixes added mechanisms.** Round-6 onward findings were answered with new
machinery: link restoring, the config pin, the keep mode, the shelter. Each
one was new surface for the next review. The merged design removed all four.
It is close to what round 5 could have shipped: restore the git directory,
name what can't be put back, use `main`'s rollback. Nothing asked whether a fix
narrowed the design or grew it.

**The problem has no complete answer at this level.** The agent runs as the
engineer's user, so a guard in the same process catches mistakes and casual
attempts, not a determined agent. An adversarial reviewer will always find one
more case. `--sandbox` and `--container` are the real isolation. A clean review
was never the right exit, and nothing else was defined.

**One session held every role.** The same session planned, built, wrote the
tests and briefed the reviewer, so its framing reached every step. The tests
used `protected_files: ["adws/"]` instead of the shipped config, and a round-13
bypass hid behind that. No one reviewed the design before it was built; the
bypass reviewer hunts bypasses, and nothing asked for a simpler design.

## What was missing

The back end of a delivery cycle ran 17 times. The front end never ran:

- a spec with a threat model, the baseline (`main`), the acceptance bar and
  what's out of scope
- a review of that spec before any code
- a rule for when to stop and hand the decision back

## Changes

1. **The spec states the bar.** A guard change's spec names the threat model,
   the baseline, the acceptance bar and what's out of scope. A spec review
   passes it before the build starts.
2. **Findings are sorted against the bar.** The reviewer files each finding as
   a bypass against the baseline, a harm against the baseline, a broken
   promise, or an old hole. Only the first two block a merge.
3. **A fix that adds a mechanism stops the loop.** So does a fourth round.
   Either one goes to the engineer with the findings so far.
4. **Tests use the shipped config.** A guard test builds its config from the
   `sssf.config.yaml` SSSF ships, not a test-only one.

`docs/designs/hub-guard-chain.md` builds these into the hub's guard-change
chain.
