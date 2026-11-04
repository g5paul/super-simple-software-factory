# Refuse every watched path with a `..` that doesn't fully resolve

Run d817e62b made the guard refuse a watched path with a `..` after a
component that doesn't exist yet. Its bypass review found the mirror case
still open: `<repo>/lnk/../missing/x.inc`, with `<repo>/lnk` linked to
`out/a`. The `..` comes before the missing part. `_REAL` can't resolve the
path, so the guard watches the lexical `<repo>/missing/x.inc`. Once the agent
runs `mkdir out/missing` and writes `[core] fsmonitor = touch PWNED` to
`out/missing/x.inc`, git loads that file, and `restore_git_dir` returns `[]`.

What should change: widen the refusal to every watched path that has a `..`
component and doesn't fully resolve, wherever the missing part sits. The
message stays as it is now, naming the include and the path and saying to
write it without `..` or to create the missing directory first. A path with
`..` that fully resolves, and a path with no `..`, keep exactly today's
behaviour.
