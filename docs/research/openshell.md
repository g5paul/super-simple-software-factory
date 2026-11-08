# NVIDIA OpenShell as a sandbox for SSSF's agents

Researched 2026-11-07, from OpenShell's docs for v0.1.7 and its README, then [tested](#the-test) on this WSL2 machine. The container drivers can't run here. The microVM driver passed all three questions.

The question: would OpenShell have solved the problems behind SSSF's reactive guards ([concepts.md](../concepts.md#guards)), so that fewer of them are needed?

Sources:

- Overview: https://docs.nvidia.com/openshell/about/overview
- README: https://github.com/NVIDIA/OpenShell
- Architecture: https://docs.nvidia.com/openshell/latest/about/architecture
- Policies: https://docs.nvidia.com/openshell/latest/how-it-works/policies/overview
- Providers: https://docs.nvidia.com/openshell/latest/how-it-works/providers/overview
- Sandboxes: https://docs.nvidia.com/openshell/latest/how-it-works/sandboxes/overview
- Support matrix: https://docs.nvidia.com/openshell/latest/about/support-matrix

## Short answer

Partly. OpenShell would stop the harm an agent can do outside the repo, and stop it better than SSSF's pattern rules: no network route to GitHub means no push, however the command is spelled. It does nothing for the harm an agent can do inside the repo it was given, which is what the `writes` check and spec protection judge. It fits SSSF as a third sandbox kind beside Vercel and E2B, not as a replacement for the guards.

## What it is

OpenShell runs each agent in a sandbox and "instruments the kernel to enforce policy on every file access, system call, and network connection at runtime" (README). Three parts (Architecture):

- The **gateway** is the control plane. It creates and deletes sandboxes, hands out policy and attaches providers.
- The **supervisor** sits on the trusted side. It "checks requests against policy, supplies credentials, resolves DNS, opens approved connections."
- The **workload** is the sandbox that holds the agent. It "never makes policy decisions."

The workload runs as one non-root user with no Linux capabilities. Landlock limits its files, and seccomp user notification stops each TCP open and DNS query for the supervisor to judge. Outside that, the sandbox has no network of its own: a Docker or Podman workload container has networking turned off, and a microVM has no network device (Architecture).

## The policy

A policy file has five sections (Policies):

| Section | What it limits | When it applies |
|---|---|---|
| `filesystem_policy` | paths the sandbox may read, or read and write, through Landlock | fixed at start |
| `landlock` | whether the sandbox still starts if Landlock can't apply | fixed at start |
| `process` | the user and group the sandbox runs as | fixed at start |
| `network_policies` | which destinations each binary may reach, and which requests it may send | changeable while running |
| `network_middlewares` | further inspection or blocking of allowed traffic | changeable while running |

"OpenShell denies every outbound connection from a sandbox unless a rule in `network_policies` allows it" (Policies). The docs describe limits on which binary may reach which host, but no allowlist of commands an agent may run.

## Credentials

"The agent process inside the sandbox never sees real credential values. At startup, OpenShell replaces each credential with an opaque placeholder token in the agent's environment" (Providers). The supervisor swaps in the real value only on a request that both the network policy and the credential's binding allow. A mismatch returns HTTP 403 with `credential_endpoint_mismatch`.

Two limits matter for SSSF:

- The `claude-code` provider reads `ANTHROPIC_API_KEY` from console.anthropic.com, not a subscription token (Providers). SSSF signs agents in with the subscription, so the `claude setup-token` token would go in as a plain `--env` value, which the agent can read (Sandboxes). That's no worse than SSSF's VM mode today, but the placeholder protection wouldn't cover it.
- pi isn't mentioned anywhere. It would need its own image and provider profile, and pi's ChatGPT sign-in, which SSSF feeds with a one-hour token from the host broker, is untried.

## Against each guard

| Guard | Would OpenShell solve it? |
|---|---|
| Command rules against `git push`, `gh` writes and recipes that bill an API key | Yes, and better. With no route to GitHub or a billing API, the command fails however it's written. Every rule bypass SSSF hit, from `git stash push` to three heredoc tricks, came from matching command text. A network block doesn't read text. |
| Secret files and `.env` keys | Yes. An upload from a git repo skips gitignored files by default (Sandboxes), so `.env` never goes in, and keys arrive as placeholders. |
| The open forgery and regex items in [open.md](../open.md) | Mostly moot. An agent could still forge events into its harness's output stream, but with nothing outside the sandbox in reach, a forged refusal hides less. |
| The git-directory guard | Yes, if only a diff or commits come back. A hook planted in the sandbox's `.git` never reaches the engineer's repo. SSSF's VM mode already works this way. |
| The `writes` allowlist, spec protection, undoing an agent's commits | No. These judge what the agent did to the code it was given. Landlock grants are additive, so a folder can't be made read-only inside a writable workspace, and the check runs on the result either way. |
| The pre-push hook | Still needed. It also scans the engineer's own pushes for secrets. |
| The guard-change chain | Smaller. Less guard code means fewer changes to review. |

## What stands in the way

- **WSL2 is experimental.** The support matrix lists "Windows WSL 2 with Docker Desktop, x86_64" as experimental. Linux and macOS are supported. The microVM runtime needs KVM.
- **Runtime versions.** It needs Docker 28.0 or later, or Podman 5.x. This machine has Podman 4.9.3, and Docker Desktop on Windows isn't connected to the WSL distro. It does have `/dev/kvm`, and the kernel has Landlock and seccomp filters built in.
- **Age.** It's at 0.1.x, with stable releases "generally every week". Some interfaces are marked experimental and "may change or be removed in a patch release" (Support matrix).
- **Telemetry** is on by default, with anonymous counts. `OPENSHELL_TELEMETRY_ENABLED=false` on the gateway turns it off (README).
- **Copy in, copy out.** A workspace is uploaded, with `--upload` or `openshell sandbox upload`, and results are downloaded; there's no host mount. `--upload` "cannot yet be combined with a trailing main command", so a run is create, upload, then `exec` (Sandboxes). That suits SSSF's remote-sandbox path, which already copies in and returns commits.
- **What a refused call looks like to the agent** isn't documented, apart from the credential 403.

## Where it would fit

The reactive guards exist because SSSF's default mode runs agents on the engineer's machine, with its network and files. `open.md` already names the fix for an agent set on getting past them: "a VM, or agents under their own user". OpenShell is a local, free form of that. It would join Vercel and E2B as `--sandbox openshell`, and in that mode the rules against harm outside the repo could be dropped instead of patched. The `writes` check, spec protection and the pre-push hook would stay in every mode.

The design is [openshell-sandbox.md](../designs/openshell-sandbox.md).

## The test

Three questions decide whether it's worth building, run in sssf-sandbox:

1. Does OpenShell run on this WSL2 machine, with Podman or Docker or the microVM runtime?
2. Can a Claude Code agent in it sign in with the `claude setup-token` token and finish a task on an uploaded copy of the repo?
3. What does the agent see when a policy blocks `git push`, and when it tries to read a gitignored `.env`?

### Results, 2026-11-07

The container drivers failed question 1, for a reason that holds for any container on this WSL2 machine. The microVM driver then passed all three; see [the microVM run](#the-microvm-run).

- **Install without root works.** The release ships plain tarballs for the CLI, gateway, supervisor and VM driver. Unpacked into `~/.local/opt/openshell/bin`, each matched NVIDIA's published SHA-256. The official `install.sh` instead runs `sudo apt-get install` and adds a systemd user service. Nothing needs a fork of OpenShell.
- **The gateway runs on Podman 4.9.3**, though the docs ask for 5.x. It needs a schema-version-2 config (`[openshell] version = 2`), and sandboxes need the gateway's TLS and per-sandbox tokens: `openshell-gateway generate-certs --output-dir ~/.local/state/openshell/tls`, then the gateway with `OPENSHELL_LOCAL_TLS_DIR` set, as the packaged service does. A plaintext gateway with `allow_unauthenticated_users` starts but refuses to launch sandboxes ("Podman sandbox launch authentication is required").
- **The sandbox dies at its seccomp probe.** The workload container exits with `seccomp notification probe: Resource busy (os error 16)`. The cause is WSL2, not Podman: every process here, `/sbin/init` included, already runs under one seccomp filter, and a test filter that allows everything installs fine without a listener and fails with "Device or resource busy" with `SECCOMP_FILTER_FLAG_NEW_LISTENER`. The kernel allows one listener per process chain, WSL's init holds it, and every container on this machine descends from init. OpenShell's network enforcement depends on that listener, so its Podman and Docker drivers can't work here. That's likely why the support matrix marks WSL2 experimental.
- **The microVM driver is the way left.** A libkrun guest runs its own kernel, so WSL's filter doesn't reach inside it. `/dev/kvm` is `root:kvm` mode 0660 and this user isn't in the `kvm` group; the engineer ran `sudo chgrp $USER /dev/kvm`, which lasts until WSL restarts.

### The microVM run

The gateway ran with `compute_driver = "vm"`. A sandbox came up in about 35 seconds the first time, most of it turning the image into a root disk, running kernel 6.12.109 as user `ubuntu`. OpenShell doesn't build images, so the test image was built with Podman: the default `nvcr.io/nvidia/base/ubuntu:24.04` plus git, curl, python3, Node and Claude Code 2.1.319. The driver reads the local image directly.

The policy allowed one binary, Claude Code's native `.../claude-code/bin/claude.exe`, to reach `api.anthropic.com:443`, and nothing else. The sandbox was created with `--upload ~/github/sssf-sandbox:/sandbox/repo` and `--env-from CLAUDE_CODE_OAUTH_TOKEN`.

1. **Sign-in and a task: yes.** `claude -p ... --permission-mode bypassPermissions` signed in with the setup-token, wrote a file in the uploaded copy and answered in 3 turns for $0.029. `openshell sandbox download` brought the file back.
2. **A blocked push.** `git push` over HTTPS failed at once: "Failed to connect to github.com port 443 after 1 ms: Couldn't connect to server". `curl` to GitHub failed the same way, and so did `curl` to `api.anthropic.com`, since only `claude.exe` may go there. The agent, told to run the curl, quoted the error and said it couldn't reach github.com. Nothing tells the agent a policy blocked it; it looks like a refused connection. `ssh` isn't in the image, so a push over SSH had nothing to run.
3. **A gitignored `.env`: kept out.** sssf-sandbox has one, and the upload left it behind. The upload also leaves out `.git`: the sandbox gets the files, no history.

What the run also showed:

- **Every denial is logged** with the binary and destination, as structured OCSF lines from `openshell logs <name> --source sandbox`, such as `NET:OPEN [MED] DENIED /usr/bin/curl(0) -> github.com:443 [reason:transparent_tcp_policy_denied]`. Claude Code's own telemetry call to Datadog was denied the same way, which did no harm. SSSF could read these lines to record a refusal in its trace, instead of matching command text.
- **The token is readable.** The sandbox's environment held the real token, as expected for `--env-from`; only provider credentials get placeholders. An agent could print it, though with no route but `api.anthropic.com` it could only use it there.
- **No proxy variables are set.** Connections are caught in the kernel and staged to the supervisor, so a program that ignores `HTTPS_PROXY` is held all the same. OpenShell sets `NODE_EXTRA_CA_CERTS` and `SSL_CERT_FILE` for its TLS inspection.

For SSSF, that means a `--sandbox openshell` run would follow the Vercel and E2B path: create, upload the worktree, run each agent with `exec`, and bring the commits back. Since no `.git` goes in, the run would need to send a bundle or init a repo inside, as the VM mode does.

A second run checked SSSF's host paths. A sandbox has no root, so the upload couldn't create `/home/gpaul`. An image that creates `~/github` owned by the sandbox user, with a `read_write` rule on it, let the tree, a run's handoff folder and `git` work at their host paths, while a write elsewhere in the home folder was refused.

Left on the machine for the next try: the binaries in `~/.local/opt/openshell/bin`, the certs in `~/.local/state/openshell/tls`, the test config in `~/.config/openshell-test/`, the CLI's gateway entry `sssf-test`, a Podman network named `openshell`, the test images `localhost/sssf-openshell:test` and `:paths`, two policies in `~/.config/openshell-test/` and the VM driver's state under `~/.local/state/openshell/`. No gateway or sandbox is running.
