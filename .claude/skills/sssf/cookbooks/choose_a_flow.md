# Choose one run or a ticket feature

SSSF builds work in two ways. Pick one before any agent runs.

- **One run.** `adw_simple_sdlc` takes a task file and plans, builds, verifies, reviews, and documents it in one chain. See [run_adw.md](run_adw.md).
- **A ticket feature.** You and the engineer grill the feature, write a spec, and split it into tickets with Matt Pocock's skills: `/grill-with-docs`, `/to-spec`, `/to-tickets`. Then `adw_tickets` builds the tickets one at a time. See [tickets.md](tickets.md).

This page is for the engineer deciding, and for the Claude session in the project repo that offers the choice.

## Two questions

Ask them in order.

1. **Could you write the task file now?** If the engineer's request leaves open questions you'd have to guess at, such as names, a data format, edge cases, or what the user sees, it needs grilling before anyone builds it. That's a ticket feature.
2. **Does it fit one builder session?** If it splits into slices that can each be tested on their own, and building them all at once would mean one long session holding everything, that's a ticket feature too.

If both answers point to one task, use `adw_simple_sdlc`, or a shorter chain if [how_to_prompt_for_the_eng.md](how_to_prompt_for_the_eng.md) allows one.

## Signals

| Signals for a ticket feature | Signals for one run |
|---|---|
| a new user-facing mode, command, or file format | a bug fix |
| a change that's hard to undo, so worth an ADR | a rename |
| new domain terms the glossary should name | a new flag or setting that follows an existing pattern |
| work that splits into slices you can check one at a time | a design that's already settled in a doc |

## What each costs

These figures come from one repo, yt-summarizer, so treat them as a rough guide. Its `adw_simple_sdlc` runs cost $0.55 to $2.34, most of them $1.20 to $1.70. Its one ticket feature, six tickets, cost $3.52 over two runs. A ticket feature also costs the engineer's time up front: the grilling, approving the spec, and approving the breakdown into tickets.

## What each leaves behind

A ticket feature leaves a spec, glossary entries, ADRs where they were earned, one commit per ticket, and two reviews blind to each other, one on the repo's standards and one on the spec. A single run leaves a plan, the build, its review rounds, and docs.

## Follow-ups to a ticket feature are single runs

Small fixes from a feature's review findings go through `adw_simple_sdlc`. Point the task file at the feature's spec and the glossary, so the planner uses the agreed names.

## Offer a ticket feature; never start one on your own

It needs the engineer's time before any agent runs. When you can't tell which fits, ask the engineer, and give your recommendation along with the signal that decided it.
