# Stop the run when any prefix of a watched path resolves somewhere new

The git-directory guard saves where each symlink on the way to a watched path
points, and stops the run with "NOT all put back" if one points somewhere else
after the agent. It records only what was a symlink when the run started. A
bypass review in run 6d75d21b found three old holes, all on `main`, where the
agent turns a real directory on the way into a link:

- `include.path = $HOME/d/../x.inc` with `~/d` a real directory. The agent
  runs `rmdir ~/d; ln -s $T/evil/a ~/d` and writes an `fsmonitor` line to
  `$T/evil/x.inc`. Git loads it, and `restore_git_dir` returns `[]`.
- `.git/config` has `include.path = ../shared.inc`. The agent moves `.git` to
  `$T/evil/g`, links `.git` there, and writes `$T/evil/shared.inc`. Every
  watched file reads the same through the link.
- `include.path = $HOME/lnk/../x.inc` with `~/lnk` linked to `$T/out/a`. The
  agent leaves `~/lnk` and replaces `$T/out/a` with a link to `$T/evil/a`.

What should change: before the agent, record where every prefix of each
watched path resolves, walking the path as written, the way the kernel does.
For `$HOME/d/../x.inc` that is `$HOME`, `$HOME/d`, `$HOME/d/..` and the full
path. After the agent, resolve them again. If any prefix now resolves
somewhere else, the run stops with "NOT all put back" and names it, the same
as a retargeted link today, and nothing is written through it. This replaces
the separate list of links on the way rather than adding a second one. A
prefix that didn't exist before and still doesn't keeps today's behaviour.
Every path whose prefixes resolve the same keeps exactly today's behaviour.

## From an earlier run's spec review

Run 7bc35cc5 wrote this design, and its spec review passed the design's size:
a prefix map that replaces `_LINKS` and the `links`/`parents` fields, with no
new mode or module-level state. It stopped on what `restore_git_dir` writes
once a prefix has moved. Two rules came out of the review:

- Never write through a moved prefix. A real directory swapped for a dangling
  link leaves `_REAL` of the paths under it empty, and today's `r in ("", p)`
  rule would still hand them to `_UNPACK`, so tar would write through the
  link. Skip a path only when its current `_REAL` is `""` and it sits under a
  moved prefix. Take the moved prefixes from the prefix comparison, never
  from the list of changed paths: a `hooks` or `core.hooksPath` directory
  whose contents changed hasn't moved. Use a prefix's earlier resolution as a
  root only when its last component isn't `.` or `..`, since `$HOME/d/..`
  resolved to `$HOME`.
- Every other path is put back as today. A path whose `_REAL` is unchanged is
  safe to write. Two tests pin it: hole 1 plus an `fsmonitor` line added to
  `~/.gitconfig`, where `~/.gitconfig` is put back and the run still says
  "NOT all put back"; and a retargeted HOME link (as in
  `test_a_retargeted_home_link_is_seen`) plus an edit to the physical
  `.gitconfig`, which is put back.

Also name in Out of scope the `config.md` limit that an ignore file the agent
changed through a link can hide one of its own new files from the rollback,
and say the refusal of a `..` that doesn't resolve stays as it is.
