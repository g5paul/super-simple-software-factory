# Ticket Task

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

1. Read the spec, your ticket, and the scout's notes named in `prompt`. Find the seams under the spec's "Testing Decisions".
2. If `previous_envelope` holds failing checks or review findings, fix those and skip to step 4.
3. Otherwise build the ticket one slice at a time, at those seams. For each acceptance criterion:
   - write one test, run it, and see it fail for the right reason (the behaviour is missing, not a typo or an import error);
   - write only enough code to pass it, and run it again to see it pass.
   Never write a test and its code in the same step. A test you never saw fail proves nothing.
4. Run the whole test file, and judge it by exit status. Then emit your `Report` JSON.

## Report

Respond with ONLY valid JSON matching `BuildOutput`, no prose before or after:

```json
{
  "status": "success",
  "summary": "<one sentence describing what you built>",
  "changed_files": ["src/server.ts", "tests/server.test.ts"],
  "artifacts": [],
  "commit_message": "<imperative one-line git subject for the code you changed>",
  "notes_for_next_agent": "<the seams you tested at; for each test, the failure you saw before the code existed; anything a reviewer should know>"
}
```

`status` is `fail` only when the ticket cannot be built as written, for example when it cannot be tested at the spec's seams. Say why in `summary`.
