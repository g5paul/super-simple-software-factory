# Plan Task

## Variables

### prompt

{{prompt}}

### previous_envelope

{{previous_envelope}}

### context_handoff_dir

{{context_handoff_dir}}

### protected_files

No agent may change these paths unless its own `writes` names them:

{{protected_files}}

## Task

Plan the work described in `prompt`.

1. Write the full plan to `<context_handoff_dir>/plan.md` — this is the copy the builder reads.
2. Copy that file into the repo under `specs/`:
   - **List `specs/` before you pick the name.** A session that plans more than once reuses its `<adw_id>`, so the obvious name may already be taken.
   - Base name: `specs/<adw_id>_<slug>.md`, where `<adw_id>` is the session directory name inside `context_handoff_dir` (`.../sessions/<adw_id>/context_handoff`) and `<slug>` is two to four kebab-case words naming the work.
   - If a file with that name already exists, use `specs/<adw_id>_<slug>_v2.md`, then `_v3`, and so on until the name is free. **Never overwrite an existing spec** — the earlier plan is the record of what was asked for then.
   - **Copy it, do not retype it.** One bash call does the whole step:
     `mkdir -p specs && cp "<context_handoff_dir>/plan.md" "specs/<adw_id>_<slug>.md"`
     Writing the plan a second time through `write` re-emits every line you already wrote, which costs the whole document again in output tokens and lets the two copies drift.
3. Emit your `Report` JSON, declaring BOTH paths in `artifacts`.

## Report

Respond with ONLY valid JSON matching `PlanOutput` — no prose before or after:

```json
{
  "status": "success",
  "summary": "<one sentence describing the plan>",
  "artifacts": ["<context_handoff_dir>/plan.md", "specs/<adw_id>_<slug>.md"],
  "commit_message": "<imperative one-line git subject for committing THIS PLAN DOCUMENT, not the work it describes — e.g. 'Add spec for the /health endpoint'>",
  "checks": ["<shell command that proves this plan is done, beyond the repo's quality checks — e.g. 'bun scripts/validate-sidebar-ui.ts'>"],
  "notes_for_next_agent": "<what the builder must know>"
}
```

`checks` lists every command your plan's verification section requires that the repo's own test, lint, format, typecheck, and build commands do not already run: a validation script the plan adds, a test file outside the usual suite. SSSF runs each from the repo root after every build, and a non-zero exit sends the output back to the builder. Each must run without a prompt and exit 0 only when the check passes. Leave it `[]` when the repo's own commands cover the plan. A check that needs a tool the repo does not declare is a dependency: name it in the plan, so the reviewer sees it.

Both `artifacts` entries are the paths you ACTUALLY wrote, `_v2` suffix and all. Gates open these files — a name you meant to use fails them.
