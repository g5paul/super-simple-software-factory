# Plan: let a pi roster with extensions run in a VM

Builds decisions 20 to 27 of `docs/designs/remote-sandboxes.md` ("pi extensions in the VM"). Decision 27's proof on real VMs happens by hand after the merge. The tests below stand in for it without a VM.

All code lives under `.claude/skills/sssf/templates/adws/adw_modules/`. All tests live under `.claude/skills/sssf/tests/`. The hub's installed copy, `adws/adw_modules/`, is protected: see "For the engineer".

## Baseline

`main` at `3491a4e` ("List the obs relay under Open in remote-sandboxes.md"). Every finding is judged against that commit. What it does now:

- `sandbox.check_roster` (`sandbox.py:156`) raises `SystemExit("--sandbox can't take pi extensions yet …")` for any pi agent that has `harness_engineering`. `session.ensure` reaches it through `sandbox.prepare`. So does a resume without the flag (`session._open_sandbox`), and so does `trial.vm_problems`.
- `worktree.anchor` (`worktree.py:75`) makes each extension path absolute against the launch checkout (`root / p`) when that file exists on the host. Otherwise it leaves the path as written.
- `agent_pi.run` builds `pi … -e <ext>… [-e REFUSE_EXTENSION] <prompt>`, so the refuse extension comes after every user extension. `agent_pi._in_vm` adds `SSSF_PI_TOKEN` and installs `sssf_rules.ts` at its host path in the VM. It does not touch `OBS_*`.
- `sandbox_remote.Remote._env` passes every `SSSF_*` and `OBS_*` variable in the command's env into the VM. That includes `OBS_AUTH_TOKEN`.
- `Remote._clone` clones `sssf/<adw_id>` into the VM at `self.tree`, which is `Path.cwd()` when the box is made. That is the host worktree path.

## Threat model

The agent runs as the engineer's own user, on the host or in the VM. Inside the VM it can change any file in the clone, including a committed extension, before the next agent call loads it. In a pi call it can run any command its tools allow, apart from what the refuse rules and the stream check catch. Through `worktree_setup` and the checks, it can run code in the VM.

This guard cannot stop the following, at its level:

- An agent editing a committed extension in the VM. The edited file loads on the next pi call. A host run has the same exposure (decision 25).
- What an extension does once pi loads it. SSSF checks where the file came from, not what it says. The engineer chose to commit and name it.
- Imports. They are not followed (decision 22). A missing import fails when pi loads the extension in the VM, and the run stops with pi's error.

What this guard does stop: a VM run loading an extension that the engineer has not committed at the commit the VM clones, or one outside the repo. Those would otherwise come from somewhere other than the reviewed branch. It also stops the obs bearer token, `OBS_AUTH_TOKEN`, from entering the VM through a pi agent's env.

## Bar

Against the baseline, the change must:

1. In a VM run (`--sandbox vercel|e2b`, fresh or resumed), check every pi agent's `harness_engineering` path before the VM opens, which means before `sandbox.open_remote` and `box.open()`. Each path must resolve, lexically, to a location inside the main checkout and be a regular file (git mode `100644` or `100755`) committed at the tip of the run's branch (`sssf/<adw_id>`). For a fresh run that tip is the run's starting commit. For a resume it is the commit the new VM clones. A path that fails stops the run with a `SystemExit` whose message names the agent and the path as the roster wrote it. No VM is created.
2. After that check, re-anchor each such path to `<run worktree path>/<repo-relative path>`. pi then loads it from the VM's clone, so files it imports beside it are present. Nothing new is copied into the VM.
3. Drop the extension refusal from `sandbox.check_roster`. Keep its other checks unchanged: an unknown provider, and pi on a non-`openai/` model.
4. For every pi agent call in a VM, set `OBS_DISABLE=1` in the env and remove `OBS_AUTH_TOKEN` from it. Every other `OBS_*` variable (`OBS_NAME`, `OBS_POOL`, `OBS_TAG`, and the operator's own) crosses as today.
5. Keep `sssf_rules.ts` as the last `-e` on the pi command line, after every user extension in roster order, on the host and in a VM. A test pins that order.
6. Document what a VM run does with extensions in `references/config.md`, and state the known limit.

The change must never:

- Change anything for a host run. That covers plain runs and `--worktree` runs without `--sandbox`: same anchoring to the launch checkout, same env (an `OBS_AUTH_TOKEN` stays in a host pi agent's env), same argv.
- Change anything for a Claude Code roster, on the host or in a VM. `agent_cc` and `Remote._env` are not edited.
- Change anything for a pi roster without extensions, beyond item 4: two env variables in a VM that nothing loaded there reads. Its argv, its anchoring and its checks stay exactly as today.
- Copy any file into the VM other than the refuse extension, which is copied as today.
- Keep an agent's file, run agent code on the host, or delete or overwrite an engineer's file where the baseline does not. The new check only reads git objects (`git rev-parse`, `git ls-tree`) in the main checkout. It does not write.
- Follow imports.

## Out of scope

These are old holes or deliberate limits. A reviewer should file them as such, not as findings against this change:

- An agent can edit a committed extension in the VM, and the edited one loads on the next call (decision 25). On a resume, the check runs against the branch tip, which may hold such an agent commit from the earlier attempt. That is the same exposure. Nothing checks extension contents.
- Imports are not followed or checked (decision 22).
- A committed regular file can still import a path outside the clone. In the VM that path holds nothing of the engineer's. The load fails with pi's error.
- No obs events come from a VM. The relay is under Open (decision 23).
- `OBS_AUTH_TOKEN` still crosses into the VM for checks, `worktree_setup`, git, and Claude Code agents, because `Remote._env` passes all `OBS_*` from `operator_env()`. That is baseline behaviour, and the request keeps Claude rosters unchanged. Decision 23's "stays on the host" is met here for pi agents only. The engineer may want a follow-up for the rest.
- A trial (`just trial --sandbox …`) no longer refuses an extension roster up front in `trial.vm_problems`. Each attempt runs the new launch check and stops before its VM if an extension is not committed. The trial gets no separate pre-check.
- A failed launch check, like a failed VM creation (decision 10), happens after `worktree.enter` has made the worktree and branch. The engineer removes them with `just discard`, as for any failed setup.
- pi's auto-discovery of project-local extensions in the clone (if any) is not changed. It behaves in the VM as on the host.

## Changes

### 1. `.claude/skills/sssf/templates/adws/adw_modules/sandbox.py`

- In `check_roster`: delete the `extended` block (the last four lines). Update the docstring so it no longer implies extensions are refused, and point to `extensions_in_clone` for them.
- Add `extensions_in_clone(cfg, root: Path, commit: str, tree: Path) -> None`, with `import os` and `from pathlib import Path` (Path is already imported):

  ```python
  def extensions_in_clone(cfg, root: Path, commit: str, tree: Path) -> None:
      """For a VM run, before its VM opens: every pi agent's harness_engineering
      path must be a file committed at `commit` inside the repo at `root`, and is
      then anchored to `tree`, where the run's branch is cloned in the VM, so pi
      loads it, and what it imports, from the clone (design decisions 21, 22).
      Imports aren't followed. Anything else stops the run, naming the agent and
      the path. Call it once per process: `tree` is inside `root`."""
  ```

  Behaviour:
  - Loop over `cfg.agents` where `coding_agent == "pi"` and `harness_engineering` is non-empty. Skip everything else, and touch no git when no agent qualifies.
  - Resolve `commit` once, only when some agent qualifies: `git rev-parse --verify --quiet <commit>^{commit}` with `cwd=root`, using `subprocess.run(..., capture_output=True, text=True)` directly on the host. Do not use `current()`, because the check is about the host's repo before any VM exists. If it fails, raise `SystemExit` saying the run's branch could not be read.
  - For each path `p` as written in the config (after `worktree.anchor`, it may already be absolute under `root`):
    - `full = Path(os.path.normpath(p if Path(p).is_absolute() else root / p))`. This is lexical, so `..` is folded and symlinks are not followed.
    - `rel = full.relative_to(root)`. On `ValueError`, or when `rel == Path(".")`, raise `SystemExit(f"--sandbox loads {agent.name}'s harness_engineering extension {p} from the VM's clone of the repo, and it is outside the repo ({root}). Move it into the repo and commit it, or run with --sandbox none.")`.
    - List the parent tree exactly, with no pathspec matching: `git ls-tree -z <sha>:<rel.parent.as_posix()>`, or `git ls-tree -z <sha>:` when the parent is `.`, with `cwd=root`. Parse the NUL-separated entries `"<mode> <type> <oid>\t<name>"`. The path passes only when the exit status is 0 and an entry has `name == rel.name`, `type == "blob"` and `mode in ("100644", "100755")`. Otherwise raise `SystemExit(f"--sandbox loads {agent.name}'s harness_engineering extension {p} from the VM's clone of the run's branch, and it is not a file committed there (at {sha[:12]}). Commit it, or run with --sandbox none.")`.
    - Collect `str(tree / rel)`.
  - Give each agent a **new** list: `agent.harness_engineering = moved`. Agents can share one list object, inherited from `defaults`.

### 2. `.claude/skills/sssf/templates/adws/adw_modules/session.py`

In `_open_sandbox`, after the `asked` / `prepare` lines and **before** `with run.phase(...)`, so before `open_remote` and `box.open()`:

```python
sandbox.extensions_in_clone(run.cfg, worktree.main_checkout(), place["branch"], Path.cwd())
```

`Path.cwd()` is the run's worktree. `worktree.enter` has already `chdir`ed there, and `Remote.__init__` takes the same `Path.cwd()` as its `tree`, which is where `_clone` puts the branch. Add `from pathlib import Path` if it is missing. Add a one-line comment citing decisions 21 and 22. This covers fresh runs, resumes with the flag, and resumes without it. A host run never reaches `_open_sandbox`, so its behaviour is unchanged.

Before committing to that spot, grep `adws/adw_*.py` under `.claude/skills/sssf/templates/` and `adw_modules/` for any code that reloads the config (`load_config`) after `session.ensure` and builds agent requests from the fresh copy. If any does, the re-anchored paths would be lost. In that case, re-anchor in the same place the reload happens, and say so in your report. At `3491a4e`, agents are resolved from `run.cfg` (`agents.py:215`, `extensions=agent.harness_engineering`).

### 3. `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py`

In `_in_vm`, after the `tree is sandbox.LOCAL` early return:

```python
env["OBS_DISABLE"] = "1"            # no obs events from a VM yet (design decision 23)
env.pop("OBS_AUTH_TOKEN", None)     # the obs server's token stays on the host
```

Update the `_in_vm` docstring to say this. Leave the `cmd` build in `run` as it is, since it already puts `REFUSE_EXTENSION` last. Add a comment there saying the refuse extension must stay last, so it checks a command after every other extension has had its turn (decision 24), and that a test pins it.

### 4. `.claude/skills/sssf/templates/adws/adw_modules/worktree.py`

Docstring only. In `anchor`, add one sentence: a VM run re-anchors pi extensions to its clone afterwards (`sandbox.extensions_in_clone`). No code change.

### 5. Tests

#### `.claude/skills/sssf/tests/test_rules.py`

- `test_a_vm_refuses_pi_extensions_and_other_providers`: rename it to `test_a_vm_refuses_other_providers_but_not_pi_extensions`. Change its last case so a pi `openai/` agent with `harness_engineering=["x.ts"]` now **passes** `check_roster`. Keep the non-`openai/` refusal.
- `test_a_pi_agent_in_a_vm_gets_a_token_and_the_refuse_extension`: change the VM expectation to `{"SSSF_PI_TOKEN": "eyJ-access", "OBS_DISABLE": "1"}`. Add a case with input `{"OBS_AUTH_TOKEN": "secret", "OBS_NAME": "builder", "OBS_POOL": "p"}`. In a VM the result has no `OBS_AUTH_TOKEN`, keeps `OBS_NAME` and `OBS_POOL`, and has `OBS_DISABLE == "1"`. On `sandbox.LOCAL` the result equals the input unchanged, with the token kept and no `OBS_DISABLE`.
- New test `test_the_refuse_extension_loads_after_every_user_extension`. Use a stand-in pi script, as `test_pi_loads_the_refusing_extension_only_for_refuse_rules` does, that writes `"$@"` one argument per line. Run `agent_pi.run` with `extensions=["first.ts", "second.ts"]` and refuse rules. Assert that the `-e` values, in order, are `["first.ts", "second.ts", str(agent_pi.REFUSE_EXTENSION)]`. Then run it again with a fake VM tree: monkeypatch `sandbox.current` to return a `SimpleNamespace` with `install` (records), `start` (records `command` and `env`, then raises a sentinel exception), and `name="vercel"`, and monkeypatch `agent_pi.vm_token` to return `"eyJ-access"`. Assert the same `-e` order in the VM. Also assert that the recorded env has `OBS_DISABLE == "1"` and no `OBS_AUTH_TOKEN` when the process env had one (`monkeypatch.setenv("OBS_AUTH_TOKEN", "secret")`). This stands in for proof cases (4) and (5).

#### New file `.claude/skills/sssf/tests/test_sandbox_extensions.py`

Module docstring: what it covers (decisions 21, 22, 24, 27) and the same "Run from the skill root" line as `test_sandbox.py`. Use the same `sys.path` setup as `test_sandbox.py`. Build the config with `agents.load_config(str(SKILL / "templates" / "sssf.config.yaml"))`, the shipped template, never a test-only one. On top of it, set `harness_engineering` on one pi agent (find it with `next(a for a in cfg.agents if a.coding_agent == "pi")`) and record its `name`.

Fixture `repo(tmp_path, monkeypatch)`: `git init -b main` a repo in `tmp_path / "repo"`, set `user.name` and `user.email` locally, make an initial commit, and `monkeypatch.chdir` into it. Use `tree = root / "adws/adw_data/worktrees/abc"` as the clone path. It need not exist, since the function only builds paths.

Tests:

1. `test_a_committed_extension_loads_from_the_clone_with_what_it_imports_uncommitted`: commit `.pi/extensions/marker.ts` (it contains `import "./helper.ts"`), and leave `helper.ts` untracked. Call it with `commit="main"`. The agent's paths become `[str(tree / ".pi/extensions/marker.ts")]`. No error, because imports are not followed. This stands in for proof case (1).
2. `test_the_path_as_worktree_anchor_left_it_is_accepted`: the same committed file, written as `str(root / ".pi/extensions/marker.ts")`, gives the same result.
3. `test_an_uncommitted_extension_stops_the_run_naming_agent_and_path` (parametrize): the file is untracked; the file is staged but not committed; the file is missing. Each raises `SystemExit`. The message contains the agent's name and the path as written. This stands in for proof case (2).
4. `test_an_extension_committed_after_the_runs_branch_was_cut_stops_it`: create branch `sssf/abc` at the initial commit, then commit the extension on `main`. Call it with `commit="sssf/abc"`. It raises `SystemExit`.
5. `test_an_extension_outside_the_repo_stops_the_run` (parametrize): an absolute path in `tmp_path / "outside.ts"` (the file exists); `../outside.ts`; and `.pi/../../outside.ts`. Each raises `SystemExit` naming the agent and the path, with "outside the repo" in the message. This stands in for proof case (3).
6. `test_a_directory_or_a_committed_symlink_is_not_an_extension`: commit a directory `.pi/extensions/` and pass it, then commit a symlink `link.ts -> /etc/hostname` and pass that. Both raise `SystemExit`.
7. `test_a_roster_without_extensions_is_untouched`: take the shipped config as loaded, with no extensions. Monkeypatch `subprocess.run` inside `sandbox` to fail the test if it is called. After the call, every agent's `harness_engineering` is unchanged. Also load `sssf.config.claude_code.yaml` and assert the same. This stands in for proof case (6) and the Claude roster part of the bar.
8. `test_agents_sharing_one_list_each_get_their_own`: two pi agents share the same list object. After the call each holds the anchored path, and the original list object is not mutated.
9. `test_a_vm_run_checks_extensions_before_the_vm_opens`, the session wiring. Make a real worktree for `sssf/abc` with `git worktree add -b sssf/abc <root>/adws/adw_data/worktrees/abc` and `chdir` into it. Monkeypatch `sandbox.open_remote` to record that it was called and then raise a sentinel. Monkeypatch `sandbox.prepare` to do nothing. Call `session._open_sandbox(SimpleNamespace(cfg=cfg, adw_id="abc"), {"branch": "sssf/abc"}, "vercel", "vercel")`.
   - With an uncommitted extension, it raises `SystemExit` and `open_remote` was never called.
   - With a committed one (committed before the branch was cut), it reaches `open_remote`, which raises the sentinel, and the agent's path at that point is `str(Path.cwd() / ".pi/extensions/marker.ts")`.

   If `_open_sandbox` needs more of `run` before `open_remote` (for example `run.phase`), give the `SimpleNamespace` a `phase` that is a no-op context manager. Do not change `_open_sandbox` to suit the test.

#### `.claude/skills/sssf/tests/test_sandbox_remote.py`

Add `test_a_pi_agents_obs_token_stays_on_the_host_and_obs_is_disabled(box, monkeypatch)`. Monkeypatch `agent_pi.vm_token` to return `"eyJ-access"`. Build `env = agent_pi._in_vm({"OBS_AUTH_TOKEN": "secret", "OBS_NAME": "builder", "SSSF_ADW_ID": "x"}, box, [])`, then `passed = box._env(env)`. Assert that `OBS_AUTH_TOKEN` is not in `passed`, `passed["OBS_DISABLE"] == "1"`, `passed["OBS_NAME"] == "builder"`, and `passed["OPENAI_API_KEY"] == "eyJ-access"`. This pins, at the VM boundary, that the token does not cross for a pi agent.

## Docs

- `.claude/skills/sssf/references/config.md`:
  - In the `harness_engineering` field row (around line 49), add: "Under `--sandbox vercel|e2b`, see [pi extensions in a VM](#pi-extensions-in-a-vm)."
  - Add a short `### pi extensions in a VM` subsection after the `harness_engineering` section (around line 412–414). It says:
    - Each path must be a file inside the repo, committed on the branch the run starts from. SSSF checks this before the VM opens, and stops the run with the agent and the path if it isn't.
    - In the VM, pi loads the file from the run's clone at the worktree path, so files it imports beside it come along. Imports aren't checked; a missing one fails when pi loads the extension, and the run stops with pi's error.
    - `sssf_rules.ts` still loads last, after every listed extension.
    - In the VM, pi agents get `OBS_DISABLE=1` and no `OBS_AUTH_TOKEN`, so an obs extension loads and posts nothing. SSSF's trace still records the run. Other `OBS_*` variables cross.
    - **Known limit:** an agent can edit a committed extension in the VM, and the edited one loads for the next agent call. A host run has the same exposure, since an extension isn't a protected file. Nothing checks it.
- `docs/designs/remote-sandboxes.md`:
  - In the "A ChatGPT roster in the VM" bullet under `## Open` (line 163), remove "and rosters with `harness_engineering` extensions until decisions 20 to 27 are built" from the "Still refused" list.
  - Add a short `## As built: pi extensions` section before `## Open`, recording the choices this plan settled that the decisions left open:
    - The check runs against the branch tip the VM clones. That is the starting commit for a fresh run, and the branch tip on a resume.
    - It requires a regular file, not a symlink or directory.
    - It runs in `session._open_sandbox` after the worktree exists.
    - A trial no longer pre-checks extensions; each attempt checks at launch.
    - `OBS_AUTH_TOKEN` is held back for pi agents only.
    - Decision 27's VM proof is still to run.
- Docstrings in `sandbox.check_roster`, `sandbox.extensions_in_clone`, `agent_pi._in_vm` and `worktree.anchor`, as described above.
- No CHANGELOG exists in this repo. No `app_docs/` write-up describes the old refusal, so the documenter has nothing to mark as outdated there.

## Verification

From the repo root:

1. `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_sandbox_extensions.py tests/test_rules.py tests/test_sandbox_remote.py tests/test_agent_pi.py tests/test_trial.py -q` exits 0.
2. The full skill suite still passes, which proves host runs, Claude rosters and extensionless pi rosters unchanged: `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q` exits 0.
3. `git grep -n "can't take pi extensions" -- .claude/skills/sssf/templates` finds nothing (exit status 1).
4. `git diff 3491a4e --stat -- .claude/skills/sssf/templates/adws/adw_modules` shows only `sandbox.py`, `session.py`, `agent_pi.py` and `worktree.py` changed. `git diff 3491a4e -- .claude/skills/sssf/templates/adws/adw_modules/sandbox_remote.py .claude/skills/sssf/templates/adws/adw_modules/agent_cc.py` is empty.
5. The proof on real VMs (decision 27, cases 1 to 6 on both providers, in sssf-sandbox) is run by hand after the merge. It is not part of this build.

## For the engineer

- `adws/adw_modules/` is protected and is the hub's installed copy of the skill's modules. After this merges, stamp the changed `sandbox.py`, `session.py`, `agent_pi.py` and `worktree.py` into the hub, as for earlier guard changes (for example `965a7f2`).
- Run decision 27's proof on Vercel and E2B in sssf-sandbox, then ask pi-agent-observability to try one VM run.
- Decide whether `OBS_AUTH_TOKEN` should also stay out of the VM for checks, setup, git and Claude Code agents (see Out of scope). That would be a change to `sandbox_remote.Remote._env`, which this plan leaves alone to keep Claude rosters unchanged.
