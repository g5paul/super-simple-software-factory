# Guard change report

Outcome: round 3 found nothing that blocks

Heads reviewed: `1b796c8d5fb6`, `d6743b79139c`, `9d146f7f0e20`

## Bypasses (0)


## Harms (0)


## False positives (0)


## Broken promises (1)

- `'stamp.json files["adws/adw_modules/a\x00b.py"] = {"source": "adws/gone.py", "sha256": "0"*64}  (or a record with no "sha256" key); install.py --update'` passes install.py update() / retire(). Base: exit 0. Head: exit 1 (ValueError: embedded null byte / KeyError: 'sha256').  retire catches only OSError, and update reads files[rel]["sha256"] unchecked. The crash comes after the template copies and before save_stamp, ensure_gitignore, register and the summary, so retired files sorted after the bad key (a legit adws/adw_retired.py in the probe) aren't removed. Denial of service only: nothing is deleted or overwritten wrongly.

## Old holes (3)

- `'stamp.json {"files": {}, "skill": "a"}'` passes agentsh_install.main version check. Base: passes (no check). Head: passes.  stamp.json is agent-editable, so the version check catches only an honest mismatch. The spec puts this out of scope. New to the known limits.
- `'stamp.json files["adws/adw_modules/engineer_mod.py"] = {"source": "adws/gone.py", "sha256": sha256(file)}; install.py --update'` passes install.py update() removal of retired SSSF code. Base: left in place. Head: deleted.  A forged record deletes a real in-repo SSSF-code file. The spec puts this out of scope because the agent could delete the file directly. It's not in the docs. New to the known limits.
- `'mkfifo adws/adw_sssf_config/repo.yaml'` passes agentsh_install.main. Base: hangs. Head: hangs.  Denial of service only. The spec puts it out of scope. A forged retired record linking to a huge file slows --update the same way, since retire reads it whole. Not in config.md. New to the known limits.

24 candidates, 42 checks.
