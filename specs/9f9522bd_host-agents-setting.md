# Plan: `host_agents`, a repo setting that keeps every agent in a VM

## Summary

Add `host_agents` (boolean, default `true`) to `repo.yaml`. With `host_agents: false`, SSSF refuses any launch that would run an agent on this machine, before a run row, worktree or agent exists. The refusal is one function in `session.py`, called from `session.ensure`, which every chain and the trial panel already go through. A second, small check in `agents.execute` stops an agent call that reaches the host anyway.

Paths below are from the repo root. `T` stands for `.claude/skills/sssf/templates/adws`.

## Threat model

The agent runs as the engineer's own user. On the host it can write a script or a `just` recipe and run it under a name no text rule matches; a justfile with `set dotenv-load` then hands that recipe the real keys from `.env`. No rule that reads the command line can close this. A VM does: its clone has no `.env`, its commands get only `SSSF_` and `OBS_` variables, and its network is closed.

What this guard does: it decides, before any agent starts, whether this process's agents will work in a VM, and refuses if not.

What it can't stop at its level:

- An agent that is already on the host (a run started before the setting was turned on and still alive, or any run under `host_agents: true`) can edit the main checkout's `repo.yaml` or the trace db. The setting protects launches; it does not contain a running host agent.
- The engineer, or anything running as them outside SSSF, can set `host_agents: true`. SSSF reads the main checkout's working-tree file, as it does for every other setting; it does not require the change to be committed.
- Code an agent wrote still runs on the host when the engineer runs it there: `just quality` on a run's branch or worktree, the repo's own test recipes after a merge, `just export` checks. No agent is involved, so this check never sees it.
- A VM agent's edit to `repo.yaml` comes back only as a commit on the run's branch. It reaches the main checkout when the engineer merges it. The protected-paths check, not this change, is what refuses that edit.

## Baseline

`main` at `9b8106a`. Every finding is judged against that commit. On it:

- `session.ensure(cfg, adw_id)` with no sandbox runs agents on the host. Thirteen scripts call it (see the table below), and `trial.review` calls it for the panel.
- `choose_sandbox` returns `None` for `--sandbox none` and for any `adw_id` that has a session row.
- `worktree.enter` resumes a run with a recorded worktree where the record says, whatever flag was passed. So `--sandbox openshell --adw-id <host run>` resumes on the host: the flag is checked by `sandbox.prepare` and then ignored, because `place["sandbox"]` is only set for a new worktree.
- `repo_settings.load` refuses `local_guard: agentsh` with a VM `sandbox`.

## Bar

Against the baseline, the change must:

1. **Leave the default alone.** With no `host_agents` key, or `host_agents: true`, every chain, trial and resume behaves as at `9b8106a`. Every existing test passes unchanged.
2. **Refuse each host route** under `host_agents: false` with `sandbox: openshell`, before any run row, process row, event, worktree, branch or agent call:
   - a new run of each of `adw_plan`, `adw_build`, `adw_plan_build`, `adw_build_review`, `adw_document`, `adw_prompt`, `adw_scout`;
   - `--sandbox none` on `adw_simple_sdlc`, `adw_build_test`, `adw_plan_build_test`, `adw_plan_build_test_quality`, `adw_tickets`;
   - `adw_trial.py start`, with any flag, and `adw_trial.py review` (see "Two routes the request did not list");
   - resuming a host run, by any chain, with no flag, `--sandbox none`, or a VM provider's name.
3. **Leave the VM path alone.** Under the setting, a flag chain with no flag or with `--sandbox openshell` opens its VM as at the baseline. Resuming a VM run works as at the baseline, including joining it from a no-flag chain with `--adw-id`.
4. **Leave non-agent commands alone**: `merge`, `discard`, `rebase`, `kill`, `status`, `phases`, `quality`, `trial show|merge|discard`, install, update, `openshell-setup`.
5. **Refuse the bad pair at load**: `host_agents: false` with `sandbox: none`, with a message naming both settings.
6. **Not be turned off from a run's worktree.** A worktree whose `repo.yaml` says `host_agents: true`, committed on the run's branch or uncommitted, changes nothing while the main checkout's says `false`.
7. **Say what to do.** Every refusal names `host_agents: false` and `adws/adw_sssf_config/repo.yaml`.

It must never: keep an agent file, run agent code, or delete or overwrite an engineer file where the baseline does not. This change adds refusals only. It creates no file and removes none.

## Design

### Where the setting is read

`repo_settings.load(budget._root())`: the main checkout's working-tree `repo.yaml` (or the cwd's outside a git repo), the same root `agentsh_guard.read` and `choose_sandbox` use. It is read at the top of `session.ensure`, before `sandbox.prepare`, before `worktree.enter` moves the process, and before any agent exists. `worktree.main_checkout()` resolves through `--git-common-dir`, so a launch from inside a run's worktree still reads the main checkout's file. A worktree's own copy is never read.

### The one check: `session.host_agents`

New function in `T/adw_modules/session.py`:

```python
def host_agents(cfg, adw_id: str | None, sandbox_name: str | None, agentless: bool = False) -> bool:
```

It returns repo.yaml's `host_agents`. When that is `false` and `agentless` is false, it raises `SystemExit` unless this launch's agents will work in a VM. "Will work in a VM" mirrors what `worktree.enter` and `ensure` do next:

- `record = worktree.recorded(db, adw_id)` when `adw_id` is given (resolve `db` against `budget._root()` as `_vm_resume` does; share that two-line resolution in a small helper both use).
- If `record` exists, the run resumes where the record says: in a VM only if `record.get("sandbox")`. The flag does not count.
- If no record exists, a new worktree is made only when `sandbox_name` is set, and `ensure` records `sandbox_name` on it: in a VM only if `sandbox_name`.

A load failure (`yaml.YAMLError`, `ValueError`, `OSError`) raises `SystemExit(f"{repo_settings.PATH} is invalid: {error}")`, the wording `agentsh_guard.read` uses.

Two messages, both ending with the same last sentence:

- A run that exists (a record without `sandbox`, or a session row for `adw_id`): `host_agents: false in adws/adw_sssf_config/repo.yaml keeps every agent in a VM, and run <adw_id> started on this machine; a resume stays where its run started. Start a new run with an ADW that takes --sandbox.`
- A new run: `host_agents: false in adws/adw_sssf_config/repo.yaml keeps every agent in a VM, and <script stem> would run its agents on this machine: it was given --sandbox none, or it takes no --sandbox. Drop --sandbox none, or use an ADW that takes --sandbox (sdlc, sdlc-quality, build-test, simple-sdlc, tickets).` Check the recipe names against `.claude/skills/sssf/templates/justfile` and use the real ones.
- Last sentence of both: `Or set host_agents: true in adws/adw_sssf_config/repo.yaml in a commit; that is the engineer's call.`

Build the path from `repo_settings.PATH`, and the script stem from `Path(sys.argv[0]).stem`, as `_start` does.

### `session.ensure`

- New keyword `agentless: bool = False`.
- Right after the `if repo_sandbox:` block and before `adw_id = adw_id or new_id(8)`: `host_ok = host_agents(cfg, adw_id, sandbox_name, agentless)`. Pass the caller's `adw_id`, which is `None` for a new run.
- Backstop, inside the `with worktree.launching():` block, after `place["sandbox"] = sandbox_name` is set and before `_start(...)`: if `not host_ok and not agentless and not (place and place.get("sandbox"))`, raise `SystemExit` with the "run exists" message. This checks the place the run will really use, so the pre-check can't drift from `worktree.enter`. For a new run it can't fire: the pre-check passed only with `sandbox_name` set, and that is what `place["sandbox"]` holds. For a resume the worktree already exists, so nothing new is left behind.
- After `_start`: `run.host_agents = host_ok`.
- Update the docstring: what `agentless` means and that the refusal happens first.

### `agents.execute`: an agent call can't reach the host

In `T/adw_modules/runner.py`, `Run.__init__` gets `self.host_agents = True` beside `self.local_guard`. In `T/adw_modules/agents.py`, the first statement of `execute` after `agent = resolve(...)`:

```python
if not getattr(run, "host_agents", True) and sandbox.current() is sandbox.LOCAL:
    raise sandbox.SandboxFailed(...)   # names host_agents: false, repo_settings.PATH and agent.name
```

`sandbox.SandboxFailed` is already in `session.STOPS`, so it prints without a traceback. `execute` is the only caller of `_interface` (line 206), and `PhaseHandle.call` is the only caller of `execute`, so this covers every agent call. This is what makes `agentless=True` safe: a run that asked for the exemption and then calls an agent stops at the call. Use `getattr` with a default, as the `local_guard` line below it does, because existing tests pass stand-in run objects.

### `adw_quality.py`

`T/adw_quality.py` calls `session.ensure(cfg, adw_id, agentless=True)`. It has `REQUIRED_AGENTS = []` and never calls `ph.call`. With `--adw-id <VM run>` it opens the VM as at the baseline; with a host run's id, or none, it runs the checks on the host as at the baseline. That is the documented hole, not a regression.

### Trials

`T/adw_modules/trial.py`: add a helper that loads `repo_settings.load(root)` and raises `TrialError` when `host_agents` is false. Call it in `start` right after `os.chdir(root)`, before `where` and `check`, and at the same point in `review`. Message: `host_agents: false in adws/adw_sssf_config/repo.yaml keeps every agent in a VM, and a trial's panel reviews the attempts on this machine, so no trial starts or is reviewed here. Run the request as one VM run instead, or set host_agents: true in adws/adw_sssf_config/repo.yaml in a commit; that is the engineer's call.` `show`, `merge` and `discard` are untouched, so a trial started before the setting was turned on can still be landed or dropped.

### `repo_settings.py`

In `T/adw_modules/repo_settings.py`:

- Import `StrictBool` and add, after `sandbox_allow`:
  ```python
  # Whether an agent may run on this machine. False refuses every launch that would
  # put one here (session.host_agents). It needs a VM sandbox.
  host_agents: StrictBool = True
  ```
  Strict, so `0` or `"false"` refuses with a message instead of being guessed. YAML's own `false`, `no` and `off` are booleans and load.
- In `_guard`, after the agentsh check: `if not self.host_agents and self.sandbox == "none": raise ValueError("host_agents: false keeps every agent in a VM; it needs sandbox: openshell, vercel or e2b, not sandbox: none")`.

No env override and no flag: the setting changes only in `repo.yaml`.

### Every place a chain or trial starts agents

| Launch | Reaches `session.ensure` as | Under `host_agents: false` |
|---|---|---|
| `adw_plan`, `adw_build`, `adw_plan_build`, `adw_build_review`, `adw_document`, `adw_prompt`, `adw_scout` | `ensure(cfg, adw_id)` | refused, unless `adw_id` is a VM run, which resumes in a VM |
| `adw_simple_sdlc`, `adw_build_test`, `adw_plan_build_test`, `adw_plan_build_test_quality` | `ensure(..., sandbox_name=flag, repo_sandbox=True)` | `none` refused; no flag takes repo.yaml's VM; a host run's resume refused |
| `adw_tickets` | `choose_sandbox`, then `ensure(..., sandbox_name=...)` | same; `claim` runs first but only reads |
| `adw_trial.py start` | `trial.start`, which spawns `adw_simple_sdlc` per attempt | refused in `trial.start` before any attempt; an attempt spawned anyway would hit `ensure` |
| `adw_trial.py review`, and the panel at the end of `start` | `trial.review` -> `ensure(cfg)` | refused in `trial.review`, and again by `ensure` |
| `adw_quality` | `ensure(cfg, adw_id, agentless=True)` | runs; an agent call from it stops in `agents.execute` |
| a new ADW from `scripts/make_adw.py` | `ensure(cfg, adw_id)` | refused, fail closed |
| `adw_worktree` (merge, discard, rebase), `adw_kill`, `adw_export`, `adw_sandbox_usage`, trace recipes, install, update, `openshell-setup` | never | start no agent; untouched |

`agents.validate` and `trial.problems` run `claude auth status` and `pi auth check` before `ensure`. Those are login checks, not agent runs, and stay as they are.

### Two routes the request did not list

1. **The trial panel.** `trial.review` runs one `referee_<family>` agent per model family on the host, in the main checkout, reading diffs the attempts wrote. It is a host agent, so it must refuse. A trial whose panel can't run is not worth its attempts' spend, so `trial start` refuses up front whatever the flag. This means a repo with `host_agents: false` has no trials until the panel can run in a VM. That is a new `docs/open.md` row.
2. **`--sandbox <provider>` with a host run's `--adw-id`.** At the baseline the flag is ignored and the run resumes on the host. The check judges by the run's record, not the flag, so it refuses.

### The operator container (decision 14)

It counts as the host. The container bind-mounts the repo, `.env` included, so a `dotenv-load` recipe gets the same keys there. The check does not read `SSSF_PI_TOKEN_SOCKET` and adds no container branch: under `host_agents: false` a launch in the container refuses exactly when it would on the host. With the default, the container behaves as at the baseline.

## Steps

All are builder steps; every path is under `.claude/skills/sssf/` or `docs/`.

1. `T/adw_modules/repo_settings.py`: the field and the `_guard` check above.
2. `T/adw_modules/session.py`: `host_agents`, the shared db-path helper, the three edits to `ensure`, docstrings (module, `ensure`, and one clause in `choose_sandbox` saying a host run's resume refuses under `host_agents: false`).
3. `T/adw_modules/runner.py`: `self.host_agents = True`.
4. `T/adw_modules/agents.py`: the check in `execute`. Import `repo_settings` there if it is not already imported.
5. `T/adw_modules/trial.py`: the helper, its two calls, and the module docstring's line on where the panel runs.
6. `T/adw_quality.py`: `agentless=True`.
7. `T/adw_trial.py`: one docstring sentence under `start`: no trial starts under `host_agents: false`.
8. `.claude/skills/sssf/templates/repo.yaml`: after the `sandbox: none` block, a comment block and `host_agents: true`. The comment says: `false` refuses any run whose agents would work on this machine, it needs a VM `sandbox`, and it suits a repo whose justfile loads `.env`.
9. `.claude/skills/sssf/tests/test_host_agents.py`: new, see Tests.
10. Docs, see below.

Do not edit `T/adw_modules/agentsh_guard.py`, `sandbox_openshell.py`, `permissions.py`, `rules.py` or any chain script other than `adw_quality.py` and `adw_trial.py`. Do not edit `adws/` at the repo root, any `repo.yaml` outside `templates/`, or `app_docs/`.

## Tests

New file `.claude/skills/sssf/tests/test_host_agents.py`. No real VM and no real agent.

Fixture, following `tests/test_adw_commit_messages.py` (chains imported with `importlib` and driven through `main`) and `tests/test_adw_tickets.py` (the VM faked by patching `session._open_sandbox` and `sandbox.prepare`):

- A temp git repo with `adws/adw_sssf_config/repo.yaml` committed. Its roster is loaded from the shipped `.claude/skills/sssf/templates/sssf.config.yaml`, never a test-only one.
- `agents.validate` patched to a no-op. `agents.execute` patched to a recorder that returns a passing envelope, except in the test of the `execute` check itself.
- `session._open_sandbox` patched to a recorder that also sets `sandbox._current` to a non-`LOCAL` stand-in through `monkeypatch.setattr`.
- A helper `trace_counts(repo)` returning the row counts of `sessions`, `processes`, `events` and `phases` (zeros when the db or tables are missing), the output of `git worktree list`, the `sssf/*` branches, and the entries under the data dir's `worktrees/`. A refusal test takes it before and after and asserts equality, and asserts the `execute` and `_open_sandbox` recorders are empty.

Cases:

1. **Settings.** Default is `True`. `host_agents: false` with `sandbox: none` raises, and the message contains both `host_agents` and `sandbox`. It loads with each of `openshell`, `vercel`, `e2b`. `host_agents: 0` and `host_agents: "false"` raise. The shipped `templates/repo.yaml` loads with `host_agents` true.
2. **Bad pair at launch.** With that pair on disk, a chain's `main` exits with a message naming both, and `trace_counts` is unchanged.
3. **No-flag chains.** Parametrized over the seven: `main(...)` raises `SystemExit` whose text contains `host_agents: false` and `adws/adw_sssf_config/repo.yaml`; nothing started.
4. **`--sandbox none`.** Parametrized over the five flag chains with `sandbox_name="none"`: same assertions. `adw_tickets` needs a committed feature folder; reuse that file's set-up.
5. **Trials.** `trial.start(...)` with `asked="none"`, `asked=None` and `asked="openshell"` raises `TrialError` naming the setting and file, with `subprocess.Popen` patched to fail the test if called. `trial.review("x")` does the same. `adw_trial.main` returns 1 for `start --sandbox none`. Nothing started.
6. **Resuming a host run.** Make a worktree host run while `repo.yaml` has `sandbox: openshell` and no `host_agents` (a flag chain with `sandbox_name="none"`, agents faked). Set `host_agents: false` in the main checkout. Each of these refuses with counts unchanged and no new `execute` call: the same chain with no flag, with `"none"`, with `"openshell"`; `adw_build` with that `adw_id`. Repeat the no-flag case for a plain host run (no worktree).
7. **An agent can't turn it off.** With the host run's worktree from case 6 and the main checkout at `false`: (a) commit `host_agents: true` to `repo.yaml` on the run's branch in its worktree; (b) in a second set-up leave the same edit uncommitted. In both, the resume refuses when launched from the main checkout and when launched with the cwd inside the worktree, and a new no-flag run launched from inside the worktree refuses too.
8. **The VM path.** Under the setting, a flag chain with no flag and with `sandbox_name="openshell"`: `_open_sandbox` is called once with `openshell`, the `worktree` event records `sandbox: openshell`, and the agents run. Resume that run with no flag, and join it from `adw_build --adw-id`: `_open_sandbox` is called again and nothing refuses. Run the same launches with `host_agents: true` and assert the same `_open_sandbox` calls.
9. **Non-agent commands.** Under the setting: `adw_quality.main` runs with `quality.run_quality` faked and writes its session row; `worktree.merge` and `worktree.discard` on a finished run work, driven as `tests/test_worktree.py` drives them; the function behind the `status` recipe in `.claude/skills/sssf/templates/justfile` returns as it does with the default.
10. **The `execute` check.** A run from `session.ensure(cfg, agentless=True)` under the setting: a `ph.call(...)` raises `sandbox.SandboxFailed` naming the setting, with `agents._interface` patched to fail the test if reached. With the default setting the same call reaches `_interface`.
11. **Default unchanged.** With no key and with `host_agents: true`, one no-flag chain and one `sandbox_name="none"` launch run their faked agents on the host as before.

Each chain's `main` moves the cwd and installs signal handlers and an excepthook; restore them the way the two files above do. Keep the file light: faked agents only, and no sleep or polling.

## Docs

- `.claude/skills/sssf/references/config.md`:
  - Settings table: a `host_agents` row after `sandbox_allow`, default `true`. `false` refuses, at launch, any run whose agents would work on this machine: `--sandbox none`, an ADW that takes no `--sandbox`, a resume of a host run, and trials. It needs `sandbox: openshell`, `vercel` or `e2b`.
  - `sandbox` row: after "A resume stays where its run started", add that under `host_agents: false` a host run's resume refuses.
  - `local_guard` row or section: one clause that `host_agents: false` can't be combined with it, since each needs the other's `sandbox` value.
  - In "A local sandbox: `--sandbox openshell`", a paragraph "Keeping every agent in the VM, `host_agents`": why (a script or recipe the text rules can't read, and a justfile that loads `.env`); what refuses; that SSSF reads the main checkout's file before any agent starts, so a run's worktree can't change it; that trials refuse because the panel runs on this machine; that the operator container counts as this machine; and what still works (`merge`, `discard`, `rebase`, `kill`, `status`, `phases`, `quality`).
  - The hole, in the same paragraph: `just quality` run on a run's branch or worktree runs code the builder wrote, on this machine, with whatever the justfile loads. No agent is involved, but the code is the agent's. Check a run's work in the VM, or review it before running the repo's checks here.
- `.claude/skills/sssf/cookbooks/run_adw.md`: in the "switch an installed repo to `sandbox: openshell`" steps, an optional step: for a repo whose justfile loads `.env`, set `host_agents: false`; then the ADWs that take no `--sandbox`, `--sandbox none`, host resumes and trials refuse. Adjust step 2's sentence about the no-flag ADWs still running on the host to say so.
- `.claude/skills/sssf/cookbooks/trial.md`: one line among the reasons a trial doesn't start.
- `.claude/skills/sssf/cookbooks/create_adw.md`: one line: a new ADW that runs agents and takes no `--sandbox` refuses under `host_agents: false`; one that runs no agent passes `agentless=True`.
- `docs/open.md`, two rows in their place in the sort, with all four fields set and today's date (2026-11-18) under Since:
  - **Bug, High**, Needs decision: under `host_agents: false`, `just quality` on a run's branch or worktree runs agent-written code on the host with whatever the justfile loads. Fix options: run quality in a VM, or refuse it in a run's worktree. Both are new mechanisms and guard changes. Link `config.md`'s paragraph.
  - **Feature**, Deferred: under `host_agents: false` no trial runs, because the panel's reviewers work on the host. The fix is a panel that runs in a VM, a new mechanism and a guard change.
  Pick the priority for each from the table's definitions; P3 fits both unless a neighbouring row suggests otherwise.
- The repo keeps no CHANGELOG and has no `app_docs/` folder, so neither is touched.

## Verification

From `.claude/skills/sssf`:

```
uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_host_agents.py tests/test_sandbox_default.py tests/test_sandbox_extensions.py tests/test_trial.py tests/test_adw_tickets.py tests/test_adw_commit_messages.py tests/test_worktree.py tests/test_repo_yaml_mapping.py -q -p no:cacheprovider -m 'not pushes and not host_only'
```

It must exit 0. Leave the whole suite to verify. Then check by reading:

- `git diff 9b8106a --stat` touches only the files named in Steps and Docs.
- Every `session.ensure(` call under `T/` appears in the table above, and only `adw_quality.py` passes `agentless=True`.
- `_interface(` is still called only from `agents.execute`.
- Each refusal message contains `host_agents: false` and `adws/adw_sssf_config/repo.yaml`.

## Out of scope

Holes this change leaves open, to be filed as old holes:

- `just quality`, `just export`'s checks, and the repo's own recipes run agent-written code on the host. Documented, with an `open.md` row for quality.
- A host agent already running when the setting is turned on keeps running. `just kill` stops it.
- A host agent, under `host_agents: true`, can edit the main checkout's `repo.yaml`; this setting is not a guard for runs that are on the host.
- SSSF does not check that `host_agents: true` was committed.
- The operator container gets no branch of its own: it counts as the host.
- Running the trial panel in a VM.
- No repo's `repo.yaml` changes, this hub's included. The P2 row on committed secret files and the other open rows stay as they are.

## For the engineer

1. The hub's installed copy under `adws/` is protected, so the builder leaves it. After the merge, update it from the templates the usual way, or `host_agents` in the hub's `repo.yaml` would fail to load as an unknown key.
2. Decide whether refusing every trial under `host_agents: false` is what you want. The alternative is to let the attempts run in VMs and skip the panel; this plan refuses because a panel reviewer is a host agent reading agent-written diffs.
3. `adws/adw_guard_change.py` and `CLAUDE.md` list the guard modules. Add `repo_settings.py` and `session.py` there if they are not listed and you want later changes to this check to go through the guard chain.
