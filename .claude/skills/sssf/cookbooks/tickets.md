# Build a feature from tickets

`adw_tickets` builds a feature one ticket at a time, test-first, from a folder that [Matt Pocock's engineering skills](https://github.com/mattpocock/skills) wrote with the engineer. This cookbook is for the Claude session in the project repo. The design and its decisions are in the SSSF repo at `docs/designs/ticket-sdlc.md`.

Use it for a feature that needs a spec and more than one session of work. For a single defined task, `just simple-sdlc` is cheaper. How to choose between them is in [how_to_prompt_for_the_eng.md](how_to_prompt_for_the_eng.md), section "One run, or a ticket feature".

## The split

| Half | Who | Where | Skills |
|---|---|---|---|
| Decide what to build | the engineer and you | this session, interactive | `/grill-with-docs`, `/to-spec`, `/to-tickets` |
| Build it | SSSF agents | `just tickets`, headless | none: the passages that matter are copied into the agents' prompts |

SSSF agents run with `--safe-mode`, so they never load skills. Everything they need from the interactive half has to be in the feature folder.

## 1. Set up the tracker, once per repo

Run `/setup-matt-pocock-skills` and choose **Local markdown**. The skills then write to `.scratch/<feature>/`. If the repo's `.gitignore` covers `.scratch/`, take that rule out: `adw_tickets` commits each ticket's `Status: resolved`, so the folder must be tracked.

## 2. Decide what to build (interactive)

1. `/grill-with-docs` until the engineer and you agree. It may update `GLOSSARY.md` and `docs/adr/`. Agents read those files and can't change them.
2. `/to-spec`. It writes `.scratch/<feature>/spec.md`. Its "Testing Decisions" section must name the test seams the engineer agreed. `adw_tickets` refuses a spec without them, and builders test only there.
3. `/to-tickets`. It writes `.scratch/<feature>/issues/NN-<slug>.md`, each with a "Blocked by" line. Get the engineer's approval of the breakdown.
4. Make a branch for the feature if you're on `main`, then commit the folder: `git add .scratch/<feature> && git commit -m "Spec and tickets for <feature>"`.

## 3. Build it

```bash
just tickets .scratch/<feature>/
```

The read step runs before any agent and prints the ticket order, what the feature has cost so far, and an estimate from past ticket runs. It stops the run on `main`, on a dirty tree, on an untracked folder, on a spec with no seams, on a blocker that isn't a ticket, and on a cycle.

Then one Haiku scout explores for the whole feature. Each ticket gets a fresh builder session with the spec, its ticket, the scout's notes, and the commits so far. A ticket lands as its own commit, `NN: title`, once every `quality.py` check passes, and that commit sets its `Status:` to `resolved`, the tracker's word for a finished ticket.

After the last ticket, two reviewers read the whole feature's diff, each blind to the other. One asks whether it follows the repo's standards, and the other whether it does what the spec asked. If either finds something blocking, one builder session fixes it, the checks run again, and the fix gets its own commit. A finding that would mean changing a protected file, such as the spec, a ticket, the glossary, or an ADR, never reaches the builder. The run prints it for you instead. The documenter writes it up, and that last commit marks the feature finished.

## 4. When it stops

Every stop leaves finished tickets committed and the unfinished one in the tree. The dashboard's session page shows the stop reason above the run, a row per ticket, and spend against the run cap; click a ticket to open its phases. The message says why it stopped, what the feature has cost across every run, and how to look at, keep, or drop what's uncommitted. Nothing is discarded for you.

| Why | What to do |
|---|---|
| A ticket stayed red after two fixes | Read the ticket's `verify` output with `just phases <adw_id>`. Fix it by hand and commit, or drop the work. Then rerun. |
| `max_run_cost` (default $5) | Rerun to continue with a fresh $5, or raise `defaults.max_run_cost`. |
| An agent's `max_call_cost` | That ticket is too big for one session. Split it with the engineer, in an interactive session, then rerun. |
| `rate_limit_stop` | The account's usage window is nearly full. Rerun after the reset time in the message. |
| The read step refused | The message names the problem and the file. |

A rerun skips done tickets, so it resumes at the first unfinished one. Once every ticket is done, a rerun goes straight to review. A feature whose docs are committed is finished, and a rerun says so and does nothing.

## 5. Report

Report to the engineer and the SSSF hub: the adw_id, the tickets built, the review findings, the commits, the cost, and anything SSSF did that you didn't expect.
