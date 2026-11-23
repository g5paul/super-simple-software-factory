# Keep a pi agent from planting an extension in ~/.pi

Run it with `--worktree` and `--base main`, on the hub's own config. The change is in `agentsh_guard.py` (the pi policy's writes) and `agent_pi.py` (how SSSF starts pi), which are guard code, so it goes through the chain.

A first run, 2738b794 (2026-11-22), passed the chain and was set aside unmerged, because the bypass review before the merge found a bypass. Its spec is `specs/2738b794_pi-own-agent-dir.md`. Start from it: its reading of pi 1.4.0's source holds, and so does its design. `auth.json` is a file a wrapped pi must write and also one pi runs `!` commands from, so narrowing `PI_WRITE` isn't safe. Wrapped pi agents get their own agent dir, a host-issued sign-in token, and no writes under `~/.pi`. Check what it says against the code at `main`. The review found three things it got wrong:

- **The bypass.** Before every call, it copied the engineer's `~/.pi/agent/models.json` to `sessions/<adw_id>/pi-agent/models.json` and never removed it. Every agent of every run may read the sessions tree (`sssf-sessions` in `file_rules`, "Every run's, decided 2026-11-09"), Claude Code agents included. On `main`, `sssf-default-deny` keeps `~/.pi/agent/models.json` from a Claude Code agent. pi reads a provider's `apiKey` and header values as literals unless they start with `!` or name a `$VAR` (`resolve-config-value.js`). So once a pi agent had run in a repo, a later Claude Code agent could read a custom provider's literal key from the copy. The chain's own review passed it.
- **A false claim in `config.md`.** It deleted the container's known limit on a `settings.json` package, saying `--no-extensions` means neither an extension nor a package loads. In pi 1.4.0, `resource-loader`'s `packageManager.resolve` still runs under `noExtensions` and npm-installs missing packages through `getNpmCommand()`, which `settings.json` can set. So a `settings.json` in the agent dir with `packages` and `npmCommand` runs a command at every pi start: inside the wrap on the host, and in the container, where it could plant an openai login after `check_stored_login`.
- **A raw error.** An agent-made folder at `pi-agent/auth.json` made SSSF's rewrite raise `IsADirectoryError`, not `SandboxFailed`, and every later pi call sharing that folder failed with a traceback.

## What broke

`docs/open.md` has the row ("A pi agent under agentsh can plant code that runs unwrapped on the host the next time anything starts pi"). In short:

- **A wrapped pi agent can write anywhere in `~/.pi`.** `file_rules` gives the pi policy `PI_WRITE = ["${HOME}/.pi", "${HOME}/.pi/**"]`.
- **pi runs every extension it finds.** On every start, even `pi --list-models`, pi loads each `*.js` or `*.ts` file in `<agent dir>/extensions/` (`dist/core/extensions/loader.js:683` in the installed `@earendil-works/pi-coding-agent`). The agent dir is `~/.pi/agent` unless `PI_CODING_AGENT_DIR` moves it. pi's trust check covers only a project's own `.pi/` folder, not the agent dir. `<agent dir>/settings.json` can list extension packages too.
- **SSSF starts pi outside the wrap.** `agent_pi._pi_catalog` runs `pi --list-models` on the host with `operator_env()`, which keeps `.env`'s keys, to resolve a pi agent's model. So an extension a pi agent writes to `~/.pi/agent/extensions/` runs in the next pi-roster run's orchestrator, as the engineer, with `.env` in its environment. The engineer's own pi runs it too.
- **Wrapped pi agents load it as well.** `agent_pi`'s command passes SSSF's extensions with `-e`, the refuse extension last, but not `--no-extensions`, so a later run's wrapped agents also load what's in the agent dir's `extensions/`, beside the refuse extension.

The engineer confirmed it on 2026-11-22 on a decoy. Under the installed policy `sssf-pi-agent-observability-pi`, a wrapped `bash -c` wrote a probe extension to `~/.pi/sssf-decoy/agent/extensions/probe.js`; under the hub's Claude policy the same write was refused. An unwrapped `PI_CODING_AGENT_DIR=~/.pi/sssf-decoy/agent pi --list-models` then ran the probe, with no sign-in and no model call. `~/.pi/agent` was not touched.

pi has `--no-extensions`: "Disable extension discovery and built-in extensions (explicit -e paths still work)". `--no-skills`, `--no-prompt-templates`, `--no-themes` and `--no-context-files` exist too.

## The change

1. **The pi policy's writes.** Narrow `PI_WRITE` to what a wrapped pi has to write. Find out what that is and say it in the spec, with how you found it: pi's source, and what a wrapped pi run writes (its session files go to `--session-dir`, under SSSF's data folder; check `auth.json` when pi refreshes a token, `models-store.json`, and anything else). Nothing under `<agent dir>/extensions/`, `settings.json` or any other folder pi loads code or config from is writable. If a file pi must write is also one it loads config from, say so and how the spec handles it. If narrowing can't be done safely, say why, and give wrapped pi agents an agent dir of their own through `PI_CODING_AGENT_DIR` instead, with the sign-in they need.
2. **pi started on the host.** `_pi_catalog` starts pi with `--no-extensions`, and with whatever else the spec finds pi loads code or config from that `--list-models` doesn't need. Find every other place SSSF starts pi outside the wrap (scripts included) and give each the same.
3. **pi started for an agent.** `agent_pi`'s command adds `--no-extensions`, so pi loads only the `-e` paths SSSF passes, the refuse extension still last. Say in the spec whether pi then still loads every `-e` path, built-in tools and the extensions a roster names (`extensions` in `sssf.config.yaml`), and what else pi discovers on its own (skills, prompt templates, context files) that a planted file could use; turn off what no agent needs, and say why for what stays.
4. **No engineer file reaches another agent.** Whatever SSSF copies or writes for a wrapped pi agent (the `models.json` copy, the sign-in, anything else) is readable by no agent that couldn't read the original on `main`: not a Claude Code agent, and not an agent of another run. Either the Claude policy denies the pi agent dir ahead of `sssf-sessions`, or the files live outside the shared sessions tree, or the spec finds something better. Say which, why, and how a worktree run's agents are covered too. Go through every file SSSF writes for a pi agent and say who can read it.
5. **A broken agent dir stops cleanly.** Anything an agent leaves in the pi agent dir that stops SSSF's own writes (a folder or a link where a file goes) ends the call with `SandboxFailed` naming the path, before pi starts.
6. **Docs.** Delete the `docs/open.md` row named above, or cut it to what's left. `references/config.md` says what a pi agent may write in `~/.pi` and that SSSF starts pi with discovery off. It says what `--no-extensions` leaves running (settings `packages` and `npmCommand`), and every container known limit stays unless the spec shows from pi's source that the change closes it.

## The bar

- **The decoy.** A test on the pi policy's file rules: a write to `${HOME}/.pi/agent/extensions/x.js`, to `${HOME}/.pi/agent/settings.json`, and to `${HOME}/.pi/<other>/agent/extensions/x.js` matches no allow rule. Each file the spec says a wrapped pi must write still matches one.
- **The host call.** `_pi_catalog`'s command carries `--no-extensions`. A test with a fake `pi` sees the flag, and `operator_env()` is still what it gets.
- **The agent call.** `agent_pi`'s command carries `--no-extensions`, every `-e` path is still there, and the refuse extension is still last (the existing test that pins it passes).
- **The copy stays private.** With a dummy `models.json` holding a literal `apiKey`, after a wrapped pi call SSSF prepares: for each file SSSF wrote for that agent, a read by the Claude policy, under the main checkout and under a worktree as `PROJECT_ROOT`, decides `deny`, and so does one by another run's pi agent if the spec says it should. On 2738b794's head the same read was allowed.
- **A folder where a file goes.** With a folder at the agent dir's `auth.json`, and again at `models.json`, the call raises `SandboxFailed` naming the path, and the wrap is never started.
- **Only what the spec names.** Every other rule in the pi and Claude policies is as on `main`, for each roster. A test compares them.
- **Nothing else changes.** Every existing test passes on the host.

## After the merge, the engineer's proof

Not part of the run, since the tests install no agentsh policy:

1. Update the hub's own install and commit it. Rerun `agentsh_install.py` so the installed policies match (`just` recipe or the install doc), for the hub and for pi-agent-observability, whose session updates its own SSSF install.
2. Repeat the decoy under the new pi policy and see the write to `~/.pi/sssf-decoy/agent/extensions/` refused. Then run one pi-roster call and see it work.

## Running the tests

The planner and the builder run under agentsh. Run only `tests/test_agentsh_guard.py` and `tests/test_agent_pi.py` from `.claude/skills/sssf` with `-m 'not pushes and not host_only'`, and leave the whole suite to verify. A whole-suite run under agentsh makes the after-call event read take many minutes (`docs/open.md`). Don't run a real pi against `~/.pi/agent`, and don't write anything under `~/.pi`; read pi's source from its install folder.

## Out of scope

- The xattr gap (agentsh doesn't trace `setxattr`), which is agentsh's.
- pi's own trust model for a project's `.pi/` folder, past what step 3 turns off.
- The Claude policy's writes.
- VM runs, where `~/.pi` is the VM's.
