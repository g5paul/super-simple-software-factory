# Guard change report

Outcome: round 2 found nothing that blocks

Heads reviewed: `89f62abb91df`, `6965a4707ee1`

## Bypasses (0)


## Harms (0)


## False positives (0)


## Broken promises (0)


## Old holes (3)

- `'plain run: agent replaces adws/adw_data with a symlink to another folder before the next host write'` passes session_files.write / open_append. Base: passes. Head: passes.  The helper opens the data root following links, so SSSF's writes land under the link's target. Out of scope in the spec, and config.md names it.
- `'agent replaces <session>/<agent> with a link to an outside folder; Tracer.__init__ ensure_dir / agent_cc raw_path.parent.mkdir / agents agent_dir.mkdir run by path'` passes folders made by path (tracer.py, runner.py, agent_cc.py, agent_pi.py, agents.py, quality.py, plan_checks.py, trial.py). Base: passes. Head: passes.  Folders made by path follow a folder link and can create an empty folder at its target. Out of scope in the spec. No file is written or deleted. New to the known limits.
- `'agent links <session>/agent_map.json or context_handoff/plan.md or the claude ledger to an outside file; host reads it'` passes host reads under the session folder (runner.py, agent_cc.py, gates.py). Base: passes. Head: passes.  Host reads under the session folder follow links. Out of scope in the spec: a read changes no file. New to the known limits.

98 candidates, 7 checks.
