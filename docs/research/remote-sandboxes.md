# Remote sandboxes for SSSF runs: Vercel Sandbox and E2B

Researched 2026-11-02. Could SSSF run parts of a run (the `claude -p --output-format stream-json` and `pi` agents, plus shell checks like pytest) inside a remote VM from Vercel Sandbox or E2B? This collects the facts a design session needs. Nothing was signed in to and no sandbox was created. SDK claims come from the published wheels, downloaded from PyPI and read, not run: `vercel-sandbox` 0.10.0 and `e2b` 2.62.0.

## Short answer

Both providers have a Python SDK that covers what SSSF needs: create, run a command with live stdout and stderr, read and write files, set env vars per sandbox or per command, and stop. Both have open outbound internet by default, with egress allowlists if we want them. Both cap a running session on the free tier (Vercel Hobby 45 minutes, E2B Hobby 1 hour), and on both the cap resets when the sandbox stops or pauses and resumes. They differ in three ways that matter to SSSF:

- **What survives the session cap.** Vercel keeps the filesystem (persistent sandboxes snapshot on stop or timeout) but every process dies. E2B kills the sandbox and deletes everything by default. Setting `on_timeout: "pause"` makes it keep memory and running processes too, so a long agent turn could survive the cap if SSSF resumes it.
- **Images.** Vercel's default image already has Node 24, Python 3.14, uv, bun, git, and the `claude` and `pi` CLIs. E2B has prebuilt `claude` and `pi` templates, and custom templates are built from Python code, with no limit on how many.
- **Cost reporting.** Neither offers a per-sandbox cost API. E2B bills per second on allocated vCPU and memory, so SSSF can compute cost from sandbox runtime and size. Vercel bills Active CPU, which a program can't see, and its billing API is daily and per team.

On Claude sign-in, `claude setup-token` makes a one-year, inference-only subscription token, and Anthropic's docs recommend it for CI and scripts. Sandcastle passes it as a sandbox-level env var at create time and never writes it to disk.

## 1. Python SDK

### Vercel

- **Package.** `vercel-sandbox` 0.10.0, uploaded 2026-10-23 ([PyPI](https://pypi.org/project/vercel-sandbox/)). The `vercel` meta-package 0.12.1 (same day) depends on it (`vercel-0.12.1.dist-info/METADATA:20`). Import it as `from vercel import sandbox`, with a sync mirror at `vercel.sandbox.sync` (README in the wheel's METADATA). Requires Python 3.10 or later. The JS SDK `@vercel/sandbox` is at 3.9.1, published 2026-10-28 ([npm](https://www.npmjs.com/package/@vercel/sandbox)).
- **Create.** `create_sandbox(name, image, source, execution_time_limit, resources, persistent, network_policy, env, ...)` (`vercel/sandbox/client.py:83-103`).
- **Run with streamed output.** `create_process(command, args, cwd, env, sudo, kill_after, stdout, stderr)` returns a live process (`_internal/async_runtime.py:1107-1140`). `process.stdout` is a `TextReader` with `readline()`, and async iteration yields one line at a time (`_internal/text_reader.py:73-102`). The docs example is `async for line in process.stdout` ([Working with Sandbox](https://vercel.com/docs/sandbox/working-with-sandbox#run-a-detached-command-and-stream-logs)). stderr gets its own reader, or pass `stderr=subprocess.STDOUT` to merge it into stdout "in arrival order" (`async_runtime.py:1134-1136`). `run_process` waits for the exit instead.
- **Files.** `fs.read_bytes`, `fs.read_text`, `fs.write_bytes`, `fs.write_text` (`_internal/async_runtime.py:621-692`), plus streaming `fs.open` handles ([Working with Sandbox](https://vercel.com/docs/sandbox/working-with-sandbox#prepare-files-and-download-artifacts)).
- **Env vars.** Per sandbox with `env=` on `create_sandbox` (`client.py:95`), and per command with `env=` on `run_process` and `create_process` (`async_runtime.py:1003`, `:1113`).
- **Stop.** `stop()` (`async_runtime.py:1277`). Stopping a persistent sandbox ends the session and keeps the sandbox. `destroy()` deletes it ([Persistence](https://vercel.com/docs/sandbox/concepts/persistent-sandboxes#delete-a-sandbox)).

### E2B

- **Package.** `e2b` 2.62.0, uploaded 2026-10-28 ([PyPI](https://pypi.org/project/e2b/)), source at `github.com/e2b-dev/e2b/tree/main/packages/python-sdk`. Requires Python 3.11 or later. `e2b-code-interpreter` 2.11.6 is a separate layer for notebook-style execution that SSSF doesn't need.
- **Create.** `Sandbox.create(template, timeout, metadata, envs, allow_internet_access, network, lifecycle, ...)` (`e2b/sandbox_sync/main.py:167-183`).
- **Run with streamed output.** `commands.run(cmd, envs=, cwd=, on_stdout=, on_stderr=, background=, timeout=)` (`e2b/sandbox_sync/commands/command.py:234-262`; [Streaming commands](https://docs.e2b.dev/commands/streaming)). The callbacks get decoded text chunks, not lines (`commands/command_handle.py:119-125` runs an incremental UTF-8 decoder over each chunk), so SSSF would have to split lines itself, the way sandcastle's Vercel provider does (`~/github/sandcastle/src/sandboxes/vercel.ts:191-210`). `retain_output=False` stops the SDK holding all output in memory (`command.py:262`). A dropped stream can be reattached with `commands.connect(pid)` ([sandbox lifetime FAQ](https://docs.e2b.dev/faq/sandbox-lifetime#stream-connections)).
- **Files.** `files.read`, `files.write`, `files.write_files` (`e2b/sandbox_sync/filesystem/filesystem.py:118-312`).
- **Env vars.** Per sandbox with `envs=` on create, and per command with `envs=` on `commands.run`, which overrides a global var of the same name for that command only ([Environment variables](https://docs.e2b.dev/sandbox/environment-variables)).
- **Stop.** `kill()` (`main.py:518-560`) or `pause()` (`main.py:789-850`).

## 2. Pause, resume, and the session limit

### Vercel

- **What's kept.** Sandboxes are persistent by default. Stopping one snapshots its filesystem, and the next SDK call starts a new session from that snapshot ([Persistence](https://vercel.com/docs/sandbox/concepts/persistent-sandboxes)). Snapshots hold "the filesystem and installed packages" ([Snapshots](https://vercel.com/docs/sandbox/concepts/snapshots)). Processes are not kept: the docs' `onResume` hook restarts a dev server on every resume ([Persistence, lifecycle hooks](https://vercel.com/docs/sandbox/concepts/persistent-sandboxes#lifecycle-hooks)). There is no memory-preserving pause.
- **Does resume reset the limit?** Yes. "The maximum duration applies to a single session, not to the sandbox itself. The limit resets every time a sandbox stops and resumes" ([Pricing, runtime limits](https://vercel.com/docs/sandbox/pricing#runtime-limits)). Hobby is 45 minutes per session, Pro and Enterprise 24 hours, and the default timeout is 5 minutes (same page).
- **Extend while running.** `extend_execution_time_limit(duration)` (`async_runtime.py:1238`; [Working with Sandbox](https://vercel.com/docs/sandbox/working-with-sandbox#execute-long-running-tasks)). Extensions are "capped at your plan's maximum" ([duration and persistence guide](https://vercel.com/kb/guide/vercel-sandbox-duration-and-persistence)), so Hobby can't go past 45 minutes in one session.
- **Snapshot gotcha.** Calling `snapshot()` by hand stops the sandbox ([Snapshots, create a snapshot](https://vercel.com/docs/sandbox/concepts/snapshots#create-a-snapshot)).

### E2B

- **What's kept.** `pause()` saves the filesystem and memory, "all running processes, loaded variables, data", and `connect()` resumes it ([Persistence](https://docs.e2b.dev/sandbox/persistence)). Network clients are disconnected. `mode="filesystem"` saves only the disk, and resuming then cold-boots it (`main.py:838`). Paused sandboxes are kept indefinitely with no TTL ([Persistence](https://docs.e2b.dev/sandbox/persistence)). Pausing takes about 4 seconds per GiB of RAM and resuming about 1 second (same page).
- **Does resume reset the limit?** Yes. "After a sandbox is paused and resumed, the continuous runtime limit is reset" ([Persistence, continuous runtime limits](https://docs.e2b.dev/sandbox/persistence)). The cap is 1 hour on Hobby and 24 hours on Pro ([Billing](https://docs.e2b.dev/billing#plans)).
- **Extend while running.** `set_timeout(seconds)` resets the timeout to that many seconds from now, up to the plan cap (`main.py:598-609`; [Sandbox lifecycle](https://docs.e2b.dev/sandbox#change-sandbox-timeout-during-runtime)). `connect()` only ever lengthens the remaining time ([Persistence](https://docs.e2b.dev/sandbox/persistence)). disler's skill computes the extension against a 24-hour cap from `get_info().started_at` (`~/github/agent-sandbox-skill/.claude/skills/agent-sandboxes/sandbox_cli/src/modules/sandbox.py:114-142`).
- **Auto-pause.** `lifecycle={"on_timeout": "pause"}` at create makes a timeout pause the sandbox instead of killing it (`main.py:198`; [Persistence, auto-pause](https://docs.e2b.dev/sandbox/persistence)).

## 3. Images with tools preinstalled

### Vercel

- **Default image.** `vercel/sandbox/universal:latest`, built on `vercel/sandbox/ubuntu` (Ubuntu 26.04) ([Images](https://vercel.com/docs/sandbox/concepts/images#vercel-managed-images); [Understanding Sandboxes](https://vercel.com/docs/sandbox/concepts#how-sandboxes-work)). Its README lists Node 24 with pnpm, Python 3.14 with uv, bun, git, git-lfs, gh, and the coding agents opencode, `claude`, `codex`, `pi`, and `fx` ([vercel/sandbox universal image](https://github.com/vercel/sandbox/tree/main/images/universal)). Python is 3.14, not 3.12, so a project pinned to 3.12 would need `uv python install 3.12` or a custom image. Images rebuild nightly and rolling tags pick up new agent releases ([Images, release cadence](https://vercel.com/docs/sandbox/concepts/images#release-cadence)), so pin a digest if run-to-run reproducibility matters.
- **Default user.** The universal image runs as `ubuntu` (uid 1000) with passwordless sudo ([universal image README](https://github.com/vercel/sandbox/tree/main/images/universal)). Commands start in the Dockerfile's `WORKDIR`, or `/` if it sets none ([Images, custom images](https://vercel.com/docs/sandbox/concepts/images#custom-images)). The universal image's working directory: not found. Sandcastle uses `/vercel/sandbox/workspace` (`vercel.ts:23`).
- **Custom image.** Build a Dockerfile and push it to Vercel Container Registry with `vercel vcr build docker . my-repository:latest --push`, then `create_sandbox(image="my-repository:latest")`. It must be `linux/amd64`, and Sandbox ignores `ENTRYPOINT` and `CMD` ([Images, custom images](https://vercel.com/docs/sandbox/concepts/images#custom-images)). The older `runtime` option is deprecated ([Pricing, resource limits](https://vercel.com/docs/sandbox/pricing#resource-limits)).
- **Snapshot as image.** Install once, `snapshot()`, then create from `SnapshotSource(snapshot_id=...)` ([Snapshots](https://vercel.com/docs/sandbox/concepts/snapshots#create-a-sandbox-from-a-snapshot)). Snapshots expire 30 days after last use by default and are tied to one region (same page).
- **Free-tier limits.** Hobby includes 15 GB of snapshot storage for the account's lifetime ([Pricing](https://vercel.com/docs/sandbox/pricing#pricing)). Every stop of a persistent sandbox makes a snapshot that counts against that, so SSSF should pass `persistent=False` or `snapshot_retention=SnapshotRetention(count=1)` ([Persistence](https://vercel.com/docs/sandbox/concepts/persistent-sandboxes#default-snapshot-expiration-and-retention)). Sandcastle passes neither (`vercel.ts:143-169`). VCR on Hobby allows 10 repositories per project and 50 images per repository, and storage costs $0.10 per GB ([VCR limits](https://vercel.com/docs/container-registry/limits-and-pricing)). Whether Hobby gets any free VCR storage: not found.

### E2B

- **Default template.** `base` (`e2bdev/base`) is Debian-based with Python 3, Node.js, Yarn, git, curl, build-essential, and gh ([Base image](https://docs.e2b.dev/template/base-image#default-e2b-base-image)). E2B also ships prebuilt `claude` and `pi` templates with those CLIs installed ([Claude Code](https://docs.e2b.dev/agents/claude-code); [Pi](https://docs.e2b.dev/agents/pi)). What versions those templates carry, and whether they have uv or bun: not found.
- **Default user.** `user`, working in `/home/user`, unlike Docker's root and `/` ([User and workdir](https://docs.e2b.dev/template/user-and-workdir)).
- **Custom template.** The current way is Python code: `Template().from_node_image("22").apt_install([...]).run_cmd(...)`, then `Template.build(template, alias=..., cpu_count=..., memory_mb=...)` ([Template quickstart](https://docs.e2b.dev/template/quickstart)). disler's skill does exactly this to add uv to a Node 22 image (`~/github/agent-sandbox-skill/.claude/skills/agent-sandboxes/build_template.py:96-128`). Base helpers include `from_python_image("3.13")`, `from_bun_image("1.3")`, and `from_template("pi")` ([Base image](https://docs.e2b.dev/template/base-image#predefined-base-images); [Pi, custom template](https://docs.e2b.dev/agents/pi)). An `e2b.Dockerfile` is the legacy format. `from_dockerfile()` parses one, without multi-stage support ([Base image, parsing Dockerfiles](https://docs.e2b.dev/template/base-image#parsing-existing-dockerfiles); [V2 migration](https://docs.e2b.dev/template/migration-v2)).
- **Free-tier limits.** "There's currently no limit on the number of templates". Hobby builds get at most 8 vCPUs, 8 GiB of memory, and 10 GiB of disk, with 20 concurrent builds ([Template quickstart, build limits](https://docs.e2b.dev/template/quickstart#build-limits)). Paused sandbox retention is unlimited on every plan ([Billing](https://docs.e2b.dev/billing#plans)).

## 4. Network

### Vercel

- **Default.** `allow-all`, "unrestricted access to the public Internet" ([Firewall](https://vercel.com/docs/sandbox/concepts/firewall#allow-all)). So api.anthropic.com and chatgpt.com are reachable with no setup.
- **Controls.** `deny-all`, or a user-defined allowlist by domain (SNI-matched, wildcards allowed) and CIDR. A policy can be changed on a running sandbox. Credential brokering can inject an `Authorization` header outside the VM, so the secret never enters it ([Firewall](https://vercel.com/docs/sandbox/concepts/firewall)). The full firewall is on Hobby ([changelog](https://vercel.com/changelog/full-sandbox-egress-firewall-now-available-on-hobby-plan)). A firewall that terminates TLS (for brokering) adds a per-sandbox CA, and Vercel sets `NODE_EXTRA_CA_CERTS`, `SSL_CERT_FILE`, and similar env vars to point at the system bundle ([Understanding Sandboxes, proxy CA](https://vercel.com/docs/sandbox/concepts#proxy-ca-certificates)).

### E2B

- **Default.** "Every sandbox has outbound access to the internet by default" ([Internet access](https://docs.e2b.dev/network/internet-access)).
- **Controls.** `allow_internet_access=False`, or `network={"allow_out": [...], "deny_out": [...]}` with IPs, CIDRs, and domains. Domain filtering works only on ports 80 and 443 and needs `deny_out` set to all traffic. `update_network` changes it on a running sandbox. Per-host rules can inject headers from stored secrets through the egress proxy (same page; `main.py:620`). Hobby allows 10 secrets per project ([Billing](https://docs.e2b.dev/billing#plans)).

## 5. Usage and cost reporting

### Vercel

- **No per-sandbox usage in the SDK.** The session state has `started_at`, `stopped_at`, and `vcpus` (`_internal/state.py:56-62`) but no CPU or cost fields. A search of the wheel for Active CPU, usage, or billing fields found nothing.
- **Billing API.** `GET /v1/billing/charges` returns FOCUS 1.3 JSONL per team at 1-day granularity, with `ServiceName`, `BilledCost`, and project tags. It doesn't break charges down by sandbox ([List FOCUS billing charges](https://vercel.com/docs/rest-api/billing/list-focus-billing-charges)). Whether it works on Hobby, and how late the data arrives: not found.
- **Dashboard.** The Usage page shows Active CPU, provisioned memory, data transfer, creations, and snapshot storage ([Working with Sandbox, monitor](https://vercel.com/docs/sandbox/working-with-sandbox#monitor-your-sandbox)).
- **Estimating it ourselves is hard.** Memory is billed on wall-clock time, which SSSF knows. Active CPU counts only time spent on the CPU, not waiting on model calls ([Pricing, Active CPU](https://vercel.com/docs/sandbox/pricing#active-cpu)), and SSSF can't see that from outside.

### E2B

- **No cost API.** Usage and cost are in the console usage tab ([Billing, monitoring usage](https://docs.e2b.dev/billing#monitoring-usage)). Budget limits are set in the console (same page, FAQ).
- **What the API gives.** `get_metrics()` returns CPU, memory, and disk use every 5 seconds (`main.py:753-786`; [Metrics](https://docs.e2b.dev/sandbox/metrics)). The lifecycle events API at `api.e2b.app/events/sandboxes/{id}` lists created, paused, resumed, and killed events, kept for 7 days ([Lifecycle events API](https://docs.e2b.dev/sandbox/lifecycle-events-api)).
- **Computing it ourselves is easy.** Billing is per second on allocated resources, $0.000014 per vCPU-second and $0.0000045 per GiB-second, on any plan ([Pricing](https://e2b.dev/pricing)). Paused and killed sandboxes cost nothing ([Billing, FAQ](https://docs.e2b.dev/billing#faq)). So running seconds times `cpu_count` and `memory_mb` from `get_info()` gives the cost. This is the same list-price approach SSSF uses for Pi.

## 6. Claude Code sign-in in a VM

- **What `claude setup-token` makes.** "a one-year OAuth token", printed to the terminal and not saved anywhere. It "requires a Pro, Max, Ultra, Team, or Enterprise plan" and "can only make model requests", so no Remote Control or claude.ai connectors ([Authentication, generate a long-lived token](https://code.claude.com/docs/en/authentication#generate-a-long-lived-token)).
- **How `claude -p` uses it.** Set `CLAUDE_CODE_OAUTH_TOKEN`. It ranks fifth in auth precedence, below `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY`, and `apiKeyHelper` ([Authentication precedence](https://code.claude.com/docs/en/authentication#authentication-precedence)). So a stray `ANTHROPIC_API_KEY` in the VM would override it, and in `-p` mode "the key is always used when present" (same section). `--bare` doesn't read the token ([Authentication](https://code.claude.com/docs/en/authentication#generate-a-long-lived-token)). After a 401, Claude Code keeps sending the env token instead of falling back to a stored login ([Errors](https://code.claude.com/docs/en/errors#oauth-token-revoked-or-expired)).
- **Revocation.** The docs say an env token can be "revoked" and show the error you get (`OAuth token revoked`) ([Errors](https://code.claude.com/docs/en/errors#oauth-token-revoked-or-expired)), but no Anthropic page I found says how to revoke one. Issues on the `anthropics/claude-code` repo report that the only way is claude.ai/settings/claude-code, one token at a time, and that `setup-token` has no list or revoke option ([#48373](https://github.com/anthropics/claude-code/issues/48373), [#57400](https://github.com/anthropics/claude-code/issues/57400)). Those are user reports, not docs. Unverified.
- **Remote and CI machines.** Anthropic presents the token for exactly this: "For CI pipelines, scripts, or other environments where interactive browser login isn't available" ([Authentication](https://code.claude.com/docs/en/authentication#generate-a-long-lived-token)). The GitHub Actions docs use it as a repo secret, and warn that it is "tied to the subscription of the person who ran `claude setup-token`", so an API key fits a secret shared across repos better ([GitHub Actions](https://code.claude.com/docs/en/github-actions)). The legal page says OAuth is for subscribers' "ordinary use of Claude Code", that advertised Pro, Max, and Ultra limits "assume ordinary, individual usage of Claude Code and the Agent SDK", and that developers may not "route requests through Free, Pro, Max, or Ultra plan credentials on behalf of their users". It also says a user may sign in to the unmodified binary with their own subscription "including where a platform hosts Claude Code" ([Legal and compliance](https://code.claude.com/docs/en/legal-and-compliance#authentication-and-credential-use)). My reading is that one engineer running their own unmodified `claude` binary in their own VM, on their own token, is inside those terms. Pooling one token across other people's runs is not.
- **How sandcastle passes it.** As an env var given to the sandbox, not a file. `init` writes a `.sandcastle/.env.example` with an empty `CLAUDE_CODE_OAUTH_TOKEN=` and tells the user to run `claude setup-token` (`~/github/sandcastle/src/InitService.ts:415-419`, `:650`). At run time `resolveEnv` reads `.sandcastle/.env`, falling back to `process.env` only for keys declared there (`src/EnvResolver.ts:52-56`), and merges it with provider env (`src/run.ts:619-626`). The Vercel provider passes the merged map as `env` to `Sandbox.create` (`src/sandboxes/vercel.ts:159-160`), so it is set for the whole sandbox, not per exec. Its `exec` passes no env (`vercel.ts:219-240`). Sandcastle has no E2B provider. Its isolated providers are Vercel and Daytona (`src/sandboxes/`).

## 7. When the session limit hits mid-command

### Vercel

- The session stops: "When you call `stop()` or the timeout expires, the session stops. For a persistent sandbox (the default), Vercel captures its filesystem and creates a snapshot automatically before the sandbox shuts down. Non-persistent sandboxes skip this and discard their filesystem" ([Snapshots, lifecycle](https://vercel.com/docs/sandbox/concepts/snapshots#how-snapshots-fit-the-sandbox-lifecycle)).
- So with a persistent sandbox the files survive, and the next SDK call resumes from them. The running command dies with the VM, since a session is "a single running VM instance" ([Persistence, sandboxes and sessions](https://vercel.com/docs/sandbox/concepts/persistent-sandboxes#sandboxes-and-sessions)). A non-persistent sandbox loses everything.
- What the Python SDK raises on a stream that is open when the session ends: not found. The wheel defines `SandboxStreamError`, `SandboxTimeoutError`, and `SandboxTerminalStateError` (`_internal/errors.py:25-90`), but no doc ties one to this case.

### E2B

- With the default `on_timeout: "kill"`, "terminates the sandbox and releases its resources. A killed sandbox cannot be resumed, and its state is gone" ([sandbox lifetime FAQ](https://docs.e2b.dev/faq/sandbox-lifetime#what-happens-when-the-timeout-expires)). Files and processes are lost.
- With `on_timeout: "pause"`, the filesystem and memory are saved, running processes included (same page). Under a snapshot backlog E2B retries for about 2 minutes and then falls back to a filesystem-only pause, which loses memory ([Persistence, auto-pause](https://docs.e2b.dev/sandbox/persistence)).
- A command stream that drops because the sandbox reached its end raises `TimeoutException` in Python ([FAQ, stream connections](https://docs.e2b.dev/faq/sandbox-lifetime#stream-connections)).

## What this means for SSSF

- SSSF runs often go past 45 minutes. On Vercel Hobby, each agent turn has to finish inside one session, and the run state has to live on disk (it already does, in `adws/adw_data/`) so a new session can pick it up. On E2B with `on_timeout: "pause"`, SSSF could resume a paused turn, but a `claude -p` process paused mid-request may not survive its HTTP connection being dropped. Unverified.
- Sync the repo in and out with git, the way sandcastle does: a `git bundle` in (`src/syncIn.ts:1-7`), and `git format-patch` then `git am --3way` out, plus a copy of untracked files (`src/syncOut.ts:12-14`, `:296`, `:368`). Neither provider needs anything more.
- Pass `CLAUDE_CODE_OAUTH_TOKEN` per command (`env=` or `envs=` on the exec call), not per sandbox, so only the agent process sees it and `pytest` doesn't. Both SDKs allow this. Make sure `ANTHROPIC_API_KEY` is unset in the image.
- For cost, E2B is a clean fit with SSSF's list-price ledger. On Vercel, SSSF could record wall time and memory and treat Active CPU as unknown.

## Not found

- Vercel: the working directory of the universal image.
- Vercel: free VCR storage on Hobby, if any.
- Vercel: whether `GET /v1/billing/charges` works on Hobby, and its data latency.
- Vercel: which Python SDK exception a command stream raises when the session times out.
- E2B: the tool versions in the prebuilt `claude` and `pi` templates, and whether they include uv or bun.
- Anthropic: an official way to revoke a `setup-token` token. Only GitHub issue reports point to claude.ai/settings/claude-code.
