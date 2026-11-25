# Keep a project's pi files out of the pi SSSF starts on the host

Run it with `--worktree` and `--base main`, on the hub's own config. The change is in `agent_pi.py`, which is guard code, so it goes through the chain.

## What broke

`docs/open.md` has the row ("A project's own `.pi/settings.json` can run a command in the host's `pi --list-models`"). In short:

- **SSSF starts pi on the host, outside any wrap.** `agent_pi._pi_catalog` runs `[PI_PATH, "--list-models", *HOST_FLAGS]` with `operator_env()`, which keeps `.env`'s keys, from the orchestrator's working folder, which is the project's checkout or a run's worktree. Other host starts are `pi auth print-bearer-token` (`_host_token`, `vm_token`, `container/pi_token_broker.py`) and `pi auth check` (`_sign_in`, `trial.py`).
- **pi reads the project's own files.** When pi trusts a project, it loads `<cwd>/.pi/settings.json` and resolves its `packages`, npm-installing a missing one through the `npmCommand` that file sets. `HOST_FLAGS` (`--no-extensions` and four more) doesn't stop that: `resource-loader`'s `packageManager.resolve` still runs under `noExtensions`. So a `.pi/settings.json` with `packages` and `npmCommand` runs a command as the engineer, with `.env` in its environment, the next time an orchestrator resolves a pi model.
- **Getting a file there.** `.pi/` is in `protected_files`, so the after-call check stops an agent that writes it through git. An agent needs the ignored-files hole (`docs/open.md`, "The after-call check can't see gitignored files") or a project that gitignores `.pi/`. Found from source by the chain's review of guard run 2738b794, on `main` and its head alike, and confirmed by the bypass review of cd9eb718. No pi was run.

What pi 1.4.0's source shows (`<npm global>/@earendil-works/pi-coding-agent/dist`), checked on 2026-11-24:

- `--no-approve` (`-na`, "Ignore project-local files for this run") sets `projectTrustOverride = false` (`cli/args.js:232`). In `main.js:595-600` that makes `projectTrusted` false, and `SettingsManager.loadFromStorage` returns `{}` for the project scope when it is (`core/settings-manager.js:258`).
- **That isn't everything.** `main.js:535` builds `startupSettingsManager` with `SettingsManager.create(cwd, agentDir)` and no trust option, and `projectTrusted` defaults to `true` (`settings-manager.js:238`). Its project settings feed `getSessionDir()` (`main.js:554`, used to make the session manager), `getDefaultProjectTrust()` (line 615) and the first-time setup (line 540). `runMigrations(cwd)` (line 533, `migrations.js:274`) runs `migrateExtensionSystem(cwd)` on the project. `hasTrustRequiringProjectResources` (`core/trust-manager.js:151`) walks up from `cwd` looking for `.agents/skills`.
- `pi auth` returns at `main.js:460`, before line 533. Check what it reads from `cwd` on its way there.

## The change

1. **Nothing from the project.** Every pi SSSF starts on the host reads, loads and runs nothing from the project's tree or any folder above the orchestrator's working folder: no `.pi/settings.json`, no `.pi/` resource, no `.agents/skills`, no `AGENTS.md`, and no migration that touches it. Find each host start (the list above, and any other, scripts included) and say in the spec what each reads from `cwd` today, with the source lines. Then pick the fix and say why: start pi with `--no-approve`, start it from a folder that holds no project (an empty folder SSSF makes, whose parents hold no `.pi/` or `.agents/`), or both. `--no-approve` alone leaves `main.js:535`'s startup settings, so if the spec picks it alone, it shows from source that nothing those settings set can run a command, write outside SSSF's folders, or change which credential pi uses.
2. **The model list stays the same.** `_pi_catalog` returns the rows it returns on `main` for the engineer's own `~/.pi/agent/models.json` and built-in providers. Custom providers live in the agent dir, not the project, so moving `cwd` shouldn't change them; say so from source.
3. **Agent calls.** A wrapped pi agent's call keeps its `cwd`, the repo, since an agent works there. Say in the spec whether an agent's pi should also get `--no-approve`, what it would lose (project skills, `AGENTS.md`, project prompt templates), and pick. Skills and context files stay on for agents today (`references/config.md`, "Discovery is off"), so keep them unless the spec shows a hole.
4. **Docs.** Delete the `docs/open.md` row named above, or cut it to what's left. `references/config.md`'s "Discovery is off" bullet says what the host's pi starts no longer read.

## The bar

- **The planted file.** In a temp repo with `.pi/settings.json` holding `{"packages": ["npm:sssf-probe"], "npmCommand": ["sh", "-c", "touch <marker>"]}`, and a fake `pi` that records its argv and `cwd`: `_pi_catalog` and each other host start pass what the spec picked, and none starts with `cwd` in the temp repo if the spec moves it. On `main` the same call starts in the repo with no `--no-approve`.
- **One real pi, no network.** The planted `.pi/settings.json` in a temp repo whose `npmCommand` would touch a marker, and the real `pi --list-models` run the way `_pi_catalog` runs it, with `PI_CODING_AGENT_DIR` at an empty temp agent dir and `--offline`: the marker isn't made. On `main`'s argv and `cwd`, in a project pi trusts, the spec says whether it is. Mark the test so it skips where pi isn't installed. If pi can't be run this way without touching `~/.pi/agent`, leave it for the engineer's proof and say so.
- **The rows.** `_pi_catalog` with a fake `pi` returns the same rows on `main` and the head.
- **Nothing else changes.** Every existing test passes on the host, `test_agent_pi.py`'s argv tests among them.

## After the merge, the engineer's proof

Update the hub's own install and commit it. Then, in a scratch repo with the planted `.pi/settings.json` and the hub's pi trusted for it, run the host's model list the way `_pi_catalog` does and see no marker. Run one pi-roster call through `adw_prompt.py` and see it work.

## Running the tests

The planner and the builder run under agentsh. Run only `tests/test_agent_pi.py` from `.claude/skills/sssf` with `-m 'not pushes and not host_only'`, and leave the whole suite to verify. A whole-suite run under agentsh makes the after-call event read take many minutes (`docs/open.md`). Don't run a real pi against `~/.pi/agent`, and don't write anything under `~/.pi`; read pi's source from its install folder.

## Out of scope

- The ignored-files hole that gets a file into `.pi/`.
- The starter harness's pi subagents with no text check (its own `open.md` row).
- What a wrapped pi agent reads from the project, past step 3's choice.
- The agentsh policies.
