# Docs

## Using SSSF

Read these in order the first time.

| Page | For |
|---|---|
| [guide/install.md](guide/install.md) | installing into a repo, on a Claude subscription, ChatGPT or API keys, plus the container and VMs |
| [guide/run.md](guide/run.md) | each chain and feature, shown with a real run |
| [guide/watch.md](guide/watch.md) | following a run from the terminal, the dashboard or SQL |
| [guide/customize.md](guide/customize.md) | your checks, prompts, roster, caps and rules, and what a fork can strip |
| [guide/troubleshooting.md](guide/troubleshooting.md) | what each stop and refusal means, and what to do |
| [concepts.md](concepts.md) | how a run is built: ADWs, phases, agents, gates, the trace |
| [secret-scan.md](secret-scan.md) | the pre-push hook that scans for secrets |
| [fork.md](fork.md) | what this fork changed from upstream |

The skill's own docs, in `.claude/skills/sssf/`, are written for the Claude session that operates SSSF, and go deeper. [SKILL.md](../.claude/skills/sssf/SKILL.md) routes each request to a cookbook, and [references/config.md](../.claude/skills/sssf/references/config.md) documents every roster key.

## This hub

These pages are about how this repo runs SSSF, and a fork can delete them.

| Page | For |
|---|---|
| [sssf.md](sssf.md) | how this hub runs SSSF on itself, and the guard-change chain |
| [projects.md](projects.md) | the repos this hub has installed SSSF in, and the skill commit each is on |
| [designs/](designs/) | one design per feature: the decisions and why |
| [research/](research/) | notes on outside tools and services, with sources |
| [postmortems/](postmortems/) | what went wrong, and what changed because of it |
