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

## Next

1. Wrapped Claude Code and pi agents with `NODE_USE_ENV_PROXY=1`: each must reach its model host and nothing else.
2. `wsl --shutdown` from Windows, then a check that the service is `active` when WSL starts again. It ends this session, so it goes last. Then the service checks: `active` with `CAP_SYS_PTRACE`, a wrapped `bash -c` denies `just test live` and `.env`, and a `kill -9` of the server kills the wrapped agent while the service comes back.
3. The proof in sssf-sandbox: one wrapped Claude Code agent and one wrapped pi agent, and the bypass corpus.
4. The decisions above, settled from the proof.
5. A guard chain run for the wrap.
