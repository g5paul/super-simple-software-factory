# Build Task

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

Implement the work described in `prompt`, guided by `previous_envelope` if present, then emit your `Report` JSON.

{{verify_next}}## Report

Respond with ONLY valid JSON matching `BuildOutput` — no prose before or after:

```json
{
  "status": "success",
  "summary": "<one sentence describing what you built>",
  "changed_files": ["src/server.ts"],
  "artifacts": [],
  "commit_message": "<imperative one-line git subject for the code you changed — this is what the commit of your work will say>",
  "notes_for_next_agent": "<how to verify this work>"
}
```

`changed_files` lists every path your change touched, from the repo root, as git names it: each file you added or edited, each file you deleted, and both the old and the new path of a rename.

`status` is `success` or `fail`, and nothing else: any other value, such as `blocked`, is refused. Work you cannot finish is `fail`, with the reason in `summary`.
