# A guard-change chain in the hub

Status: proposed, 2026-11-03, revised the same day after the git-directory
guard post-mortem. Nothing is built.

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
- **bypass_reviewer** is a new roster agent. Its prompt is today's
  `.claude/agents/bypass-reviewer.md`, steps and report unchanged, moved into
  the hub's `adws/` prompt folder. It writes only under its scratch directory.
- **bypass_reviewer** is briefed with the spec, not with the operator's
  latest summary, so the bar can't drift between rounds.
- **The loop.** A blocking finding goes back to the builder as a fix,
  re-enters verify, and the reviewer runs again on the new head. Two things
  stop the loop and hand the run to the engineer with the findings so far:
  - a fourth round
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
| `old_holes` | the same shape; holes the baseline shares, or the spec puts out of scope |
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
- **`quality.py`** has one block, the hub suite. Its argv is the command in
  each test file's docstring.
- **`protected_files`** covers `adws/` (the guard the chain runs under),
  `.claude/agents/`, `CLAUDE.md` and `docs/projects.md`.
- **`agent_rules`** adds the hub's push rules: no `git push`, no `gh pr`.
- **Roster.** planner and builder as in the other Claude repos;
  bypass_reviewer on opus.
- **Listed.** The hub is public, so its row goes in `docs/projects.md` like
  sssf-sandbox's.

## What changes for this session

This session becomes the hub's operator for guard changes. It writes the ADW
prompt from your request, launches the chain, watches the trace and reports,
the way the project sessions do. The `bypass-reviewer` subagent stays, for a
quick check outside a run. The CLAUDE.md rule changes from "run the
bypass-reviewer agent" to "run guard changes through `adw_guard_change`".

## Out of scope

- Non-guard hub changes (docs, cookbooks). They can use `adw_simple_sdlc`
  once SSSF is in the hub; nothing here requires it.
- Sending updates to the project repos. That stays a hand-off.

## Open questions

1. Should a reported old hole open a follow-up ticket automatically, or only
   appear in the write-up?
2. Who decides that a fix "adds a mechanism": the reviewer alone, or the
   reviewer plus a check on the diff, such as new module-level state?

The engineer settled two first-draft questions on 2026-11-03: the bound is
3 rounds, with a new mechanism as a second stop, and `no_false_positive`
starts on.
