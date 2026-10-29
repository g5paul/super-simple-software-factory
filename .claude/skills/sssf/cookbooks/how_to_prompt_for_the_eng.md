# How to Prompt for the Engineering

Read this **before every ADW launch**. The prompt you pass is what the whole chain reads: the planner plans from it, the builder builds from it, the reviewer judges against it. Your prompt might run through 10s or 100s of agents. A sloppy prompt is not a small tax; it is paid again by every agent in the chain.

## Purpose

Turn what the engineer said into the prompt the ADW receives: **clearer, not different.** You are a translator, not a redesigner.

## The one rule

**The intent is theirs. The precision is yours.**

| You MAY | You MAY NOT |
|---|---|
| Carry every constraint forward, verbatim | Quietly drop a requirement because it looks hard or odd |
| Fix grammar, cut rambling, order the steps | Soften a strong ask ("rewrite" → "refactor a bit") |
| Change the language used to better communicate the idea | Research the codebase for exact file names, never go into the app |

If you catch yourself improving the *idea* rather than the *sentence*, stop. Raise the concern to the engineer in your own message and launch what they asked for.

## You never touch the application, you prompt, monitor, observe, and report.

Outside of understanding the ADWs, you never research, touch, or dive into the codebase thats being operated on.

Your role is to simply kick off the workflow. There are entire teams of agents inside these ADWs built to do the work.

Your job is to kick it off, monitor, observe, report. Not interact with the application layer. You operate only on the agentic layer, the ADWs, the software factory.

## The shape

Four lines. Nothing else earns its tokens.

```
<the ask — one imperative sentence, their words where they were specific>
Where: <files or dirs you verified>
Done means: <the observable result — a response shape, a passing test, a rendered element>
Out of scope: <what you were tempted to add, named so nobody adds it>
```

**Before** (what the engineer said):

> can we get tags on posts, sorted by popularity

**After** (what the ADW receives):

```
Add a GET /api/tags endpoint returning {tags: [{tag, count}]} — the distinct tags
across all posts with how many posts carry each, sorted by count descending then
tag ascending.
Where: src/server.ts (routes), src/server.test.ts (tests)
Done means: GET /api/tags returns the counts, and a new test in server.test.ts covers it.
Out of scope: tag editing UI, tag filtering on the post list.
```

Same idea, same scope. What changed is that "popularity" became a sort order, the files are named, and nobody has to guess where it stops.

## Which ADW

**If the engineer named one, launch that one.** Their call stands — no second-guessing, no "upgrading" them to a longer chain. If you think another fits better, say so in your own message and launch what they asked for.

**If they did not, read what this repo actually has and choose from that.**

```bash
ls adws/adw_*.py                       # the menu
head -20 adws/adw_<name>.py            # every ADW opens with its `Phases:` line — the chain in one line
```

Chains are the engineer's to add, rename, and rewire, so **the files on disk are the only authority**. Never launch from memory or from a name you saw in a doc; read the docstrings, then match by shape:

| The work | Look for a chain that |
|---|---|
| Changes code, and the shape is not obvious — new behaviour, more than one file, anything you would want a plan for | goes end to end: plans, builds, verifies, reviews, and documents |
| Changes code, one well-understood edit | plans, builds, and verifies |
| Implements a plan this session already produced (`--adw-id`) | starts at build and verifies |
| Confirms built work is what was asked for | ends in a review phase |
| Writes up work already shipped | captures the diff and documents it |
| Is a question, and nothing should change | is a single read-only agent — the one case where one phase is right |

**Never a single-agent chain when the engineer asked for work to be done.** One-phase ADWs answer questions and run one-offs; they do not deliver.

**The more complex the ask, the more complete the chain.** Complexity means: more than one file, a behaviour you cannot describe in one sentence, anything touching data or an interface others call, or any request where you had to guess. When two chains both fit, take the longer one — a phase you did not need costs cents, while a change nobody planned, verified, reviewed, or wrote up costs an afternoon.

If nothing on disk fits the shape you need, say so and offer to compose one (`create_adw.md`) rather than forcing the work into a chain that skips the phase it needed.

### One run, or a ticket feature

The table above picks a chain for one task. First decide whether the work is one task. A ticket feature ([tickets.md](tickets.md)) is several tasks, built by `adw_tickets` from a spec and tickets that the engineer writes with you in Matt Pocock's skills: `/grill-with-docs`, `/to-spec`, `/to-tickets`.

Ask two questions.

1. **Could you write the task file now?** If the engineer's request leaves open questions you'd have to guess at, such as names, a data format, edge cases, or what the user sees, it needs grilling before anyone builds it. That's a ticket feature.
2. **Does it fit one builder session?** If it splits into slices that can each be tested on their own, and building them all at once would mean one long session holding everything, that's a ticket feature too.

If both answers point to one task, use `adw_simple_sdlc`, or a shorter chain the table above allows.

| Signals for a ticket feature | Signals for one run |
|---|---|
| a new user-facing mode, command, or file format | a bug fix |
| a change that's hard to undo, so worth an ADR | a rename |
| new domain terms the glossary should name | a new flag or setting that follows an existing pattern |
| work that splits into slices you can check one at a time | a design that's already settled in a doc |

**What each costs.** These figures come from one repo, yt-summarizer, so treat them as a rough guide. Its `adw_simple_sdlc` runs cost $0.55 to $2.34, most of them $1.20 to $1.70. Its one ticket feature, six tickets, cost $3.52 over two runs. A ticket feature also costs the engineer's time up front: the grilling, approving the spec, and approving the breakdown into tickets.

**What each leaves behind.** A ticket feature leaves a spec, glossary entries, ADRs where they were earned, one commit per ticket, and two reviews blind to each other, one on the repo's standards and one on the spec. A single run leaves a plan, the build, its review rounds, and docs.

**Follow-ups to a ticket feature are single runs.** Small fixes from a feature's review findings go through `adw_simple_sdlc`. Point the task file at the feature's spec and the glossary, so the planner uses the agreed names.

**Offer a ticket feature; never start one on your own.** It needs the engineer's time before any agent runs. When you can't tell which fits, ask the engineer, and give your recommendation along with the signal that decided it.

## Workflow

1. **Read it twice.** Mark every noun that could point at two things.
2. **Verify before you write.** Every path, route, and symbol you put in the prompt must exist — check it. A wrong path costs a whole build phase.
3. **Draft the four lines.**
4. **Diff against the original.** Every specific thing they said, still there? Anything in your draft they did not say? Delete it.
5. **Ask at most one question**, only when two readings would produce different code. Otherwise state your assumption in the prompt and say so when you report.
6. **Launch** the chain from *Which ADW* above; `run_adw.md` covers the mechanics and the watching. Inline for a short ask; for anything longer, write `requests/<slug>.md` and pass the path — every ADW takes either.

## Rules that do not bend here

- **Do not write the plan.** Your prompt says WHAT and DONE MEANS. HOW belongs to the planner — unless the engineer specified how, and then you carry it word for word.
- **Do not address the harness in the prompt.** "Use the reviewer", "retry twice", "then commit" are chain choices, and the chain is chosen by which ADW you launch, not by prose the agents will read.
- **Do not pad.** No preamble, no restating the repo, no encouragement. Gates check claims, not prose.
- **Their exact words survive.** When the engineer was specific — a name, a number, a format, a file — quote it rather than paraphrasing.

## Report back

After launching, show the engineer three things so a bad translation dies in seconds rather than at the commit phase:

1. **The prompt you actually sent** — verbatim.
2. **The ADW you chose**, and the one-line reason — or that you used the one they named.
   If they named a roster (a config, a model tier), say which one you ran on; if they did not, you ran the default, and switching that is their call, not yours (`run_adw.md`).
3. **The `adw_id`**, so they can watch it (`just phases <adw_id>`).

Then observe and report per `run_adw.md`. You run the system; you do not do the work inside it.
