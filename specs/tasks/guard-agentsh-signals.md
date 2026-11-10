# Refuse an agent's signals to processes outside its session

Item 9 of the Next list in `docs/designs/agentsh-local-sandbox.md`. Start from `main`, once the engineer has installed the fork build below.

## The problem

An agent under agentsh can signal any process the engineer's user owns. The probes are in the design doc's section "Stock policies, sockets, signals and DNS, 2026-11-10": under the hub's policy, an agent killed two decoy processes outside its session, with TERM and with KILL. There are two reasons. The server config template `agentsh_sssf.yaml` sets `ptrace.trace.signal: false`, so the tracer doesn't stop on signal syscalls. And in ptrace mode, agentsh's signal handler allowed every signal without reading the policy, until the fork patch below.

What an agent can do with it:

- Kill the SSSF runner. The run then ends without its after-call checks, the ones that put back a renamed `.git` and catch a swapped workspace folder.
- Kill the engineer's editor, other Claude sessions, or other runs.

What it can't do, and still can't after this change: free itself from the tracer, which runs as user `agentsh`, or `ptrace` a process, which Yama's `ptrace_scope` 1 refuses.

## Before this run: the fork build

Two fork patches in `~/github/agentsh`, made and tested outside this chain, in one build the engineer installs:

1. **Signals in ptrace mode.** The handler judges each `kill`, `tkill`, `tgkill`, `rt_sigqueueinfo`, `rt_tgsigqueueinfo` and `pidfd_send_signal` against the session policy's `signal_rules`, with agentsh's own target types: `self`, `session` for another process the tracer holds in the same session, `parent`, `external` and `system`. `kill(0)` and `kill(-pgid)` are judged for every member of the group, and one refusal refuses the call; `kill(-1)` counts as `external`. Signal 0 is allowed, since it delivers nothing. It also judges the four calls that make the kernel signal a process later: `fcntl` `F_SETOWN` and `F_SETOWN_EX`, and `ioctl` `FIOSETOWN` and `SIOCSPGRP`. A new owner is judged as a SIGKILL to it, since `F_SETSIG` can make its signal any signal. A policy with no `signal_rules` still allows every signal.
2. **The DNS proxy's forward** (Next item 8). An allowed query goes only to the resolvers in `/etc/resolv.conf`, rebuilt from the one question the proxy checked.

The hub session fills in this table when the build exists, and the run doesn't start until it does:

| | |
|---|---|
| Commit | `472d63cf` on `sssf/dns-signals`, over `3a36c68f` |
| Version | `0.20.6-dev.fork+472d63cf` |
| sha256 | `f9da98024093f13634f26d349237eb217dc5b10fab826d222f08d8a88f79933e` |

## What should change

1. **`agentsh_sssf.yaml` sets `trace.signal: true`.** Nothing else in the server config changes.
2. **`policy_text` adds `signal_rules`** to every policy it writes, for both harnesses:
   - allow every signal to `self` and `session`;
   - refuse every signal to `parent`, `external` and `system`, with a message that says the agent may signal only its own processes.

   "Every signal" is `@all` and the numbers 32 to 63. agentsh's `@all` stops at 31, and a signal no rule names is refused, so without the numbers a program's own real-time signals fail: glibc uses 32 and 33 between its threads. The engine takes the first rule that matches, so the allow rules come first. That keeps a process in the session free to signal its parent when the parent is in the session too; the SSSF runner, the agent's real parent, is outside it and gets `external`.
3. **The pin names the new build.** `PINNED_SHA256` becomes the sha256 above, and its comment names the commit. Nothing else in `check_machine` changes.
4. **`config.md`, in "Running local agents under agentsh"**, says which build a repo needs and why: this one, since earlier builds let an agent kill any process the engineer's user owns, the SSSF runner among them, and let a DNS query carry data to a server the agent picked. It says the engineer reinstalls with `just agentsh-install` after the update, since `check_machine` refuses while the installed config and policies differ from what the checkout renders.
5. **`docs/guide/install.md`**'s pinned-versions row names the new build.

## The bar

The earlier bars stand. An agent's signal to a process outside its session is refused, whichever of the six syscalls sends it, and so is making a process outside the session a file's signal owner. Signals inside the session keep working: a shell's job control, `timeout`, `kill $!`, a test runner stopping its workers, and Claude Code and pi stopping their own tools. A policy that breaks a run is a finding. Any change to the guard beyond the five above is a finding. A `config.md` line that promises more than the probes showed is a finding.

## Testing

Unit tests: `policy_text`'s output has the rules for both harnesses, in the order above, each naming `@all` and 32 to 63, and the rendered server config has `signal: true`. The tests monkeypatch `PINNED_SHA256`, so they don't see the value.

After the merge and the engineer's reinstall, the hub session runs on the installed build: decoy processes outside the session survive TERM and KILL from each of the six syscalls, and `F_SETOWN` with `F_SETSIG` set to SIGTERM fails; `sleep 9 & kill $!`, `timeout 1 sleep 5` and a background job's `kill %1` work; the check set holds as before; Claude Code and pi exit 0 under the wrap; and a guard run passes under `local_guard: agentsh`.

## This run is under the text guard

`check_machine` refuses while the pin is wrong, so this run can't start under agentsh. The engineer sets the hub's `local_guard: text` for this run only and sets it back after the merge and reinstall.

## Out of scope

The fork's code, reviewed and tested outside this chain. `resource_limits`, `env_policy`, `package_rules` and `unix_socket_rules`: default-deny already refuses socket connects. The pin check's two old holes (Next item 1). The tool-config task. pi's policy.
