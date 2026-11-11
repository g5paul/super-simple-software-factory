# The hub's own recipes, loaded by the justfile's `import? 'local.just'`.
# They serve this repo only, and a fork can delete this file. docs/sssf.md
# says what each command does.

# The clone the hub installs its own SSSF from, so the hub's runs use merged skill code.
install_clone := env_var_or_default("SSSF_INSTALL_CLONE", home_directory() / "github/sssf-install")

# a change to guard code, through the chain: just guard-change specs/tasks/<task>.md [--worktree]
guard-change *ARGS:
    uv run adws/adw_guard_change.py "$@"

# each installed repo's SSSF update, checked against the hub: just projects [--listed]
projects *ARGS:
    uv run adws/check_projects.py "$@"

# the four levers, week by week, from every trace: just levers [--weeks N] [--listed]
levers *ARGS:
    uv run adws/levers.py "$@"

# what each feature cost, runs joined to the commits that landed: just features [--listed]
features *ARGS:
    uv run adws/features.py "$@"

# each chain's usual phase times, and the runs past twice them: just phase-times [--repo NAME] [--listed]
phase-times *ARGS:
    uv run adws/phase_times.py "$@"

# Run it on a short branch after a skill change merges to main, then commit stamp.json.
# move the install clone to main and update the hub's own install from it
hub-update:
    git -C "{{install_clone}}" pull --ff-only
    SSSF_SOURCE="{{install_clone}}/.claude/skills/sssf" uv run .claude/skills/sssf/scripts/install.py --update
