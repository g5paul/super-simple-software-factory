# A guard-change chain in the hub

Status: proposed, 2026-11-03. Nothing is built.

## Why

Hub work happens outside SSSF. This session plans a change, edits the files,
runs the suite, starts the bypass reviewer, reads its report and merges, and
none of it lands in a trace. The git-directory guard took seven review rounds
on one branch. The only record of them is commit messages and this session's
chat, and one round stopped after three seconds without anyone seeing why.

A run records exactly what was missing: each phase, its agent, cost, gate
verdicts and stalls. So guard changes in the hub should go through a chain,
the way project work does, and show up in `just obs-all` beside every other
repo.

## The chain: `adw_guard_change.py`

```
engineer(request) -> planner -> git(commit_plan)
  -> builder -> code(verify: the hub suite) [-> builder(fix) ... bounded]
  -> bypass_reviewer [-> builder(fix bypass) -> code(verify) -> bypass_reviewer ... bounded]
  -> git(commit_build)
```

- **planner** writes the spec, as in `adw_simple_sdlc`. It reads the guard
  module, `references/config.md` and the tests.
- **builder** changes the guard code and adds a test for each case it fixes.
- **verify** is `quality.py` over the hub suite (`pytest tests` from the skill
  root). Code, not an agent.
- **bypass_reviewer** is a new roster agent. Its prompt is today's
  `.claude/agents/bypass-reviewer.md`, steps and report unchanged, moved into
  the hub's `adws/` prompt folder. It writes only under its scratch directory.
- **The loop.** A bypass goes back to the builder as a fix, re-enters verify,
  and the reviewer runs again on the new head. That is the rule in the hub's
  CLAUDE.md ("fixed, with a test for it, and a second run is clean"), written
  as code. The bound is 3 rounds; past it the run stops and the engineer
  decides.
- **No merge.** The run leaves the branch with its commits. Merging to `main`
  stays the engineer's step, the same as in every project.

### The reviewer's output

A new type, `BypassReview`:

| Field | Meaning |
|---|---|
| `bypasses` | each one: the candidate as a string literal, the check it passes, base and head verdicts, the diff line |
| `old_holes` | the same shape; reported, never blocking |
| `false_positives` | harmless input head stops |
| `candidates`, `checks`, `suite` | the counts |

A gate, `no_bypass`, passes when `bypasses` is empty. `old_holes` and
`false_positives` go into the run's write-up and the trace, so the engineer
reads them without them stopping the run. A new false positive that blocks
ordinary agent work is worth a stop too; that is the second gate,
`no_false_positive`, off by default.

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

1. Is 3 the right bound on review rounds? The git-directory guard needed 7,
   but four of those found harms rather than bypasses.
2. Should a reported old hole open a follow-up ticket automatically, or only
   appear in the write-up?
3. Should `no_false_positive` start on?
