# Guard Spec Review Task

## Variables

### prompt

{{prompt}}

### previous_envelope

{{previous_envelope}}

### context_handoff_dir

{{context_handoff_dir}}

### phase_name

{{phase_name}}

## Task

1. Review the spec `previous_envelope` names, as your instructions say. Under 400 words.
2. Write the review to `<context_handoff_dir>/<phase_name>.md`, then emit your `Report` JSON.

## Report

Respond with ONLY valid JSON matching `ReviewOutput`, no prose before or after:

```json
{
  "status": "success",
  "summary": "<one sentence: approved, or the worst problem>",
  "approved": false,
  "findings": [
    {"requirement": "Bar", "met": false, "evidence": "<what is vague or missing, quoted>"}
  ],
  "blocking": ["<what the planner must change>"],
  "artifacts": ["<context_handoff_dir>/<phase_name>.md"],
  "notes_for_next_agent": "<for the planner: what to change>"
}
```

Give one finding per required section, plus one for the design's size. `approved` is true only when `blocking` is empty and every finding is met.
