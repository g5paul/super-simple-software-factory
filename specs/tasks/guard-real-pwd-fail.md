# Treat a failed `pwd -P` as a path the guard can't resolve

`_REAL` and `_WHERE` in `permissions.py` resolve a path's directory with
`cd -P "$d" && { pwd -P; echo x; }`. The `{ ...; }` group's status is
`echo`'s, so when `pwd -P` fails after `cd -P` succeeds, the group still
succeeds and the directory reads as empty, which then resolves as `/`. A
bypass review of run 6bbc5ca1 found it, present on `main` too:
`_REAL('$L/A/B/x.inc')` returned `/x.inc`.

The reproduction: a physical path over 4096 characters behind a chain of
links, with an ancestor at mode 311 so `pwd -P` can't read its way up. The
engineer's config includes a file there. The guard watches `/x.inc` instead
of the real file, and an agent edit to the real include passes: git then
loads the agent's `core.hooksPath`.

What should change: when `pwd -P` fails, the path counts as not resolving,
the same as when `cd -P` fails, in `_REAL` and in `_WHERE` alike. A watched
path that doesn't resolve then follows the rules the guard already has for
one: refused before the agent if it has a `..`, and otherwise handled as a
missing path. A prefix that resolved before and now fails this way counts as
moved. Every path where `pwd -P` succeeds keeps exactly today's behaviour.
