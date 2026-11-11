# Super Simple Software Factory

> So take it. Fork it, strip the parts you do not need, rename the agents, throw out half the workflows, and roll what is left into the factory your product actually needs. The specific chains in here matter far less than the shape: code owns the loop, agents own the phases, and every run leaves a trace you can go read.
>
> from [disler/super-simple-software-factory](https://github.com/disler/super-simple-software-factory#built-to-be-observed-customized-and-reused)

This fork is what came of doing that.

SSSF is a Claude Code skill that installs a software factory into a repo. The factory is a set of ADWs, or AI developer workflows. Each ADW is a short Python script that runs coding agents and plain code checks in a fixed order. Python decides what runs next, what counts as done, and when to retry. Each agent works inside one phase and hands back a typed report. Every event lands in a SQLite trace while the run is still going.

<p align="center">
  <img src="images/01_factory_spine.svg" alt="A run spine with engineer, agent and code phases on a fixed rail. Every event drops into the SQLite trace, which the dashboard polls" width="850">
</p>

**Kept from upstream.** Code owns the loop, agents own the phases, and every run leaves a trace. All 12 of upstream's ADWs are still here, beside two new ones, tickets and trials.

This fork changed or added:

- agents on a Claude or ChatGPT subscription, with no API key;
- spend caps per call, run, repo and machine, and a stop before a usage window runs out;
- a branch and worktree per run, several runs at once, and trials that race two rosters on one request;
- the [operator session](docs/concepts.md#the-operator-session), the Claude Code session that launches and follows runs, in a container;
- VM runs in an OpenShell microVM, or in a Vercel or E2B remote VM;
- rules each repo owns, checked on every shell command an agent runs;
- agentsh, an opt-in second guard for runs on the host, described in [where a run runs](docs/concepts.md#where-a-run-runs);
- the guard-change chain, which reviews every change to the guard code before it merges.

[The fork's changelog](docs/fork.md) lists each change and links its design.

## Quickstart

You need a Claude subscription, and a repo with a clean tree and at least one commit. Install [uv](https://docs.astral.sh/uv/), [just](https://github.com/casey/just) and Claude Code first. [Before you start](docs/guide/install.md#before-you-start) lists the versions tested. Then, from the root of your repo:

```bash
git clone https://github.com/g5paul/super-simple-software-factory ~/github/super-simple-software-factory
export SSSF_SOURCE=~/github/super-simple-software-factory/.claude/skills/sssf

mkdir -p .claude/skills && cp -r "$SSSF_SOURCE" .claude/skills/
uv run .claude/skills/sssf/scripts/install.py --coding-agent claude_code
cp .env.sample .env
git add -A && git commit -m "Install SSSF"

just demo        # two cheap read-only runs; both should end ✓ success
```

Then put your real test command in `adws/adw_modules/quality.py`. Until you do, every test phase passes. Restart Claude Code and ask it for work in plain words:

```
/sssf add a /health endpoint that returns the git sha, on its own branch
```

The operator session picks an ADW, launches it, follows the trace, and reports the result and its cost. Nothing merges until you say so.

For the same steps at a slower pace, with what each one does, follow the tutorial [Your first run](docs/guide/first-run.md).

## Docs

| Page | For |
|---|---|
| [First run](docs/guide/first-run.md) | a guided first task, from install to a merged run |
| [Install](docs/guide/install.md) | each sign-in path, the operator container, VMs, and updates |
| [Run](docs/guide/run.md) | every ADW and feature, each shown with a real run |
| [Watch](docs/guide/watch.md) | the terminal recipes, the dashboard, and the trace in SQL |
| [Customize](docs/guide/customize.md) | your checks, prompts, roster, caps and rules |
| [Troubleshooting](docs/guide/troubleshooting.md) | what each stop and refusal means |
| [Concepts](docs/concepts.md) | how a run is built: the operator session, phases, agents, guards, where a run runs, gates, the trace |

The [docs index](docs/README.md) lists the rest, and keeps them apart from the pages about how this hub runs.

## Why code owns the loop

Hand one agent a whole feature and you can't say which step failed, "done" means the agent stopped talking, and a retry starts from nothing. Run it twice and you get two different results.

SSSF splits the work into phases, and code runs the order. A phase is one agent with one job, or one command. A gate checks each agent's report against the repo, for example that the files it names exist and that the diff matches what it says it changed. A failed check goes back to the same agent session as a correction, which costs a turn rather than a restart.

This pays off when the same ADW runs a hundred times. For a one-off change, prompt an agent and move on. [Code owns the loop](docs/concepts.md#code-owns-the-loop) explains the design.

## Built to be observed, customized and reused

The shape matters more than the parts, so take this fork as upstream asked you to take it. The checks, prompts and roster are starters for your repo, and [Make SSSF your own](docs/guide/customize.md) says where each lives.

Some parts serve only this hub, and a fork can delete them. [Strip what you don't need](docs/guide/customize.md#strip-what-you-dont-need) lists the files. The hub's own `adws/adw_sssf_config/repo.yaml` sets `local_guard: agentsh`, which needs an agentsh build that isn't published yet, so set it to `text` in your fork. A repo you install SSSF into starts on `text`.

## Credit

SSSF is [disler's](https://github.com/disler/super-simple-software-factory) design, and this fork keeps its shape. disler explains it in the video [Super Simple Software Factory](https://youtu.be/haUfb1ievTE), and teaches the patterns behind it in [Tactical Agentic Coding](https://agenticengineer.com/tactical-agentic-coding?y=sssf).

<p align="center">
  <img src="images/07_rise_with_the_ceiling.svg" alt="Vibe coding sits inside a narrow band with a short arrow of headroom above it. Agentic engineering rises far above that band, with a tall arrow" width="850">
</p>

## License

MIT. See [LICENSE](LICENSE).
