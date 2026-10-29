# Design: a ticket SDLC for SSSF

Status: settled in a grilling session on 2026-10-29. The decisions at the end are the record. Where the text and a decision disagree, the decision wins.

SSSF's chains follow disler's SDLC: one task in, one plan, one build, checks, review, docs, three commits, all headless. This design adds a second way to build, based on Matt Pocock's engineering skills ([mattpocock/skills](https://github.com/mattpocock/skills), MIT, read from the fork at `~/github/skills`, commit `a496f55`). It sits beside the current chains and replaces none of them. It also adds cost caps to every chain, because keeping unattended spend under control is the engineer's main concern as SSSF grows.

## Why

The two workflows catch different failures. disler's chain is good at getting a defined task built, checked, and committed with no one watching. It is weak where Matt's skills are strong:

- **Alignment.** SSSF's planner guesses what the task file means. Matt's skills grill the engineer until the spec says what they mean.
- **Size.** One builder session carries the whole feature. Matt's skills split a feature into tickets that each fit a fresh context window.
- **Test discipline.** SSSF's builder writes tests when it chooses to. Matt's `tdd` skill works red, then green, at seams agreed before any test is written.
- **Review.** SSSF has one reviewer asking one mixed question. Matt's `code-review` asks two separate questions, so one can't mask the other: does it follow the repo's standards, and does it do what the spec says?
- **Shared language.** Matt's skills keep a `GLOSSARY.md` and ADRs, so agents use the project's terms and respect past decisions.

## The two workflows side by side

| | SSSF today (disler) | Matt Pocock's skills |
|---|---|---|
| Input | one task file | a spec from a grilling session, split into tickets with blocking edges |
| The engineer's part | write the task, read the result | grilling, glossary, ADRs, spec, approving the ticket breakdown, agreeing the test seams |
| Build | one builder for the whole task | a fresh implementer per ticket, `tdd` at agreed seams, in parallel worktrees when tickets allow |
| Checks | `quality.py`: test, lint, format, typecheck, build | typecheck and single test files often, the full suite at the end |
| Review | one reviewer, up to N rounds, with the builder revising | `code-review`: standards and spec as two parallel reviews, one fix pass after |
| Commits | spec, code, write-up | per ticket, merged onto an integration branch |
| Runs | headless, start to end | interactive, with the agent driving subagents |

Matt's flow, skill by skill: `grill-with-docs`, then `to-spec`, then `to-tickets`, then `implement-spec` (or `implement` for one ticket). After that comes `pr`, and `retro` to suggest changes to the agent's environment.

## The design

### Split where the engineer's work ends

Everything up to an approved ticket breakdown needs the engineer, so it stays interactive. It runs in the project's own Claude session, using Matt's skills as they are, with `/setup-matt-pocock-skills` run once to choose the local markdown tracker:

1. `grill-with-docs`: grilling, plus updates to `GLOSSARY.md` and ADRs.
2. `to-spec`: the spec, at `.scratch/<feature>/spec.md`. Its "Testing Decisions" section records the test seams agreed with the engineer.
3. `to-tickets`: one file per ticket at `.scratch/<feature>/issues/NN-<slug>.md`, each with what to build, what blocks it, and acceptance criteria.

`.scratch/<feature>/` is committed. Agents can't change it, `GLOSSARY.md`, or the ADRs. Only the engineer changes them, interactively.

### A new ADW: `adw_tickets.py`

```
just tickets .scratch/<feature>/
```

It runs on the current branch, and refuses `main` and a dirty tree.

1. **Read.** This is plain code, not an agent. It loads the spec and the tickets. It checks that the spec's Testing Decisions section isn't empty, that every blocking edge resolves, and that there are no cycles. Then it orders the tickets so blockers come first, and skips any ticket that already has an "NN:" commit for this feature (that's how a rerun resumes). It prints an estimate from past tickets. A bad folder fails here, before any agent runs.
2. **Scout, once.** A read-only Haiku scout explores the code for the whole feature: where things live, the patterns to follow, and what each ticket will likely touch. Its notes are saved in the run's session folder.
3. **Per ticket, in order.**
   - `ticket_builder` (Sonnet) starts a fresh session with pointers to the spec, the ticket, the scout's notes, and the commits so far. Its system prompt carries the `tdd` discipline, at the spec's seams only.
   - Verify runs every `quality.py` block. Failures go back to the same session, a bounded number of times.
   - The commit is "NN: title", in the builder's words. Code sets `Status: done` in the ticket file in the same commit.
   - A ticket that still fails stops the run. Its work stays uncommitted, and the message prints how to look at it, keep it, or drop it.
4. **Review the feature.** `standards_reviewer` and `spec_reviewer` (both Opus) read the feature's whole diff, one after the other, each blind to the other's findings. Then one fresh `ticket_builder` session fixes what they found, verify runs again, and the fix gets its own commit.
5. **Document.** Same as today: the shared documenter writes the write-up, then a commit.

### Carrying Matt's discipline into headless agents

SSSF's Claude agents run with `--safe-mode`, which drops the operator's CLAUDE.md, skills, plugins, and MCP servers. That keeps an agent's behaviour down to its own prompt, so they can't load Matt's skills. Instead, the parts that matter are copied into the new agents' prompt files:

- `ticket_builder`: `tdd`, with its notes on good tests and mocking, and the `codebase-design` terms
- `standards_reviewer`: the `code-review` smell baseline and its rules (the repo's standards win, smells are judgement calls)
- `spec_reviewer`: the spec brief from `code-review`

Each copied passage notes its source file and upstream commit, so `git diff a496f55..<new>` on the fork shows what to bring over. Steps that ask the engineer something are dropped, because the spec and tickets already carry the answers. The existing builder and reviewer prompts stay as they are.

The other option was to turn off `--safe-mode` and let these agents load skills. That was rejected: every other skill and setting the operator has would leak into the factory, and runs would stop being repeatable across machines.

### Cost caps, for every chain

Three checks stop unattended spend. Each one stops without spending more, leaves the work where the engineer can see it, and says why.

- **Run cap.** `defaults.max_run_cost`, $5 by default, is set in the roster. It's checked before each agent starts, against the run's spend so far from Claude Code's own cost reports. A run can go over by at most one agent's cost. Each run gets the full cap. The stop message shows the feature's total spend so far, so a rerun is an informed decision.
- **Session cap.** Each agent's cap in the roster is passed to `claude -p --max-budget-usd`, so a runaway session stops itself. That stop is final: no retry. It depends on a test showing the flag works on a subscription login.
- **Usage guard.** `rate_limit_stop`, already built, watches the account's 5-hour and 7-day windows.

Where a run stops decides what's left:

| Where it stops | What's committed | What's in the tree | Rerun |
|---|---|---|---|
| `adw_simple_sdlc`, before build | the spec | clean | starts over |
| `adw_simple_sdlc`, during build or verify | the spec | uncommitted code | starts over, after the engineer deals with the tree |
| `adw_simple_sdlc`, before docs | the spec and the code | clean | starts over (no resume) |
| `adw_tickets`, between tickets | the tickets done so far | clean | resumes at the next ticket |
| `adw_tickets`, partway through a ticket | the tickets before it | that ticket's unfinished work | refused until the engineer cleans the tree |
| `adw_tickets`, during review or fix | every ticket | possibly part of a fix | resumes at review, once the tree is clean |

### The dashboard

The account panel already covers the account. The session view gets:

- a ticket run grouped by ticket, each showing its title, status, retries, and cost
- the run's cost against its cap, as a bar
- the stop reason at the top of the run: `budget_stop`, a session cap, `rate_limit_stop`, or a failed ticket

### Not in the first version

- **Parallel tickets.** SSSF works in one tree, and `permissions.py` compares the tree before and after each agent. Parallel tickets need worktrees and a merge step. Serial runs get most of the value.
- **A real issue tracker.** Local ticket files only.
- **`pr` and `retro`.** These stay in the interactive session.

### Build order and testing

First the cost caps, then `adw_tickets`, then the dashboard. Each is built test-first in this repo, then tried on `sssf-sandbox`, then on yt-summarizer, whose multi-run designs are already shaped like ticket sets.

## Decisions

Settled in the grilling session, in order.

1. **Split where the engineer's work ends.** Grilling, the spec, and the ticket breakdown stay interactive, using Matt's skills as they are. SSSF starts from approved tickets.
2. **A separate ADW.** `adw_tickets.py` runs beside `adw_simple_sdlc`, which stays as it is for one-shot tasks.
3. **The discipline goes in the prompts, with `--safe-mode` kept on.** The passages that matter are copied into the agents' prompt files, each noting its upstream file and commit.
4. **One folder per feature.** `.scratch/<feature>/` holds `spec.md` and `issues/`. It is committed, and it joins `protected_files`, so no agent may change it.
5. **One commit per ticket.** Each ticket that passes its checks is committed as "NN: title". The review fix and the write-up get one commit each after that.
6. **A fresh builder session per ticket.** Each one starts with the spec, its ticket, and the earlier commits as pointers. Retries for that ticket stay in its session.
7. **Two reviewer agents.** `standards_reviewer` and `spec_reviewer` run one after the other, and neither sees the other's findings.
8. **Only the engineer changes `GLOSSARY.md` and ADRs.** Agents read them. Both are added to `protected_files`, and they change only in interactive grilling.
9. **One roster file.** `ticket_builder`, `standards_reviewer`, and `spec_reviewer` are new agents in `sssf.config.yaml`, with their own prompt folders. The documenter is shared, and the existing prompts stay as they are.
10. **A failed ticket stops the run.** Tickets before it stay committed. Its work stays uncommitted in the tree, and the run says which ticket failed and why.
11. **A rerun resumes from git.** Tickets with an "NN:" commit for this feature on the branch are skipped. A rerun refuses to start on a dirty tree.
12. **Code marks a ticket done.** The ticket's own commit sets `Status: done` in its file. Nothing else in the ticket changes, and the checkboxes stay as written.
13. **The spec carries the seams.** `to-spec` agrees the test seams with the engineer and records them under "Testing Decisions". The read step requires that section to be non-empty. Tickets follow Matt's template unchanged. (Revised by 29: the first version asked for a seams section in every ticket, which `to-tickets` doesn't write.)
14. **One fix pass after review.** The fix is followed by the checks. If they fail after the retries, the run stops, with the tickets still committed.
15. **The feature folder is the only input.** A missing `spec.md` or `issues/` fails the read step.
16. **Models follow the current roster.** `ticket_builder` uses Sonnet, and both reviewers use Opus.
17. **A scout step runs before the builders.** This reverses the draft. Cost is the engineer's main concern for unattended runs, and exploring once up front costs less than every builder finding its own way. The shape of the step is decided below.
18. **The current branch.** `adw_tickets` commits to the branch it's on, refuses `main`, and creates no branches.
19. **One scout per feature.** A read-only Haiku scout explores once, before any ticket. Its notes are saved in the run's session folder, and every `ticket_builder` gets a pointer to them.
20. **A cost cap per run, for every chain.** `defaults.max_run_cost` is in estimated dollars, from Claude Code's own cost reports. Before each agent starts, SSSF compares the run's spend with it, and at or over the cap the run stops with a resumable reason in the trace.
21. **$5 by default.** The cap is set in the roster where it's visible, and each repo may change it.
22. **A cap per agent session.** Each agent's cap is passed to `claude -p --max-budget-usd`, so a runaway session stops itself. This depends on a test showing the flag works on a subscription login. If it doesn't, the engineer decides the alternative.
23. **The session view groups a ticket run by ticket.** Each ticket shows its title, status, retries, and cost. Every run shows its cost against the cap and its stop reason at the top. This is built after the ADW works.
24. **An estimate before a ticket run.** `just tickets` prints an estimate from past tickets, or "no history yet". It informs and doesn't enforce.
25. **The cap is per run.** Each `just tickets` run gets the full `max_run_cost`. The stop message and the pre-run estimate both show the feature's total spend so far, so every rerun is an informed decision.
26. **Unfinished work is never discarded automatically.** When a stop leaves a ticket half-built, the message prints the commands to look at it (`git diff`), keep it (`git stash`), or drop it (`git checkout . && git clean -fd`). The engineer chooses.
27. **A session cap stops the run, with no retry.** The trace records the agent and what it spent. Tickets that keep hitting the cap are too big and should be split in an interactive session.
28. **Every agent gets a session cap.** That includes `adw_simple_sdlc`'s planner, builder, reviewer, and documenter. Each cap goes in the roster, set from that agent's past costs with headroom.
29. **Seams come from the spec.** This revises 13 after reading `to-spec` and `to-tickets`.
30. **Onboarding covers the setup.** `cookbooks/onboard.md` gets an optional step to run `/setup-matt-pocock-skills` with the local markdown tracker, and a new `cookbooks/tickets.md` covers the whole flow.
31. **Build order.** First the cost caps for every chain (20 to 22, 25, 27, 28). Then `adw_tickets`, tried on the sandbox and then yt-summarizer. Then the dashboard's ticket grouping and cost bar. Each piece is tested and committed before the next starts.
32. **The fork stays Matt's.** Nothing SSSF needs is changed in `~/github/skills`, so pulling upstream stays free of conflicts. The engineer's installed skills are symlinks into the fork's working tree.
33. **This design is committed before the build starts.** Nothing is pushed without the engineer's go-ahead.
34. **What the build found about the caps.** Tested on a subscription login on 2026-10-29: `--max-budget-usd` works there, so 22 needs no alternative. Claude Code counts it per call, even on a resumed session, and checks it after each request, so a call can go over by one request. The roster key is `max_call_cost`. The run cap is checked before every send, not only when an agent starts, so a phase's retries can't spend past it.
