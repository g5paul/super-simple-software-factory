# Bypass Review Task

## Variables

### prompt

{{prompt}}

### context_handoff_dir

{{context_handoff_dir}}

### phase_name

{{phase_name}}

## Task

1. Follow your steps for the change `prompt` names, working under `<context_handoff_dir>/<phase_name>/`.
2. Write the review to `<context_handoff_dir>/<phase_name>.md`: the verdict first (**bypass found** or **no bypass found**), then each list in the order of your report.
3. Emit your `Report` JSON.

## Report

Respond with ONLY valid JSON matching `BypassReview`, no prose before or after. Give each `candidate` as a Python string literal, so newlines and quotes are exact.

```json
{
  "status": "success",
  "summary": "<one sentence: the verdict and the worst finding>",
  "bypasses": [
    {
      "candidate": "'cat <<EOF | bash\\ngit push\\nEOF'",
      "check": "rules.breach",
      "base": "stopped",
      "head": "passes",
      "diff_line": "<the line in the diff that let it through>",
      "note": ""
    }
  ],
  "harms": [],
  "false_positives": [],
  "broken_promises": [],
  "old_holes": [
    {
      "candidate": "'...'",
      "check": "permissions.enforce",
      "base": "passes",
      "head": "passes",
      "note": "<the promise it breaks>",
      "new_to_known_limits": true
    }
  ],
  "adds_mechanism": false,
  "mechanism": "",
  "candidates": 0,
  "checks": 0,
  "artifacts": ["<context_handoff_dir>/<phase_name>.md"],
  "notes_for_next_agent": "<for the builder: what each blocking finding needs, in one line each>"
}
```

`status` is `success` when the review completed, whatever it found. Empty `bypasses`, `harms` and `false_positives` mean nothing blocks.
