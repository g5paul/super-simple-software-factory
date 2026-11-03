# Secret scan before every push

`.githooks/pre-push` runs [gitleaks](https://github.com/gitleaks/gitleaks) over the commits a push would send. For a branch the remote already has, that's the commits past its copy. For a new branch, it's the commits on none of the remote's branches. A find refuses the push, and gitleaks prints it redacted, with its rule, file and commit. The hook passes `--verbose` for that; without it gitleaks 8.30.1 prints only a count. Without gitleaks the hook refuses too, so a missing scanner can't let a push through unscanned.

- **Set up once per clone:** `git config core.hooksPath .githooks`. Git doesn't run a repo's tracked hooks on its own, so a fresh clone has to opt in.
- **gitleaks** 8.30.1 or later. On this machine it's in `~/.local/bin`, installed from its GitHub release after checking the release's SHA-256.
- **Tested** by running the hook as git does, with ref lines on stdin. A clean commit and a clean new branch pass. A planted fake GitHub token is refused on an existing branch and on a new one. With gitleaks missing from `PATH`, the hook refuses.
- **To push anyway,** `git push --no-verify` skips the hook. Use it only knowing nothing was scanned.

## Who pushes

On this machine `~/.claude/settings.json` denies `git push`, `git * push`, `gh pr create`, `gh pr merge`, `gh repo create`, `gh release create` and `gh api` in every Claude Code session, so every push is one the engineer runs (`! git push ...` in a session, or a terminal). The rules match commands as written: they stop pushes nobody asked for, not a session set on getting around them. SSSF agents run under `--safe-mode`, where these rules aren't checked; each repo's `agent_rules` cover those. GitHub's "Block command line pushes that expose my email" refuses a push carrying a private address.

## In the project repos

yt-summarizer, pi-agent-observability and fusion-harness each track the same hook, with `core.hooksPath` set in their clones. [projects.md](projects.md) lists the commits.
