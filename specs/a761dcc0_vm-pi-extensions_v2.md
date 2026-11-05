# Plan: let a pi roster with extensions run in a VM

This plan builds decisions 20 to 27 of `docs/designs/remote-sandboxes.md` ("pi extensions in the VM"). Decision 27's proof on real VMs happens by hand after the merge. The tests below stand in for it without a VM.

All code lives under `.claude/skills/sssf/templates/adws/adw_modules/`, and all tests under `.claude/skills/sssf/tests/`. The hub's installed copy, `adws/adw_modules/`, is protected: see "For the engineer".

Revision 2 makes two changes, following `spec_review_1.md`:

- Extensions are now checked at the run's **starting commit**, `place["base"]`, not at the branch tip.
- The tests now start from the shipped roster with its own extensions cleared. The shipped `planner` and `scout` load `adws/adw_data/harness_engineering/subagents.ts`. One test covers that shipped case directly.

## Baseline

`main` at `3491a4e` ("List the obs relay under Open in remote-sandboxes.md"). Every finding is judged against that commit. At that commit:

- **`sandbox.check_roster`** (`sandbox.py:156`) raises `SystemExit("--sandbox can't take pi extensions yet …")` for any pi agent with `harness_engineering`. Three callers reach it:
  - `session.ensure`, through `sandbox.prepare`;
  - a resume without the flag, through `session._open_sandbox`;
  - `trial.vm_problems`.

  So the shipped pi roster, whose `planner` and `scout` load `subagents.ts`, can't run under `--sandbox` at all.
- **`worktree.anchor`** (`worktree.py:75`) makes each extension path absolute against the launch checkout (`root / p`) when the file exists on the host. Otherwise it leaves the path as written.
- **`worktree.enter`** records `place["base"]` (`git rev-parse --short HEAD` right after `git worktree add`) in the run's `worktree` trace event. A resume reads the same record back through `worktree.recorded`, so `place["base"]` is the run's starting commit on a fresh run and on a resume alike.
- **`agent_pi.run`** builds `pi … -e <ext>… [-e REFUSE_EXTENSION] <prompt>`, so the refuse extension follows every user extension.
- **`agent_pi._in_vm`** adds `SSSF_PI_TOKEN` and installs `sssf_rules.ts` at its host path in the VM. It does not touch `OBS_*`.
- **`sandbox_remote.Remote._env`** passes every `SSSF_*` and `OBS_*` variable in the command's env into the VM, including `OBS_AUTH_TOKEN`.
- **`Remote._clone`** clones `sssf/<adw_id>` into the VM at `self.tree`, which is `Path.cwd()` when the box is made: the host worktree path.

## Threat model

The agent runs as the engineer's own user, on the host or in the VM. In the VM it can:

- change any file in the clone, including a committed extension, before the next agent call loads it;
- commit to the run's branch, and a resume clones that branch;
- run any command its tools allow, apart from what the refuse rules and the stream check catch;
- run code through `worktree_setup` and the checks.

At its level, this guard can't stop:

- An agent editing, deleting or committing over an extension in the VM. The branch-tip version is what loads on the next pi call, and on a resume (decision 25). A host run has the same exposure.
- What an extension does once pi loads it. SSSF checks where the file came from, not what it says.
- Imports. They are not followed (decision 22). A missing import fails when pi loads the extension in the VM, and the run stops with pi's error.

What it does stop:

- A VM run loading an extension the engineer didn't commit before the run started, including one an agent first committed on the run's branch.
- A VM run loading an extension from outside the repo.
- The obs bearer token, `OBS_AUTH_TOKEN`, entering the VM through a pi agent's env.

## Bar

Against the baseline, the change must:

1. **Check extensions before the VM opens.** This applies to every VM run (`--sandbox vercel|e2b`), fresh or resumed. The check runs before `sandbox.open_remote` and `box.open()`, and covers every pi agent's `harness_engineering` path. Each path must:
   - resolve lexically to a place inside the main checkout;
   - be a regular file (git mode `100644` or `100755`);
   - be committed at the run's **starting commit**, `place["base"]`.

   For a resume, `place["base"]` comes from the trace's `worktree` record. A file first committed on `sssf/<adw_id>` after the start doesn't pass. A path that fails stops the run with a `SystemExit` naming the agent and the path as the roster wrote it, and no VM is created. A place with no `base` stops the run too: fail closed.
2. **Anchor each checked path to the clone.** After the check, each such path becomes `<run worktree path>/<repo-relative path>`, so pi loads it from the VM's clone, with the files it imports beside it. Nothing new is copied into the VM.
3. **Drop the extension refusal from `sandbox.check_roster`.** Its other checks stay: an unknown provider, and pi on a non-`openai/` model.
4. **Disable obs in the VM.** For every pi agent call in a VM, the env gets `OBS_DISABLE=1` and loses `OBS_AUTH_TOKEN`. Every other `OBS_*` variable (`OBS_NAME`, `OBS_POOL`, `OBS_TAG`, the operator's own) crosses as today.
5. **Load the refuse extension last.** `sssf_rules.ts` stays the last `-e` on the pi command line, after every user extension in roster order, on the host and in a VM. A test pins the order.
6. **Document the behaviour.** `references/config.md` says what a VM run does with extensions, and states the known limit.

The change must never:

- Change a host run (plain or `--worktree` without `--sandbox`): same anchoring to the launch checkout, same env (`OBS_AUTH_TOKEN` stays in a host pi agent's env), same argv.
- Change a Claude Code roster, on the host or in a VM. `agent_cc` and `Remote._env` are not edited.
- Change a pi roster without extensions beyond item 4, which adds two env variables in a VM that nothing loaded there reads. Its argv, anchoring and checks stay exactly as today.
- Copy any file into the VM beyond the refuse extension, which is copied as today.
- Keep an agent's file, run agent code on the host, or delete or overwrite an engineer's file where the baseline does not. The new check only reads git objects (`git rev-parse`, `git ls-tree`) in the main checkout, and writes nothing.
- Follow imports.

## Out of scope

These are old holes or deliberate limits. A reviewer should file them as such.

- **Agent-edited extensions load (decision 25).** The check is against the starting commit, but the VM loads the branch tip's file. An agent can edit or replace a committed extension in the VM, and the edited one loads on the next call and on a resume. If an agent deletes it from the branch, pi fails to load it and the run stops with pi's error. Nothing checks extension contents.
- **Imports aren't followed or checked (decision 22).** A committed file can import a path outside the clone. In the VM that path holds nothing of the engineer's, so the load fails with pi's error.
- **No obs events come from a VM.** The relay is under Open (decision 23).
- **`OBS_AUTH_TOKEN` still crosses for everything except pi agents.** Checks, `worktree_setup`, git and Claude Code agents still get it in the VM, because `Remote._env` passes all `OBS_*` from `operator_env()`. That is baseline behaviour, and the request keeps Claude rosters unchanged. Decision 23's "stays on the host" is met for pi agents only. Possible follow-up for the engineer.
- **A trial no longer refuses an extension roster up front.** `just trial --sandbox …` drops that refusal from `trial.vm_problems`. Each attempt runs the new launch check and stops before its VM if an extension isn't committed. There is no separate trial pre-check.
- **A failed launch check leaves its worktree.** Like a failed VM creation (decision 10), it happens after `worktree.enter` has made the worktree and branch. The engineer removes them with `just discard`, as for any failed setup.
- **pi's own auto-discovery is unchanged.** Whatever pi discovers of project-local extensions in the clone, it discovers in the VM as on the host.

## Changes

### 1. `.claude/skills/sssf/templates/adws/adw_modules/sandbox.py`

In `check_roster`, delete the `extended` block (its last four lines). Update the docstring so it no longer implies extensions are refused, and point to `extensions_in_clone` for them.

Add `extensions_in_clone(cfg, root: Path, commit: str, tree: Path) -> None`, with `import os` at the top:

```python
def extensions_in_clone(cfg, root: Path, commit: str, tree: Path) -> None:
    """For a VM run, before its VM opens: every pi agent's harness_engineering
    path must be a file inside the repo at `root`, committed at `commit`, the
    run's starting commit. Each is then anchored to `tree`, where the run's
    branch is cloned in the VM, so pi loads it, and what it imports, from the
    clone (design decisions 21, 22). Imports aren't followed. Anything else
    stops the run, naming the agent and the path. Call it once per process:
    `tree` is inside `root`."""
```

Behaviour:

1. **Pick the agents.** Loop over `cfg.agents` where `coding_agent == "pi"` and `harness_engineering` is non-empty. Skip all others. When none qualifies, return without running git.
2. **Resolve the commit.** Do this once, only when some agent qualifies:
   - If `commit` is empty, raise `SystemExit` saying the run's starting commit isn't known.
   - Otherwise run `git rev-parse --verify --quiet <commit>^{commit}` with `cwd=root`, via `subprocess.run(..., capture_output=True, text=True)` on the host. Don't use `current()`: the check reads the host's repo before any VM exists.
   - If it exits non-zero, raise `SystemExit` saying the starting commit `<commit>` can't be read.
3. **Check each path.** For each path `p` as written in the config (after `worktree.anchor`, it may already be absolute under `root`):
   - Compute `full = Path(os.path.normpath(p if Path(p).is_absolute() else root / p))`. This is lexical: `..` folds, symlinks aren't followed.
   - Compute `rel = full.relative_to(root)`. On `ValueError`, or when `rel == Path(".")`, raise:

     ```python
     SystemExit(f"--sandbox loads {agent.name}'s harness_engineering extension {p} from the VM's clone of the repo, and it is outside the repo ({root}). Move it into the repo and commit it, or run with --sandbox none.")
     ```
   - List the parent tree exactly, with no pathspec matching. Run `git ls-tree -z <sha>:<rel.parent.as_posix()>` with `cwd=root`, or `git ls-tree -z <sha>:` when the parent is `.`.
   - Parse the NUL-separated entries, each `"<mode> <type> <oid>\t<name>"`. The path passes only if the exit status is 0 and some entry has `name == rel.name`, `type == "blob"` and `mode in ("100644", "100755")`. Otherwise raise:

     ```python
     SystemExit(f"--sandbox loads {agent.name}'s harness_engineering extension {p} from the VM's clone, and it is not a file committed at the run's starting commit ({sha[:12]}). Commit it before the run starts, or run with --sandbox none.")
     ```
   - Collect `str(tree / rel)`.
4. **Give each agent a new list:** `agent.harness_engineering = moved`. The shipped roster's agents can share one list object, inherited from `defaults`, so mutating it in place would change every agent at once.

### 2. `.claude/skills/sssf/templates/adws/adw_modules/session.py`

In `_open_sandbox`, add the call after the `asked` / `prepare` lines and **before** `with run.phase(...)`, so it runs before `open_remote` and `box.open()`:

```python
# Extensions come from the VM's clone, committed at the run's start (design decisions 21, 22).
sandbox.extensions_in_clone(run.cfg, worktree.main_checkout(), place.get("base", ""), Path.cwd())
```

Why these arguments:

- `Path.cwd()` is the run's worktree. `worktree.enter` has already `chdir`ed there, and `Remote.__init__` takes the same `Path.cwd()` as its `tree`, which is where `_clone` puts the branch.
- `place["base"]` is the run's starting commit, recorded by `enter` on a fresh run and read back from the trace on a resume.

Add `from pathlib import Path` if it's missing. The call covers a fresh run, a resume with the flag, and a resume without it. A host run never reaches `_open_sandbox`.

Before you settle on this spot, grep `.claude/skills/sssf/templates/adws/adw_*.py` and `adw_modules/` for anything that reloads the config (`load_config`) after `session.ensure` and builds agent requests from the fresh copy. If something does, re-anchor there too, and say so in your report. At `3491a4e`, agents resolve from `run.cfg` (`agents.py:215`, `extensions=agent.harness_engineering`).

### 3. `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py`

In `_in_vm`, after the `tree is sandbox.LOCAL` early return, add:

```python
env["OBS_DISABLE"] = "1"            # no obs events from a VM yet (design decision 23)
env.pop("OBS_AUTH_TOKEN", None)     # the obs server's token stays on the host
```

Update the `_in_vm` docstring to match. Leave the `cmd` build in `run` as it is. Add a comment there: the refuse extension stays last, so it checks a command after every other extension has had its turn (decision 24), and a test pins this.

### 4. `.claude/skills/sssf/templates/adws/adw_modules/worktree.py`

Docstring only. In `anchor`, add one sentence: a VM run re-anchors pi extensions to its clone afterwards (`sandbox.extensions_in_clone`).

### 5. Tests

#### `.claude/skills/sssf/tests/test_rules.py`

- **`test_a_vm_refuses_pi_extensions_and_other_providers`.** Rename it to `test_a_vm_refuses_other_providers_but_not_pi_extensions`. Its last case, a pi `openai/` agent with `harness_engineering=["x.ts"]`, now **passes** `check_roster`. Keep the non-`openai/` refusal.
- **`test_a_pi_agent_in_a_vm_gets_a_token_and_the_refuse_extension`.**
  - The VM expectation becomes `{"SSSF_PI_TOKEN": "eyJ-access", "OBS_DISABLE": "1"}`.
  - Add a case with input `{"OBS_AUTH_TOKEN": "secret", "OBS_NAME": "builder", "OBS_POOL": "p"}`. In a VM the result has no `OBS_AUTH_TOKEN`, keeps `OBS_NAME` and `OBS_POOL`, and sets `OBS_DISABLE == "1"`. On `sandbox.LOCAL` it equals the input, unchanged.
- **New `test_the_refuse_extension_loads_after_every_user_extension`.**
  - Use a stand-in pi script, as `test_pi_loads_the_refusing_extension_only_for_refuse_rules` does, writing `"$@"` one argument per line.
  - Run `agent_pi.run` with `extensions=["first.ts", "second.ts"]` and refuse rules. The `-e` values, in order, must be `["first.ts", "second.ts", str(agent_pi.REFUSE_EXTENSION)]`.
  - Then run it with a fake VM tree. Monkeypatch `sandbox.current` to return a `SimpleNamespace` with:
    - `install`, which records;
    - `start`, which records `command` and `env`, then raises a sentinel;
    - `name="vercel"`.

    Monkeypatch `agent_pi.vm_token` to return `"eyJ-access"`, and `monkeypatch.setenv("OBS_AUTH_TOKEN", "secret")`.
  - Assert the same `-e` order in the VM, `OBS_DISABLE == "1"`, and no `OBS_AUTH_TOKEN` in the recorded env.
  - This stands in for proof cases (4) and (5).

#### New file: `.claude/skills/sssf/tests/test_sandbox_extensions.py`

**Setup.** Give the module a docstring naming what it covers (decisions 21, 22, 24, 27), with the same "Run from the skill root" line and `sys.path` setup as `test_sandbox.py`.

Every config comes from the shipped template, `agents.load_config(str(SKILL / "templates" / "sssf.config.yaml"))`, never a test-only one. The shipped `planner` and `scout` load `adws/adw_data/harness_engineering/subagents.ts`, so use two helpers:

- `shipped()`: the shipped config as loaded.
- `roster(*paths)`: `shipped()`, then `agent.harness_engineering = []` on **every** agent, then `list(paths)` set on one pi agent, `builder` (assert it is pi). It returns `(cfg, agent)`.

**Fixtures.**

- `repo(tmp_path, monkeypatch)`:
  - `git init -b main` in `tmp_path / "repo"`, with `user.name` and `user.email` set locally;
  - one initial commit;
  - `monkeypatch.chdir` into it.

  It returns `root`, resolved, plus a `commit(*files)` helper that writes, adds and commits files and returns the new full sha.
- `tree = root / "adws/adw_data/worktrees/abc"`: the clone path. It need not exist.

**Tests.**

1. `test_a_committed_extension_loads_from_the_clone_with_what_it_imports_uncommitted`.
   - Commit `.pi/extensions/marker.ts`, which contains `import "./helper.ts"`, and leave `helper.ts` untracked. `start` is that commit's sha.
   - `roster(".pi/extensions/marker.ts")`. The call with `commit=start` leaves `builder` with `[str(tree / ".pi/extensions/marker.ts")]` and raises nothing, since imports aren't followed.
   - Stands in for proof case (1).
2. `test_the_shipped_rosters_subagents_extension_is_checked_and_anchored`.
   - Commit `adws/adw_data/harness_engineering/subagents.ts` and `themeMap.ts` beside it. Contents can be placeholders.
   - Call it with the **shipped** config as loaded. Every agent that had that path, `planner` and `scout` at least, now holds `str(tree / "adws/adw_data/harness_engineering/subagents.ts")`.
   - Every agent without extensions keeps `[]`.
3. `test_the_shipped_roster_with_its_extension_uncommitted_stops_naming_the_agent`. Same, with nothing committed. `SystemExit` names `planner`, the first agent in the roster that loads it, and `adws/adw_data/harness_engineering/subagents.ts`.
4. `test_the_path_as_worktree_anchor_left_it_is_accepted`. The test 1 file, written as `str(root / ".pi/extensions/marker.ts")`, gives the same result.
5. `test_an_uncommitted_extension_stops_the_run_naming_agent_and_path`, parametrized over three cases: untracked, staged but not committed, missing. Each raises `SystemExit` whose message contains `builder` and the path as written. Stands in for proof case (2).
6. `test_an_extension_committed_after_the_run_started_stops_it`.
   - `start` is the initial commit. Create branch `sssf/abc` there, then commit the extension **on `sssf/abc`**, as an agent's commit would land. Call it with `commit=start`: `SystemExit`.
   - Committing it on `main` after `start` gives the same result.
   - This pins the starting commit, not the branch tip.
7. `test_an_extension_outside_the_repo_stops_the_run`, parametrized over three paths:
   - an absolute path, `tmp_path / "outside.ts"` (the file exists);
   - `../outside.ts`;
   - `.pi/../../outside.ts`.

   Each raises `SystemExit` naming `builder` and the path, with "outside the repo". Stands in for proof case (3).
8. `test_a_directory_or_a_committed_symlink_is_not_an_extension`. A committed directory `.pi/extensions` and a committed symlink `link.ts -> /etc/hostname` both raise `SystemExit`.
9. `test_a_roster_without_extensions_is_untouched`.
   - `roster()` with no paths, which clears every agent.
   - Monkeypatch `sandbox.subprocess.run` to fail the test if called. The call must not run git, and every agent still has `[]`.
   - Do the same with `sssf.config.claude_code.yaml` as loaded. Its agents are `claude_code`, so nothing changes and git doesn't run.
   - Stands in for proof case (6) and the Claude-roster part of the bar.
10. `test_agents_sharing_one_list_each_get_their_own`. Two pi agents share one list object holding a committed path. Afterwards each holds the anchored path, and the original list object is unchanged.
11. `test_a_vm_run_checks_extensions_before_the_vm_opens`. This tests the session wiring.
    - Let `start` be the sha of a commit holding `.pi/extensions/marker.ts`. Run `git worktree add -b sssf/abc <root>/adws/adw_data/worktrees/abc <start>`, then `chdir` into the worktree.
    - Monkeypatch `sandbox.open_remote` to record that it was called, then raise a sentinel. Monkeypatch `sandbox.prepare` to a no-op.
    - Call `session._open_sandbox(SimpleNamespace(cfg=cfg, adw_id="abc"), {"branch": "sssf/abc", "base": start[:7]}, "vercel", "vercel")`, with `cfg` from `roster(...)`. Expected outcomes:
      - With `roster(".pi/extensions/marker.ts")`: it reaches `open_remote`, which raises the sentinel. At that point `builder`'s path is `str(Path.cwd() / ".pi/extensions/marker.ts")`.
      - With `roster(".pi/extensions/other.ts")`, where `other.ts` is committed only on `sssf/abc` after `start`: `SystemExit`, and `open_remote` is never called.
      - With a place missing `"base"`: `SystemExit`, and `open_remote` is never called.
    - If `_open_sandbox` needs more of `run` before `open_remote`, add it to the `SimpleNamespace` (for example `phase` as a no-op context manager). Don't change `_open_sandbox` to suit the test.

#### `.claude/skills/sssf/tests/test_sandbox_remote.py`

Add `test_a_pi_agents_obs_token_stays_on_the_host_and_obs_is_disabled(box, monkeypatch)`:

- Monkeypatch `agent_pi.vm_token` to return `"eyJ-access"`.
- Build `env = agent_pi._in_vm({"OBS_AUTH_TOKEN": "secret", "OBS_NAME": "builder", "SSSF_ADW_ID": "x"}, box, [])`, then `passed = box._env(env)`.
- Assert:
  - `OBS_AUTH_TOKEN` is not in `passed`;
  - `passed["OBS_DISABLE"] == "1"`;
  - `passed["OBS_NAME"] == "builder"`;
  - `passed["OPENAI_API_KEY"] == "eyJ-access"`.

## Docs

- **`.claude/skills/sssf/references/config.md`**
  - In the `harness_engineering` field row (around line 49), add: "Under `--sandbox vercel|e2b`, see [pi extensions in a VM](#pi-extensions-in-a-vm)."
  - Around line 412, fix "The starter roster ships with none", which is already false. The starter roster's `planner` and `scout` load `adws/adw_data/harness_engineering/subagents.ts`.
  - Add a short `### pi extensions in a VM` subsection after that section, saying:
    - Each path must be a file inside the repo, committed at the commit the run starts from. A file committed on the run's branch afterwards doesn't count. SSSF checks this before the VM opens, and if a path fails, it stops the run, naming the agent and the path.
    - In the VM, pi loads the file from the run's clone, at the worktree path, so files it imports beside it come along. Imports aren't checked. A missing one fails when pi loads the extension, and the run stops with pi's error.
    - `sssf_rules.ts` still loads last, after every listed extension.
    - In the VM, pi agents get `OBS_DISABLE=1` and no `OBS_AUTH_TOKEN`, so an obs extension loads but posts nothing. SSSF's trace still records the run. Other `OBS_*` variables cross.
    - **Known limit:** an agent can edit a committed extension in the VM, and the edited one loads for the next agent call. A host run has the same exposure, since an extension isn't a protected file. Nothing checks it.
- **`docs/designs/remote-sandboxes.md`**
  - Under `## Open`, in the bullet "A ChatGPT roster in the VM" (line 163), remove "and rosters with `harness_engineering` extensions until decisions 20 to 27 are built" from the "Still refused" list.
  - Add a short `## As built: pi extensions` section before `## Open`. It records what this build settled that the decisions left open:
    - The check is against the run's starting commit, the `base` the worktree record keeps, on a fresh run and on a resume alike. A file an agent committed on the run's branch doesn't pass.
    - The check requires a regular file: no symlink, no directory.
    - The check runs in `session._open_sandbox`, after the worktree exists.
    - A trial no longer pre-checks extensions. Each attempt checks at launch.
    - `OBS_AUTH_TOKEN` is held back for pi agents only.
    - Decision 27's VM proof is still to run.
- **Docstrings** in `sandbox.check_roster`, `sandbox.extensions_in_clone`, `agent_pi._in_vm` and `worktree.anchor`, as described above.
- **No other docs.** This repo has no CHANGELOG. No `app_docs/` write-up describes the old refusal, so the documenter has nothing to mark outdated there.

## Verification

Run these from the repo root:

1. `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_sandbox_extensions.py tests/test_rules.py tests/test_sandbox_remote.py tests/test_agent_pi.py tests/test_trial.py -q` exits 0.
2. The full skill suite still passes, which shows host runs, Claude rosters and extensionless pi rosters are unchanged: `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q` exits 0.
3. `git grep -n "can't take pi extensions" -- .claude/skills/sssf/templates` finds nothing (exit status 1).
4. `git diff 3491a4e --stat -- .claude/skills/sssf/templates/adws/adw_modules` lists only `sandbox.py`, `session.py`, `agent_pi.py` and `worktree.py`. `git diff 3491a4e -- .claude/skills/sssf/templates/adws/adw_modules/sandbox_remote.py .claude/skills/sssf/templates/adws/adw_modules/agent_cc.py` is empty.
5. The proof on real VMs, decision 27 cases 1 to 6 on both providers in sssf-sandbox, is run by hand after the merge. It is not part of this build.

## For the engineer

- `adws/adw_modules/` is protected: it is the hub's installed copy of the skill's modules. After the merge, stamp the changed `sandbox.py`, `session.py`, `agent_pi.py` and `worktree.py` into the hub, as for earlier guard changes (for example `965a7f2`).
- Run decision 27's proof on Vercel and E2B in sssf-sandbox. Then ask pi-agent-observability to try one VM run. Its extensions must be committed before the run starts.
- Decide whether `OBS_AUTH_TOKEN` should also stay out of the VM for checks, setup, git and Claude Code agents (see "Out of scope"). That would be a change to `sandbox_remote.Remote._env`, which this plan leaves alone so Claude rosters stay unchanged.
