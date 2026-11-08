# pi agents under `--sandbox openshell`

Researched 2026-11-08, from the hub's code at 6e680bc, the host's pi 1.4.0 install, the OpenShell 0.1.7 CLI's `--help`, and OpenShell's docs. Nothing was built or run. The question: what would it take to run SSSF's pi agents, signed in to ChatGPT, under `--sandbox openshell`, which refuses them today?

Paths below are short forms. `M/` is `.claude/skills/sssf/templates/adws/adw_modules/`, `PI/` is the host's pi install at `~/.nvm/versions/node/v24.11.1/lib/node_modules/@earendil-works/pi-coding-agent/`, and `PIAI/` is its bundled `PI/node_modules/@earendil-works/pi-ai/dist/`.

## Short answer

It's a small build, and most of the plumbing already works. The token already crosses the OpenShell stdin frame the way it does for Vercel and E2B. The obs relay uses the VM's loopback, which OpenShell allows. What's missing is pi itself, plus ripgrep and fd, in the image, a network rule for `api.openai.com`, and lifting the refusal in `check_roster`.

I found one real risk, and it isn't specific to pi. OpenShell matches a rule's binary against the connecting process and every one of its parents. pi is a Node script, so its rule has to name `node`. That lets any Node process, and anything a Node process starts, reach `api.openai.com`. The same parent matching already applies to Claude Code. Every command a Claude agent runs descends from `claude.exe`, so each one may reach `api.anthropic.com` today, which goes further than the design and the research say. I don't think this blocks the build. The rule opens one model host, as the Claude rule does, and Vercel and E2B leave the network wide open. It does need two engineer decisions: add the rule only for rosters with a pi agent, and narrow it to the request paths pi uses.

## 1. What a pi agent needs in a Vercel or E2B VM today

### Binaries

- Node at 22.19.0 or later, which is pi's `engines` floor (`M/sandbox_remote.py:100`, `PI/package.json:95-97`), and pi at 1.4.0 or later, the first version with `auth print-bearer-token` (`M/sandbox_remote.py:101`). The tool check reads both before any agent starts and fails the run if one is missing (`M/sandbox_remote.py:510-532`). `tools_needed` asks for them only when the roster has a pi agent (`M/sandbox_remote.py:111-114`).
- E2B's template pins Node 24.11.1 and pi 1.5.0 (`.claude/skills/sssf/scripts/e2b_template.py:26-27`, `:36-39`). Vercel's default image brings its own unpinned pi (`docs/designs/remote-sandboxes.md:173`, `:233`). Run 7e085166 found pi 1.4.0 and Node 24.22.1 there (`docs/designs/remote-sandboxes.md:265`).
- pi is a Node script. Its `bin/pi` resolves to `dist/bundle/cli.js`, whose first line is `#!/usr/bin/env node` (checked with `readlink -f` and `head -1` on the host install).
- ripgrep and fd. pi's `grep` and `find` tools look for `rg`, and for `fd` or `fdfind`, on `PATH`. When they're missing, pi downloads them from GitHub releases (`PI/dist/utils/tools-manager.js:101`, `:244`, `:301-326`). The pi rosters list both tools (`~/github/pi-agent-observability/adws/adw_sssf_config/sssf.config.yaml:15-21`, `~/github/sssf-sandbox/adws/adw_sssf_config/sssf.config.chatgpt.yaml:19-20`). Neither VM image installs them, so on Vercel and E2B pi has presumably been downloading them through the open network. I haven't confirmed that.

### Files

- The clone at the host worktree's path. The run's session folder, where pi writes `<session>/<agent>/pi_sessions`, is created and chowned to the VM user before the clone (`M/sandbox_remote.py:539-543`).
- SSSF's refuse extension `sssf_rules.ts`, which `_in_vm` copies in at its host path through `tree.install` before each pi call (`M/agent_pi.py:260`, `:590`; `M/sandbox_remote.py:739-752`). pi loads it last (`M/agent_pi.py:676-678`).
- The roster's `harness_engineering` extensions. Each must be a file committed at the run's starting commit, and pi loads it from the clone (`M/sandbox.py:175-224`).
- No `auth.json`. pi creates an empty `~/.pi/agent/auth.json` and `models-store.json` on first use, and the token is in neither (`docs/research/pi-chatgpt-in-vm.md:141`). pi needs a writable home folder for this.

### Env vars and the token

- `agent_pi.run` builds the env with `child_env` and `_with_refusals`, then `_in_vm` (`M/agent_pi.py:698-703`). `_in_vm` drops `OBS_AUTH_TOKEN`, sets `OBS_DISABLE=true`, and sets `SSSF_PI_TOKEN` from `vm_token()` (`M/agent_pi.py:574-594`).
- `vm_token()` runs `pi auth print-bearer-token --provider openai --min-expiry 35m` on the host (`M/agent_pi.py:364-383`, `VM_TOKEN_MINUTES` at `:264`). It runs once per pi call. pi refreshes the host login under its own lock when it has to, so the refresh token never leaves the host (`docs/research/pi-chatgpt-in-vm.md:138`). An access token lasts an hour (`docs/research/pi-chatgpt-in-vm.md:44`).
- `Remote._env` passes only `SSSF_` and `OBS_` variables, never `OBS_AUTH_TOKEN`. It renames `SSSF_PI_TOKEN` to `OPENAI_API_KEY`, so a host API key never crosses (`M/sandbox_remote.py:52-53`, `:824-835`).
- `Remote.start` adds `CLAUDE_CODE_OAUTH_TOKEN` to every agent's env, pi agents included (`M/sandbox_remote.py:616`). `open` requires the Claude token file even for a roster with no Claude agent (`M/sandbox_remote.py:493`, `:151-159`). So a pi agent in a VM holds the Claude token too. That's true on all three providers.
- `prepare` checks that the host can hand out a ChatGPT token before anything is created (`M/sandbox.py:151-153`).

### Hosts pi reaches

- Inference goes to `https://api.openai.com/v1` (`PIAI/providers/openai.js:11`). pi takes a key that doesn't start with `sk-` as a ChatGPT sign-in token for that base URL (`PIAI/api/openai-responses.js:26-31`). The request goes through the OpenAI SDK client (`PIAI/api/openai-responses.js:241`), which means `POST /v1/responses` with a streamed response.
- `chatgpt.com` appears in the `openai` route only as the usage page in an error message (`PIAI/api/openai-responses.js:18`, `M/agent_pi.py:35`). Its other uses are in the `openai-codex` provider, which SSSF doesn't use in a VM. `auth.openai.com` is used for refresh, and that happens on the host.
- pi's own calls to `pi.dev`: the version check (`PI/dist/utils/version-check.js:4`) and the install report (`PI/dist/modes/interactive/interactive-mode.js:1017`) run only in interactive mode. The remote model catalog (`PI/dist/core/remote-catalog-provider.js:5`) refreshes over the network only when `allowModelNetwork` is true (`PI/dist/core/model-runtime.js:100`). The `-p` path never sets it (`PI/dist/core/agent-session-services.js:57-61`, `:111`). So a `pi -p` call reaches only `api.openai.com`, plus GitHub when `rg` or `fd` is missing. This is from reading 1.4.0. Version 1.5.0, which E2B runs, wasn't read.
- `PI_OFFLINE=1` turns off pi's startup network calls and the tool downloads (`PI/dist/main.js:455-459`, `PI/dist/utils/tools-manager.js:13-18`, `:308-311`). It also sets `PI_SKIP_VERSION_CHECK`. Inference isn't affected. SSSF doesn't set it.

### The obs extension and relay

- A relay starts only for a pi call in a VM whose roster loads at least one extension, when the host has `OBS_AUTH_TOKEN` and `OBS_DISABLE` isn't `true` (`M/agent_pi.py:558-571`, `docs/designs/remote-sandboxes.md:108`).
- `Remote._open_relay` writes `sssf_obs_relay.cjs` under `/tmp/sssf-obs-<hex>` and starts it under `setsid -w`, with its `--follow` mode as the live stream (`M/sandbox_remote.py:71-81`, `:639-667`). It sets the agent's `OBS_SERVER_URL` to `http://127.0.0.1:<port>` and drops `OBS_DISABLE` (`M/sandbox_remote.py:617-619`).
- The relay is dependency-free Node and listens on the VM's loopback (`M/sssf_obs_relay.cjs:1-19`). The host follows the stream, sweeps the file after the call, and posts to the engineer's obs with the host's token (`M/obs_relay.py:1-23`, `M/sandbox_remote.py:679-712`). The VM makes no outbound connection for obs.

## 2. What the OpenShell path lacks

### The image

`containerfile()` installs git, curl, ca-certificates, python3, xz-utils, Node 24.11.1 from nodejs.org into `/usr/local`, uv, the repo's Python, and `@anthropic-ai/claude-code` (`M/sandbox_openshell.py:124-146`, `NODE` at `:50`). It doesn't install pi, ripgrep or fd. The tool check would fail a pi roster with "pi is missing" even with the refusal lifted (`M/sandbox_remote.py:101`, `:530`). The tag hashes the Containerfile (`M/sandbox_openshell.py:149-150`), so any change gives every repo a new tag, and each must run `just openshell-setup` again (`M/sandbox_openshell.py:203-212`).

### The network policy

The policy has two fixed rules, `anthropic_api` and `package_registries`, plus one per `sandbox_allow` entry (`M/sandbox_openshell.py:153-174`). `anthropic_api` allows `api.anthropic.com:443`, `protocol: rest`, `access: full`, for one binary, `CLAUDE_EXE` (`M/sandbox_openshell.py:53`, `:163`). That's the real path of Claude Code's native executable. The registries rule allows read-only GETs from any binary, `ANY_BINARY = "/**"`, which is still unproven (`M/sandbox_openshell.py:54-56`, `:159-162`; `docs/designs/openshell-sandbox.md:97`). No rule names `api.openai.com`, so pi's model calls would be denied. GitHub is closed (`docs/designs/openshell-sandbox.md:32`), so pi's `rg` and `fd` downloads would be denied too.

A repo could add `node` to `api.openai.com` itself through `sandbox_allow`, which takes a binary, host, port and access (`M/sandbox_openshell.py:165-167`, `M/repo_settings.py:27`, `:60-61`). That does nothing while `check_roster` refuses pi.

### Env and the token

There's nothing to add here. `_VM.create_process` sends the env as `KEY=<base64>` lines on stdin, ended by a `.` line, and `_SHELL` exports each value before it `exec`s the agent (`M/sandbox_openshell.py:258-275`, `:351-358`). The exec checks found that a value sent this way shows up on no command line on either side (`docs/designs/openshell-sandbox.md:69`). `OPENAI_API_KEY` would ride the same frame as `CLAUDE_CODE_OAUTH_TOKEN`. OpenShell sets `NODE_EXTRA_CA_CERTS` for its TLS inspection (`docs/research/openshell.md:112`). Node should trust the proxy's CA through it, but no Node client has made a TLS call through OpenShell yet.

### Landlock paths

The policy grants read-only on `/bin`, `/usr`, `/lib`, `/proc`, `/dev/urandom`, `/etc` and `/var/log`. It grants read-write on `/tmp`, `/dev/null` and the baked shared folder, plus the workdir through `include_workdir: true` (`M/sandbox_openshell.py:55`, `:171-172`). pi's binaries under `/usr/local` are readable. The relay's `/tmp` folder, the clone, the session folder and the refuse extension's host path all sit inside writable paths. That last one is under the repo, which is inside the shared folder (`M/sandbox_openshell.py:395-398`). pi's `~/.pi/agent` needs a writable home. Claude Code writes its own home folder and the proof run passed, so the home folder is most likely writable through `include_workdir`. The code comment says only "most likely" (`M/sandbox_openshell.py:169-170`), and the proof has to confirm it.

### Code that checks the provider

- `check_roster` refuses any pi agent under `openshell` (`M/sandbox.py:164-167`), and a test pins that (`.claude/skills/sssf/tests/test_sandbox_openshell.py:259-265`). The `openai/`-only check after it applies to every provider (`M/sandbox.py:168-172`).
- `OpenShell` overrides `_create`, `_delete`, `_call_end`, `keys`, `owner`, `session_minutes`, `errors` and `sandbox_name_max` (`M/sandbox_openshell.py:370-448`). None of these is pi-specific. `Vercel` overrides `_create`, `_end` and `_delete` (`M/sandbox_vercel.py:35-62`), and `E2B` sets `session_minutes = 60` and `owner = "user"` (`M/sandbox_e2b.py:143-145`). No pi code is Vercel-only or E2B-only. All of it sits in `Remote` and `agent_pi._in_vm`.
- Sessions: OpenShell has a 24-hour session and no refresh (`M/sandbox_openshell.py:373`). Nothing bounds a pi call's length below the token's life, as Vercel's 45-minute session does. A pi call still running when its token expires, between 35 and 57 minutes after it starts, would fail on a 401. E2B's 60-minute session doesn't stop this either, so the gap isn't new, only more likely to be hit.
- The docs say pi is refused in this mode (`.claude/skills/sssf/references/config.md:49`, `:369`; `docs/designs/openshell-sandbox.md:34`).

## 3. The binary rule and Node: the key risk

**How OpenShell matches a binary.** The network rules page says to "list the path of the executable that opens the connection", identified "by the real path of its executable, as the kernel reports it". It also says that `npm` "is a script, so the rule lists the Node.js interpreter" ([Network rules](https://docs.nvidia.com/openshell/latest/how-it-works/policies/network-rules)). The schema says a listed binary matches "the executable that opens the connection or any of its parent processes" ([Policy schema](https://docs.nvidia.com/openshell/latest/how-it-works/policies/schema)). The network rules page puts it this way: "if a rule lists an agent's executable, tools that the agent launches can use the rule too." OpenShell also records a hash of each executable the first time it connects and denies the path if the file changes later. SSSF writes each rule as `binaries: [{path: ...}]` (`M/sandbox_openshell.py:154-157`).

**What that means for pi.** A rule can't name pi, because the kernel sees `/usr/local/bin/node` running `cli.js`. The rule has to name `/usr/local/bin/node`, which the nodejs.org tarball ships as a real file. Then, under the parent rule:

1. Any process whose executable is Node may reach the host. That includes the obs relay, `npm` and its install scripts in `worktree_setup`, and any `node` a repo's checks start through `Remote.run`.
2. Any process with a Node ancestor may reach it too. That covers every command pi's `bash` tool runs, so `curl` from a pi agent reaches `api.openai.com`.
3. A Claude Code agent can run `node -e "fetch(...)"` and reach `api.openai.com` as well. Its own rule doesn't cover the host, but `node` does. A Claude-only roster would gain the host whenever the rule is in the policy.

**This is already true for Claude.** The same parent matching means every command a Claude agent runs may reach `api.anthropic.com` with any method today. The design says "`curl` to `api.anthropic.com` failed too, since the policy allowed only `claude.exe` there" (`docs/designs/openshell-sandbox.md:18`, `docs/research/openshell.md:105`). That test's `curl` must have run outside the agent, through `exec`. A `curl` from the agent's own Bash tool would most likely have been allowed. I haven't tested that reading, and it should be checked: it changes what the design claims about the existing rule.

**What naming Node opens.** The agent already holds the token (section 1), so the rule doesn't widen what an agent can spend on the engineer's plan. The new thing is an upload channel. Code in the VM could send repo contents to `api.openai.com` under an attacker's own `sk-` key, for example as a stored response or a file upload. The Claude rule already opens the same channel to `api.anthropic.com`. Vercel and E2B have no allowlist at all (`docs/designs/remote-sandboxes.md:44`). Under decision 14's sorting (`docs/designs/openshell-sandbox.md:36-40`), this is a VM-reachable route. It goes no further than the model host the agent needs anyway, and no network rule can close it without closing the model host.

**What can narrow it.**

- Add the `api.openai.com` rule only when the roster has a pi agent, and the `api.anthropic.com` rule only when it has a Claude agent. The policy is written per run (`M/sandbox_openshell.py:399-400`), so this costs one argument. It stops case 3 for single-harness rosters.
- Use explicit L7 `rules` instead of `access: full`, for example only `POST /v1/responses`. The `rest` protocol checks method, path and query ([Network rules](https://docs.nvidia.com/openshell/latest/how-it-works/policies/network-rules)). That shuts file uploads and other endpoints but leaves responses open. I haven't confirmed which paths pi 1.5.0 uses, so the proof should start with `enforcement: audit` and read the log.
- A dedicated copy of Node at its own path, used only by pi's shim, would keep the relay, npm and checks off the rule. The agent can still run that copy, though, since it descends from it anyway. It isn't worth the image complexity.
- Credential placeholders. OpenShell can keep a provider credential out of the sandbox, put a placeholder in the env, and swap it in at the proxy for bound endpoints. It ships an `openai` profile for `https://api.openai.com/v1` with `OPENAI_API_KEY` ([Providers](https://docs.nvidia.com/openshell/latest/how-it-works/providers/overview)). `openshell provider update --credential KEY` reads the value from the CLI's env, which keeps it off the command line, and `sandbox create --provider` attaches it (CLI `--help`). A placeholder would keep the token out of the agent's reach, so the agent couldn't copy it into a commit. It wouldn't stop the upload channel above, since an attacker's own key needs no placeholder. Open questions: whether the placeholder starts with `sk-`, which would turn off pi's ChatGPT path (`PIAI/api/openai-responses.js:26-31`); how per-run providers on one gateway-wide store behave with two runs at once; and where the gateway stores the token on the host. Updates apply only to new processes, and "an existing process keeps its revision-scoped reference" (Providers), so a placeholder doesn't fix a pi call that outlives its token.

So the binary rule isn't a blocker. It's the same trade the Claude rule already makes. The engineer should know that the rule covers everything an agent launches, and so does the existing one.

## 4. Changes

Guard files are the nine in `CLAUDE.md`. `sandbox.py` and `sandbox_remote.py` aren't on that list.

| # | Change | File | Guard |
|---|---|---|---|
| 1 | Lift the pi refusal for `openshell`. Keep the `openai/`-only check | `M/sandbox.py:164-167` | No, but it decides what may run in the VM. Send it through the chain with 3 |
| 2 | Install pi at a pinned version, plus `ripgrep` and `fd-find` (Ubuntu's `fdfind`, a name pi looks for), and add `pi --version`, `rg --version` and `fdfind --version` to the image's check line | `M/sandbox_openshell.py:124-146` | Yes |
| 3 | Add a `NODE_EXE = "/usr/local/bin/node"` rule for `api.openai.com:443`, `protocol: rest`, `enforcement: enforce`, written only when the roster has a pi agent. Write the Claude rule only when the roster has a Claude agent. `policy()` gains the roster's harness set, and `_create` passes it | `M/sandbox_openshell.py:153-174`, `:399-400` | Yes |
| 4 | Set `PI_OFFLINE=1` for pi agents under OpenShell, so a missing tool fails fast and pi never tries `pi.dev`. Without OpenShell it would also stop the `rg` and `fd` downloads on Vercel and E2B, so scope it to this provider, for example as a provider attribute that `Remote.start` merges | `M/sandbox_openshell.py`, `M/sandbox_remote.py:606-625` | Yes for the first file |
| 5 | Give `CLAUDE_CODE_OAUTH_TOKEN` only to Claude Code agents, and read the token file only when the roster has one. This affects every provider | `M/sandbox_remote.py:490-493`, `:616` | No, but it changes what an agent holds. Chain it |
| 6 | Flip `test_check_roster_refuses_pi_under_openshell`. Add tests for the policy per roster, the Containerfile's pi line, and a pi agent's frame holding `OPENAI_API_KEY` and no Claude token | `.claude/skills/sssf/tests/test_sandbox_openshell.py:259-271` | No |
| 7 | Docs: decision 5 and an as-built note on parent matching in `docs/designs/openshell-sandbox.md`, plus `config.md:49`, `:369` and `:372` | docs | No |
| 8 | Optional: raise the token's floor under OpenShell, for example `--min-expiry 55m`, so a call gets nearly the full hour. A refresh then happens on most calls, under pi's lock on the host | `M/agent_pi.py:264`, `:370-371` | Yes |
| 9 | Optional, later: OpenShell provider placeholders for the ChatGPT token | `M/sandbox_openshell.py`, `M/agent_pi.py` | Yes |

The trace's `sandbox_denied` events (`M/sandbox_openshell.py:427-448`) need no change. A denied `node` call to GitHub would log under `node`.

## Decisions for the engineer

1. **Allow `node` to reach `api.openai.com`, knowing it covers everything Node starts.** I recommend yes. It's the same trade as the Claude rule, and it's tighter than Vercel and E2B.
2. **Write each model host's rule only for a roster that needs it.** I recommend yes. It's one argument, and it keeps a Claude roster from gaining `api.openai.com` through `node -e`.
3. **Use `access: full` or explicit L7 rules for `api.openai.com`.** I recommend explicit `POST /v1/responses`, settled by an `audit` pass in the proof. Narrow the Claude rule the same way later, after its own audit.
4. **The token's route: the stdin frame, as on Vercel and E2B, or OpenShell placeholders.** I recommend the stdin frame for v1. It's proven and needs no new code. Placeholders can follow once the open questions in section 3 have answers.
5. **Pi's version in the image.** I recommend pinning it to E2B's `PI`, 1.5.0, from one constant both scripts read, so the version the ChatGPT proof ran is the one every image gets.
6. **One image for both harnesses, or a pi variant.** I recommend one image. pi, ripgrep and fd add little, and a second image doubles `just openshell-setup`.
7. **Stop giving pi agents the Claude token (change 5).** I recommend yes, on every provider. A pi agent has no use for it, and with `claude.exe` in the image, holding the token lets it run Claude.
8. **Re-check the Claude rule's reach.** The parent matching means the design overstates how narrow `anthropic_api` is. I recommend fixing the design text now and testing it in the proof (step 3 below).

## 5. A proof run

In sssf-sandbox, whose repo default is already `sandbox: openshell` (`~/github/sssf-sandbox/adws/adw_sssf_config/repo.yaml:22`), with its ChatGPT roster (`~/github/sssf-sandbox/adws/adw_sssf_config/sssf.config.chatgpt.yaml:10-11`). The repo's recipes take `--config` (`~/github/sssf-sandbox/justfile:51-68`). The project session runs it through the skill once the hub's change lands.

1. **Audit pass.** Use the `api.openai.com` rule with `enforcement: audit` and `adw_plan_build_test --worktree --sandbox openshell` on a small request. Read `openshell logs` for every path pi used, and check that `sandbox_tools` records pi, Node, and no denial to `pi.dev` or GitHub.
2. **Enforced pass.** Run `adw_simple_sdlc --worktree --sandbox openshell` with the L7 rules on enforce. Plant three commands in the request for the builder: a `curl` to `github.com`, a `node -e` fetch to `example.com`, and a `curl https://api.openai.com/v1/models` from the agent's Bash. I expect the first two denied and the third allowed through parent matching. With L7 rules on, a GET to `/v1/models` should be denied at L7, which shows the narrowing works. The `grep` and `find` tools must work with no download. Check that no token appears in the trace, the session files or the commits, and that the pi agent's env had no `CLAUDE_CODE_OAUTH_TOKEN`.
3. **Claude rule check.** In the same repo's Claude roster, plant a `curl https://api.anthropic.com/` in a builder's Bash, to settle decision 8.
4. **The obs relay.** Run with `sssf.config.obsrelay.yaml` against a stand-in obs on a scratch port, as decision 49 did (`docs/designs/remote-sandboxes.md:117`, `:271`). Expect an `obs_relay` event per call, with `live` and `swept` true.
5. **A Claude-only roster after the change.** Its `openshell-policy.yaml` in the session folder must have no `api.openai.com` rule.
6. **A real project.** Then pi-agent-observability's session does one run with its ChatGPT roster and the obs extension while the engineer has obs up, as 0939000a did on Vercel (`docs/designs/remote-sandboxes.md:272`). Its config loads `extension/pi-observability.ts` on every agent (`~/github/pi-agent-observability/adws/adw_sssf_config/sssf.config.yaml:9-21`), and its repo default is `sandbox: none` (`~/github/pi-agent-observability/adws/adw_sssf_config/repo.yaml:25`), so the request names `--sandbox openshell`. The engineer sends the request in their own words, with "Use the sssf skill (/sssf) for this."

Which hub projects use pi rosters: pi-agent-observability's shipped roster is all pi on `openai/` models (`~/github/pi-agent-observability/adws/adw_sssf_config/sssf.config.yaml:10-11`). fusion-harness ships a Claude Code roster (`~/github/fusion-harness/adws/adw_sssf_config/sssf.config.yaml:7`) and a pi roster beside it, `sssf.config.chatgpt.yaml` (`:10-11`), with `harness_engineering: []` and `sandbox: none` (`~/github/fusion-harness/adws/adw_sssf_config/repo.yaml:24`). sssf-sandbox ships three pi rosters: `chatgpt`, `obsrelay` and `vmext`.

## Not confirmed

- That an agent's own `curl` reaches `api.anthropic.com` under today's policy. The docs say so, but no test ran it.
- Which request paths pi 1.5.0 sends to `api.openai.com`, and that its stream passes OpenShell's `rest` inspection. Claude's streamed calls do.
- That Node trusts OpenShell's CA through `NODE_EXTRA_CA_CERTS` under SSSF's exec shell.
- That the sandbox user's home folder is writable for `~/.pi/agent`.
- That pi in Vercel and E2B downloads `rg` and `fd` from GitHub today.
- How OpenShell's credential placeholders look, and whether pi accepts one as a ChatGPT token.
