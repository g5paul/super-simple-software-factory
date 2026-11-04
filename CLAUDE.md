# SSSF hub

This repo is the source of the `sssf` skill (`.claude/skills/sssf/`). `sssf-update` copies only that folder into the repos where SSSF is installed. Everything else here, this file and `.claude/agents/` included, stays in the hub.

## Guard changes

Guard code is what limits an agent: `rules.py`, `sssf_refuse.py`, `sssf_rules.ts`, `permissions.py`, `agent_cc.py`, `agent_pi.py` and `gates.py` under `.claude/skills/sssf/templates/adws/adw_modules/`, and the agent prompts' lines on what an agent may do.

Make a guard change through the guard-change chain, `adws/adw_guard_change.py`, on a branch cut from `main`. `docs/sssf.md` says how to run it. The chain judges each finding against the spec's baseline and bar, and stops at three rounds or at a fix that adds a mechanism. A run that passes leaves its commits on the branch, and merging is the engineer's call. A run that stops goes to the engineer with `guard_report.md`. Say what the report found in the merge commit or the report to the engineer.

The `bypass-reviewer` subagent stays for a quick check outside a run. Until three chain runs in a row pass with the subagent finding nothing the chain missed, also run the subagent on the branch, with `main` as the base, before you merge. The first live run, cdd093fe, passed the chain and the subagent then found a bypass, so the count started over. f11ed7c0, d6fb947e and d071d025 make three. Whether to drop the subagent step is the engineer's call.

The review exists because a guard change once passed every test and still let `cat <<EOF | bash` past every agent rule. Two project sessions caught it after the update was sent.
