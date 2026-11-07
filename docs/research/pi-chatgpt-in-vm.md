# Pi on a ChatGPT plan in a remote VM

Researched 2026-11-02. Can a pi agent signed in with a ChatGPT subscription run inside a Vercel Sandbox or E2B VM for an SSSF run, and what is the safest way to get it signed in there?

## Short answer

Yes. Pi can run in the VM without its login ever going in. Pi treats any `OPENAI_API_KEY` that doesn't start with `sk-` as a ChatGPT access token and sends it to `api.openai.com/v1` the same way it sends its stored login. So the VM only needs a bare access token, which lasts one hour and comes with no refresh token. The host keeps the refresh token in `~/.pi/agent/auth.json`, refreshes it under pi's own lock, and hands each agent a token with enough time left. OpenAI documents this exact shape for Codex app-server: the app owns the refresh and passes only `ACCESS_TOKEN` to the child process.

Copying `auth.json` into the VM is the option to avoid. OpenAI's refresh tokens rotate on every refresh, so whichever side refreshes first leaves the other holding a used token, and the next refresh there fails with `refresh_token_reused` and a forced re-login. Deleting the copy at the end doesn't undo that, and it doesn't revoke anything.

There is no ChatGPT equivalent of `claude setup-token` for a personal plan. The nearest thing, a Codex access token, is for Business and Enterprise workspaces and only works with Codex CLI.

My recommendation:

1. Build the access-token-only route first. It is a small change to `sandbox_remote.start`, works the same on Vercel and E2B, and puts a one-hour, inference-scoped token in the VM instead of the 30-day refresh session.
2. Later, with the domain allowlist the design already plans, move the token out of the VM entirely with Vercel's credentials brokering. The VM then holds a placeholder, and Vercel's firewall swaps in the real token for requests to `api.openai.com`.
3. Don't copy `auth.json`, and don't run a login inside the VM.

Unconfirmed, and worth one live check each before building: that pi in a fresh VM finds the roster's model from an env-var token, that the pi SDK's `ModelRuntime.getAuth` can refresh the host login from a script, and what OpenAI's consumer terms say today (their page refused automated fetches). The live check on 2026-11-03 settled the first two; see "Live check" at the end. The engineer read the terms the same day: see "Terms, as the engineer read them".

## 1. Where the credentials live

### Pi

Pi 1.4.0 (`~/.nvm/versions/node/v24.11.1/lib/node_modules/@earendil-works/pi-coding-agent/`, its bundled `pi-ai` 1.4.0 under `node_modules/@earendil-works/pi-ai/dist/`):

- Pi stores credentials in `auth.json` under its agent directory, `~/.pi/agent` by default (`dist/core/auth-storage.js:31`), which `PI_CODING_AGENT_DIR` overrides (`docs/environment-variables.md:81`). It creates the file with mode 600 (`auth-storage.js:27`) and takes a file lock for every write (`auth-storage.js:62-80`, `:123-162`).
- The ChatGPT login is the `openai` provider, "Sign in with ChatGPT" (`auth/oauth/openai-chatgpt.js:253-262`). A login or refresh stores `type: "oauth"`, `access`, `refresh`, `expires` (epoch milliseconds, three minutes early), `clientId` and `scopes` (`openai-chatgpt.js:136-155`). There is no account id field. The local file has exactly these keys under `openai` (checked with `jq paths`, no values read).
- Each login registers a new OAuth client. Pi sends `client_id: dynamic_agent_client` and stores the client id OpenAI issues in the callback (`openai-chatgpt.js:12-13`, `:39-42`). A refresh needs that stored id (`openai-chatgpt.js:172-183`).
- The token is requested for resource `https://api.openai.com/v1` with scopes `openid profile email offline_access resource.invoke chatgpt.tokens.use.direct` (`openai-chatgpt.js:18`, `:23-24`).
- Pi identifies the install to OpenAI with `ext_agent_host_id: urn:uuid:<deviceId>` (`openai-chatgpt.js:185-209`). The device id is a random UUID kept as `deviceId` in `~/.pi/agent/settings.json` (`dist/core/settings-manager.js:841-846`; the local file has the key). Pi sends it only at login, not on refresh (`openai-chatgpt.js:177-182`) and not on model requests (a search of `pi-ai/dist` finds it only in the login code and the CLI).
- The legacy `openai-codex` provider is a separate login against the Codex backend, with its own refresh (`auth/oauth/openai-codex.js:123-140`) and an account id read from the access token's `chatgpt_account_id` claim (`openai-codex.js:241-245`).

### Codex CLI

From `openai/codex` at `main` commit `fbf5f0d3a716d08c254d3e4941d5b12eaff5afe7` (paths under `codex-rs/login/src/`), and Codex CLI 0.153.0 on this machine:

- Codex keeps credentials in `auth.json` under `CODEX_HOME` (default `~/.codex`) or in the OS keyring, set by `cli_auth_credentials_store` (`file`, `keyring` or `auto`) ([Codex auth, credential storage](https://learn.chatgpt.com/docs/auth#credential-storage); `developers.openai.com/codex/auth` redirects there).
- The file holds `auth_mode`, `OPENAI_API_KEY`, `tokens`, `last_refresh`, and optional `agent_identity`, `personal_access_token` and Bedrock fields (`auth/storage.rs:53-77`). `tokens` holds `id_token`, `access_token`, `refresh_token`, `oauth_provenance`, `login_owner_id` and `account_id` (`token_data.rs:11-33`). The local `~/.codex/auth.json` has these keys, mode 600.
- Codex refreshes when the access token's JWT expiry is within 5 minutes, or when `last_refresh` is older than 8 days (`auth/manager.rs:221-222`, `:3535-3546`). It refreshes against `https://auth.openai.com/oauth/token` with its fixed client id (`manager.rs:230`, `:1863`).

### Does a refresh rotate the refresh token?

Yes. OpenAI's [token reference](https://developers.openai.com/siwc/token-sharing-open-source/token-reference) for the route pi uses says: "Access tokens are valid for one hour (`expires_in: 3600`)." "Refresh tokens are valid for 30 days." "Each successful refresh returns a replacement refresh token with a fresh 30-day lifetime." The [accounts and sessions page](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions) calls it "a rotating token" and says to "serialize refreshes for the same session so two processes do not race a rotating token."

What happens to the old token is in [errors and recovery](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery): `refresh_token_reused` is one of the codes that mean the refresh token is unusable, and the fix is to "clear unusable tokens and repeat OAuth." Codex's source shows the server's message: "Your refresh token has already been used to generate a new access token. Please try signing in again." (`auth/util.rs:24-31`), and Codex maps it to "Please log out and sign in again." (`auth/manager.rs:225`, `:1836`). Pi's resolver says the same in a comment: once a refresh starts, "the provider may already have rotated the refresh token" (`pi-ai/dist/auth/resolve.js:53-57`).

So two machines on one login do log each other out. Codex's CI guide says it plainly: "Do not share the same file across concurrent jobs or multiple machines," and it lists "another machine or concurrent job rotated the token first" as a reason to reseed ([CI/CD auth](https://learn.chatgpt.com/docs/auth/ci-cd-auth)). On one machine, pi is safe: it re-checks expiry under the file lock and refreshes once across processes (`resolve.js:48-95`).

On this machine the stored access token expired about 57 minutes after the file's last write, which matches 3600 seconds less pi's 3-minute margin. That is an estimate from the file's timestamp, not a decoded token.

## 2. Is there a `claude setup-token` equivalent?

Not for a personal ChatGPT plan. What exists:

- **A bare access token in `OPENAI_API_KEY`.** Pi's `openai` provider reads `OPENAI_API_KEY` (`pi-ai/dist/providers/openai.js:12-14`) when nothing is stored for the provider (`resolve.js:4-9`, `:35-38`). `isChatGPTSignIn` treats any key for `openai` at `https://api.openai.com/v1` that doesn't start with `sk-` as a ChatGPT token and adjusts the request to match (`pi-ai/dist/api/openai-responses.js:22-31`, `:84-85`, `:261`). An env token can't be refreshed, so it lasts at most an hour. This is the shape OpenAI documents for Codex app-server: "Set `ACCESS_TOKEN` in the app-server child process's environment," "Manage token renewal in your app," and restart the child with the renewed token ([Codex app-server](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server); [preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)).
- **Codex access tokens.** "ChatGPT workspace credentials scoped to Codex permissions," "currently supported for ChatGPT Business and Enterprise workspaces." They must expire, one day at the shortest, and are meant "only on trusted runners." Codex reads one from `CODEX_ACCESS_TOKEN` or `codex login --with-access-token` ([access tokens](https://learn.chatgpt.com/docs/enterprise/access-tokens); `auth/manager.rs:1019`, `:1032-1034`). This is the closest match to `setup-token`, but it doesn't cover Plus or Pro, and nothing says pi can use one. Whether it works against `api.openai.com/v1` is unconfirmed.
- **Device-code login.** Codex has `codex login --device-auth` (`codex-rs/cli/src/main.rs:543-544`), which the user must first enable in ChatGPT settings ([Codex auth, headless devices](https://learn.chatgpt.com/docs/auth#login-on-headless-devices)). Pi offers device code only on the legacy `openai-codex` provider (`openai-codex.js:23-29`). Pi's `openai` provider has none, and OpenAI's [sign-in page](https://developers.openai.com/siwc/token-sharing-open-source/sign-in) for this route describes only an authorization code with PKCE and a `127.0.0.1` loopback callback. Either way, a device code needs a person at a browser for each login, and it gives a full 30-day session.
- **`CODEX_API_KEY` and `OPENAI_API_KEY`.** Both are API keys, billed at API rates, not plan credentials (`auth/manager.rs:1017-1029`, `:1586-1593`; [Codex auth, API key](https://learn.chatgpt.com/docs/auth#sign-in-with-an-api-key)).

## 3. Official support for headless and remote machines

**Codex CLI.** The [auth page](https://learn.chatgpt.com/docs/auth#login-on-headless-devices) has a section for headless devices. It says to "prefer device code authentication (beta)," and gives two fallbacks: copy `~/.codex/auth.json` from a machine with a browser (with `scp` and `docker cp` examples), or forward port 1455 over SSH. The CLI's own login message says "On a remote or headless machine? Use `codex login --device-auth` instead." (`codex-rs/cli/src/login.rs:123`). For CI, the [CI/CD guide](https://learn.chatgpt.com/docs/auth/ci-cd-auth) calls ChatGPT auth "an advanced workflow for enterprise and other trusted private automation," says "Do not use this workflow for public or open-source repositories," and says "API keys are still the recommended option for most CI/CD jobs."

**Pi.** `docs/providers.md:14` says that on a remote or headless machine, the user can paste the final redirect URL back into pi. The code accepts that paste (`openai-chatgpt.js:44-60`, `:218-231`). `docs/containerization.md` covers containers. It warns that mounting `~/.pi/agent` "exposes your Pi credentials" (`:23`), says not to run `/login` inside a Docker Sandbox "because that writes a real credential into it" (`:86`), and shows a proxy that keeps the real Claude token on the host and gives the sandbox a placeholder (`:84-101`). It has no ChatGPT example.

**OpenAI's page for this route.** [Self-hosted VMs](https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms) covers an open-source app on a remote VM. Its steps are: give the VM its own stable `ext_agent_host_id` first, do the OAuth on a local machine, copy the credential file to the VM over a secure channel while keeping the VM's host ID, then let the VM own later refreshes. It says the procedure "transfers an existing credential session," and that per-host revocation for transferred sessions isn't available yet. So the session moves to the VM. It isn't shared with the laptop.

## 4. What OpenAI's terms and help pages say

- **Sign in with ChatGPT.** The [overview](https://developers.openai.com/siwc/token-sharing-open-source) says the docs cover "open-source and locally hosted apps," and a "paid or remotely hosted app" should fill in an interest form first. The token "does not grant access to their ChatGPT conversations or other account context." SSSF is open source and runs on the user's own machine. Its agents run in a VM the user rents, which fits the self-hosted VM page above. Whether OpenAI counts a Vercel or E2B sandbox as a self-hosted VM is unconfirmed.
- **Codex.** The CI guide limits ChatGPT auth to trusted private runners and recommends API keys for automation (quotes in section 3). The access-tokens page says the same: "only on trusted runners," not public CI, forked pull requests or shared machines.
- **Terms of use.** openai.com's terms pages returned HTTP 403 to every fetch, so I couldn't read the current text. Search results and an archived copy of the December 2024 terms show the rule users "may not share your account credentials or make your account available to anyone else" and "are responsible for all activities that occur under your account." Unconfirmed against OpenAI's live page. My reading is that one person's own login on one person's own VM isn't sharing, but I haven't found an OpenAI page that says so.
- **Help center.** I found no help.openai.com article on using a ChatGPT login on a remote machine or in automation. Unconfirmed either way.

## 5. The options, ranked by risk

Lowest risk first. "In the VM" means readable by the agent and by anything the repo's code runs. SSSF agents run with full permissions, so treat that as exposed.

### 1. Broker the token at the VM's egress, refreshing on the host

The VM gets `OPENAI_API_KEY=<placeholder>`, any string without `sk-`, so pi takes the ChatGPT path. The provider's firewall replaces the `Authorization` header on requests to `api.openai.com`. No token enters the VM, and the refresh token never leaves the host.

- **Vercel.** "Credentials brokering injects credentials into egressing traffic. The secrets never enter the sandbox." Injected headers replace any the sandbox sets, and policies "can be updated at runtime, without restarting the process." The firewall terminates TLS for those domains with a per-sandbox CA added to the system certificates ([Vercel Sandbox firewall](https://vercel.com/docs/sandbox/concepts/firewall)). The Python SDK shows `create_sandbox(network_policy=...)` and `update_network_policy(...)`.
- **E2B.** Secret injection adds a stored secret to outbound HTTPS requests to chosen hosts, and "the proxy resolves it outside the sandbox." The secret lives in E2B's secret store, referenced with `Secret.fill`. It fails open: an unresolved secret means the request goes out without the header and gets a 401 ([E2B secret injection](https://docs.e2b.dev/secrets/inject)).
- **A proxy of my own on the host** doesn't fit. The host is a laptop, and the VM can't reach it without a public tunnel. Vercel's `forwardURL` needs a public HTTP/1.1 server.

What it takes: `sandbox_vercel._create` passes a network policy with a transform for `api.openai.com`. Before each pi agent starts, the runner gets a fresh access token (as in option 2) and calls `update_network_policy` with it. On E2B, the runner creates or updates the secret instead. Unconfirmed: that `vercel-sandbox==0.10.0`'s sync API has network policies, that a policy survives the stop and resume `_refresh` does, that Node in the VM trusts Vercel's CA under the environment `start` gives it, that E2B can update a secret's value in place from Python, and that pi's streams on this route use HTTPS that the firewall can rewrite. The token then sits with Vercel or E2B instead of the VM, which is a smaller exposure but not none.

### 2. An access token only, refreshed on the host

The runner passes a ChatGPT access token as `OPENAI_API_KEY` to each pi agent in the VM, the way it passes `CLAUDE_CODE_OAUTH_TOKEN` today (`Remote.start` in `sandbox_remote.py`). The VM never gets a refresh token or `auth.json`, so nothing it does can rotate the host's session. A leaked token works for under an hour, only against `api.openai.com/v1`, and can't read ChatGPT conversations.

What it takes:

- **A host-side token helper.** A short Node script run with pi's own install calls the pi SDK's `ModelRuntime.getAuth("openai", { minOAuthValidityMs })` (`dist/index.d.ts:15`; `dist/core/model-runtime.d.ts:21-26`, `:89-90`). Asked for more validity than the token has, pi refreshes under its file lock and saves the result (`resolve.js:100-116`), so it can't race the user's other pi sessions on the host. Ask for `AGENT_SECONDS` plus a margin, 35 minutes (`AGENT_SECONDS` in `sandbox_remote.py`). The helper prints the token to the runner over a pipe and never to a log. That `ModelRuntime.create()` with no options reads `~/.pi/agent/auth.json` is unconfirmed.
- **`start` picks the token by agent.** For `pi`, set `OPENAI_API_KEY` from the helper; for `claude`, keep `CLAUDE_CODE_OAUTH_TOKEN`. `_env` passes only `SSSF_` and `OBS_` host variables (`PASSED` and `Remote._env` in `sandbox_remote.py`), so nothing else leaks in. Each agent gets its own token, so a run longer than an hour is fine.
- **Checks in `open`.** Fail if the VM has `~/.pi/agent/auth.json`, because a stored credential wins over the env var (`resolve.js:24-34`). Fail if the host has no `openai` OAuth entry, by key name only.
- **Lift the refusal** in `sandbox.check_roster` (`sandbox.py:150-155`) for pi agents on the `openai` provider. Keep refusing `openai-codex`.
- **One live check first.** Run one pi agent in a VM with an env-var token and confirm it finds the roster's model. With an env key, pi may load a different model list than with an OAuth login (`pi-ai/dist/providers/openai.js:26`; `docs/providers.md:122`).

This is less work than option 1 and works the same on both providers. Option 1 can be added on top later, since it changes only where the token goes.

### 3. API-key billing

Put an `sk-` key in `~/.config/sssf-sandbox/token.env` as `OPENAI_API_KEY` and pass it to pi agents, as above. Pi then takes the API-key path. There's no plan credential anywhere and no rotation to manage, and OpenAI recommends this for automation (section 3). The key is long-lived, though, and sits in the VM unless it's brokered as in option 1, so its exposure is worse than a one-hour token. Use a project key with a spend limit. Usage costs money at API rates. SSSF's cost caps already use pi's list-price `cost` (`budget.py`), so they would match real spend for once. This is the smallest build: a second key in the token file and a branch in `start`.

### 4. A login inside the VM

Pi's `openai` provider has no device code. Its login opens a browser and waits on `127.0.0.1:1455`, with a pasted redirect URL as the fallback (`openai-chatgpt.js:19-22`, `:44-60`). Only the legacy `openai-codex` provider has a device code, and it talks to the Codex backend, which OpenAI tells token-sharing apps not to use (see `docs/research/pi-usage-limits.md`). Either way, a person has to sign in for every VM, and each VM ends up with a full 30-day refresh session. It wouldn't log the host out, since it's a separate login, but every VM leaves a live session behind that only expires, because pi's `/logout` doesn't revoke at the provider (`docs/providers.md:16`). It can't run unattended. Building it means an interactive step in `open`, which SSSF runs don't have.

### 5. Copy `auth.json` in for each run, delete it at the end

This is the riskiest. The VM gets the refresh token, so anything in it can keep the session alive for 30 days at a time, and deleting the file doesn't revoke it. Worse, it breaks the host. A pi agent in the VM refreshes once its token has under 5 minutes left (`resolve.js:46`, `:100-106`), which the rotation in section 1 turns into a logout for every other holder of that session. That includes the user's own pi sessions on the host and any other VM. The reverse happens too: the host refreshes, and the VM's next refresh fails mid-run. Copying the file back at the end only helps if nothing on the host refreshed meanwhile. OpenAI's self-hosted VM page describes moving a session to a VM, not sharing it.

Building it means writing `auth.json` and `settings.json` into the VM in `open`, reading `auth.json` back after each agent under pi's lock, choosing a winner when both sides refreshed, and deleting the files in `close`. That is more code than option 2, and the result is worse.

## Recommendation

1. Run two live checks in sssf-sandbox. First, a host script calls `ModelRuntime.getAuth("openai", { minOAuthValidityMs })` and reports only whether it got a token and how long it has left. Second, one pi agent in a Vercel VM runs with that token as `OPENAI_API_KEY` and no `auth.json`. Log key names and the model id, never values.
2. If both pass, build option 2: the helper, the per-agent token in `start`, the `auth.json` check in `open`, and lifting the refusal in `check_roster` for the `openai` provider. Add a test that the VM's environment for a pi agent has `OPENAI_API_KEY` and no refresh token, like case 6 does for Claude.
3. When the domain allowlist from decision 8 gets built, add option 1 on Vercel and move the Claude token behind the firewall at the same time.
4. Record in `docs/designs/remote-sandboxes.md` that the ChatGPT route uses the host's login with one-hour tokens, and that copying `auth.json` is ruled out because refresh tokens rotate.

## Not confirmed

- OpenAI's current consumer terms text. Their pages returned 403, and the quote above comes from search results and an archived copy.
- Whether OpenAI counts a Vercel or E2B sandbox as a "self-hosted VM" for Sign in with ChatGPT.
- Whether a Codex access token works with pi, or against `api.openai.com/v1` at all.
- That pi's SDK `ModelRuntime.create()` reads the default `auth.json`, and that pi in a VM finds the roster's model with an env-var token.
- For option 1: network policy support in `vercel-sandbox==0.10.0`'s sync API, whether a policy survives stop and resume, CA trust for Node in the VM, and in-place secret updates on E2B from Python.
- The exact access-token lifetime on this machine. About an hour, estimated from the file's timestamp. OpenAI's docs say 3600 seconds.

## Live check, 2026-11-03

Run from the hub in two E2B VMs made from SSSF's `sssf` template, with sssf-sandbox's E2B key. The VMs were iuy28kwl3i8obsy2ncp38 and i9un9gablvpvs7jpxt0t0, both killed at the end. No token value was printed: the script replaced it in every output line.

- **The host can refresh and hand out a token with no model call.** `pi auth print-bearer-token --provider openai --min-expiry 35m` refreshed the expired host login under pi's lock and printed a token with 57 minutes left. That replaces the `ModelRuntime.getAuth` question: SSSF can shell out to pi for the token, as it already does for `pi --list-models`.
- **pi in the VM finds the roster's models from the token alone.** With no `~/.pi/agent/` in the VM, `pi --list-models` showed no openai models. With `OPENAI_API_KEY` set to the token it showed 44, including `gpt-6.1-sol` and `gpt-6.1-terra`, the chatgpt roster's two models. The host shows 45. `pi auth check --provider openai --no-refresh` said `ready`.
- **Both models answered.** `pi -p --provider openai --model gpt-6.1-sol --no-tools --no-session` and the same with `gpt-6.1-terra` each replied `OK`.
- **Nothing durable is left in the VM.** pi creates `~/.pi/agent/auth.json` and `models-store.json` on first use, both `{}`, and the token is in neither, nor anywhere under `~/.pi`.
- **The host login was untouched.** After both checks, `pi auth check --provider openai --no-refresh` on the host still said `ready`.
- **Versions differ.** The template's pi is 1.5.0 and the host's is 1.4.0, because the template installs the latest at build time. A build should pin the template's pi or note the version it ran.

So option 2 works as described, and the token handout is one command on the host.

## Terms, as the engineer read them, 2026-11-03

The engineer checked OpenAI's published terms and help pages, using a chatbot's search as a pointer and reading the quoted sentences in context. Their summary: the exact combination isn't addressed in the documents reviewed, and no quoted provision specifically prohibits it. The combination is pi, a VM the engineer creates and deletes, unattended runs, and a short-lived access token as the only credential passed. On that reading the engineer approved building option 2.

Built as option 2 in 51d54f6, with a fix in 673a70d; the proof run is in `docs/designs/remote-sandboxes.md`'s log.
