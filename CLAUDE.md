# SSSF hub

This repo is the source of the `sssf` skill (`.claude/skills/sssf/`). `sssf-update` copies only that folder into the repos where SSSF is installed. Everything else here, this file and `.claude/agents/` included, stays in the hub.

## Guard changes

Guard code is what limits an agent: `rules.py`, `sssf_refuse.py`, `sssf_rules.ts`, `permissions.py`, `agent_cc.py`, `agent_pi.py` and `gates.py` under `.claude/skills/sssf/templates/adws/adw_modules/`, and the agent prompts' lines on what an agent may do.

Before you merge a branch that changes guard code into `main`, run the `bypass-reviewer` agent on it, with `main` as the base and the branch as the head. Merge once it reports no bypass, or once each bypass it found is fixed, with a test for it, and a second run is clean. Say what it found in the commit message or the report to the engineer.

The review exists because a guard change once passed every test and still let `cat <<EOF | bash` past every agent rule. Two project sessions caught it after the update was sent.
