# Standards Review Task

## Variables

### prompt

{{prompt}}

### context_handoff_dir

{{context_handoff_dir}}

### phase_name

{{phase_name}}

## Task

1. Read the diff named in `prompt`, and find the repo's standards files.
2. Report (a) every place the diff breaks a documented standard, citing the file and the rule, and (b) any baseline smell, naming it and quoting the hunk. Under 400 words.
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
      "problem": "<what is wrong>",
      "source": "<the standard: file and rule, or the smell's name>",
      "blocking": false
    }
  ],
  "artifacts": ["<context_handoff_dir>/<phase_name>.md"],
  "notes_for_next_agent": "<the standards files you checked against>"
}
```

`blocking` is true only for a documented standard the diff breaks. A smell is never blocking. `status` is `success` when the review itself completed; an empty `findings` list is a clean review.
