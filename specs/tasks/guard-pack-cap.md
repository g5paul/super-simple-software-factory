# Fail closed when the git-directory pack hits its cap

This branch adds `sssf-private-names` to the git-directory guard's watched
files. A bypass review found that it opens a bypass against `main`. When
`.git/sssf-private-names` is a symlink to a file over 16 MB, the pack that
follows links stops at its size cap while reading that file. Every path packed
after it is left out of both the before and after packs, `~/.gitconfig`
included. An agent can then write an fsmonitor command into a linked
`~/.gitconfig`, and the guard doesn't see it. On `main` the same write stops
the run. A `hooks` link to a large tree has caused the same masking before;
the comment by `PACK_CAP` accepts it.

Make a pack cut off at the cap fail closed: the run stops with a git-guard
breach that says the watched paths were too large to check, instead of
silently comparing a partial pack. That closes the new trigger and the old
`hooks` one together. A setup whose watched paths really are that large then
stops every run, and the message must say why and what to do.

The reproduction: an agent appends `[core]` and `fsmonitor = touch
/nonexistent/pwned` to `~/dotfiles/gitconfig`, which `~/.gitconfig` links to,
while `.git/sssf-private-names` links to a 17 MB file. Keep everything this
branch already does for the names file.
