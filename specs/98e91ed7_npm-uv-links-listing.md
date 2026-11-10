# Plan: npm config, links to uv's managed Pythons, and folder listing under agentsh

Next item 12 of `docs/designs/agentsh-local-sandbox.md`. Probes: section "The tool-config run under agentsh, 130ca6e0" in that doc.

## Baseline

`main` at `ec90a97` (skill 9ccf50d's guard). Judge every finding against that commit. The builder works on the current branch, `guard-agentsh-tool-fixes`, which is at `ec90a97`.

## Threat model

An agent runs as the engineer's own user, under `agentsh wrap` with SSSF's default-deny policy. It can run any command the exec rules allow, and open, write or link any path the file rules allow. The kernel lets it do anything else the engineer's user can do, wherever the policy says yes. So any `allow` on a path the engineer owns is a real grant.

How agentsh judges a link (design doc, "The ninth patch: link targets", and `Rules.link` in `.claude/skills/sssf/tests/test_agentsh_guard.py`): it checks the link's own path and its resolved target, both as `symlink`, and the first deny wins. `SYSTEM_LINK` is safe today only because the kernel refuses a new name in `/usr/bin` and `/bin`. Under `~/.local/share/uv/python` the engineer owns every folder, so the kernel won't refuse anything there. **An allow of `${HOME}/.local/share/uv/python/**` would let an agent make a link anywhere in that tree**, for example `<install>/lib/python3.13/site-packages/sitecustomize.py` pointing to stdlib `antigravity.py`. That is a new file in the engineer's interpreter, and it runs every time the engineer starts that interpreter. That would break the bar. So this plan narrows the request's glob to the one name a venv's `python` link resolves to, the interpreter binary:

`${HOME}/.local/share/uv/python/*/bin/python3.*`

The policy's `*` doesn't cross `/` (see `glob_regex` in the test file). On this machine, `~/github/sssf-sandbox/.venv/bin/python` links to `~/.local/share/uv/python/cpython-3.13.10-linux-x86_64-gnu/bin/python3.13`. Every uv install's `bin/` holds `python3.<minor>` (and `python3.<minor>-config`). `python` and `python3` are relative links to it, and the engine resolves through them.

What stays possible: an agent can add a new link named `python3.<something>` in an existing install's `bin/`, where that name isn't taken yet (for example `python3.99`), pointing to a file in those installs, `/usr/bin` or `/bin`. Nothing looks a Python up by such a name. uv runs an install by its fixed `python3.<minor>`, and `~/.local/bin`'s links name exact files. An existing name can't be replaced, because `symlink(2)` answers EEXIST and the policy still refuses delete and rename there. No other write, create, mkdir, hard link, chmod or rename under `~/.local/share/uv` is allowed by any rule. That's `HOME_READ`, which is read-only, plus the default deny. The `.lock` and `.temp` there stay unwritable.

`UV_PYTHON_INSTALL_DIR`'s folder in the agent cache needs nothing new. A link there has its own path and its target both inside the agent cache, and `sssf-agent-cache` allows every operation there, `symlink` included. That rule comes before `sssf-system-link`.

npm: `npm_config_userconfig` and `npm_config_init_module` point at files in the agent cache. An agent may write both, as it may write the rest of the cache. Another wrapped run of the same repo would then read that npmrc. It's the same exposure as the shared cache, already in `config.md`'s limits. npm under the wrap can reach only the allowed hosts. The engineer's own npm never reads these files, because the variables are set on the wrapped agent only.

What this guard can't stop at its level: anything outside the wrap (quality checks, host git, the engineer's editor) following a link an agent made. That's already recorded in `config.md`.

## Bar

Must:
- Under the wrap, these work: `npm install`, `npm init -y`, `npx`, and `uv run --no-project --with pyyaml` on a venv built on a uv-managed Python.
- `cache_env` sets `npm_config_userconfig` and `npm_config_init_module` into the agent cache, the same for both harnesses.
- `sssf-system-link` gains exactly `${HOME}/.local/share/uv/python/*/bin/python3.*`, and nothing else new.
- `config.md` records the folder listing as a limit, and no line anywhere still promises that `list` stays denied on the folders above the workspace.

Never, against `ec90a97`:
- No agent file kept: no write, create, rename, delete, mkdir, chmod or hard link under `~/.local/share/uv`, `~/.npmrc`, `~/.npm`, `~/.cache/uv` or `~/.local/bin`. The one residual is a new `python3.*` link name in an install's `bin/`, recorded in Threat model and in `config.md`.
- No link whose own path is outside `bin/python3.*` in the managed tree gets allowed there. That includes `site-packages`, `lib`, the install root and `.temp`.
- A link target outside `/usr/bin`, `/bin` and `~/.local/share/uv/python/*/bin/python3.*` stays refused, `/etc/shadow` among them.
- No read of a file in a sibling repo.
- No other change to the policy text, rule order or env.

## Out of scope

- Folder listing itself. An agent can list `/`, `/home`, the home folder, `~/github` and the folders down to the workspace, so it sees the names of other repos and of `adws/adw_data` sessions and worktrees, but nothing inside them. This plan only records it. Closing it takes a fork patch that judges a folder opened for its entries as `list`.
- pnpm, which fails on this machine without the wrap. Its `npm_config_store_dir` / `npm_config_cache_dir` make npm warn "Unknown env config". That's old and stays.
- `npm install -g` and the global prefix (`~/.nvm/...`), still refused as on the baseline.
- npm's `globalconfig`, `<prefix>/etc/npmrc`, and its built-in npmrc. Both are under `~/.nvm/**` or `/usr`. The policy lets an agent read them and not write them, so a missing one answers ENOENT and npm goes on. Moving them would drop admin settings for nothing.
- The three items left by run 130ca6e0 (Next item 11). pi's policy.

## npm's files in the home folder (checked)

From npm 11.6.2's `@npmcli/config/lib/definitions/definitions.js` and `lib/cli/update-notifier.js` on this machine:
- `userconfig`, default `~/.npmrc`: moved, as `npm_config_userconfig`.
- `init-module`, default `~/.npm-init.js`: moved, as `npm_config_init_module`. The deprecated `init.module` alias has the same default, and npm reads `init-module`.
- `cache`, default `~/.npm`: already moved. `logs-dir` defaults to `<cache>/_logs`, `npx` uses `<cache>/_npx`, and the update notifier writes `<cache>/_update-notifier-last-checked`, so all three follow the cache.
- `globalconfig` and `prefix`: under the node install, which the policy lets an agent read and not write. Not moved; see Out of scope.

What an agent loses: the engineer's `~/.npmrc` on a machine that has one, private registry settings and auth tokens included. It was already denied by `sssf-credentials`, so npm stopped there before this change. A repo that needs registry settings puts them in its own project `.npmrc`, which npm still reads from the workspace. Its tokens should come from the environment, since `.npmrc` isn't a secret file to the policy. The registry host must also be in `network_allow`.

## Changes

### 1. `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`

- `cache_env`: add, after `"npm_config_cache"`:
  - `"npm_config_userconfig": f"{folder}/npmrc"`
  - `"npm_config_init_module": f"{folder}/npm-init.js"`

  Update the docstring to say it also moves npm's user config and init module. `start()` already strips any same-named variable whatever its case, so an engineer's `NPM_CONFIG_USERCONFIG` is replaced. No change there.
- `SYSTEM_LINK`: becomes `["/usr/bin/**", "/bin/**", "${HOME}/.local/share/uv/python/*/bin/python3.*"]`. Extend the comment above it. A link to uv's managed interpreter binary is for a venv built on one, as in `uv run --with`. The engine judges a link's own path with this rule too, and the engineer owns that tree, so the glob names only the interpreter file and never the tree. Existing names answer EEXIST. A new `python3.*` name in `bin/` is the residual, and nothing runs by that name.
- The comment above `FOLDER_OPS` (lines 100–103): keep its reasoning, and add one sentence. In ptrace mode the engine judges opening a folder as `open` and doesn't check reading its entries apart from that, so this rule lets an agent list these folders; see `config.md`. Change the comment only. `FOLDER_OPS` stays as it is.

### 2. `docs/designs/agentsh/deny-policy.py`

`test_file_rules_equal_the_designs_deny_policy_output` requires its file rules to match the guard's. Make the same `SYSTEM_LINK` change and comment change at line 67 (and the `FOLDER_OPS` comment if it has the same text). Don't change anything else.

### 3. `.claude/skills/sssf/tests/test_agentsh_guard.py`

- `test_the_agents_caches_are_set_for_both_harnesses_and_beat_the_engineers`: assert `want["npm_config_userconfig"] == f"{guard.agent_cache}/npmrc"` and `want["npm_config_init_module"] == f"{guard.agent_cache}/npm-init.js"`. Add `"NPM_CONFIG_USERCONFIG": "/home/u/.npmrc"` to the launch env and assert it's gone from the child env. The existing loop already checks both harnesses get equal values.
- `test_links_to_system_pythons`:
  - Change the pin to `agentsh_guard.SYSTEM_LINK == ["/usr/bin/**", "/bin/**", "${HOME}/.local/share/uv/python/*/bin/python3.*"]`.
  - Allowed: `r.link(f"{main}/.v/bin/python", f"{HOME}/.local/share/uv/python/cpython-3.13.10-linux-x86_64-gnu/bin/python3.13")`, and the same with a link in the agent cache (`f"{main}/adws/adw_data/agent-cache/uv/x/bin/python"`).
  - Denied, by link path. Each case links to `/usr/bin/python3.12` and to the managed `python3.13`, and must come out `deny`:
    - `<install>/lib/python3.13/site-packages/sitecustomize.py`
    - `<install>/lib/python3.13/site-packages/x.pth`
    - `<install>/x`
    - `~/.local/share/uv/python/x`
    - `~/.local/share/uv/python/.temp/x`
    - `<install>/bin/pip9`
    - `~/.local/share/uv/tools/ruff/bin/python3.13`
    - `~/.local/bin/python3.13`
  - Denied, by target. Each case is linked from `f"{main}/l"` and must come out `deny`:
    - `<install>/lib/python3.13/os.py`
    - `<install>/bin/pip`
    - `~/.local/share/uv/tools/ruff/bin/ruff`
    - `~/.local/share/uv/python/x`
    - `~/.local/share/uv/credentials/credentials.toml`
  - Allowed, and documented as the residual: `r.link(f"{install}/bin/python3.99", "/usr/bin/python3.12") == "allow"`. Its comment says the kernel answers EEXIST for an existing name.
- In the test that checks writes under the engineer's trees (the one around line 1725 that loops `("write", "create", "mkdir")`), add `<install>/bin/python3.13`, `<install>/lib/python3.13/site-packages/x.pth`, `~/.local/share/uv/python/.lock` and `~/.npmrc` for `write`, `create`, `mkdir`, `rename`, `delete`, `link` and `chmod`, all `deny`.
- New test `test_the_system_link_rule_adds_only_the_managed_interpreter`: render the shipped policy for both harnesses (`shipped_text(main, harness)`). Use `rule_of(text, "sssf-system-link")` to assert its paths equal `SYSTEM_LINK` and its operations are `["symlink"]`. Then assert that the set of `${HOME}/.local/share/uv/python` paths across the whole policy, minus the `HOME_READ` ones, is exactly the new glob.

Build every config from the shipped `sssf.config.yaml`, as the file already does. Add no test-only config.

### 4. `.claude/skills/sssf/references/config.md`, section "Running local agents under agentsh"

- Line 456. Replace "The folders themselves can be opened and checked, not listed." with "The folders themselves can be opened and checked, and that lets an agent list them; see the limits below."
- In the same paragraph, add `npm_config_userconfig` and `npm_config_init_module` to the list of variables SSSF sets. After "Agents no longer write …", add: "npm reads its user config and `npm init`'s module from the agent cache, not `~/.npmrc` and `~/.npm-init.js`, so a registry setting or token in your `~/.npmrc` doesn't reach agents (it was denied before). Put a repo's registry settings in its own `.npmrc`, take tokens from the environment, and add the host to `network_allow`."
- Replace the limits bullet "An absolute link may point into a system folder only in `/usr/bin` (or `/bin`) …" with this: an absolute link may point into a system folder only in `/usr/bin` (or `/bin`), or at a uv-managed Python's interpreter, `~/.local/share/uv/python/<install>/bin/python3.<minor>`, so a venv built on a managed Python works with `uv run --with`. A Python elsewhere, under `/usr/local` or `/opt` for example, can't be a venv's base. Because agentsh judges a link's own path by the same rule, an agent can add a new link named `python3.<something>` in a managed install's `bin/`. Nothing runs a Python by such a name. It can't change, replace or remove any file there.
- Change the "A fresh venv needs a Python in `/usr/bin` …" bullet only as far as it now reads wrong. An existing `.venv` on a managed Python now also works for `uv run --with`. A fresh venv still can't use the managed Pythons, because `UV_PYTHON_INSTALL_DIR` hides them.
- New limits bullet: "Under agentsh an agent can list every folder from `/` down to the workspace, `~/github` (or wherever your repos sit) and `adws/adw_data` among them, so it can see the names of your other repos and of the sessions and worktrees in `adws/adw_data`, but not read inside them. In ptrace mode agentsh judges opening a folder as `open` and doesn't check reading its entries apart from that. Closing this needs a fork patch that judges a folder opened for its entries as `list`."

### 5. `docs/designs/agentsh-local-sandbox.md`

Line 948. End the bullet's last sentence, "`list` stays denied on the folders.", with ", by the rule; on the engine the `open` lists them ([the probes below](#the-tool-config-run-under-agentsh-130ca6e0))." Leave the Next list and every other section as they are. The hub session records item 12 after its probes.

### Docs this makes out of date

- `config.md` and the guard's comments: covered above.
- No CHANGELOG in the repo.
- `app_docs/`: don't edit it. For the documenter: any write-up of run 130ca6e0 / skill 9ccf50d that says the folders above the workspace can't be listed, that only `/usr/bin` and `/bin` link targets are allowed, or that npm reads `~/.npmrc`, now describes old behaviour.

## Verification

The builder runs under agentsh, and the skill suite reads the hub's `.env` at import. As in run 130ca6e0, run the suite from a copy outside the repo. Copy `.claude` and `docs/designs/agentsh` to a fresh `/tmp` folder, keeping their relative paths so `DENY_POLICY` resolves, and run there:

`uv run --no-config --directory <copy>/.claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_agentsh_guard.py -q -p no:cacheprovider`

Then run the whole `tests` folder the same way. Failures from file or connect denials of the wrap are known (run 6e904be8: 172 failed, 12 errors at its head). Compare your count with that, and report any new failure in `test_agentsh_guard.py`. SSSF's own quality checks run outside the wrap, and they decide. The repo's test check is `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`.

Must pass:
1. `test_agentsh_guard.py` in full, the new and changed tests among them. `test_file_rules_equal_the_designs_deny_policy_output` too, for both harnesses.
2. A diff of the rendered policy against `ec90a97` for one checkout and both harnesses shows only the one new `sssf-system-link` path. Render it with `agentsh_guard.policy_text(repo_settings.RepoSettings(), "/m/proj/.git", h, "n", sessions="/m/proj/adws/adw_data/sessions", refuse_script="/m/proj/adws/adw_modules/sssf_refuse.py")` from this tree and from `git show ec90a97:<path>` in a temp folder. Put the diff in the build notes.
3. `cache_env("/c")` differs from `ec90a97`'s by exactly the two npm keys.

Can't run in the build (the builder can't install a policy). See For the engineer.

## For the engineer

`adws/adw_modules/` is protected. The hub's own `adws/adw_modules/agentsh_guard.py` renders the policy that `check_machine` compares with the installed one, so after the merge:

1. Bring the hub's `adws/` onto the new skill, as fa43096 did for 9ccf50d. Then stop every agent and reinstall with `just agentsh-install` and the sudo lines it prints. Do the same in sssf-sandbox.
2. The hub session reruns the 2026-11-10 probes under each policy, with the variables `cache_env` sets, in the hub and in sssf-sandbox:
   - These must work: `npm install`, `npm init -y`, `npx -y cowsay hi` (or any registry package), `npm config get userconfig` naming the agent cache, and in sssf-sandbox `uv run --no-project --with pyyaml python -c 'import yaml'`.
   - These must be refused: writes to `~/.npmrc`, `~/.npm`, `~/.cache/uv` and `~/.local/bin`, a file write under `~/.local/share/uv/python`, a read of a sibling repo's README, and a link to `/etc/shadow`.
   - Two link probes inside the managed tree: `ln -s /usr/bin/python3 <install>/lib/python3.13/site-packages/sitecustomize.py` must be refused. `ln -s /usr/bin/python3 <install>/bin/python3.99` is expected to pass, as the residual. Remove it outside the wrap afterwards.
   - A Claude Code agent under each policy runs the npm and uv commands and exits 0.
3. Record the results under item 12 in the design doc.
