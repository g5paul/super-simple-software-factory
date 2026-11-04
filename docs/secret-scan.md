# Secret scan before every push

`.githooks/pre-push` refuses any push while `SSSF_ADW_ID` is set, so an SSSF agent can't push. Then it runs [gitleaks](https://github.com/gitleaks/gitleaks) over the commits a push would send. For a branch the remote already has, that's the commits past its copy. For a new branch, it's the commits on none of the remote's branches. A find refuses the push, and gitleaks prints it redacted, with its rule, file and commit. The hook passes `--verbose` for that; without it gitleaks 8.30.1 prints only a count. Without gitleaks the hook refuses too, so a missing scanner can't let a push through unscanned.

- **Set up once per clone:** `git config core.hooksPath .githooks`. Git doesn't run a repo's tracked hooks on its own, so a fresh clone has to opt in.
- **gitleaks** 8.30.1 or later. On this machine it's in `~/.local/bin`, installed from its GitHub release after checking the release's SHA-256.
- **Tested** by running the hook as git does, with ref lines on stdin. A clean commit and a clean new branch pass. A planted fake GitHub token is refused on an existing branch and on a new one. With gitleaks missing from `PATH`, the hook refuses.
- **Private names.** The hook also refuses a push whose commits name a private project, in a message or a change, when `.git/sssf-private-names` lists it. In the hub, that keeps the records of unlisted installs out of this public fork. [projects.md](projects.md#unlisted-projects) says where they go. SSSF's git-directory guard puts the file back and stops the run if it changes during an agent call, the engineer's own edit included, so edit it between runs.
- **To push anyway,** `git push --no-verify` skips the hook. Use it only knowing nothing was scanned.

## Where it comes from

The hook is SSSF's template, `.claude/skills/sssf/templates/githooks/pre-push`. `install.py` stamps it into every repo it installs into, and `--update` adds it to older installs. The hub's `.githooks/pre-push` is a symlink to the template, so the hub runs the same hook it ships. The installer never sets `core.hooksPath`: it prints the line for the engineer, names any `.git/hooks` file the switch would turn off, and says when gitleaks is missing. `cookbooks/install.md`, "Secret scan before every push", is the operator's guide. `tests/test_install_hook.py` covers the install and the hook.

## Who pushes

On this machine `~/.claude/settings.json` denies `git push`, `git * push`, `gh pr create`, `gh pr merge`, `gh repo create`, `gh release create` and `gh api` in every Claude Code session, so every push is one the engineer runs (`! git push ...` in a session, or a terminal). The rules match commands as written: they stop pushes nobody asked for, not a session set on getting around them. SSSF agents run under `--safe-mode`, where these rules aren't checked; each repo's `agent_rules` cover those. GitHub's "Block command line pushes that expose my email" refuses a push carrying a private address.

## In the project repos

pi-agent-observability and fusion-harness each added the hook by hand before the installer shipped it, with `core.hooksPath` set in their clones. [projects.md](projects.md) lists the commits. On their update to skill 96f1130, pi-agent-observability took the template's hook. fusion-harness kept its own and copied in the agent refusal, on `sssf-run` only.
