# Trial

Run one request under several rosters, have a panel judge what each built, and let the engineer pick the one to merge. Design: `docs/designs/trials.md` in the SSSF repo.

## When

Only when the engineer asks for a trial by name: "try this with main and chatgpt", "race the rosters on this", "run it as a trial". Never turn an ordinary request into a trial on your own; it costs one full `adw_simple_sdlc` run per roster, plus the panel.

**Check how the repo loads SSSF's recipes first.** If its own justfile has a `mod sssf 'sssf.just'` line, every recipe here takes the `sssf` prefix: `just sssf trial ...`. Otherwise run them as written.

## The rosters

A trial names its rosters. `main` is the repo's own `adws/adw_sssf_config/sssf.config.yaml`; any other name is `adws/adw_sssf_config/sssf.config.<name>.yaml`. `just rosters` lists the files there and each agent's model. Use the names the engineer gave. The same roster twice is allowed: it shows how much one roster varies from run to run.

A roster the repo doesn't have yet is added by the engineer's say-so:

```bash
uv run .claude/skills/sssf/scripts/install.py --add-roster chatgpt    # or claude_code, or pi
```

Then commit it. A Pi roster on ChatGPT needs `pi` signed in with `/login`, and `SSSF_PI_AUTH=subscription` in `.env`; the trial checks both and refuses without them. Ask the engineer to set either one; never set it yourself.

## Launch

Write the request the way [how_to_prompt_for_the_eng.md](how_to_prompt_for_the_eng.md) says: every attempt and every panel reviewer reads it, and the panel judges against it alone, with no plan.

```bash
just trial "<request or path/to/request.md>" main chatgpt > trial.log 2>&1
```

Run it in the background. It prints the trial id and each attempt's adw_id, waits for every attempt to end, runs the panel, and prints the scorecard. Each attempt is an ordinary worktree run: watch it with `just phases <adw_id>`, `just kill <adw_id>` stops it, and the dashboard shows it.

It refuses, and starts nothing, when:

- a roster isn't signed in, or doesn't load: report the roster and the reason.
- there are fewer free worktree slots than attempts: `max_worktree_runs` in `adws/adw_sssf_config/repo.yaml` is 2 by default, so a three-roster trial needs it raised. That is the engineer's call.
- a cost cap is reached: report the stop's message, which says when it clears.

A note that two attempts "share one plan's usage window" is a warning, not a refusal. Pass it on.

## While it runs

`just trial-show <trial>` prints the scorecard at any point. If the `just trial` process dies, the attempts carry on; once they have all ended, `just trial-review <trial>` runs the panel and prints the scorecard. Use the same command to retry a panel that failed.

## Report the scorecard, then wait

Report the scorecard as printed: each attempt's roster, run status, checks, panel verdict, ranks, cost, time, and diff size; any blocking reasons; the suggestion and why; and the trial's total cost. Then stop and wait for the engineer's pick. A suggestion is not a pick.

- **checks**: the attempt's last full `quality.py` run.
- **panel**: `pass` when no reviewer blocked it, `block` when one did, `unknown` when a reviewer never ruled on it, `-` when the run failed and the panel never saw it.
- **rank**: the average place across the panel, 1 is best. **rank\***: the same without the vote of the reviewer from the attempt's own model family. With one family it is empty.
- The suggestion is the cheapest attempt that passed every gate. When the cheapest and the best ranked differ, say so.

## Land the pick

On the engineer's word only:

```bash
just trial-merge <trial> <adw_id>     # merges it like `just merge`, then discards the other attempts
just trial-discard <trial>            # drops every attempt
```

A merge that fails discards nothing. Report it the way [run_adw.md](run_adw.md) reports a failed `just merge`. The traces and scorecards stay either way, and build the roster leaderboard on the dashboard's project page.
