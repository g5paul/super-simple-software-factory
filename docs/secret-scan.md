# Secret scan before every push

`.githooks/pre-push` is the git hook SSSF installs to scan each push. It runs [gitleaks](https://github.com/gitleaks/gitleaks) over the commits a push would send, and it refuses any push from an SSSF agent.

## What the hook refuses

The hook refuses a push in each of these cases:

- **An SSSF agent is pushing.** The hook refuses any push while `SSSF_ADW_ID` is set.
- **gitleaks is missing.** Without gitleaks the hook refuses, so a missing scanner can't let a push through unscanned.
- **A commit names a private project.** If `.git/sssf-private-names` lists a name, the hook refuses a push whose commits name it in a message or an added line. In the hub, this check keeps the records of unlisted installs out of this public fork. [projects.md](projects.md#unlisted-projects) says where those records go.
- **gitleaks finds a secret.** gitleaks prints each find redacted, with its rule, file and commit. The hook passes `--verbose` for that, because without it gitleaks 8.30.1 prints only a count.

The scan covers only the commits the push would send. For a branch the remote already has, those are the commits past the remote's copy. For a new branch, they are the commits on none of the remote's branches.

If `.git/sssf-private-names` changes during an agent call, SSSF's git-directory guard puts the file back and stops the run. That includes the engineer's own edit, so edit the file between runs.

## Turn it on

1. Install gitleaks 8.30.1 or later. On this machine, the one SSSF is built on, gitleaks is in `~/.local/bin`, installed from its GitHub release after checking the release's SHA-256.
2. Run `git config core.hooksPath .githooks` once in each clone. Git doesn't run a repo's tracked hooks on its own, so a fresh clone has to opt in.

To push without the scan, run `git push --no-verify`. That skips the hook, so use it only if you accept that nothing was scanned.

## How it was tested

The tests ran the hook as git does, with ref lines on stdin:

- A clean commit and a clean new branch pass.
- The hook refuses a planted fake GitHub token, on an existing branch and on a new one.
- With gitleaks missing from `PATH`, the hook refuses.

## Where it comes from

The hook is SSSF's template, `.claude/skills/sssf/templates/githooks/pre-push`. `install.py` stamps it into every repo it installs into, and `--update` adds it to older installs. The hub's `.githooks/pre-push` is a symlink to the template, so the hub runs the same hook it ships.

The installer never sets `core.hooksPath`. Instead, it prints the line for the engineer to run, names any `.git/hooks` file the switch would turn off, and says when gitleaks is missing. `cookbooks/install.md`, "Secret scan before every push", is the operator session's guide. `tests/test_install_hook.py` covers the install and the hook.

[projects.md](projects.md#pre-push-secret-scan) records which project repos have the hook.

## Who pushes

This section is the hub's own machine setup, not part of SSSF.

On this machine, `~/.claude/settings.json` denies `git push`, `git * push`, `gh pr create`, `gh pr merge`, `gh repo create`, `gh release create` and `gh api` in every Claude Code session. So the engineer runs every push, with `! git push ...` in a session or from a terminal. The deny rules match commands as written. They stop pushes nobody asked for, but not a session set on getting around them.

SSSF agents run under `--safe-mode`, where these deny rules aren't checked. Each repo's `agent_rules` cover agents instead. GitHub's "Block command line pushes that expose my email" setting refuses a push that carries a private address.
