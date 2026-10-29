# Spec Review Task

## Variables

### prompt

{{prompt}}

### context_handoff_dir

{{context_handoff_dir}}

### phase_name

{{phase_name}}

## Task

1. Read the spec and the tickets named in `prompt`, then the diff.
2. Report missing or partial requirements, scope creep, and requirements implemented wrongly, quoting the spec or ticket line for each. Under 400 words.
3. Write the review to `<context_handoff_dir>/<phase_name>.md`, then emit your `Report` JSON.

## Report

Respond with ONLY valid JSON matching `AxisReviewOutput`, no prose before or after:

```json
{
  "status": "success",
  "summary": "<one sentence: N findings, the worst one>",
  "findings": [
    {
      "where": "src/server.ts:42",
      "problem": "<what is missing, extra, or wrong>",
      "source": "<the spec or ticket line, quoted>",
      "blocking": true
    }
  ],
  "artifacts": ["<context_handoff_dir>/<phase_name>.md"],
  "notes_for_next_agent": "<what the fix must change>"
}
```

`status` is `success` when the review itself completed; an empty `findings` list is a clean review.
