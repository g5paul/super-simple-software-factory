# SSSF hub

This repo is the source of the `sssf` skill (`.claude/skills/sssf/`). `sssf-update` copies only that folder into the repos where SSSF is installed. Everything else here, this file and `.claude/agents/` included, stays in the hub.

## Guard changes

Guard code is what limits an agent: `rules.py`, `sssf_refuse.py`, `sssf_rules.ts`, `secret_files.py`, `permissions.py`, `agent_cc.py`, `agent_pi.py` and `gates.py` under `.claude/skills/sssf/templates/adws/adw_modules/`, and the agent prompts' lines on what an agent may do.

Make a guard change through the guard-change chain, `adws/adw_guard_change.py`, on a branch cut from `main`. `docs/sssf.md` says how to run it. The chain judges each finding against the spec's baseline and bar, and stops at three rounds or at a fix that adds a mechanism. A run that passes leaves its commits on the branch, and merging is the engineer's call. A run that stops goes to the engineer with `guard_report.md`. Say what the report found in the merge commit or the report to the engineer.

The `bypass-reviewer` subagent stays for a quick check outside a run, or on a run that looks wrong. A merge no longer needs it: f11ed7c0, d6fb947e and d071d025 passed the chain three times in a row with the subagent finding nothing the chain missed, and the engineer dropped the step on 2026-11-03. If the subagent ever finds a bypass that a passing run missed, run it before every merge again until three more runs come back clean.

The review exists because a guard change once passed every test and still let `cat <<EOF | bash` past every agent rule. Two project sessions caught it after the update was sent.

## Public and private repos

This repo is public. Some of the repos SSSF is installed in are private or unlisted, and nothing about them belongs here. Keep the two apart:

- Name a private repo nowhere this repo tracks or sends: files, commit messages, test fixtures, and output pasted into either. Call it "a project", and describe a fix it turned up without its name, its packages or its commands. Its records go in the private repo `CLAUDE.local.md` names.
- Treat command output as public before it's pasted. `adws/check_projects.py`, the trace and logs name every repo they touch; use `check_projects.py --listed` when the output goes anywhere public.
- `.git/sssf-private-names` lists the private names, and the pre-push hook refuses a push that adds one. That's the backstop, not the plan: a name caught there was already written down.
