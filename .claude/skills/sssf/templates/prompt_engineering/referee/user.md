# Trial Panel Task

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

Judge every attempt in `previous_envelope.attempts` against `prompt`, then rank them.

1. Read each attempt's `diff_path` in full.
2. For each attempt, rule on every requirement in `prompt`: one `findings` entry each, with evidence.
3. Rank every attempt by label, best first.
4. Write your review to `<context_handoff_dir>/<phase_name>.md`, one section per label. Then emit your `Report` JSON.

## Report

Respond with ONLY valid JSON matching `PanelOutput`, no prose before or after:

```json
{
  "status": "success",
  "summary": "<one sentence: which attempts pass, and which ranks first>",
  "verdicts": [
    {
      "label": "A",
      "approved": false,
      "findings": [
        { "requirement": "<the ask, in the requester's words>", "met": true, "evidence": "src/server.ts:42, handler registered" }
      ],
      "blocking": ["<what is missing or wrong>"]
    }
  ],
  "ranking": ["B", "A"],
  "artifacts": ["<context_handoff_dir>/<phase_name>.md"],
  "notes_for_next_agent": ""
}
```

`status` is `success` when the review itself completed; it is not a verdict. Give exactly one verdict per label, and list every label exactly once in `ranking`.
