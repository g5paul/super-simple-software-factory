# Keep Claude Code agents out of the engineer's own Claude Code files

Item 13 of the Next list in `docs/designs/agentsh-local-sandbox.md`. Start from `main`. The hub session ran the probes below on 2026-11-10, on build 05b3777f, under the installed policies of skill c9d3950 and on a private agentsh server with probe copies of sssf-sandbox's policy.

## The problem

Claude Code keeps its login, settings, plugins, transcripts and program in the engineer's home folder, and runs from there outside the wrap. `CLAUDE_WRITE` gives agents every operation on `~/.claude/**`, `~/.claude.json` and `~/.local/share/claude/**`. The `sssf-agent-config` deny list guards nine paths in `~/.claude`: `settings.json`, `settings.local.json`, `CLAUDE.md`, `AGENTS.md`, `skills/**`, `rules/**`, `hooks/**`, `agents/**` and `commands/**`. Everything else is open. Under `agentsh wrap`, with the installed sssf-sandbox policy, an agent:

1. **Can change what the engineer's Claude Code runs.** It created a file in `~/.local/share/claude/versions/`, the folder that holds the program `~/.local/bin/claude` points to, and the rule allows writing the existing `2.1.322` there too. It created files in `~/.claude/plugins/` and `~/.claude/shell-snapshots/`, which the engineer's Claude Code loads: plugins carry hooks, and the Bash tool sources a snapshot. It created a file next to `~/.claude/settings.json`. Any of these runs the agent's code outside the wrap, the next time the engineer uses Claude Code.
2. **Can read the engineer's full Claude Code login.** Claude Code opens `~/.claude/.credentials.json`, the OAuth login with its refresh token.
3. **Can read every other repo's sessions.** `~/.claude/projects/` holds the transcripts of every repo the engineer has run Claude Code in, `~/.claude/file-history/` the files those sessions edited, and `~/.claude.json` a `projects` map keyed by each repo's path. Each running session's `~/.claude/sessions/<pid>.json` gives its `cwd` and name, and Claude Code opens every one at start. `~/.claude/jobs/` holds other Claude Code sessions' scratch files.

## What the probes showed works

With `CLAUDE_CONFIG_DIR` set to a fresh folder, and `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token` in place of the login, Claude Code 2.1.322 under SSSF's flags (`--safe-mode --disable-slash-commands --permission-mode bypassPermissions`, stream-json):

- **It worked with `~/.claude` closed.** It wrote its `.claude.json`, `projects/`, `sessions/`, `session-env/`, `backups/` and `plugins/` into that folder. Under a probe policy with `${HOME}/.claude`, `${HOME}/.claude/**`, `${HOME}/.claude.json` and `${HOME}/.claude.json.*` taken out of `CLAUDE_WRITE`, a call with a Bash tool call and a Read worked, and so did a `--resume` of it, which remembered its earlier answer. It still looked at `~/.claude`, `daemon`, `jobs`, `session-env`, `settings.json` and `shell-snapshots`, was refused, and went on. Outside its folder it wrote only lock files under `~/.local/state/claude/locks`.
- **The agent's tools never see the token.** A Bash tool call that ran `env | grep -c '^CLAUDE_CODE_OAUTH_TOKEN='` printed 0; the event log shows the `env` and `grep` execs ran. `sssf-proc-secrets` already refuses `/proc/*/environ`.
- **A planted hook stays dead.** A `SessionStart` and a `UserPromptSubmit` hook in the folder's `settings.json` didn't fire under `--safe-mode`, and did without it. So a hook one agent plants in the folder doesn't run in the next agent.
- **The engineer's skill repos go unread.** It didn't read `~/github/skills`, `~/github/plugins`, `~/CLAUDE.md` or `~/AGENTS.md`, which `CLAUDE_READ` allows. It read `~/.config/anthropic` once.

## What should change

For Claude Code agents under `local_guard: agentsh` only:

1. **Each run gets its own Claude Code config folder**, in the run's session folder, and `child_env` sets `CLAUDE_CONFIG_DIR` to it for every Claude Code agent in the run and for `check_login`. One folder per run, so a retry's `--resume` finds its transcript, and no run reads another's.
2. **Agents sign in with a token.** `child_env` passes `CLAUDE_CODE_OAUTH_TOKEN` from the launching shell's environment, or else from the file `--sandbox` already reads, `~/.config/sssf-sandbox/token.env`. With neither, the run refuses before any worktree or run row exists, and says to make one with `claude setup-token` and save it there, mode 600. `check_login` runs with that environment. The spec says what the token can do, inference only for about a year, and that agents never get the refresh token again.
3. **`CLAUDE_WRITE` drops `${HOME}/.claude`, `${HOME}/.claude/**`, `${HOME}/.claude.json` and `${HOME}/.claude.json.*`.** No rule then allows any operation there, so default-deny refuses reads as well as writes.
4. **`~/.local/share/claude` becomes read-only.** Agents keep `READ` on it, so they can run Claude Code, and lose every write. `child_env` sets `DISABLE_AUTOUPDATER=1` for Claude Code agents, so Claude Code doesn't try to update itself there. The spec says what else under `.local/share/claude`, `.local/state/claude` and `.cache/claude-cli-nodejs` the engineer's Claude Code loads or runs, and keeps writes only where it loads nothing.
5. **`CLAUDE_READ` drops `${HOME}/github/skills/**` and `${HOME}/github/plugins/**`**, which only the engineer's own skill links needed. The spec checks whether the other `CLAUDE_READ` paths are still read, and keeps those that are.
6. **`config.md`, in "Running local agents under agentsh"**, says Claude Code agents run from their own config folder, sign in with a token from `claude setup-token`, and can't read or write `~/.claude`, `~/.claude.json` or Claude Code's program. It drops the limit line on other repos' transcripts that commit c9d3950 added, and keeps what still holds: `/proc/<pid>/cwd` and `cmdline` show the folders other processes run in. It says the engineer reinstalls with `just agentsh-install` after the update.

## The bar

The earlier bars stand. Under the rendered policy, a Claude Code agent:

- can't read `~/.claude/.credentials.json`, `~/.claude.json`, `~/.claude/projects/**`, `~/.claude/file-history/**`, `~/.claude/sessions/**`, `~/.claude/jobs/**` or `~/.config/sssf-sandbox/token.env`, and can't list `~/.claude`;
- can't create, write, rename or delete anything under `~/.claude`, at `~/.claude.json`, or under `~/.local/share/claude`, `2.1.322` included;
- runs: a call with a Bash tool call and a Read, a `--resume` of it, and a subagent, under each repo's policy.

A run with no token refuses with that message and starts no agent. A pi agent's policy doesn't change. Any change to the guard beyond the six above is a finding, and so is a `config.md` line that promises more than the probes show.

## Testing

Unit tests: `child_env` for a Claude Code agent under agentsh has `CLAUDE_CONFIG_DIR` in the run's session folder, the token from the environment or the file, and `DISABLE_AUTOUPDATER=1`; without a token, the launch refuses. The rendered policy's `sssf-agent-write` has no path under `${HOME}/.claude` or `${HOME}/.claude.json`, and no write on `${HOME}/.local/share/claude`. The tests use a temp token file, never the real one.

After the merge, the engineer reinstalls both repos' policies. The hub session reruns the probes above under the wrap in the hub and sssf-sandbox, with new decoy files only, and then a real chain in sssf-sandbox under `local_guard: agentsh`.

## This run is under agentsh

The hub stays on `local_guard: agentsh`. This run's agents run under the installed policy, which still allows `~/.claude`, so they sign in as before; the new code only reaches the runner after the merge. The builder runs the suite the way run 47b4d0ea's did, from a copy outside the repo.

## Out of scope

Runs under `local_guard: text`, which keep the engineer's login and `~/.claude`. VM runs, which already sign in with the token. pi's policy. `/proc/<pid>/cwd` and `cmdline`. The agent cache's shared writes across one repo's runs, a known limit.
