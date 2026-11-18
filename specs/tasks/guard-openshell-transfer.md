# Move files into an OpenShell VM by upload, not by piped stdin

Run it with `--worktree` and `--base main`, on the hub's own config. `sandbox_openshell.py` is guard code, and so is the env a VM command gets. That's why this goes through the chain.

## What broke

On 2026-11-18 the engineer made OpenShell the default for their repos (decision 15 in `docs/designs/openshell-sandbox.md`). A project's first run under it failed before any agent started. `Remote._clone` (`sandbox_remote.py`, about line 583) pipes the git bundle on stdin, through `sh -c "cat > /tmp/sssf-in.bundle"` with `input=bundle.read_bytes()`. OpenShell 0.1.7 refused it:

> piped stdin exceeds the 4 MiB limit; use `sandbox upload` for larger input

The bundle was 21.5 MB. The branches of three of the engineer's repos have bundles of 14 to 21 MB. Only repos under about 4 MiB of history work today, which is why the proof run in sssf-sandbox (1.7 MB) passed. Decision 9 says the adapter moves files with `upload` and `download`, but the code pipes instead.

The same pipe carries four other inputs, each of which can pass 4 MiB in some repo:
- `write_text`;
- `install`, one of SSSF's own files;
- the obs relay script;
- `handoff_in`, a gzipped tar of the whole handoff folder.

The way out has the same shape: `handoff_out` and `commits_out` read base64 from stdout. Nobody has measured whether `exec` limits output.

The same project's other failure: `credentials()` in `sandbox_remote.py` refuses when the host has `ANTHROPIC_API_KEY` set. In that repo `.env` sets it for the project's own tools, and `utils.load_dotenv` puts it in `os.environ`. But a VM command gets only `SSSF_` and `OBS_` variables (`_env`, `PASSED`, `HELD`), so the host's key never reaches the VM. The project now starts every run with the key blanked on the command line.

A third note from another project's run: every Claude Code agent call under OpenShell logs `sandbox_denied` rows for `claude.exe` reaching `http-intake.logs.us5.datadoghq.com:443`, Claude Code's own telemetry. Six attempts, each logged twice, in one small run. They hide real denials.

## The change

1. **Inputs past OpenShell's stdin limit go in by `openshell sandbox upload`.**
   - The spec finds OpenShell 0.1.7's limits on `exec` stdin and stdout, from its CLI source or docs, or by a check the engineer's session runs (no test or agent starts a real VM). It cites each limit.
   - Every place `Remote` hands the VM bytes on stdin either stays under the limit with a margin the spec names, or goes through upload on OpenShell. That covers `_clone`, `write_text`, `install`, `handoff_in` and the relay. Vercel and E2B keep their path unless the spec shows the same limit there.
   - The upload filters by `.gitignore` inside a git work tree, and stops on an empty selection. The bundle lives under `adws/adw_data/`, which is ignored. Stage what SSSF uploads in a fresh folder outside any work tree, made by SSSF and holding only that file. Never pass a path an agent could have written or linked. The spec says why `--no-git-ignore` is or isn't needed.
   - In the VM, the file lands where the piped write put it, owned the same way, and is removed or kept as today.
   - The way out changes only if the spec finds a stdout limit SSSF can reach. If it does, `download` must not let the VM pick a name or a link that lands outside the staging folder. Keep `handoff_out`'s checks: the `data` filter, and no symlinks or non-files.
2. **The key check judges what reaches the VM.** `credentials()` refuses `ANTHROPIC_API_KEY` only when the env a VM command would get holds it, by any name Claude Code reads as an API key. A key the host keeps, whether from `.env` or the shell, no longer stops the run. The spec lists each way a host variable reaches the VM, across all three providers, and shows the check still covers every one.
3. **Claude Code's telemetry is off in the VM, if the spec shows it's a narrowing.** A Claude Code agent's VM env gets Claude Code's documented setting that turns off its non-essential traffic, such as `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, with a fixed value and never one read from the host. The spec cites the setting's documentation and says what else it turns off. If it also stops something an SSSF run needs, such as the model call, the sign-in or the version check SSSF relies on, leave this out and say so.
4. **Docs.**
   - Decision 9 and "As built" in `openshell-sandbox.md` say how files move now.
   - `config.md`'s OpenShell section names the stdin limit and what goes by upload.
   - The `ANTHROPIC_API_KEY` sentence in `config.md`, or wherever the docs describe that refusal, says it applies only when the key would reach the VM.

## The bar

- **Big inputs work.** Through `tests/test_sandbox_openshell.py`'s fake CLI, taught to refuse stdin over 4 MiB with OpenShell's message, a run's VM opens and clones a branch whose bundle is 21 MB. A handoff folder over 4 MiB goes in and comes back. Build the big inputs inside the test, never on a command line.
- **The fake fails like the real one.** Without the change, the 21 MB clone fails in the fake with OpenShell's message, so the test can't pass by accident.
- **Nothing extra reaches the VM.** The upload sends exactly the bytes the piped write sent. A file an agent planted next to the bundle, or a symlink in its place, doesn't reach the VM or the host's upload command.
- **The key.** With `ANTHROPIC_API_KEY` in the host's `os.environ` and not in the VM's env, a run's sandbox opens. With the key in what the VM would get, under any name the spec lists, it refuses as today.
- **Telemetry**, if change 3 ships: a Claude Code agent's VM env holds the setting with its fixed value. A host value of the same name doesn't override it, and pi agents don't get it.
- **No false stop, nothing else changes.** Every existing test passes. Vercel and E2B runs send the same commands as on `main`, unless the spec shows they share the limit. With small inputs, an OpenShell run's trace events and saved patch are as on `main`.

## Running the tests

Run the touched test files from `.claude/skills/sssf` with `-m 'not pushes and not host_only'`, and leave the whole suite to verify. A 21 MB input passes agentsh's 128 KiB limit for one argument: build it in the test and send it on stdin or in a file, never as an argument, and mark any test that can't avoid it `host_only`. No test starts a real VM. Keep runs light: the agentsh event store slows under heavy load (`docs/open.md`).

## Out of scope

- The procedure for switching an installed repo's sandbox, and the log-row names in the docs. The hub session makes those doc edits on `main`.
- The task-limit row (each ADW in its own systemd scope).
- The P3 host commit check rows.
