# Keep the OpenShell sandbox name within the CLI's 19-character limit

The first proof run in a project failed before any agent started:
`openshell sandbox create failed: name exceeds maximum length (20 > 19)`.
`OpenShell._create` names the sandbox `sssf-<adw_id>-<uuid hex[:6]>`, which is
5 + 8 + 1 + 6 = 20 characters. OpenShell 0.1.7 refuses a name over 19, though
its `--help` doesn't say so; a 19-character name was checked by hand and
works. The labels are fine as they are.

## What should change

- The name's random suffix is 4 hex characters, so a name is
  `sssf-<adw_id>-<hex4>`, 18 characters. Name the CLI's 19-character limit as a class attribute of `OpenShell`, beside
  `session_minutes`, with the error text above as its source in a comment. Add
  no module-level name: the chain counts one as new state and stops, and have
  `_create` raise `SandboxFailed` with a plain message if a name would exceed
  it, instead of letting the CLI refuse it.
- `sweep` parses names with the new suffix length. It still reads the
  `adw_id` from the name and leaves alone a name that doesn't parse.
- `templates/justfile`'s `openshell-setup` recipe points to
  `docs/research/openshell.md` for install notes. That file is only in the
  hub, not in a project. Point to the section of `references/config.md` in
  the skill that covers the OpenShell mode instead, and add a short install
  note there if it has none: unpack the release's CLI, gateway, supervisor
  and VM driver tarballs into a folder on `PATH`, and check each against
  NVIDIA's published SHA-256.
- `docs/designs/openshell-sandbox.md`'s "As built" section gives the name
  format and the limit.

## Tests

- Every name `_create` makes is at most 19 characters, matches what `sweep`
  parses, and gives back the run's `adw_id`.
- `sweep` deletes a stale sandbox with the new name format and leaves a name
  in the old 6-character format alone.
- Every existing test passes, updated only where it pins the old suffix
  length.

## Out of scope

Everything else in the provider.
