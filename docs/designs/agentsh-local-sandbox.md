# Design: agentsh as the local sandbox

Status: direction decided by the engineer on 2026-11-08. The research note is done, [agentsh-local-sandbox.md](../research/agentsh-local-sandbox.md) in `docs/research/`, and the proof in sssf-sandbox is being set up. The open item is in [open.md](../open.md).

Under `sandbox: none`, SSSF runs each agent under [agentsh](https://github.com/canyonroad/agentsh) with a policy built from the repo's settings, in place of the text checks that limit a local agent today. `--sandbox openshell` stays as it is.

## Why

SSSF's local guard was never designed as a sandbox. It grew one hole at a time, because a local agent had nothing under it. Every limit searches the command's text and guesses what the shell will run, and the shell always has another spelling. Each fix found the next:

- `cat <<EOF | bash` got past every rule after a guard change that passed every test.
- Quotes inside a word, such as `j''ust test live`, are an accepted limit.
- Backslash-newline split a command past all three checkers until 37273db.
- A `<<-` heredoc's stripped tabs did the same until 4d7e6bc.
- A `<<-EOF` body mixing `\\` and `\` continuations, which two shells join differently, is an accepted limit since run cefd2f59.

agentsh is a product built for this problem by a company, Canyon Road, that open-sources it and builds a commercial offering on it. It enforces where a process starts or opens a file, through seccomp, Landlock, FUSE and eBPF, after the shell has done its parsing. So a rule on `just` with `test live` sees the real arguments however the command was spelled, and a rule on opening `.env` holds against `cat .e\` and `nv`, `$(printf ...)` or a Python one-liner. That retires the class, not the next instance. It supports WSL2 with the full Linux stack (`docs/windows-wsl2-claude-code.md` in agentsh).

## What unifies

| Today, a text check | Under agentsh, enforced in the kernel |
|---|---|
| `forbid` rules: the stream check (`rules.py`), Claude Code's prefix (`sssf_refuse.py`), pi's extension (`sssf_rules.ts`) | `command_rules` on the real program and its arguments |
| The `.env` guard (`secret_files.py`) | `file_rules` denying the open |
| No network limit for a local agent | network rules |

SSSF keeps building the policy from `repo.yaml`, launching each agent under `agentsh wrap` in `agent_cc.py` and `agent_pi.py`, and reading agentsh's audit events into the trace. The text checkers can stay as a second layer that gives the friendlier refusal message, until the proof shows they add nothing. OpenShell isolates the host and the network but can't see a recipe's arguments, so the two can also work together later.

## Before SSSF relies on it

A vendor's claims get a proof first. The research note, `docs/research/agentsh-local-sandbox.md`, answers:

- what `wrap` covers, including the agent's own file tools and network;
- the mode a normal user gets on this machine, and any one-time sudo setup;
- how each SSSF rule maps to agentsh policy;
- network limits, and how agentsh's LLM proxy treats subscription tokens;
- Claude Code and pi under `wrap`;
- audit events;
- maturity, licence and open findings.

The proof then runs in sssf-sandbox. It wraps one Claude Code agent and one pi agent with a policy denying `just test live` and opening `.env`, and replays the bypass corpus from the guard reports of runs c285e8ad, 6e1c3a88 and cefd2f59 and `config.md`'s accepted limits. Every form must die at exec or open.

Not every agentsh feature fits SSSF. Its CLAUDE.md snippet asks the agent to wrap its own commands, which is advisory. Its shell shim edits `/bin/sh` on the host, and skips policy when stdin isn't a TTY unless forced. Only `wrap` fits a local run.

## What the research found

agentsh can be the local sandbox, and it closes the class SSSF kept patching. As the engineer's user, with no root, it runs in its `landlock` mode, and its seccomp checks on exec and file opens still work there. `args_patterns` are Go regexes searched in the process's argv after the shell has parsed it, so every spelling in the guard reports reaches a rule as one exec of `just` with `test live`.

It isn't yet a boundary against an agent working around it. Two conditions come first.

1. **SSSF configures it.** Out of the box `wrap` checks almost nothing: exec checking is off by default (agentsh `internal/config/sandbox.go:21`); the file check skips read-only opens, so reading `.env` isn't checked (`docs/seccomp.md:77`); and when interception setup fails, `wrap` warns once and starts the agent with no checks (`internal/cli/wrap.go:128`). SSSF needs its own root-owned config, and treats "running without interception" as a failed launch.
2. **The server's local API is closed.** Its auth defaults to `none` (`config.yml:43`), and the API can run commands. Without eBPF, which needs a root server, nothing stops an agent from connecting to it on 127.0.0.1. agentsh's own adversarial review marks the missing per-session ownership checks as confirmed and High, finding #29, still open.

Other gaps that matter to SSSF:

- A symlink to `.env` gets past the file check, finding #1, next on the vendor's list.
- Command rules match a basename, so a copy of a binary under another name gets through. A Landlock execute list that leaves out the workspace and `/tmp` closes it.
- Processes the agent didn't start, through `systemd-run --user`, tmux, `at` or ssh, run unfiltered.
- pi is a Node script whose exec resolves to its package's `cli.js`, so a rule on the name `pi` never fires. The rule names the package path and runs only `[nested]`, or it would deny the wrapped pi agent's own launch.
- A plain deny reaches the agent as `Permission denied`, with the rule's message only in the audit event. A `redirect` to a script that prints SSSF's refusal can show text.
- The last release is v0.20.5 from 2026-06-23. The November review's fixes, approvals failing closed among them, are only on `main`.

Even so, it guards better than the regexes against an agent's mistakes, which is all the text guard promises today.

## Decisions

The research note's recommendations, for the engineer to confirm after the proof:

| Decision | Recommendation |
|---|---|
| Ship now or wait | A second layer now, behind a `repo.yaml` key such as `local_guard: agentsh`, off by default. The text checkers stay until the proof shows they add nothing |
| A root server with eBPF | Not yet. The server runs as the engineer's user |
| The API escape | Closing it is the precondition for calling agentsh a boundary: upstream's fix for #29, or a fix carried in the engineer's fork, with `auth.type: api_key` and the key kept from agents |
| Install | With sudo, root-owned binaries, config and policies. A file under `~` is one the agent can edit, and policies reload every 30 seconds |
| Release or `main` | v0.20.6 final for daily use. The proof can test `main` |
| How a denial counts | A blocked exec counts as a refusal toward the three-refusal stop, not a breach |
| Rule format | An argv form, such as `deny_exec`, beside `forbid`, which keeps its text uses |
| Proxy, DLP, FUSE, `BASH_ENV` | All off for SSSF runs. Each changes what the agent sees or where it works |
| Silent fallback | A failed launch, and an upstream request for `wrap --require-interception` |

The seam is one place: a new local sandbox whose `start` puts `agentsh wrap --policy <name> --root <cwd> --` before the agent's command. That covers `agent_cc.py` and `agent_pi.py` through `tree.start`, and leaves quality checks and git outside the wrap.

## Proof on this machine, 2026-11-08

Run as the engineer's user with no sudo, from the v0.20.5 tarball (checksums match), with a scratch config and a policy denying `just test live`, `gh`, `git push` and opening `.env`.

- **seccomp is out on this WSL2 host.** `agentsh detect` reports `NEW_LISTENER filter install failed: device or resource busy`. WSL's own `/sbin/init`, PID 1, installs a seccomp filter with a listener, and every process inherits it. The kernel refuses a second listener under an ancestor that holds one. So agentsh's seccomp exec and file checks never run here, from any shell. That contradicts agentsh's WSL2 guide, which promises the full stack. The distro runs `systemd=true`. Whether WSL without systemd differs isn't tested.
- **What works without root.** With `agentsh-unixwrap` on `PATH`, detect reports `landlock` mode at 60/100: Landlock ABI v5 file limits and Landlock's TCP connect filter. Landlock limits folders and ports. It can't deny one file in an allowed folder or check a command's arguments.
- **ptrace is the remaining way to check execs, and it needs privilege here.** In `wrap`, the agentsh server attaches to the agent, and the server isn't the agent's ancestor. With `kernel.yama.ptrace_scope` at 1, `PTRACE_SEIZE` fails with `operation not permitted`, in both full ptrace mode and the hybrid mode with `agentsh-unixwrap`.
- **Fail-closed has a gap.** With `on_attach_failure: fail_closed`, one run still ran `just test unit` untraced before the attach failed and the run stopped. The other run ran nothing. ptrace's `max_hold_ms` also allows a held syscall after 5 seconds by default.
- **Config notes.** ptrace with the unix-socket wrapper allows exec tracing only. `sandbox.unix_sockets.enabled: false` lets ptrace trace files and network too. `wrap` mounted FUSE over the workspace with `sandbox.fuse.enabled: false` set.

So on this machine, agentsh checks execs only once a tracer has `CAP_SYS_PTRACE` or `ptrace_scope` drops to 0. Both need the engineer's sudo, and the choice is open.

## Next

1. The proof in sssf-sandbox, per the note's plan, once the ptrace choice above is made. The engineer installs agentsh, its config and the policy with sudo. Then `agentsh detect`, one wrapped Claude Code agent and one wrapped pi agent, and today's bypass corpus with the escape probes.
2. The decisions above, settled from the proof.
3. A guard chain run for the wrap.
