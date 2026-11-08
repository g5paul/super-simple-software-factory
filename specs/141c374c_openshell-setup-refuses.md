# Refuse `just openshell-setup` inside an SSSF run

## Summary

`just openshell-setup` acts outside the repo: it writes a systemd user unit, TLS
keys and a gateway config under `$HOME`, starts the gateway service, and builds a
Podman image. Only a line in the run cookbook keeps agents off it.

This change adds a one-line refusal to the recipe and a matching one to
`scripts/openshell_image.py`. Both fire when `SSSF_ADW_ID` is set. It follows the
pattern `references/config.md` already gives for a recipe that must never run
under an agent, and the pattern `templates/githooks/pre-push` uses.

No guard module changes. All code goes in the skill's copies:
`.claude/skills/sssf/templates/justfile` and
`.claude/skills/sssf/scripts/openshell_image.py`.

## Threat model

- **What the agent can do.** An agent runs as the engineer's own user. Its shell
  can start any recipe, script or binary that user can, with the engineer's
  `$HOME` and systemd user session. At the baseline, an agent that follows a
  "run `just openshell-setup`" stop message changes that `$HOME`, starts a
  service and builds an image.
- **How an agent is told apart.** `mark_agent` in
  `.claude/skills/sssf/templates/adws/adw_modules/utils.py` sets `SSSF_ADW_ID`. Both
  `child_env` functions call it (`agent_cc.py`, `agent_pi.py`), so a Claude Code or
  pi agent and every command it runs carry the variable. The chain process and the
  engineer's shell don't set it. Nothing in `adws/` or `adw_modules/` calls
  `scripts/openshell_image.py`, so no chain step can hit the new refusal.
- **What this guard can't stop at its level.** The variable is a courtesy, not a
  lock, as `mark_agent`'s docstring says. An agent running as the engineer can
  clear it, edit the recipe, or run the steps itself. See Known limits.

## Baseline

`main` at `80a56b0` ("Record the rollout of skill 0fbee01 and a flaky sandbox
test"). Every finding is judged against it. At the baseline:

- `openshell-setup` in `.claude/skills/sssf/templates/justfile` starts with
  `set -euo pipefail` and then the `/dev/kvm` check. It never reads `SSSF_ADW_ID`.
- `main()` in `.claude/skills/sssf/scripts/openshell_image.py` runs `podman build`
  unless `--print` is given. It never reads `SSSF_ADW_ID`.
- The hub's `agent_rules` cover GitHub writes only, and `templates/repo.yaml` ships
  with no rules.

## Bar

The change must:

- Make `openshell-setup`, with a non-empty `SSSF_ADW_ID`, print one line of its own
  to stderr and exit non-zero as the first thing after `set -euo pipefail`. Nothing
  is written under `$HOME`, and no `systemctl`, `podman`, `openshell`,
  `openshell-gateway` or `uv` runs.
- Hold however the recipe is called: `just openshell-setup`,
  `just sssf openshell-setup` where the recipes are a module, or from a script.
- Make `scripts/openshell_image.py` refuse the same way unless `--print` is given,
  right after argument parsing, before any layout lookup, `uv` or Podman.
- Say in the refusal that it's the engineer's to run, outside an SSSF run.

It must never:

- Change what the recipe or script does when `SSSF_ADW_ID` is unset or empty. The
  rest of the recipe stays byte for byte.
- Refuse `--print`.
- Change `rules.py`, any other guard module, `agent_rules` or `templates/repo.yaml`.
- Change any existing test.
- Keep an agent file, run agent code, or delete or overwrite an engineer file where
  the baseline doesn't.
- Touch a real `$HOME`, gateway service or Podman store from a test.

Judge findings against what the request sets: this stops an agent from running the
recipe by name, the way a `forbid` rule on a billing recipe does. It is no stronger
than that.

## Known limits

These are by design. Report any of them as an old hole, not a finding.

- An agent that clears the variable gets past both refusals: `env -u SSSF_ADW_ID
  just openshell-setup` or `SSSF_ADW_ID= just openshell-setup`. An empty value counts
  as unset, the same as `mark_agent`'s documented check and `pre-push`.
- An agent that runs the recipe's steps by hand gets past it (`systemctl --user`,
  `podman build`, writing the unit file itself). So does one that edits its copy of
  the recipe. A local agent could already do all of these.
- **An operator session outside a run isn't marked.** A Claude Code session that
  follows `cookbooks/run_adw.md` and launches a chain isn't an SSSF agent, so it has
  no `SSSF_ADW_ID` and the recipe won't refuse it. The cookbook's "don't run it
  yourself" remains the only thing covering that session, and the cookbook must say
  so rather than claim the recipe stops every reader. The request asks only for
  refusal inside a run, so this change doesn't close it.
- `just` adds its own `error: recipe ... failed` line after the recipe's one line.
  `[no-exit-message]` would hide it, but also for the recipe's other failures, so
  it stays.

## Out of scope

- A built-in rule in `rules.py`, or any change to `agent_rules`,
  `templates/repo.yaml` or the guard modules, `sandbox_openshell.py` included. Its
  stop messages keep naming `just openshell-setup`, and they're for the engineer.
- Other recipes that act outside the repo, and other scripts, such as
  `scripts/e2b_template.py`.
- Everything in Known limits.

## Changes

### 1. `.claude/skills/sssf/templates/justfile`

In `openshell-setup` only, add this line straight after `set -euo pipefail`, with
the recipe's four-space indent:

```bash
[ -z "${SSSF_ADW_ID:-}" ] || { echo "Refusing: openshell-setup is the engineer's to run, outside an SSSF run (SSSF_ADW_ID is set)." >&2; exit 1; }
```

Put this as the first line of the comment block above the recipe:

```
# It refuses while SSSF_ADW_ID is set, so an agent in a run can't start it by name.
```

Leave the last comment line as it is, because `just --list` shows that line as the
summary. Don't edit the repo-root `justfile`: it's a stamped copy, and the update
after merge refreshes it (see For the engineer).

### 2. `.claude/skills/sssf/scripts/openshell_image.py`

- Add `import os`.
- Right after `args = parser.parse_args()`, before the `try`:

  ```python
  if os.environ.get("SSSF_ADW_ID") and not args.show:
      print("Refusing: building the OpenShell image is the engineer's to run, "
            "outside an SSSF run (SSSF_ADW_ID is set). --print still works.",
            file=sys.stderr)
      return 1
  ```

- In the module docstring, after the `--print` sentence, add: "Without --print it
  refuses while SSSF_ADW_ID is set, as `just openshell-setup` does."

### 3. `.claude/skills/sssf/references/config.md`

At the end of the `--sandbox openshell` paragraph that starts the OpenShell section
(the one with "It never runs `sudo`"), add:

> It refuses inside a run: when `SSSF_ADW_ID` is set, as it is for every agent, the recipe prints one line and exits non-zero before it does anything else. `scripts/openshell_image.py` does the same unless `--print` is given. That stops an agent running them by name. It doesn't stop one that clears the variable or runs the steps by hand, and a session outside a run isn't marked at all.

### 4. `.claude/skills/sssf/cookbooks/run_adw.md`

In the `openshell` bullet under "In a VM", keep "tell the engineer to run `just
openshell-setup`; don't run it yourself, since it starts a system service and builds
an image." Directly after it, add:

> The recipe enforces this inside a run: it refuses when `SSSF_ADW_ID` is set, as it is for every agent SSSF starts. A session outside a run isn't marked, so there it won't stop you, and this instruction still applies.

### 5. `docs/designs/openshell-sandbox.md`

Add one bullet to `## As built`:

> - **Who runs setup.** `just openshell-setup` and `scripts/openshell_image.py` (without `--print`) refuse while `SSSF_ADW_ID` is set, so an agent in a run can't start them by name. It's the same courtesy check as `pre-push`, and an agent can clear the variable.

### 6. `.claude/skills/sssf/tests/test_openshell_setup_refuses.py` (new)

Put every new test here, so the existing test files stay byte for byte. These tests
load no SSSF config, except the shipped `templates/sssf.config.yaml` where the script
needs a repo layout. Never write a test-only config.

Helpers:

- `JUST = shutil.which("just")`. Mark the recipe tests
  `pytest.mark.skipif(not JUST, reason="just isn't installed")`, the convention
  `test_install_hook.py` uses for gitleaks.
- `standins(tmp_path, names)` writes executables that record their call to the file
  `$STANDIN_LOG`, then exit 0:
  ```sh
  #!/bin/sh
  echo "$(basename "$0") $*" >> "$STANDIN_LOG"
  ```
  The recipe tests use `systemctl`, `podman`, `openshell`, `openshell-gateway` and
  `uv`. The `uv` stand-in is what keeps the real image script from running.
- `environment(...)` starts from `os.environ`, sets `HOME` to a fake folder, puts
  the stand-ins first on `PATH`, sets `STANDIN_LOG`, **removes `SSSF_ADW_ID` and
  `SSSF_AGENT`**, then sets `SSSF_ADW_ID` only if the test asks. Removing them
  matters because the suite itself runs inside an agent that has the variable set.
- `recipe_repo(tmp_path, module)` copies `templates/justfile` into a temp repo, as
  `justfile`, or as `sssf.just` beside a `justfile` holding
  `mod sssf 'sssf.just'`. It replaces `/dev/kvm` in the copy with a temp file that
  exists. Without that, the result would depend on this machine's KVM access, and
  a refusal placed after the KVM check could go unnoticed on a machine that stops
  at it.

Recipe tests:

- `test_recipe_refuses_inside_a_run`, parametrized over direct
  (`just openshell-setup`), module (`just sssf openshell-setup`) and script (a shell
  script file in the repo that runs `just openshell-setup`). Assert:
  - a non-zero exit;
  - stderr lines, leaving out those starting `error: recipe`, are exactly one line
    containing `engineer's to run` and `outside an SSSF run`;
  - the stand-in log is empty or missing;
  - `list(home.rglob("*")) == []`.
- `test_recipe_gets_past_the_check_without_the_variable`, parametrized over direct
  and module. Assert:
  - exit 0, and `Refusing` isn't in stderr;
  - the log holds `systemctl --user enable --now openshell-gateway`,
    `uv run .claude/skills/sssf/scripts/openshell_image.py --repo .` and
    `openshell status`;
  - `.config/systemd/user/openshell-gateway.service` and
    `.config/openshell/gateway.toml` exist under the fake home.
- `test_recipe_treats_an_empty_variable_as_unset`: `SSSF_ADW_ID=""`, exit 0, and the
  log holds `openshell status`.

Script tests, run with `sys.executable -I` as `test_openshell_image.py` does, in a
`git init` repo under the fake home with the shipped config copied to
`repo / osh.CONFIG`, as `write_shipped_config` does:

- `test_script_refuses_inside_a_run`: stand-ins `podman` and `uv`, variable set, no
  `--print`. Exit non-zero, one stderr line with the same two phrases, empty stdout,
  and an empty log. The `uv` stand-in shows it refused before the layout lookup.
- `test_script_print_works_inside_a_run`: stand-in `podman` only, variable set,
  `--print`. Exit 0, stdout's first line matches
  `localhost/sssf-openshell:[0-9a-f]{12}`, and `podman` wasn't called.
- `test_script_builds_without_the_variable`: stand-in `podman` only, variable
  removed, no `--print`. Exit 0, and the log has one `podman build -t
  localhost/sssf-openshell:` line.

## Docs this change leaves as they are

- `docs/guide/run.md`, `docs/guide/install.md`, `docs/guide/troubleshooting.md` and
  `docs/fork.md` tell the engineer to run the recipe, which stays true.
- `references/config.md`'s `SSSF_ADW_ID` section already teaches this pattern.
- There is no `app_docs/` and no project CHANGELOG in this repo.

## Verification

Never run `just openshell-setup`, or `openshell_image.py` without `--print`, outside
the new tests' stand-ins.

1. Run the new tests and read the summary. `-rs` must show no skip on this machine,
   which has `just`:
   `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_openshell_setup_refuses.py tests/test_openshell_image.py tests/test_sandbox_openshell.py -q -p no:cacheprovider -rs`
2. Run the repo's skill suite, unchanged:
   `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`
3. Run the hub's chain suite:
   `uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest adws/tests -q -p no:cacheprovider`
4. Confirm the summary line survives:
   `just --justfile .claude/skills/sssf/templates/justfile --list` must still show
   `set up the local OpenShell gateway and image for --sandbox openshell`.

A scratch copy of the recipe with this line was already tried under `just` 1.59.0,
with stand-ins and a fake `$HOME`:

| Case | Result |
|---|---|
| Set: direct, module and from a shell | exit 1, no stand-in called, nothing under `$HOME` |
| Unset or empty | ran to the end, exit 0 |
| Set, with `SSSF_ADW_ID=` in `.env` | still refused, since `just` doesn't override an exported variable |

## For the engineer

No protected file needs to change. After merge:

- **Update the hub.** Run `uv run .claude/skills/sssf/scripts/install.py --update` on
  a short branch, as `docs/sssf.md` says. Until then the hub's own root `justfile`
  doesn't refuse. Project repos get the recipe through `just sssf-update`. A repo
  that edited its stamped `justfile` or `sssf.just` sees it flagged for `--take` or
  `--keep`, and keeps the old recipe if it keeps its copy.
- **Decide on operator sessions.** See Known limits. Closing that gap needs a
  different mechanism, such as a marker for operator
  sessions, so it would be a separate change.
- **Leave out the existing work-tree changes.** `adws/adw_modules/sandbox_openshell.py`
  and `adws/adw_sssf_config/stamp.json` were already modified when this plan was
  written. They aren't part of this work.
