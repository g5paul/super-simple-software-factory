# NVIDIA OpenShell as a sandbox for SSSF's agents

Researched 2026-11-07, from OpenShell's docs for v0.1.7 and its README. None of it has been run here yet; [the test](#the-test) below is the next step.

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

## The test

Three questions decide whether it's worth building, run in sssf-sandbox:

1. Does OpenShell run on this WSL2 machine, with Podman or Docker or the microVM runtime?
2. Can a Claude Code agent in it sign in with the `claude setup-token` token and finish a task on an uploaded copy of the repo?
3. What does the agent see when a policy blocks `git push`, and when it tries to read a gitignored `.env`?

Results go below as they come in.
