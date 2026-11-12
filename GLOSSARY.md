# SSSF

The factory that runs AI coding agents through chains of plan, build, test and review in the repos it's installed in, and the hub that ships it.

## Sending work upstream

**Product change**:
A change to the project's own code, tests or docs, the kind the project itself would take. A README edit a builder makes as part of a fix is one.
_Avoid_: patch, fix (for the thing sent upstream)

**SSSF file**:
A file in a repo that exists because of SSSF: what the install put there, and what a run writes for SSSF's own use.
_Avoid_: factory file, tooling

**Record**:
A spec or doc a run writes about its own work, such as the plan in `specs/` or the summary in `app_docs/`. A record is an SSSF file, never a product change.
_Avoid_: artifact, run doc

**Feature**:
One piece of product work, as commits: those on a feature branch, those one run made, or those of one ticket feature.
_Avoid_: change set, PR (for the commits themselves)

**Export**:
Making a branch, based on upstream, that holds a feature's product changes and no SSSF files.
_Avoid_: upstreaming, patch
