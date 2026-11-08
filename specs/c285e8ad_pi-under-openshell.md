# Run pi agents under `--sandbox openshell`

The engineer accepted the recommendations in `docs/research/pi-under-openshell.md` on 2026-11-08, except that request-path narrowing waits for the proof. This spec lifts the refusal from decision 5 of `docs/designs/openshell-sandbox.md`, puts pi in the image, and writes each model host's rule only for a roster that needs it. It also stops giving pi agents the Claude token on every provider.

Line numbers are from the tree at planning time (`3619225`, which only adds a task file over the baseline).

## Threat model

- **What the agent can do.** An agent in a VM runs any command as the sandbox user, and reads anything that user can read, including its own environment. It can start `node` or `claude.exe`, and write the commits, bundles and handoff files that return to the host.
- **What this guard can't stop at its level.**
  - A network rule can't tell an agent's model call from its other use of that host, because OpenShell matches the connecting process and all its parents.
  - Env values can't be hidden from the process that holds them.
  - All agents in a run share one VM and one user, so a later agent can read what an earlier one left in `/tmp` or the clone, and a live process's environment.
- **Local mode.** Under `--sandbox none` the agent runs as the engineer's own user. This change touches only VM code, so decision 14's local-mode limits don't change.

## Baseline

`main` at `538edfb`. Every finding is judged against that commit.

At that commit:
- `check_roster` refuses every pi agent under `openshell`.
- `policy()` always writes `anthropic_api` and never mentions `api.openai.com`.
- `Remote.open` always requires the Claude token file.
- `Remote.start` gives every agent `CLAUDE_CODE_OAUTH_TOKEN`.

## Bar

Findings are sorted by decision 14 of `docs/designs/openshell-sandbox.md`.

**Must do**
1. A pi roster on `openai/` models passes `check_roster` under `openshell`. A pi agent on another provider is refused in every VM mode.
2. The image holds pi at the shared pinned version, `rg` and `fdfind`, and the build fails if any of them is missing.
3. A roster's policy has a model-host rule only for the harnesses it has.
4. A pi agent under OpenShell gets `PI_OFFLINE=1`. Under Vercel and E2B it doesn't.
5. On every provider, a pi agent's start env has `OPENAI_API_KEY` and no `CLAUDE_CODE_OAUTH_TOKEN`. A Claude Code agent's env keeps the Claude token. An all-pi roster opens a VM with no token file.

**Must never do, against the baseline.** Each of these blocks:
- Let a VM agent reach another host.
- Write a model host's rule on a roster that doesn't need it.
- Put the Claude token in a pi agent's environment.
- Put a token in a command line, the trace, a session file or a commit.
- Keep an agent's file, run agent code on the host, or delete or overwrite an engineer's file, where the baseline does otherwise.

**Known limits: accepted, not bypasses**
- Any process with `node` or `claude.exe` among its ancestors, which covers every command the agent runs, can reach that roster's model host. The agent already holds a token for it, so it gains no spending power, only an upload channel to that host. `api.anthropic.com` already gives a Claude agent the same channel. On a mixed roster both hosts are open, and either agent can reach either one by starting `node` or `claude.exe`, with its own or an attacker's key.
- The pi agent holds its hour's ChatGPT access token, as on Vercel and E2B.

## Out of scope

Reviewers file these as old holes or later work:
- Narrowing either model host's rule to request paths. The proof's audit pass decides that later.
- OpenShell's credential placeholders, and a longer token lifetime for long pi calls (the research's changes 8 and 9). A pi call that outlives its token fails on a 401.
- Building an image or running a sandbox. The proof runs after the merge. It settles the research's "Not confirmed" list: the writable home for `~/.pi/agent`, `NODE_EXTRA_CA_CERTS`, pi 1.5.0's request paths and its `PI_OFFLINE` behaviour (read only on 1.4.0).
- Agents sharing one VM on a mixed roster. A pi agent could read a token only if an earlier agent wrote it down, or from a live process's environment. The baseline shares the VM the same way.
- `sandbox_allow`, the Landlock paths and `ANY_BINARY`, which stay as they are.
- Local-mode routes under decision 14.

## Changes

Don't edit `adws/adw_modules/`. It's a protected, installed copy, and the engineer rolls it out after the merge.

### 1. `.claude/skills/sssf/templates/adws/adw_modules/sandbox.py`

- Delete the `openshell` refusal at lines 164-167. Keep the `openai/` check at lines 168-172 as it is.

### 2. `.claude/skills/sssf/templates/adws/adw_modules/sandbox_remote.py`

- Add `PI_VERSION = "1.5.0"` beside `TOOLS`, with a comment saying it's the pi the ChatGPT live check ran and that both image scripts read it. This module imports only the standard library and sibling modules that do too, so the E2B script's environment can import it without pyyaml.
- Add `harnesses(cfg) -> set[str]`, returning `{a.coding_agent for a in getattr(cfg, "agents", None) or []}`. Make `tools_needed` use it.
- Add a class attribute on `Remote`: `pi_env: dict[str, str] = {}`, described as what a provider adds to a pi agent's env.
- In `open` (line 493), read the token only when it may be needed:
  ```python
  found = harnesses(getattr(self.run_, "cfg", None))
  if not found or "claude_code" in found:
      self._token = _token()
  ```
  An unknown or empty roster reads the token, as the baseline does. The existing fixtures have no `cfg` and depend on this.
- In `start` (line 616), replace the `agent_env` line:
  ```python
  agent_env = self._env(env)
  if "OPENAI_API_KEY" in agent_env:    # only _env's rename of agent_pi's token sets this
      agent_env.update(self.pi_env)
  elif self._token:
      agent_env["CLAUDE_CODE_OAUTH_TOKEN"] = self._token
  ```
  `_env` passes only `SSSF_` and `OBS_` names, so a host `OPENAI_API_KEY` can't make a Claude agent look like a pi agent. `agent_pi._in_vm` sets `SSSF_PI_TOKEN` on every pi call in a VM, or raises. Don't change `Sandbox.start`'s signature. `pi_env` goes only on `start`, never on `run`.
- Update the module docstring at lines 11-14: only a Claude Code agent gets the token from the file, and a pi agent gets its ChatGPT token as `OPENAI_API_KEY`.

### 3. `.claude/skills/sssf/templates/adws/adw_modules/sandbox_openshell.py`

- Add `NODE_EXE = "/usr/local/bin/node"` beside `CLAUDE_EXE`.
- In `containerfile`:
  - Add `ripgrep fd-find` to the apt line.
  - Add `f"RUN npm install -g @earendil-works/pi-coding-agent@{sandbox_remote.PI_VERSION}"` after the Claude Code install.
  - Make the check line `node --version && uv --version && claude --version && pi --version && rg --version && fdfind --version && git --version`, plus `find`. The Python `find` stays last, because `test_containerfile_installs_and_checks_the_python` pins that.
- Change the signatures to `policy(folder, allow, harnesses=frozenset({"claude_code"}))` and `policy_yaml(folder, allow, harnesses=frozenset({"claude_code"}))`. The default keeps the pinned two-argument tests valid. `_create` always passes the real set.
- Build the rules so each model rule is conditional:
  - `"claude_code" in harnesses` adds `anthropic_api`, unchanged.
  - `"pi" in harnesses` adds `openai_api`: `rule("api.openai.com", 443, "full", NODE_EXE)`.
  - `package_registries` and the `repo_allow_*` rules are unconditional, as now.
  - An empty set gets no model rule, so it fails closed.
- In `_create` (line 400), pass `sandbox_remote.harnesses(getattr(self.run_, "cfg", None))`.
- On `class OpenShell`, set `pi_env = {"PI_OFFLINE": "1"}`.
- In the module docstring (lines 13-15), say that the Claude token and the ChatGPT token both travel this way. Add a sentence that each roster's policy opens only its own harnesses' model hosts.

### 4. `.claude/skills/sssf/scripts/e2b_template.py`

- Follow `openshell_image.py`'s pattern: `sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "templates" / "adws"))`, then `from adw_modules.sandbox_remote import PI_VERSION as PI  # noqa: E402`. Delete the literal.
- Change the docstring's "raise PI here" to name `PI_VERSION` in `sandbox_remote.py`, which the OpenShell image reads too. Add that raising it also changes the OpenShell tag.

### 5. Tests

Build each config from the shipped `.claude/skills/sssf/templates/sssf.config.yaml`, through the existing `shipped()` helper. Don't add a test-only config.

**`.claude/skills/sssf/tests/test_sandbox_openshell.py`**
- Flip `test_check_roster_refuses_pi_under_openshell` to `test_check_roster_lets_a_pi_openai_roster_through_under_openshell`.
- Add a test parametrized over `sandbox.PROVIDERS`: a pi `openai/` roster passes, and a pi agent on `anthropic/` or `google/` raises `SystemExit` naming that agent.
- Add `policy()` tests with `{"pi"}`, `{"claude_code"}` and `{"pi", "claude_code"}`:
  - Each has only its model rules, plus `package_registries`.
  - `openai_api` equals `{"endpoints": [{"host": "api.openai.com", "port": 443, "protocol": "rest", "enforcement": "enforce", "access": "full"}], "binaries": [{"path": "/usr/local/bin/node"}]}`.
  - An empty set has neither model rule.
  - `"github"` is in none of them.
- Add a `_create` test with `run.cfg` set per roster, using `layout` and the fake CLI. Load `openshell-policy.yaml` and check its rules. A pi-only roster has no `api.anthropic.com` and a Claude-only roster has no `api.openai.com`. `--no-auto-providers` stays in the create argv.
- Add a Containerfile test:
  - `RUN npm install -g @earendil-works/pi-coding-agent@1.5.0` appears.
  - The apt line holds `ripgrep` and `fd-find`.
  - The check line holds `pi --version`, `rg --version` and `fdfind --version`.
- Add a test that loads `e2b_template.py` with `importlib.util.spec_from_file_location` and asserts `PI == sandbox_remote.PI_VERSION == "1.5.0"`.
- Add a start-frame test on OpenShell with the fake CLI: an `OpenShell` box with `box.sandbox = osh._VM("sb")`, decoding the `KEY=<base64>` frame from the `setsid` exec's stdin.
  - A pi start (`env={"SSSF_PI_TOKEN": "chatgpt-access"}`, `box._token = TOKEN`) has `OPENAI_API_KEY` and `PI_OFFLINE=1`, and no `CLAUDE_CODE_OAUTH_TOKEN`.
  - A Claude start has the Claude token, and no `PI_OFFLINE` or `OPENAI_API_KEY`.
  - No token value appears in any call's argv.
- **Second required test edit.** `test_containerfile_without_a_python_installs_none` pins the tag `c1f8d90c5594` at line 435. This change must alter that hash, because the request says the tag changes. So update only the pinned value to `osh.image_tag(Path("/home/u/repo"), "")` as it comes out after your change, and change its comment to say it's the tag from when the image gained pi. Report this in your notes as the one exception beyond the flip.

**`.claude/skills/sssf/tests/test_sandbox_remote.py`**
- Move the `box` fixture's body into a helper `_make_box(provider, tmp_path, monkeypatch, cfg=None, token=True)`, which the fixture calls with the same result. Don't change any existing test body.
- Add these tests, on both Vercel and E2B:
  - A pi start's env has `OPENAI_API_KEY` and no `CLAUDE_CODE_OAUTH_TOKEN`, checked in `box.raw.calls` as well as the process's output.
  - A Claude start keeps the token.
  - Neither provider sets `PI_OFFLINE`, for either harness.
  - `_env` doesn't let a host's `PI_OFFLINE` cross.
- Add open tests, using `monkeypatch.setattr(sandbox_remote.Remote, "_check_tools", lambda self: None)` and a missing `TOKEN_FILE`:
  - An all-pi roster opens a VM.
  - A Claude or mixed roster raises `SandboxFailed` naming the token file, and no VM is created.
  - An all-pi box given a non-pi start sends no `CLAUDE_CODE_OAUTH_TOKEN` key at all, not an empty one.

### 6. Docs

- **`docs/designs/openshell-sandbox.md`**
  - Line 18: keep the finding, and correct the sentence on `curl` to `api.anthropic.com`. That test ran outside an agent, through `exec`, so no `claude.exe` was among its parents.
  - Decision 5: keep the v1 text, and add that pi runs under OpenShell from this change (2026-11-08, accepted from `docs/research/pi-under-openshell.md`).
  - "As built": add a line saying OpenShell matches a rule against the connecting process and its parents, so a model host's rule covers every command its agent runs, and that each rule is written only for a roster with that harness.
  - "As built": add a line saying the one image holds pi, `ripgrep` and `fd-find`.
- **`.claude/skills/sssf/references/config.md`**
  - Line 49: `Under --sandbox vercel|e2b|openshell`, with no "runs no pi agents".
  - Line 369: remove "Claude Code agents only". Say a roster's pi agents must be on `openai/` models, and that a roster with no Claude Code agent needs no token file.
  - Line 373: describe the rules per harness. `claude.exe` may reach `api.anthropic.com` for a roster with a Claude Code agent, and `/usr/local/bin/node` may reach `api.openai.com` for a roster with a pi agent. Say each rule covers every command that agent runs, since OpenShell matches parents, and that the two env values travel on stdin. Mention that `PI_OFFLINE=1` is set for pi here.
- **`.claude/skills/sssf/cookbooks/run_adw.md`** line 126: pi runs there. `token.env` is needed only for a roster with a Claude Code agent. The network line names both model hosts per roster.
- **`docs/guide/run.md`** line 234 and **`docs/guide/install.md`** line 198: drop "Claude Code rosters only", and in `run.md` name both model hosts.
- **`docs/open.md`**: delete only the row on pi under `--sandbox openshell` (line 25).
- **Help text.** Run `rg -n "openshell" .claude/skills/sssf/templates/adws/adw_*.py`, and correct any help line that says the mode is for Claude Code only. Leave the file alone if none does.

There's no `CHANGELOG` in this repo and no `app_docs/` folder, so neither needs a change. `README.md` doesn't mention OpenShell. `.claude/skills/sssf/templates/env.sample` wasn't reviewed, because SSSF's secret-file rule keeps it from agents. See "For the engineer". The research note and `docs/designs/remote-sandboxes.md` are records, so leave them.

## Verification

From the skill root, the repo's own command:

```
cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
```

The whole suite must pass. The only edits to existing tests are the flip and the single pinned hash above, plus moving the fixture's body into its helper.

From the repo root:

```
uv run .claude/skills/sssf/scripts/e2b_template.py --help
```

This must exit 0. It proves the script's own environment, which has no pyyaml, can import the shared constant.

Review by reading:
- `git diff` shows nothing under `adws/adw_modules/`.
- No new host appears in `policy()`.
- `PI_OFFLINE` is set only through `OpenShell.pi_env`.

## For the engineer

These are outside the builder's paths or this build:
1. After the merge, roll the skill out to the installed copies, including this repo's protected `adws/adw_modules/`, as usual.
2. Run `just openshell-setup` in each repo using OpenShell. The tag changes, so a run stops and names that recipe until then.
3. Run the proof in section 5 of `docs/research/pi-under-openshell.md`, with `access: full` and the audit pass reading the paths. Its step 2's L7 expectation applies only once the rule is narrowed.
4. Check whether `.claude/skills/sssf/templates/env.sample` says every sandbox run needs the Claude token. No agent could read it for this plan.
