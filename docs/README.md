# Docs

Start with the tutorial, then use the how-to guides by task.

## Tutorial

| Page | For |
|---|---|
| [Your first run](guide/first-run.md) | install SSSF, run one change on its own branch, and read the result |

## How-to guides

| Page | For |
|---|---|
| [Install SSSF in a repo](guide/install.md) | install into a repo on a Claude subscription, ChatGPT or API keys, plus the operator container, VMs and agentsh |
| [Run SSSF](guide/run.md) | start each ADW, on its own branch, in parallel, in a VM; merge, discard or stop a run |
| [Watch a run](guide/watch.md) | follow a run from the terminal, the dashboard or SQL |
| [Make SSSF your own](guide/customize.md) | your checks, prompts, roster and rules, and what a fork can strip |
| [When something goes wrong](guide/troubleshooting.md) | what each stop and refusal means, and what to do |

## Reference

| Page | For |
|---|---|
| [Config reference](../.claude/skills/sssf/references/config.md) | every roster and `repo.yaml` key, its default, and what it enforces |
| [Secret scan before every push](secret-scan.md) | the pre-push hook: what it scans and refuses, and how to turn it on |
| [Every recipe](guide/run.md#every-recipe) | every `just` recipe and the line it runs |
| [Stops](guide/troubleshooting.md#stops) | each stop, its cap and its fix |
| [The trace](concepts.md#the-trace) | the trace's tables |

The rest of the skill's docs in `.claude/skills/sssf/`, [SKILL.md](../.claude/skills/sssf/SKILL.md) and the cookbooks, are written for the operator session.

## Explanation

| Page | For |
|---|---|
| [How SSSF works](concepts.md) | how a run is built and why: the operator session, ADWs, phases, agents, guards, where a run runs, gates, the trace, the caps |
| [What this fork adds](fork.md) | what this fork changed from upstream, and why |

## This hub's records

These pages are about how this hub runs SSSF, and a fork can delete them.

| Page | For |
|---|---|
| [SSSF in the hub](sssf.md) | how this hub runs SSSF on itself: the guard-change chain, updates, the install clone and the project reports |
| [Projects running SSSF](projects.md) | the repos this hub installed SSSF in, and the skill commit each is on |
| [Open items](open.md) | every open SSSF item, each linked to its design |
| [Designs](designs/) | one design per feature: the decisions and why |
| [Research](research/) | notes on outside tools and services, with sources |
| [Postmortems](postmortems/) | what went wrong, and what changed because of it |
