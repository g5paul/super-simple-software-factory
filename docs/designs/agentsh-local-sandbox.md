# Design: agentsh as the local sandbox

Status: direction decided by the engineer on 2026-11-08. The research note is done, [agentsh-local-sandbox.md](../research/agentsh-local-sandbox.md) in `docs/research/`, and the proof in sssf-sandbox passed on 2026-11-08. The engineer settled the decisions and the rollout the same day, and nothing is built in SSSF yet. The open item is in [open.md](../open.md).

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

SSSF keeps building the policy from `repo.yaml`, launching each agent under `agentsh wrap` in `agent_cc.py` and `agent_pi.py`, and reading agentsh's audit events into the trace. The text checkers leave the `sandbox: none` path one at a time once the rollout below has its results. They stay for VM runs, because agentsh doesn't run inside those VMs. OpenShell isolates the host and the network but can't see a recipe's arguments, so the two can also work together later.

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

The engineer settled these on 2026-11-08, after the proof. OpenShell stays the local sandbox and agentsh replaces the text guard under `sandbox: none`.

| Decision | Settled |
|---|---|
| Direction | agentsh replaces the text guard under `sandbox: none`. `--sandbox openshell` stays the local sandbox and doesn't change, so repos on it aren't part of this rollout. agentsh is a guard, not isolation: agents still run on the machine with its files, and quality checks still run outside the wrap |
| Text guard patches | Frozen from 2026-11-08. A new spelling that gets past the text checks becomes a case in `docs/designs/agentsh/corpus.sh`, not another regex |
| Who runs the server | A dedicated `agentsh` service user with `CAP_SYS_PTRACE` and `CAP_DAC_READ_SEARCH`. No root server and no eBPF, and agents can't reach it through ptrace because they run as the engineer |
| The API escape | Policy denies on the socket, TCP port 18080 and a proxy CONNECT to it, with auth left at `none`. The corpus keeps those checks, so a regression fails loudly. The API stays open to unwrapped local processes |
| Install | With sudo, root-owned binaries, config and policies. SSSF generates a policy from `repo.yaml` and prints the install command, and a run refuses to start when the installed copy differs, at the cost of one sudo step per rule change |
| Which build | The fork's `sssf/patched`, pinned, with the build recipe and checksum recorded here. The installed build is `241cd393` on `sssf/confine`, from [the namespace hole](#the-namespace-hole-2026-11-09). The guard pins it: `PINNED_SHA256` is `ee6cd37e…20bce` since run 7047b87c, merged as 7b36e01. v0.20.6 final lacks all nine patches |
| How a denial counts | Only a denied `ptrace_execve` with a named rule counts toward the three-refusal stop, and it's never a breach. File and network denials go to the trace |
| Rule format | An argv form, `deny_exec`, which becomes agentsh's `command_rules`, beside `forbid`, which keeps its text uses |
| Proxy, DLP, FUSE, `BASH_ENV` | The session proxy on, because the network allowlist depends on it. The LLM proxy, DLP, FUSE and `BASH_ENV` off |
| Silent fallback | None. A run refuses to start if the service isn't active, the wrap can't attach, or the service reports weaker enforcement than the policy needs. An upstream request for `wrap --require-interception` can wait |
| Other credentials | A deny list in the policy covering `~/.ssh`, `~/.aws`, `~/.config/gh`, `~/.config/sssf-sandbox` and similar, retested with both agents |
| Default-deny for files | Adopted by the engineer on 2026-11-08, after [the third run](#the-seventh-patch-and-the-third-run). The wrap's policies end in a deny of everything, with 20 file rules for Claude Code and 18 for pi, generated rather than written by hand. It accepts these limits: an agent can read its own sign-in, Claude Code can write `~/.claude.json`, which can hold MCP settings, and can read other projects' transcripts under `~/.claude/projects`, and `/tmp` is shared. A new tool may need a path added, and the trace names the path when it does |
| Install files and the config check | Decided 2026-11-09, after [the first guard run](#the-first-guard-run-cf9d8014). The install command writes to a fresh temp folder outside the repo, and `check_machine` compares the installed config byte for byte with what SSSF renders, apart from the machine's `policies.allowed` list |
| The push rule | Decided 2026-11-09. `sssf-no-push` denies `push` as git's first argument, or anywhere after a leading option. It accepts that `git -c a=b commit -m push` is denied and that an `argv[0]` with a space gets past it |
| Workspace | Pinned, because agentsh [widens `--root` to the repo top](#agentsh-widens-the-workspace-to-the-repo-top). `sssf.yaml` sets `policies.detect_project_root: false`, and the wrap refuses a `--root` that isn't the checkout's top |
| Sign-ins | One policy per agent type. Claude Code can't open `~/.pi/agent/auth.json` and pi can't open `~/.claude/.credentials.json`. An agent can still read its own sign-in. Credential injection waits: it's untested and may not fit OAuth refresh |
| Network hosts | The base allowlist stays model hosts, sign-in hosts and package registries. A `network_allow` key in `repo.yaml` adds a repo's own hosts, and each change takes the sudo step |
| Secret files | The policy's rules come from `secret_files.py`'s logic: `.env.sample` is allowed, as the guard allows it, and each repo's `secret_files` patterns are added |

## Rollout

1. A guard chain run builds the wrap, behind `local_guard: agentsh` in `repo.yaml`.
2. sssf-sandbox runs a full SDLC chain under it. The proof only ran single agents.
3. The hub moves, so every guard run tests it.
4. pi-agent-observability moves, for heavy pi use and the hang question.
5. fusion-harness moves.

Each project's session makes the change in its own repo.

agentsh becomes the `sandbox: none` default after at least 10 real chain runs across at least two repos, with both agents, no guard miss, no denial that breaks legitimate work, and no hang that a run's timeout fails to stop. New installs then get it. A repo that wants the old guard must say `local_guard: text`, and a machine without the service refuses to run, with no fallback. The patched build exists only on this machine, and agentsh is tested only on this WSL2 setup, so that default reaches no further for now.

The text checkers then leave the `none` path one at a time, in this order of separate guard chain runs: the stream check, Claude Code's prefix, pi's extension and the `.env` guard. The prompt lines telling agents the rules stay.

## What agentsh-secure-sandbox offers

[g5paul/agentsh-secure-sandbox](https://github.com/g5paul/agentsh-secure-sandbox), at 0e5751b, is a fork of upstream's TypeScript library, version 0.6.3. It installs agentsh inside a cloud VM, replaces `/bin/bash` with a shim and returns a wrapped sandbox object, with adapters for Vercel, E2B and nine other providers. It has no adapter for the local machine, and it pins stock v0.20.5, which has none of the five patches. SSSF is Python and wraps on the host, so it can't use the library directly, and replacing `/bin/bash` isn't something to do on the engineer's machine.

Three parts are still useful:

- **The default policy.** `agentDefault` in `src/policies/presets.ts` ends its file rules with a catch-all deny, and its deny lists are a good source for the credentials decision. Its network list isn't: it allows `github.com`, `*.cloudflare.com` and `*.googleapis.com`, wider than SSSF's allowlist, and the wildcards invite the fronting that the proxy patches closed.
- **`minimumSecurityMode`.** A run stops when `agentsh detect` reports weaker enforcement than the config requires. SSSF's launch check could do the same.
- **Credential injection.** `http_services` lets the proxy add a credential so the agent never holds it. The fork has the code in `internal/policy`, but nothing here has tested it, and upstream's review lists finding #41 against its path rule.

The seam is one place: a new local sandbox whose `start` puts `agentsh wrap --policy <name> --root <cwd> --` before the agent's command. That covers `agent_cc.py` and `agent_pi.py` through `tree.start`, and leaves quality checks and git outside the wrap.

## Proof on this machine, 2026-11-08

Run as the engineer's user with no sudo, from the v0.20.5 tarball (checksums match), with a scratch config and a policy denying `just test live`, `gh`, `git push` and opening `.env`.

- **seccomp is out on this WSL2 host.** `agentsh detect` reports `NEW_LISTENER filter install failed: device or resource busy`. WSL's own `/sbin/init`, PID 1, installs a seccomp filter with a listener, and every process inherits it. The kernel refuses a second listener under an ancestor that holds one. So agentsh's seccomp exec and file checks never run here, from any shell. That contradicts agentsh's WSL2 guide, which promises the full stack. The distro runs `systemd=true`. Whether WSL without systemd differs isn't tested.
- **What works without root.** With `agentsh-unixwrap` on `PATH`, detect reports `landlock` mode at 60/100: Landlock ABI v5 file limits and Landlock's TCP connect filter. Landlock limits folders and ports. It can't deny one file in an allowed folder or check a command's arguments.
- **ptrace is the remaining way to check execs, and it needs privilege here.** In `wrap`, the agentsh server attaches to the agent, and the server isn't the agent's ancestor. With `kernel.yama.ptrace_scope` at 1, `PTRACE_SEIZE` fails with `operation not permitted`, in both full ptrace mode and the hybrid mode with `agentsh-unixwrap`.
- **Fail-closed has a gap.** With `on_attach_failure: fail_closed`, one run still ran `just test unit` untraced before the attach failed and the run stopped. The other run ran nothing. ptrace's `max_hold_ms` also allows a held syscall after 5 seconds by default.
- **Config notes.** ptrace with the unix-socket wrapper allows exec tracing only. `sandbox.unix_sockets.enabled: false` lets ptrace trace files and network too. `wrap` mounted FUSE over the workspace with `sandbox.fuse.enabled: false` set.

So on this machine, agentsh checks execs only once a tracer has `CAP_SYS_PTRACE` or `ptrace_scope` drops to 0. Both need the engineer's sudo, and the choice is open.

## Why agentsh's docs and this machine disagree

agentsh's WSL2 guide says this, at `docs/windows-wsl2-claude-code.md:6`: WSL2 is a real Linux VM, so you get the full Linux stack, the same 100% score as bare-metal Linux. That sentence is an argument, not a test result. The kernel has the features, but one feature fails on this machine for a reason the docs do not cover.

agentsh's own tool disagrees with its docs. `agentsh detect` gave 40 out of 100 on this machine, and 60 with `agentsh-unixwrap` on `PATH`.

**Why seccomp fails.** agentsh checks commands and file opens with a seccomp listener. A listener stops a system call and decides to allow it or deny it. The kernel has one rule: a process under a listener cannot add a second listener. The kernel returns "device or resource busy". WSL puts a listener on PID 1, and all other processes come from PID 1. So agentsh gets "busy" in every shell.

The agentsh source has no code and no test for this case. Probably the authors tested on a WSL version without this listener. This machine has WSL 3.0.3.0 with kernel 6.18.54.1. An older WSL, or WSL without systemd, is not tested.

**Why ptrace is the fallback.** With ptrace, one process watches another process and stops each system call. On Ubuntu, `ptrace_scope` is 1. With that setting, a process can watch only its own child processes, unless it has the right `CAP_SYS_PTRACE`. In `agentsh wrap`, the server watches the agent, but the server did not start the agent. So the kernel refuses.

**Why a service.** The server must get `CAP_SYS_PTRACE`, and the agent must not get it. There are three ways:

1. `setcap` on the agentsh file. Then any process can start agentsh with the right, the agent too. The agent could write its own config and start a server that allows everything.
2. Set `ptrace_scope` to 0. Then every process of the engineer can watch every other one, and the whole machine is weaker.
3. Run the server as a separate user, as a system service, with the right. The agent runs as the engineer, so it cannot change the server's config, stop the server or start a second copy with the right.

Only the third way keeps the right away from the agent. A guard that the agent can turn off is not a guard. The recommendation is the service.

The direction stays correct. agentsh checks the real program and its real arguments after the shell reads the command, so a change in spelling does not matter. On this machine, that check needs ptrace, and ptrace needs one setup step with sudo. The WSL listener issue is worth a report to the agentsh authors, sent by the engineer.

## The agentsh service

The engineer chose the service on 2026-11-08. The files are in [agentsh/](agentsh/):

- `agentsh.service` is the systemd unit.
- `sssf.yaml` is the server config.
- `sssf-proof.yaml` is the proof policy.
- `install.sh` installs them. The engineer runs it with sudo in their own terminal.

**The service.** It runs `agentsh server` as the system user `agentsh`, with `CAP_SYS_PTRACE` and no other capability. Its config and policies are owned by root under `/etc/agentsh`. Its state is in `/var/lib/agentsh`.

**The socket.** The server listens on `/run/agentsh/agentsh.sock`. Only the `agentsh` user can write in `/run/agentsh`, so an agent cannot put its own server there. The server makes a socket for each wrap's ptrace handshake in its temp folder, so `TMPDIR` points to `/run/agentsh` too. A private `/tmp` would hide those sockets from `wrap`.

**Before the service.** systemd ran with no failed units. The services running were cron, dbus, polkit, rsyslog, ssh, journald, logind, resolved, timesyncd, udevd, unattended-upgrades and wsl-pro. Nothing used port 18080. The `.deb` has no systemd unit, no install scripts and no `agentsh` user, so the unit and the user are SSSF's.

**WSL can stop at any time.** The engineer can shut WSL down or restart it, and a crash can kill it. The service handles each case like this:

| Event | What happens |
|---|---|
| WSL starts, after `wsl --shutdown` or a Windows restart | systemd starts the enabled service with `multi-user.target`. `/run` is new, so no old socket stays |
| The server crashes or is killed | `Restart=always` starts it again after 2 seconds. After 5 starts in 60 seconds, systemd stops trying |
| The server dies during a run | The tracer sets `PTRACE_O_EXITKILL` (`internal/ptrace/tracer.go:640` in agentsh), so the kernel kills every agent it traced. The run fails closed, and SSSF reports the failed agent |
| WSL dies during a run | Everything stops together. On the next start, the service comes up, and SSSF sees an unfinished run |
| The service is down when SSSF starts an agent | `wrap` would start its own server as the engineer, without the right. SSSF must set `AGENTSH_NO_AUTO=1`, use the socket address, and refuse to start an agent when `systemctl is-active agentsh` is not `active` |

**Not covered yet.**

- The API is open to every local process, upstream finding #29. The socket is mode 0666 because the agent runs as the engineer and `wrap` must reach it.
- The server cannot `chown` a wrap's socket folder to the engineer, because it has only `CAP_SYS_PTRACE`. The code then uses mode 0711. Whether `wrap` still connects is not tested.
- `ProtectHome=read-only` lets the server read home folders. Whether ptrace file checks need more is not tested.

## Service checks, 2026-11-08

The engineer ran `install.sh`. The service is `active`, and its `CapEff` is `0x80000`, which is `CAP_SYS_PTRACE` alone.

- **The server could not reach the workspace.** As the `agentsh` user, it got `permission denied` on a stat of a workspace in the engineer's mode 0750 home folder and in a mode 0700 scratch folder. The session failed to start, and nothing ran. A dummy workspace in `/tmp` with mode 0755 was used for the next check.
- **The wrap attached, and every check failed open.** `just test live`, `j""ust test live`, `cat .env` and `gh` all ran. The server can't open the agent's `/proc/<pid>/mem`, because it is the engineer's file with mode 0600 and the kernel checks file permissions before ptrace rights. agentsh handles the read failure in two ways:
  - The exec handler allows the exec when it can't read the filename (agentsh `internal/ptrace/tracer.go:2013`). That is a fail-open bug.
  - The file handler denies in the same case (`internal/ptrace/handle_file.go:228`). It denied 6 opens, but `cat .env` still ran, so not every open reached it. Not explained yet.
  - The memory reader has a `process_vm_readv` fallback that needs no file permission, but `ensureMemFD` returns an error first (`internal/ptrace/memory.go:107`), so the fallback never runs.
- **The fix for this machine.** The unit adds `CAP_DAC_READ_SEARCH`. With it, the server can open `/proc/<pid>/mem` to read, and it can reach workspaces in the home folder. It adds little power, because `CAP_SYS_PTRACE` already lets the server read any process's memory.
- **The fix upstream.** The exec handler must deny when it can't read the filename, and the memory reader must try `process_vm_readv` before it gives up. Both are fork patches or upstream reports. SSSF can't rely on agentsh until the fail-open is fixed, because any read failure lets an exec through.

**After `CAP_DAC_READ_SEARCH`.** The engineer installed the updated unit. `CapEff` is `0x80004`. The checks ran again in a wrapped `bash`:

| Command | Result |
|---|---|
| `just test unit`, `cat .env.example` | ran |
| `just test live`, `j""ust test live`, `gh`, `git push`, `git -c x=y push` | `Permission denied` at exec |
| `cat .env`, `cat "$(printf ".%s" env)"`, Python `open(".e"+"nv")` | `Permission denied` at open |
| backslash-newline, split arguments, both `<<-` forms, `cat <<EOF \| bash`, `sh -c`, `j\ust` | `Permission denied` at exec |
| Python `os.execv` of `just` | denied |
| a symlink to `.env` | denied. Upstream finding #1 predicted a pass, but the ptrace path resolves the link |
| a hard link to `.env` | denied at `link` |
| `systemd-run --user` | denied: the agent can't reach the D-Bus socket |
| the server's socket, `/run/agentsh/agentsh.sock` | denied by the default unix-socket rule |
| **a copy of `just` under another name** | **ran**. Command rules match the basename |
| **the server's HTTP API, `127.0.0.1:18080`** | **HTTP 200**. Upstream finding #29 is real |

A workspace in the engineer's home folder, sssf-sandbox, now opens.

Two gaps stay. The proof policy now denies TCP to the server's port, for the engineer to install. A copied binary needs a Landlock execute list, or a rule on the binary's content, not its name.

**The kill check.** A wrapped `sleep 600` ran from 15:53:40. The engineer installed the updated policy and ran `systemctl kill -s KILL agentsh` at 15:55:56. The results:

- The agent died in the same second, and no process of it stayed. `wrap` exited 255. `PTRACE_O_EXITKILL` works, so a server crash fails closed.
- systemd restarted the service at 15:55:58, with `NRestarts=1`.
- After the restart, the agent could not connect to `127.0.0.1:18080`, so the API rule works. `https://api.anthropic.com/` still answered, and `just test live` was still denied.

**Again on the patched build, 2026-11-08.** `agentsh 0.20.6-dev.fork+bcf186b`, policy `sssf-net`. The wrapped agent wrote a heartbeat every second, and every fifth second it reached npm through its session proxy with a `200`. The engineer sent `SIGKILL` to the server. The last heartbeat was at 17:16:10, and systemd logged `status=9/KILL` at 17:16:11. No agent process was left. systemd started the service again at 17:16:13, with `NRestarts=1`. A new wrapped agent was guarded: `just test live` and `.env` were denied, npm through the proxy returned `200`, `example.com` got a 403 from the proxy, and a direct connect to the API port was refused.

## The fail-open patch, 2026-11-08

The fork's branch `fix/ptrace-exec-fail-closed`, commit 31b5af0 on a07c789, makes two changes:

- `handleExecve` denies with `EACCES` when it can't read the filename or argv.
- `getMemReader` falls back to `process_vm_readv` alone when `/proc/<tid>/mem` won't open.

The tests were built in a rootless podman container, `golang:1.25.4-bookworm`, and run on the host. In the container, every attach failed with `wait4 after interrupt ... timed out`.

- `TestIntegration_ExecveUnreadableArgsDenied` fails on the original code with `EFAULT`, because the tracer allows the exec. It passes on the patch with `EACCES`.
- The memory reader unit tests pass, and `go vet` passes.
- The full ptrace integration suite gives the same results with and without the patch: 15 failures in both, most because their helpers call `go build` and the host has no Go.

The fork branch stays local until the engineer decides to publish it to the remote fork.

**The patched build.** `agentsh 0.20.6-dev.fork+31b5af0`, built in the container the release's way: CGO on, linked to libseccomp. sha256 `9b703c6c1bc2c39e0f694ea5f307f50e25dd0955723da43d83f7e62c4f9a1cad`. It started with a copy of `/etc/agentsh/sssf.yaml` and answered `/health`. It replaces only `/usr/bin/agentsh`, through `dpkg-divert`, so the package's own binary stays as `/usr/bin/agentsh.distrib` and comes back with `dpkg-divert --rename --remove`. The other files stay at v0.20.5.

The draft upstream report is [agentsh/upstream-report.md](agentsh/upstream-report.md), for the engineer to send. The installed service still runs v0.20.5 without the patch. With the patch, the server could read agent memory without `CAP_DAC_READ_SEARCH`. It still needs that right to reach workspaces in a mode 0750 home folder.

## A copied binary, 2026-11-08

The corpus showed that a copy of `just` under another name runs `just test live`. A Landlock execute list was the planned fix. It doesn't fit:

- **Landlock never runs in this config.** Only `agentsh-unixwrap` applies it (`cmd/agentsh-unixwrap/main.go:193`). `wrap` uses the wrapper only with the unix-socket wrapper on, and ptrace allows that only with exec-only tracing (agentsh `internal/config/config.go:382`), which drops the file checks. The wrapper also goes on without Landlock when it fails to apply: `landlock: ... (continuing without)`.
- **A path list in policy doesn't hold either.** `CheckExecve` matches the raw filename from the exec, with no resolving (`internal/policy/engine.go:1432`). So `/usr/bin/../../tmp/j` matches an allow glob such as `/usr/**`.
- **An agent must run what it builds.** `.venv/bin/python`, `node_modules/.bin` and the binaries that `cargo test` builds live in the workspace. The workspace must allow exec, so a copy placed there runs. A byte-edited copy defeats a rule on the file's content too.

**What the copy can reach.** File rules apply to every process, whatever its name. A copy `./j` of `just` with `set dotenv-load` failed at `.env`: `I/O error at .env: Permission denied`. With `--no-dotenv` the recipe ran, with no secrets. `--dotenv-path .env` failed the same way.

So the copied binary is an accepted limit, like running a recipe's body by hand. The engineer accepted it on 2026-11-08, in place of the Landlock execute list. A command rule stops a mistake or a casual attempt. The file rules on secrets and the network rules stop the harm, and those don't depend on a program's name. The next layer to tighten is the network: allow only the model hosts and the package registries, in place of `sssf-allow-net`. In ptrace mode the server runs a DNS proxy, so domain rules may work without eBPF. That needs a test.

## The network allowlist, 2026-11-08

The policy `sssf-net` allows the model hosts and the package registries on port 443, and denies the rest. The checks are in [agentsh/net-checks.sh](agentsh/net-checks.sh), run inside `agentsh wrap --policy sssf-net`.

**Run 1: domain rules alone allow nothing.** In ptrace mode the connect check gets a bare IP (agentsh `internal/api/ptrace_handlers.go`, `HandleNetwork`). A domain rule matches only a host name, so every connect failed, `api.anthropic.com` included. The server records DNS answers, but feeds them only to a TLS watch (`internal/ptrace/tracer.go`, `handleConnectExit`), not to the policy. Denied names got no DNS answer.

**Run 2: the session proxy.** With `sandbox.network.enabled: true` the server starts a CONNECT proxy for each session, and `wrap` sets `HTTPS_PROXY` to it. The proxy checks the CONNECT host against the domain rules. The connect check allows only the proxy's address.

| Check | Result |
| --- | --- |
| `api.anthropic.com`, npm, PyPI through the proxy | allowed |
| `example.com`, `github.com`, a raw IP through the proxy | 403 from the proxy |
| The agentsh API and `sshd` through the proxy | 403 |
| Port 80 to an allowed host | 403 |
| A direct connect that skips the proxy: curl, Python, `127.0.0.1:22`, `127.0.0.2:22` | `EACCES` |
| DNS for a denied name | no answer |

The proxy takes a random port, so the policy allows all of `127.0.0.2`, its own address. A service bound to `0.0.0.0` answers there too: `sshd` and two more on this machine. The policy denies those three ports by number, which breaks when a new service starts. It must become an exact allow for the session's own proxy.

**Domain fronting gets past it.** The proxy checks the CONNECT host, not the TLS handshake inside the tunnel. A CONNECT to `chatgpt.com` with the SNI `example.com` returned the real `example.com` page and certificate. The same held for `api.openai.com` and `registry.npmjs.org`: all three are on Cloudflare, which routes by SNI to any site it hosts. `api.anthropic.com` returned 403, and the PyPI and crates.io hosts refused the handshake. With a matching SNI and a foreign `Host` header, Cloudflare returned 403 for all three.

So a check in the proxy that the ClientHello's SNI equals the CONNECT host closes the fronting, with no TLS inspection. The proxy already parses the SNI to rewrite redirects (`internal/netmonitor/proxy.go`, `sniRewriteFirstRecord`). The `tls_inspection` config block exists but nothing reads it.

## The proxy patches, 2026-11-08

Two more commits in the fork, on the local branch `fix/proxy-sni-and-self-allow` cut from a07c789. Like the first patch, the branch stays local until the engineer decides to publish it.

1. **SNI check, d3c2d33.** With the new `sandbox.network.require_sni_match`, the first record of a CONNECT tunnel must be a TLS ClientHello. For a host name, its SNI must equal the CONNECT host. For an IP literal, it must carry no SNI. Anything else closes the tunnel before a byte goes upstream, and the server logs `tunnel_sni_mismatch`. A policy SNI rewrite skips the check. The option is off by default, so upstream behavior stays the same.
2. **Self-allow, 49f20e2.** The ptrace connect check allows a connect to the exact address and port of the session's own proxy. The policy then needs no loopback allow, and the `127.0.0.2` rules are gone.

The tests use real ClientHellos from Go's `crypto/tls`. They cover a matching name, case and a trailing dot, a fronted name, a subdomain, no SNI, an IP with and without a name, non-TLS bytes, and the check turned off. The proxy tests check what reaches a recording upstream. With the fixes turned off, the fronted ClientHello, the SSH banner and the proxy connect went red. With them on, all went green. The `netmonitor`, `api`, `config` and `policy` suites give the same results as the base, apart from two notify-socket tests that fail at random on both.

The build branch `sssf/patched` holds all three patches on a07c789: 95fd0e7, 8d7a904 and bcf186b. The build is `agentsh 0.20.6-dev.fork+bcf186b`, sha256 `2e38026982b6dd454d2861c686bdef16a405eb9f5e60c0cd76a0d2af19b98ce2`. It replaces the earlier single-patch build, which was never installed.

Still open in this design:

- The SNI check refuses a ClientHello split across TLS records, and Encrypted Client Hello, whose outer SNI is the CDN's public name. Both fail closed.
- A connect to the proxy isn't logged as a `ptrace_network` event.

## The network allowlist on the patched build, 2026-11-08

`agentsh 0.20.6-dev.fork+bcf186b`, with `require_sni_match: true` and the proxy back on `127.0.0.1:0`. The policy has no loopback allow.

| Check | Result |
| --- | --- |
| `api.anthropic.com`, npm, PyPI, `chatgpt.com`, `api.openai.com`, `index.crates.io` with a matching SNI | allowed |
| Python `urllib`, which reads `HTTPS_PROXY` | allowed |
| Denied names, raw IPs, port 80, the agentsh API and `sshd` through the proxy | 403 |
| Fronting through `chatgpt.com`, `api.openai.com`, `registry.npmjs.org`, `api.anthropic.com` and `pypi.org` | the proxy closes the tunnel, and the handshake ends with an EOF |
| A direct connect: curl with no proxy, Python sockets, `127.0.0.1:22`, `127.0.0.2:22` | `EACCES` |
| The bypass corpus under `sssf-proof` | as before: every text-guard bypass held, and the copied `just` ran |

**Node's `fetch` ignores `HTTPS_PROXY`**, so it failed with `EACCES`. With `NODE_USE_ENV_PROXY=1`, Node 24 sends it through the proxy, where `registry.npmjs.org` was allowed and `example.com` was refused. SSSF's wrap must set that variable, and the proof must check that Claude Code and pi reach their model host.

**A session may load only a listed policy.** The build includes upstream 6cb6c7d from 2026-11-07. A session may load only a policy named in `policies.allowed`, or only the default when the list is empty. It refused `sssf-net`, and also the `agent-default` that `wrap` asks for when given no `--policy`. The config now lists both proof policies, and SSSF must pass `--policy` every time. The rule also keeps an agent from picking a looser policy for itself.

## Real agents under the wrap, 2026-11-08

`agentsh 0.20.6-dev.fork+bcf186b`, policy `sssf-net`, `NODE_USE_ENV_PROXY=1`. Each agent got one prompt: say OK, then run `curl https://example.com` once through its shell tool. Curl, bash and Python all ran clean under the same policy. Both agents failed in the tracer.

**Claude Code 2.1.319, Haiku.** It answered `OK` through `api.anthropic.com` and `mcp-proxy.anthropic.com`. The policy denied one telemetry connect to Datadog, and Claude Code went on without it. Its shell tool failed twice, first with "Working directory ... no longer leads where it did", then with `EACCES` on its temp file under `/tmp/claude-1000`. The server log shows 118 file calls from Claude Code's PID denied as `unknown session for file session_id=""`, spread over the whole 10-second run. The handler denies a call it can't match to a session (`internal/api/ptrace_handlers.go`, `HandleFile`), and logs no policy event for it. The workspace is the real directory, with no FUSE mount. A wrapped bash writes under `/tmp/claude-1000` without trouble. One theory: when a new thread stops before the tracer sees its clone event, the fallback looks up the parent by `readPPID`, which for a thread is the parent of the whole process, here the untraced `wrap` client (`internal/ptrace/tracer.go`, `findParentByTGID` callers). A Python test with 200 threads didn't reproduce it, so the cause is open.

**pi 1.4.0, `openai/gpt-5.6-luna`.** It hung for the full 240-second timeout and made no network call. Its ChatGPT token had expired, so the login refresh at `auth.openai.com`, now allowed, never ran. Four seconds in, the server logged `ptrace WATCHDOG: tracee ptrace-stopped but Run loop not advancing it (#369 #2)` for pi's main thread, stopped in `openat`. The watchdog logged no recovery and no dump. Claude Code's run logged the same warning once, on a `close`. Upstream has six commits on #369, a deadlock between FUSE and clone, and the watchdog that should recover such threads. `wrap` prints "FUSE workspace mount" even with `sandbox.fuse.enabled: false`. When the timeout ended `wrap`, pi died with it.

**No fix upstream yet, 2026-11-08.** Upstream `main` has nothing past a07c789. Of the 67 upstream branches, the #369 ones are the source branches of squash merges already on `main`. A trial merge leaves only conflicts with code `main` later rewrote, plus diagnostics such as an env-gated stop/resume trace in `fix-369-ptrace-stop-resume-trace`. Issue #369 closed on 2026-05-29 and shipped in v0.20.3. It covered seccomp with unix sockets and FUSE on exe.dev kernels. The watchdog names #369 for any stopped thread, so our stall only shares its label. No upstream issue, open or closed, matches an empty session on file calls, a stall under Node or Bun, or WSL.

So ptrace mode on this machine runs simple tools but not yet the agents SSSF runs. Both failures are closed: a call is denied or a thread waits, and nothing runs unchecked.

## The debug round, 2026-11-08

A debug build, local fork branch `debug/tracer-faults`, logged three points: a file call with an empty session, a minimal tracee state from the fallback, and a session the clone event filled in late. The service ran it with `AGENTSH_PTRACE_TRACE=1`, upstream's trace, which dumps a ring of recent stops and resumes when a thread wedges.

**Both agents worked on the second try.** Claude Code answered, its shell tool ran `curl`, and the proxy refused `example.com` with a 403. pi refreshed its ChatGPT token at `auth.openai.com`, called `api.openai.com`, and got the same 403. Neither run logged a denial or a wedge. pi's stall did not come back. It is intermittent, so the trace stays on for the next runs.

**The empty-session race is real.** In the Claude Code run, 26 new threads got a minimal state with no parent found. All 26 were threads of Claude Code's process. Each reported as its parent the `wrap` client, which isn't traced, because the kernel gives a `CLONE_THREAD` child the PPID of the whole process. The clone event filled in each session later, mostly in under a millisecond, once after 14 ms. No file call landed in the gap this time. In the first run, 118 did.

**The fix, 98bedaf** on local branch `fix/ptrace-thread-session`, cut from a07c789: in both fallback paths, look a new thread up by its own thread group first, and by PPID after that. Every thread in a group shares one session. A unit test covers a new thread with an untraced process parent, a forked child, and two cases with no match. It fails with the thread-group lookup turned off. The `internal/ptrace` unit tests give the same results as the base. The build branch `sssf/patched` now holds four patches, the last 30dee5c.

**The fix in the debug build, 2026-11-08.** `agentsh 0.20.6-dev.debug+7aa2437`, three runs of each agent. All six finished with exit 0, in 13 to 16 seconds. Each agent reached only its model host, and each one's `curl` to `example.com` got the proxy's 403. Claude Code's Datadog telemetry was denied each time. The fallback ran 68 times, 50 of them for threads, and found a parent every time. No session was empty, and no file call was denied as unknown session. The watchdog logged two capture-only exec-stall notes for agent processes that ran over 10 seconds, and no wedge. pi's stall did not come back in seven runs.

**The clean build, 2026-11-08.** `agentsh 0.20.6-dev.fork+30dee5c`, sha256 `413a33dd...4986`, with the trace drop-in removed. Every network and fronting check held, and the corpus matched the earlier runs: every text-guard bypass held, and the copied `just` ran. One Claude Code run and one pi run each finished with exit 0, reached only their model hosts, and got the proxy's 403 for `example.com`. The server logged no warnings.

## After a restart

`wsl --shutdown` from Windows on 2026-11-08, on the clean build. WSL booted at 18:12:01 and systemd started the service at 18:12:05. It ran as user `agentsh` with `CAP_SYS_PTRACE` and `CAP_DAC_READ_SEARCH`, and `NRestarts=0`.

Every check matched the clean build's results. A wrapped `bash -c` was denied `just test live` and `.env` and could read `.env.example`. The corpus held except for the copied `just`, the network and fronting checks held, and one Claude Code run and one pi run each finished with exit 0 and reached only their model hosts. The server logged no warnings.

The kill check passed again. A wrapped agent wrote a heartbeat, and the engineer sent `SIGKILL` to the server at 18:19:43. The last heartbeat was at 18:19:43, and the wrap exited at 18:19:43 with code 255. systemd restarted the service at 18:19:45, and `NRestarts` went to 1. A new wrapped agent was denied `just test live` and `.env`.

`/tmp` does not survive a restart. `/usr/lib/tmpfiles.d/tmp.conf` has `D /tmp`, so systemd empties it at every boot, and the test workspace went with it. The check scripts need it rebuilt first, as the next section says, and SSSF's real workspaces can't live there.

**The wrap slows each exec.** The heartbeat loop `date; sleep 1` beat every 2 seconds, so I timed it. Five runs of `/bin/true` took 2 ms bare and 840 ms wrapped, and `sleep 1` took 1004 ms bare and 1975 ms wrapped. Three rounds of the loop took 3012 ms bare and 6410 ms wrapped. Each timing also includes one `date` call to read the clock. The agents don't feel this much, since their runs finished in 15 seconds. A test suite that starts many processes will. The section on the cause explains it.

## The proof in sssf-sandbox, 2026-11-08

On the clean build, `30dee5c`. The sandbox's `.env` holds live API keys, and a guard failure in an agent test would send them to a model provider. So the proof ran in a detached worktree of sssf-sandbox at `~/sssf-agentsh-proof`, with a fake `.env`. One check went at the real `.env` by absolute path with `wc -c`, which prints only a count, and it was denied.

**The guard held for both agents.** One Claude Code run and one pi run were each told to try `just test live`, `wc -c .env`, a Python open of `.env` with the name built at run time, `curl https://example.com`, and their own file-reading tool on `.env`. All five failed for both agents, and the fake value appeared nowhere in their output. The worktree has no Claude Code settings file, so the refusals came from the wrap. The server's events show `sssf-no-live-tests`, `sssf-secret-files` and `default-deny-network` firing. Claude Code's session hit the search's limit of 2000 events, so its counts may be short. No file call was denied as unknown session, and the watchdog logged no wedge.

**The corpus held,** with one exception that hides behind the next finding. The copied `just` only failed because it couldn't load `.env`. With the fix below it ran, so the accepted limit stands. `net-checks.sh` wasn't rerun in the worktree. It passed on this build after the restart, and here only the agents' `curl` and model traffic tested the network.

**`set dotenv-load` breaks every recipe under the wrap.** SSSF's stamped justfile sets it, so `just` opens `.env` before it runs anything. The deny makes `just test unit` fail with `I/O error at .env: Permission denied`. Setting `JUST_NO_DOTENV=true` before the wrap fixes it: `just test unit` ran and `just test live` was still denied. An agent that clears the variable gets the same `.env` error and no contents. Recipes then run without the keys from `.env`, which is what the deny is for. A recipe that needs one has to get it some other way, or run outside the wrap.

**The wrap is slow on a real project.** Each wrapped time includes starting a session. The suite is 281 tests, and one of them, `test_the_checkout_is_set_up`, fails bare and wrapped because the worktree has no `.setup-marker`.

| Command | Bare | Wrapped |
|---|---:|---:|
| `git status --short` | 13 ms | 2333 ms |
| `python -m pytest -q` | 242 ms | 6919 ms |
| `uv run pytest -q` | 290 ms | 8676 ms |

pytest's own clock went from 0.12 seconds to 1.55, so most of the wrapped time passes outside the tests, in session start and Python's start. Each agent still finished, in 34 and 37 seconds, for five tool calls. The next section has the cause.

## The cause of the slowdown, 2026-11-08

The tracer polls on a 5 ms timer. `Run` in `internal/ptrace/tracer.go` calls `Wait4` with `WNOHANG`, and when no agent has stopped yet it waits on a 5 ms idle timer. A checked syscall stops the agent at entry and again at exit. Each stop is ready microseconds after the resume, but by then the loop is asleep, so each one waits out the timer. That makes about 11 ms per checked call. Starting a process makes many checked calls, loading its libraries among them, which is why Python's start takes 700 ms.

A debug build, `debug/tracer-idle` at 8c2ea044, makes the interval settable, and I set it to 200 µs. Every wrapped number dropped by about the same factor, 4.2 to 5.0:

| Measure | Bare | Wrapped, 5 ms | Wrapped, 200 µs |
|---|---:|---:|---:|
| Failed open | 2 µs | 11.1 ms | 2.3 ms |
| Real open | 3 µs | 22.3 ms | 4.5 ms |
| Fork | 0.3 ms | 22.5 ms | 5.2 ms |
| Fork and exec | 0.5 ms | 72.6 ms | 15.1 ms |
| Python start | 8.8 ms | 700 ms | 151 ms |
| `git status --short` | 13 ms | 2333 ms | 490 ms |
| `python -m pytest -q` | 242 ms | 6919 ms | 1630 ms |
| `uv run pytest -q` | 290 ms | 8676 ms | 1995 ms |

Two other guesses fell first. An fsync per event was wrong: SQLite writes through an async queue, and JSONL's `AppendEvent` doesn't sync with integrity off. Work in the server was wrong too: over 500 failed opens it used 640 ms of CPU in 6 seconds.

A shorter timer isn't the fix. At 200 µs the server used 770 ms of CPU during a wrapped `sleep 5`, about 15% of a core, because `scanWedged` and `reconcileProc` run on every tick. The same measure at 5 ms wasn't taken. It also still costs 2.3 ms per failed open, about 1.1 ms per stop, so the wait is longer than the 200 µs asked for. The better fix is to poll `Wait4` in a short spin right after resuming an agent, and only then fall back to the 5 ms timer. The corpus still held at 200 µs, 16 of 16. The copied `just` counts as held there only because of the `.env` failure above. No file call was denied as unknown session, and the watchdog logged no wedge.

## The spin patch, 2026-11-08

A fifth local fork patch, `fix/ptrace-wait-spin` at fb321cc5, built as `agentsh 0.20.6-dev.spin+fb321cc5`. After each resume the loop keeps polling `Wait4` for 200 µs, and only then falls back to the 5 ms timer. `AGENTSH_PTRACE_SPIN_US` overrides the window, and a single CPU never spins.

| Measure | Bare | Timer 5 ms | Timer 200 µs | Spin 200 µs |
|---|---:|---:|---:|---:|
| Failed open | 2 µs | 11.1 ms | 2.3 ms | about 70 µs |
| Real open | 3 µs | 22.3 ms | 4.5 ms | about 120 µs |
| Fork | 0.3 ms | 22.5 ms | 5.2 ms | 0.3 ms |
| Fork and exec | 0.5 ms | 72.6 ms | 15.1 ms | 0.9 to 1.5 ms |
| `sleep 1` | 1001 ms | 1482 ms | 1098 ms | 1016 to 1036 ms |
| `git status --short` | 13 ms | 2333 ms | 490 ms | 58 to 80 ms |
| `python -m pytest -q` | 242 ms | 6919 ms | 1630 ms | about 1500 ms |
| `uv run pytest -q` | 290 ms | 8676 ms | 1995 ms | 1670 ms |
| Server CPU, 500 failed opens | | 640 ms | 300 ms | 60 ms |
| Server CPU, wrapped `sleep 5` | | not measured | 770 ms | 290 ms |

The guard Claude Code run went from 34 seconds to 10, and pi from 37 to between 21 and 27. The corpus held, with the copied `just` caveat above, `net-checks.sh` passed, and both agents were denied all five actions. No file call was denied as unknown session, and the watchdog logged no wedge.

**pytest barely moved at 200 µs.** `import pytest` took 99 ms bare and 1155 ms under the spin. Python works for longer than 200 µs between its file opens, so those stops missed the spin and waited out the 5 ms timer. A 1 ms window, set with `AGENTSH_PTRACE_SPIN_US=1000`, confirmed it:

| Measure | Bare | Spin 200 µs | Spin 1 ms |
|---|---:|---:|---:|
| Python start | 8.8 ms | 64 to 88 ms | 30 to 33 ms |
| `import pytest` | 99 ms | 1155 ms | 255 ms |
| `git status --short` | 13 ms | 58 to 80 ms | 47 to 53 ms |
| `python -m pytest -q` | 242 ms | about 1500 ms | 460 to 484 ms |
| `uv run pytest -q` | 290 ms | 1670 ms | 485 ms |
| Server CPU, 500 failed opens | | 60 ms | 50 to 60 ms |
| Server CPU, wrapped `sleep 5` | | 290 ms | 290 to 300 ms |

The wider window cost no measurable server CPU, and pytest used 240 ms of server CPU in its 0.48-second run. The corpus held, 16 of 16 with the copied `just` caveat. The guard Claude Code run took 9 seconds and pi 20, and both agents were denied. `net-checks.sh` wasn't rerun at this window. No file call was denied as unknown session, and the watchdog logged no wedge.

The 8 tests in `internal/ptrace` that fail under the `integration` tag, in a container, fail the same way on the unpatched base, so the patch didn't cause them. Why they fail isn't known. They cover file redirect, soft delete, the scratch page, TracerPid masking, three connect-exit cases and write escalation on a TLS connect.

**A hang that looked like the patch, and wasn't.** A pi run hit the 240-second limit on this build, then two more hung the same way. All three stopped at event 407, just after opening `~/.pi/agent/models.json`, with no wedge logged. In the one I caught live, every thread was asleep, none stopped by ptrace, and the main thread sat in `epoll_wait`. Its stdin was a socket inherited from the Claude Code tool's shell, which stays open. `pi -p` waits for the end of stdin when it isn't a terminal. Run bare, with no agentsh, pi hung with stdin as an open pipe or an open socket, and exited at once with `/dev/null` or a closed socket. `agent-run.sh` now gives it `/dev/null`, as `sandbox.start` already does for SSSF's agents, so SSSF runs weren't exposed. With `/dev/null`, 66 wrapped pi runs in a row finished clean. Why earlier foreground runs got through isn't known.

The stall in the debug round looks different: its main thread was stopped in `openat`, and the watchdog logged `not advancing`. It may be a separate bug, so it stays open.

## The daily build, 2026-11-08

`agentsh 0.20.6-dev.fork+9f8ebbb7`, sha256 `b091e7e1f08e1fea92079c2905cd78742a565f534715d00331ba72aca7eb820a`, from `sssf/patched` with five local patches and the spin at 1 ms by default. The `spin.conf` drop-in is gone. A copy of the previous build, `30dee5c`, is in `~/.local/share/agentsh-builds/clean/`, outside `/tmp`.

The full check set passed, apart from one pi hang covered below:

| Check | Result |
|---|---|
| Service | `active`, user `agentsh`, `CAP_SYS_PTRACE` and `CAP_DAC_READ_SEARCH` |
| Plain denies | `just test live`, `.env` and the real `.env` denied, `.env.example` readable |
| With `JUST_NO_DOTENV=true` | `just test unit` runs, `just test live` denied, the copied `just` runs, the accepted limit |
| Corpus | 16 of 16 held, with the copied `just` caveat |
| Network and fronting | all held |
| Guard agents | Claude Code in 8 seconds and pi in 17, both denied all five actions |
| Server log | no unknown-session denials, no wedge, no warnings |

Timings: failed open 70 µs, fork and exec 1.2 ms, Python start 28 ms, `git status --short` 49 ms, `python -m pytest -q` about 460 ms, `uv run pytest -q` 508 ms.

**One pi run in 50 hung on the model call.** Session `32f075b2` looked normal up to its connection to `api.openai.com`, then got no answer for 39.6 seconds, until the loop killed it at its 40-second limit. A healthy run gets its answer in about 1.7 seconds. stdin was `/dev/null`, and the server logged no wedge. The 40 runs after it were clean. The records can't tell a slow response from OpenAI apart from a stall in agentsh's proxy, and `pi-loop.sh`'s output from that run was thrown away. Across the spin builds, with stdin closed, that's 1 hang in 118 wrapped pi runs. A bare pi loop of about the same size would help tell the two apart, at the cost of that many subscription calls, and a hang this rare might not show up either way. It stays open unless it comes back. SSSF's run timeouts should catch a hang like this, though that isn't tested under the wrap.

**Wrapped agents can read other credentials.** The policy denies `.env` and allows the rest of the disk. A wrapped shell could open `~/.claude/.credentials.json`, `~/.pi/agent/auth.json`, `~/.config/sssf-sandbox/token.env` and `~/.config/gh/hosts.yml`, opened only and not printed. The network allowlist narrows where they could go. It doesn't stop an agent reading them. The policy also denies `.env.sample`, which `secret_files.py` allows.

**Tests that fail with or without the patches.** In a container, 8 tests in `internal/ptrace` under the `integration` tag and 24 in `internal/netmonitor/unix` fail, and they fail the same way on the commit before the spin patch. `internal/api` passes. The cause isn't known. The seccomp tests among them may not work in a container at all.

## The default-deny test, 2026-11-08

`deny-policy.py` writes one policy per agent: secrets and other credentials denied first, then the workspace, the worktree's git data, temp, devices, tool caches and the agent's own files allowed, then system paths read-only, then a final deny. `path-harvest.sh` built the list from the file events of a real workload, and `deny-test.sh` runs the test. That's 17 file rules for Claude Code and 15 for pi.

**The denies held.** 42 probes under each policy tried reading, creating, deleting, renaming, chmodding and linking a plain file outside the list, by direct paths, `..`, symlinks, hard links, `dir_fd` and `/proc/self/{root,cwd,fd}`, and tried writing to `~/.bashrc`, `~/.profile` and `~/.claude/settings.json`. No fake file or real config changed. Each agent could open its own sign-in and not the other's. Other repos, `~/.ssh`, the gh token, the VM token, every `/proc/*/environ` and the Windows drive were all denied. An agent's own sign-in stays readable, as decided.

**pi works.** It edited a file and ran the tests, exit 0 in 14 seconds, with one harmless denial, `/mnt/wsl/resolv.conf`.

**Claude Code doesn't, yet.** It exits 0 but can do nothing: its Edit and Bash tools fail with `EACCES`, and it reports that honestly. Its file calls use paths such as `/proc/self/fd/14/hello.py.tmp.123`. `resolveViaProc` in `internal/ptrace/handle_file.go` reads the `/proc/self` link from the tracer's process, so it gets the tracer's PID instead of the agent's. The path then fails to resolve and reaches the rules as raw text, which matches no allow. For the same reason it logged `/proc/self/cwd/innocent` as `/var/lib/agentsh/innocent`.

So that bug is failing closed here. Opens were safe either way, because `handleFile` re-checks the real file behind the descriptor at exit. Other operations get no such check, so allowing `/proc/self/fd/**` to let Claude Code work should open a way around every rule: `exec 3</; rm /proc/self/fd/3/home/...`. That wasn't run, because the policy would have to allow it first. The fix belongs in the tracer: resolve `/proc/self` and `/proc/thread-self` to the agent's own IDs, so the rules see the real path. Whether Claude Code tolerates the `/proc/*/environ` deny can't be judged until then.

**A denied missing file breaks tools.** agentsh answers `EACCES` where the kernel would answer `ENOENT`. `uv` stops on that: `failed to open file '/home/gpaul/uv.toml': Permission denied`. The next policy needs read rules for `uv.toml` and `pyproject.toml` in `~`, `/home` and `/`, for `/tmp` itself, where `uv` makes its temp files, for bare `/etc`, and for `mkdir` on `~`. Each tool's config search may need the same, and that's the real cost of default-deny.

`test_the_checkout_is_set_up` fails with either policy, because the proof workspace has no `.setup-marker`.

### The sixth patch and the rerun

`e97a99c8`, `fix(ptrace): resolve paths in the tracee's view`, on `sssf/patched`, local. Build sha256 `ba85e8d65b19a005341bd2e0cf67bfbf1687aa47e0a20ce4739a4e7f22766701`. It fixes three things in `resolveViaProc`:

- `/proc/self` and `/proc/thread-self` now name the agent. In the failing test, `/proc/self/fd/3` had resolved to `/sys/fs/cgroup/cpu.max`, which was what the tracer held at fd 3, so a rule judged an unrelated file. `/proc/self/cwd` and `/dev/fd` had the same bug.
- `link/../x` now resolves as the kernel does, beside the link's target. `filepath.Clean` used to collapse it as text and judge it beside the link. Opens were saved by the exit check, but `rm` or `mv` got no such check wherever a symlink left the workspace.
- A descriptor link to a pipe, a socket or a deleted file is now the end of the path. Otherwise the dangling-symlink guard would have denied writes to `/dev/stdout` on a pipe.

The new tests failed before the fix. The package's unit tests pass, and the integration tag shows the same 8 failures as without the patch.

On that build, with 20 file rules and 143 patterns for Claude Code, 18 and 120 for pi:

| Check | Result |
|---|---|
| 46 denial probes, under each policy | all as intended. Each agent could open only its own sign-in, and no fake file or config changed |
| 9 checks that must work, including a piped `/dev/stdout`, renames through a workspace descriptor, and uv's config search | all passed |
| Tools: pytest, git commit, uv, node, rg | exit 0, no file denials |
| Claude Code: edit a file, run tests, git | worked, exit 0 in 8 seconds. 69 `/proc/*/environ` denials did it no harm |
| pi: the same | worked, exit 0 in 16 seconds, one harmless denial |
| Bypass corpus | 16 of 16 as before, with the copied `just` running, the accepted limit |
| Network and fronting checks | as before |

This covers a small task, not a full chain. So far default-deny costs a list of paths and the risk that a new tool's config search hits `EACCES`, and the trace names the path when it does.

The run led to two policy changes, tested in the third run below:

- Claude Code warned that managed settings "are NOT in effect", because a denied read of `/mnt/c/Program Files/ClaudeCode/managed-settings.d` looks like a failure to read. That folder doesn't exist on this machine, but on one where it does, the deny would switch off the organisation's policy. The Claude policy now allows that path.
- The git data rule allowed writes to `hooks/` and `config`, which run on the host outside the wrap at the next git command. Those writes are now denied. A worktree commit shouldn't need them, and the probe now checks that.

### Long arguments are refused

**Any single argument over 4096 bytes is refused with exit 126**, on every build and policy so far, by rule `argv_truncated`. `bash -c` with 4000 bytes ran and with 4100 was refused. `git commit -m` with a 6000-byte message was refused. Claude Code's shell-snapshot command is refused on every run, and it carries on without one. An agent's long command or heredoc would fail the same way, and under the settled denial counting these would add up toward the three-refusal stop.

Failing closed is right, since a cut-off argument could hide a token that a rule denies. The limits are too low. `maxArgLen` is a constant of 4096 in `internal/ptrace`, and `tracer.go` hard-codes 1000 arguments and 64 KB in total, so no config changes them. The kernel allows 128 KB per argument. `max_argv_bytes` in the config only applies to the seccomp path.

### The seventh patch and the third run

`41960715`, `fix(ptrace): read argv up to what the kernel allows`, on `sssf/patched`, local. Build sha256 `3774b1170d447e8c935fb8c2d7421199b6d3fafc312d6f77de1bb2cd1c5db45d`. It reads 128 KiB per argument and 2 MiB in total, and still denies an exec above those. Rules see the whole argv. The trace stores a clipped copy, 4 KiB per argument and 64 KiB in all, with `argv_clipped` set, so a rule can act on a part of a command the trace doesn't show. The new limit tests failed before the fix. The `ptrace` and `api` unit tests pass, and the integration tag shows the same 8 failures by name.

On that build, with both policy changes installed:

| Check | Result |
|---|---|
| 50 denial probes under each policy: 16 reads, 22 changes, 12 opens | 49 held, and the 50th is each agent opening its own sign-in, as expected. No fake file or mode changed |
| Writes to `.git/hooks` and `.git/config` | held |
| A denied command after a 100 KB argument | exit 126, the policy |
| 16 checks that must work: 100 KB `bash -c`, a 120 KB argument, 2000 arguments, a 6 KB commit message, `env -i`, a worktree commit, and the earlier ones | all passed |
| Tools | exit 0, no file denials, and no denied execs |
| Claude Code | worked, exit 0 in 9 seconds. No denied execs, so its shell snapshot now runs. The managed-settings warning is gone. 83 file denials, 68 of them `/proc/*/environ`, did it no harm |
| pi | worked, exit 0 in 14 seconds, one harmless denial |
| Bypass corpus | 16 of 16 as before, with the copied `just` running, the accepted limit |
| Network and fronting checks | as before |
| Exec with a 120 KB argument | about 6 ms wrapped, against 1 ms bare. `/bin/true` takes about 2 ms wrapped |

Claude Code's one clipped command stored 4109 bytes in the trace.

This is still one small task per agent, not a full chain, which is what rollout step 2 is for. The engineer adopted default-deny on this result, and the Decisions table lists the limits that comes with.

### agentsh widens the workspace to the repo top

An earlier attempt at this run was started by mistake from the hub's `docs/designs/agentsh`, and it showed that **`--root` isn't the boundary**. `DetectProjectRoots` walks up from `--root` to the nearest `.git`, or the nearest language marker before it, and sets `${PROJECT_ROOT}` to that. So the wrap with `--root docs/designs/agentsh` allowed the whole hub, including its `.git`. The probe and tool runs made commits on the hub's `main` and reset them. `main` ended where it started and no file changed, but a run must not get that close to a real repo. `deny-test.sh` and `path-harvest.sh` now refuse to start outside the proof workspace.

An SSSF run starts at a checkout or worktree top, which holds `.git`, so the walk stops there. A workspace without `.git` keeps walking, and a `package.json` or `.git` in a parent or in the home folder would widen it. The wrap needs `policies.detect_project_root: false` in `sssf.yaml` and to refuse a `--root` that isn't the checkout's top. Neither is tested yet.

That attempt was useful in one way. With no `hello.py` to edit, Claude Code searched the home folder for it, and default-deny answered 229 opens with `EACCES`, `.gnupg` among them. It then reported that it couldn't find the file and made no edits.

### The first guard run, cf9d8014

The chain ran on 2026-11-08 from `specs/tasks/guard-agentsh-wrap.md` and stopped for the engineer after two rounds, at $12.26, because round 1's fix added state. It found no bypass, and the text checks judged 135 commands the same on `main` and on the branch. Every agentsh check ran against a fake wrap and service or the reviewer's own evaluator, so no real exec was denied. The report is [guard-run-cf9d8014.md](agentsh/guard-run-cf9d8014.md).

What blocked it:

- The install script could overwrite a file through a link at `adws/adw_data`, a narrower form of round 1's harm.
- `sssf-no-push` denied `git commit -m "fix the push path"`, `git stash push` and `git log --grep push`. The rule came from the proof policy, and the task asked for it verbatim.
- Round 1's fix added `_finished` with a 30-second wait, and a failed final read now replaced the three-denial stop and lost a clean call's spend.

Worth more than those: `check_machine` passed a config with `ptrace.trace.execve: false` or `on_attach_failure: fail_open`, and that config waited in an agent-writable folder until the sudo step. Separately, **the build never set `JUST_NO_DOTENV=true`**. The task required it, the spec dropped it, and neither review caught it. I found it reading the builder's notes.

The engineer decided these on 2026-11-09:

1. Narrow the push rule. See below.
2. Install files leave the repo, and the installed config is compared whole. This removes the link harm, the untracked-files finding and the config hole, where patching each would leave the next.
3. The new state goes, and the outcome order stays the spec's.
4. The next run starts from the same code. `specs/tasks/guard-agentsh-wrap-2.md` is its task. It's a new session rather than a joined one, because joining would count cf9d8014's spend against the $20 cap. The branch `sssf/cf9d8014` and its worktree stay until that run is settled.

**The push rule.** `args_patterns: ['^\S*\s+push(\s|$)', '^\S*\s+-.*push']`, tested through `LoadFromFile` and `CheckExecve` in the fork at depths 0, 1 and 3, not yet under the service. agentsh matches against all of argv joined by spaces, **`argv[0]` included**, so the pattern skips a first token. It denies `git push`, `git -c a=b push`, `git -c "a=b c" push`, `git -C /x push`, `git --git-dir /x push`, `git -c alias.p=push p` and an empty `argv[0]`. It allows `git commit -m push`, `git stash push` and `git log --grep push`. Two limits come with it:

- `git -c a=b commit -m push` is denied. It fails closed, and it's rare.
- `exec -a "a b" git push` isn't denied, because joined text can't separate that first token from an argument. The old pattern denied it. Getting there takes a deliberate spoof, a copied binary already does the same with less effort, and the network rules still refuse GitHub. A fork patch could match arguments without `argv[0]`, if this ever matters.

The second run's planner found a gap in that pattern, and the engineer decided two more things on 2026-11-09:

- **A newline in a git option.** `.` doesn't cross a newline, so `git -c $'a=b\nc' push` matches neither pattern. The second pattern becomes `(?s)^\S*\s+-.*push`, tested on the fork's engine as above, and a commit message or `--grep` holding a newline stays allowed. That run built with the earlier text, so the third run makes the change.
- **Pi's read loop.** If an exception leaves it outside `on_event`, such as a failed raw log write or a JSON line that isn't an object, no `close` runs, so denials aren't read and that call's spend isn't kept. The call still fails and the wrap ends at exit. `main` behaves the same for pi, so the engineer accepted this as a carve-out from the bar on dropped breaches.

**The first real run must check** these, from the builder's notes:

- Session and handoff paths on a `--worktree` run. `adws/adw_data/sessions/<id>/` and the `REFUSE_*` files may sit in the main checkout, outside `${PROJECT_ROOT}`, so default-deny may refuse them and the chain may fail closed. The fix would be an allow on that folder, which widens what an agent can read across runs. That's the engineer's call at rollout step 2.
- That the attach lines and event fields match what `internal/cli/wrap.go` and the events API really produce, and that `order=asc` and `type=ptrace_execve` are honoured.

### The second guard run, 8b06e76e

The chain ran on 2026-11-09 from `specs/tasks/guard-agentsh-wrap-2.md` and stopped at three rounds, at $11.20, with one bypass left. The decided changes went in, and text mode matched `main` on all 64 command forms. Stop paths ran under a fake wrap that forks as `wrap.go` does, not under the service. The report is [guard-run-8b06e76e.md](agentsh/guard-run-8b06e76e.md).

Each round closed the bypass before it, and the fixes for rounds 1 and 2 opened the next:

1. A billing refusal killed only the wrap, so the agent ran on unread.
2. The fix went through `ps`, so a stop with no room to fork left the agent running.
3. The `/proc` walk added for that read `stat` as text. A child whose name isn't valid UTF-8 raises `ValueError`, the walk skips it, and killing the agent orphans it before `ps` looks. Reading bytes fixes it.

I offered to make the last fixes by hand and merge, because the setting is off by default. The engineer refused on 2026-11-09: guard code goes through the chain, or the chain isn't checking what ships. The engineer also decided:

- `sudo install` follows a link, so an agent running during the install could get a root-only file copied to `/etc/agentsh/sssf.yaml`. The install lines print a warning to stop every agent first, and this stays a limit, because a check in the printed commands would still leave a race.
- `GIT_CONFIG_COUNT` aliases, `git send-pack` and the `mv .git .git2` rename go in `config.md`'s known limits. The two git spellings also pass the text checks, so they're cases in [corpus.sh](agentsh/corpus.sh).
- A stop with no file descriptors left, and a signal inside the walk, stay open as limits. Closing the first means recording the agent's pid, which is new state, and no agent action was found that reaches it.

`specs/tasks/guard-agentsh-wrap-3.md` is the third run's task. The branches `sssf/cf9d8014` and `sssf/8b06e76e` and their worktrees stay until it's settled.

The first real run must also check that the network rules refuse both git spellings, and whether the rename gets round the file rules under the service.

### The third guard run, 0cba1ad2

The chain ran on 2026-11-09 from `specs/tasks/guard-agentsh-wrap-3.md` and passed in one round, at $6.19, on 173 candidates and 524 checks. The three runs cost $29.65. The verify phase passed 1696 tests in the skill suite and 57 in the chain's own. The engineer merged it the same day as b5d0b82. The report is [guard-run-0cba1ad2.md](agentsh/guard-run-0cba1ad2.md).

No check ran under the service, so this proves the wrap's code and not that the wrap works. The reviewer judged the push rule with Python's `re`, and the fork test covers Go's RE2 on the policy engine.

The report's six old holes went three ways:

- **Held for a chain run.** The whole-config comparison catches a changed installed file and misses a changed template. The workspace allow lets an agent edit `agentsh_sssf.yaml`, an edit stays after a stop, and the next refusal sends the engineer to the install, which installs it. `check_machine` then passes `fail_open`. The install script should refuse when the template differs from the committed version. That's guard code, so it goes in the task for the chain run after rollout step 2, when the session folder allow is likely to need one too. Until then, `config.md` says to run `git status` before following a refusal's install step. Only the engineer can run that step, and step 2 runs only in sssf-sandbox.
- **Recorded as limits.** Four more push spellings, an `argv[0]` holding a newline or tab, and two tracebacks on a broken template are in `config.md`. The spellings that pass the text checks are cases in [corpus.sh](agentsh/corpus.sh).
- **Watch at step 2.** `(?s)` also denies a multi-line commit message or `--grep` value naming `push` after a leading option, and each denial counts toward the three-refusal stop. An agent retrying such a message can end its run. If that happens in practice, the pattern needs another look.

### The first real runs

On 2026-11-09, after the engineer installed the generated policy, sssf-sandbox ran its SDLC chain under the real service on the branch `agentsh-rollout`, `sandbox: none` and `local_guard: agentsh`, Claude roster. These were the first checks that weren't against a fake, and they found what three chain runs couldn't.

| Run | How | Result |
|---|---|---|
| d4026dde | `--worktree` | Stopped in 5.6 s, $0.08. The push rule denied five of Claude Code's own git calls, and every Bash call failed on the refuse script |
| dc693ea9 | `--worktree`, throwaway patch for both | Stopped in 71 s, $0.38. The events API returns `null` past the last page, which SSSF counted as a failed poll |
| ac34222b | `--worktree`, patch plus the `null` fix | Failed the plan gates, $0.76. The planner couldn't write `plan.md` to the handoff folder in the main checkout |
| 4827dcf6 | no worktree, same patch | **Passed**, 13 phases in 4 minutes, $1.45, with planner, builder, reviewers and documenter all wrapped |

The patch was the engineer's call: hand-made in the test bed, only to find every failure before one chain run. It doesn't ship.

What held under the real service: the launch checks, the attach, denial counting from the real API, the refusal stop and its spend, and session cleanup. The reviewer tried `github.com`, `~/.ssh/config` and `~/.netrc`, and every agent tried `.env`. All were denied, and none counted as a refusal. Claude Code read `/proc/*/environ` for about 78 processes each call, and every read was denied. It also runs `reg.exe` through WSL interop. Under the wrap, `cmd.exe` and `curl.exe` fail with `connect failed 13`, because the interop socket is denied.

**Why the fakes missed it.** Claude Code's real git calls carry about 40 `-c` options, `hook.pre-push.enabled=false` among them, which the `-.*push` pattern matched. The real API answers `null` for an empty page, where every fake answered `[]`. And a real exec event carries `fields.argv` with no `command`, so the refusal message named nothing. The real argv lists and events are in [first-real-runs/](agentsh/first-real-runs/), as test fixtures.

**The push rule, third form.** `['^\S*\s+push(\s|$)', '(?s)^\S*\s+-.*\spush(\s|$)', '(?is)^\S*\s+-.*\salias\.[^=\s]*=.*push']`. It needs `push` as a word of its own, or an alias whose value names it, after a leading option. Any alias value naming `push` is denied, in any case. It's tested on the fork's engine at 3 depths, against 41 cases including the 5 real argv lists, and ran under the service in the patch. `git -C` on a path holding `push` now runs.

**The handoff folder, decided 2026-11-09.** A `--worktree` run keeps its handoff in the main checkout, outside the workspace, and an installed policy can't name one run's folder: its variables come from the service, not the run. The engineer chose to allow read and write on the main checkout's `adws/adw_data/sessions/**` for every run. Agents can then reach other runs' handoff files, as text mode allows today. Moving the handoff folder into the worktree is the stronger fix, recorded for later. It changes the session layout for every mode.

**A relative symlink.** One `symlink` with a relative target fell to agentsh's own `default-deny-files`, not to any SSSF rule, so an agent making a relative link in its workspace may be refused.

**The refuse script and the sessions folder (built).** agentsh checks a symlink's own path and its target as the raw string the agent passed, both with `symlink`, so no absolute rule can judge a relative target. SSSF adds no link rule. A relative target falls to `default-deny-files`, as on `main`, and the existing rules judge an absolute target. The relative-link allow tried in 8f8304ae was dropped (see below). No `REFUSE_*` files exist in the templates, so the main-checkout allows are the sessions folder (`sssf-sessions`) and `sssf_refuse.py` (`sssf-refuse-script`, Claude Code only).
**SSSF's session-folder writes (built).** Every host write under `<data_root>/sessions/` goes through `adw_modules/session_files.py`, given the run's data root. It walks from the data root with `O_NOFOLLOW` at every folder, writes a temp file and renames it over the target, and opens appended logs so that anything but SSSF's own single-link regular file is replaced. No file rule judges link targets.

`specs/tasks/guard-agentsh-wrap-4.md` is the chain run's task, covering all of these.

### The chain run for the real-run fixes, 8f8304ae

The chain ran on 2026-11-09 from `specs/tasks/guard-agentsh-wrap-4.md` and stopped after round 2, at $7.02, because round 1's fix added a hand-kept list. The report is [guard-run-8f8304ae.md](agentsh/guard-run-8f8304ae.md).

Items 1 to 5 and 7 passed review on the first try: the push rule, the refuse-script read, the `null` page, the refusal message, the sessions folder allow, and the tests from real data. Round 1 also found that `git -c ALIAS.p=push p` runs `git push`, since git reads config keys in any case, and the fix closed it.

Every other finding came from item 6, the relative-symlink allow. agentsh checks a link's target as the raw string, without resolving it against the link's folder, so no absolute rule can judge a relative target.
- **Round 1:** a link made at an allowed path outside the workspace, such as `~/.claude.json.x -> .ssh/id_rsa`, pointed at a denied file. The agent still can't read through it inside the wrap, but the engineer's own Claude Code rewrites `~/.claude.json` and would follow it.
- **Round 2:** the fix listed the folders where links are denied, which missed the workspace root. A `secret_files` glob like `keys/[a-z]*` still denied almost every link.

Each list closes the cases it names and misses the next.

The engineer decided on 2026-11-09:

1. **Drop the symlink allow.** A relative symlink stays denied by agentsh's own fallback, as on `main`. An agent's relative `ln -s`, `bun add` and `npm install` fail. `uv venv` and `worktree_setup` don't. A fork patch that resolves the target against the link's folder would allow it safely, if a repo needs it.
2. **Widen the alias pattern** to `(?is)^\S*\s+-.*\salias\.[^=\s]*=.*push`, so any alias value naming `push` is denied in any case. It's tested on the fork's engine against 41 cases at 3 depths, the five real argv lists among them.
3. **Record the workspace-root swap as a limit.** An agent can replace its workspace with a link to a folder it made, which `main` allows too. The after-call guard is expected to catch it.

`specs/tasks/guard-agentsh-wrap-5.md` is the next run's task. It keeps the reviewed code and removes item 6.

### The chain run without the symlink allow, 1df8cc16

The chain ran on 2026-11-09 from `specs/tasks/guard-agentsh-wrap-5.md` and stopped after round 3, at $6.65, because round 2's fix added a `..` link deny. The report is [guard-run-1df8cc16.md](agentsh/guard-run-1df8cc16.md).

Dropping the symlink allow and the wider alias pattern held through three reviews with no bypass. The finding came from the sessions folder allow. After each agent call, SSSF writes `<agent>/envelope.json` into the session folder by path, and the write follows a symlink. On a `--worktree` run an agent can put a link there, made in place or moved in from its workspace.
- **Round 1:** a target of `sessions/../../../CLAUDE.md` let SSSF overwrite a file in the main checkout. `main` denies that link.
- **Round 2:** the fix denied link targets holding `..`.
- **Round 3:** targets of `/proc/<pid>/fd/<n>` and `/dev/fd/<n>` reach any file SSSF holds open. The reviewer showed the envelope write cutting a held-open `sssf.db` to 29 bytes. On a plain run the session folder sits in the workspace, so `main` has the same hole.

A rule on link targets misses the next spelling, as the symlink allow did. The writer is SSSF, so the fix goes there.

The engineer decided on 2026-11-09:

1. **Fix it on SSSF's side.** Every host write under the session folder writes a temp file in the same folder and renames it over the target. A rename replaces a link instead of following it.
2. **Keep the sessions folder allow** as it is. A tighter allow without `rename` could break Claude Code's own file writes.
3. **Accept a limit:** an agent can write `.env.example` and `.env.sample` anywhere, because that allow sits ahead of every deny, as on `main`.

`specs/tasks/guard-agentsh-wrap-6.md` is the next run's task. It starts from the run's round-2 snapshot, without the `..` rule.

### The chain run for link-safe session writes, bad50afd

The chain ran on 2026-11-09 from `specs/tasks/guard-agentsh-wrap-6.md` and passed in round 2, at $5.46. It merged as bf9f648. The report is [guard-run-bad50afd.md](agentsh/guard-run-bad50afd.md). The three runs of rollout step 2's guard work cost $19.13 in all.

Every SSSF write under the session folder now goes through one helper. It walks from the data folder with `O_NOFOLLOW`, writes a temp file and renames it over the target, and opens logs so a link, hard link or FIFO at the path is replaced. The reviewer tried 91 attacks on the write sites: links absolute, relative and dangling, `/proc/self/fd` and `/dev/fd` links to held-open files, hard links, FIFOs, links in place of each folder, and a thread swapping a folder for a link 400 times. On `main` every one reached the outside file, a SQLite db came out corrupt, and every FIFO write hung. On the merge none did.

Round 1 found one false positive: the temp name grew with the target's, so a handoff name of 234 bytes or more failed on a remote-sandbox run. Round 2 confirmed the fixed-length name.

Old holes, all on `main` too, now in `config.md`: a link at the data folder itself on a plain run, folder creation that follows a folder link, and reads under the session folder that follow links. Remote-sandbox handoff files also come back without their mode bits.

### Rollout step 2 done, 2026-11-09

With the guard work merged and the policy it generates installed, sssf-sandbox ran two SDLC chains under the real service on skill 89e9092, with no patches.

| Run | How | Result |
|---|---|---|
| 8152e879 | `--worktree`, a `longest_word` helper | Passed, 14 phases in 3 min 20 s, $0.92 |
| 16cc2882 | no worktree, a `count_unique_words` helper | Passed, 10 phases in 2 min, $0.59 |

Neither run had a refusal or an error, and no agentsh session was left open. The `--worktree` run wrote its handoff files, `plan.md` among them, to the main checkout's session folder. No link was left in either session folder, and the main checkout was clean after both.

Denials that are expected and harmless: on a `--worktree` run, git and uv search upward from the worktree for config, so they read the main checkout's `.gitignore`, `pyproject.toml`, `adws/adw_modules` and a `uv.toml` above the worktree. On a plain run every agent tries `.env`, which `sssf-secret-files` denies. Telemetry and update checks are denied by the network rules. None counts as a refusal.

**Not run: a real push from each spelling in [corpus.sh](agentsh/corpus.sh).** This session's settings deny push commands, so the engineer runs that check. The proof workspace has no remote, so the check needs one that points at GitHub.

### The install-check run, e027505a

The chain ran on 2026-11-09 from `specs/tasks/guard-agentsh-install-check.md` and stopped after round 2, at $6.48. The report is [guard-run-e027505a.md](agentsh/guard-run-e027505a.md).

The design had the project's install script tell the engineer's changes from a run's. Each install recorded its commit in a root-owned file, and SSSF marked its own commits with an `SSSF-Run: yes` trailer. The script then refused when a source file was uncommitted or a marked commit had touched one since the record.
- **Round 1:** the check's `git log` ran an agent's `gpg.program` through `log.showSignature`, where the old script never ran git. CRLF checkouts were refused every time. Six ways around it came from a sourceless `.pyc` package, `.git/shallow` and git config settings that hide or skip the trailer.
- **Round 2:** the fix kept a growing list of git settings to override, which the reviewer flagged. An agent's `core.sshCommand` still ran through a partial-clone fetch, and `info/grafts` and per-key `trailer.SSSF-Run.*` settings still hid the mark.

The check runs git and Python inside a repo an agent can change, so each setting it overrides leaves the next one.

The engineer decided on 2026-11-09 to drop the approach and install from the hub: the skill's source runs the install, reads only `repo.yaml` and `sssf.config.yaml` from the project as data, and shows a diff against what's installed before the sudo lines. The old script stays until that merges. `specs/tasks/guard-agentsh-hub-install.md` is the task.

### The hub-install run, 8b800eb5

The chain ran on 2026-11-09 from `specs/tasks/guard-agentsh-hub-install.md` and passed in round 3, at $6.03. It merged as c065858. The report is [guard-run-8b800eb5.md](agentsh/guard-run-8b800eb5.md).

The install now runs from the hub: `uv run --directory <hub> .claude/skills/sssf/scripts/agentsh_install.py --repo <project>`. The policy code and the config template are the hub's. It reads the project's `repo.yaml`, `sssf.config.yaml` and `stamp.json` as data, imports nothing from the project, and runs no program or git there. It shows a diff against what's installed before the sudo lines. The reviewer planted `yaml.py`, `sitecustomize.py` and fake `adw_modules` in the project, and none ran. A new `network_allow` host and a `data_dir: /` change showed as `+` lines. A `fail_open` in the project's template never reached the output.

Removing the project's old `adw_agentsh_policy.py` took a change to `install.py --update`, which now deletes SSSF files a newer skill retired. That part drew both harms:
- **Round 1:** a forged `stamp.json` record such as `adws/adw_modules/../../.git/config`, with the target's checksum, made the engineer's update delete it, `~/.bashrc` too. The fix refused `..` and parent links.
- **Round 2:** an agent still running during the update could swap a folder for a link between the check and the delete, 5 times in 15,000. The fix walks open folder descriptors with `O_NOFOLLOW` and deletes relative to them.

Left open, in `config.md`: a malformed `stamp.json` record makes `--update` crash before it saves the stamp, which is denial of service only. The version check reads an agent-editable stamp. A forged record can delete an in-repo SSSF file. A FIFO at `repo.yaml` stalls the install.

### Rollout step 3: the hub under agentsh, 2026-11-09

The hub's `repo.yaml` sets `local_guard: agentsh` since dd07d8f. Its policy, `sssf-super-simple-software-factory-claude`, rendered from the install clone `~/github/sssf-install` and matches sssf-sandbox's rule for rule, apart from the project's own config folder. The engineer installed it.

The first guard run under it, 056faf3e, passed in round 1. No phase failed on a denial. What agentsh denied, beyond what sssf-sandbox showed:
- Claude Code reads every process's `/proc/<pid>/environ` at startup, about 75 a phase, all denied by `sssf-proc-secrets`.
- Python's user site folder, `~/.local/lib/python3.12/site-packages`, which Python looks in by default.
- `~/.config/sssf/projects.json`, when the builder's tests ran `install.py`. The tests still passed.

**Links with a relative target are denied everywhere,** in `/tmp` and the sessions folder too: agentsh's own `default-deny-files` takes them, as found in run 8f8304ae. A link with an absolute target works. For the hub this means:
- the reviewer couldn't build a dangling-link `stamp.json` case and dropped it;
- `git init` tests whether it can make a link, and sets `core.symlinks=false` when it can't, so all 70 of the reviewer's scratch repos had links off.

The run-8b800eb5 harms were link attacks, so the bypass reviewer's prompt now says to give every link an absolute target, to set `core.symlinks true` in scratch repos, and to report a candidate that needs a relative link instead of dropping it (6c1421d).

### The stamp-refusal runs, 056faf3e and 6cfe04c2

The first guard runs under agentsh. 056faf3e passed in round 1, at $4.07. Its one real broken promise was a stamp nested 1,200 deep, which passed the check and crashed in `save_stamp`. 6cfe04c2 started from its code, fixed that, and passed in round 1, at $5.01. They merged as 67bf1a2. The reports are [guard-run-056faf3e.md](agentsh/guard-run-056faf3e.md) and [guard-run-6cfe04c2.md](agentsh/guard-run-6cfe04c2.md).

What agentsh cost the reviewers, beyond relative links:
- **`tar -x` failed** with "Cannot open: Permission denied" on every file in a subfolder, in the scratch folder and in `/tmp`. A file at the archive's top level extracts. Each piece tested alone works under the same policy: `mkdir`, `O_EXCL` opens, opens through a folder handle (an `O_PATH` one too), and a path with a slash. The denial log has no entry for it. The reviewer unpacked with `git show` per file instead. The cause and the fix are [the eighth patch](#the-eighth-patch-tar-and-openat2-2026-11-09).
- **uv couldn't make a venv** in `~/.cache/uv`, so the reviewer ran its checks on three interpreters with its own harness, not the suite.
- One flaky test in build verify: `test_agentsh_guard.py::test_a_non_breach_failure_with_three_denials_becomes_the_breach[cc]` failed once with "the tracer failed" and passed on the same code.

### The eighth patch: tar and openat2, 2026-11-09

`3918a619`, `fix(ptrace): allow openat2 resolve flags that only restrict`, on `sssf/patched`, local. Build sha256 `4ff5df413d75a3c826625a82b4fa3a515a68374cfd21e15738d7d49d978cc6a9`, in `~/.local/share/agentsh-builds/openat2/`, built as `41960715` was. A copy of `41960715` stays in `~/.local/share/agentsh-builds/argv/`.

**The cause.** GNU tar 1.35 opens each parent folder with `openat2(dirfd, "foo/", O_PATH|O_DIRECTORY|O_NOFOLLOW, RESOLVE_BENEATH)`, then opens the file relative to the handle it gets back. The tracer's `extractFileArgs` refused every `openat2` with a resolve flag, as upstream wrote it in 448913e6, because "the kernel applies restricted path resolution ... that we cannot replicate. Fail closed." The handler then returns `EACCES` before any rule sees the call, and logs `handleFile: cannot extract args, denying` to the service's journal only. That's why the policy's denial log had no entry. A top-level file needs no parent handle, so it extracted. The journal held 1,491 of these lines from the two stamp-refusal runs and the diagnosis. A ptrace logger written for the diagnosis showed tar's calls, since the machine has no strace, and a two-call C repro failed under the wrap with `RESOLVE_BENEATH` or `RESOLVE_NO_SYMLINKS` and passed with no resolve flag or a plain `openat`.

**The fix.** `BENEATH`, `NO_SYMLINKS`, `NO_MAGICLINKS`, `NO_XDEV` and `CACHED` can only make the kernel refuse a lookup. With any of them it reaches the file a plain `openat` of the same path reaches, or fails, so the tracer now checks the path as it does for `openat`. `IN_ROOT` resolves `/` and `..` against the handle, so the path lands somewhere else than it names. It stays refused, as does any bit the code doesn't know. `golang.org/x/sys` v0.42.0 has no `RESOLVE_CACHED`, so the patch defines the kernel's value, 0x20.

The new tests call the real `extractFileArgs` on a call held in the test's own memory. The six restrict-only cases failed against the old check and pass with the fix. The four fail-closed cases pass on both. `go vet` is clean, the `ptrace` unit tests pass, and the integration tag shows the same 8 failures by name.

On that build:

| Check | Result |
|---|---|
| The tar loop and its 9 variants: a folder entry, an implicit folder, a folder made first, `-m`, `-U`, `--no-same-owner`, `--no-same-permissions`, `--delay-directory-restore`, a top-level file | all extract |
| A tree three folders deep | extracts. Its link to `~/github` is refused, which the policy means |
| The repro with each resolve flag | the five pass, `IN_ROOT`, `IN_ROOT` with `BENEATH`, and an unknown bit are refused |
| 49 denial probes under each policy | all held, no fake file or mode changed |
| Tools | exit 0, no file denials |
| Claude Code | worked, exit 0 in 12 seconds. 97 file denials did it no harm: 27 `/proc/*/environ`, `~/.ssh/config` and `~/.netrc`, and its own background writes into `~/.claude`, a plugin clone and a skill sync, that the policy keeps out |
| pi | worked, exit 0 in 13 seconds, one harmless denial |
| Bypass corpus | 24 of 24 held |
| Network and fronting checks | as before |
| `npm install` and `bun add` of a local tarball with nested folders and no `bin` | installed, the nested file in place. A package with a `bin` fails on its relative `node_modules/.bin` link, as before |
| `pnpm add`, the same | fails. Corepack searches upward for a `package.json`, gets `EACCES` on one above the workspace, and stops |

Whether npm and bun failed on `41960715` wasn't tested. The `pnpm` failure is the same kind as uv's `../uv.toml` lookup: a config search above the workspace that gets `EACCES` and treats it as an error, where a missing file would have been fine. Corepack's pnpm also fails on this machine outside the wrap, so pnpm can't be tested past that point here.

`agentsh wrap` runs the command in the `--root` folder, not the caller's. A test that starts it elsewhere needs a `cd` inside the wrapped command.

### Tool config and caches, 2026-11-09

To find everything uv, npm, bun, Corepack and Node touch outside a `--worktree` workspace, each ran under `sssf-proof`, which allows every file but `.env`. The service's event log gave 2,935 operations outside the workspace. A small Go program then judged each one against the hub's installed policy with the fork's own engine (`LoadFromFile`, `NewEngineWithVariables`, `CheckFile`). The script is `cfgharvest.sh`, in the job's temp folder, and the events are in `~/.local/share/agentsh-harvest/cfg1/`. uv 0.10.3, npm 11.6.2, bun 1.4.4, Node 24.11.1.

Three things the docs had wrong:
- **`uv venv` needs a relative link.** It makes `bin/python3 -> python`, so it fails under any policy, `sssf-proof` included, and so does any uv command that has to create an environment: `uv run` in a fresh checkout, `uv run --with`, `uvx`. The engineer's decision of 2026-11-09 on the relative-link allow said `uv venv` needed none, which held for the uv tested then, not for this one. The venv in `~/.cache/uv` fails for this reason, not because of a cache rule.
- **uv fails in a plain run too.** It reads `uv.toml` and `pyproject.toml` in every folder up to `/`, and stops on `EACCES` for `~/github/uv.toml`. Every repo under `~/github` is hit, sssf-sandbox among them. `CONFIG_SEARCH` covers only `/`, `/home` and `$HOME`.
- **npm and bun fail on a package with a `bin`**, on its relative `node_modules/.bin` link: `npm error syscall symlink`, `error: failed to link bin-pkg: EACCES`. A package with no `bin` installs. The decision record said this, and the eighth patch's first table said npm and bun install without that qualifier.

What the hub's policy would also deny: `package.json` in every folder above the workspace, which Corepack stops on and bun skips; `~/.bunfig.toml`; bun's `~/.bun/install/cache` and Corepack's `~/.cache/node/corepack`; `mkdir ~/.npm`; and `~/.npmrc`, which the credential rule means to deny.

A cache an agent writes is also a way out of the wrap. `TOOL_WRITE` lets agents write `~/.cache/uv`, `~/.local/share/uv`, `~/.npm` and cargo's registry. The engineer's own uv links unpacked wheels from its cache without checking them again, `~/.local/share/uv` holds Pythons and tools, and cargo builds extracted crate sources. So an agent's edit there runs on the host, outside SSSF, in later work in any repo.

The engineer decided on 2026-11-09:

1. **Agents get caches of their own.** Under the wrap, each tool's cache and install folders point at a folder only this repo's wrapped agents use, through the tools' environment variables. The policy stops allowing writes to the engineer's own caches.
2. **A fork patch for relative link targets comes first.** It resolves a relative target against the link's folder and judges the result with the file rules, so `~/.claude.json.x -> .ssh/id_rsa` is still denied. The decision of 2026-11-09 named this as the safe way to allow relative links if a repo needs them, and every Python repo needs them for a venv.
3. **The config lookups and the caches go through the guard chain**, from `specs/tasks/guard-agentsh-tool-config.md`, after the patch is installed.

### The namespace hole, 2026-11-09

An agent under the wrap could read any file it can read outside it. It makes a user and mount namespace of its own, with `unshare(CLONE_NEWUSER|CLONE_NEWNS)` and no uid map. It then bind-mounts a denied folder onto a folder in its workspace and reads the file there. The tracer resolves every path through `/proc/<tid>/root`, in the agent's view, so it judged `<workspace>/b/secret.txt` and allowed it. `chroot` worked too.

It turned up while the ninth patch's link resolver was being checked by hand: that resolver walks the agent's view, and the question was whether the agent can make that view differ from the host's. On the installed build, `3918a619`, under the hub's policy:

| Step | Result |
|---|---|
| `cat` of a canary file in `~/sssf-ns-canary/`, outside every allow | `Permission denied` |
| `unshare(USER\|NS)`, `/` made private, the canary's folder bind-mounted over `<workspace>/b`, then `b/secret.txt` read | every step OK, and the canary's text came back |
| `chroot(".")` in the same namespace | OK |
| `unshare -Ur` | fails, since it writes `/proc/self/uid_map`, which the policy denies. The calls don't need the map: the process keeps its own uid for file access and holds every capability in its new namespace |

That gets past every file read rule, the credential denies included, in every repo on `local_guard: agentsh`, which today means the hub and sssf-sandbox. The exec rules and the network rules don't depend on the agent's view of the files, so push stays denied. A secret read this way still reaches the model's context, and the agent can write it into the workspace. The probes are `nsprobe.c` and `nsbind.c`, in the job's temp folder. agentsh has a seccomp syscall blocklist, with `mount` among its documented defaults, but the machine's config runs ptrace mode with no `sandbox.seccomp` section, and whether that blocklist stacks with ptrace wasn't tested.

A second gap came from the same reading. When the tracer fails to inject its BPF prefilter into a tracee, it logs a warning and stops on every syscall instead. io_uring is denied only by that filter, so such a tracee could use it.

**The fix.** `87c6994d`, `fix(ptrace): refuse namespaces, mounts and chroot in tracees`, on `sssf/patched`, local. Each of the tracer's three BPF builders refuses, before any other rule:
- `mount`, `umount2`, `chroot`, `pivot_root`, `setns` and the new mount API (`open_tree`, `open_tree_attr`, `move_mount`, `fsopen`, `fsconfig`, `fsmount`, `fspick`, `mount_setattr`), with `EPERM`;
- `unshare` and `clone` whose first argument asks for a namespace, with `EPERM`. Threads and forks run as before;
- `clone3`, with `ENOSYS`. Its flags sit in a struct the filter can't read, and libc then falls back to `clone`, as it does under Docker's default seccomp profile.

`dispatch` refuses the same calls, which covers a tracee without the prefilter, io_uring included. The new test loads each builder into a child process and makes 22 calls there, so the kernel answers. Before the fix every mount call reached the kernel and `clone(CLONE_NEWUSER)` made a child; after it, each gets the refusal. A second test checks `dispatch`'s list against the same table. `go vet` is clean, the `ptrace`, `api` and `policy` tests pass, and the integration tag shows the same 8 failures by name. The fork and clone tracking tests pass under the new filter.

Two builds:

| Build | Version | sha256 | Folder |
|---|---|---|---|
| The namespace fix alone, `241cd393` on `sssf/confine`: `3918a619` with `87c6994d`'s diff. Built first from a `git archive` copy; a rebuild from the commit gives the same bytes. **Installed 2026-11-09** | `0.20.6-dev.fork+3918a619+ns.87c6994d` | `ee6cd37e03bdd1461cb1c622a15adcf9264c19bc27b5301c2d94355b27120bce` | `~/.local/share/agentsh-builds/confine-only/` |
| `sssf/patched` at `87c6994d`, with the ninth patch's two commits under it | `0.20.6-dev.fork+87c6994d` | `7dc7d9615b958157337bbe5787bab344d174a6610cfcbe9db4bfb8b439b29334` | `~/.local/share/agentsh-builds/confine-combined/` |

The engineer installed the fix alone. On it:

| Check | Result |
|---|---|
| `cat` of the canary | `Permission denied` |
| `nsbind` | `unshare` refused with `EPERM`, so the mount fails and `b/secret.txt` doesn't exist |
| `nsprobe` | `unshare`, `mount` and `chroot` all refused, `/proc/self/root` is `/` |
| `unshare -Ur true` | `unshare failed: Operation not permitted` |
| A Python thread and subprocess, Node's `worker_threads` and `execSync` | work |
| 49 denial probes under each policy, and the must-work lines | all held, all ran |
| Tools | exit 0, no file denials |
| Claude Code | exit 0 in 10 seconds, 89 file denials of the kinds seen before |
| pi | exit 0 in 13 seconds, one denial, `/mnt/wsl/resolv.conf` |
| Bypass corpus | 24 of 24 held |
| Network and fronting checks | as before |

Results are in `~/.local/share/agentsh-harvest/dd6/`. A tool that needs a namespace gets `EPERM` under the wrap: bubblewrap, Chrome's and Electron's sandboxes, rootless podman. Claude Code and pi don't use one in SSSF's runs.

The pin in the guard was never moved for the eighth patch: `PINNED_SHA256` is still build `41960715`'s, and the docs said `3918a619`. So `check_machine` has refused every run under `local_guard: agentsh` since the eighth patch went in. The refusal also stops the guard chain, which runs under agentsh on the hub, so the pin task needed the hub on `local_guard: text` for one run.

Run 7047b87c moved the pin, under the text guard, and merged as 7b36e01 for $1.30. The bypass review found nothing blocking and two old holes, both in the pin check itself. An agent that edits the project's copy of `agentsh_guard.py` can set `PINNED_SHA256` or `BINARY` to a binary it picks, since the runner reads the constant from that copy and the "differs" check covers the config and policies, not this module. And `check_machine` hashes the file on disk, so a service still running an older binary after an install without a restart passes. The hub went back to `local_guard: agentsh`, and the real `check_machine` passes on the hub and sssf-sandbox, both on skill 7b36e01.

### The ninth patch: link targets, 2026-11-09

`f2ee6845`, `fix(ptrace): judge a symlink at the file it leads to`, and `65e2d4fb`, `fix(ptrace): refuse a symlink target through /proc`, on `sssf/patched` under the namespace fix, local. They have no branch of their own: `3918a619..65e2d4fb` is the patch to review. A link's target used to reach the rules as raw text. A relative one matched no rule and fell to the default deny, which broke `uv venv` and npm's and bun's `.bin` links. An absolute one was judged as text, so `<workspace>/../../.ssh/id_rsa` passed any rule allowing the workspace.

`symlinkat` and `symlink` now hand the rules the file the link leads to. A relative target is read from the link's folder, and each part that exists is resolved the way the kernel follows it, through links, with `..` after them. A part that doesn't exist yet is judged as text below its deepest existing folder. That only works while the rest has no `..` and the first missing part isn't a dangling link, so either case fails closed.

The first review run asked how `/proc` resolves, which led to a hole in `f2ee6845`. The walk read `/proc/self` as the agent, which is right for the agent's own opens and wrong for a link, since whoever follows it later reads `/proc/self` as itself. So `ln -s /proc/self/cwd/.ssh/id_rsa x` was judged as `<cwd>/.ssh/id_rsa` and allowed, and a tool following `x` from `$HOME` would reach `~/.ssh/id_rsa`. `3918a619` denied it, since no rule allows `symlink` under `/proc`. `65e2d4fb` makes the walk for link targets fail closed when it enters `/proc`, directly or through a link: `/dev/fd`, `/dev/stdin`, a relative target that climbs to `/proc`, or an earlier link into it. It also fixes a link whose own folder is missing, where `filepath.Dir` had applied the `..` as text.

The new tests failed before each fix and pass after: 7 resolution cases, 5 fail-closed cases, 9 cases through `/proc` and the missing folder. The `ptrace`, `api` and `policy` tests pass. The integration tag shows the 8 known failures, and `ReadExitSkipForNonStatusFd` fails about one run in three, on `3918a619` as well.

Limits:
- **The check happens when the link is made.** A relative link that resolves inside the workspace can be moved later to a shallower folder the agent may write, such as `/tmp/x`, and lead to `~/.ssh/id_rsa`. Swapping a folder on its path for a link does the same for an absolute target. Inside the wrap every read through a link is judged at the file it reaches, so this only matters to a tool that follows such a link outside the wrap. `3918a619` allows the same link in one step.
- **Reviewed on 2026-11-09.** An independent review found the patch's own logic sound, and three holes that were there before it. [The review and three fixes](#the-review-and-three-fixes-2026-11-10) has them.

### The review and three fixes, 2026-11-10

A reviewer subagent with a fresh context read `3918a619..65e2d4fb` and probed it with C programs on decoy files under a temp folder. The full transcript is kept outside the repo. `resolveLinkTarget` and `walkViaProc` held: about 30 target spellings, `//proc`, `/./proc`, `/../proc`, links to `/` and links up to `/proc` among them, each resolved right or failed closed. The holes it found were older than the patch, and one more turned up while their probes became tests:

| Hole | Probe on the installed build, `241cd393` | Fix on `sssf/races` |
|---|---|---|
| **A link path with a missing folder was judged as text.** When a path's parent didn't exist, `resolveParentViaProc` returned the raw path. A second thread making and removing `ws/missing` in a loop got `ws/missing/../../out/x` past a rule allowing the workspace, since `**` crosses `..`. `symlinkat`, `mkdirat`, `linkat` and `renameat2` all share that fallback | 17, 33, 12 and 3 entries made in the refused folder, 300 tries each | `8ac79c33`: refuse with `ENOENT`, which is what the kernel says unless the race wins |
| **`renameat`, `mknod` and `lchown` were never judged.** None was in the tracer's lists or the seccomp prefilter. glibc 2.39's `renameat2` with no flags makes `renameat`, as `mv` does | `renameat` moved a refused decoy into the workspace and a workspace file into the refused folder; `mknod` made a file there; the tracer saw none of them | `dea39254`: all three judged. A unit test holds the three syscall lists to one set of path-writing calls and failed on the parent for exactly these three |
| **A link target could change after the check.** The kernel copies the target inside the syscall, after the tracer resumes the thread, so a second thread rewriting the buffer gets a link the rules never saw. `redirect_file.go`'s comment said the opposite | 126 of 500 links in the workspace led to the refused decoy | `3a36c68f`: `symlinkat` and `symlink` take an exit stop. The tracer opens the link's folder with `openat2` and `RESOLVE_NO_SYMLINKS`, reads the stored target through it, judges that, and on a refusal removes the link and fails the call |
| A bind mount in a private namespace, from the review's run on `65e2d4fb` | refused at `unshare` | already closed by the namespace fix |

The `renameat` hole is the serious one. Under the installed build, a plain `mv` of a file outside every allow into the workspace goes through, and the file can then be read there. It reaches every repo on `local_guard: agentsh`, the hub and sssf-sandbox. The other two need a racing thread, and the target race only makes a link, which every read inside the wrap still judges at the file it reaches.

Each fix has a probe that failed before it and passes after, three runs out of three: `symlink_race_integration_test.go` builds its C helpers with gcc and runs them traced. `TestSymlinkExitKeepsAllowedLinks` checks relative, absolute and dangling links still work. The `ptrace`, `api` and `policy` tests show the same failures as `sssf/patched`, 9 in `api` that need cgo, and so does the integration tag, 10 by name. The BPF budget test went from 100 instructions to 128, since the program is now 101. The real limit is the `uint8` jump offset, 255 instructions, and is written beside it.

| Build | Version | sha256 | Folder |
|---|---|---|---|
| `sssf/races` at `3a36c68f`: `sssf/patched`, so the ninth patch and the namespace fix, with the three fixes. Built twice with the same bytes | `0.20.6-dev.fork+3a36c68f` | `0e4430b5eb9f77ddb8014bcc6cd1fad3c8eda6a4deeb7b041777f74c46440886` | `~/.local/share/agentsh-builds/races/` |

Limits:
- **A link moved before the exit stop isn't judged.** A second thread can rename the new link away between the kernel's call and the stop. The call then fails with `EACCES` and the moved link stays. That is the link-time limit above.
- **Folders that ignore case.** On `/mnt/c` and casefold folders `.SSH` reaches `.ssh`, and a rule that names `.ssh` misses it. Not probed.
- **openat2 is needed** for the exit stop, Linux 5.6 or later. Without it every symlink fails closed.

**Installed 2026-11-10.** The engineer installed `3a36c68f`. On it, under sssf-sandbox's policy, `mv`, `rename`, a direct `renameat` and `mknod` of a canary outside every allow were refused and nothing moved; `ln -s` to the canary, absolute or relative, was refused. A relative `ln -s` in the workspace, `mv` inside it, `uv venv` and npm's `.bin` link worked. The check set matched the namespace build's: 98 probes held, 32 must-work lines ran, Claude Code exited 0 in 9 seconds with 89 file denials and pi in 13 seconds with one, the corpus held 24 of 24, and the 28 network verdicts were the same. Results are in `~/.local/share/agentsh-harvest/dd7/`.

Run 162cbc4b moved the pin, under the text guard, and merged as 99904da for $1.91. It also brought `config.md` in line with the ninth patch: relative links work, and a link's target is judged at the file it reaches. The bypass review found nothing blocking and the same two old holes in the pin check as run 7047b87c, both Next item 1. The hub went back to `local_guard: agentsh`, and the real `check_machine` passes on the hub and sssf-sandbox, both on skill 99904da. sssf-sandbox's quality run c0ffc393 passed.

### Stock policies, sockets, signals and DNS, 2026-11-10

The engineer asked whether agentsh's stock policies in `configs/policies/` shaped SSSF's. They didn't. `policy_text` writes each policy from the repo's settings, and the stock files were read only during research. Upstream's 15 policies are the same at `a07c7897` (2026-11-07) as on the fork. Besides SSSF's three sections they use `unix_socket_rules`, `env_policy`, `package_rules`, `resource_limits`, `signal_rules` and `audit`. `agent-default` is built to let an agent work: it allows the Docker socket, which is as good as root, and wildcard model hosts. So SSSF borrows from it section by section, never whole.

Probes under the hub's policy, with decoys I started and deleted after:

- **Unix sockets are refused.** Connects to listeners in `/tmp`, `~`, `/run/user/1000` and an abstract socket failed with EACCES, and so did a throwaway `ssh-agent` holding a fresh key. Listing `/run/user/1000/gnupg` was refused. Default-deny covers sockets, so `unix_socket_rules` adds nothing. Agents still get `SSH_AUTH_SOCK`, harmless while connects are refused.
- **sudo and su fail, and the network rules hold.** TCP to 127.0.0.1, the LAN gateway and 169.254.169.254 was refused, and UDP to any port but 53.
- **Signals aren't judged.** The server config has `trace.signal: false`, and in ptrace mode agentsh's `HandleSignal` (`internal/api/ptrace_handlers.go:462`) allows every signal anyway: "allow all signals for now". An agent killed two decoy processes outside its session, with TERM and with KILL. It can't free itself: the tracer runs as user `agentsh`, and when the agent killed its own wrap client, a detached child stayed traced and was refused a canary 8 times in 8. `ptrace` attach and `/proc/<pid>/mem` on a decoy were refused (Yama `ptrace_scope` 1). What it can do is kill the SSSF runner, so the run ends without its after-call checks, and kill the engineer's editor, other sessions and other runs.
- **The DNS proxy forwards to the agent's choice of server.** The tracer sends every port-53 `sendto` and `connect` to agentsh's DNS proxy. The proxy answers NXDOMAIN for a name the policy doesn't allow, `example.com` in the probe. For an allowed first question it forwards the agent's raw packet, from the service and outside the network rules, to the address the agent named (`dns_proxy.go`, `forwardQuery(raw, redirectInfo.originalResolver)`). In the probe, `api.anthropic.com` sent to the system resolver got an answer, and sent to 192.0.2.1 came back SERVFAIL after the proxy's timeout. A second question naming a decoy host went through unchecked. 8.8.8.8, 9.9.9.9 and 208.67.222.222 answer from this machine, so a packet whose first question is an allowed host can carry anything to a server on any public IP's port 53, and the reply comes back. That's a way out past network default-deny. Next item 8.

Not borrowed: `env_policy`, since `agent_env` already drops the `.env` keys and provider keys before launch; `package_rules`; `audit`; and `resource_limits`, not checked under ptrace here.

### The tenth and eleventh patches: DNS and signals, 2026-11-10

Both are on `sssf/dns-signals`, cut from `sssf/races` at `3a36c68f`, local.

`ba8403a0`, `fix(ptrace): forward DNS only to the system resolver, and only the checked question`, is Next item 8. The proxy no longer sends the agent's packet anywhere. It builds a new query from the one question it checked, adds a bare OPT record of its own when the agent's packet had one, and sends that only to the first resolver in `/etc/resolv.conf`, or to the policy's `RedirectUpstream`. A packet with more than one question gets FORMERR, and with no system resolver the answer is SERVFAIL. `dns_forward_test.go` runs the proxy against two fake resolvers, the system's and the agent's pick, and checks that only the system's gets a packet and that bytes after the question, an extra record and EDNS option data never reach it. All six tests failed before the fix.

`472d63cf`, `fix(ptrace): judge signals against signal_rules, and judge signal owners`, is Next item 9. The tracer works out every process a signal reaches: the one named, each member of the group for `kill(0)` and `kill(-pgid)`, and "everyone" for `kill(-1)`. It describes each against the sender (in the session, parent, child, descendant, sibling, same user, command name), and the router judges each with the policy's `signal_rules`. One refusal refuses the call with EPERM, a gone target gets ESRCH, and signal 0 is allowed. A zombie whose parent is in the session counts as in the session, so a shell can still reap its jobs. A policy without `signal_rules` allows every signal, so installing the build changes nothing until the guard chain adds the rules.

The F_SETOWN probe above found a second way to send a signal: `fcntl(F_SETOWN)` on a pipe, `F_SETSIG` set to SIGTERM and `O_ASYNC`, then a write, and the kernel kills the owner itself. The patch judges a new owner from `fcntl` `F_SETOWN` and `F_SETOWN_EX` and from `ioctl` `FIOSETOWN` and `SIOCSPGRP` as a SIGKILL to it. With signal tracing on, the prefilter stops `fcntl` and `ioctl` only for those commands, by a bit test on the command: bit 3 for the two `fcntl` commands, which the everyday ones 0 to 7 and `F_DUPFD_CLOEXEC` lack, and bit 15 for the two `ioctl`s, which terminal ioctls lack. The vfork fast path no longer lets these calls by unjudged.

`signal_integration_test.go` runs a traced C program against a decoy `sleep` outside the session, with and without the prefilter. Each of the six syscalls and each of the six owner calls toward the decoy is refused, a vfork child's `F_SETOWN` too, and the decoy lives. Signals to itself, to its own child and to its own group work, real-time signals included, as do `F_SETOWN` to itself and to its child. On the old code the outside cases succeeded; with the owner handler switched off the owner cases did. Upstream's `TestIntegration_SignalDeny` and `TestIntegration_SignalRedirect` failed on a first draft that skipped the policy for a signal to the sender itself, so self-signals still go to the policy, as target type `self`. The `ptrace`, `api`, `policy`, `signal` and `config` tests show the 9 `api` failures that need cgo and nothing else, and the integration tag the same 10 failures by name as `sssf/patched`. The new tests passed five runs in five.

| Build | Version | sha256 | Folder |
|---|---|---|---|
| `sssf/dns-signals` at `472d63cf`: `3a36c68f` with both patches. Built three times with the same bytes | `0.20.6-dev.fork+472d63cf` | `f9da98024093f13634f26d349237eb217dc5b10fab826d222f08d8a88f79933e` | `~/.local/share/agentsh-builds/dns-signals/` |

Limits:
- **The check comes before delivery.** A target can exit and its pid be reused between the tracer's check and the kernel's delivery. The window is a few `/proc` reads, and the new process would have to be one the agent's user may signal.
- **DNS session lookup.** The proxy finds the session from the tracer's last redirect, so with two sessions at once a query could be judged under the other's policy. Both are SSSF policies with the same allowed hosts in practice, but not by design.
- **approve and absorb mean refuse.** The tracer can't hold a signal for approval or swallow it quietly, so either decision refuses the call.
- **`@all` is 1 to 31** in agentsh, so SSSF's rules name 32 to 63 as well.

**Installed 2026-11-10.** The engineer installed `472d63cf`. Under the hub's policy, a query for `api.anthropic.com` sent to 192.0.2.1 or 8.8.8.8 was answered by the system resolver, where before the dead address gave SERVFAIL; `example.com` still got NXDOMAIN, and a packet with a second question got FORMERR. Signals can't be probed until the chain run turns `trace.signal` on. The check set gave the same results as on `3a36c68f`, apart from pids and timings: 98 probes held, Claude Code exited 0 in 7 seconds with 86 file denials and pi in 14 seconds with one, all on the same paths, the corpus held 24 of 24, and the 28 network verdicts were the same. Results are in `~/.local/share/agentsh-harvest/dd8/`.

Run 8a727544, from `specs/tasks/guard-agentsh-signals.md` under the text guard, turned `trace.signal` on, added the five `signal_rules` to every policy `policy_text` writes and moved the pin to `472d63cf`. It passed in one round for $3.57 and merged as 63828cf. The bypass review checked 1,021 cases and found nothing blocking. It listed two old holes: a quote inside the command word, which `config.md` already lists, and `kill -TERM $PPID`, which passes the text checks, so under `local_guard: text` an agent can still kill the runner. The new rules refuse that one under agentsh. `config.md` adds a limits bullet: the check comes before delivery, signal 64 can't be named in a rule and so is always refused, and a refused signal goes to the trace as a denial without counting toward the three-refusal stop. The guard's final event read takes every event type and counts only named exec denials, which bears the last part out. The hub and sssf-sandbox are on skill 63828cf.

**Signals on the service, 2026-11-10.** The engineer installed the new config and policies, and the real `check_machine` passes on the hub and sssf-sandbox. Under the hub's policy, against `sleep` decoys started outside the wrap:

- The test's C program got EPERM from `kill` with TERM and KILL, `tkill`, `tgkill`, `rt_sigqueueinfo`, `rt_tgsigqueueinfo`, `pidfd_send_signal`, `kill(-1)`, and all six owner calls, a vfork child's `F_SETOWN` too. Signal 0 to the decoy was allowed. To itself, its child and its own group it could signal, a real-time signal included, and set itself or its child as owner.
- `F_SETOWN` toward a decoy, before `F_SETSIG` and `O_ASYNC`, got EPERM.
- In bash, `kill -TERM` and `kill -KILL` to a decoy got "Operation not permitted", while `kill $!`, `timeout 1 sleep 5`, `kill %1`, a Python real-time signal to itself and `kill -0 $PPID` worked.
- Claude Code under the hub's policy exited 0 in 7 seconds. Its shell ran `kill $!` (status 143) and `timeout` (124), and its `kill -TERM` to a decoy was refused.

Every decoy lived. `kill(0)` is allowed under the wrap, because `wrap` starts the agent in a process group of its own, so the group holds only the agent's processes. The event log had 23 `ptrace_signal` denials, all `sssf-signal-external`, and 38 allows under `sssf-signal-self` and `sssf-signal-session`. The check set, run again with `trace.signal` on, matched `dd8` line for line apart from pids and timings: 98 probes held, Claude Code exited 0 in 9 seconds and pi in 13, the corpus held 24 of 24 and the 28 network verdicts were the same, in `~/.local/share/agentsh-harvest/dd9/`. Its policies have no `signal_rules`, so that shows tracing doesn't break the agents, and the Claude Code run above shows the rules don't. pi has no policy of its own yet (Next item 7), so it hasn't run under the rules.

### The tool-config run and the hub's own tests, 2026-11-10

Run 45742573, from `specs/tasks/guard-agentsh-tool-config.md`, was the first guard run under `local_guard: agentsh`. Its spec passed review on the second try, `7ce5f4f` on branch `guard-agentsh-tool-config`, and its build failed after 772 seconds for $3.70: SSSF stopped the builder for 29 refusals under `sssf-no-live-tests`. The builder never ran a live test. Each refusal was a stub `just test live` that `test_rules_continuation.py` runs from pytest's temp folder, five processes below the builder. The policy is right to refuse it there, and the guard is right to count every named exec denial in the agent's tree, or an agent could hide a refused command a process further down. The fix belongs in the tests. The partial build is in `~/.local/share/agentsh-harvest/45742573-partial-build.diff`.

The builder got the suite to run under the wrap by copying `.claude` into `/tmp` and running `uv run --no-config` there, because inside the repo the policy refuses the hub's `.env`, which `adws/tests` and the skill suite read at import, and uv's config above the repo. Run that way, the whole skill suite made 239,299 execs under the hub's policy, and 31 were refused, all `just test live`, from five tests in `test_rules_continuation.py` and `test_rules_tab_heredoc.py`. In all, 203 tests failed and 12 errored. Apart from the refused stubs, they failed on file and connect denials, which fail a test without counting toward the stop.

Run 6e904be8, from `specs/tasks/guard-test-fixture-commands.md`, ran under the text guard, since under agentsh its builder would have run the old tests first. It passed in one round for $3.47 and merged as 66145ef. The two files now run a stub `nosuch-sssf-fixture` in place of `just`, and their fixture rules forbid that. Every row of both tables keeps its rule, expected result, splits, tabs and quotes, and its recorded run is the old one with the name changed. Under the hub's policy the three rules files made 373 execs and none was refused. The whole skill suite at the merged head made 239,268 execs under the hub's policy and none was refused; 172 tests failed and 12 errored, all on file and connect denials, down from 203 failures.

The review found no bypass, and one broken promise: four rows in the tab-heredoc table run the `git` stub as `git push origin main`, which `sssf-no-push` refuses by name. They don't reach the stub under agentsh today, because each test first writes a `.env` file in its temp folder and the policy refuses that write. Those rows check the real push rule, so a fixture name can't stand in for `git`. If the `.env` write is ever allowed, they'll stop a builder the same way. It listed five old holes, four already in `config.md`. The new one: `config.md`'s example pi pattern `(^|[;&|]\s*)pi\b` misses pi after a wrapper word, as in `env pi -p hi`, `timeout 60 pi`, `bash -c '...'` or `echo $(...)`. That comes from the pattern's anchor, and only the text guard reads it.

### The tool-config run under agentsh, 130ca6e0

Run 130ca6e0, from `specs/tasks/guard-agentsh-tool-config.md`, ran under `local_guard: agentsh` and passed in two rounds for $9.00, in 61 minutes. It merged as 9ccf50d, and the hub is on that skill in fa43096. It's the first guard run to pass under agentsh. agentsh refused nothing in it. The text guard refused once, in the build: a Python heredoc that edits a test and names `.env` in its text.

The spec took three reviews. The first asked for two things: say that moving `UV_PYTHON_INSTALL_DIR` hides the engineer's managed Pythons, since the network rules refuse uv's download host, and allow only the operations the tools need on the folders above the workspace rather than all of READ, so an agent can't list sibling repos or session ids. The second found that version 2's workaround, `uv python install` outside the wrap, would since uv 0.8 leave a `python3.13` link in `~/.local/bin` pointing into a folder agents may write. Version 3 uses `--no-bin` and absolute paths.

What changed:

- **Config lookups.** A new rule, `sssf-config-folders`, allows `open`, `stat`, `readlink` and `access` on each folder above the workspace, each operation cited from uv's, bun's or Corepack's source, and `sssf-config-search` allows reads of the tools' config file names in those folders. `list` stays denied on the folders, by the rule; on the engine the `open` lists them ([the probes below](#the-tool-config-run-under-agentsh-130ca6e0)).
- **Agent caches.** Under the wrap, each tool's cache and install folders point at `adws/adw_data/agent-cache` in the main checkout, through the tools' environment variables, set in `start()`. The policy drops the write allow on the engineer's own caches and allows the agent folder instead. Reads of the engineer's managed Pythons and uv tools stay allowed.
- **System links.** `sssf-system-link` allows a link whose target is under `/usr/bin` or `/bin`, so `uv venv` can link the system Python by an absolute path.

Round 1's review found a bypass: rendered rule paths weren't glob-escaped, so a checkout at a path like `~/github/repo[old]` would get rules that match a sibling repo's cache and config. The fix escapes every path SSSF renders. Round 2 found nothing that blocks, in 2,085 checks over nine checkouts with glob characters.

Left from the report:

- **Broken promise.** On a worktree run an agent can replace the agent-cache folder with a relative link to `~/.local`. The documented `uv python install` step sets `UV_PYTHON_BIN_DIR` inside that folder, so only `--no-bin` keeps it out of `~/.local/bin`. SSSF's own `mkdir` of the folder follows the link on worktree runs too; the spec named only plain runs. No harm was shown.
- **uv's hardlinks.** uv hardlinks cache files into a venv. In a venv built outside the wrap, an agent that writes a site-packages file in place changes that file in the engineer's `~/.cache/uv`. `config.md` now says agents no longer write `~/.cache/uv`, which isn't so for those venvs. `main` had the same hole.
- **`${PROJECT_ROOT}`** is expanded by the engine, where SSSF's escaping can't reach. If the fork puts the root into a glob as raw text, the workspace, `.git` and secret-file rules widen or miss for a checkout path with glob characters. Unchecked on the engine.

**Installed and probed, 2026-11-10.** The engineer installed the new policy, and the real `check_machine` passes on the hub. Under the hub's policy, with the variables `cache_env` sets, from the hub's root:

- These work: `uv run --no-project --with pyyaml`, the hub's own `uv run`, `uv venv` (its `python` links to `/usr/bin/python3`), `uv pip install` into that venv, `bun add`, and a link to `/usr/bin/python3` in the workspace. `uv cache dir` and `npm config get cache` name folders in the agent cache. Claude Code under the policy ran both uv commands and exited 0 in 10 seconds.
- These are refused: writes to the engineer's `~/.cache/uv`, `~/.local/bin` and `~/.npm`, a read of a sibling repo's README, a link to `/etc/shadow`, and `uv pip install` with `UV_CACHE_DIR` set back to `~/.cache/uv`.

Three things the probes found:

- **The folders above the workspace can be listed.** In ptrace mode agentsh judges opening a folder as `open`, and reading its entries isn't a separate check, so the `open` that `sssf-config-folders` allows lets `ls` list them. `/`, `/home` and the home folder were listable on `main` too. `~/github` wasn't, and now an agent can read the names of the engineer's other repos there, though nothing inside them. The spec's never-item, no `list` on a folder above the workspace that the baseline didn't let an agent list, doesn't hold on the engine.
- **npm fails under the wrap.** It opens `~/.npmrc`, which doesn't exist here, gets EACCES instead of ENOENT and stops on loading its config. `main` has no rule for that file either. With `npm_config_userconfig` pointed at a file in the agent cache, `npm install` worked.
- **pnpm fails on this machine with or without the wrap.** Corepack fetches pnpm 12.18.0, which Corepack's shim can't start, and the engineer's own `pnpm` fails the same way. agentsh isn't involved.

The event log also had two `sendto` denials under `default-deny-network` with no address, from processes the probe started; their source isn't known. The check set wasn't rerun: its policies and the binary are the same as for `dd9`.

**sssf-sandbox, 2026-11-10.** It updated to skill 9ccf50d in a local commit on `agentsh-rollout`, and the engineer installed its new policy. `check_machine` passes there, and the same probes gave the same results with one more failure. Claude Code under its policy ran `uv run` in the project and `uv cache dir` and exited 0 in 7 seconds.

The failure: `uv run --no-project --with pyyaml` stops on "failed to symlink file" from the throwaway environment in the agent cache to a Python under `~/.local/share/uv/python`. With no `.python-version`, uv takes the interpreter of the project's `.venv`, which the engineer built outside the wrap on uv's managed 3.13. The environment's `python` links to that interpreter, and `sssf-system-link` allows only targets under `/usr/bin` and `/bin`. In the hub the same command found `/usr/bin/python3` and worked. With `--python /usr/bin/python3` it works in sssf-sandbox too. `uv venv --python 3.13` fails as the spec said it would: uv no longer sees the managed Pythons, and the download host is refused. So under the wrap, in a repo whose venv is on a managed Python, `uv run --with` fails. Allowing links to those Pythons looked safe, since agents can read them and can't write them. Run 98e91ed7 found it isn't ([the run](#the-tool-fixes-run-98e91ed7)).

### The tool-fixes run, 98e91ed7

Run 98e91ed7, from `specs/tasks/guard-agentsh-tool-fixes.md`, took on Next item 12 under `local_guard: agentsh`. It passed in two rounds for $4.64, in 34 minutes, with no refusals of either kind, and merged as 45e6cc8. The hub is on that skill in f0a3ae5, and sssf-sandbox in a local commit, 05f8f3c. Neither policy changed, so neither needed a reinstall.

- **npm.** `cache_env` sets `npm_config_userconfig` and `npm_config_init_module` to files in the agent cache. `config.md` says the engineer's `~/.npmrc` no longer reaches agents, and that a repo's registry settings go in its own `.npmrc` with tokens from the environment.
- **Listing.** `config.md` drops "not listed" and lists among the limits that an agent can list every folder from `/` to the workspace: the names of the engineer's other repos and of the sessions and worktrees in `adws/adw_data`, not what's inside them.
- **uv's managed Pythons: dropped.** Version 2 of the spec allowed links to `~/.local/share/uv/python/*/bin/python3.*`, and round 1's review found a bypass. The engine checks a link's own path and its target with the one `symlink` operation against the one rule list, and the workspace, temp, agent-cache and sessions rules allow every operation and come first. So the allow also let an agent make a link at `<install>/bin/python3.99` that leads to a script it wrote, which runs as the engineer if anything starts that name, and which the agent can't delete afterwards. A rule can't narrow it. The builder dropped the allow in fix round 1, and round 2 found the rendered rules the same as on `main` in 2,376 checks. `config.md` records the uv case as a limit. Commit 45e6cc8's message still says it allows those links; it doesn't.

On the installed service, `check_machine` passes in both repos. Under each policy with the new variables, `npm init -y`, `npm install` and `npx --yes semver` work, and a write to `~/.npmrc` is refused. npm warns that `npm_config_store_dir` and `npm_config_cache_dir`, pnpm's settings in `cache_env`, are unknown to it and "will stop working in the next major version of npm".

**Two uv variables tried, 2026-11-10.** In a scratch project in sssf-sandbox, with a venv made outside the wrap on uv's managed 3.13, under its policy with `cache_env`'s variables. With `UV_PYTHON_PREFERENCE=only-system`, nothing changed: `uv run --with` and `uv run --no-project --with` still fail on the link, because uv takes a venv's interpreter whatever the preference, and the project run, `uv sync` and `uvx` worked either way. With `UV_PYTHON=/usr/bin/python3`, both `--with` forms worked on 3.12, but in the project uv rebuilt the venv on `/usr/bin/python3`, which in a real repo would swap the engineer's venv to another Python. Neither belongs in `cache_env`, so the link needs the fork patch, or stays a limit.

### The twelfth and thirteenth patches: link targets and listing, 2026-11-10

Both are on `sssf/links-listing`, cut from `sssf/dns-signals` at `472d63cf`, local. They close what run 98e91ed7 left in Next item 12.

`34eff9bb`, `fix(ptrace): judge a symlink's target as symlink_target`. The ptrace router judged a link's own path and its target with the one operation, `symlink`. It now judges the target as `symlink_target`. A rule that allows links to a tree names `symlink_target`, so it no longer lets an agent make a link inside that tree. An allow rule that names only `symlink` allows no target, so it fails closed. A deny rule that names only `symlink` no longer refuses a target, so it fails open: see the probes below. Hardlinks and renames judge both ends as before. The seccomp-notify handler, which SSSF doesn't use, still judges both as `symlink`.

`05b3777f`, `fix(ptrace): judge reading a folder's entries as "list"`. No ptrace syscall produced `list`, so an allowed `open` on a folder let an agent list it. The tracer now stops `getdents64`, and `getdents` on amd64, and judges the folder its descriptor names in `/proc` as `list`. Another thread, or a process sharing the descriptor table, could swap the folder in that slot between the check and the kernel's read. So while an allowed `getdents` is under way, `close`, `dup2`, `dup3` and `close_range` on that slot are refused in every task that shares the table, found with `kcmp`; the vfork fast path doesn't skip them then. A swap that was already in flight at entry shows at the exit stop, and the tracer wipes the entries and fails the call.

`listing_integration_test.go` lists an allowed and a refused folder through `getdents64`, `getdents` and a reopened descriptor. It races 3,000 reads against a thread that flips the slot with `dup2`, and against a `CLONE_FILES` vfork child that flips it with `dup3`. Before the patch it failed, with 82 and 97 reads leaking the refused folder's names; after it, five runs passed with none. The router test checks six link cases, among them a link inside an allowed target tree, which is refused. The ptrace integration suite fails the same 15 tests by name before and after, and the ptrace, policy and api unit tests pass.

| Build | Version | sha256 | Folder |
|---|---|---|---|
| `sssf/links-listing` at `05b3777f`: `472d63cf` with both patches. Built three times with the same bytes | `0.20.6-dev.fork+05b3777f` | `603dbf1495da87c28be301f607efd00c66fb250633334204a0b04c2f10df1747` | `~/.local/share/agentsh-builds/links-listing/out1/` |

**Probes without installing.** A private server on the new build, run as the engineer's user with copies of the installed config and policies, can't attach to `agentsh wrap`, since Yama lets it trace only its own children. `agentsh exec` works, because the server starts the command itself. Under the hub's installed policy:

- `ls ~/github` and `os.listdir` of it are refused ("reading directory: Permission denied"). `/`, `/home`, the home folder, `/usr/bin`, `/proc`, `/proc/self/fd`, `/tmp`, `~/.claude` and the workspace still list, from `SYSTEM_READ`, `HOME_READ`, `sssf-agent-write` and `sssf-workspace`. `ps`, Python's `subprocess`, `git status`, `npm install` and `bun add` work.
- Every link to `/usr/bin` is refused, `uv venv` and `uv run` among them, because `sssf-system-link` names `symlink`. A policy must move to `symlink_target` before this build is installed.
- **A link to `~/.claude/settings.json`, `.git/config` or `.git/hooks/pre-push` is allowed.** `sssf-agent-config` and `sssf-git-config` take `WRITE_OPS`, which names `symlink` but not `symlink_target`, so the target falls through to `sssf-agent-write` and `sssf-git-data`. 472d63cf refuses all three. With `symlink_target` added to both lists, all three are refused and a link to a workspace file still works. `check_machine` refuses the new build until the checkout pins it, so the build can't run under today's policies by accident.

Under a probe copy of each policy with `sssf-system-link` set to `symlink_target`, and with `${HOME}/.local/share/uv/python/*/bin/python3.*` added, links to `/usr/bin`, `uv venv`, `uv run --with`, npm and bun work. In sssf-sandbox, on a scratch project whose venv is on uv's managed 3.13, both `uv run --with` forms work. Round 1's bypass, a link at `<install>/bin/python3.99`, is refused, and so is a link to `/etc/shadow`. A one-turn Claude Code call exits 0 in 4 seconds, and the slot gate never fired.

Walking folders costs more. Through a private server, `find /usr/lib/python3` (897 folders) takes 0.25 to 0.33 seconds against 0.16 on 472d63cf, and an `os.walk` of it 0.22 to 0.29 against 0.15: about 0.1 ms more per folder, for the two extra stops and the policy check of each `getdents`. Outside the wrap both take 0.01 to 0.02. (A walk of the hub itself takes 46 to 90 seconds under the wrap on 472d63cf, since `adws/adw_data/sessions` holds 699,000 entries, mostly bypass reviewers' scratch runs.)

Limits:
- **Two swaps in flight.** A swap allowed before the `getdents` stop that lands after the entry check, and a second that undoes it before the exit check, still race. It needs two long-running calls, such as `close_range` over many descriptors, timed around the stop.
- **Names still show other ways.** `inotify` and `fanotify` events carry the names that change in a watched folder, and `stat` says whether a name exists. Neither is traced.
- **A false refusal.** A thread that closes or `dup2`s onto a slot while another thread lists it gets `EINTR` or `EBUSY`. Programs don't do that on purpose.

### The links and listing run, 47b4d0ea

Run 47b4d0ea, from `specs/tasks/guard-agentsh-links-listing.md`, passed in one round under `local_guard: agentsh` on 472d63cf, for $4.75. The first spec review sent the bar back for leaving out the brief's must-work and must-refuse lists; the second passed it. The first verify failed only on the flaky `test_a_non_breach_failure_with_three_denials_becomes_the_breach[cc]`, and the builder changed nothing. Merged by fast-forward as `39bce41`:

- `WRITE_OPS` adds `symlink_target`, so the two deny lists refuse a link to a file they guard.
- `sssf-system-link` names `symlink_target`, and `SYSTEM_LINK` adds `${HOME}/.local/share/uv/python/*/bin/python3.*`.
- A new `sssf-home-folder` rule gives `${HOME}` itself `FOLDER_OPS` only, and `HOME_READ` drops it, so the home folder can be opened but not listed.
- `PINNED_SHA256` names build 05b3777f; `config.md`, `install.md` and `deny-policy.py` follow.

The bypass review found no bypass, harm, false positive or broken promise, and three old holes:

- **Claude Code's own folder.** `sssf-agent-write` allows everything on `~/.claude/**` and `~/.claude.json`. The folder names under `~/.claude/projects` and the projects map in `~/.claude.json` name the engineer's other repos, and the transcripts and `~/.claude/file-history` hold their files' contents. The run's new `config.md` line said an agent "can't list your other repos"; the hub session rewrote it by hand after the merge to say how the names and files still reach an agent. It's Next item 13.
- **`/proc/<pid>/cwd` and `cmdline`.** `sssf-system-read` allows reading `/proc/**`, so the engineer's other processes show their repos' paths. Listing through `/proc/<pid>/cwd` is judged on the resolved folder and refused.
- **The main checkout's folders on a `--worktree` run.** `adws`, `adws/adw_data` and `adws/adw_data/worktrees` are in `sssf-config-folders`, so on the new build they can be opened and not listed. The spec named the first two only.

**Installed, 2026-11-10.** The engineer installed build 05b3777f and both repos' policies, rendered from the install clone at `c9d3950`. `check_machine` passes in the hub and sssf-sandbox. Under `agentsh wrap` on the service, in both repos:

- `ls ~`, `ls ~/github` and `os.listdir` of `~/github` are refused. `/`, `/home`, the workspace, `adws/adw_data`, `/usr/bin`, `/proc`, `/tmp` and `~/.claude` list. `ps`, Python's `subprocess` and `git status` work.
- Links to `/usr/bin`, `uv venv` on `/usr/bin/python3`, `uv run --with`, `npm install` and `bun add` work. In sssf-sandbox, whose `.venv` links to uv's managed 3.13, `uv run --with pyyaml` works with and without `--no-project`.
- Links to `/etc/shadow`, `~/.claude/settings.json`, `.git/config` and `.git/hooks/pre-push` are refused, and so is a link at `<managed install>/bin/python3.99`. Writes to `~/.npmrc` and `~/.local/share/uv` are refused, and so is reading the other repo's README.
- A one-turn Claude Code call exits 0.

### The Claude home run, cea6fe8d

Run cea6fe8d, from `specs/tasks/guard-agentsh-claude-home.md`, passed in two rounds under `local_guard: agentsh` on 05b3777f, for $6.13. The first spec review found a smaller design, without a new `LocalGuard` field or a `session_files` helper, and the planner took it. The first verify failed two tests, which the builder fixed: `check_login` makes one subprocess call, and `agents.execute` reads `local_guard` from the request. Merged by fast-forward as `57bca99`. For Claude Code agents under agentsh only:

- Each run gets its own config folder, `sessions/<adw_id>/claude-config`, passed as `CLAUDE_CONFIG_DIR`. SSSF writes it through the link-safe walk, so a link an agent puts there is replaced by a real folder.
- Agents sign in with `CLAUDE_CODE_OAUTH_TOKEN`, from the launching environment or else `~/.config/sssf-sandbox/token.env`, and get `DISABLE_AUTOUPDATER=1`. Without a token, the launch refuses before any worktree or run row exists.
- `CLAUDE_WRITE` drops `~/.claude`, `~/.claude/**`, `~/.claude.json` and `~/.claude.json.*`, so default-deny refuses them. `~/.local/share/claude/**` moves to `CLAUDE_READ`, read-only. `CLAUDE_READ` drops `~/AGENTS.md`, `~/CLAUDE.md`, `~/github/skills/**` and `~/github/plugins/**`.

Round 1's bypass: `check_login` ran `claude auth status` outside the wrap with its config folder in `/tmp`, which every agent policy allows in full, so an agent could plant a `settings.json` there for that unwrapped Claude Code to load. The fix makes the folder under `~/.config/sssf-sandbox/`, which `sssf-credentials` denies to agents. Round 2 found no bypass, harm, false positive, broken promise or old hole.

**Installed, 2026-11-10.** The engineer installed both repos' policies, rendered from the install clone at skill 57bca99, and `check_machine` passes in both. Under `agentsh wrap`, in the hub and sssf-sandbox, with new decoy names: listing `~/.claude` or `~/.claude/projects`, and reading `~/.claude/.credentials.json`, `~/.claude.json` or `~/.config/sssf-sandbox/token.env`, are refused. Creating a file in `~/.claude/plugins`, `~/.claude/shell-snapshots` or `~/.local/share/claude/versions`, or next to `~/.claude/settings.json` or `~/.claude.json`, is refused. `claude --version` runs. Claude Code, from a config folder in the repo's sessions folder and signed in from the token file, ran a Bash call and a Read and resumed. The hub session's own scratch files under `~/.claude/jobs` are now unreadable to agents too.

**A real chain, 1ae08aa4, 2026-11-10.** `adw_simple_sdlc.py --worktree` in sssf-sandbox under `local_guard: agentsh`, launched with `CLAUDE_CODE_OAUTH_TOKEN` unset so the runner had to read the token file. It passed for $0.68: plan, build, a review that sent the tests back once, the revision, a second review, verify and docs, six agents in all. Every agent ran from `sessions/1ae08aa4/claude-config`, which holds the run's `.claude.json` and transcripts. The agentsh denials SSSF logged for the run were all refused file opens and connects, none of them execs, so none counted toward the stop: 434 opens of `~/.claude` and about 100 each of `daemon`, `jobs`, `session-env` and `shell-snapshots`, 24 of `plugins`, 12 of `settings.json`, Claude Code's reads of every process's `/proc/<pid>/environ` (refused by `sssf-proc-secrets`), and its telemetry connects. Claude Code carried on past each one.

**The tamper check, 2026-11-10.** The hub session looked for anything an agent could have planted while `~/.claude` and `~/.local/share/claude` were open. All five Claude Code versions in `~/.local/share/claude/versions` (2.1.317 to 2.1.322) match the sha256 and size in their release manifests at `downloads.claude.ai`, and `~/.local/bin/claude` points at 2.1.322. The official plugin marketplace copy matches `anthropics/claude-plugins-official` at the commit its `.gcs-sha` names, file for file, and `settings.json` enables no plugin. The plugins synced from claude.ai hold skills and HTTP MCP servers at their vendors' URLs, with no hooks or scripts. `settings.json` has one hook, the engineer's notification script, and no shell snapshot fetches, decodes or evals anything. Item 13 is done.

## What got built

- A symlink at `<session>/<agent>/envelope.json` to `/proc/<pid>/fd/<n>` or to a main-checkout file is replaced by SSSF's next envelope write, and the target is unchanged. This holds on a `--worktree` run and a plain one.
The first real run must check, beyond the items above:

- The push cases under the real service: `git push`, `git -c a=b push`, `git -C /x push`, `git -c alias.p=push p`, `git -c $'a=b\nc' push` and `git -C $'/x\ny' push` denied; `git commit -m push`, `git stash push`, `git log --grep push`, `git commit -m $'line one\npush it'`, `git log --grep $'x\npush'` and `git -C /x/push-tools status` run; `git -c 'alias.p=!git push' p`, `git -c ALIAS.p=push p` and `git -c 'alias.p=!f(){ git push; }; f' p` are denied; `git -c alias.st=status st` runs.
- A relative `ln -s` in the workspace is denied, and `uv venv` works.
- That the service accepts the rendered config (`render_config`) and starts on it.
- That `set dotenv-load` recipes run under the wrap with `JUST_NO_DOTENV=true`.

## Running the checks

The scripts are in [agentsh/](agentsh/). `proof-workspace.sh` builds the workspace at `~/sssf-agentsh-proof`: a detached worktree of sssf-sandbox holding a `.env` with `FAKE=not-a-secret`, a `.env.example` with `FAKE=`, and a justfile with an added `test kind:` recipe that runs `@echo ran test {{kind}}`. Run the rest from that folder. Set `AGENTSH_CONFIG=/etc/agentsh/sssf.yaml`, `AGENTSH_SERVER=unix:///run/agentsh/agentsh.sock` and `AGENTSH_NO_AUTO=1`, then:

- the network and fronting checks: `agentsh wrap --policy sssf-net --root "$PWD" -- bash net-checks.sh`
- the bypass corpus: `agentsh wrap --policy sssf-proof --root "$PWD" -- bash corpus.sh`
- one real agent: `agent-run.sh claude` or `agent-run.sh pi`, with `guard` added to have it try the denied actions. It also lists the session's network events and denials from the server API

The corpus and the agent runs leave `j`, `e`, `hl` or `agent-*.log` in the worktree if they get that far. To see the copied `just` run, set `JUST_NO_DOTENV=true` first.

### The pin and root run, d48bd070

Run d48bd070, from `specs/tasks/guard-agentsh-pin-and-root.md`, took Next items 1, 10 and 11, and merged as 7ab1149. The hub session's probes on 2026-11-10, on build 05b3777f:

- **The engine's `${PROJECT_ROOT}`.** `ExpandVariables` in the fork's `internal/policy/vars.go` puts the value into a rule as raw text, and gobwas/glob then reads `*?[]{}\` in it as syntax. A test on the engine with a root of `/h/github/repo[old]` allowed writes to `/h/github/repoo` and denied them in the root itself; `re*po`, `a{b,c}`, `q?x` and a backslash did the same to their siblings. SSSF's own escaped paths matched only themselves. `${HOME}` expands the same way.
- **The agent cache.** On this build a link from the cache to `~/.local`, absolute or relative, is refused, so run 130ca6e0's swap is closed. A plain run's agent can still move the cache away and put a link to a workspace folder in its place.
- **The pin.** The service's running binary can't be seen by a user (`/proc/<pid>/exe` is denied), but systemd's `ExecMainStartTimestamp` and the files' change times can.
- **Hardlinks.** A file in the workspace with another link outside it can be written through the workspace link: an append to a decoy hardlinked from `~/.cache/uv` changed the cache copy while a direct write was refused. sssf-sandbox's `.venv` shared 492 files with `~/.cache/uv`, both repos' dashboard `node_modules` 5,176 files with bun's cache, and the hub's `.git` 1,565 object files with the install clone, made by a plain local `git clone`. The engineer re-cloned it with `--no-local` the same day, and the hub's count went to 0. The check is the next run.

What changed: the rendered config ends with a comment naming `BINARY` and `PINNED_SHA256`, so an edit to either refuses as "differs"; `check_machine` refuses when the binary or config changed after the service started; a root or `$HOME` holding any of `*?[]{}\$` refuses; and `start()` makes the agent cache with `os.mkdir` and refuses a link or a file there. `config.md` records the hardlink limit and the pi pattern's miss after a wrapper word.

The first attempt stopped in its bypass review: agentsh's ptrace watchdog logged a tracee stuck in `openat` for 4 seconds with the run loop "not advancing it", and the service stopped answering event polls until it recovered on its own. It's the stall of Next item 2, the first time under Claude Code. The rerun joined the run, and its `commit_plan` committed the first attempt's uncommitted build with the spec, so 2787893 holds the code under a spec message and `commit_build` failed with nothing to commit. 5575ac5 makes `commit_plan` commit only the spec. The two attempts cost $5.85.

Round 1's review found nothing that blocks, in 146 checks. It found a broken promise, that the change-time check follows a link at `BINARY` or `CONFIG`, and two old holes new to the limits: a surviving edit to `SERVER`, `CONFIG` or `POLICY_DIR` isn't in the pin line. Both go into the hardlink run's spec, `specs/tasks/guard-agentsh-hardlinks.md`.

### The hardlink runs, 9497dc7f and b684482d

Both took `specs/tasks/guard-agentsh-hardlinks.md`, Next item 14. Each wrapped launch now walks the workspace and the git common dir, and refuses while a regular file there has a link outside them, naming a few files and how to rebuild each tree. SSSF's own setup, checks and dashboard recipes build with copies: `UV_LINK_MODE=copy`, `npm_config_package_import_method=copy`, and bun's `--backend=copyfile`. `check_machine` refuses a link at `BINARY` or `CONFIG`, and the pin line names every path constant.

Run 9497dc7f was cut off by a WSL restart in its build and rejoined. Its round 1 review found no bypass and one blocking false positive: the walk passes full paths to `lstat` and `scandir`, so a folder chain deeper than PATH_MAX refused every launch, and the hub held one, left by an earlier review. The builder's fix kept one open folder descriptor per level. Round 2 flagged that as a new mechanism, and it refused a tree about 1,100 folders deep under the usual open-file limit of 1,024, so the chain stopped. $7.62.

The engineer chose to restart from round 1's code, `0d430ab` (local branch `keep/9497dc7f-round1`), with only what round 1's review asked for: a path longer than PATH_MAX refuses with its own message, a folder swapped for a symlink before it is listed refuses, the walk's cost is stated as it is, and three known limits. Run b684482d did that. Its first try stopped in the plan phase on the slow poll below; rejoined, it passed round 1 with no bypass, harm or false positive in 137 checks, and merged as cc6a0d1. $4.93, the stopped plan included. Its three broken promises are all in the one-time cleanup steps, and go into the event-store spec. Its two new known limits, a parent swapped for a symlink and a folder renamed during the walk, both need a process changing the tree while the walk runs.

Before the hub's update, the hub session removed what the walk would refuse in the hub: 22 scratch folders in seven runs' bypass reviews, holding 10 unreadable folders and about 1,180 over-long paths, a review's venv linked to uv's cache, and two tool-config probe folders from 2026-11-09. Every run report stayed. The hub's walk took 1.9 seconds, and found only the dashboard's `node_modules`, which the engineer rebuilds.

### The event store, 2026-11-11

Run b684482d's first planner was stopped 31 seconds in, with "the agentsh service stopped answering event polls". There was no watchdog line. The cause was the poll's query:

- `/var/lib/agentsh/events.db` was 32,497,913,856 bytes, three days after the 2026-11-08 install. agentsh has no retention setting for it and no way to skip storing allowed events. The audit log rotates at 512 MB and keeps 10 files, which on 2026-11-10 held about two hours of runs.
- `LiveEvents._poll` asks `/api/v1/events/search` for the session's events with `type=ptrace_execve`. The `events` table indexes `(session_id, ts_unix_ns)` and `(type, ts_unix_ns)`, not both, and with no statistics SQLite took the type index: every `ptrace_execve` event ever stored, filtered to the session. The query took 12.9 seconds cold after the reboot and 1.0 second warm; without `type` it took 0.005 seconds. `_get` gives up after 5 seconds, and three failed polls stop the agent.
- The engineer ran `ANALYZE` on the store, with no run going. The typed query then took 0.01 seconds.
- The journal logged 105,197 "sqlite: event channel full, dropping event" warnings between 2026-11-10 and 2026-11-11, in bursts during runs, up to 3,504 a minute. A dropped `ptrace_execve` denial isn't counted toward `MAX_REFUSALS`; agentsh still enforces it. agentsh opens the store with no busy timeout.

This explains b684482d's stall. Run d48bd070's stall had a ptrace watchdog line and no dropped events, so it may be a second cause. The fix is `specs/tasks/guard-agentsh-events.md`, Next item 15: poll by session only, an hourly root timer that keeps 2 days and runs `ANALYZE` weekly and publishes the store's size, and a warning in `check_machine`. The engineer chose all three on 2026-11-11.

### Status on 2026-11-09: published, opt-in

The default stays `local_guard: text`, in the template and in `config.md`, so publishing the hub changes no repo's guard. A repo turns agentsh on itself, with `local_guard: agentsh` and the engineer's sudo install. Two repos have: the hub since dd07d8f, and sssf-sandbox. Both are Python with Claude Code rosters. Projects update from the local hub, not the fork, so they're one `sssf-update` from this code either way, and still on the text guard after it.

## Next

1. **Done: the pin check's two old holes**, by [run d48bd070](#the-pin-and-root-run-d48bd070). Left for item 14: links at `BINARY` or `CONFIG`, and the other path constants.
2. The stall stays open. It came back on 2026-11-10 under Claude Code, in run d48bd070's first bypass review, and on 2026-11-11 in run b684482d's planner, where [the event store](#the-event-store-2026-11-11) was the cause. If it comes back with a fast poll query, turn the trace on with the drop-in `Environment=AGENTSH_PTRACE_TRACE=1`.
3. The one model-call hang stays open. If it comes back, see whether a bare pi does it too, and whether the proxy still holds the tunnel.
4. The real-push check from step 2, for the engineer: each push spelling in [corpus.sh](agentsh/corpus.sh), from a workspace whose remote points at GitHub, under the installed policy. The network rules should refuse every one.
5. **Done: what agentsh denied ordinary work.** `tar -x` by [the eighth patch](#the-eighth-patch-tar-and-openat2-2026-11-09), relative link targets by [the ninth](#the-ninth-patch-link-targets-2026-11-09), and config lookups and the agents' own caches by [run 130ca6e0](#the-tool-config-run-under-agentsh-130ca6e0).
6. Rollout step 4, now that items 5, 8 and 9 are done: one Claude-roster project turns agentsh on. The engineer runs its sudo install from the install clone, and the project's session runs one chain and reports what agentsh denied. The hub and sssf-sandbox are both Python, so a project with another toolchain tests patterns neither has.
7. pi under the wrap needs its own policy. Until it has one, every project with a pi roster, pi-agent-observability's ChatGPT roster among them, stays on `local_guard: text`.
8. **Done: the DNS proxy's forward**, the tenth fork patch, in build 472d63cf, pinned by run 8a727544 ([the patches](#the-tenth-and-eleventh-patches-dns-and-signals-2026-11-10)).
9. **Done: signals**, the eleventh fork patch in the same build, with `trace.signal` and its rules from run 8a727544. Its last check, a guard run under agentsh, passed as run 130ca6e0 ([the patches](#the-tenth-and-eleventh-patches-dns-and-signals-2026-11-10)).
10. **Done: the example pi pattern's miss** is a known limit in `config.md`, by run d48bd070.
11. **Done: run 130ca6e0's three items.** The raw `${PROJECT_ROOT}` and the agent-cache link by run d48bd070; uv's hardlinks go to item 14.
12. **Done: uv's managed Pythons and folder listing**, by fork build 05b3777f and [run 47b4d0ea](#the-links-and-listing-run-47b4d0ea). Left: pnpm's two `npm_config_*` variables draw a deprecation warning from npm.
13. **Done: the engineer's own Claude Code files.** Agents run from their own config folder with a token, and `~/.claude`, `~/.claude.json` and Claude Code's program are closed to them ([run cea6fe8d](#the-claude-home-run-cea6fe8d)). `/proc/<pid>/cwd` and `cmdline` stay a limit.
14. **Done: the hardlink check**, by [runs 9497dc7f and b684482d](#the-hardlink-runs-9497dc7f-and-b684482d). Left for item 15: the one-time steps' three gaps.
15. **The event store**, `specs/tasks/guard-agentsh-events.md`: poll by session only, prune to 2 days with a root timer that publishes the size, warn in `check_machine`, and fix the hardlink run's one-time steps. After it merges, the engineer moves the 32 GB store aside once.
