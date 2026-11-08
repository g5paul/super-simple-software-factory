# Make the OpenShell project label exact, so one project's sweep can't delete another's sandboxes

Guard run 32f87444 added `--sandbox openshell` on this branch (build 5dc3891).
Its review (`adws/adw_data/sessions/32f87444/context_handoff/guard_report.md`,
broken promise 1) found that `sandbox_openshell.label` maps a project name to
a label by replacing every character outside `[A-Za-z0-9_.-]` with `-` and
cutting it at 63. So `my app` and `my-app`, `app+x` and `app-x`, `café` and
`caf-`, and two 64-character names that differ only in the last character all
get the same `sssf=` label. `sweep` lists by that label, so a run in one
project deletes the live sandboxes of the other, and the work they hadn't
committed in the VM is lost. Vercel and E2B compare the project tag exactly.

## What should change

- The `sssf=` label is distinct for every distinct project name: keep a
  readable cleaned prefix, cut short enough, then `-` and a short hash of the
  exact name, within OpenShell's label length limit. Read
  `~/.local/opt/openshell/bin/openshell sandbox create --help` for the limit
  and the allowed characters.
- `_create` and `sweep` use the same function, so a sandbox made by a run is
  found by that project's sweep and by no other's.
- The `adw_id` label is SSSF's own hex id; leave it as it is.
- `docs/designs/openshell-sandbox.md`'s "As built" section says how the label
  is made, in one sentence.

## Tests

- Every colliding pair the review named gets two different labels.
- A label made for `_create` matches the selector `sweep` lists with, for the
  same project.
- Every label passes OpenShell's limits, including for a 200-character name
  and a non-ASCII one.
- Every existing test passes unchanged.

## Out of scope

- Two repos whose main checkouts share a folder name count as one project in
  every provider, since the project is that folder's name (the review's old
  hole 2). This change keeps that.
- Everything else in the provider.
