# A guard-change chain in the hub

Status: built, 2026-11-03, as `adws/adw_guard_change.py`. "As built" lists
where the build differs from this design.

## Why

Hub work happens outside SSSF. This session plans a change, edits the files,
runs the suite, starts the bypass reviewer, reads its report and merges, and
none of it lands in a trace. The git-directory guard took 17 review rounds
on one branch. The only record of them is commit messages and this session's
chat, and one round stopped after three seconds without anyone seeing why.

The rounds also showed what the work lacked besides a trace.
`docs/postmortems/git-dir-guard.md` has the account. In short, there was no
spec stating the bar, no design review, and no rule for when to stop, and
fixes kept adding mechanisms that the next round broke. The chain below adds
those steps.

A run records exactly what was missing: each phase, its agent, cost, gate
verdicts and stalls. So guard changes in the hub should go through a chain,
the way project work does, and show up in `just obs-all` beside every other
repo.

## The chain: `adw_guard_change.py`

```
engineer(request) -> planner -> spec_reviewer -> git(commit_plan)
  -> builder -> code(verify: the hub suite) [-> builder(fix) ... bounded]
  -> bypass_reviewer [-> builder(fix) -> code(verify) -> bypass_reviewer ... at most 3 rounds]
  -> git(commit_build)
```

- **planner** writes the spec, as in `adw_simple_sdlc`. It reads the guard
  module, `references/config.md` and the tests. The spec has four required
  sections:
  - **Threat model:** what the agent can do, and what this guard can't stop
    at its level, since the agent runs as the engineer's user.
  - **Baseline:** the ref the change is judged against, `main` by default.
  - **Bar:** what the change must do and must never do against the
    baseline. The default is the one the git-directory guard merged on: no
    agent file kept, no agent code run, and no engineer file deleted or
    overwritten, where the baseline does otherwise.
  - **Out of scope:** the holes the change won't close, so the reviewer
    files them as old holes rather than blockers.
- **spec_reviewer** checks the spec before any code. It checks the four
  sections are present and concrete, and asks whether a smaller design meets
  the same bar. It is the existing spec_reviewer role, briefed for guard
  work.
- **builder** changes the guard code and adds a test for each case it fixes.
  A guard test builds its config from the `sssf.config.yaml` SSSF ships,
  never a test-only one. A round-13 bypass hid behind a test config that
  protected all of `adws/`.
- **verify** is `quality.py` over the hub suite (`pytest tests` from the skill
  root). Code, not an agent.
- **bypass_reviewer** is a new roster agent. Its prompt starts from
  `.claude/agents/bypass-reviewer.md`, in the hub's `adws/` prompt folder,
  with the finding classes and a JSON report added. It writes only under its
  scratch directory.
- **bypass_reviewer** is briefed with the spec, not with the operator's
  latest summary, so the bar can't drift between rounds.
- **The loop.** A blocking finding goes back to the builder as a fix,
  re-enters verify, and the reviewer runs again on the new head. Two things
  stop the loop and hand the run to the engineer with the findings so far:
  - a fourth round
  - a diff check that finds new module-level state or a new `global`
    statement in a guard module
  - a fix that adds a mechanism rather than narrowing one: new state, a new
    mode, a new list of exceptions. The reviewer flags this, using the field
    below. The git-directory guard added four such mechanisms (link
    restoring, a config pin, a keep mode, an ignored-file shelter), and the
    merged design removed all four.

  This replaces the CLAUDE.md rule's "a second run is clean". An adversarial
  reviewer of same-user guard code always finds one more case, so "clean" is
  not a reachable exit.
- **No merge.** The run leaves the branch with its commits. Merging to `main`
  stays the engineer's step, the same as in every project.

### The reviewer's output

A new type, `BypassReview`:

| Field | Meaning |
|---|---|
| `bypasses` | each one: the candidate as a string literal, the check it passes, base and head verdicts, the diff line |
| `harms` | the same shape: head deletes, overwrites or runs what the baseline doesn't |
| `broken_promises` | the change misses something its spec says, with neither a bypass nor a harm against the baseline |
| `old_holes` | the same shape; holes the baseline shares, or the spec puts out of scope. Each says whether it is new to the known limits in `references/config.md` |
| `false_positives` | harmless input head stops |
| `adds_mechanism` | true when the fix under review added state, a mode or an exception list, with a line saying which |
| `candidates`, `checks`, `suite` | the counts |

A gate, `no_bypass`, passes when `bypasses` and `harms` are both empty. Those
two are judged against the spec's baseline and bar. `broken_promises`,
`old_holes` and `false_positives` go into the run's write-up and the trace,
so the engineer reads them without them stopping the run. A new false
positive that blocks ordinary agent work is worth a stop too; that is the
second gate, `no_false_positive`, on by default.

## Which guard runs the chain

The chain tests a change to the guard, and runs under a guard. If it ran
under the branch's guard, a broken guard could let its own review agent
through unchecked. So it runs under `main`'s.

That falls out of how SSSF installs. `install.py` stamps the skill's
templates into `adws/` at the repo root, and `sssf-update` refuses while the
skill's source is on an unmerged branch. In the hub, the chain lives in
`adws/` and was stamped from `main`; the change under test lives in
`.claude/skills/sssf/`. The reviewer unpacks base and head from git, as it
does today, and never imports from either live folder.

## Installing SSSF in the hub

The hub becomes a project like the others, with these differences:

- **Branch.** `main` is the hub's working line, so SSSF's stamped files go on
  `main` itself, not on an `sssf-run` branch. Runs cut their own branches.
- **`quality.py`** has two blocks: the skill's suite, with the command in
  each test file's docstring, and the chain's own tests in `adws/tests/`.
- **`protected_files`** adds `.claude/agents/`, `CLAUDE.md`,
  `CLAUDE.local.md`, `docs/projects.md` and `adws/tests/` to the shipped
  list, which already covers `adws/` (the guard the chain runs under).
- **`agent_rules`** adds the hub's push rules, as a refuse rule: no
  `git push`, and none of the `gh` commands that write to GitHub.
- **Roster.** The Claude Code roster as shipped, plus `guard_spec_reviewer`
  and `bypass_reviewer`, both on Opus. `max_run_cost` is $20, for three
  Opus reviews, and `max_worktree_runs` is 1.
- **Listed.** The hub is public, so its row goes in `docs/projects.md` like
  sssf-sandbox's.

## What changes for this session

This session is the hub's operator for guard changes. It writes the ADW
prompt from your request, launches the chain, watches the trace and reports,
the way the project sessions do. The `bypass-reviewer` subagent stays, for a
quick check outside a run. The CLAUDE.md rule now sends guard changes through
`adw_guard_change`. It first also asked for a subagent review before every
merge. The first live run, cdd093fe, passed the chain and the subagent then
found a bypass, so the rule became three clean runs in a row. f11ed7c0,
d6fb947e and d071d025 met it, and the engineer dropped the step on
2026-11-03. A bypass the subagent finds after a passing run brings it back.

## Out of scope

- Non-guard hub changes (docs, cookbooks). They can use `adw_simple_sdlc`
  once SSSF is in the hub; nothing here requires it.
- Sending updates to the project repos. That stays a hand-off.

## As built

- **Head is a snapshot commit.** The build stays uncommitted until the end,
  so the chain commits the working tree through a throwaway index before
  each round, and the reviewer unpacks that commit. The tree, the index and
  the refs stay as they were.
- **`no_bypass` and `no_false_positive` are loop decisions, not gates.** An
  SSSF gate sends its correction back to the same agent, and a bypass is the
  builder's to fix, not the reviewer's. The reviewer's report has a gate of
  its own, `review_well_formed`.
- **The spec reviewer is its own role,** `guard_spec_reviewer`. The existing
  `spec_reviewer` prompt reviews a diff against its spec, and a role has one
  prompt.
- **The chain runs the suite, not the reviewer.** Verify has already run it
  on head, so the reviewer's brief carries the result.

Smaller choices the design left open:

- The state check covers `sssf_rules.ts` too: a new top-level `let` or `var`
  counts. It runs on the first build as well, but there it only reports;
  only state added by a fix stops the run.
- A rejected spec goes back to the planner once. A second rejection stops
  the run before any code.
- `--base` sets the baseline, `main` by default, and the chain refuses to
  start on `main` itself.

## Settled questions

The engineer settled these on 2026-11-03.

1. **Old holes open no tickets.** They go in the write-up and the trace. The
   reviewer compares each one with the known limits in
   `references/config.md`'s git-directory section and marks those missing
   from it as new to the list. The engineer decides which become tickets or
   doc lines. The same holes came back in nearly every round of the
   git-directory guard, so automatic tickets would file each one many times.
2. **Two signals mark a new mechanism, and either one stops the loop.** One
   is the reviewer's `adds_mechanism`. The other is a code check on the
   diff: new module-level state, or a new `global` statement, in a guard
   module. The check would have caught three of the git-directory guard's
   four mechanisms (`_PIN`, `_UNSURE`, `_EXISTED`). It would have missed link
   restoring, which was new shell code, so the reviewer's judgement stays. A
   false stop costs the engineer one question.
3. **The bound is 3 rounds,** with a new mechanism as a second stop.
4. **`no_false_positive` starts on.**
