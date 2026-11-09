# Install the agentsh policy from the hub, not from the project

Rollout step 2 of `docs/designs/agentsh-local-sandbox.md` is done. This replaces item 4 of its Next list. The chain run e027505a, from `specs/tasks/guard-agentsh-install-check.md`, tried to have the project's own install script check where its source files came from. It stopped after round 2: the check runs git and imports Python in a repo an agent can change, and each round found another git or Python setting that ran agent code or hid a change. The design doc's section "The install-check run, e027505a" records it and the engineer's decision. Don't reuse its approach. Start from `main`.

## The problem

`uv run adws/adw_agentsh_policy.py`, in the project, builds the server config and the policies from the project's own code and template, then prints `sudo install` lines the engineer runs as root. An agent can change those files on a plain run, and SSSF's commit phases may commit the change. The known case is in `config.md`: an agent's edit to `agentsh_sssf.yaml` sets `on_attach_failure: fail_open`, and the engineer installs it.

## What should change

1. **The install runs from the skill's source, the hub,** for example `uv run --directory <hub> .claude/skills/sssf/scripts/agentsh_install.py --repo <project>`. The policy code and the config template it uses are the hub's. It imports nothing from the project, runs no program from it, and runs no git in it. Find the git common dir and the main checkout from the filesystem, and say how in the spec. Make sure `uv` and Python don't pick up the project's `pyproject.toml`, `uv.toml`, `.python-version` or `sys.path`.
2. **It reads only data from the project:** `adws/adw_sssf_config/repo.yaml` and `sssf.config.yaml`, with `yaml.safe_load`, and the skill version in `stamp.json`. It refuses when that version differs from the hub's skill version, since the project's own `agentsh_guard.py` checks the installed files at run time and must render the same text.
3. **It shows the engineer what will change before the sudo lines:** a diff of each new file against what's installed in `/etc/agentsh/`, or the whole file when nothing is installed. Then the sudo lines. An agent's change to `repo.yaml`, such as a new `network_allow` host, shows up there for the engineer to read.
4. **A run's refusal no longer sends the engineer straight to install.** When the installed config or a policy differs from what the project renders, it says so, gives the `git status` and `git diff` commands for `adws/`, says to discard a change the engineer didn't make, and points at the hub install.
5. **Remove `adws/adw_agentsh_policy.py`** from the skill's templates, and see that `sssf-update` removes it from a project, or say in the spec why it can't.
6. **`config.md`** replaces the `agentsh_sssf.yaml` limit with what's built, gives the new install command, and says the hub must be on the same machine at the project's skill version.

## The bar

The earlier bars stand. The install running any code from the project, or any program a project file or setting names, is a harm. An agent's change to a project file that reaches the printed sudo lines without showing in the diff is a bypass. A list of git or Python settings to override, or a provenance check on commits, is a finding.

## Out of scope

The policy rules themselves, the hub's own copy under `adws/`, pi under the wrap, running a real agent under the service, sudo, and turning this on outside sssf-sandbox.
