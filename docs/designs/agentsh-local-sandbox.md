# Design: agentsh as the local sandbox

Status: direction decided by the engineer on 2026-11-08; design pending the research note and a proof. The open item is in [open.md](../open.md).

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

## Next

1. The research note.
2. The proof in sssf-sandbox. Building agentsh needs Go or a release package, installed by the engineer.
3. This design's decisions, from the note.
4. A guard chain run for the wrap in `agent_cc.py` and `agent_pi.py`.
